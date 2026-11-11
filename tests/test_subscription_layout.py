import threading
import time
from dataclasses import replace
from types import MethodType

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox

from nodera.geolocation import endpoint_rows
from nodera.models import Profile, ProfileLibrary, Settings
from nodera.profile_view import is_pro_subscription
from nodera.storage import Store
from nodera.ui import MainWindow
from nodera.workbench import WorkbenchMixin, breakable_text

schedule_locations = WorkbenchMixin.schedule_location_check


def make_window(tmp_path, count=2, name="Рабочая подписка с длинным названием"):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        name,
        "https://example.com/s/test",
        {
            "proxies": [
                {"name": f"route-{i}", "type": "ss", "server": "8.8.8.8"} for i in range(count)
            ]
        },
        "",
        usage={"total": 2048, "download": 1024, "expire": 2000000000},
    )
    window = MainWindow(
        Store(tmp_path), Settings(update_hours=0, geo_auto=False, close_to_tray=False), profile
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    return app, window


def wait(app, window):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if not window.task and not window.geo_task and not window.pending_geo_profiles:
            return
        QTest.qWait(5)
    raise AssertionError("Background checks did not finish")


def fake_location(client, profile, name, choices, dns, cancelled, force, peers):
    return {
        "timestamp": time.time(),
        "rows": [
            {
                **row,
                "country": "US",
                "ip": "8.8.8.8",
                "lat": 37,
                "lon": -122,
                "timestamp": time.time(),
                "status": "Проверено по IP",
            }
            for row in endpoint_rows(profile, name, choices)
        ],
    }


def test_subscription_selectors_fit_selected_text_and_keep_full_names(tmp_path):
    app, window = make_window(tmp_path)
    try:
        for selector in (window.profile_selector, window.connections_profile_selector):
            assert len(selector.currentText()) == 18
            assert selector.currentText().endswith("…")
            assert selector.toolTip() == window.profile.name
            option = QStyleOptionComboBox()
            selector.initStyleOption(option)
            text_rect = selector.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option,
                QStyle.SubControl.SC_ComboBoxEditField,
                selector,
            )
            assert text_rect.width() >= selector.fontMetrics().horizontalAdvance(
                selector.currentText()
            )
        wide = window.profile_selector.width()
        connections_wide = window.connections_profile_selector.width()
        window.profile.name = "Дом"
        window.refresh_profile()
        assert window.profile_selector.currentText() == "Дом"
        assert window.profile_selector.width() < wide
        # The compact map header and connections toolbar use different font sizes.
        assert window.connections_profile_selector.currentText() == "Дом"
        assert window.connections_profile_selector.width() < connections_wide
        assert window.nav[4].text() == "Подключения"
        assert not hasattr(window, "geo_button")
    finally:
        window.close()
        app.processEvents()


def test_subscription_detail_updates_and_search_does_not_change_active_profile(tmp_path):
    app, window = make_window(tmp_path)
    try:
        first = window.profile
        second = Profile("Другой профиль", "", {"proxies": []}, "")
        window.library = ProfileLibrary([first, second], first.id)
        window.refresh_profile()
        window.profile_search.setText("другой")
        assert window.profile_list.item(0).isHidden()
        assert not window.profile_list.item(1).isHidden()
        assert window.profile is first
        assert window.profile_summary_title.text() == first.name
        assert window.profile_summary_nodes.text() == "2"
        assert window.profile_summary_remaining.text() == "1.0 КБ"
        assert first.url not in window.subscription_info.text()
    finally:
        window.close()
        app.processEvents()


def test_real_connection_automatically_checks_all_routes_even_above_64(tmp_path, monkeypatch):
    app, window = make_window(tmp_path, count=70)
    window.settings = replace(window.settings, geo_auto=True)
    monkeypatch.setattr("nodera.geolocation.GeoClient.check_node", fake_location)
    window.schedule_location_check = MethodType(schedule_locations, window)
    monkeypatch.setattr(window, "check_connection", lambda: None)
    monkeypatch.setattr(window, "periodic_delays", lambda: None)
    try:
        window.set_connected("test", verify=True)
        wait(app, window)
        assert len(window.geo_for_profile()) == 70
        assert window.address_table.rowCount() == 1
    finally:
        window.connected = False
        window.close()
        app.processEvents()


