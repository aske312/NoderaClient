import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.delay_probe import probe_delays
from nodera.models import ClientError, Profile, Settings
from nodera.storage import Store
from nodera.ui import MainWindow
from nodera.world_map import WorldCanvas


def wait(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


def make_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "test",
        "",
        {
            "proxies": [{"name": f"route-{i}", "type": "direct"} for i in range(40)],
            "proxy-groups": [
                {"name": "automatic", "type": "fallback", "proxies": ["route-0", "route-1"]}
            ],
        },
        "",
    )
    window = MainWindow(Store(tmp_path), Settings(update_hours=0, geo_auto=False), profile)
    window.timer.stop()
    window.client_location_timer.stop()
    window.show()
    window.navigate(4)
    return app, window


def test_all_route_probes_are_bounded_and_cancel_pending_work(monkeypatch):
    active, maximum = 0, 0
    lock = threading.Lock()

    class API:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def delay(self, name):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.002)
            with lock:
                active -= 1
            return 15

    monkeypatch.setattr("nodera.delay_probe.Controller", API)
    names = [f"route-{i}" for i in range(65)]
    values = probe_delays(1, "secret", names + names)
    assert set(values) == set(names) and maximum <= 4
    assert all(value["value"] == 15 and value["checked_at"] for value in values.values())
    assert probe_delays(1, "secret", names, lambda: True) == {}


def test_explicit_forced_mode_and_one_click_return(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    window.connected = True
    window.node_list.setCurrentRow(1)
    groups = window.groups_data
    calls = []
    monkeypatch.setattr(window.core, "force_server", lambda name: calls.append(name) or groups)
    monkeypatch.setattr(window.core, "restore_policy", lambda: calls.append("strategy") or groups)
    window.force_button.click()
    wait(app, lambda: window.task is None)
    assert "route-1" in window.route_mode_label.text()
    assert window.restore_policy_button.isVisible()
    assert window.node_list.item(1).data(Qt.ItemDataRole.UserRole + 1)["forced"]
    window.restore_policy_button.click()
    wait(app, lambda: window.task is None)
    assert calls == ["route-1", "strategy"]
    assert not window.profile.forced_server and not window.force_button.isChecked()
    assert window.force_button is window.restore_policy_button
    assert window.force_button.text() == "Включить маршрут"
    assert "Стратегия подписки" in window.route_mode_label.text()
    window.connected = False
    window.close()


def test_global_checks_all_routes_and_periodic_refresh_preserves_selection(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    window.connected = True
    selected = window.current_node()
    window.refresh_controls()
    calls = []

    def probe(port, secret, names, cancelled, aliases=None):
        calls.append(names)
        return {name: {"value": 123, "checked_at": time.time()} for name in names}

    monkeypatch.setattr("nodera.workbench.probe_delays", probe)
    window.delay_button.click()
    wait(app, lambda: not window.inspections)
    assert len(calls[0]) == 40 and window.current_node() == selected
    assert "40/40" in window.delay_status.text()
    monkeypatch.setattr(type(window.core), "running", property(lambda _: True))
    window.delay_timer.timeout.emit()
    wait(app, lambda: not window.inspections)
    assert len(calls) == 2
    geo_calls = []
    monkeypatch.setattr(window, "check_locations", lambda **kwargs: geo_calls.append(kwargs))
    window.geo_button.click()
    window.server_tabs.setCurrentIndex(1)
    app.processEvents()
    window.geo_button.click()
    assert geo_calls == [{"all_nodes": True}, {"all_nodes": True}]
    assert window.route_actions.isHidden()
    assert window.delay_button.isVisible()
    window.connected = False
    window.close()


def test_map_only_describes_the_hovered_point():
    app = QApplication.instance() or QApplication([])
    canvas = WorldCanvas()
    canvas.resize(700, 500)
    canvas.world_map.set_client_point(
        dict(code="RU", name="Client", lat=55.7, lon=37.6, source="manual")
    )
    canvas.world_map.set_locations(
        [
            dict(
                code="DE",
                name="Germany",
                lat=50,
                lon=9,
                role="relay",
                node="route-a",
                host="relay.example",
                route=0,
                step=0,
                order_known=True,
            )
        ],
        True,
    )
    canvas.show()
    app.processEvents()
    regions = canvas.world_map.hit_regions
    assert len(regions) == 2
    assert canvas.toolTip() == canvas.world_map.toolTip() == ""
    assert canvas.world_map.tooltip_at(QPoint(0, 0)) == ""
    relay = next(region for region in regions if region[2].get("role") == "relay")
    text = canvas.world_map.tooltip_at(relay[1].center().toPoint())
    assert "route-a" in text and "relay.example" in text and "Client" not in text
    canvas.close()


def test_network_tag_tracks_actual_probe_and_original_menu_heights(tmp_path, monkeypatch):
    app, window = make_window(tmp_path)
    assert all(button.height() == 36 for button in window.nav.values())
    window.connected = True
    window.profile = None  # no route checks needed for this isolated status test
    monkeypatch.setattr(window.core, "check_connectivity", lambda: 10)
    window.check_connection()
    assert window.network_tag.property("state") == "checking"
    wait(app, lambda: not window.inspections)
    assert window.network_tag.property("state") == "ok"
    assert "Сеть доступна" == window.network_tag.text()
    assert window.network_checked_wall > 0 and "HTTPS" in window.network_tag.toolTip()

    def fail():
        raise ClientError("no answer")

    monkeypatch.setattr(window.core, "check_connectivity", fail)
    window.check_connection()
    wait(app, lambda: not window.inspections)
    assert window.network_tag.property("state") == "error"
    window.sidebar_toggle.click()
    window.sidebar_animation.setCurrentTime(window.sidebar_animation.duration())
    app.processEvents()
    assert all(button.height() == 34 for button in window.nav.values())
    assert window.sidebar.width() == 42
    window.connected = False
    window.close()
