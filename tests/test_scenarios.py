import time

import pytest

from nodera.demo import demo_library
from nodera.demo_core import DemoCore
from nodera.models import ClientError, Settings
from nodera.test_scenarios import TestScenarios


def test_default_failures_high_latency_overrides_and_live_profile_guard(tmp_path):
    model = TestScenarios(tmp_path)
    assert model.condition("demo-pro", "DE · Frankfurt", 0)["delay"] == 40
    assert model.condition("demo-pro", "NL · Amsterdam", 1)["delay"] == 0
    assert model.condition("demo-pro", "FI · Helsinki", 2)["delay"] == 1500
    model.node("demo-pro", "DE · Frankfurt", "slow", 3500, False)
    assert model.condition("demo-pro", "DE · Frankfurt", 0) == {
        "state": "slow",
        "delay": 3500,
        "udp": False,
    }
    assert model.condition("demo-standard", "DE · Frankfurt", 0)["delay"] == 40
    assert TestScenarios(tmp_path).condition("demo-pro", "DE · Frankfurt", 0)["delay"] == 3500
    with pytest.raises(ClientError):
        model.preset("live-profile", "outage")


def test_automatic_timeline_and_unstable_channel(tmp_path):
    now = [0]
    model = TestScenarios(tmp_path, clock=lambda: now[0])
    model.automatic("demo-pro", True, 5)
    assert model.tick() == []
    for second, preset in ((5, "primary-down"), (10, "slow"), (15, "outage"), (20, "recovered")):
        now[0] = second
        assert model.tick() == ["demo-pro"]
        assert model.settings("demo-pro")["preset"] == preset
    model.automatic("demo-pro", False, 5)
    model.node("demo-pro", "DE · Frankfurt", "unstable", 60)
    model.tick()
    assert model.condition("demo-pro", "DE · Frankfurt", 0)["delay"] == 0
    now[0] = 25
    assert model.tick() == ["demo-pro"]
    assert model.condition("demo-pro", "DE · Frankfurt", 0)["delay"] == 60
    model.reset("demo-pro")
    assert model.settings("demo-pro") == model.defaults()


def test_three_failures_freeze_route_failover_and_recovery(tmp_path):
    model = TestScenarios(tmp_path)
    p = demo_library().active
    core = DemoCore(tmp_path / "runtime", model)
    core.start(p, Settings())
    assert core.groups()[0]["current"] == "DE · Frankfurt"
    for stamp in range(3):
        core.update_route_health(
            {
                name: {"value": core.delay(name), "checked_at": time.time() + stamp}
                for name in core.node_names
            }
        )
        assert ("NL · Amsterdam" in core.frozen_routes) == (stamp == 2)
    model.preset(p.id, "primary-down")
    assert core.groups()[0]["current"] != "DE · Frankfurt"
    model.preset(p.id, "outage")
    with pytest.raises(ClientError):
        core.check_connectivity()
    assert not core.connection_snapshot()
    model.preset(p.id, "recovered")
    core.update_route_health(
        {
            name: {"value": core.delay(name), "checked_at": time.time() + 10}
            for name in core.node_names
        }
    )
    assert not core.frozen_routes
    assert core.check_connectivity()
    assert len(core.connection_snapshot()) == 2


@pytest.mark.parametrize("state", ["expired", "exhausted"])
def test_subscription_limit_events(tmp_path, state):
    model = TestScenarios(tmp_path)
    p = demo_library().active
    core = DemoCore(tmp_path / "runtime", model)
    core.start(p, Settings())
    model.subscription(p.id, state)
    with pytest.raises(ClientError):
        core.check_connectivity()
    assert not core.connection_snapshot()
    model.subscription(p.id, "normal")
    assert core.check_connectivity()
    model.subscription(p.id, "unavailable")
    assert core.check_connectivity()
    with pytest.raises(ClientError):
        model.check_subscription(p.id, updating=True)