def test_subscription_update_automatically_refreshes_ip_without_connection(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    window.settings = replace(window.settings, geo_auto=True)
    monkeypatch.setattr("nodera.geolocation.GeoClient.check_node", fake_location)
    window.schedule_location_check = MethodType(schedule_locations, window)
    fetched = Profile(
        "Updated",
        window.profile.url,
        {"proxies": [{"name": "new-route", "type": "ss", "server": "1.1.1.1"}]},
        "",
    )
    monkeypatch.setattr(window, "fetch_subscription", lambda *a: fetched)
    try:
        window.load_subscription(window.profile.url, window.profile.name)
        wait(app, window)
        assert not window.connected
        assert set(window.geo_for_profile()) == {"new-route"}
    finally:
        window.close()
        app.processEvents()


def test_updated_profile_replaces_an_inflight_ip_check(tmp_path, monkeypatch):
    app, window = make_window(tmp_path, count=1)
    window.settings = replace(window.settings, geo_auto=True)
    entered, release = threading.Event(), threading.Event()
    hosts = []

    def check(client, profile, name, *args):
        host = profile.config["proxies"][0]["server"]
        hosts.append(host)
        if len(hosts) == 1:
            entered.set()
            assert release.wait(3)
        return fake_location(client, profile, name, *args)

    monkeypatch.setattr("nodera.geolocation.GeoClient.check_node", check)
    window.schedule_location_check = MethodType(schedule_locations, window)
    try:
        window.schedule_location_check()
        deadline = time.monotonic() + 2
        while not entered.is_set() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        assert entered.is_set()
        old_task = window.geo_task
        window.profile = replace(
            window.profile,
            config={"proxies": [{"name": "route-0", "type": "ss", "server": "1.1.1.1"}]},
        )
        window.library = window.library.upsert(window.profile)
        window.schedule_location_check()
        assert old_task.cancelled.is_set()
        release.set()
        wait(app, window)
        assert hosts == ["8.8.8.8", "1.1.1.1"]
        assert window.geo_for_profile()["route-0"]["endpoints"][0]["host"] == "1.1.1.1"
    finally:
        release.set()
        wait(app, window)
        window.close()
        app.processEvents()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://312node.net/s/test", False),
        ("https://312NODE.NET:443/s/test", False),
        ("https://312node.net.example.com/s/test", False),
        ("https://example.com/312node.net", False),
        ("https://312node.net@example.com/s/test", False),
    ],
)
def test_domain_alone_never_confirms_pro_subscription(url, expected):
    assert is_pro_subscription(Profile("Name", url, {}, "")) is expected


def test_pro_tags_and_compact_subscription_card(tmp_path):
    from nodera.pro_provenance import TRUSTED_PRO_KEYS, digest, verification_record

    app, window = make_window(tmp_path)
    try:
        window.profile.url = "https://312node.net/s/test"
        window.profile.pro_verification = verification_record(
            {
                "kid": next(iter(TRUSTED_PRO_KEYS)),
                "url_sha256": digest(window.profile.url.encode("utf-8")),
            },
            window.profile.config,
        )
        window.refresh_profile()
        window.show()
        window.resize(840, 620)
        window.navigate(1)
        app.processEvents()
        assert all(
            not badge.isHidden()
            for badge in (
                window.home_pro_badge,
                window.connections_pro_badge,
                window.profile_pro_badge,
            )
        )
        assert "PRO · " in window.profile_list.item(0).text()
        assert window.profile_actions.height() < 300
        window.profile.url = "https://example.com/s/test"
        window.refresh_profile()
        assert window.profile_pro_badge.isHidden()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("has_profiles", [False, True])
def test_saved_subscriptions_are_scheduled_at_startup(tmp_path, monkeypatch, has_profiles):
    app, window = make_window(tmp_path)
    calls = []
    monkeypatch.setattr(window, "schedule_location_check", lambda p: calls.append(p.id))
    try:
        if has_profiles:
            second = Profile("Second", "", {"proxies": []}, "")
            window.library = window.library.upsert(second, activate=False)
        else:
            window.profile = None
            window.library = ProfileLibrary()
        app.processEvents()
        assert calls == [p.id for p in window.library.profiles]
    finally:
        window.close()
        app.processEvents()


def test_settings_have_no_tab_line_or_network_and_map_hints(tmp_path):
    app, window = make_window(tmp_path)
    try:
        assert not window.settings_tabs.tabBar().drawBase()
        from PySide6.QtWidgets import QWidget

        for index in (1, 2):
            assert all(
                not field.toolTip()
                for field in window.settings_tabs.widget(index).findChildren(QWidget)
            )
        assert not window.client_point_result.text()
    finally:
        window.close()
        app.processEvents()


def test_home_status_tag_is_below_the_power_button(tmp_path):
    from PySide6.QtCore import QPoint

    app, window = make_window(tmp_path)
    try:
        window.show()
        app.processEvents()
        bottom = window.power.mapTo(window, QPoint(0, window.power.height())).y()
        top = window.network_tag.mapTo(window, QPoint(0, 0)).y()
        assert top > bottom
        assert window.network_tag.text() == "Отключено"
        window.connected = True
        window.network_state = "error"
        window.groups_data = [{"name": "main", "current": "route-0"}]
        window.delay_results[(window.profile.id, "route-0")] = {
            "value": 0,
            "checked_at": time.time(),
        }
        window.update_network_tag()
        assert window.network_tag.text() == "Нет ответа"
    finally:
        window.connected = False
        window.close()
        app.processEvents()


