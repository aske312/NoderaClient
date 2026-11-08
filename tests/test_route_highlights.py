import os
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.models import Profile, Settings
from nodera.storage import Store
from nodera.ui import MainWindow


@pytest.fixture
def route_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "Test",
        "",
        {
            "proxies": [{"name": name, "type": "direct"} for name in ("A", "B", "C")],
            "proxy-groups": [{"name": "auto", "type": "fallback", "proxies": ["A", "B", "C"]}],
        },
        "",
    )
    window = MainWindow(
        Store(tmp_path), Settings(tun=False, geo_auto=False, update_hours=0), profile
    )
    for timer in window.findChildren(QTimer):
        timer.stop()
    window.show()
    window.navigate(4)
    window.connected = True
    try:
        yield app, window
    finally:
        window.connected = False
        window.quit_requested = True
        window.close()
        app.processEvents()


def groups(window, current, active=None):
    window.set_groups(
        [
            {
                "name": "auto",
                "type": "Fallback",
                "current": current,
                "active": [current] if active is None else active,
                "members": [
                    {"name": name, "type": "Direct", "delay": 25} for name in ("A", "B", "C")
                ],
            }
        ]
    )


def rows(window):
    return {
        window.node_list.item(i).data(Qt.ItemDataRole.UserRole): window.node_list.item(i).data(
            Qt.ItemDataRole.UserRole + 1
        )
        for i in range(window.node_list.count())
    }


def test_automatic_switch_is_distinct_from_selection_and_frozen_state_wins(route_window):
    app, window = route_window
    groups(window, "A")
    window.node_list.setCurrentRow(2)
    assert rows(window)["A"]["active"] and not rows(window)["C"]["active"]
    assert rows(window)["A"]["current"]
    groups(window, "B")
    assert window.current_node() == "C"
    assert rows(window)["B"]["active"] and not rows(window)["A"]["active"]
    assert rows(window)["B"]["current"] and not rows(window)["A"]["current"]
    window.core.frozen_routes["B"] = time.monotonic() + 120
    window.render_nodes()
    assert rows(window)["B"]["frozen"] and not rows(window)["B"]["active"]
    assert not rows(window)["B"]["current"]
    assert "Заморожен" in window.node_list.item(1).text()
    groups(window, "A")
    assert rows(window)["A"]["active"] and rows(window)["B"]["frozen"]
    window.core.frozen_routes.clear()
    groups(window, "B")
    assert rows(window)["B"]["active"] and not rows(window)["B"]["frozen"]
    window.set_disconnected()
    assert not any(row["active"] for row in rows(window).values())
    assert not any(row["current"] for row in rows(window).values())
    app.processEvents()


def test_forced_route_and_nested_group_current_are_highlighted(route_window):
    _, window = route_window
    groups(window, "A", active=[])
    assert rows(window)["A"]["active"]
    window.profile = replace(window.profile, forced_server="C")
    window.render_nodes()
    assert rows(window)["C"]["active"] and not rows(window)["A"]["active"]
    assert rows(window)["C"]["current"]


def test_current_route_is_distinct_from_connections_on_previous_routes(route_window):
    _, window = route_window
    window.flow_monitor.update(
        [
            {"id": "old", "metadata": {}, "chains": ["A", "auto"]},
            {"id": "other", "metadata": {}, "chains": ["C", "auto"]},
        ]
    )
    groups(window, "B")
    window.node_list.setCurrentRow(0)
    assert all(row["active"] for row in rows(window).values())
    assert [name for name, row in rows(window).items() if row["current"]] == ["B"]
    assert "Текущий" in window.node_list.item(1).text()
    assert "В работе" in window.node_list.item(0).text()
    assert window.current_node() == "A"
    groups(window, "C")
    assert rows(window)["C"]["current"] and not rows(window)["B"]["current"]


def test_live_balanced_connections_update_highlights_and_drop_completed_routes(
    route_window, monkeypatch
):
    app, window = route_window
    groups(window, "", active=[])
    window.groups_data[0]["type"] = "LoadBalance"
    window.node_list.setCurrentRow(1)
    connections = [
        {"id": "one", "metadata": {}, "chains": ["A", "auto"]},
        {"id": "two", "metadata": {}, "chains": ["C", "auto"]},
    ]
    monkeypatch.setattr(type(window.core), "running", property(lambda _: True))
    monkeypatch.setattr(window.core, "connection_snapshot", lambda: connections)

    def capture():
        window.capture_flows()
        deadline = time.monotonic() + 3
        while "flows" in window.inspections and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        assert "flows" not in window.inspections

    capture()
    assert rows(window)["A"]["active"] and rows(window)["C"]["active"]
    assert rows(window)["A"]["current"] and rows(window)["C"]["current"]
    assert not rows(window)["B"]["active"] and window.current_node() == "B"
    connections.clear()
    capture()
    assert not any(row["active"] for row in rows(window).values())
    assert not any(row["current"] for row in rows(window).values())
    assert len(window.flow_monitor.flows) == 2  # History does not keep routes active.
