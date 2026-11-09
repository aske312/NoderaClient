import os
import threading
import time
from http.server import BaseHTTPRequestHandler

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from nodera.bundled_core import bundled_location
from nodera.controller import Controller
from nodera.core import Core
from nodera.models import Profile, Settings
from nodera.settings_ui import detect_point
from nodera.storage import Store
from nodera.ui import MainWindow
from tests.loopback import ThreadingHTTPServer


def test_automatic_client_point_saved_once_and_network_rows_follow_mode(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), None)
    window.client_location_timer.stop()
    window.timer.stop()
    calls = []
    point = dict(code="DE", lat=50.1, lon=8.6, name="Frankfurt", source="ip-physical")
    monkeypatch.setattr(
        "nodera.settings_ui.detect_client_location", lambda: calls.append(1) or point
    )
    detect_point(window, automatic=True)
    while window.inspections:
        app.processEvents()
        time.sleep(0.01)
    window.auto_client_location()
    saved = Store(tmp_path).load_settings()
    assert calls == [1] and saved.client_point["code"] == "DE"
    assert saved.client_location_attempted and window.world_map.client_point["code"] == "DE"
    window.connection_mode.setCurrentIndex(window.connection_mode.findData("proxy"))
    assert not window.stack_setting.setting_row.isEnabled()
    assert not window.mtu_setting.isEnabled()
    window.connection_mode.setCurrentIndex(window.connection_mode.findData("tun"))
    assert window.stack_setting.setting_row.isEnabled() and window.mtu_setting.isEnabled()
    window.connected = True
    window.refresh_controls()
    assert all(not field.setting_row.isEnabled() for field in window.network_settings_controls)
    assert window.flush_dns_button.isEnabled()
    window.connected = False
    window.close()


def test_connection_delete_encodes_id_and_never_deletes_all():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(204)

    with Controller(1, "secret", httpx.MockTransport(handle)) as controller:
        controller.close_connection("a/b?#")
    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url.raw_path == b"/connections/a%2Fb%3F%23"


def test_recheck_uses_group_healthcheck_url_and_reaches_nested_groups():
    def handle(request):
        if request.url.path == "/proxies":
            return httpx.Response(
                200,
                json={
                    "proxies": {
                        "top": {"type": "Selector", "all": ["auto"]},
                        "auto": {
                            "type": "Fallback",
                            "all": ["working"],
                            "testUrl": "https://example.invalid/check",
                            "expectedStatus": "200",
                        },
                        "working": {"type": "Direct"},
                    }
                },
            )
        assert request.url.path == "/group/auto/delay"
        assert request.url.params["url"] == "https://example.invalid/check"
        assert request.url.params["expected"] == "200"
        return httpx.Response(200, json={"working": 10})

    with Controller(1, "secret", httpx.MockTransport(handle)) as controller:
        assert controller.recheck_routes(["top"]) == ["auto"]


def test_selected_connection_can_be_closed_in_automatic_strategy(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = Profile(
        "test",
        "",
        {
            "proxies": [{"name": "route", "type": "direct"}],
            "proxy-groups": [{"name": "auto", "type": "fallback", "proxies": ["route"]}],
        },
        "",
    )
    window = MainWindow(Store(tmp_path), Settings(update_hours=0), profile)
    window.client_location_timer.stop()
    window.timer.stop()
    window.connected = True
    window.navigate(8)
    window.flow_monitor.update(
        [{"id": "active-id", "metadata": {"host": "example.invalid"}, "chains": ["auto"]}]
    )
    window.render_flows(force=True)
    view = window.flow_views[0]
    view.topLevelItem(0).child(0).setSelected(True)
    assert window.close_flows_button.isEnabled()
    calls = []
    monkeypatch.setattr(window.core, "close_connections", lambda ids: (calls.extend(ids) or ids, 0))
    window.close_flows_button.click()
    while window.task:
        app.processEvents()
        time.sleep(0.01)
    assert calls == ["active-id"]
    assert not window.flow_monitor.flows["active-id"]["active"]
    assert window.node_list.pin_enabled
    window.connected = False
    window.close()


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_real_core_force_route_close_stream_and_restore_policy(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"hello")
            self.wfile.flush()
            entered.set()
            release.wait(8)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    profile = Profile(
        "test",
        "",
        {"proxies": [{"name": "route-a", "type": "direct"}], "rules": ["MATCH,REJECT"]},
        "",
    )
    core = Core(tmp_path / "runtime")
    errors = []

    def request():
        try:
            with httpx.Client(
                proxy=f"http://127.0.0.1:{core.mixed_port}", trust_env=False, timeout=6
            ) as client:
                client.get(f"http://127.0.0.1:{server.server_port}/")
        except httpx.HTTPError as exc:
            errors.append(type(exc).__name__)

    worker = None
    try:
        core.start(profile, Settings(tun=False, system_proxy=False))
        groups = core.force_server("route-a")
        assert next(group for group in groups if group["name"] == "GLOBAL")["current"] == "route-a"
        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        assert entered.wait(5)
        flows = core.status()["flows"]
        identity = next(
            row["id"]
            for row in flows
            if str(row.get("metadata", {}).get("destinationPort")) == str(server.server_port)
        )
        assert core.close_connections([identity, identity]) == ([identity], 0)
        worker.join(3)
        assert not worker.is_alive() and errors
        core.restore_policy()
        with Controller(core.port, core.secret) as controller:
            assert controller.request("GET", "/configs")["mode"] == "rule"
    finally:
        release.set()
        core.stop()
        server.shutdown()
        server.server_close()
        if worker:
            worker.join(3)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
@pytest.mark.parametrize("strategy", ["fallback", "url-test", "load-balance"])
def test_real_core_automatic_strategy_rechecks_and_uses_working_route(
    tmp_path, monkeypatch, strategy
):
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    monkeypatch.setattr("nodera.core.CONNECTIVITY_URL", url)
    profile = Profile(
        strategy,
        "",
        {
            "proxies": [
                {"name": "broken", "type": "http", "server": "127.0.0.1", "port": 1},
                {"name": "working", "type": "direct"},
            ],
            "proxy-groups": [
                {
                    "name": "automatic",
                    "type": strategy,
                    "proxies": ["broken", "working"],
                    "url": url,
                    "interval": 300,
                }
            ],
            "rules": ["MATCH,automatic"],
        },
        "",
    )
    core = Core(tmp_path / "runtime")
    try:
        core.start(profile, Settings(tun=False, system_proxy=False))
        assert core.recheck_routes() == ["automatic"]
        if strategy != "load-balance":
            assert core.groups()[0]["current"] == "working"
        core.check_connectivity()
    finally:
        core.stop()
        server.shutdown()
        server.server_close()
