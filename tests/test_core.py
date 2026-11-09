import os
import socket
import threading
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from nodera.bundled_core import bundled_location
from nodera.core import Core, PortConflict, free_port, ready_version, runtime_config
from nodera.models import ClientError, Profile, Settings
from nodera.system_proxy import SystemProxy
from tests.loopback import ThreadingHTTPServer


def test_ui_log_overflow_does_not_drop_persistent_network_records(tmp_path):
    from collections import deque

    core = Core(tmp_path / "runtime")
    core._logs = deque(maxlen=2)
    core._session_secrets = ("sensitive-value",)
    for index in range(5):
        core._record(f"record={index} password=sensitive-value")
    text = core.trace.path.read_text(encoding="utf-8")
    assert all(f"record={index}" in text for index in range(5))
    assert "sensitive-value" not in text
    messages = core.drain_logs()
    assert messages[0][0] == "warning"
    assert "network.jsonl" in messages[0][1]


@pytest.mark.parametrize("status", [204, 200, 302, 503])
def test_network_status_requires_real_https_success(tmp_path, monkeypatch, status):
    core = Core(tmp_path)
    core.mixed_port = 54321
    monkeypatch.setattr(Core, "running", property(lambda _: True))
    original_client = httpx.Client
    calls = []

    def client(**kwargs):
        calls.append(kwargs)
        return original_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(status))
        )

    monkeypatch.setattr("nodera.core.httpx.Client", client)
    if status == 204:
        core.check_connectivity()
    else:
        with pytest.raises(ClientError):
            core.check_connectivity()
    assert calls[0]["proxy"] == "http://127.0.0.1:54321"
    assert calls[0]["trust_env"] is False


def test_vpn_without_admin_fails_before_starting_core_or_creating_session(tmp_path, monkeypatch):
    core = Core(tmp_path, core_resolver=lambda: tmp_path / "mihomo.exe")
    monkeypatch.setattr("nodera.core.is_admin", lambda: False)
    with pytest.raises(ClientError, match="права"):
        core.start(Profile("P", "", {"tun": {"enable": False}}, ""), Settings())
    assert core.runtime is None and core.process is None


def test_controller_does_not_mean_tun_is_ready_but_later_readiness_is_accepted():
    states = iter([False, False, True])

    def response(request):
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "test"})
        return httpx.Response(200, json={"tun": {"enable": next(states)}})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(response)
    ) as client:
        assert ready_version(client, require_tun=True) is None
        assert ready_version(client, require_tun=True) is None
        assert ready_version(client, require_tun=True) == "test"


def test_proxy_readiness_does_not_require_tun():
    def response(request):
        assert request.url.path == "/version"
        return httpx.Response(200, json={"version": "test"})

    with httpx.Client(
        base_url="http://localhost", transport=httpx.MockTransport(response)
    ) as client:
        assert ready_version(client, require_tun=False) == "test"


def test_runtime_owns_listeners_preserves_routing():
    config = {
        "mixed-port": 9000,
        "allow-lan": True,
        "external-controller": "0.0.0.0:9001",
        "external-controller-unix": "bad.sock",
        "external-ui": "C:/private",
        "secret": "remote",
        "listeners": [{"port": 80}],
        "tun": {"enable": True, "stack": "mixed", "dns-hijack": ["any:53"]},
        "dns": {
            "enable": True,
            "listen": "0.0.0.0:53",
            "nameserver": ["https://dns.example/dns-query"],
        },
        "rules": ["MATCH,PRO"],
        "proxy-groups": [{"name": "PRO", "type": "select", "proxies": ["node"]}],
        "rule-providers": {
            "test": {"type": "http", "path": "../../overwrite", "url": "https://example.com/rules"}
        },
    }
    result = runtime_config(config, Settings(tun=False, mixed_port=7890), 50000, "local-secret")
    assert result["mixed-port"] == 7890
    assert result["allow-lan"] is False
    assert result["external-controller"] == "127.0.0.1:50000"
    assert result["secret"] == "local-secret"
    assert result["find-process-mode"] == "always"
    assert "listeners" not in result and "external-ui" not in result
    assert "external-controller-unix" not in result
    assert "listen" not in result["dns"]
    assert result["tun"]["enable"] is False
    assert result["rules"] == config["rules"]
    assert result["rule-providers"]["test"]["path"].startswith("./providers/")
    assert config["rule-providers"]["test"]["path"] == "../../overwrite"


