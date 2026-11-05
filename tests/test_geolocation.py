import time

import httpx
import pytest

from nodera.geolocation import GeoClient, endpoint_rows, verified_locations
from nodera.models import ClientError, Profile
from nodera.routes import route_for_node


def response(ip, country="DE"):
    return {
        "success": True,
        "ip": ip,
        "country_code": country,
        "latitude": 50.1,
        "longitude": 8.7,
        "city": "Frankfurt",
        "connection": {"asn": 123, "org": "Test"},
    }


def test_resolves_both_families_and_binds_relay_and_origin(tmp_path):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=response(request.url.path[1:]))

    client = GeoClient(tmp_path, httpx.MockTransport(handle))
    profile = Profile("P", "", {"proxies": [{"name": "Node", "server": "relay.example"}]}, "")
    profile.route_overrides["Node"] = [
        {"role": "relay", "country": "RU", "host": "relay.example"},
        {"role": "server", "country": "US", "host": "origin.example"},
    ]
    dns_calls = []

    def dns(host, kind):
        dns_calls.append((host, kind))
        return {
            "Answer": [
                {
                    "type": 1 if kind == "A" else 28,
                    "data": "8.8.8.8" if kind == "A" else "2606:4700:4700::1111",
                }
            ]
        }

    result = client.check_node(profile, "Node", dns=dns)
    assert len(result["rows"]) == 4
    assert dns_calls == [
        (host, kind) for host in ("relay.example", "origin.example") for kind in ("A", "AAAA")
    ]
    assert len(calls) == 2  # shared IPs use the cache, not duplicate external lookups
    assert [(row["role"], row["step"]) for row in endpoint_rows(profile, "Node")] == [
        ("relay", 0),
        ("server", 1),
    ]
    routes = [{"node": "Node", "hops": route_for_node(profile, "Node"), "order_known": True}]
    locations = verified_locations(routes, {"Node": result})
    assert [item["code"] for item in locations] == ["DE", "DE"]
    assert all(item["source"] == "geoip" for item in locations)
    assert [item["city"] for item in locations] == ["Frankfurt", "Frankfurt"]


def test_private_addresses_and_local_names_never_leave_machine(tmp_path):
    def unexpected(_):
        pytest.fail("Private address sent to external service")

    client = GeoClient(tmp_path, httpx.MockTransport(unexpected))
    assert client.lookup("192.168.1.1")["country"] == ""
    assert client.lookup("::1")["country"] == ""
    assert client.resolve("[::1]") == ["::1"]
    for host in ("router", "router.local", "Router.LOCAL.", "https://secret.example/path"):
        with pytest.raises(ClientError):
            client.resolve(host)


def test_cache_persists_but_rate_limit_and_mismatched_ip_are_not_cached(tmp_path):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(200, json=response("8.8.8.8"))
        return httpx.Response(429)

    transport = httpx.MockTransport(handle)
    client = GeoClient(tmp_path, transport)
    client.lookup("8.8.8.8")
    restored = GeoClient(tmp_path, transport)
    assert restored.lookup("8.8.8.8")["cached"]
    with pytest.raises(ClientError):
        restored.lookup("1.1.1.1")
    with pytest.raises(ClientError):
        restored.lookup("9.9.9.9")
    assert len(calls) == 2
    assert restored.lookup("8.8.8.8")["cached"]
    other = GeoClient(
        tmp_path / "other",
        httpx.MockTransport(lambda _: httpx.Response(200, json=response("8.8.8.8"))),
    )
    with pytest.raises(ClientError):
        other.lookup("1.1.1.1")
    assert not other.cache


def test_hidden_origin_does_not_inherit_country_from_relay(tmp_path):
    profile = Profile(
        "P", "", {"proxies": [{"name": "Node · Relay: Germany", "server": "8.8.8.8"}]}, ""
    )
    name = profile.config["proxies"][0]["name"]
    endpoints = endpoint_rows(profile, name)
    assert len(endpoints) == 1 and endpoints[0]["role"] == "relay"
    routes = [{"node": name, "hops": route_for_node(profile, name), "order_known": True}]
    row = dict(
        endpoints[0], **response("8.8.8.8"), country="DE", lat=50, lon=8, timestamp=time.time()
    )
    assert all(
        item["role"] != "server" for item in verified_locations(routes, {name: {"rows": [row]}})
    )


@pytest.mark.parametrize("failure", ["error", "empty", "fake"])
def test_cdn_resolution_falls_back_from_core_and_follows_cname(tmp_path, failure):
    calls = []

    def core_dns(host, kind):
        if failure == "error":
            raise ClientError("Core DNS unavailable")
        return {"Answer": [{"type": 1, "data": "198.18.0.1"}]} if failure == "fake" else {}

    def handle(request):
        name, kind = request.url.params["name"], request.url.params["type"]
        calls.append((name, kind))
        if name == "cdn.example":
            return httpx.Response(200, json={"Answer": [{"type": 5, "data": "edge.example."}]})
        return httpx.Response(
            200,
            json={
                "Answer": [
                    {
                        "type": 1 if kind == "A" else 28,
                        "data": "1.1.1.1" if kind == "A" else "2606:4700:4700::1111",
                    }
                ]
            },
        )

    client = GeoClient(tmp_path, httpx.MockTransport(handle))
    assert client.resolve("cdn.example", core_dns) == ["1.1.1.1", "2606:4700:4700::1111"]
    assert ("edge.example", "A") in calls


def test_cname_loop_is_bounded(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"Answer": [{"type": 5, "data": "loop.example"}]})

    with pytest.raises(ClientError):
        GeoClient(tmp_path, httpx.MockTransport(handle)).resolve("loop.example")
    assert len(calls) <= 8
