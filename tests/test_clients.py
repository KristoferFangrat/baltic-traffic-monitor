"""Client tests with a fake session – no network."""

from btm.sources.adsb import ROUTESET_URL, AdsbLolClient
from btm.sources.ais import BASE_URL, DigitrafficClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self.payload)

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return FakeResponse(self.payload)


def test_digitraffic_client_sends_identification_and_area(load_fixture):
    session = FakeSession(load_fixture("ais_locations.json"))
    client = DigitrafficClient(session, user_agent="test-agent")

    rows = client.fetch_locations(59.0, 19.5, 300)

    assert session.headers["Digitraffic-User"] == "test-agent"
    assert session.calls == [(f"{BASE_URL}/locations", {"latitude": 59.0, "longitude": 19.5, "radius": 300})]
    assert len(rows) == 2


def test_adsb_client_clamps_radius(load_fixture):
    session = FakeSession(load_fixture("adsb.json"))
    AdsbLolClient(session).fetch_area(59.33, 18.07, 999)

    url, _ = session.calls[0]
    assert url.endswith("/lat/59.33/lon/18.07/dist/250")


def test_route_lookup_is_batched_per_100_planes(load_fixture):
    session = FakeSession(load_fixture("routeset.json"))
    planes = [(f"SAS{i}", 59.0, 18.0) for i in range(250)]

    AdsbLolClient(session).fetch_routes(planes)

    assert [len(body["planes"]) for _, body in session.calls] == [100, 100, 50]
    url, body = session.calls[0]
    assert url == ROUTESET_URL
    assert body["planes"][0] == {"callsign": "SAS0", "lat": 59.0, "lng": 18.0}