def test_missing_core_is_actionable(tmp_path):
    def missing():
        raise ClientError("Встроенное ядро отсутствует. Переустановите Nodera.")

    with pytest.raises(ClientError, match="Переустановите"):
        Core(tmp_path, core_resolver=missing).start(Profile("P", "", {}, ""), Settings())


def test_runtime_cleanup(tmp_path):
    core = Core(tmp_path)
    core.runtime = tmp_path / "session-test"
    core.runtime.mkdir()
    (core.runtime / "config.yaml").write_text("private")
    core.stop()
    assert not (tmp_path / "session-test").exists()
    core.stop()


def test_session_rate_and_counter_reset(tmp_path, monkeypatch):
    core = Core(tmp_path)
    core.started_at = 5
    responses = iter([(1024, 1024), (5120, 2048), (4, 1)])
    times = iter([10.0, 12.0, 14.0])
    monkeypatch.setattr(Core, "running", property(lambda _: True))
    monkeypatch.setattr("nodera.core.time.monotonic", lambda: next(times))

    def handle(_):
        down, up = next(responses)
        return httpx.Response(
            200, json={"downloadTotal": down, "uploadTotal": up, "connections": []}
        )

    monkeypatch.setattr(
        core,
        "_client",
        lambda: httpx.Client(base_url="http://localhost", transport=httpx.MockTransport(handle)),
    )
    assert core.status()["download_rate"] == 0
    data = core.status()
    assert data["download_rate"] == 2048
    assert data["upload_rate"] == 512
    assert data["uptime"] == 7
    assert core.status()["download_rate"] == 0


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_busy_proxy_port_is_replaced_automatically(tmp_path):
    core = Core(tmp_path / "runtime")
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        preferred = occupied.getsockname()[1]
        settings = Settings(tun=False, mixed_port=preferred, system_proxy=False)
        profile = Profile(
            "P", "", {"proxies": [{"name": "A", "type": "direct"}], "rules": ["MATCH,DIRECT"]}, ""
        )
        try:
            assert core.start(profile, settings)
            assert core.mixed_port not in (0, preferred)
            with socket.create_connection(("127.0.0.1", core.mixed_port), timeout=2):
                pass
            assert occupied.fileno() != -1
        finally:
            core.stop()
    assert core.process is None
    assert core.runtime is None


def test_bind_race_is_retried_without_changing_preferred_settings(tmp_path, monkeypatch):
    core = Core(tmp_path / "runtime", core_resolver=lambda: tmp_path / "unused.exe")
    settings = Settings(tun=False, mixed_port=7890)
    requests = []
    starts = []

    def pick(preferred):
        requests.append(preferred)
        return 50000 + len(requests), 51000 + len(requests)

    def start(profile, local_settings, binary):
        starts.append(local_settings.mixed_port)
        if len(starts) < 3:
            raise PortConflict()
        return "test"

    monkeypatch.setattr("nodera.core.choose_ports", pick)
    monkeypatch.setattr(core, "_start", start)
    assert core.start(Profile("P", "", {}, ""), settings) == "test"
    assert requests == [7890, 0, 0]
    assert starts == [50001, 50002, 50003]
    assert settings.mixed_port == 7890