def test_about_keeps_full_data_path_and_wraps_long_values(tmp_path):
    app, window = make_window(tmp_path)
    try:
        window.resize(840, 620)
        window.show()
        window.navigate(7)
        app.processEvents()
        data = window.application_detail_values["Данные"]
        assert data.text().replace("\u200b", "") == str(window.store.root)
        assert data.wordWrap()
        assert window.application_version.wordWrap()
        assert window.application_details.isVisible()
        assert window.stack.widget(7).horizontalScrollBar().maximum() == 0
        long_path = "C:/Users/Example/" + "long-folder-name/" * 12
        data.setText(breakable_text(long_path))
        QTest.qWait(50)
        assert data.text().replace("\u200b", "") == long_path
        assert data.sizePolicy().hasHeightForWidth()
        assert data.height() >= data.heightForWidth(data.width())
    finally:
        window.close()
        app.processEvents()


def test_connections_have_one_selector_and_a_strategy_tag(tmp_path):
    app, window = make_window(tmp_path)
    try:
        window.show()
        window.navigate(4)
        app.processEvents()
        assert window.connections_profile_selector.isVisible()
        assert window.group_combo.isHidden()
        assert window.connections_strategy_tag.text() == "Не задана"
        window.profile.config["proxy-groups"] = [
            {"name": "Automatic", "type": "fallback", "proxies": ["route-0", "route-1"]}
        ]
        window.refresh_profile()
        assert window.connections_strategy_tag.text() == "Резервирование"
        assert window.connections_strategy_tag.isVisible()
        assert window.delay_timer.interval() == 15000
        window.set_connected("test")
        window.navigate(0)
        app.processEvents()
        assert window.route_panel.isHidden()
    finally:
        window.connected = False
        window.close()
        app.processEvents()


def test_native_tooltips_are_suppressed_only_on_requested_screens(tmp_path):
    from PySide6.QtCore import QEvent

    app, window = make_window(tmp_path)
    try:
        for index, target in (
            (4, window.node_list.viewport()),
            (6, window.rule_table.viewport()),
            (8, window.flow_views[0].viewport()),
        ):
            window.navigate(index)
            event = QEvent(QEvent.Type.ToolTip)
            assert window.eventFilter(target, event)
        window.navigate(2)
        assert not window.eventFilter(window.poll_interval, QEvent(QEvent.Type.ToolTip))
    finally:
        window.close()
        app.processEvents()


def test_applications_keep_updating_during_delay_checks_on_other_screens(tmp_path, monkeypatch):
    from tests.test_flow_monitor import flow

    app, window = make_window(tmp_path)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(type(window.core), "running", property(lambda _: True))
    monkeypatch.setattr(window.core, "update_route_health", lambda _: None)
    monkeypatch.setattr(
        window.core,
        "connection_snapshot",
        lambda: [
            flow("browser", process="chrome.exe"),
            flow("chat", process="discord.exe"),
            flow("system", process="service.exe"),
        ],
    )

    def probe(*args):
        entered.set()
        assert release.wait(3)
        return {"route-0": {"value": 25, "checked_at": time.time()}}

    monkeypatch.setattr("nodera.workbench.probe_delays", probe)
    window.connected = True
    try:
        window.navigate(1)
        window.test_group()
        assert entered.wait(1)
        window.capture_flows()
        deadline = time.monotonic() + 2
        while len(window.flow_monitor.flows) < 3 and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        assert {row["process"] for row in window.flow_monitor.rows()} == {
            "chrome.exe",
            "discord.exe",
            "service.exe",
        }
        assert "delays" in window.inspections
        assert window.task is None
        assert window.profile_selector.isEnabled()
        assert window.nav[4].isEnabled()
        release.set()
        while window.inspections and time.monotonic() < deadline + 2:
            app.processEvents()
            QTest.qWait(5)
        assert not window.inspections
        assert window.delay_results[(window.profile.id, "route-0")]["value"] == 25

        window.flow_timer.start(100000)
        monkeypatch.setattr(
            window.core,
            "status",
            lambda: dict(
                download=0,
                upload=0,
                connections=0,
                download_rate=0,
                upload_rate=0,
                uptime=1,
                flows=[],
            ),
        )
        monkeypatch.setattr(window.core, "groups", lambda: window.groups_data)
        window.poll()
        deadline = time.monotonic() + 2
        while window.poll_task and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        assert len(window.flow_monitor.flows) == 3
    finally:
        release.set()
        deadline = time.monotonic() + 3
        while window.inspections and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        window.connected = False
        window.close()
        app.processEvents()
