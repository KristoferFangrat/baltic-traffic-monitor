-- Baltic Traffic Monitor – analytical views used by the dashboard.
SET search_path TO traffic, public;

-- Views are dropped and recreated so their columns can change between versions
DROP VIEW IF EXISTS v_vessel_destinations, v_flight_destinations, v_latest_vessels, v_latest_aircraft,
    v_ais_gaps, v_loitering, v_emergency_squawks, v_hourly_traffic, v_pipeline_health CASCADE;

-- AIS ship type code -> readable category (ITU-R M.1371)
CREATE OR REPLACE FUNCTION ship_category(code INTEGER)
RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN code IS NULL OR code = 0     THEN 'Okänd'
        WHEN code = 30                    THEN 'Fiske'
        WHEN code IN (31, 32, 52)         THEN 'Bogsering'
        WHEN code = 35                    THEN 'Militär'
        WHEN code = 36                    THEN 'Segelbåt'
        WHEN code = 37                    THEN 'Fritidsbåt'
        WHEN code BETWEEN 40 AND 49       THEN 'Höghastighetsfartyg'
        WHEN code = 50                    THEN 'Lotsbåt'
        WHEN code = 51                    THEN 'Sjöräddning'
        WHEN code = 55                    THEN 'Myndighet'
        WHEN code BETWEEN 60 AND 69       THEN 'Passagerare'
        WHEN code BETWEEN 70 AND 79       THEN 'Last'
        WHEN code BETWEEN 80 AND 89       THEN 'Tanker'
        ELSE 'Övrigt'
    END
$$;

-- Latest known position per vessel within the last hour
CREATE VIEW v_latest_vessels AS
SELECT DISTINCT ON (p.mmsi)
    p.mmsi,
    v.name,
    ship_category(v.ship_type) AS category,
    v.destination,
    v.dest_locode,
    v.dest_name,
    v.dest_country,
    p.observed_at,
    p.lat,
    p.lon,
    p.sog_knots,
    p.cog_deg,
    p.nav_status
FROM vessel_positions p
LEFT JOIN vessels v USING (mmsi)
WHERE p.observed_at > now() - INTERVAL '1 hour'
ORDER BY p.mmsi, p.observed_at DESC;

-- Latest known position per aircraft within the last 10 minutes
CREATE VIEW v_latest_aircraft AS
SELECT DISTINCT ON (p.icao_hex)
    p.icao_hex,
    COALESCE(NULLIF(p.callsign, ''), a.last_callsign) AS callsign,
    a.registration,
    a.type_code,
    a.is_military,
    p.observed_at,
    p.lat,
    p.lon,
    p.altitude_ft,
    p.on_ground,
    p.ground_speed_kt,
    p.track_deg,
    p.squawk,
    r.origin_iata,
    r.origin_icao,
    r.origin_city,
    r.origin_country,
    r.dest_iata,
    r.dest_icao,
    r.dest_name,
    r.dest_city,
    r.dest_country,
    r.plausible AS route_plausible
FROM aircraft_positions p
LEFT JOIN aircraft a USING (icao_hex)
LEFT JOIN flight_routes r
       ON r.callsign = COALESCE(NULLIF(p.callsign, ''), a.last_callsign)
      AND r.route_codes IS NOT NULL
WHERE p.observed_at > now() - INTERVAL '10 minutes'
ORDER BY p.icao_hex, p.observed_at DESC;

-- Anomaly 1: AIS gaps – a moving vessel stops transmitting for > 30 min, then reappears.
CREATE VIEW v_ais_gaps AS
WITH ordered AS (
    SELECT
        mmsi,
        observed_at,
        lat,
        lon,
        sog_knots,
        LAG(observed_at) OVER w AS prev_observed_at,
        LAG(lat)         OVER w AS prev_lat,
        LAG(lon)         OVER w AS prev_lon,
        LAG(sog_knots)   OVER w AS prev_sog
    FROM vessel_positions
    WHERE observed_at > now() - INTERVAL '24 hours'
    WINDOW w AS (PARTITION BY mmsi ORDER BY observed_at)
)
SELECT
    o.mmsi,
    v.name,
    ship_category(v.ship_type) AS category,
    o.prev_observed_at AS went_dark_at,
    o.observed_at      AS reappeared_at,
    o.observed_at - o.prev_observed_at AS gap,
    o.prev_lat, o.prev_lon,
    o.lat, o.lon,
    -- great-circle distance (km) between last and first position around the gap
    ROUND((6371 * 2 * ASIN(SQRT(
        POWER(SIN(RADIANS(o.lat - o.prev_lat) / 2), 2) +
        COS(RADIANS(o.prev_lat)) * COS(RADIANS(o.lat)) *
        POWER(SIN(RADIANS(o.lon - o.prev_lon) / 2), 2)
    )))::NUMERIC, 1) AS distance_km
