import pytest

from nodera.models import Profile
from nodera.network_quality import current_route, network_quality


def quality(value=80, *, state="ok", connected=True, age=0, results=(), spare=0):
    return network_quality(
        connected,
        state,
        "chosen",
        {
            ("p", "chosen"): {"value": value, "checked_at": 100 - age},
            ("p", "spare"): {"value": spare, "checked_at": 100},
        },
        "p",
        results,
        100,
    )


@pytest.mark.parametrize(
    "value,expected", [(80, "ok"), (499, "ok"), (500, "slow"), (1500, "slow"), (0, "error")]
)
def test_only_current_route_affects_network_quality(value, expected):
    assert quality(value)[0] == expected


def test_recovered_connection_remembers_recent_outage_then_clears_warning():
    assert quality(results=[(50, False), (99, True)])[0] == "unstable"
    assert quality(results=[(39, False), (99, True)])[0] == "ok"
    assert quality(1500, age=46)[0] == "unverified"
    assert quality(80, state="error")[0] == "ok"
    assert quality(1500, connected=False)[0] == "off"


def test_only_selected_route_matters_even_when_other_active_route_fails():
    state, _ = network_quality(
        True,
        "ok",
        "a",
        {("p", "a"): {"value": 70, "checked_at": 100}, ("p", "b"): {"value": 0, "checked_at": 100}},
        "p",
        [],
        100,
    )
    assert state == "ok"


def test_current_route_uses_final_rule_and_nested_choice_instead_of_other_groups():
    profile = Profile("test", "", {"rules": ["MATCH,default"]}, "")
    groups = [
        {"name": "other", "current": "broken", "active": ["broken"]},
        {"name": "default", "current": "nested", "active": ["good", "old"]},
        {"name": "nested", "current": "good"},
    ]
    assert current_route(profile, groups) == "good"
    profile.forced_server = "forced"
    assert current_route(profile, groups) == "forced"
    profile.forced_server = ""
    profile.config = {"mode": "global"}
    assert (
        current_route(profile, [*groups, {"name": "GLOBAL", "current": "global-node"}])
        == "global-node"
    )
    profile.config = {"mode": "direct"}
    assert current_route(profile, groups) == "DIRECT"


def test_unresolved_or_cyclic_route_does_not_claim_to_work():
    profile = Profile("test", "", {}, "")
    assert current_route(profile, []) is None
    assert (
        current_route(profile, [{"name": "a", "current": "b"}, {"name": "b", "current": "a"}])
        is None
    )
    assert network_quality(True, "ok", None, {}, profile.id, [], 100)[0] == "unverified"


@pytest.mark.parametrize("bypass", ["DIRECT", "REJECT", "REJECT-DROP"])
def test_bypass_final_rule_does_not_replace_selected_server_on_dashboard(bypass):
    profile = Profile("production-shaped", "", {"rules": [f"MATCH,{bypass}"]}, "")
    groups = [{"name": "main", "current": "selected-server", "active": ["selected-server"]}]
    assert current_route(profile, groups) == "selected-server"
    profile.config["mode"] = "direct"
    assert current_route(profile, groups) == "DIRECT"
