import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.models import ClientError, Profile, Settings
from nodera.storage import Store
from nodera.ui import MainWindow


def make_window(tmp_path, profile=None, **settings):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        Store(tmp_path),
        Settings(update_hours=0, geo_auto=False, close_to_tray=False, **settings),
        profile,
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    window.show()
    app.processEvents()
    return app, window


def wait(app, window):
    deadline = time.monotonic() + 5
    while window.inspections and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    app.processEvents()
    assert not window.inspections


@pytest.mark.parametrize(
    "missing", [None, "profile", "total", "expire", "strategy", "unlimited", "permanent", "unknown"]
)
def test_subscription_strip_requires_all_three_finite_server_values(tmp_path, missing):
    profile = Profile(
        "Personal",
        "",
        {"proxies": [], "proxy-groups": [{"name": "Auto", "type": "fallback"}]},
        "",
        usage={"total": 1024, "expire": int(time.time()) + 86400},
    )
    if missing in {"total", "expire"}:
        profile.usage.pop(missing)
    elif missing == "strategy":
        profile.config.pop("proxy-groups")
    elif missing == "unknown":
        profile.config["proxy-groups"][0]["type"] = "unknown"
    elif missing in {"unlimited", "permanent"}:
        profile.usage["total" if missing == "unlimited" else "expire"] = 0
    app, window = make_window(tmp_path, None if missing == "profile" else profile)
    try:
        assert window.quota_panel.isVisible() == (missing is None)
        if missing is None:
            # Exhausted/expired finite plans remain meaningful and visible.
            profile.usage = {"total": 10, "download": 10, "expire": 1}
            window.refresh_dashboard()
            assert window.quota_panel.isVisible()
            assert window.quota_labels[0][0].text() == "0 Б"
            assert window.quota_labels[1][0].text() == "Истёк"
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("tun", [False, True])
def test_session_and_traffic_visibility_follow_connection_lifecycle(tmp_path, tun):
    profile = Profile("Personal", "", {"proxies": []}, "")
    app, window = make_window(tmp_path, profile, tun=tun)
    try:
        assert not window.traffic_panel.isVisible()
        assert not window.session_panel.isVisible()
        assert not window.route_panel.isVisible()
        status_x = window.network_tag.mapTo(window, QPoint(window.network_tag.width() // 2, 0)).x()
        power_x = window.power.mapTo(window, QPoint(window.power.width() // 2, 0)).x()
        assert abs(status_x - power_x) <= 2
        window.set_connected("test")
        app.processEvents()
        assert window.traffic_panel.isVisible()
        assert window.session_panel.isVisible() == tun
        assert window.route_panel.isVisible()
        window.set_disconnected()
        app.processEvents()
        assert not window.traffic_panel.isVisible()
        assert not window.session_panel.isVisible()
        assert not window.route_panel.isVisible()
    finally:
        window.connected = False
        window.close()
        app.processEvents()


def test_first_location_retries_failure_and_marks_map_without_connection(tmp_path, monkeypatch):
    # An old failed attempt flag must not prevent a new lookup.
    app, window = make_window(tmp_path, client_location_attempted=True)
    point = dict(code="DE", lat=50.1, lon=8.6, name="Frankfurt", source="ip-physical")
    calls = []

    def detect():
        calls.append(True)
        if len(calls) == 1:
            raise ClientError("Offline")
        return point

    monkeypatch.setattr("nodera.settings_ui.detect_client_location", detect)
    try:
        window.auto_client_location()
        wait(app, window)
        assert window.client_location_timer.isActive()
        assert window.client_location_timer.interval() == 60000
        assert not window.world_map.client_point and not window.connected
        window.client_location_timer.stop()
        window.auto_client_location()
        wait(app, window)
        assert window.world_map.client_point["code"] == "DE"
        assert not window.world_map.connected and not window.connected
        assert Store(tmp_path).load_settings().client_point["code"] == "DE"
        window.auto_client_location()
        assert len(calls) == 2
    finally:
        window.close()
        app.processEvents()


def test_successful_location_remains_visible_if_settings_cannot_be_saved(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    point = dict(code="DE", lat=50.1, lon=8.6, name="Frankfurt", source="ip-physical")
    monkeypatch.setattr("nodera.settings_ui.detect_client_location", lambda: point)

    def unavailable(_):
        raise OSError("Read-only directory")

    monkeypatch.setattr(window.store, "save_settings", unavailable)
    try:
        window.auto_client_location()
        wait(app, window)
        window.refresh_dashboard()
        assert window.world_map.client_point["code"] == "DE"
        assert not window.connected
        assert "Не удалось сохранить" in window.client_point_result.text()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("has_profile", [False, True])
def test_home_header_keeps_subscription_left_and_action_buttons_together_on_right(
    tmp_path, has_profile
):
    profile = (
        Profile("Personal subscription " * 8, "", {"proxies": []}, "") if has_profile else None
    )
    app, window = make_window(tmp_path, profile)
    try:
        for width, height in ((1040, 720), (840, 620)):
            window.resize(width, height)
            app.processEvents()
            header = window.dashboard_header
            picker = window.profile_selector if has_profile else window.profile_add
            assert picker.mapTo(header, QPoint(0, 0)).x() == 8
            refresh = window.dashboard_update_button.mapTo(header, QPoint(0, 0))
            subscriptions = window.open_profile.mapTo(header, QPoint(0, 0))
            assert subscriptions.x() - refresh.x() - window.dashboard_update_button.width() == 8
            assert header.width() - subscriptions.x() - window.open_profile.width() == 8
            assert subscriptions.y() == refresh.y() == picker.mapTo(header, QPoint(0, 0)).y()
    finally:
        window.close()
        app.processEvents()