FROM ordered o
LEFT JOIN vessels v USING (mmsi)
WHERE o.prev_observed_at IS NOT NULL
  AND o.observed_at - o.prev_observed_at > INTERVAL '30 minutes'
  AND o.prev_sog > 2;

-- Anomaly 2: Loitering – vessel reports "under way" but stays inside a small box for 3+ hours.
CREATE VIEW v_loitering AS
SELECT
    p.mmsi,
    v.name,
    ship_category(v.ship_type) AS category,
    MIN(p.observed_at) AS first_seen,
    MAX(p.observed_at) AS last_seen,
    COUNT(*)           AS reports,
    ROUND(AVG(p.lat)::NUMERIC, 4) AS lat,
    ROUND(AVG(p.lon)::NUMERIC, 4) AS lon,
    ROUND(AVG(p.sog_knots)::NUMERIC, 1) AS avg_sog
FROM vessel_positions p
LEFT JOIN vessels v USING (mmsi)
WHERE p.observed_at > now() - INTERVAL '6 hours'
  AND p.nav_status = 0                       -- 0 = under way using engine
GROUP BY p.mmsi, v.name, v.ship_type
HAVING MAX(p.observed_at) - MIN(p.observed_at) >= INTERVAL '3 hours'
   AND MAX(p.lat) - MIN(p.lat) < 0.02         -- ~2 km
   AND MAX(p.lon) - MIN(p.lon) < 0.04
   AND AVG(p.sog_knots) < 3;

-- Anomaly 3: Emergency squawks (7500 hijack, 7600 radio failure, 7700 general emergency)
CREATE VIEW v_emergency_squawks AS
SELECT DISTINCT ON (p.icao_hex, p.squawk)
    p.icao_hex,
    p.callsign,
    a.registration,
    a.type_code,
    p.squawk,
    CASE p.squawk
        WHEN '7500' THEN 'Kapning'
        WHEN '7600' THEN 'Radiobortfall'
        WHEN '7700' THEN 'Nödläge'
    END AS meaning,
    p.observed_at,
    p.lat,
    p.lon,
    p.altitude_ft
FROM aircraft_positions p
LEFT JOIN aircraft a USING (icao_hex)
WHERE p.squawk IN ('7500', '7600', '7700')
  AND p.observed_at > now() - INTERVAL '24 hours'
ORDER BY p.icao_hex, p.squawk, p.observed_at DESC;

-- Traffic volume per hour and source (last 48 h)
CREATE VIEW v_hourly_traffic AS
SELECT date_trunc('hour', observed_at) AS hour, 'Fartyg' AS source, COUNT(DISTINCT mmsi) AS unique_objects
FROM vessel_positions
WHERE observed_at > now() - INTERVAL '48 hours'
GROUP BY 1
UNION ALL
SELECT date_trunc('hour', observed_at), 'Flyg', COUNT(DISTINCT icao_hex)
FROM aircraft_positions
WHERE observed_at > now() - INTERVAL '48 hours'
GROUP BY 1;

-- Pipeline health: last run per source
CREATE VIEW v_pipeline_health AS
SELECT DISTINCT ON (source)
    source,
    started_at,
    finished_at,
    status,
    fetched,
    inserted,
    error_message,
    now() - started_at AS age
FROM ingestion_runs
ORDER BY source, started_at DESC;

-- Where are the ships heading? One row per category and destination.
CREATE VIEW v_vessel_destinations AS
SELECT
    category,
    dest_locode,
    -- unresolved free text is grouped on what the crew typed
    COALESCE(dest_name, NULLIF(TRIM(destination), ''), 'Okänd') AS dest_name,
    dest_country,
    (dest_name IS NOT NULL) AS resolved,
    COUNT(*) AS vessels
FROM v_latest_vessels
GROUP BY 1, 2, 3, 4, 5;

-- Where are the aircraft heading? Civil/military split, plus the route.
CREATE VIEW v_flight_destinations AS
SELECT
    CASE WHEN is_military THEN 'Militärt' ELSE 'Civilt' END AS category,
    dest_iata,
    dest_icao,
    COALESCE(dest_city, dest_name, 'Okänd') AS dest_city,
    dest_name,
    dest_country,
    COUNT(*) AS aircraft,
    COUNT(*) FILTER (WHERE route_plausible IS FALSE) AS uncertain
FROM v_latest_aircraft
GROUP BY 1, 2, 3, 4, 5, 6;
