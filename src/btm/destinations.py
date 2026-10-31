"""Turn raw destinations into readable place names.

* Ships: the AIS "destination" field is free text typed by the crew. Most
  use UN/LOCODE port codes ("FIHEL", "SE STO"), some write a route
  ("SENYN>FIMHQ"), a port name ("HELSINKI") or a status ("FOR ORDERS").
* Aircraft: ADS-B has no destination. Routes come from adsb.lol's routeset
  API (see btm.sources.adsb) and only need the country name translated.

Country names are in Swedish (ISO 3166 translations shipped with pycountry).
"""

from __future__ import annotations

import csv
import gettext
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pycountry

PORTS_CSV = Path(__file__).resolve().parents[2] / "data" / "ports.csv"

# Preferred countries when a port *name* matches several countries (Baltic first)
REGION_PRIORITY = ["SE", "FI", "EE", "LV", "LT", "PL", "DE", "DK", "RU", "NO", "NL", "BE", "GB"]

# Statuses crews type instead of a port – shown as Swedish text
SPECIAL_DESTINATIONS: dict[str, str] = {
    "FORORDERS": "Inväntar order",
    "FORORDER": "Inväntar order",
    "ORDERS": "Inväntar order",
    "FISHING": "Fiske",
    "FISHINGGROUNDS": "Fiske",
    "FISHINGGROUND": "Fiske",
    "PATROL": "Patrullering",
    "SAR": "Sjöräddning",
    "TRAINING": "Övning",
    "EXERCISE": "Övning",
    "ANCHORAGE": "Ankarplats",
    "ATANCHOR": "Ankarplats",
    "ATSEA": "Till sjöss",
    "SEA": "Till sjöss",
    "OFFSHORE": "Till sjöss",
    "DREDGING": "Muddring",
    "SURVEY": "Sjömätning",
    "PILOTSTATION": "Lotsstation",
    "PILOT": "Lotsstation",
}
MISSING_VALUES = {"", "NA", "NONE", "NULL", "UNKNOWN", "TBA", "TBN", "XXXXX", "NOTDEFINED"}

# Common spellings that don't match the UN/LOCODE name
NAME_ALIASES: dict[str, str] = {
    "STPETERSBURG": "RULED",
    "SPETERSBURG": "RULED",
    "SPB": "RULED",
    "STPETE": "RULED",
    "PETERSBURG": "RULED",
    "GOTHENBURG": "SEGOT",
    "COPENHAGEN": "DKCPH",
    "MARIEHAMN": "FIMHQ",
    "HELSINGFORS": "FIHEL",
}

# Bilingual Finnish ports where UN/LOCODE lists the Finnish name first:
# show the Swedish name first, keep the Finnish one in parentheses.
PORT_NAME_OVERRIDES = {
    "FIMHQ": "Mariehamn (Maarianhamina)",
    "FITKU": "Åbo (Turku)",
    "FIVAA": "Vasa (Vaasa)",
    "FIOUL": "Uleåborg (Oulu)",
    "FIRAU": "Raumo (Rauma)",
    "FILOV": "Lovisa (Loviisa)",
    "FINLI": "Nådendal (Naantali)",
    "FIKAS": "Kaskö (Kaskinen)",
    "FIPAR": "Pargas (Parainen)",
    "FIKRS": "Kristinestad (Kristiinankaupunki)",
}

# Shorter, everyday Swedish names where the official translation is long
COUNTRY_OVERRIDES = {
    "GB": "Storbritannien",
    "RU": "Ryssland",
    "KR": "Sydkorea",
    "KP": "Nordkorea",
    "IR": "Iran",
    "SY": "Syrien",
    "VE": "Venezuela",
    "BO": "Bolivia",
    "TZ": "Tanzania",
    "MD": "Moldavien",
    "VN": "Vietnam",
    "LA": "Laos",
    "TW": "Taiwan",
    "CZ": "Tjeckien",
}

ROUTE_SEPARATORS = re.compile(r"\s*(?:=>|->|>|/|\bTO\b|\bVIA\b)\s*")


@dataclass(frozen=True)
class Port:
    locode: str
    name: str
    country: str


