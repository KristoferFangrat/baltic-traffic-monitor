import pytest

from btm.destinations import country_name, resolve_ais_destination
from btm.formatting import flight_destination, flight_route, vessel_destination
from btm.sources.adsb import parse_routes


# ---------------------------------------------------------------------------
# AIS destinations – what crews actually type
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "locode", "name"),
    [
        ("FIHEL", "FIHEL", "Helsingfors (Helsinki)"),  # plain UN/LOCODE
        ("SE STO", "SESTO", "Stockholm"),  # LOCODE with a space
        ("se-sto", "SESTO", "Stockholm"),  # lower case + dash
        ("HELSINKI", "FIHEL", "Helsingfors (Helsinki)"),  # port name
        ("Göteborg", "SEGOT", "Göteborg"),  # accents
        ("GOTHENBURG", "SEGOT", "Göteborg"),  # English alias
        ("ST.PETERSBURG", "RULED", "Saint Petersburg"),
        ("SENYN>FIMHQ", "FIMHQ", "Mariehamn (Maarianhamina)"),  # route: last leg wins
        ("SE NYN => FI MHQ", "FIMHQ", "Mariehamn (Maarianhamina)"),
        ("STOCKHOLM - HELSINKI", "FIHEL", "Helsingfors (Helsinki)"),
        ("TURKU", "FITKU", "Åbo (Turku)"),  # Swedish name shown first
        ("@@KIEL@@", "DEKEL", "Kiel"),  # AIS padding
    ],
)
def test_resolves_ports(raw, locode, name):
    d = resolve_ais_destination(raw)
    assert (d.locode, d.name) == (locode, name)


@pytest.mark.parametrize(
    ("raw", "name"),
    [("FOR ORDERS", "Inväntar order"), ("fishing", "Fiske"), ("AT SEA", "Till sjöss")],
)
def test_special_destinations(raw, name):
    d = resolve_ais_destination(raw)
    assert d.name == name and d.locode is None


@pytest.mark.parametrize("raw", [None, "", "   ", "@@@@@", "XX123", "UNKNOWN", "12345"])
def test_unresolvable_destinations(raw):
    assert not resolve_ais_destination(raw).resolved


def test_country_names_are_swedish():
    assert country_name("DE") == "Tyskland"
    assert country_name("GB") == "Storbritannien"  # override of the long official name
    assert country_name("fi") == "Finland"
    assert country_name("XX") == "XX"  # unknown code returned as-is
    assert country_name(None) is None


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def test_vessel_destination_label_keeps_the_code():
    row = {"dest_name": "Helsingfors (Helsinki)", "dest_country": "FI", "dest_locode": "FIHEL"}
    assert vessel_destination(row) == "Helsingfors (Helsinki), Finland (FIHEL)"


def test_vessel_destination_label_falls_back_to_raw_text():
    assert vessel_destination({"destination": "XX123"}) == "XX123 (okänd kod)"
    assert vessel_destination({"destination": None}) == "Okänd"
    assert vessel_destination({"dest_name": "Inväntar order"}) == "Inväntar order"


def test_flight_destination_label():
    row = {"dest_city": "Helsinki", "dest_country": "FI", "dest_iata": "HEL", "route_plausible": True}
    assert flight_destination(row) == "Helsinki, Finland (HEL)"
    assert flight_destination({**row, "route_plausible": False}).endswith("– osäker")
    assert flight_destination({**row, "dest_iata": None, "dest_icao": "EFHK"}) == "Helsinki, Finland (EFHK)"
    assert flight_destination({}) == "Okänd"


def test_flight_route_label():
    assert flight_route({"origin_iata": "ARN", "dest_iata": "HEL"}) == "ARN → HEL"
    assert flight_route({"origin_icao": "ESSA", "dest_iata": "HEL"}) == "ESSA → HEL"
    assert flight_route({"origin_iata": "ARN"}) == "–"


# ---------------------------------------------------------------------------
# adsb.lol routeset
# ---------------------------------------------------------------------------
def test_parse_routes(load_fixture):
    routes = {r.callsign: r for r in parse_routes(load_fixture("routeset.json"))}

    sas = routes["SAS1421"]
    assert sas.route_codes == "ESSA-EFHK"
    assert (sas.origin.iata, sas.destination.iata) == ("ARN", "HEL")
    assert sas.destination.city == "Helsinki" and sas.destination.country == "FI"
    assert sas.plausible is True

    multi_leg = routes["CKK216"]
    assert multi_leg.origin.iata == "PVG"
    assert multi_leg.destination.iata == "FRA"  # final stop
    assert multi_leg.plausible is False

    unknown = routes["DEMO01"]
    assert unknown.route_codes is None and unknown.destination is None
