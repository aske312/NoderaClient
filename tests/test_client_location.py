import httpx
import pytest

from nodera.client_location import detect_client_location
from nodera.models import ClientError


def test_physical_lookup_preserves_tls_hostname_and_returns_approximate_point():
    def handle(request):
        if request.url.host == "1.1.1.1":
            assert request.url.params["name"] == "ipwho.is"
            return httpx.Response(200, json={"Answer": [{"type": 1, "data": "8.8.8.8"}]})
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "ipwho.is"
        assert request.extensions["sni_hostname"] == "ipwho.is"
        return httpx.Response(
            200,
            json={
                "success": True,
                "ip": "8.8.4.4",
                "country_code": "DE",
                "latitude": 50.1,
                "longitude": 8.6,
                "city": "Frankfurt",
            },
        )

    point = detect_client_location((12, "192.0.2.1"), httpx.MockTransport(handle))
    assert point["code"] == "DE" and point["source"] == "ip-physical"
    assert point["name"] == "Frankfurt" and point["lat"] == 50.1


def test_failed_direct_connection_does_not_fall_back_to_vpn_geolocation():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        raise httpx.ConnectError("unreachable")

    with pytest.raises(ClientError, match="VPN не подставлена"):
        detect_client_location((12, "192.0.2.1"), httpx.MockTransport(handle))
    assert len(calls) == 1
