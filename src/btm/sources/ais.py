"""AIS vessel data from Fintraffic Digitraffic (open data, CC BY 4.0).

Docs: https://www.digitraffic.fi/en/marine-traffic/
No API key is needed, but Digitraffic asks clients to identify themselves
with the ``Digitraffic-User`` header.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

BASE_URL = "https://meri.digitraffic.fi/api/ais/v1"

HEADING_NOT_AVAILABLE = 511
COG_NOT_AVAILABLE = 360
SOG_NOT_AVAILABLE = 102.3


@dataclass(frozen=True)
class VesselPosition:
    mmsi: int
    observed_at: datetime
    lat: float
    lon: float
    sog_knots: float | None
    cog_deg: float | None
    heading_deg: int | None
    nav_status: int | None


@dataclass(frozen=True)
class Vessel:
    mmsi: int
    name: str | None
    ship_type: int | None
    call_sign: str | None
    imo: int | None
    destination: str | None
    draught_m: float | None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip("@").strip()
    return text or None


def parse_locations(payload: dict[str, Any]) -> list[VesselPosition]:
    """Parse the GeoJSON FeatureCollection returned by ``/locations``."""
    positions: list[VesselPosition] = []
    for feature in payload.get("features", []):
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])  # GeoJSON order is lon, lat
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        ts_ms = props.get("timestampExternal")
        mmsi = props.get("mmsi") or feature.get("mmsi")
        if ts_ms is None or mmsi is None:
            continue

        sog = props.get("sog")
        cog = props.get("cog")
        heading = props.get("heading")

        positions.append(
            VesselPosition(
                mmsi=int(mmsi),
                observed_at=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                lat=lat,
                lon=lon,
                sog_knots=None if sog is None or sog >= SOG_NOT_AVAILABLE else float(sog),
                cog_deg=None if cog is None or cog >= COG_NOT_AVAILABLE else float(cog),
                heading_deg=None if heading in (None, HEADING_NOT_AVAILABLE) else int(heading),
                nav_status=props.get("navStat"),
            )
        )
    return positions


def parse_vessels(payload: Iterable[dict[str, Any]]) -> list[Vessel]:
    """Parse the vessel metadata list returned by ``/vessels``."""
    vessels: list[Vessel] = []
    for item in payload:
        mmsi = item.get("mmsi")
        if mmsi is None:
            continue
        imo = item.get("imo")
        draught = item.get("draught")
        vessels.append(
            Vessel(
                mmsi=int(mmsi),
                name=_clean_text(item.get("name")),
                ship_type=item.get("shipType"),
                call_sign=_clean_text(item.get("callSign")),
                imo=int(imo) if imo else None,
                # Digitraffic reports draught in decimetres
                draught_m=round(draught / 10, 1) if draught else None,
                destination=_clean_text(item.get("destination")),
            )
        )
    return vessels


class DigitrafficClient:
    def __init__(self, session: requests.Session, user_agent: str, timeout: float = 30):
        self.session = session
        self.timeout = timeout
        self.session.headers["Digitraffic-User"] = user_agent

    def fetch_locations(self, lat: float, lon: float, radius_km: int) -> list[VesselPosition]:
        resp = self.session.get(
            f"{BASE_URL}/locations",
            params={"latitude": lat, "longitude": lon, "radius": radius_km},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return parse_locations(resp.json())

    def fetch_vessels(self) -> list[Vessel]:
        resp = self.session.get(f"{BASE_URL}/vessels", timeout=self.timeout)
        resp.raise_for_status()
        return parse_vessels(resp.json())