def test_bind_race_retry_is_bounded(tmp_path, monkeypatch):
    core = Core(tmp_path / "runtime", core_resolver=lambda: tmp_path / "unused.exe")
    calls = []

    def fail(*args):
        calls.append(True)
        raise PortConflict()

    monkeypatch.setattr(core, "_start", fail)
    with pytest.raises(ClientError, match="автоматически"):
        core.start(Profile("P", "", {}, ""), Settings(tun=False))
    assert len(calls) == 3


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_ownership_conflict_is_retried_with_real_core(tmp_path, monkeypatch):
    from nodera.ports import listener_owners

    core = Core(tmp_path / "runtime")
    injected = []

    def ownership(*ports):
        result = listener_owners(*ports)
        if (
            not injected
            and core.process
            and any(core.process.pid in pids for pids in result.values())
        ):
            injected.append(core.process.pid)
            return {port: {0xFFFFFFFF} for port in ports}
        return result

    monkeypatch.setattr("nodera.core.listener_owners", ownership)
    profile = Profile(
        "P", "", {"proxies": [{"name": "A", "type": "direct"}], "rules": ["MATCH,DIRECT"]}, ""
    )
    try:
        assert core.start(profile, Settings(tun=False, system_proxy=False))
        assert injected and core.process.pid != injected[0]
        assert core.process.pid in listener_owners(core.mixed_port)[core.mixed_port]
        assert len(list((tmp_path / "runtime").glob("session-*"))) == 1
    finally:
        core.stop()


def test_validation_failure_removes_plaintext(tmp_path, monkeypatch):
    import subprocess

    binary = tmp_path / "mihomo.exe"
    binary.touch()
    original_run = subprocess.run

    def reject_config(command, **kwargs):
        if command[0] == str(binary.resolve()):
            return subprocess.CompletedProcess(command, 1)
        return original_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", reject_config)
    core = Core(tmp_path / "runtime", core_resolver=lambda: binary)
    settings = Settings(tun=False, mixed_port=free_port(), system_proxy=False)
    with pytest.raises(ClientError, match="отклонило"):
        core.start(Profile("P", "", {}, ""), settings)
    assert core.process is None
    assert core.runtime is None
    assert list((tmp_path / "runtime").glob("session-*")) == []


@pytest.mark.skipif(
    os.name != "nt" or not bundled_location().is_file(),
    reason="Run scripts/prepare_core.py on Windows",
)
def test_real_core_lifecycle(tmp_path):
    core = Core(tmp_path)
    settings = Settings(tun=False, mixed_port=free_port(), system_proxy=False)
    profile = Profile(
        "test",
        "",
        {"proxies": [{"name": "local", "type": "direct"}], "rules": ["MATCH,DIRECT"]},
        "",
    )
    try:
        assert core.start(profile, settings)
        assert core.running
        with socket.create_connection(("127.0.0.1", core.mixed_port), timeout=2):
            pass
        assert core.status()["download"] >= 0
        core.set_log_level("debug")
        core.flush_dns()
        runtime = core.runtime
    finally:
        core.stop()
    assert not core.running
    assert not runtime.exists()
    assert core.drain_logs()


@pytest.mark.skipif(
    os.name != "nt" or not bundled_location().is_file(),
    reason="Run scripts/prepare_core.py on Windows",
)
def test_real_selection_restore_and_delay(tmp_path, monkeypatch):
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        "nodera.controller.TEST_URL", f"http://127.0.0.1:{server.server_port}/generate_204"
    )
    core = Core(tmp_path)
    settings = Settings(tun=False, mixed_port=free_port(), system_proxy=False)
    group = "PRO / Выбор?#"
    profile = Profile(
        "test",
        "",
        {
            "proxies": [{"name": "A", "type": "direct"}, {"name": "B", "type": "direct"}],
            "proxy-groups": [{"name": group, "type": "select", "proxies": ["A", "B"]}],
            "rules": [f"MATCH,{group}"],
        },
        "",
        selections={group: "B", "retired-group": "old-node"},
    )
    try:
        core.start(profile, settings)
        assert core.groups()[0]["current"] == "B"
        assert core.select(group, "A")[0]["current"] == "A"
        assert core.delay("A") > 0
        assert core.groups()[0]["current"] == "A"
        assert core.status()["uptime"] >= 0
    finally:
        core.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
