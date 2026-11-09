"""Offline traffic tests: client -> Mihomo -> proxy server -> HTTPS origin.

The certificate and private key in fixtures are public test credentials only.
No Internet, Windows proxy changes, or TUN routes are used by these tests.
"""

import os
import select
import socket
import socketserver
import ssl
import struct
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import httpx
import pytest
import yaml

from nodera.bundled_core import bundled_location
from nodera.core import CREATE_FLAGS, Core, runtime_config
from nodera.geodata import prepare_session_geodata
from nodera.models import ClientError, Profile, Settings
from tests.loopback import ThreadingHTTPServer

FIXTURES = Path(__file__).parent / "fixtures"


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class Origin(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class Gateway(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        method, destination, _ = self.rfile.readline().decode().strip().split()
        if method != "CONNECT":
            return
        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
            pass
        host, port = destination.rsplit(":", 1)
        if host != "127.0.0.1" or int(port) != self.server.origin_port:
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        with socket.create_connection((host, int(port)), timeout=5) as upstream:
            self.server.connections += 1
            self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self.wfile.flush()
            peers = [self.connection, upstream]
            while True:
                readable, _, _ = select.select(peers, [], [], 5)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = upstream if source is self.connection else self.connection
                    target.sendall(data)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_https_upload_download_reaches_proxy_server_and_recovers_after_bad_selection(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "nodera.core.is_admin", lambda: pytest.fail("Proxy requires no admin check")
    )
    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(FIXTURES / "localhost-cert.pem", FIXTURES / "localhost-key.pem")
    origin.socket = tls.wrap_socket(origin.socket, server_side=True)
    gateway = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Gateway)
    gateway.daemon_threads = True
    gateway.origin_port, gateway.connections = origin.server_port, 0
    core = Core(tmp_path)
    with serving(origin), serving(gateway), socket.socket() as offline:
        offline.bind(("127.0.0.1", 0))
        nodes = [
            {
                "name": "Gateway",
                "type": "http",
                "server": "127.0.0.1",
                "port": gateway.server_address[1],
            },
            {
                "name": "Offline",
                "type": "http",
                "server": "127.0.0.1",
                "port": offline.getsockname()[1],
            },
        ]
        profile = Profile(
            "Local network",
            "",
            {
                "proxies": nodes,
                "tun": {"enable": True},
                "proxy-groups": [
                    {"name": "Choice", "type": "select", "proxies": ["Offline", "Gateway"]}
                ],
                "rules": ["MATCH,Choice"],
            },
            "",
            selections={"Choice": "Gateway"},
        )
        runtime = None
        try:
            core.start(profile, Settings(tun=False, system_proxy=False))
            runtime = core.runtime
            assert core.groups()[0]["current"] == "Gateway"
            trust = ssl.create_default_context(cafile=str(FIXTURES / "localhost-cert.pem"))
            url = f"https://127.0.0.1:{origin.server_port}/echo"
            payload = bytes(range(256)) * 8192

            def exchange():
                with httpx.Client(
                    proxy=f"http://127.0.0.1:{core.mixed_port}",
                    verify=trust,
                    trust_env=False,
                    timeout=8,
                ) as client:
                    response = client.post(url, content=payload)
                    response.raise_for_status()
                    assert response.content == payload

            exchange()
            assert gateway.connections == 1
            counters = core.status()
            assert counters["download"] >= len(payload)
            assert counters["upload"] >= len(payload)
            core.select("Choice", "Offline")
            with pytest.raises(httpx.HTTPError):
                exchange()
            assert gateway.connections == 1
            core.select("Choice", "Gateway")
            exchange()
            assert gateway.connections == 2
        finally:
            core.stop()
        assert not core.running and runtime is not None and not runtime.exists()


@pytest.mark.parametrize("dns", [None, {"enable": False}, {"enable": True, "nameserver": []}])
def test_vpn_has_routes_and_dns_even_when_subscription_does_not(dns):
    config = {"tun": {"enable": False, "auto-route": False, "dns-hijack": []}}
    if dns is not None:
        config["dns"] = dns
    generated = runtime_config(config, Settings(), 50000, "test")
    assert generated["tun"]["enable"] is True
    assert generated["tun"]["device"] == "Nodera"
    assert generated["tun"]["auto-route"] is True
    assert generated["tun"]["auto-detect-interface"] is True
    assert generated["tun"]["dns-hijack"] == ["any:53"]
    assert generated["dns"]["enable"] is True
    assert generated["dns"]["nameserver"]
    assert generated["dns"]["default-nameserver"]
    assert config["tun"]["enable"] is False


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_generated_vpn_config_is_accepted_without_starting_a_tunnel(tmp_path):
    config = runtime_config(
        {
            "tun": {"enable": False},
            "dns": {"enable": False},
            "proxies": [{"name": "Local", "type": "direct"}],
            "rules": ["MATCH,DIRECT"],
        },
        Settings(),
        50000,
        "local-test-secret",
    )
    prepare_session_geodata(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    result = subprocess.run(
        [str(bundled_location()), "-d", str(tmp_path), "-f", str(path), "-t"],
        capture_output=True,
        timeout=10,
        creationflags=CREATE_FLAGS,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


class DatagramEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data, listener = self.request
        listener.sendto(data, self.client_address)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_udp_upload_and_download_through_mixed_proxy(tmp_path):
    origin = socketserver.ThreadingUDPServer(("127.0.0.1", 0), DatagramEcho)
    core = Core(tmp_path)
    profile = Profile(
        "UDP test",
        "",
        {"proxies": [{"name": "Local", "type": "direct"}], "rules": ["MATCH,DIRECT"]},
        "",
    )

    def exact(stream, count):
        value = b""
        while len(value) < count:
            chunk = stream.recv(count - len(value))
            assert chunk, "SOCKS connection closed"
            value += chunk
        return value

    with serving(origin), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
        datagram.bind(("127.0.0.1", 0))
        datagram.settimeout(4)
        try:
            core.start(profile, Settings(tun=False, system_proxy=False))
            with socket.create_connection(("127.0.0.1", core.mixed_port), timeout=4) as control:
                control.sendall(b"\x05\x01\x00")
                assert exact(control, 2) == b"\x05\x00"
                control.sendall(
                    b"\x05\x03\x00\x01"
                    + socket.inet_aton("127.0.0.1")
                    + struct.pack("!H", datagram.getsockname()[1])
                )
                reply = exact(control, 4)
                assert reply == b"\x05\x00\x00\x01"
                address = socket.inet_ntoa(exact(control, 4))
                port = struct.unpack("!H", exact(control, 2))[0]
                destination = (
                    b"\x00\x00\x00\x01"
                    + socket.inet_aton("127.0.0.1")
                    + struct.pack("!H", origin.server_address[1])
                )
                # Stay below the pinned core's 8/16 KiB UDP receive buffers.
                for length in (1, 1350, 4096, 8000):
                    payload = os.urandom(length)
                    datagram.sendto(destination + payload, (address, port))
                    try:
                        response, _ = datagram.recvfrom(65536)
                    except TimeoutError:
                        pytest.fail(f"UDP echo timed out for {length} bytes via {address}:{port}")
                    assert response == destination + payload
        finally:
            core.stop()
        assert not core.running and core.runtime is None


class SlowOrigin(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.entered.set()
        if self.server.release.wait(4):
            try:
                self.send_response(204)
                self.end_headers()
            except (OSError, ssl.SSLError):
                pass

    def log_message(self, *args):
        pass


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
@pytest.mark.parametrize("expire", [False, True])
def test_late_https_response_and_probe_timeout_do_not_stop_core(tmp_path, monkeypatch, expire):
    origin = ThreadingHTTPServer(("127.0.0.1", 0), SlowOrigin)
    origin.entered, origin.release = threading.Event(), threading.Event()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(FIXTURES / "localhost-cert.pem", FIXTURES / "localhost-key.pem")
    origin.socket = tls.wrap_socket(origin.socket, server_side=True)
    trust = ssl.create_default_context(cafile=str(FIXTURES / "localhost-cert.pem"))
    original_client = httpx.Client

    def client(**kwargs):
        if "proxy" in kwargs:
            kwargs["verify"] = trust
            kwargs["timeout"] = 0.15 if expire else 2
        return original_client(**kwargs)

    monkeypatch.setattr("nodera.core.httpx.Client", client)
    monkeypatch.setattr("nodera.core.CONNECTIVITY_URL", f"https://127.0.0.1:{origin.server_port}/")
    profile = Profile(
        "Delayed reply",
        "",
        {"proxies": [{"name": "Local", "type": "direct"}], "rules": ["MATCH,DIRECT"]},
        "",
    )
    core = Core(tmp_path / "runtime")
    with serving(origin), ThreadPoolExecutor(max_workers=1) as executor:
        try:
            core.start(profile, Settings(tun=False, system_proxy=False))
            pid, port = core.process.pid, core.mixed_port
            future = executor.submit(core.check_connectivity)
            assert origin.entered.wait(2)
            if expire:
                with pytest.raises(ClientError):
                    future.result(timeout=3)
            else:
                assert not future.done()
            assert core.running and core.process.pid == pid and core.mixed_port == port
            origin.release.set()
            if not expire:
                future.result(timeout=3)
            with original_client(
                proxy=f"http://127.0.0.1:{port}", verify=trust, trust_env=False, timeout=2
            ) as connection:
                assert connection.get(f"https://127.0.0.1:{origin.server_port}/").status_code == 204
            assert core.running
            trace = core.trace.path.read_text(encoding="utf-8")
            assert "https_probe_started" in trace
            assert f"event=process_terminate_sent pid={pid}" not in trace
            if expire:
                assert "https_probe_failed" in trace and "core_running=True" in trace
        finally:
            origin.release.set()
            core.stop("test finished")
        assert "reason=test finished" in core.trace.path.read_text(encoding="utf-8")
