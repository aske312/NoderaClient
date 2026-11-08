import os
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.controller import Controller
from nodera.models import ClientError, Profile, ProfileLibrary, Settings
from nodera.profile_view import initial_subscription_settings
from nodera.storage import Store
from nodera.ui import MainWindow


def pending_profile():
    return Profile(
        "New",
        "https://example.invalid/subscription",
        {
            "proxies": [{"name": "a", "type": "direct"}],
            "tun": {"enable": False, "stack": "system", "mtu": 1400},
            "ipv6": False,
            "mixed-port": 18080,
            "log-level": "warning",
        },
        "",
        settings_initialized=False,
    )


def wait(app, window):
    deadline = time.monotonic() + 4
    while window.task and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    app.processEvents()
    assert window.task is None


def test_first_activation_persists_mihomo_defaults_but_later_user_choices_win(tmp_path):
    store = Store(tmp_path)
    settings = Settings(tun=True, tun_stack="gvisor", ipv6_mode="on")
    profile = pending_profile()
    library, seeded = store.save_library_activation(ProfileLibrary([profile], profile.id), settings)
    assert not seeded.tun and seeded.tun_stack == "system" and seeded.tun_mtu == 1400
    assert seeded.ipv6_mode == "off" and seeded.mixed_port == 18080
    assert seeded.log_level == "warning" and seeded.device_id == settings.device_id
    assert seeded.system_proxy == settings.system_proxy
    assert Store(tmp_path).load_profile().settings_initialized
    assert Store(tmp_path).load_settings() == seeded
    manual = replace(seeded, tun=True, tun_mtu=1500)
    store.save_settings(manual)
    _, again = store.save_library_activation(library, manual)
    assert again == manual and Store(tmp_path).load_settings() == manual


@pytest.mark.parametrize("existing", [False, True])
def test_failed_import_rolls_back_settings_and_does_not_consume_first_activation(
    tmp_path, monkeypatch, existing
):
    store = Store(tmp_path)
    settings = Settings(tun=True)
    if existing:
        store.save_settings(settings)
    path = tmp_path / "settings.json"
    original = path.read_bytes() if existing else None
    profile = pending_profile()

    def failed(*args):
        raise OSError("Cannot write encrypted library")

    monkeypatch.setattr(store, "save_library", failed)
    with pytest.raises(OSError):
        store.save_library_activation(ProfileLibrary([profile], profile.id), settings)
    assert (path.read_bytes() if path.exists() else None) == original
    assert not profile.settings_initialized


def test_missing_or_unsupported_values_do_not_reset_settings_or_windows_preferences():
    settings = Settings(tun=False, mixed_port=18080, tun_stack="system", tun_mtu=1400)
    assert initial_subscription_settings({}, settings) == settings
    assert (
        initial_subscription_settings(
            {
                "tun": {"enable": "true", "stack": "unknown", "mtu": True},
                "mixed-port": -1,
                "ipv6": 1,
                "log-level": "unknown",
                "allow-lan": True,
                "external-controller": "0.0.0.0:9090",
            },
            settings,
        )
        == settings
    )


