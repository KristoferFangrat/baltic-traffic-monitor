"""Synthetic demo data so the dashboard can be shown without network access.

Generates ~6 hours of vessel traffic along real Baltic shipping lanes and
aircraft around Stockholm, including one example of every anomaly the views
detect (AIS gap, loitering vessel, emergency squawk).
All names, MMSI numbers and registrations are fictional.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import UTC, datetime, timedelta

import psycopg

from btm import db
from btm.sources.adsb import AircraftPosition
from btm.sources.ais import Vessel, VesselPosition

log = logging.getLogger(__name__)

# (lat, lon) waypoints of common routes
ROUTES: list[list[tuple[float, float]]] = [
    [(59.32, 18.10), (59.45, 19.50), (59.90, 22.00), (60.15, 24.95)],  # Stockholm–Helsinki
    [(59.32, 18.10), (60.10, 19.93), (60.45, 22.25)],  # Stockholm–Åland–Turku
    [(59.45, 24.75), (60.15, 24.95)],  # Tallinn–Helsinki
    [
        (55.30, 13.00),
        (55.80, 15.50),
        (57.00, 18.50),
        (58.50, 20.50),
        (59.60, 23.50),
        (60.00, 26.50),
    ],  # Main Baltic lane
    [(57.64, 18.29), (58.90, 17.95)],  # Visby–Nynäshamn
    [(54.40, 18.70), (55.50, 17.00), (56.60, 16.40)],  # Gdańsk–Kalmar sound
    [(56.95, 24.10), (57.80, 22.00), (58.50, 20.50)],  # Riga–open sea
]

SHIP_TYPES = [70, 70, 70, 71, 79, 80, 80, 84, 60, 60, 69, 30, 52, 36, 37]
NAME_A = ["NORDIC", "BALTIC", "SEA", "NORTHERN", "AURORA", "POLAR", "AMBER", "SILVER", "OCEAN"]
NAME_B = ["STAR", "WIND", "SPIRIT", "TRADER", "PIONEER", "HARMONY", "CARRIER", "SPRING", "QUEEN"]
MID = [265, 266, 230, 276, 275, 277, 261, 211, 219]  # SE, SE, FI, EE, LV, LT, PL, DE, DK

AIRLINES = ["SAS", "FIN", "NAX", "DLH", "BTI", "KLM", "AFR", "BAW", "WZZ", "RYR"]
TYPES = ["A320", "A20N", "A321", "B738", "B38M", "A359", "E190", "AT76", "CRJ9", "B77W"]


def _interp(route: list[tuple[float, float]], t: float) -> tuple[float, float, float]:
    """Point and course (deg) at fraction t ∈ [0, 1] of a polyline."""
    segs = list(zip(route, route[1:], strict=False))
    lengths = [math.dist(a, b) for a, b in segs]
    target = t * sum(lengths)
    for (a, b), length in zip(segs, lengths, strict=False):
        if target <= length or (a, b) == segs[-1]:
            f = 0 if length == 0 else min(target / length, 1)
            lat = a[0] + (b[0] - a[0]) * f
            lon = a[1] + (b[1] - a[1]) * f
            course = math.degrees(math.atan2((b[1] - a[1]) * math.cos(math.radians(lat)), b[0] - a[0]))
            return lat, lon, course % 360
        target -= length
    raise AssertionError("unreachable")


def _vessels(rng: random.Random, now: datetime) -> tuple[list[Vessel], list[VesselPosition]]:
    vessels: list[Vessel] = []
    positions: list[VesselPosition] = []
    step = timedelta(minutes=5)
    n_steps = 6 * 12  # 6 hours

    for i in range(70):
        mmsi = rng.choice(MID) * 1_000_000 + rng.randint(100_000, 999_999)
        ship_type = rng.choice(SHIP_TYPES)
        route = ROUTES[3] if i == 0 else rng.choice(ROUTES)  # gap example on the long main lane
        if rng.random() < 0.5:
            route = list(reversed(route))
        vessels.append(
            Vessel(
                mmsi=mmsi,
                name=f"{rng.choice(NAME_A)} {rng.choice(NAME_B)} {i:02d}",
                ship_type=ship_type,
                call_sign=f"DEMO{i:03d}",
                imo=9_000_000 + rng.randint(0, 999_999),
                destination=rng.choice(["SESTO", "FIHEL", "EETLL", "FITKU", "LVRIX", "PLGDN", "SEVBY"]),
                draught_m=round(rng.uniform(3, 11), 1),
            )
        )
        speed = rng.uniform(9, 19)  # knots
        progress = rng.uniform(0, 0.6)
        route_nm = sum(math.dist(a, b) for a, b in zip(route, route[1:], strict=False)) * 60 * 0.75
        gap_vessel = i == 0
        for k in range(n_steps + 1):
            ts = now - (n_steps - k) * step
            if gap_vessel and 30 <= k <= 40:  # ~1 h silence while moving
                progress += speed * (5 / 60) / route_nm
                continue
            # Ferries shuttle back and forth: progress 1→2 is the return leg
            leg = progress % 2
            if leg <= 1:
                lat, lon, course = _interp(route, leg)
            else:
                lat, lon, course = _interp(route[::-1], leg - 1)
            positions.append(
                VesselPosition(
                    mmsi=mmsi,
                    observed_at=ts - timedelta(seconds=rng.randint(0, 50)),
                    lat=lat + rng.gauss(0, 0.002),
                    lon=lon + rng.gauss(0, 0.002),
                    sog_knots=round(speed + rng.gauss(0, 0.4), 1),
                    cog_deg=round(course, 1),
                    heading_deg=int(course),
                    nav_status=0,
                )
            )
            progress += speed * (5 / 60) / route_nm

    # Loitering vessel: "under way" but drifting inside a ~1 km box for 5 h
    loiter_mmsi = 273_000_000 + rng.randint(100_000, 999_999)
    vessels.append(Vessel(loiter_mmsi, "DEMO LOITERER", 70, "DEMOLTR", None, "UNKNOWN", 6.5))
    centre = (57.95, 19.40)
    for k in range(5 * 12):
        positions.append(
            VesselPosition(
                mmsi=loiter_mmsi,
                observed_at=now - timedelta(minutes=5 * k),
                lat=centre[0] + rng.gauss(0, 0.002),
                lon=centre[1] + rng.gauss(0, 0.004),
                sog_knots=round(abs(rng.gauss(0.8, 0.3)), 1),
                cog_deg=round(rng.uniform(0, 359), 1),
                heading_deg=None,
                nav_status=0,
            )
        )
    return vessels, positions


def _aircraft(rng: random.Random, now: datetime) -> list[AircraftPosition]:
    rows: list[AircraftPosition] = []
    centre = (59.65, 17.92)  # Arlanda
    for i in range(55):
        hex_code = f"4a{rng.randint(0, 0xFFFF):04x}"
        airline = rng.choice(AIRLINES)
        callsign = f"{airline}{rng.randint(10, 9999)}"
        type_code = rng.choice(TYPES)
        military = i in (1, 2)
        squawk = "7700" if i == 0 else f"{rng.randint(1, 6)}{rng.randint(0, 7)}{rng.randint(0, 7)}{rng.randint(0, 7)}"
        heading = rng.uniform(0, 360)
        dist = rng.uniform(0.2, 3.0)  # degrees from centre
        bearing = rng.uniform(0, 2 * math.pi)
        lat0 = centre[0] + dist * math.cos(bearing) * 0.9
        lon0 = centre[1] + dist * math.sin(bearing) * 1.8
        altitude = rng.choice([0, 3000, 8000, 15000, 24000, 33000, 36000, 38000])
        speed = 0.0 if altitude == 0 else 180 + altitude / 100
        # minute-by-minute track for the last 8 minutes (shows on the live map)
        for m in range(8):
            ts = (now - timedelta(minutes=m)).replace(microsecond=0)
            back = speed / 60 * m / 60  # degrees travelled (approx.)
            lat = lat0 - back * math.cos(math.radians(heading))
            lon = lon0 - back * math.sin(math.radians(heading)) * 1.8
            rows.append(
                AircraftPosition(
                    icao_hex=hex_code,
                    observed_at=ts,
                    lat=lat,
                    lon=lon,
                    altitude_ft=altitude,
                    on_ground=altitude == 0,
                    ground_speed_kt=round(speed, 0),
                    track_deg=round(heading, 1),
                    vertical_rate_fpm=0,
                    squawk=squawk,
                    callsign=f"DEMO{i:02d}" if military else callsign,
                    registration=f"SE-{''.join(rng.choices('ABCDEFGHIJKLMNOPRSTUVXYZ', k=3))}",
                    type_code="C130" if military else type_code,
                    is_military=military,
                )
            )
    # Historic volume for the hourly chart: 6 hours, 30–60 aircraft per hour
    for h in range(1, 7):
        for _j in range(rng.randint(30, 60)):
            rows.append(
                AircraftPosition(
                    icao_hex=f"4b{rng.randint(0, 0xFFFF):04x}",
                    observed_at=(now - timedelta(hours=h, minutes=rng.randint(0, 59))).replace(microsecond=0),
                    lat=centre[0] + rng.uniform(-3, 3),
                    lon=centre[1] + rng.uniform(-5, 5),
                    altitude_ft=rng.choice([15000, 33000, 37000]),
                    on_ground=False,
                    ground_speed_kt=440.0,
                    track_deg=rng.uniform(0, 360),
                    vertical_rate_fpm=0,
                    squawk="1000",
                    callsign=f"{rng.choice(AIRLINES)}{rng.randint(10, 9999)}",
                    registration=None,
                    type_code=rng.choice(TYPES),
                    is_military=False,
                )
            )
    return rows


def seed_demo_data(conn: psycopg.Connection, seed: int = 42) -> None:
    rng = random.Random(seed)
    now = datetime.now(UTC)
    vessels, vessel_positions = _vessels(rng, now)
    aircraft = _aircraft(rng, now)

    with db.ingestion_run(conn, "demo_seed") as run:
        run.fetched = len(vessel_positions) + len(aircraft)
        db.upsert_vessels(conn, vessels)
        run.inserted = db.insert_vessel_positions(conn, vessel_positions)
        run.inserted += db.insert_aircraft_positions(conn, aircraft)
    log.info(
        "Demo data loaded: %d vessels, %d vessel positions, %d aircraft positions",
        len(vessels),
        len(vessel_positions),
        len(aircraft),
    )
