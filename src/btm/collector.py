"""Collector entry point.

python -m btm.collector init-db   # create schema + views
python -m btm.collector once      # one poll of every source
python -m btm.collector run       # poll forever on the configured intervals
python -m btm.collector seed      # load synthetic demo data (no internet needed)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import psycopg

from btm import db
from btm.config import Settings
from btm.sources.adsb import AdsbLolClient
from btm.sources.ais import DigitrafficClient
from btm.sources.http import make_session

log = logging.getLogger("btm.collector")


@dataclass
class Job:
    name: str
    interval: int
    func: Callable[[], None]
    next_run: float = field(default=0.0)


class Collector:
    def __init__(self, settings: Settings, conn: psycopg.Connection):
        self.s = settings
        self.conn = conn
        session = make_session(settings.user_agent)
        self.ais = DigitrafficClient(session, settings.user_agent)
        self.adsb = AdsbLolClient(session)
        self._stop = False

    # -- individual jobs ---------------------------------------------------
    def collect_ais_vessels(self) -> None:
        with db.ingestion_run(self.conn, "ais_vessels") as run:
            vessels = self.ais.fetch_vessels()
            run.fetched = len(vessels)
            run.inserted = db.upsert_vessels(self.conn, vessels)
        log.info("AIS vessels: %d upserted", run.inserted)

    def collect_ais_positions(self) -> None:
        with db.ingestion_run(self.conn, "ais_locations") as run:
            rows = self.ais.fetch_locations(self.s.ais_lat, self.s.ais_lon, self.s.ais_radius_km)
            run.fetched = len(rows)
            run.inserted = db.insert_vessel_positions(self.conn, rows)
        log.info("AIS positions: %d fetched, %d new", run.fetched, run.inserted)

    def collect_adsb(self) -> None:
        with db.ingestion_run(self.conn, "adsb") as run:
            rows = self.adsb.fetch_area(self.s.adsb_lat, self.s.adsb_lon, self.s.adsb_radius_nm)
            run.fetched = len(rows)
            run.inserted = db.insert_aircraft_positions(self.conn, rows)
        log.info("ADS-B positions: %d fetched, %d new", run.fetched, run.inserted)

    def purge(self) -> None:
        deleted = db.purge_old_positions(self.conn, self.s.retention_days)
        log.info("Retention: deleted %d rows older than %d days", deleted, self.s.retention_days)

    # -- scheduling --------------------------------------------------------
    def jobs(self) -> list[Job]:
        return [
            Job("ais_vessels", self.s.ais_vessel_refresh_seconds, self.collect_ais_vessels),
            Job("ais_positions", self.s.ais_poll_seconds, self.collect_ais_positions),
            Job("adsb", self.s.adsb_poll_seconds, self.collect_adsb),
            Job("purge", 6 * 3600, self.purge),
        ]

    def run_once(self) -> None:
        for job in self.jobs():
            self._safe(job)

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_stop", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "_stop", True))
        jobs = self.jobs()
        log.info("Collector started with %d jobs", len(jobs))
        while not self._stop:
            now = time.monotonic()
            for job in jobs:
                if now >= job.next_run:
                    self._safe(job)
                    job.next_run = time.monotonic() + job.interval
            time.sleep(1)
        log.info("Collector stopped")

    @staticmethod
    def _safe(job: Job) -> None:
        # One failing source must never stop the others; the failure is logged
        # in ingestion_runs and shown in the dashboard.
        try:
            job.func()
        except Exception:
            log.exception("Job %s failed", job.name)


def wait_for_db(url: str, attempts: int = 30) -> psycopg.Connection:
    for i in range(1, attempts + 1):
        try:
            return db.connect(url)
        except psycopg.OperationalError:
            log.info("Waiting for database (%d/%d)...", i, attempts)
            time.sleep(2)
    raise SystemExit("Database not reachable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baltic Traffic Monitor collector")
    parser.add_argument("command", choices=["init-db", "once", "run", "seed"])
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    conn = wait_for_db(settings.database_url)
    db.init_schema(conn)

    if args.command == "init-db":
        log.info("Schema ready")
    elif args.command == "seed":
        from btm.seed import seed_demo_data

        seed_demo_data(conn)
    else:
        collector = Collector(settings, conn)
        collector.run_once() if args.command == "once" else collector.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
