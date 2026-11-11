import os
import socket
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from nodera.bundled_core import bundled_location
from nodera.core import Core
from nodera.models import ClientError, Profile, Settings
from nodera.route_health import RouteHealth
from tests.loopback import ThreadingHTTPServer


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


def test_health_changes_never_reload_configuration_or_replace_outbounds(tmp_path, monkeypatch):
    core = Core(tmp_path)
    monkeypatch.setattr(type(core), "running", property(lambda _: True))
    calls = []

    class Api:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def recheck_routes(self, *args):
            calls.append("group-check")
            raise ClientError("temporary probe failure")

        def request(self, *args, **kwargs):
            pytest.fail("Health observations must not mutate core configuration")

    monkeypatch.setattr("nodera.core.Controller", Api)
    for _ in range(3):
        core.update_route_health(samples())
    assert "a" in core.frozen_routes
    assert core.probe_aliases() == {}
    core.update_route_health(samples(10))
    assert not core.frozen_routes
    assert calls == ["group-check", "group-check"]


def test_forced_route_is_not_disrupted_by_failed_background_probe(tmp_path, monkeypatch):
    core = Core(tmp_path)
    monkeypatch.setattr(type(core), "running", property(lambda _: True))
    core.forced_server = "a"
    monkeypatch.setattr(
        "nodera.core.Controller", lambda *a: pytest.fail("Pinned route must stay untouched")
    )
    for _ in range(3):
        core.update_route_health(samples())
    assert core.forced_server == "a"
    assert "a" in core.frozen_routes


def test_old_health_batch_cannot_affect_reloaded_session(tmp_path, monkeypatch):
    core = Core(tmp_path)
    monkeypatch.setattr(type(core), "running", property(lambda _: True))
    old = core.route_health
    core.route_health = RouteHealth()
    for _ in range(3):
        core.update_route_health(samples(), expected=old)
    assert not core.route_health.failures and not core.frozen_routes


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_health_flapping_preserves_live_dns_cache_and_healthy_requests(tmp_path):
    class DNS(socketserver.BaseRequestHandler):
        def handle(self):
            query, stream = self.request
            cursor = 12
            while query[cursor]:
                cursor += query[cursor] + 1
            question = query[12 : cursor + 5]
            kind = struct.unpack("!H", query[cursor + 1 : cursor + 3])[0]
            self.server.queries += 1
            answer = kind == 1
            header = query[:2] + struct.pack("!5H", 0x8180, 1, int(answer), 0, 0)
            body = (
                b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 600, 4) + socket.inet_aton("127.0.0.1")
                if answer
                else b""
            )
            stream.sendto(header + question + body, self.client_address)

    class HTTP(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    resolver = socketserver.ThreadingUDPServer(("127.0.0.1", 0), DNS)
    resolver.daemon_threads = True
    resolver.queries = 0
    origin = ThreadingHTTPServer(("127.0.0.1", 0), HTTP)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (resolver, origin)]
    for thread in threads:
        thread.start()
    core = Core(tmp_path / "runtime")
    address = f"127.0.0.1:{resolver.server_address[1]}"
    profile = Profile(
        "health flapping",
        "",
        {
            "ipv6": False,
            "dns": {
                "enable": True,
                "ipv6": False,
                "enhanced-mode": "redir-host",
                "nameserver": [address],
                "default-nameserver": [address],
            },
            "proxies": [
                {"name": "a", "type": "http", "server": "127.0.0.1", "port": 1},
                {"name": "b", "type": "direct"},
            ],
            "rules": ["MATCH,b"],
        },
        "",
    )
    try:
        core.start(profile, Settings(tun=False, system_proxy=False))
        pid = core.process.pid
        with httpx.Client(
            proxy=f"http://127.0.0.1:{core.mixed_port}", trust_env=False, timeout=5
        ) as client:
            url = f"http://health-cache.test:{origin.server_port}/"
            assert client.get(url).status_code == 204
            queries = resolver.queries
            assert queries > 0
            for _ in range(3):
                for _ in range(3):
                    core.update_route_health(samples())
                assert "a" in core.frozen_routes
                assert client.get(url).status_code == 204
                core.update_route_health(samples(10))
                assert not core.frozen_routes
                assert client.get(url).status_code == 204
            assert resolver.queries == queries, "Health flapping discarded the live DNS cache"
            assert core.process.pid == pid
    finally:
        core.stop()
        for server in (resolver, origin):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(2)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_real_core_health_failover_and_recovery_without_config_reload(tmp_path, monkeypatch):
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
        pid = core.process.pid
        core.update_route_health(samples())
        assert not core.frozen_routes
        core.update_route_health(samples())
        assert not core.frozen_routes
        core.update_route_health(samples())
        assert "a" in core.frozen_routes
        group = core.groups()[0]
        assert group["current"] == "b"
        assert [member["name"] for member in group["members"]] == ["a", "b"]
        assert group["members"][0]["type"] == "Http"
        with pytest.raises(ClientError, match="заморожен"):
            core.force_server("a")
        core.check_connectivity()
        assert core.process.pid == pid
        # Reloading logs a new initial configuration/provider set. Freeze and
        # recovery must leave the running DNS resolver and transports intact.
        trace = core.trace.path.read_text(encoding="utf-8")
        assert trace.count("Initial configuration complete") == 1
        enabled.set()
        assert core.delay("a") > 0  # the original outbound remains available to probes
        assert not core.frozen_routes
        assert core.groups()[0]["members"][0]["type"] == "Http"
        core.force_server("a")
        core.check_connectivity()
        assert core.process.pid == pid
        # Reloading logs a new initial configuration/provider set. Freeze and
        # recovery must leave the running DNS resolver and transports intact.
        trace = core.trace.path.read_text(encoding="utf-8")
        assert trace.count("Initial configuration complete") == 1
    finally:
        core.stop()
        server.shutdown()
        server.server_close()