@pytest.mark.parametrize("crash", [False, True])
def test_http_through_real_core_and_proxy_recovery(tmp_path, crash):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"Nodera proxy works"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class FakeWindowsProxy:
        values = {"flags": 9, "server": "", "bypass": "", "pac": ""}

        def read(self):
            return dict(self.values)

        def write(self, values):
            self.values = dict(values)

    backend = FakeWindowsProxy()
    original = backend.read()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    core = Core(tmp_path / "runtime")
    core.system_proxy = SystemProxy(tmp_path, backend, lambda _: None)
    settings = Settings(tun=False, mixed_port=0)
    profile = Profile(
        "test",
        "",
        {
            "proxies": [{"name": "Local test", "type": "direct"}],
            "proxy-groups": [{"name": "PRO", "type": "select", "proxies": ["Local test"]}],
            "rules": ["MATCH,PRO"],
        },
        "",
    )
    try:
        core.start(profile, settings)
        assert core.mixed_port > 0
        assert backend.values["server"] == f"127.0.0.1:{core.mixed_port}"
        with httpx.Client(proxy=f"http://127.0.0.1:{core.mixed_port}", trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{server.server_port}/")
            assert response.status_code == 200
            assert response.content == b"Nodera proxy works"
        if crash:
            core.process.kill()
            core.process.wait(timeout=3)
            with pytest.raises(ClientError, match="остановилось"):
                core.status()
        else:
            core.stop()
        assert backend.read() == original
        assert not core.system_proxy.pending
        assert core.runtime is None
    finally:
        core.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_live_subscription_applies_rules_preserves_tcp_and_rolls_back(tmp_path, monkeypatch):
    from copy import deepcopy
    from dataclasses import replace

    from nodera.controller import Controller

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    core = Core(tmp_path / "runtime")
    settings = Settings(tun=False, system_proxy=False)
    profile = Profile(
        "test",
        "",
        {
            "proxies": [{"name": "A", "type": "direct"}, {"name": "B", "type": "direct"}],
            "proxy-groups": [{"name": "Choice", "type": "select", "proxies": ["A", "B"]}],
            "rules": ["MATCH,Choice"],
        },
        "",
        selections={"Choice": "B"},
    )
    tunnel = None
    try:
        core.start(profile, settings)
        pid, port, started = core.process.pid, core.mixed_port, core.started_at
        tunnel = socket.create_connection(("127.0.0.1", port), timeout=3)
        tunnel.sendall(
            f"CONNECT 127.0.0.1:{server.server_port} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
        )
        response = tunnel.recv(4096)
        assert b"200" in response
        config = deepcopy(profile.config)
        config["proxies"].append({"name": "C", "type": "direct"})
        config["proxy-groups"][0]["proxies"].append("C")
        config["rules"] = ["MATCH,REJECT"]
        updated = replace(profile, config=config)
        core.apply_profile(updated)
        assert core.process.pid == pid and core.mixed_port == port and core.started_at == started
        assert core.groups()[0]["current"] == "B"
        assert "C" in core.node_names
        tunnel.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        assert b"200" in tunnel.recv(4096)  # Existing tunnel survives a change to REJECT.
        with httpx.Client(proxy=f"http://127.0.0.1:{port}", trust_env=False, timeout=3) as client:
            try:
                rejected = client.get(f"http://127.0.0.1:{server.server_port}/")
                assert rejected.status_code != 200
            except httpx.HTTPError:
                pass
        with pytest.raises(ClientError, match="отклонило"):
            core.apply_profile(replace(profile, config={**config, "rules": ["MATCH,Missing"]}))
        assert core.running and core.process.pid == pid and "C" in core.node_names
        # Simulate a failure after the kernel accepted the replacement.
        original_groups = Controller.groups
        fail_once = [True]

        def groups(self, *args, **kwargs):
            if fail_once.pop() if fail_once else False:
                raise ClientError("Injected API failure")
            return original_groups(self, *args, **kwargs)

        monkeypatch.setattr(Controller, "groups", groups)
        with pytest.raises(ClientError, match="восстановлена"):
            core.apply_profile(profile)
        assert core.process.pid == pid and "C" in core.node_names
        assert any(item["name"] == "C" for item in core.groups()[0]["members"])
    finally:
        if tunnel:
            tunnel.close()
        core.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