def test_import_updates_settings_widgets_once_and_refresh_preserves_manual_changes(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    window = MainWindow(store, Settings(tun=True, geo_auto=False, update_hours=0), None)
    fetched = pending_profile()
    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", lambda *a, **kw: fetched)
    try:
        window.load_subscription(fetched.url, fetched.name)
        wait(app, window)
        assert window.connection_mode.currentData() == "proxy"
        assert window.stack_setting.currentData() == "system"
        assert window.ipv6_setting.currentData() == "off"
        assert window.mtu_setting.value() == 1400 and window.port.value() == 18080
        window.connection_mode.setCurrentIndex(window.connection_mode.findData("tun"))
        window.save_settings()
        window.update_profile()
        wait(app, window)
        assert window.settings.tun and store.load_settings().tun
        assert window.profile.settings_initialized
    finally:
        window.quit_requested = True
        window.close()
        app.processEvents()


def test_new_subscription_waits_for_activation_when_another_session_is_connected(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path)
    old = Profile("Old", "", {"proxies": [{"name": "old", "type": "direct"}]}, "")
    settings = Settings(tun=True, geo_auto=False, update_hours=0)
    store.save_settings(settings)
    store.save_profile(old)
    window = MainWindow(store, settings, old)
    fetched = pending_profile()
    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", lambda *a, **kw: fetched)
    try:
        window.connected = True
        window.session_profile = old
        window.load_subscription(fetched.url, fetched.name)
        wait(app, window)
        assert window.profile.id == old.id and window.settings == settings
        added = next(p for p in store.load_library().profiles if p.id != old.id)
        assert not added.settings_initialized
        window.connected = False
        window.switch_profile(added.id)
        wait(app, window)
        assert window.profile.settings_initialized and not window.settings.tun
        assert not store.load_settings().tun
    finally:
        window.quit_requested = True
        window.close()
        app.processEvents()


def test_group_failure_does_not_stop_other_strategies_or_change_manual_selection():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/proxies":
            return httpx.Response(
                200,
                json={
                    "proxies": {
                        "manual": {"type": "Selector", "all": ["bad", "best", "balance"]},
                        "bad": {"type": "Fallback", "all": []},
                        "best": {
                            "type": "URLTest",
                            "all": [],
                            "testUrl": "https://example.invalid/ok",
                            "expectedStatus": "200",
                        },
                        "balance": {"type": "LoadBalance", "all": []},
                    }
                },
            )
        if request.url.path == "/group/bad/delay":
            return httpx.Response(504)
        return httpx.Response(200, json={})

    with Controller(1, "secret", httpx.MockTransport(handle)) as controller:
        assert controller.recheck_routes(["manual"]) == ["best", "balance"]
    assert all(request.method == "GET" for request in requests)
    assert requests[2].url.params["url"] == "https://example.invalid/ok"
    assert requests[2].url.params["expected"] == "200"


def test_failed_checks_are_reported_if_no_group_could_be_checked():
    def handle(request):
        if request.url.path == "/proxies":
            return httpx.Response(200, json={"proxies": {"auto": {"type": "Fallback", "all": []}}})
        return httpx.Response(504)

    with Controller(1, "secret", httpx.MockTransport(handle)) as controller:
        with pytest.raises(ClientError):
            controller.recheck_routes(["auto"])


def test_healthy_session_periodically_rechecks_automatic_groups_without_overlap(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    profile = pending_profile()
    profile.config["proxy-groups"] = [{"name": "auto", "type": "fallback", "proxies": ["a"]}]
    window = MainWindow(
        Store(tmp_path), Settings(tun=False, geo_auto=False, update_hours=0), profile
    )
    calls = []
    monkeypatch.setattr(type(window.core), "running", property(lambda _: True))
    monkeypatch.setattr(window.core, "recheck_routes", lambda: calls.append("recheck") or ["auto"])
    monkeypatch.setattr(window, "test_group", lambda: None)
    monkeypatch.setattr(window, "check_connection", lambda: calls.append("verify"))
    try:
        window.connected = True
        window.network_state = "ok"
        window.hint.setText("Current connection")
        window.periodic_delays()
        window.periodic_delays()
        assert window.hint.text() == "Current connection"
        deadline = time.monotonic() + 4
        while window.inspections and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        app.processEvents()
        assert calls == ["recheck", "verify"]
        window.periodic_delays()
        assert calls == ["recheck", "verify"]
        window.profile = replace(window.profile, forced_server="a")
        window.route_recovery_at = float("-inf")
        window.periodic_delays()
        assert calls == ["recheck", "verify"]
    finally:
        window.connected = False
        window.quit_requested = True
        window.close()
        app.processEvents()
