import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from nodera.bundled_core import bundled_location
from nodera.core import Core
from nodera.flow_monitor import FlowMonitor
from nodera.models import Profile, Settings


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_real_core_exposes_client_process_domain_and_traffic(tmp_path):
    entered, release = threading.Event(), threading.Event()
    body = b"Nodera flow capture " * 100

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[:20])
            self.wfile.flush()
            entered.set()
            if release.wait(8):
                self.wfile.write(body[20:])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    core = Core(tmp_path / "runtime")
    profile = Profile(
        "Flow test",
        "",
        {
            "proxies": [{"name": "local", "type": "direct"}],
            "hosts": {"flow-capture.invalid": "127.0.0.1"},
            "rules": ["MATCH,DIRECT"],
        },
        "",
    )
    result, errors = [], []

    def request():
        try:
            with httpx.Client(
                proxy=f"http://127.0.0.1:{core.mixed_port}", trust_env=False, timeout=12
            ) as client:
                result.append(
                    client.get(f"http://flow-capture.invalid:{server.server_port}/").content
                )
        except Exception as exc:
            errors.append(exc)

    requesting = None
    try:
        core.start(profile, Settings(tun=False, system_proxy=False))
        requesting = threading.Thread(target=request, daemon=True)
        requesting.start()
        assert entered.wait(8), errors
        snapshot = core.status()
        monitor = FlowMonitor()
        monitor.update(snapshot["flows"])
        captured = next(row for row in monitor.rows() if row["host"] == "flow-capture.invalid")
        assert captured["process"].lower() in {"python.exe", "pythonw.exe"}
        assert captured["path"]
        assert captured["network"] == "TCP"
        assert captured["download"] > 0
        assert "DIRECT" in captured["route"]
        release.set()
        requesting.join(timeout=5)
        assert not requesting.is_alive() and not errors
        assert result == [body]
    finally:
        release.set()
        if requesting:
            requesting.join(timeout=3)
        core.stop()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
