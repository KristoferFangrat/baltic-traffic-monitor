"""Integration tests against a real PostgreSQL (set TEST_DATABASE_URL)."""

from datetime import UTC, datetime, timedelta

import pytest

from btm import db
from btm.seed import seed_demo_data
from btm.sources.adsb import parse_aircraft, parse_routes
from btm.sources.ais import VesselPosition, parse_locations, parse_vessels

pytestmark = pytest.mark.integration


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_loads_are_idempotent(pg_conn, load_fixture):
    positions = parse_locations(load_fixture("ais_locations.json"))
    aircraft = parse_aircraft(load_fixture("adsb.json"))

    assert db.insert_vessel_positions(pg_conn, positions) == 2
    assert db.insert_vessel_positions(pg_conn, positions) == 0  # re-run inserts nothing
    assert db.insert_aircraft_positions(pg_conn, aircraft) == 3
    assert db.insert_aircraft_positions(pg_conn, aircraft) == 0
    assert count(pg_conn, "vessel_positions") == 2
    assert count(pg_conn, "aircraft") == 3


def test_vessel_upsert_keeps_known_values(pg_conn, load_fixture):
    db.upsert_vessels(pg_conn, parse_vessels(load_fixture("ais_vessels.json")))
    # second load without a name must not wipe the stored name
    from btm.sources.ais import Vessel

    db.upsert_vessels(pg_conn, [Vessel(265547250, None, None, None, None, "SESTO", 7.0)])
    name, dest = pg_conn.execute("SELECT name, destination FROM vessels WHERE mmsi = 265547250").fetchone()
    assert (name, dest) == ("DEMO TRADER", "SESTO")


def test_ais_gap_view_detects_silence(pg_conn):
    now = datetime.now(UTC)
    rows = [
        VesselPosition(111, now - timedelta(minutes=90), 59.0, 19.0, 12.0, 90.0, 90, 0),
        VesselPosition(111, now - timedelta(minutes=85), 59.0, 19.03, 12.0, 90.0, 90, 0),
        # 45 minutes of silence
        VesselPosition(111, now - timedelta(minutes=40), 59.0, 19.30, 12.0, 90.0, 90, 0),
    ]
    db.insert_vessel_positions(pg_conn, rows)

    gaps = pg_conn.execute("SELECT mmsi, gap, distance_km FROM v_ais_gaps").fetchall()
    assert len(gaps) == 1
    mmsi, gap, distance = gaps[0]
    assert mmsi == 111 and gap == timedelta(minutes=45)
    assert 14 < float(distance) < 17  # ~0.27° longitude at 59°N


def test_ingestion_run_logs_errors(pg_conn):
    with pytest.raises(RuntimeError):
        with db.ingestion_run(pg_conn, "adsb") as run:
            run.fetched = 5
            raise RuntimeError("API down")

    status, fetched, msg = pg_conn.execute("SELECT status, fetched, error_message FROM ingestion_runs").fetchone()
    assert status == "error" and fetched == 5 and "API down" in msg


def test_demo_seed_populates_every_view(pg_conn):
    seed_demo_data(pg_conn)
    for view in (
        "v_latest_vessels",
        "v_latest_aircraft",
        "v_ais_gaps",
        "v_loitering",
        "v_emergency_squawks",
        "v_hourly_traffic",
        "v_pipeline_health",
    ):
        assert count(pg_conn, view) > 0, f"{view} is empty"


def test_purge_removes_old_rows(pg_conn):
    old = datetime.now(UTC) - timedelta(days=10)
    db.insert_vessel_positions(pg_conn, [VesselPosition(222, old, 59, 19, 1, 1, 1, 0)])
    assert db.purge_old_positions(pg_conn, retention_days=7) == 1


def test_vessel_destinations_are_interpreted_on_load(pg_conn):
    from btm.sources.ais import Vessel

    db.upsert_vessels(
        pg_conn,
        [
            Vessel(1, "A", 70, None, None, "FI HEL", None),
            Vessel(2, "B", 80, None, None, "FOR ORDERS", None),
            Vessel(3, "C", 60, None, None, "XX123", None),
        ],
    )
    rows = pg_conn.execute("SELECT mmsi, dest_locode, dest_name, dest_country FROM vessels ORDER BY mmsi").fetchall()
    assert rows == [
        (1, "FIHEL", "Helsingfors (Helsinki)", "FI"),
        (2, None, "Inväntar order", None),
        (3, None, None, None),
    ]


def test_backfill_interprets_existing_vessels(pg_conn):
    pg_conn.execute("INSERT INTO vessels (mmsi, destination) VALUES (10, 'SE STO'), (11, 'XX123')")
    pg_conn.commit()
    assert db.backfill_vessel_destinations(pg_conn) == 1
    assert pg_conn.execute("SELECT dest_locode FROM vessels WHERE mmsi = 10").fetchone() == ("SESTO",)


def test_routes_join_into_latest_aircraft_and_destination_view(pg_conn, load_fixture):
    db.insert_aircraft_positions(pg_conn, parse_aircraft(load_fixture("adsb.json")))
    # adsb.json has SAS1421 at 'now' in 2026 – move it into the 10-minute window
    pg_conn.execute("UPDATE aircraft_positions SET observed_at = now()")
    pg_conn.commit()

    # Only SAS1421 is airborne with a callsign and no route yet
    assert [c for c, *_ in db.callsigns_needing_routes(pg_conn)] == ["DEMO01", "SAS1421"]

    db.upsert_flight_routes(pg_conn, parse_routes(load_fixture("routeset.json")))
    assert db.callsigns_needing_routes(pg_conn) == []  # fresh routes are not looked up again

    row = pg_conn.execute(
        "SELECT origin_iata, dest_iata, dest_city, route_plausible FROM v_latest_aircraft WHERE callsign = 'SAS1421'"
    ).fetchone()
    assert row == ("ARN", "HEL", "Helsinki", True)

    dest = dict(
        pg_conn.execute("SELECT dest_city, aircraft FROM v_flight_destinations WHERE category = 'Civilt'").fetchall()
    )
    assert dest["Helsinki"] == 1


def test_vessel_destination_view_groups_unresolved_text(pg_conn):
    from btm.sources.ais import Vessel

    now = datetime.now(UTC)
    db.upsert_vessels(
        pg_conn, [Vessel(1, "A", 70, None, None, "FIHEL", None), Vessel(2, "B", 70, None, None, "XX", None)]
    )
    db.insert_vessel_positions(pg_conn, [VesselPosition(m, now, 59, 19, 10, 90, 90, 0) for m in (1, 2)])
    rows = pg_conn.execute(
        "SELECT dest_name, resolved, vessels FROM v_vessel_destinations ORDER BY dest_name"
    ).fetchall()
    assert rows == [("Helsingfors (Helsinki)", True, 1), ("XX", False, 1)]
