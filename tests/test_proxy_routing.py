"""Real mixed-proxy traffic and routing, isolated from Windows network settings."""

import http.client
import os
import select
import socket
import socketserver
import ssl
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nodera.bundled_core import bundled_location
from nodera.core import Core
from nodera.models import ClientError, Profile, Settings
from tests.test_traffic import serving

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not bundled_location().is_file(), reason="Windows core"
)
FIXTURES = Path(__file__).parent / "fixtures"
HOSTS = {name + ".test": "127.0.0.1" for name in ("direct", "proxied", "blocked", "other")}


class Rejected(OSError):
    pass


class EchoOrigin(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        payload = self.rfile.read(int(self.headers["Content-Length"]))
        with self.server.counter_lock:
            self.server.received.append(len(payload))
        if self.headers.get("X-Test-Hold") == "1":
            self.server.waiting.set()
            self.server.release.wait(3)
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def log_message(self, *args):
        pass


class RecordingGateway(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        method, destination, _ = self.rfile.readline().decode().strip().split()
        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
            pass
        host, port = destination.rsplit(":", 1)
        # Mihomo may resolve the subscription's hosts mapping before CONNECT.
        target = HOSTS.get(host, host)
        if method != "CONNECT" or target != "127.0.0.1" or int(port) != self.server.origin_port:
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        with socket.create_connection((target, int(port)), timeout=5) as upstream:
            with self.server.counter_lock:
                self.server.destinations.append(host)
            self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self.wfile.flush()
            while True:
                readable, _, _ = select.select([self.connection, upstream], [], [], 5)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = upstream if source is self.connection else self.connection
                    target.sendall(data)


@pytest.fixture
def routing_lab(tmp_path, protocol):
    origin = ThreadingHTTPServer(("127.0.0.1", 0), EchoOrigin)
    origin.daemon_threads = True
    origin.received, origin.counter_lock = [], threading.Lock()
    origin.waiting, origin.release = threading.Event(), threading.Event()
    if protocol != "http-plain":
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(FIXTURES / "localhost-cert.pem", FIXTURES / "localhost-key.pem")
        origin.socket = tls.wrap_socket(origin.socket, server_side=True)
    gateway = socketserver.ThreadingTCPServer(("127.0.0.1", 0), RecordingGateway)
    gateway.daemon_threads = True
    gateway.origin_port = origin.server_port
    gateway.destinations, gateway.counter_lock = [], threading.Lock()
    core = Core(tmp_path / "runtime")
    profile = Profile(
        "Proxy routing",
        "",
        {
            "proxies": [
                {
                    "name": "Gateway",
                    "type": "http",
                    "server": "127.0.0.1",
                    "port": gateway.server_address[1],
                }
            ],
            "proxy-groups": [{"name": "Choice", "type": "select", "proxies": ["Gateway"]}],
            "hosts": HOSTS,
            "rules": [
                "DOMAIN,direct.test,DIRECT",
                "DOMAIN,blocked.test,REJECT",
                "DOMAIN,proxied.test,Choice",
                "MATCH,DIRECT",
            ],
        },
        "",
    )
    with serving(origin), serving(gateway):
        try:
            yield core, profile, origin, gateway
        finally:
            core.stop("proxy routing test finished")


def exact(stream, count):
    result = bytearray()
    while len(result) < count:
        chunk = stream.recv(count - len(result))
        if not chunk:
            raise Rejected("Proxy closed the connection")
        result.extend(chunk)
    return bytes(result)


def exchange(core, origin, host, protocol, payload=b"Routing works", inspect=False):
    if inspect:
        origin.waiting.clear()
        origin.release.clear()
    with socket.create_connection(("127.0.0.1", core.mixed_port), timeout=5) as stream:
        if protocol == "http":
            stream.sendall(
                f"CONNECT {host}:{origin.server_port} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
            )
            response = bytearray()
            while not response.endswith(b"\r\n\r\n"):
                response.extend(exact(stream, 1))
                assert len(response) < 8192
            if response.split(b" ")[1] != b"200":
                raise Rejected("HTTP proxy rejected the destination")
        elif protocol == "socks5":
            stream.sendall(b"\x05\x01\x00")
            assert exact(stream, 2) == b"\x05\x00"
            destination = host.encode()
            stream.sendall(
                b"\x05\x01\x00\x03"
                + bytes([len(destination)])
                + destination
                + struct.pack("!H", origin.server_port)
            )
            reply = exact(stream, 4)
            if reply[1] != 0:
                raise Rejected("SOCKS5 proxy rejected the destination")
            address_length = {1: 4, 4: 16}.get(reply[3])
            if reply[3] == 3:
                address_length = exact(stream, 1)[0]
            assert address_length is not None
            exact(stream, address_length + 2)
        trust = ssl.create_default_context(cafile=str(FIXTURES / "localhost-cert.pem"))
        # The local origin's certificate is for localhost. Routing uses the
        # CONNECT/SOCKS destination, while TLS still validates the test origin.
        connection = (
            nullcontext(stream)
            if protocol == "http-plain"
            else trust.wrap_socket(stream, server_hostname="localhost")
        )
        target = f"http://{host}:{origin.server_port}/echo" if protocol == "http-plain" else "/echo"
        with connection as secure:
            secure.sendall(
                f"POST {target} HTTP/1.1\r\nHost: {host}:{origin.server_port}\r\nContent-Length: {len(payload)}\r\nX-Test-Hold: {int(inspect)}\r\n\r\n".encode()
                + payload
            )
            if inspect:
                try:
                    assert origin.waiting.wait(2), "Request did not reach origin"
                    matching = [
                        row
                        for row in core.connection_snapshot()
                        if row.get("metadata", {}).get("host") == host
                    ]
                    assert matching, "Live connection is absent from Mihomo's snapshot"
                finally:
                    origin.release.set()
            response = http.client.HTTPResponse(secure)
            response.begin()
            if response.status != 200:
                raise Rejected(f"Proxy returned HTTP {response.status}")
            assert response.read() == payload
            if inspect:
                return matching[-1]


def blocked(core, origin, host, protocol):
    with pytest.raises((OSError, http.client.HTTPException)):
        exchange(core, origin, host, protocol)


@pytest.mark.parametrize("protocol", ["http", "socks5", "http-plain"])
def test_proxy_applies_rules_and_local_priority_through_reload_and_reconnect(routing_lab, protocol):
    core, profile, origin, gateway = routing_lab
    settings = Settings(tun=False, system_proxy=False)
    core.start(profile, settings)
    direct = exchange(core, origin, "direct.test", protocol, inspect=True)
    assert direct["rule"] == "Domain" and direct["rulePayload"] == "direct.test"
    assert direct["chains"] == ["DIRECT"]
    assert gateway.destinations == []
    proxied = exchange(core, origin, "proxied.test", protocol, inspect=True)
    assert proxied["rulePayload"] == "proxied.test"
    assert "Gateway" in proxied["chains"] and "Choice" in proxied["chains"]
    assert len(gateway.destinations) == 1
    received = len(origin.received)
    blocked(core, origin, "blocked.test", protocol)
    assert len(origin.received) == received and len(gateway.destinations) == 1
    default = exchange(core, origin, "other.test", protocol, inspect=True)
    assert default["rule"] == "Match" and default["chains"] == ["DIRECT"]

    pid, port = core.process.pid, core.mixed_port
    override = replace(profile, local_rules=["DOMAIN,proxied.test,REJECT"])
    core.apply_profile(override)
    received = len(origin.received)
    blocked(core, origin, "proxied.test", protocol)
    assert len(origin.received) == received and len(gateway.destinations) == 1
    exchange(core, origin, "direct.test", protocol)
    core.apply_profile(profile)
    exchange(core, origin, "proxied.test", protocol)
    assert len(gateway.destinations) == 2

    updated = replace(
        profile,
        config={
            **profile.config,
            "rules": ["DOMAIN,direct.test,REJECT", "DOMAIN,blocked.test,DIRECT", "MATCH,Choice"],
        },
    )
    core.apply_profile(updated)
    assert core.process.pid == pid and core.mixed_port == port
    blocked(core, origin, "direct.test", protocol)
    exchange(core, origin, "blocked.test", protocol)
    exchange(core, origin, "other.test", protocol)
    assert len(gateway.destinations) == 3
    with pytest.raises(ClientError):
        core.apply_profile(replace(profile, config={**profile.config, "rules": ["MATCH,Missing"]}))
    blocked(core, origin, "direct.test", protocol)
    exchange(core, origin, "blocked.test", protocol)
    runtime = core.runtime
    core.stop()
    assert not runtime.exists()
    core.start(updated, settings)
    blocked(core, origin, "direct.test", protocol)
    exchange(core, origin, "blocked.test", protocol)
    exchange(core, origin, "proxied.test", protocol)
    assert core.running


@pytest.mark.parametrize("protocol", ["http", "socks5", "http-plain"])
def test_proxy_concurrent_transfers_keep_routes_and_payloads_across_three_sessions(
    routing_lab, protocol
):
    core, profile, origin, gateway = routing_lab
    settings = Settings(tun=False, system_proxy=False)
    transferred = 0
    for session in range(3):
        core.start(profile, settings)
        expected = len(gateway.destinations) + 12

        def transfer(index):
            payload = bytes([session, index]) * 8192
            host = "proxied.test" if index % 2 else "direct.test"
            exchange(core, origin, host, protocol, payload)
            return len(payload)

        with ThreadPoolExecutor(max_workers=6) as workers:
            transferred += sum(workers.map(transfer, range(24)))
        assert len(gateway.destinations) == expected
        blocked(core, origin, "blocked.test", protocol)
        assert core.running
        runtime = core.runtime
        core.stop()
        assert not core.running and not runtime.exists()
    assert len(origin.received) == 72
    assert sum(origin.received) == transferred == 72 * 16384
