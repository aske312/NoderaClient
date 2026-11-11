import os
import time
from dataclasses import replace
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.models import Profile, Settings
from nodera.storage import Store
from nodera.ui import STYLE, MainWindow


def test_window_initial_state_navigation_and_profile(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    window = MainWindow(Store(tmp_path), Settings(), None)
    window.show()
    app.processEvents()
    assert not window.power.isEnabled()
    assert not hasattr(window, "core_path")
    for index in range(9):
        window.navigate(index)
        expected = 0 if index in (1, 4, 6, 8) else 2 if index in (3, 5) else index
        assert window.stack.currentIndex() == expected
    wait_task(window, app)
    window.profile = Profile(
        "Test PRO",
        "https://example.com/s/private",
        {"proxies": [{"name": "node", "type": "ss"}]},
        "2026-11-03T10:00:00+00:00",
    )
    window.refresh_profile()
    assert window.power.isEnabled()
    assert "private" not in window.profile_details.text()
    assert window.profile_title.text() == "Test PRO"
    assert window.diagnostic_list.count() == 5
    window.close()
    app.processEvents()


def wait_task(window, app):
    deadline = time.monotonic() + 5
    while (window.task or window.inspections) and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
        time.sleep(0.01)
    assert window.task is None
    assert not window.inspections


def test_tools_are_inside_settings_and_map_fills_home(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(), None)
    window.timer.stop()
    window.show()
    app.processEvents()
    assert set(window.nav) == {
        0,
        1,
        2,
        4,
        6,
        7,
        8,
        window.geodata_page_index,
        window.geolocation_page_index,
    }
    assert window.settings_tabs.indexOf(window.events_section) >= 0
    assert window.settings_tabs.indexOf(window.diagnostics_section) >= 0
    for width, height in ((940, 660), (1100, 760)):
        window.resize(width, height)
        app.processEvents()
        assert window.world_map.geometry() == window.stack.widget(0).rect()
    window.navigate(3)
    assert window.stack.currentIndex() == 2
    assert window.settings_tabs.currentWidget() is window.events_section
    window.navigate(7)
    assert window.application_values["Версия приложения"]
    assert window.application_values["Ядро"].startswith("Mihomo ")
    window.close()
    app.processEvents()


def test_address_table_covers_profile_not_only_selected_node(tmp_path):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "Test",
        "",
        {
            "proxies": [
                {"name": "A", "type": "ss", "server": "8.8.8.8"},
                {"name": "B", "type": "tuic", "server": "8.8.8.8"},
                {"name": "C", "type": "vless", "server": "1.1.1.1"},
            ]
        },
        "",
    )
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), profile)
    window.timer.stop()
    assert window.address_table.rowCount() == 2
    counts = [window.address_table.item(row, 5).text() for row in range(2)]
    assert sorted(counts) == ["1", "2"]
    window.node_list.setCurrentRow(1)
    assert window.address_table.rowCount() == 2
    window.close()
    app.processEvents()


def test_pro_policy_search_and_local_mode_override(tmp_path):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "PRO",
        "https://example.com/s/private",
        {
            "proxies": [{"name": "A", "type": "vless"}],
            "tun": {"enable": False},
            "rules": ["PROCESS-NAME,example.exe,A", "DOMAIN,bad.example,REJECT", "MATCH,DIRECT"],
        },
        "2026-11-03T10:00:00+00:00",
    )
    window = MainWindow(Store(tmp_path), Settings(), profile)
    window.timer.stop()
    assert window.connection_mode.currentData() == "tun"
    assert "TUN" in window.mode_badge.text()
    assert window.rule_table.rowCount() == 3
    window.rule_search.setText("example.exe")
    assert not window.rule_table.isRowHidden(0)
    assert window.rule_table.isRowHidden(1)
    assert window.rule_table.isRowHidden(2)
    window.connection_mode.setCurrentIndex(window.connection_mode.findData("proxy"))
    window.save_settings()
    saved = Store(tmp_path).load_settings()
    assert saved.tun is False
    assert "локальный прокси" in window.mode_badge.text().casefold()
    window.close()
    app.processEvents()


