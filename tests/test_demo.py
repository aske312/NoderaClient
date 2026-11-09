import copy
import os
import socket
import subprocess
import time

import httpx
import pytest

from nodera.channel_policy import ChannelPolicy
from nodera.core import Core
from nodera.demo import DemoWindow, demo_library, is_demo_profile, prepare_demo
from nodera.demo_channels import DemoChannelService
from nodera.demo_core import DemoCore
from nodera.models import ClientError, Profile, ProfileLibrary, Settings
from nodera.profile_view import is_pro_subscription
from nodera.storage import Store
from nodera.subscription import SubscriptionUnavailable
from nodera.test_scenarios import DEMO_IDS, DEMO_PROFILES


@pytest.fixture
def no_network(monkeypatch):
    monkeypatch.setattr("nodera.versioning.git_revision", lambda *args: "demo-test")

    def forbidden(*args, **kwargs):
        pytest.fail("Demo attempted a real network connection or core process")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def test_demo_fixtures_are_unsigned_and_persist_changes(tmp_path):
    store = Store(tmp_path)
    prepare_demo(store)
    library = store.load_library()
    assert len(library.profiles) == len(DEMO_IDS)
    assert not any(is_pro_subscription(p) for p in library.profiles)
    assert [p.id for p in library.profiles] == list(DEMO_IDS)
    library.profiles[1].name = "Renamed"
    store.save_library(library)
    prepare_demo(store)
    assert store.load_library().profiles[1].name == "Renamed"


def test_demo_routes_have_three_or_four_addressed_hops(no_network):
    from nodera.geolocation import endpoint_rows
    from nodera.routes import node_route

    lengths = set()
    for profile in demo_library().profiles:
        for node in profile.config["proxies"]:
            hops, explicit = node_route(profile, node["name"])
            assert explicit
            assert len(hops) in (3, 4)
            lengths.add(len(hops))
            assert all(item["host"] and item["country"] for item in hops)
            assert len({item["host"] for item in hops}) == len(hops)
            assert hops[-1]["role"] == "server"
            assert hops[-1]["country"] == node["name"][:2]
            assert {row["host"] for row in endpoint_rows(profile, node["name"])} >= {
                hop["host"] for hop in hops
            }
    assert lengths == {3, 4}


def test_demo_tier_badges_and_options_are_local_to_demo_window(tmp_path, no_network):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from nodera.profiles_ui import ProfilesMixin

    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    prepare_demo(store)
    library = store.load_library()
    pro, standard = library.profiles[:2]
    window = DemoWindow(store, store.load_settings(), pro, library)
    try:
        assert window.is_pro_profile(pro)
        assert not window.is_pro_profile(standard)
        assert not ProfilesMixin.is_pro_profile(window, pro)
        unsigned_live = Profile("PRO", "https://312node.net/s/unsigned", {}, "")
        assert not window.is_pro_profile(unsigned_live)
        for profile, expected in ((pro, True), (standard, False), (pro, True)):
            window.profile = profile
            window.library = window.library.upsert(profile)
            window.preview_profile_id = profile.id
            window.refresh_profile()
            for badge in (
                window.home_pro_badge,
                window.connections_pro_badge,
                window.profile_pro_badge,
            ):
                assert badge.isHidden() != expected
            assert window.dashboard_options_button.isHidden() != expected
            assert window.profile_options.isHidden() != expected
    finally:
        window.exit_app()
        app.processEvents()


def test_seeding_adds_demos_without_replacing_live_profile_or_settings(tmp_path):
    store = Store(tmp_path)
    real = Profile("Live", "https://subscription.example/s/test", {"proxies": []}, "", id="live")
    real.local_rules = ["DOMAIN,example.org,DIRECT"]
    store.save_library(ProfileLibrary([real], real.id))
    settings = Settings(tun=True, system_proxy=True, mixed_port=12345)
    store.save_settings(settings)
    prepare_demo(store)
    prepare_demo(store)
    library = store.load_library()
    assert len(library.profiles) == len(DEMO_IDS) + 1
    assert library.active == real
    assert store.load_settings() == settings
    assert sum(is_demo_profile(p) for p in library.profiles) == len(DEMO_IDS)


