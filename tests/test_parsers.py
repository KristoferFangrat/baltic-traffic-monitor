from datetime import UTC, datetime, timedelta

from btm.sources.adsb import parse_aircraft
from btm.sources.ais import parse_locations, parse_vessels


def test_parse_locations_converts_geojson(load_fixture):
    rows = parse_locations(load_fixture("ais_locations.json"))

    assert len(rows) == 2  # feature without coordinates is skipped
    first = rows[0]
    assert first.mmsi == 265547250
    assert (first.lat, first.lon) == (59.3721, 18.3412)  # GeoJSON lon/lat swapped correctly
    assert first.observed_at == datetime.fromtimestamp(1793448000, tz=UTC)
    assert first.sog_knots == 12.4
    assert first.heading_deg == 88


def test_parse_locations_maps_not_available_values_to_none(load_fixture):
    moored = parse_locations(load_fixture("ais_locations.json"))[1]

    assert moored.sog_knots is None  # 102.3 = not available
    assert moored.cog_deg is None  # 360 = not available
    assert moored.heading_deg is None  # 511 = not available
    assert moored.nav_status == 5


def test_parse_vessels_cleans_metadata(load_fixture):
    vessels = parse_vessels(load_fixture("ais_vessels.json"))

    assert len(vessels) == 2
    trader, nameless = vessels
    assert trader.name == "DEMO TRADER"  # AIS '@' padding removed
    assert trader.call_sign == "SDEM"
    assert trader.draught_m == 7.2  # decimetres -> metres
    assert nameless.name is None
    assert nameless.imo is None
    assert nameless.draught_m is None


def test_parse_aircraft(load_fixture):
    rows = parse_aircraft(load_fixture("adsb.json"))
    by_hex = {r.icao_hex: r for r in rows}

    assert set(by_hex) == {"4ac9e1", "4ab123", "4ae000"}  # no-position aircraft skipped, hex lowercased

    sas = by_hex["4ac9e1"]
    assert sas.callsign == "SAS1421"
    assert sas.altitude_ft == 36000
    assert sas.on_ground is False
    assert sas.vertical_rate_fpm == -64
    now = datetime.fromtimestamp(1793448000, tz=UTC)
    assert sas.observed_at == (now - timedelta(seconds=1.4)).replace(microsecond=0)

    mil = by_hex["4ab123"]
    assert mil.on_ground is True and mil.altitude_ft == 0
    assert mil.is_military is True
    assert mil.squawk == "7700"

    bare = by_hex["4ae000"]
    assert bare.altitude_ft is None and bare.callsign is None


def test_parse_aircraft_handles_empty_payload():
    assert parse_aircraft({"ac": None, "now": 1793448000000}) == []
