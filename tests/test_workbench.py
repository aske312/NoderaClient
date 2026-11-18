import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.core import runtime_config
from nodera.models import ClientError, Profile, Settings
from nodera.rule_editor import make_rule
from nodera.storage import Store
from nodera.ui import MainWindow
from nodera.updater import Release


def wait(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.mark.parametrize("connected", [False, True])
def test_app_update_download_and_installer_wait_for_user_without_closing_client(
    tmp_path, monkeypatch, connected
):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.timer.stop()
    release = Release("1.2.3", "https://github.com/test", "a" * 64, 9)
    window.available_updates["app"] = release
    window.connected = connected
    calls = []

    def download(*args, **kwargs):
        assert not window.closing
        calls.append("download")
        return tmp_path / "installer.exe"

    def launch(path, downloaded, *, resume):
        assert downloaded == release and resume == connected
        calls.append("launch")

    monkeypatch.setattr("nodera.updater.download_release", download)
    monkeypatch.setattr("nodera.updater.launch_installer", launch)
    monkeypatch.setattr(window, "exit_app", lambda: calls.append("exit"))
    try:
        window.apply_updates()
        assert window.task is None
        wait(app, lambda: not window.inspections)
        assert calls == ["download"]
        assert window.downloaded_update == tmp_path / "installer.exe"
        assert window.update_apply.text() == "Установить Nodera"
        window.apply_updates()
        assert calls == ["download", "launch"]
        assert not window.closing and window.connected == connected
    finally:
        window.connected = False
        window.close()
        app.processEvents()


def test_failed_update_launch_keeps_client_open_and_allows_retry(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.timer.stop()
    window.available_updates["app"] = Release("1.2.3", "https://github.com/test", "a" * 64, 9)
    monkeypatch.setattr(
        "nodera.updater.download_release", lambda *a, **kw: tmp_path / "installer.exe"
    )

    def failed(*args, **kwargs):
        raise ClientError("Installer failed")

    monkeypatch.setattr("nodera.updater.launch_installer", failed)
    monkeypatch.setattr(window, "exit_app", lambda: pytest.fail("Failed update closed the client"))
    try:
        window.apply_updates()
        wait(app, lambda: not window.inspections)
        window.apply_updates()
        assert not window.closing
        assert window.downloaded_update is None
        assert window.update_check.isEnabled() and window.update_apply.isEnabled()
        assert window.update_status.text() == "Installer failed"
    finally:
        window.close()
        app.processEvents()


def test_background_download_survives_connection_change_and_blocks_duplicate(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0, geo_auto=False), None)
    window.timer.stop()
    window.available_updates["app"] = Release("1.2.3", "https://github.com/test", "a" * 64, 9)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def download(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return tmp_path / "installer.exe"

    monkeypatch.setattr("nodera.updater.download_release", download)
    try:
        window.apply_updates()
        wait(app, entered.is_set)
        assert window.task is None
        window.session_generation += 1
        window.apply_updates()
        window.check_updates(automatic=True)
        assert "app" in window.available_updates
        release.set()
        wait(app, lambda: not window.inspections)
        assert calls == [True]
        assert window.downloaded_update == tmp_path / "installer.exe"
    finally:
        release.set()
        wait(app, lambda: not window.inspections)
        window.close()
        app.processEvents()


def test_automatic_update_retries_busy_startup_and_network_failure(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer

    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0, geo_auto=False), None)
    window.timer.stop()
    window.update_timer = QTimer(window)
    window.update_timer.setSingleShot(True)
    calls = []

    def check(**kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise ClientError("Offline")
        return None

    monkeypatch.setattr("nodera.updater.check_release", check)
    try:
        window.task = object()
        window.check_updates(automatic=True)
        assert not calls and window.update_timer.interval() == 60000
        window.task = None
        window.check_updates(automatic=True)
        wait(app, lambda: not window.inspections)
        assert window.update_timer.interval() == 30 * 60 * 1000
        assert window.update_status.text() == "Nodera · Offline"
        window.check_updates(automatic=True)
        wait(app, lambda: not window.inspections)
        assert window.update_timer.interval() == 6 * 60 * 60 * 1000
        assert "актуальны" in window.update_status.text()
    finally:
        window.task = None
        window.close()
        app.processEvents()


def test_update_offer_notifies_once_and_requires_action_before_download(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QSystemTrayIcon

    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        Store(tmp_path), Settings(update_hours=0, geo_auto=False, close_to_tray=False), None
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    notices, downloads = [], []
    monkeypatch.setattr(window.tray, "isVisible", lambda: True)
    monkeypatch.setattr(QSystemTrayIcon, "supportsMessages", lambda: True)
    monkeypatch.setattr(window.tray, "showMessage", lambda *args: notices.append(args))
    monkeypatch.setattr(window, "apply_updates", lambda: downloads.append(True))
    release = Release("9.0.0", "https://github.com/test", "a" * 64, 9)
    monkeypatch.setattr("nodera.updater.check_release", lambda **kwargs: release)
    try:
        window.check_updates(automatic=True)
        wait(app, lambda: not window.inspections)
        assert len(notices) == 1 and not downloads
        window.check_updates(automatic=True)
        wait(app, lambda: not window.inspections)
        assert len(notices) == 1
        window.show_update_offer()
        app.processEvents()
        assert "9.0.0" in window.update_offer.message.text()
        window.update_offer.reject()
        assert not downloads
        window.show_update_offer()
        app.processEvents()
        window.update_offer.action.click()
        assert downloads == [True]
        assert window.stack.currentWidget() == window.application_page
    finally:
        if getattr(window, "update_offer", None):
            window.update_offer.close()
        window.quit_requested = True
        window.close()
        app.processEvents()


def test_one_update_action_handles_all_sources_and_keeps_partial_failures_retryable(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    app = QApplication.instance()
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.timer.stop()
    app_release = Release("9.0.0", "https://github.com/test", "a" * 64, 9)
    core_release = SimpleNamespace(version="1.19.34")
    databases = SimpleNamespace(version="2026-11-19")
    calls = []

    monkeypatch.setattr("nodera.updater.check_release", lambda **kw: app_release)
    monkeypatch.setattr("nodera.core_updates.check_release", lambda *a, **kw: core_release)
    monkeypatch.setattr("nodera.geodata_updates.check_release", lambda *a, **kw: databases)

    def missing_core(*args, **kwargs):
        calls.append("core")
        raise ClientError("Timeout")

    monkeypatch.setattr(window.core_manager, "download", missing_core)
    monkeypatch.setattr(window.geodata_manager, "download", lambda *a, **kw: databases)
    monkeypatch.setattr(window.geodata_manager, "activate", lambda _: calls.append("geodata"))
    monkeypatch.setattr(
        "nodera.updater.download_release", lambda *a, **kw: calls.append("app") or tmp_path / "app"
    )
    monkeypatch.setattr("nodera.updater.launch_installer", lambda *a, **kw: calls.append("launch"))
    window.check_updates()
    wait(app, lambda: not window.inspections)
    assert not calls
    assert set(window.available_updates) == {"app", "core", "geodata"}
    assert all(
        version in window.update_status.text() for version in ("9.0.0", "1.19.34", "2026-11-19")
    )
    window.apply_updates()
    wait(app, lambda: not window.inspections)
    assert calls == ["core", "geodata", "app"]
    assert set(window.available_updates) == {"app", "core"}
    assert "Timeout" in window.update_status.text()
    assert window.update_apply.text() == "Установить Nodera"
    window.apply_updates()
    assert calls[-1] == "launch"


def test_background_poll_preserves_selection_and_does_not_lock_controls(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "P",
        "",
        {
            "proxies": [{"name": "A"}, {"name": "B"}],
            "proxy-groups": [{"name": "Choice", "type": "select", "proxies": ["A", "B"]}],
        },
        "",
    )
    window = MainWindow(Store(tmp_path), Settings(geo_auto=False, update_hours=0), profile)
    window.timer.stop()
    window.set_connected("test")
    window.server_search.setText("B")
    window.node_list.setCurrentRow(1)
    item = window.node_list.currentItem()
    groups = window.groups_data
    entered, release = threading.Event(), threading.Event()

    def status():
        entered.set()
        assert release.wait(3)
        return dict(download=0, upload=0, connections=0, download_rate=0, upload_rate=0, uptime=1)

    monkeypatch.setattr(window.core, "status", status)
    monkeypatch.setattr(window.core, "groups", lambda: groups)
    try:
        window.poll()
        wait(app, entered.is_set)
        assert window.task is None and window.poll_task
        assert window.power.isEnabled() and window.select_button.isEnabled()
        assert window.profile_selector.isEnabled()
        release.set()
        wait(app, lambda: window.poll_task is None)
        assert window.node_list.currentItem() is item
        assert window.current_node() == "B"
        assert window.server_search.text() == "B"
        # An old response cannot revive UI from a previous session.
        release.clear()
        entered.clear()
        window.poll()
        wait(app, entered.is_set)
        window.session_generation += 1
        window.set_disconnected()
        release.set()
        wait(app, lambda: window.poll_task is None)
        assert not window.connected and window.stats[0].text() == "—"
    finally:
        release.set()
        wait(app, lambda: window.poll_task is None)
        window.close()
        app.processEvents()


def test_journal_pause_filter_and_local_rules_persist(tmp_path):
    app = QApplication.instance() or QApplication([])
    profile = Profile("P", "", {"proxies": [{"name": "A"}], "rules": ["MATCH,DIRECT"]}, "")
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), profile)
    window.timer.stop()
    try:
        window.rule_table.setCurrentCell(0, 0)
        assert not window.rule_action_buttons[1].isEnabled()
        assert not window.rule_action_buttons[2].isEnabled()
        assert not window.rules_apply_button.isEnabled()
        window.log_search.setText("needle")
        window.log("needle debug", "debug", "Mihomo")
        window.log_pause.setChecked(True)
        window.log("needle error", "error", "Mihomo")
        assert "needle error" not in window.events.toPlainText()
        window.log_pause.setChecked(False)
        assert "needle error" in window.events.toPlainText()
        window.log_filter.setCurrentIndex(window.log_filter.findData("error"))
        assert "needle debug" not in window.events.toPlainText()
        rule = make_rule("DOMAIN-SUFFIX", "example.org", "REJECT")
        window.save_local_rules([rule])
        wait(app, lambda: window.task is None)
        assert window.library.active.local_rules == [rule]
        assert Store(tmp_path).load_profile().local_rules == [rule]
        assert window.rule_table.rowCount() == 2
        assert window.rule_table.item(0, 3).text() == "Локальное"
        window.rule_table.setCurrentCell(0, 0)
        assert window.rule_action_buttons[1].isEnabled()
        assert window.rule_action_buttons[2].isEnabled()
        window.rule_table.setCurrentCell(1, 0)
        assert not window.rule_action_buttons[1].isEnabled()
        assert not window.rule_action_buttons[2].isEnabled()
        config = runtime_config(
            profile.config,
            Settings(tun_stack="system", tun_mtu=1400, ipv6_mode="off"),
            1,
            "secret",
            [rule],
        )
        assert config["rules"] == [rule, "MATCH,DIRECT"]
        assert config["tun"]["mtu"] == 1400 and config["tun"]["stack"] == "system"
        assert config["ipv6"] is False
        assert profile.config["rules"] == ["MATCH,DIRECT"]
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize(
    "kind,value", [("DOMAIN", "x,REJECT"), ("IP-CIDR", "::1/128"), ("PROCESS-NAME", "C:/app.exe")]
)
def test_invalid_local_rule_cannot_change_syntax(kind, value):
    with pytest.raises(ClientError):
        make_rule(kind, value, "DIRECT")


@pytest.mark.parametrize("available", [False, True])
def test_connection_state_reflects_network_check_and_survives_api_poll(
    tmp_path, monkeypatch, available
):
    app = QApplication.instance() or QApplication([])
    profile = Profile("P", "", {"proxies": [{"name": "A"}]}, "")
    window = MainWindow(Store(tmp_path), Settings(geo_auto=False, update_hours=0), profile)
    window.timer.stop()

    def probe():
        if not available:
            raise ClientError("Network unavailable")

    monkeypatch.setattr(window.core, "check_connectivity", probe)
    # This test simulates a connection without a core; do not probe real API ports.
    monkeypatch.setattr(window, "test_group", lambda: None)
    monkeypatch.setattr(
        window.core,
        "status",
        lambda: dict(download=0, upload=0, connections=0, download_rate=0, upload_rate=0, uptime=1),
    )
    monkeypatch.setattr(window.core, "groups", lambda: window.groups_data)
    try:
        window.set_connected("test", verify=True)
        wait(app, lambda: not window.inspections)
        assert window.network_state == ("ok" if available else "error")
        status = window.state.text()
        window.poll()
        wait(app, lambda: window.poll_task is None)
        assert window.state.text() == status
        if available:
            assert "доступ есть" in status
        else:
            assert "нет ответа" in status
        assert "HTTPS" in window.state.toolTip()
        window.set_disconnected()
        assert window.network_state == "unverified"
    finally:
        window.close()
        app.processEvents()


def test_old_network_error_cannot_change_state_after_disconnect(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.timer.stop()
    entered, release = threading.Event(), threading.Event()
    errors = []

    def probe():
        entered.set()
        assert release.wait(3)
        raise ClientError("Error from previous connection")

    try:
        window.start_inspection("connectivity", probe, lambda _: None, errors.append)
        wait(app, entered.is_set)
        window.session_generation += 1
        window.set_disconnected()
        release.set()
        wait(app, lambda: not window.inspections)
        assert errors == []
        assert not window.connected and window.network_state == "unverified"
    finally:
        release.set()
        wait(app, lambda: not window.inspections)
        window.close()
        app.processEvents()


def test_network_check_restarts_when_server_changes_during_probe(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.timer.stop()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def probe():
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
            raise ClientError("Old server failed")

    monkeypatch.setattr(window.core, "check_connectivity", probe)
    try:
        window.set_connected("test", verify=True)
        wait(app, entered.is_set)
        window.session_generation += 1
        release.set()
        wait(app, lambda: window.network_state == "ok" and not window.inspections)
        assert len(calls) == 2
        assert window.network_error == ""
    finally:
        release.set()
        wait(app, lambda: not window.inspections)
        window.close()
        app.processEvents()