@pytest.mark.parametrize("identity", DEMO_IDS[2:])
def test_error_demo_profiles_are_offline_and_expose_named_failure(tmp_path, no_network, identity):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from nodera.profile_view import subscription_notice

    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    prepare_demo(store)
    library = store.load_library()
    profile = next(p for p in library.profiles if p.id == identity)
    window = DemoWindow(store, store.load_settings(), profile, library.upsert(profile))
    for timer in window.findChildren(QTimer):
        timer.stop()
    window.scenarios.clock = lambda: 0
    try:
        assert window.demo_mode and isinstance(window.core, DemoCore)
        assert window.test_profile.count() == len(DEMO_IDS)
        assert window.is_pro_profile(profile) == DEMO_PROFILES[identity]["pro"]
        state = window.scenarios.settings(identity)["subscription"]
        if state in {"expired", "exhausted", "removed"}:
            assert subscription_notice(profile)
            with pytest.raises(SubscriptionUnavailable):
                window.core.start(profile, window.settings)
            with pytest.raises(SubscriptionUnavailable):
                window.fetch_subscription(profile.url, profile.name)
            window.scenarios.subscription(identity, "normal")
            restored = window.fetch_subscription(profile.url, profile.name)
            assert not subscription_notice(restored)
        else:
            window.core.start(profile, window.settings)
            delays = [window.core.delay(name) for name in window.core.node_names]
            if identity == "demo-outage":
                assert not any(delays)
                with pytest.raises(ClientError):
                    window.core.check_connectivity()
            elif identity == "demo-slow":
                assert all(delay >= 1500 for delay in delays)
            elif identity == "demo-failover":
                assert delays[0] == 0 and any(delays[1:])
                assert window.core.groups()[0]["current"] != window.core.node_names[0]
            elif identity == "demo-unstable":
                assert delays[0] == 0
                window.scenarios.clock = lambda: 3
                assert window.core.delay(window.core.node_names[0]) > 0
            elif identity == "demo-update-error":
                assert window.core.check_connectivity()
                with pytest.raises(ClientError, match="обновления"):
                    window.fetch_subscription(profile.url, profile.name)
                assert not profile.access_state
    finally:
        window.exit_app()
        app.processEvents()


def test_filtered_demo_keeps_full_mihomo_catalog_during_migration(tmp_path):
    from dataclasses import replace

    from nodera.channel_selection import selected_config

    store = Store(tmp_path)
    p = demo_library().active
    filtered = selected_config(p.config, [p.config["proxies"][0]["name"]])
    filtered["proxies"][0].pop("network", None)
    p = replace(p, config=filtered, channel_capabilities={})
    store.save_library(ProfileLibrary([p], p.id))
    prepare_demo(store)
    updated = store.load_library().active
    assert len(updated.config["proxies"]) == 1
    assert updated.config["proxies"][0]["network"] == "xhttp"
    assert updated.channel_capabilities["protocols"] == ["vless", "hysteria2", "tuic", "wireguard"]


