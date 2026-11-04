from nodera.models import Profile
from nodera.route_inventory import route_inventory


def test_inventory_spans_all_nodes_and_merges_same_address():
    profile = Profile(
        "Test",
        "",
        {
            "proxies": [
                {"name": "A", "server": "8.8.8.8"},
                {"name": "B", "server": "8.8.8.8"},
                {"name": "C", "server": "1.1.1.1"},
            ]
        },
        "",
    )
    rows = route_inventory(profile, {}, cache={"8.8.8.8": {"country": "DE", "timestamp": 1}})
    assert len(rows) == 2
    first = next(row for row in rows if row["ip"] == "8.8.8.8")
    assert first["nodes"] == {"A", "B"}
    assert first["countries"] == {"DE"}


def test_shared_dns_host_reuses_known_addresses_without_duplicate_unresolved_row():
    profile = Profile(
        "Test",
        "",
        {
            "proxies": [
                {"name": "A", "server": "node.example"},
                {"name": "B", "server": "node.example"},
            ]
        },
        "",
    )
    observations = {
        "A": {"rows": [{"host": "node.example", "ip": "8.8.8.8", "country": "FI", "timestamp": 1}]}
    }
    rows = route_inventory(profile, observations)
    assert len(rows) == 1
    assert rows[0]["nodes"] == {"A", "B"}
    assert rows[0]["countries"] == {"FI"}


def test_hidden_origins_are_explicit_unknown_groups_not_invented_ips():
    name = "VLESS CDN · Relay: Gremany"
    profile = Profile("Test", "", {"proxies": [{"name": name, "server": "8.8.8.8"}]}, "")
    rows = route_inventory(profile, {})
    hidden = [row for row in rows if row["host"] == "Адрес не передан"]
    assert {next(iter(row["roles"])) for row in hidden} == {"cdn", "server"}
    assert all(not row.get("ip") for row in hidden)
