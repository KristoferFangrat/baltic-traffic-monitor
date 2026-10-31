-- Baltic Traffic Monitor – core schema
-- Raw positions are append-only; reference tables (vessels, aircraft) are upserted.

CREATE SCHEMA IF NOT EXISTS traffic;
SET search_path TO traffic, public;

-- ---------------------------------------------------------------------------
-- Maritime (AIS via Fintraffic Digitraffic)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vessels (
    mmsi         BIGINT PRIMARY KEY,
    name         TEXT,
    ship_type    INTEGER,
    call_sign    TEXT,
    imo          BIGINT,
    destination  TEXT,
    draught_m    NUMERIC(5, 1),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Destination interpreted from the free-text AIS field (see btm.destinations).
-- ADD COLUMN IF NOT EXISTS keeps this file safe to re-run on an existing database.
ALTER TABLE vessels ADD COLUMN IF NOT EXISTS dest_locode  TEXT;   -- UN/LOCODE, e.g. FIHEL
ALTER TABLE vessels ADD COLUMN IF NOT EXISTS dest_name    TEXT;   -- port name or status text
ALTER TABLE vessels ADD COLUMN IF NOT EXISTS dest_country TEXT;   -- ISO 3166 alpha-2

CREATE TABLE IF NOT EXISTS vessel_positions (
    id           BIGSERIAL PRIMARY KEY,
    mmsi         BIGINT       NOT NULL,
    observed_at  TIMESTAMPTZ  NOT NULL,
    lat          DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon          DOUBLE PRECISION NOT NULL CHECK (lon BETWEEN -180 AND 180),
    sog_knots    REAL,
    cog_deg      REAL,
    heading_deg  SMALLINT,
    nav_status   SMALLINT,
    ingested_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_vessel_position UNIQUE (mmsi, observed_at)
);

CREATE INDEX IF NOT EXISTS ix_vessel_positions_time ON vessel_positions (observed_at DESC);
CREATE INDEX IF NOT EXISTS ix_vessel_positions_mmsi_time ON vessel_positions (mmsi, observed_at DESC);

-- ---------------------------------------------------------------------------
-- Aviation (ADS-B via adsb.lol)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aircraft (
    icao_hex      TEXT PRIMARY KEY,
    registration  TEXT,
    type_code     TEXT,
    last_callsign TEXT,
    is_military   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aircraft_positions (
    id               BIGSERIAL PRIMARY KEY,
    icao_hex         TEXT         NOT NULL,
    observed_at      TIMESTAMPTZ  NOT NULL,
    lat              DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon              DOUBLE PRECISION NOT NULL CHECK (lon BETWEEN -180 AND 180),
    altitude_ft      INTEGER,
    on_ground        BOOLEAN      NOT NULL DEFAULT FALSE,
    ground_speed_kt  REAL,
    track_deg        REAL,
    vertical_rate_fpm INTEGER,
    squawk           TEXT,
    callsign         TEXT,
    ingested_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_aircraft_position UNIQUE (icao_hex, observed_at)
);

CREATE INDEX IF NOT EXISTS ix_aircraft_positions_time ON aircraft_positions (observed_at DESC);
CREATE INDEX IF NOT EXISTS ix_aircraft_positions_hex_time ON aircraft_positions (icao_hex, observed_at DESC);

-- Route per callsign from adsb.lol's routeset API. Cached and refreshed by the collector.
CREATE TABLE IF NOT EXISTS flight_routes (
    callsign        TEXT PRIMARY KEY,
    route_codes     TEXT,                 -- ICAO codes, e.g. ESSA-EFHK; NULL = unknown route
    origin_icao     TEXT,
    origin_iata     TEXT,
    origin_name     TEXT,
    origin_city     TEXT,
    origin_country  TEXT,
    dest_icao       TEXT,
    dest_iata       TEXT,
    dest_name       TEXT,
    dest_city       TEXT,
    dest_country    TEXT,
    plausible       BOOLEAN,              -- does the aircraft's position fit the route?
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Pipeline observability
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT        NOT NULL,          -- 'ais_locations' | 'ais_vessels' | 'adsb'
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    fetched       INTEGER,
    inserted      INTEGER,
    status        TEXT        NOT NULL CHECK (status IN ('running', 'ok', 'error')),
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS ix_ingestion_runs_source_time ON ingestion_runs (source, started_at DESC);