@dataclass(frozen=True)
class Destination:
    raw: str | None
    locode: str | None = None
    name: str | None = None  # port name, or Swedish status text for special destinations
    country: str | None = None  # ISO 3166 alpha-2

    @property
    def resolved(self) -> bool:
        return self.name is not None


# ---------------------------------------------------------------------------
# Countries
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _sv() -> gettext.NullTranslations:
    try:
        return gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["sv"])
    except OSError:
        return gettext.NullTranslations()


@lru_cache(maxsize=512)
def country_name(code: str | None) -> str | None:
    """'FI' -> 'Finland', 'DE' -> 'Tyskland'. Unknown codes are returned unchanged."""
    if not code:
        return None
    code = code.strip().upper()
    if code in COUNTRY_OVERRIDES:
        return COUNTRY_OVERRIDES[code]
    country = pycountry.countries.get(alpha_2=code)
    if country is None:
        return code
    return _sv().gettext(getattr(country, "common_name", None) or country.name)


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------
def _key(text: str) -> str:
    """Upper-case letters only, accents removed: 'Göteborg' -> 'GOTEBORG'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z]", "", ascii_text.upper())


class PortIndex:
    def __init__(self, ports: list[Port]):
        self.by_code: dict[str, Port] = {p.locode: p for p in ports}
        self.by_name: dict[str, list[Port]] = {}
        for p in ports:
            # "Helsingfors (Helsinki)" is findable as both HELSINGFORS and HELSINKI
            for part in re.split(r"[()/]", p.name):
                if key := _key(part):
                    self.by_name.setdefault(key, []).append(p)

    @classmethod
    def from_csv(cls, path: Path = PORTS_CSV) -> PortIndex:
        with open(path, encoding="utf-8") as f:
            ports = [
                Port(r["locode"], PORT_NAME_OVERRIDES.get(r["locode"], r["name"]), r["country"])
                for r in csv.DictReader(f)
            ]
        return cls(ports)

    def by_port_name(self, key: str) -> Port | None:
        candidates = self.by_name.get(key)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        ranked = sorted(
            candidates,
            key=lambda p: REGION_PRIORITY.index(p.country) if p.country in REGION_PRIORITY else 99,
        )
        best = ranked[0]
        return best if best.country in REGION_PRIORITY else None  # ambiguous outside the region


@lru_cache(maxsize=1)
def default_index() -> PortIndex:
    return PortIndex.from_csv()


def _lookup(segment: str, index: PortIndex) -> Destination | None:
    compact = re.sub(r"[^A-Z0-9]", "", segment)
    if not compact:
        return None
    if compact in SPECIAL_DESTINATIONS:
        return Destination(segment, name=SPECIAL_DESTINATIONS[compact])
    # UN/LOCODE: 2-letter country + 3 characters, possibly typed as "SE STO" or "SE-STO"
    if len(compact) == 5 and (port := index.by_code.get(compact)):
        return Destination(segment, port.locode, port.name, port.country)
    letters = _key(segment)
    if letters in NAME_ALIASES and (port := index.by_code.get(NAME_ALIASES[letters])):
        return Destination(segment, port.locode, port.name, port.country)
    if port := index.by_port_name(letters):
        return Destination(segment, port.locode, port.name, port.country)
    return None


def resolve_ais_destination(raw: str | None, index: PortIndex | None = None) -> Destination:
    """Best-effort interpretation of an AIS destination string."""
    if raw is None:
        return Destination(None)
    text = raw.upper().replace("@", " ").strip()
    if _key(text) in MISSING_VALUES or not re.search(r"[A-Z]", text):
        return Destination(raw.strip() or None)

    index = index or default_index()
    # "SENYN>FIMHQ", "STOCKHOLM - HELSINKI": the last leg is where the ship is heading
    segments = [s for s in ROUTE_SEPARATORS.split(text) if s.strip()]
    if len(segments) == 1 and " - " in text:
        segments = [s for s in text.split(" - ") if s.strip()]
    if segments and (found := _lookup(segments[-1].strip(), index)):
        return Destination(raw.strip(), found.locode, found.name, found.country)
    # Whole string as a last resort ("FOR ORDERS", "ST PETERSBURG")
    if found := _lookup(text, index):
        return Destination(raw.strip(), found.locode, found.name, found.country)
    return Destination(raw.strip())
