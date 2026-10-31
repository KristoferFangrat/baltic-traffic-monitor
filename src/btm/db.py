"""PostgreSQL access: schema setup, idempotent loads and run logging."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import astuple
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from btm.destinations import resolve_ais_destination
from btm.sources.adsb import AircraftPosition, FlightRoute
from btm.sources.ais import Vessel, VesselPosition

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def connect(database_url: str) -> psycopg.Connection:
    conn = psycopg.connect(database_url, autocommit=False)
    conn.execute("SET search_path TO traffic, public")
    return conn


def init_schema(conn: psycopg.Connection, sql_dir: Path = SQL_DIR) -> None:
    """Apply every ``*.sql`` file in order. All statements are idempotent."""
    for path in sorted(sql_dir.glob("*.sql")):
        log.info("Applying %s", path.name)
        conn.execute(path.read_text(encoding="utf-8"))
    conn.commit()


# ---------------------------------------------------------------------------
# Loads – staging table + INSERT ... ON CONFLICT keeps re-runs idempotent
# ---------------------------------------------------------------------------
def insert_vessel_positions(conn: psycopg.Connection, rows: Sequence[VesselPosition]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS stg_vessel_positions
                (LIKE vessel_positions INCLUDING DEFAULTS) ON COMMIT DELETE ROWS
            """
        )
        with cur.copy(
            "COPY stg_vessel_positions (mmsi, observed_at, lat, lon, sog_knots, cog_deg, "
            "heading_deg, nav_status) FROM STDIN"
        ) as copy:
            for r in rows:
                copy.write_row(astuple(r))
        cur.execute(
            """
            INSERT INTO vessel_positions
                (mmsi, observed_at, lat, lon, sog_knots, cog_deg, heading_deg, nav_status)
            SELECT mmsi, observed_at, lat, lon, sog_knots, cog_deg, heading_deg, nav_status
            FROM stg_vessel_positions
            ON CONFLICT (mmsi, observed_at) DO NOTHING
            """
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


def upsert_vessels(conn: psycopg.Connection, rows: Sequence[Vessel]) -> int:
    if not rows:
        return 0
    params = []
    for r in rows:
        dest = resolve_ais_destination(r.destination)
        params.append(
            (r.mmsi, r.name, r.ship_type, r.call_sign, r.imo, r.destination, r.draught_m,
             dest.locode, dest.name, dest.country)
        )  # fmt: skip
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO vessels (mmsi, name, ship_type, call_sign, imo, destination, draught_m,
                                 dest_locode, dest_name, dest_country)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (mmsi) DO UPDATE SET
                name         = COALESCE(EXCLUDED.name, vessels.name),
                ship_type    = COALESCE(EXCLUDED.ship_type, vessels.ship_type),
                call_sign    = COALESCE(EXCLUDED.call_sign, vessels.call_sign),
                imo          = COALESCE(EXCLUDED.imo, vessels.imo),
                destination  = EXCLUDED.destination,
                draught_m    = EXCLUDED.draught_m,
                dest_locode  = EXCLUDED.dest_locode,
                dest_name    = EXCLUDED.dest_name,
                dest_country = EXCLUDED.dest_country,
                updated_at   = now()
            """,
            params,
        )
    conn.commit()
    return len(rows)


def backfill_vessel_destinations(conn: psycopg.Connection) -> int:
    """Interpret destinations for vessels stored before destination parsing existed."""
    rows = conn.execute(
        "SELECT mmsi, destination FROM vessels WHERE destination IS NOT NULL AND dest_name IS NULL"
    ).fetchall()
    updates = []
    for mmsi, raw in rows:
        dest = resolve_ais_destination(raw)
        if dest.resolved:
            updates.append((dest.locode, dest.name, dest.country, mmsi))
    if updates:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE vessels SET dest_locode = %s, dest_name = %s, dest_country = %s WHERE mmsi = %s",
                updates,
            )
    conn.commit()
    return len(updates)


def insert_aircraft_positions(conn: psycopg.Connection, rows: Sequence[AircraftPosition]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO aircraft (icao_hex, registration, type_code, last_callsign, is_military)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (icao_hex) DO UPDATE SET
                registration  = COALESCE(EXCLUDED.registration, aircraft.registration),
                type_code     = COALESCE(EXCLUDED.type_code, aircraft.type_code),
                last_callsign = COALESCE(EXCLUDED.last_callsign, aircraft.last_callsign),
                is_military   = aircraft.is_military OR EXCLUDED.is_military,
                updated_at    = now()
            """,
            # dedupe within the batch so ON CONFLICT doesn't hit the same row twice
            list(
                {
                    r.icao_hex: (r.icao_hex, r.registration, r.type_code, r.callsign, r.is_military) for r in rows
                }.values()
            ),
        )
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS stg_aircraft_positions
                (LIKE aircraft_positions INCLUDING DEFAULTS) ON COMMIT DELETE ROWS
            """
        )
        with cur.copy(
            "COPY stg_aircraft_positions (icao_hex, observed_at, lat, lon, altitude_ft, on_ground, "
            "ground_speed_kt, track_deg, vertical_rate_fpm, squawk, callsign) FROM STDIN"
        ) as copy:
            for r in rows:
                copy.write_row(
                    (
                        r.icao_hex,
                        r.observed_at,
                        r.lat,
                        r.lon,
                        r.altitude_ft,
                        r.on_ground,
                        r.ground_speed_kt,
                        r.track_deg,
                        r.vertical_rate_fpm,
                        r.squawk,
                        r.callsign,
                    )
                )
        cur.execute(
            """
            INSERT INTO aircraft_positions
                (icao_hex, observed_at, lat, lon, altitude_ft, on_ground, ground_speed_kt,
                 track_deg, vertical_rate_fpm, squawk, callsign)
            SELECT icao_hex, observed_at, lat, lon, altitude_ft, on_ground, ground_speed_kt,
                   track_deg, vertical_rate_fpm, squawk, callsign
            FROM stg_aircraft_positions
            ON CONFLICT (icao_hex, observed_at) DO NOTHING
            """
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Flight routes
# ---------------------------------------------------------------------------
def callsigns_needing_routes(conn: psycopg.Connection, limit: int = 300) -> list[tuple[str, float, float]]:
    """Callsigns currently in the air whose route is missing or stale.

    Known routes are refreshed after 12 h, unknown ones after 1 h (new callsigns are
    added to the community route database over time).
    """
    return conn.execute(
        """
        SELECT DISTINCT ON (la.callsign) la.callsign, la.lat, la.lon
        FROM v_latest_aircraft la
        LEFT JOIN flight_routes r ON r.callsign = la.callsign
        WHERE la.callsign IS NOT NULL
          AND (r.callsign IS NULL
               OR r.fetched_at < now() - CASE WHEN r.route_codes IS NULL
                                              THEN INTERVAL '1 hour' ELSE INTERVAL '12 hours' END)
        ORDER BY la.callsign, la.observed_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()