def test_live_profile_uses_real_core_service_and_subscription_fetch(
    tmp_path, no_network, monkeypatch
):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from nodera import channel_selection
    from nodera.profiles_ui import ProfilesMixin

    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    real = Profile("Live", "https://subscription.example/s/test", {"proxies": []}, "", id="live")
    store.save_library(ProfileLibrary([real], real.id))
    store.save_settings(Settings(geo_auto=False, update_hours=0, close_to_tray=False))
    prepare_demo(store)
    library = store.load_library()
    window = DemoWindow(store, store.load_settings(), library.active, library)
    try:
        assert type(window.core) is Core
        assert not window.demo_mode
        assert window.channel_service() is channel_selection
        assert window.test_page_index in window.navigation.pages
        assert window.navigation.buttons[window.test_page_index].isHidden()
        window.navigate(window.test_page_index)
        assert window.stack.currentIndex() == 0
        real_before = copy.deepcopy(real)
        window.scenarios.preset("demo-pro", "outage")
        window.task = object()
        window.apply_test_scenario("demo-pro")
        assert window.pending_test_scenarios == {"demo-pro"}
        assert store.load_library().active == real_before
        window.task = None
        window.apply_test_scenario("demo-pro")
        assert not window.pending_test_scenarios
        assert window.profile == real_before
        assert store.load_library().active == real_before
        assert type(window.core) is Core
        monkeypatch.setattr(ProfilesMixin, "fetch_subscription", lambda *args: real)
        assert window.fetch_subscription(real.url, real.name) is real
        window.profile = next(p for p in library.profiles if p.id == "demo-pro")
        window.refresh_profile()
        assert not window.navigation.buttons[window.test_page_index].isHidden()
        window.navigate(window.test_page_index)
        assert window.stack.currentIndex() == window.test_page_index
        window.select_core()
        assert isinstance(window.core, DemoCore)
        assert window.channel_service() is window.demo_channels
        window.profile = real
        window.refresh_profile()
        assert window.navigation.buttons[window.test_page_index].isHidden()
        assert window.stack.currentIndex() == 0
        window.select_core()
        assert type(window.core) is Core
        assert window.channel_service() is channel_selection
    finally:
        window.exit_app()
        app.processEvents()


def test_demo_core_models_both_sessions_without_network(tmp_path, no_network):
    store = Store(tmp_path)
    prepare_demo(store)
    core = DemoCore(tmp_path / "runtime")
    for p in demo_library().profiles:
        original = copy.deepcopy(p)
        assert core.start(p, store.load_settings()) == "Demo"
        assert core.running and core.check_connectivity()
        assert core.status()["connections"] == 2
        core.force_server(core.node_names[-1])
        assert core.groups()[0]["current"] == core.node_names[-1]
        core.close_connections(["demo-flow-0"])
        assert len(core.connection_snapshot()) == 1
        core.restore_policy()
        assert p == original
        core.stop()
        assert not core.running and not core.process and not core.mixed_port
        assert not (tmp_path / "runtime").exists()


def test_demo_pro_selection_apply_reject_and_durable_rollback(tmp_path, no_network):
    store = Store(tmp_path)
    prepare_demo(store)
    p = store.load_library().active
    service = DemoChannelService(tmp_path)
    ready = service.select_channels(
        p, "demo", tmp_path, policy=ChannelPolicy(2, ["vless", "trojan"])
    )
    assert len(ready.profile.config["proxies"]) == 2
    assert sum(row["bytes"] for row in ready.rows if row["state"] == "passed") == 0
    prepared = service.change_server_selection(ready, "demo")
    assert prepared.server_state == "pending"
    assert service.change_server_selection(prepared, "demo", "reject").server_state == "rejected"
    assert len(store.load_library().active.config["proxies"]) == 6
    accepted = service.change_server_selection(prepared, "demo", "accept")
    core = DemoCore(tmp_path / "runtime")
    core.start(p, store.load_settings())
    service.apply_selection(accepted, p, store.load_library(), store, core, True)
    assert len(store.load_library().active.config["proxies"]) == 2
    discovered = DemoChannelService(tmp_path).discover_server_selection(accepted.profile, "demo")
    assert discovered.local_applied
    restored = service.change_server_selection(discovered, "demo", "rollback")
    assert restored.restored_config == p.config
    with pytest.raises(ClientError):
        service.select_channels(p, "demo", tmp_path, policy=ChannelPolicy(protocols=["ssh"]))


