from nodera.geolocation import endpoint_rows
from nodera.locations import active_locations, infer_country
from nodera.models import Profile
from nodera.routes import active_route_views, node_route, route_for_node, route_text


def test_cdn_host_is_separate_from_relay_and_hidden_origin():
    name = "VLESS CDN · Relay: Gremany"
    profile = Profile(
        "P",
        "",
        {
            "proxies": [
                {
                    "name": name,
                    "type": "vless",
                    "server": "8.8.8.8",
                    "servername": "tls.example.org",
                    "ws-opts": {"headers": {"Host": "edge.example.org"}},
                }
            ]
        },
        "",
    )
    hops = route_for_node(profile, name)
    assert next(hop for hop in hops if hop["role"] == "cdn")["host"] == "edge.example.org"
    assert next(hop for hop in hops if hop["role"] == "relay")["host"] == "8.8.8.8"
    assert not next(hop for hop in hops if hop["role"] == "server").get("host")
    assert {row["role"] for row in endpoint_rows(profile, name)} == {"cdn", "relay"}


def test_reality_sni_is_not_mistaken_for_a_cdn_or_origin():
    profile = Profile(
        "P",
        "",
        {
            "proxies": [
                {
                    "name": "Reality",
                    "type": "vless",
                    "server": "8.8.8.8",
                    "servername": "decoy.example.org",
                }
            ]
        },
        "",
    )
    assert [row["host"] for row in endpoint_rows(profile, "Reality")] == ["8.8.8.8"]


def test_relay_label_never_becomes_the_exit_country():
    name = "1 · VLESS · XHTTP · REALITY · Relay: Москва"
    profile = Profile(
        "P", "", {"proxies": [{"name": name, "type": "vless"}]}, "", locations={name: "RU"}
    )
    hops, ordered = node_route(profile, name)
    assert ordered
    assert [(item["role"], item["country"]) for item in hops] == [("relay", "RU"), ("server", "")]


def test_subscription_relay_precedes_cdn_without_guessing_its_country():
    name = "1 · VLESS · WebSocket · TLS · CDN · Relay: Москва"
    profile = Profile("P", "", {"proxies": [{"name": name, "type": "vless"}]}, "")
    groups = [{"name": "PRO", "active": [name]}]
    route = active_route_views(profile, groups, True)[0]
    assert route["order_known"]
    assert [(hop["role"], hop["country"]) for hop in route["hops"]] == [
        ("relay", "RU"),
        ("cdn", ""),
        ("server", ""),
    ]
    assert "→" in route_text(route)
    assert active_locations(profile, groups, True)[0]["role"] == "relay"


def test_multiple_country_hints_do_not_pick_the_first_country():
    assert infer_country({"name": "🇷🇺 → 🇩🇪"}) is None
    profile = Profile("P", "", {"proxies": [{"name": "🇷🇺 → 🇩🇪", "type": "ss"}]}, "")
    hops, _ = node_route(profile, "🇷🇺 → 🇩🇪")
    assert [item["country"] for item in hops] == ["RU", "DE"]


def test_dialer_group_resolves_live_choice_in_entry_to_exit_order():
    profile = Profile(
        "P",
        "",
        {
            "proxies": [
                {"name": "Frankfurt", "type": "ss", "dialer-proxy": "Hidden"},
                {"name": "Helsinki", "type": "ss"},
                {"name": "Tokyo", "type": "ss"},
            ],
            "proxy-groups": [
                {"name": "Hidden", "type": "select", "proxies": ["Helsinki", "Tokyo"]}
            ],
        },
        "",
    )
    groups = [{"name": "PRO", "active": ["Frankfurt"], "choices": {"Hidden": "Tokyo"}}]
    locations = active_locations(profile, groups, True)
    assert [(item["role"], item["code"]) for item in locations] == [
        ("relay", "JP"),
        ("server", "DE"),
    ]
    groups[0]["choices"]["Hidden"] = "Helsinki"
    assert [item["code"] for item in active_locations(profile, groups, True)] == ["FI", "DE"]


def test_explicit_metadata_and_manual_override_preserve_duplicate_countries_and_unknown_hops():
    name = "N"
    declared = [
        {"role": "relay", "country": "RU"},
        {"role": "cdn", "country": ""},
        {"role": "server", "country": "RU"},
    ]
    profile = Profile(
        "P",
        "",
        {"proxies": [{"name": name, "type": "ss"}], "x-nodera": {"routes": {name: declared}}},
        "",
    )
    hops, _ = node_route(profile, name)
    assert [item["country"] for item in hops] == ["RU", "", "RU"]
    groups = [{"name": "PRO", "active": [name]}]
    assert [item["step"] for item in active_locations(profile, groups, True)] == [0, 2]
    profile.route_overrides[name] = [{"role": "server", "country": "FI"}]
    assert node_route(profile, name)[0][0]["country"] == "FI"


def test_dialer_cycle_is_bounded_and_does_not_invent_an_entry():
    profile = Profile(
        "P", "", {"proxies": [{"name": "Frankfurt", "type": "ss", "dialer-proxy": "Frankfurt"}]}, ""
    )
    result = active_route_views(profile, [{"name": "PRO", "active": ["Frankfurt"]}], True)
    assert not result[0]["order_known"]
    assert result[0]["hops"][0]["country"] == ""


def test_editor_shows_full_dialer_chain_and_manual_chain_is_not_prepended_twice():
    profile = Profile(
        "P",
        "",
        {
            "proxies": [
                {"name": "Frankfurt", "type": "ss", "dialer-proxy": "Helsinki"},
                {"name": "Helsinki", "type": "ss"},
            ]
        },
        "",
    )
    route = route_for_node(profile, "Frankfurt")
    assert [item["country"] for item in route] == ["FI", "DE"]
    profile.route_overrides["Frankfurt"] = [
        {"role": item["role"], "country": item["country"]} for item in route
    ]
    assert [item["country"] for item in route_for_node(profile, "Frankfurt")] == ["FI", "DE"]
