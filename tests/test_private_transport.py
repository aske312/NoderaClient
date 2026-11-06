import copy

import httpx

from nodera.controller import Controller
from nodera.core import runtime_config
from nodera.models import Settings, visible_proxies


def configuration():
    helper = "__312_transport_" + "a" * 32
    return {
        "proxies": [
            {
                "name": "VPN",
                "type": "trojan",
                "server": "8.8.8.8",
                "port": 443,
                "password": "inner-key",
                "dialer-proxy": helper,
            },
            {
                "name": helper,
                "type": "hysteria2",
                "server": "8.8.4.4",
                "port": 20000,
                "password": "outer-key",
                "fingerprint": "a" * 64,
                "x-nodera-route": {"role": "transport", "hidden": True},
            },
        ],
        "proxy-groups": [{"name": "Selection", "type": "select", "proxies": ["VPN"]}],
        "rules": ["MATCH,Selection"],
    }


def test_transport_remains_in_runtime_with_pin_and_fixed_dialer_but_not_probe_inventory():
    original = configuration()
    before = copy.deepcopy(original)
    config = runtime_config(original, Settings(tun=False), 12345, "local-controller-secret")
    assert original == before
    assert [node["name"] for node in visible_proxies(original)] == ["VPN"]
    assert [node["name"] for node in visible_proxies(config)] == ["VPN"]
    assert config["proxies"][0]["dialer-proxy"] == config["proxies"][1]["name"]
    assert config["proxies"][1]["fingerprint"] == "a" * 64
    assert "skip-cert-verify" not in config["proxies"][1]
    assert "x-nodera-route" not in config["proxies"][1]
    assert config["proxy-groups"][0]["proxies"] == ["VPN"]


def test_engine_global_group_cannot_make_transport_selectable():
    nodes = configuration()["proxies"]
    names = [node["name"] for node in nodes]
    response = {
        "proxies": {
            "GLOBAL": {"type": "Selector", "now": "VPN", "all": names},
            **{node["name"]: {"type": node["type"]} for node in nodes},
        }
    }
    with Controller(
        12345, "secret", httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as api:
        groups = api.groups(["GLOBAL"], ["VPN"])
    assert [member["name"] for member in groups[0]["members"]] == ["VPN"]
