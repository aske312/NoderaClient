"""Loopback acceptance with real Mihomo and a Shadowsocks TCP/UDP server."""

import copy
import os
import socket
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest
import yaml

from nodera import channel_selection as channels
from nodera.bundled_core import bundled_location, resolve_core
from nodera.core import CREATE_FLAGS, free_port
from nodera.models import Profile
from tests.loopback import ThreadingHTTPServer


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows Mihomo")
def test_real_shadowsocks_sustained_transfer_udp_and_failed_route(tmp_path, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = b"x" * channels.TRANSFER_BYTES if self.path == "/data" else b""
            self.send_response(200 if payload else 204)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http_thread = threading.Thread(target=http.serve_forever, daemon=True)
    http_thread.start()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.2)
    dns_address = udp.getsockname()
    stopped = threading.Event()

    def dns_server():
        while not stopped.is_set():
            try:
                query, address = udp.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            answer = (
                query[:2]
                + struct.pack("!5H", 0x8180, 1, 1, 0, 0)
                + query[12:]
                + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x5d\xb8\xd8\x22"
            )
            udp.sendto(answer, address)

    dns_thread = threading.Thread(target=dns_server, daemon=True)
    dns_thread.start()
    port = free_port()
    backend_root = tmp_path / "backend"
    backend_root.mkdir()
    backend_config = backend_root / "config.yaml"
    backend_config.write_text(
        yaml.safe_dump(
            {
                "mode": "direct",
                "log-level": "silent",
                "allow-lan": False,
                "listeners": [
                    {
                        "name": "test-ss",
                        "type": "shadowsocks",
                        "listen": "127.0.0.1",
                        "port": port,
                        "cipher": "aes-128-gcm",
                        "password": "local-test",
                        "udp": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [str(resolve_core()), "-d", str(backend_root), "-f", str(backend_config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_FLAGS,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                assert time.monotonic() < deadline and process.poll() is None
                time.sleep(0.05)
        config = {
            "proxies": [
                {
                    "name": "working",
                    "type": "ss",
                    "server": "127.0.0.1",
                    "port": port,
                    "cipher": "aes-128-gcm",
                    "password": "local-test",
                    "udp": True,
                },
                {
                    "name": "unreachable",
                    "type": "ss",
                    "server": "127.0.0.1",
                    "port": free_port(),
                    "cipher": "aes-128-gcm",
                    "password": "local-test",
                    "udp": True,
                },
            ],
            "proxy-groups": [
                {"name": "routes", "type": "select", "proxies": ["working", "unreachable"]}
            ],
            "rules": ["MATCH,routes"],
        }
        p = Profile("Test", "https://312node.net/s/test", copy.deepcopy(config), "")
        before = copy.deepcopy(p)
        monkeypatch.setattr(channels, "request_candidates", lambda *a, **k: (config, "v1"))
        monkeypatch.setattr(channels, "physical_interface", lambda **_: (1, "127.0.0.1", None))
        monkeypatch.setattr(
            "nodera.core.CONNECTIVITY_URL", f"http://127.0.0.1:{http.server_port}/204"
        )
        monkeypatch.setattr(channels, "TRANSFER_URL", f"http://127.0.0.1:{http.server_port}/data")
        original_udp_probe = channels.udp_probe
        monkeypatch.setattr(
            channels, "udp_probe", lambda mixed: original_udp_probe(mixed, destination=dns_address)
        )
        started = time.monotonic()
        result = channels.select_channels(
            p, "test-pc", tmp_path / "checks", duration=30, require_udp=True
        )
        assert time.monotonic() - started >= 30
        assert p == before
        assert [row["state"] for row in result.rows] == ["passed", "failed"]
        assert result.rows[0]["udp"] and result.rows[0]["transfers"] >= 3
        assert result.rows[0]["bytes"] >= 3 * channels.TRANSFER_BYTES
        assert [node["name"] for node in result.profile.config["proxies"]] == ["working"]
        assert not list((tmp_path / "checks").iterdir())
    finally:
        process.terminate()
        process.wait(timeout=10)
        stopped.set()
        udp.close()
        dns_thread.join(timeout=2)
        http.shutdown()
        http.server_close()
        http_thread.join(timeout=2)