def test_server_controls_selection_and_health_check(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "PRO",
        "https://example.com/s/private",
        {
            "proxies": [{"name": "A", "type": "ss"}, {"name": "B", "type": "trojan"}],
            "proxy-groups": [
                {"name": "Manual", "type": "select", "proxies": ["A", "B"]},
                {"name": "Auto", "type": "fallback", "proxies": ["A", "B"]},
            ],
        },
        "2026-11-03T10:00:00+00:00",
    )
    window = MainWindow(Store(tmp_path), Settings(tun=False), profile)
    window.timer.stop()
    assert window.node_list.count() == 2
    assert window.select_button.isEnabled()
    assert not window.delay_button.isEnabled()
    window.node_list.setCurrentRow(1)
    window.select_server()
    wait_task(window, app)
    assert Store(tmp_path).load_profile().selections == {"Manual": "B"}
    assert window.current_group()["current"] == "B"
    window.set_connected("test")
    groups = [
        {
            "name": "Manual",
            "type": "Selector",
            "current": "A",
            "members": [
                {"name": "A", "type": "VLESS", "delay": None},
                {"name": "B", "type": "Trojan", "delay": None},
            ],
        },
        {
            "name": "Auto",
            "type": "Fallback",
            "current": "A",
            "members": [
                {"name": "A", "type": "VLESS", "delay": None},
                {"name": "B", "type": "Trojan", "delay": None},
            ],
        },
    ]
    window.set_groups(groups)
    assert window.select_button.isEnabled()
    window.group_combo.setCurrentIndex(1)
    assert not window.select_button.isEnabled()
    assert window.delay_button.isEnabled()
    window.group_combo.setCurrentIndex(0)
    window.node_list.setCurrentRow(1)
    assert window.current_node() == "B"
    window.server_search.setText("Trojan")
    assert window.node_list.item(0).isHidden()
    assert not window.node_list.item(1).isHidden()
    window.server_search.clear()
    monkeypatch.setattr(
        window.core, "select", lambda group, member: [dict(groups[0], current=member), groups[1]]
    )
    window.select_server()
    wait_task(window, app)
    assert window.profile.selections == {"Manual": "B"}
    assert Store(tmp_path).load_profile().selections == {"Manual": "B"}
    monkeypatch.setattr(window.core, "delay", lambda member: 73)
    window.test_server()
    deadline = time.monotonic() + 3
    while "server-test" in window.inspections and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert not window.task and "server-test" not in window.inspections
    assert "73 мс" in window.server_result.text()
    assert "Доступен" in window.server_result.text()
    window.close()
    app.processEvents()


def test_auto_update_while_connected_preserves_profile_on_failure_and_retries(
    tmp_path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "PRO",
        "https://example.com/s/private",
        {"proxies": [{"name": "A", "type": "ss"}]},
        "2020-01-01T00:00:00+00:00",
        selections={"Manual": "A"},
    )
    store = Store(tmp_path)
    store.save_profile(profile)
    window = MainWindow(store, Settings(update_hours=1), profile)
    window.timer.stop()
    window.subscription_timer.stop()
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise ClientError("Нет сети")

    from nodera.models import ClientError

    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", fail)
    window.connected = True
    window.auto_update_profile()
    wait_task(window, app)
    assert calls == [True]
    window.connected = False
    window.update_retry_at = 0
    window.auto_update_profile()
    wait_task(window, app)
    assert calls == [True, True]
    assert store.load_profile() == profile
    assert window.profile == profile
    window.auto_update_profile()
    assert calls == [True, True]  # backoff, no modal error loop
    window.update_retry_at = 0
    updated = replace(profile, updated_at=datetime.now(timezone.utc).isoformat(), selections={})
    monkeypatch.setattr("nodera.profiles_ui.fetch_profile", lambda *args, **kwargs: updated)
    window.auto_update_profile()
    wait_task(window, app)
    assert window.profile.updated_at == updated.updated_at
    assert window.profile.selections == profile.selections
    assert store.load_profile() == window.profile
    window.auto_update_profile()
    assert window.task is None
    window.close()
    app.processEvents()


def test_bottom_navigation_reaches_every_page_and_closes_palette(tmp_path, monkeypatch):
    monkeypatch.setattr("nodera.autostart.is_enabled", lambda: False)
    app = QApplication.instance() or QApplication([])
    profile = Profile("Test", "", {"proxies": []}, "")
    window = MainWindow(Store(tmp_path), Settings(update_hours=0, geo_auto=False), profile)
    window.timer.stop()
    window.show()
    app.processEvents()
    try:
        assert (
            window.navigation.mapTo(window, QPoint(0, 0)).y()
            >= window.stack.mapTo(window, QPoint(0, window.stack.height())).y()
        )
        for index in (0, 4, 1):
            window.nav[index].click()
            assert window.stack.currentIndex() == index
            assert window.nav[index].isChecked()
        for index in (6, 8, 2, 7):
            window.navigation.tools.click()
            app.processEvents()
            assert window.navigation.palette.isVisible()
            window.nav[index].click()
            app.processEvents()
            assert window.stack.currentIndex() == index
            assert window.navigation.tools.isChecked()
            assert not window.navigation.palette.isVisible()
        window.navigation.tools.click()
        app.processEvents()
        QTest.keyClick(window.navigation.palette, Qt.Key.Key_Escape)
        app.processEvents()
        assert not window.navigation.palette.isVisible()
        assert window.stack.currentIndex() == 7
        window.nav[0].click()
        window.current_route_button.setText("Long server name " * 12)
        app.processEvents()
        center = window.power.mapTo(
            window, QPoint(window.power.width() // 2, window.power.height() // 2)
        )
        assert abs(center.x() - window.stack.geometry().center().x()) <= 2
        window.activateWindow()
        window.setFocus()
        app.processEvents()
        QTest.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert window.stack.currentIndex() == 4
        QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert window.navigation.palette.isVisible()
        QTest.keyClick(window.navigation.palette, Qt.Key.Key_Escape)
        app.processEvents()
        assert not window.navigation.palette.isVisible()
        wait_task(window, app)
    finally:
        window.quit_requested = True
        window.close()
        app.processEvents()