def upsert_flight_routes(conn: psycopg.Connection, routes: Sequence[FlightRoute]) -> int:
    if not routes:
        return 0

    def ap(a, field):
        return getattr(a, field) if a else None

    params = [
        (
            r.callsign, r.route_codes,
            ap(r.origin, "icao"), ap(r.origin, "iata"), ap(r.origin, "name"),
            ap(r.origin, "city"), ap(r.origin, "country"),
            ap(r.destination, "icao"), ap(r.destination, "iata"), ap(r.destination, "name"),
            ap(r.destination, "city"), ap(r.destination, "country"),
            r.plausible,
        )
        for r in {r.callsign: r for r in routes}.values()
    ]  # fmt: skip
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO flight_routes (callsign, route_codes,
                origin_icao, origin_iata, origin_name, origin_city, origin_country,
                dest_icao, dest_iata, dest_name, dest_city, dest_country, plausible, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (callsign) DO UPDATE SET
                route_codes = EXCLUDED.route_codes,
                origin_icao = EXCLUDED.origin_icao, origin_iata = EXCLUDED.origin_iata,
                origin_name = EXCLUDED.origin_name, origin_city = EXCLUDED.origin_city,
                origin_country = EXCLUDED.origin_country,
                dest_icao = EXCLUDED.dest_icao, dest_iata = EXCLUDED.dest_iata,
                dest_name = EXCLUDED.dest_name, dest_city = EXCLUDED.dest_city,
                dest_country = EXCLUDED.dest_country,
                plausible = EXCLUDED.plausible,
                fetched_at = now()
            """,
            params,
        )
    conn.commit()
    return len(params)


def purge_old_positions(conn: psycopg.Connection, retention_days: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM vessel_positions WHERE observed_at < now() - make_interval(days => %s)",
            (retention_days,),
        )
        deleted = cur.rowcount
        cur.execute(
            "DELETE FROM aircraft_positions WHERE observed_at < now() - make_interval(days => %s)",
            (retention_days,),
        )
        deleted += cur.rowcount
    conn.commit()
    return deleted


# ---------------------------------------------------------------------------
# Run logging
# ---------------------------------------------------------------------------
class RunLog:
    def __init__(self) -> None:
        self.fetched = 0
        self.inserted = 0


@contextmanager
def ingestion_run(conn: psycopg.Connection, source: str) -> Iterator[RunLog]:
    """Record a pipeline run in ``ingestion_runs`` – success or failure."""
    started = datetime.now(UTC)
    run_id = conn.execute(
        "INSERT INTO ingestion_runs (source, started_at, status) VALUES (%s, %s, 'running') RETURNING id",
        (source, started),
    ).fetchone()[0]
    conn.commit()

    run = RunLog()
    try:
        yield run
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE ingestion_runs SET finished_at = now(), status = 'error', "
            "fetched = %s, inserted = %s, error_message = %s WHERE id = %s",
            (run.fetched, run.inserted, f"{type(exc).__name__}: {exc}"[:2000], run_id),
        )
        conn.commit()
        raise
    else:
        conn.execute(
            "UPDATE ingestion_runs SET finished_at = now(), status = 'ok', fetched = %s, inserted = %s WHERE id = %s",
            (run.fetched, run.inserted, run_id),
        )
        conn.commit()