def test_demo_window_connect_select_apply_and_rollback_offline(tmp_path, no_network):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    prepare_demo(store)
    library = store.load_library()
    window = DemoWindow(store, store.load_settings(), library.active, library)

    def wait():
        deadline = time.monotonic() + 5
        while (
            window.task or window.inspections or window.poll_task
        ) and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not window.task and not window.inspections

    try:
        window.toggle()
        wait()
        assert window.connected
        window.test_group()
        window.capture_flows()
        wait()
        assert len(window.flow_monitor.flows) == 2
        window.diagnose_network()
        window.start_channel_selection(policy=ChannelPolicy(max_channels=2))
        wait()
        assert window.pending_channel_result.server_state == "pending"
        window.apply_ready_channels()
        wait()
        assert len(window.profile.config["proxies"]) == 2
        window.rollback_ready_channels()
        wait()
        assert len(window.profile.config["proxies"]) == 6
        window.toggle()
        wait()
        assert not window.connected
        window.profile = window.library.profiles[1]
        window.library.active_id = window.profile.id
        window.refresh_profile()
        window.toggle()
        wait()
        assert window.connected and not is_pro_subscription(window.profile)
    finally:
        window.exit_app()
        wait()
        app.processEvents()


def test_pro_actions_on_preview_do_not_activate_subscription(tmp_path, no_network, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    prepare_demo(store)
    library = store.load_library()
    library.active_id = "demo-standard"
    store.save_library(library)
    window = DemoWindow(store, store.load_settings(), library.active, library)
    monkeypatch.setattr(window.channel_dialog, "exec", lambda: 0)

    def wait():
        until = time.monotonic() + 5
        while window.task or window.inspections:
            assert time.monotonic() < until
            QTest.qWait(10)
        app.processEvents()

    try:
        window.profile_list.setCurrentRow(0)
        window.profile_options.click()
        assert window.channel_profile_id == "demo-pro"
        assert window.profile.id == "demo-standard"
        window.start_channel_selection(policy=ChannelPolicy(max_channels=1))
        wait()
        assert window.pending_channel_result.profile.id == "demo-pro"
        window.apply_ready_channels()
        wait()
        assert window.profile.id == store.load_library().active_id == "demo-standard"
        assert (
            len(next(p for p in window.library.profiles if p.id == "demo-pro").config["proxies"])
            == 1
        )
        window.rollback_ready_channels()
        wait()
        assert window.profile.id == store.load_library().active_id == "demo-standard"
        assert (
            len(next(p for p in window.library.profiles if p.id == "demo-pro").config["proxies"])
            == 6
        )
    finally:
        window.exit_app()
        wait()


def test_scenario_screen_changes_health_and_is_absent_from_normal_app(tmp_path, no_network):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton

    from nodera.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    prepare_demo(store)
    library = store.load_library()
    normal = MainWindow(store, store.load_settings(), library.active, library)
    assert all(title != "Тестовый режим" for title, _ in normal.navigation.pages.values())
    normal.exit_app()
    app.processEvents()
    window = DemoWindow(store, store.load_settings(), library.active, library)

    def wait():
        until = time.monotonic() + 5
        while (window.task or window.inspections) and time.monotonic() < until:
            QTest.qWait(10)
        app.processEvents()

    def apply():
        next(
            b for b in window.findChildren(QPushButton) if b.text() == "Применить сценарий"
        ).click()
        wait()

    try:
        window.navigate(window.test_page_index)
        assert window.navigation.tools.text() == "Тестовый режим"
        window.toggle()
        wait()
        window.test_preset.setCurrentIndex(window.test_preset.findData("outage"))
        apply()
        assert window.network_state == "error"
        assert not window.core.connection_snapshot()
        assert window.scenarios.settings("demo-pro")["preset"] == "outage"
        window.test_preset.setCurrentIndex(window.test_preset.findData("recovered"))
        apply()
        assert window.network_state == "ok"
        assert window.test_events.toPlainText()
        window.test_subscription.setCurrentIndex(window.test_subscription.findData("exhausted"))
        apply()
        assert window.profile.usage["download"] == window.profile.usage["total"]
        assert window.network_state == "error"
    finally:
        window.exit_app()
        wait()
