"""ADS-B aircraft data from adsb.lol (open data, ODbL).

Docs: https://api.adsb.lol/docs
Endpoint used: ``/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}`` (radius max 250 nm).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

BASE_URL = "https://api.adsb.lol/v2"

# Bit 0 of dbFlags marks an aircraft as military in the readsb database
DB_FLAG_MILITARY = 1


@dataclass(frozen=True)
class AircraftPosition:
    icao_hex: str
    observed_at: datetime
    lat: float
    lon: float
    altitude_ft: int | None
    on_ground: bool
    ground_speed_kt: float | None
    track_deg: float | None
    vertical_rate_fpm: int | None
    squawk: str | None
    callsign: str | None
    registration: str | None
    type_code: str | None
    is_military: bool


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_aircraft(payload: dict[str, Any]) -> list[AircraftPosition]:
    """Parse the ``{"ac": [...], "now": <ms>}`` envelope from adsb.lol."""
    now_ms = payload.get("now")
    now = datetime.fromtimestamp(now_ms / 1000, tz=UTC) if now_ms else datetime.now(UTC)

    positions: list[AircraftPosition] = []
    for ac in payload.get("ac") or []:
        lat, lon = ac.get("lat"), ac.get("lon")
        hex_code = _clean(ac.get("hex"))
        if lat is None or lon is None or hex_code is None:
            continue  # no position fix – nothing to store

        # Position age relative to the server timestamp; round to whole seconds
        seen_pos = float(ac.get("seen_pos", ac.get("seen", 0)) or 0)
        observed_at = (now - timedelta(seconds=seen_pos)).replace(microsecond=0)

        alt_raw = ac.get("alt_baro")
        on_ground = alt_raw == "ground"
        altitude = 0 if on_ground else (int(alt_raw) if isinstance(alt_raw, (int, float)) else None)

        rate = ac.get("baro_rate", ac.get("geom_rate"))

        positions.append(
            AircraftPosition(
                icao_hex=hex_code.lower(),
                observed_at=observed_at,
                lat=float(lat),
                lon=float(lon),
                altitude_ft=altitude,
                on_ground=on_ground,
                ground_speed_kt=float(ac["gs"]) if ac.get("gs") is not None else None,
                track_deg=float(ac["track"]) if ac.get("track") is not None else None,
                vertical_rate_fpm=int(rate) if isinstance(rate, (int, float)) else None,
                squawk=_clean(ac.get("squawk")),
                callsign=_clean(ac.get("flight")),
                registration=_clean(ac.get("r")),
                type_code=_clean(ac.get("t")),
                is_military=bool((ac.get("dbFlags") or 0) & DB_FLAG_MILITARY),
            )
        )
    return positions


class AdsbLolClient:
    def __init__(self, session: requests.Session, timeout: float = 30):
        self.session = session
        self.timeout = timeout

    def fetch_area(self, lat: float, lon: float, radius_nm: int) -> list[AircraftPosition]:
        radius_nm = max(1, min(radius_nm, 250))
        resp = self.session.get(f"{BASE_URL}/lat/{lat}/lon/{lon}/dist/{radius_nm}", timeout=self.timeout)
        resp.raise_for_status()
        return parse_aircraft(resp.json())
