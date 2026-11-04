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


def wait(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


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
