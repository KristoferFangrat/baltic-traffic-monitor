"""ADS-B aircraft data from adsb.lol (open data, ODbL).

Docs: https://api.adsb.lol/docs
Endpoints used:
* ``GET /v2/lat/{lat}/lon/{lon}/dist/{radius_nm}`` – live positions (radius max 250 nm)
* ``POST /api/0/routeset`` – origin/destination per callsign. Route data comes from the
  community "standing data" project and is matched on callsign only, so adsb.lol also
  returns ``plausible``: whether the aircraft's position actually lies on that route.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

BASE_URL = "https://api.adsb.lol/v2"
ROUTESET_URL = "https://api.adsb.lol/api/0/routeset"
ROUTESET_MAX_PLANES = 100

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


@dataclass(frozen=True)
class Airport:
    icao: str
    iata: str | None
    name: str | None
    city: str | None
    country: str | None  # ISO 3166 alpha-2


@dataclass(frozen=True)
class FlightRoute:
    callsign: str
    route_codes: str | None  # e.g. "ESSA-EFHK"; None when the route is unknown
    origin: Airport | None
    destination: Airport | None
    plausible: bool | None


def _airport(data: dict[str, Any] | None) -> Airport | None:
    if not data or not data.get("icao"):
        return None
    return Airport(
        icao=str(data["icao"]).upper(),
        iata=_clean(data.get("iata")),
        name=_clean(data.get("name")),
        city=_clean(data.get("location")),
        country=(_clean(data.get("countryiso2")) or "").upper() or None,
    )


def parse_routes(payload: list[dict[str, Any]]) -> list[FlightRoute]:
    """Parse the list returned by ``/api/0/routeset``."""
    routes: list[FlightRoute] = []
    for item in payload or []:
        callsign = _clean(item.get("callsign"))
        if not callsign:
            continue
        codes = _clean(item.get("airport_codes"))
        airports = [a for a in (_airport(x) for x in item.get("_airports") or []) if a]
        if not codes or codes.lower() == "unknown" or len(airports) < 2:
            routes.append(FlightRoute(callsign, None, None, None, None))
            continue
        plausible = item.get("plausible")
        routes.append(
            FlightRoute(
                callsign=callsign,
                route_codes=codes,
                origin=airports[0],
                destination=airports[-1],  # multi-leg routes: final stop
                plausible=bool(plausible) if plausible is not None else None,
            )
        )
    return routes


class AdsbLolClient:
    def __init__(self, session: requests.Session, timeout: float = 30):
        self.session = session
        self.timeout = timeout

    def fetch_area(self, lat: float, lon: float, radius_nm: int) -> list[AircraftPosition]:
        radius_nm = max(1, min(radius_nm, 250))
        resp = self.session.get(f"{BASE_URL}/lat/{lat}/lon/{lon}/dist/{radius_nm}", timeout=self.timeout)
        resp.raise_for_status()
        return parse_aircraft(resp.json())

    def fetch_routes(self, planes: Sequence[tuple[str, float, float]]) -> list[FlightRoute]:
        """Look up routes for ``(callsign, lat, lon)`` tuples, max 100 per request."""
        routes: list[FlightRoute] = []
        for i in range(0, len(planes), ROUTESET_MAX_PLANES):
            batch = planes[i : i + ROUTESET_MAX_PLANES]
            resp = self.session.post(
                ROUTESET_URL,
                json={"planes": [{"callsign": c, "lat": lat, "lng": lon} for c, lat, lon in batch]},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            routes.extend(parse_routes(resp.json()))
        return routes
