import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nodera.bundled_core import bundled_location
from nodera.core import Core
from nodera.models import ClientError, Profile, Settings
from nodera.route_health import RouteHealth, probe_name, quarantine_config


def samples(a=0, b=10):
    return {"a": {"value": a}, "b": {"value": b}}


def test_freeze_needs_three_failures_and_healthy_alternative_and_recovers_immediately():
    health = RouteHealth()
    health.update(samples(), 0)
    assert not health.frozen
    health.update(samples(), 10)
    assert not health.frozen
    health.update(samples(), 20)
    assert health.frozen == {"a": 35}
    health.update(samples(10), 21)
    assert not health.frozen
    health.update(samples(), 22)
    health.update(samples(), 23)
    assert not health.frozen  # The recovery cleared the failure streak.
    health.update(samples(0, 0), 200)
    health.update(samples(0, 0), 210)
    health.update(samples(0, 0), 215)
    assert not health.frozen and not health.failures
    health.update(samples(), 220)
    assert not health.frozen


def test_duplicate_and_stale_probe_results_cannot_freeze_or_refreeze_a_route():
    health = RouteHealth()

    def batch(a, stamp):
        return {"a": {"value": a, "checked_at": stamp}, "b": {"value": 10, "checked_at": stamp}}

    health.update(batch(0, 100), 0)
    health.update(batch(0, 100), 1)
    health.update(batch(0, 100), 2)
    assert health.failures["a"] == 1 and not health.frozen
    health.update(batch(0, 101), 3)
    health.update(batch(0, 102), 4)
    assert "a" in health.frozen
    health.update(batch(10, 103), 5)
    assert not health.frozen
    health.update(batch(0, 102), 6)
    assert not health.frozen and health.failures["a"] == 0


def test_success_before_next_probe_time_recovers_last_healthy_route_during_outage():
    health = RouteHealth()
    for now in range(3):
        health.update(samples(), now)
    assert "a" in health.frozen
    health.update(samples(10, 0), 3)
    assert not health.frozen
    assert health.failures["b"] == 1
    health.update(samples(0, 0), 4)
    assert not health.frozen and not health.failures


def test_quarantine_blocks_outbound_but_retains_isolated_probe_and_does_not_mutate_source():
    source = {
        "proxies": [{"name": "a", "type": "http", "server": "127.0.0.1", "port": 1}],
        "proxy-groups": [{"name": "auto", "type": "fallback", "include-all": True}],
    }
    updated = quarantine_config(source, {"a"})
    assert source["proxies"][0]["type"] == "http"
    assert updated["proxies"][0] == {"name": "a", "type": "reject"}
    assert updated["proxies"][1]["name"] == probe_name("a")
    assert updated["proxy-groups"][0]["exclude-filter"]
    assert quarantine_config(source, {}) == source


def test_failed_apply_does_not_publish_unenforced_freeze(tmp_path, monkeypatch):
    core = Core(tmp_path)
    monkeypatch.setattr(type(core), "running", property(lambda _: True))

    def fail(previous):
        raise ClientError("reload failed")

    monkeypatch.setattr(core, "_reload_quarantine", fail)
    core.update_route_health(samples())
    core.update_route_health(samples())
    with pytest.raises(ClientError):
        core.update_route_health(samples())
    assert core.route_health.frozen == core.frozen_routes == {}


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_real_core_quarantine_failover_probe_and_recovery(tmp_path, monkeypatch):
    enabled = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            if not enabled.is_set():
                self.send_error(503)
                return
            self.send_response(200, "Connection established")
            self.end_headers()
            self.connection.settimeout(3)
            self.rfile.readline()
            while self.rfile.readline().strip():
                pass
            self.wfile.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
            self.wfile.flush()

        def do_HEAD(self):
            if self.path == "/target" or enabled.is_set():
                self.send_response(204)
                self.end_headers()
            else:
                self.connection.close()

        def do_GET(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}/target"
    monkeypatch.setattr("nodera.core.CONNECTIVITY_URL", target)
    monkeypatch.setattr("nodera.controller.TEST_URL", target)
    profile = Profile(
        "test",
        "",
        {
            "proxies": [
                {"name": "a", "type": "http", "server": "127.0.0.1", "port": server.server_port},
                {"name": "b", "type": "direct"},
            ],
            "proxy-groups": [
                {"name": "auto", "type": "fallback", "proxies": ["a", "b"], "url": target}
            ],
            "rules": ["MATCH,auto"],
        },
        "",
    )
    core = Core(tmp_path / "runtime")
    try:
        core.start(profile, Settings(tun=False, system_proxy=False))
        with pytest.raises(ClientError):
            core.delay("a")
        assert core.delay("b") > 0
        core.update_route_health(samples())
        assert not core.frozen_routes
        core.update_route_health(samples())
        assert not core.frozen_routes
        core.update_route_health(samples())
        assert "a" in core.frozen_routes
        group = core.groups()[0]
        assert group["current"] == "b"
        assert [member["name"] for member in group["members"]] == ["a", "b"]
        assert group["members"][0]["type"] == "Reject"
        with pytest.raises(ClientError, match="заморожен"):
            core.force_server("a")
        core.check_connectivity()
        enabled.set()
        assert core.delay("a") > 0  # probes the original, while normal outbound is Reject
        assert not core.frozen_routes
        assert core.groups()[0]["members"][0]["type"] == "Http"
        core.force_server("a")
        core.check_connectivity()
    finally:
        core.stop()
        server.shutdown()
        server.server_close()
