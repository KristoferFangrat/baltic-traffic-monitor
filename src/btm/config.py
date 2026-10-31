"""Runtime configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    database_url: str
    user_agent: str

    # AIS (Digitraffic) – circle in kilometres
    ais_lat: float
    ais_lon: float
    ais_radius_km: int
    ais_poll_seconds: int
    ais_vessel_refresh_seconds: int

    # ADS-B (adsb.lol) – circle in nautical miles (max 250)
    adsb_lat: float
    adsb_lon: float
    adsb_radius_nm: int
    adsb_poll_seconds: int
    route_poll_seconds: int

    # Retention
    retention_days: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv("DATABASE_URL", "postgresql://btm:btm@localhost:5433/btm"),
            user_agent=os.getenv(
                "USER_AGENT",
                "baltic-traffic-monitor/0.1 (+https://github.com/KristoferFangrat/baltic-traffic-monitor)",
            ),
            ais_lat=_float("AIS_LAT", 59.0),
            ais_lon=_float("AIS_LON", 19.5),
            ais_radius_km=_int("AIS_RADIUS_KM", 300),
            ais_poll_seconds=_int("AIS_POLL_SECONDS", 120),
            ais_vessel_refresh_seconds=_int("AIS_VESSEL_REFRESH_SECONDS", 3600),
            adsb_lat=_float("ADSB_LAT", 59.33),
            adsb_lon=_float("ADSB_LON", 18.07),
            adsb_radius_nm=min(_int("ADSB_RADIUS_NM", 200), 250),
            adsb_poll_seconds=_int("ADSB_POLL_SECONDS", 60),
            route_poll_seconds=_int("ROUTE_POLL_SECONDS", 120),
            retention_days=_int("RETENTION_DAYS", 7),
        )
