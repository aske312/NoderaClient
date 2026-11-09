import os
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler

import pytest

from nodera.bundled_core import bundled_location
from nodera.controller import Controller
from nodera.core import Core
from nodera.models import ClientError, Profile, Settings
from nodera.provider_cache import RuleCache
from tests.loopback import ThreadingHTTPServer


def setup_cache(tmp_path, profile=None):
    profile = profile or Profile("P", "https://example.test/private", {}, "")
    runtime = tmp_path / "session"
    runtime.mkdir(exist_ok=True)
    config = {
        "rule-providers": {
            "security": {
                "type": "http",
                "url": "https://example.test/private-rule-token",
                "behavior": "domain",
                "format": "text",
                "path": "./providers/rules.cache",
            }
        }
    }
    return profile, config, runtime, RuleCache(tmp_path, profile, config, runtime)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_rule_cache_survives_session_and_preserves_provider_age(tmp_path):
    profile, config, runtime, cache = setup_cache(tmp_path)
    source = runtime / "providers/rules.cache"
    source.parent.mkdir()
    source.write_bytes(b"secret.example.test\n")
    modified = time.time() - 1800
    os.utime(source, (modified, modified))
    cache.capture()
    persisted = next(cache.directory.glob("*.dpapi"))
    assert b"secret.example.test" not in persisted.read_bytes()
    assert "private-rule-token" not in persisted.name
    source.unlink()
    restored = RuleCache(tmp_path, profile, config, runtime)
    restored.restore()
    assert source.read_bytes() == b"secret.example.test\n"
    assert abs(source.stat().st_mtime - modified) < 1
    mtime = persisted.stat().st_mtime_ns
    restored.capture()
    assert persisted.stat().st_mtime_ns == mtime


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_cache_is_isolated_between_profiles_and_provider_urls(tmp_path):
    profile, config, runtime, cache = setup_cache(tmp_path)
    source = runtime / "providers/rules.cache"
    source.parent.mkdir()
    source.write_bytes(b"example.test\n")
    cache.capture()
    source.unlink()
    RuleCache(tmp_path, replace(profile, id="other-profile"), config, runtime).restore()
    assert not source.exists()
    config["rule-providers"]["security"]["url"] = "https://example.test/another-source"
    RuleCache(tmp_path, profile, config, runtime).restore()
    assert not source.exists()


def test_corrupt_rule_cache_falls_back_to_core_fetch(tmp_path):
    _, _, runtime, cache = setup_cache(tmp_path)
    cache.directory.mkdir()
    cache.entries[0][1].write_bytes(b"invalid ciphertext")
    cache.restore()
    assert not (runtime / "providers/rules.cache").exists()


def test_cache_cannot_restore_outside_runtime_directory(tmp_path):
    profile, config, runtime, _ = setup_cache(tmp_path)
    config["rule-providers"]["security"]["path"] = "../../outside"
    with pytest.raises(ClientError):
        RuleCache(tmp_path, profile, config, runtime)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_real_core_reconnect_uses_cached_rules_when_source_is_offline(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"example.test\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = Profile(
        "Local rules",
        "",
        {
            "proxies": [{"name": "Local", "type": "direct"}],
            "rule-providers": {
                "security": {
                    "type": "http",
                    "behavior": "domain",
                    "format": "text",
                    "interval": 3600,
                    "url": f"http://127.0.0.1:{server.server_port}/rules",
                }
            },
            "rules": ["RULE-SET,security,DIRECT", "MATCH,DIRECT"],
        },
        "",
    )
    settings = Settings(tun=False, system_proxy=False)
    core = Core(tmp_path / "runtime")

    def wait_for_rules():
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with Controller(core.port, core.secret) as client:
                data = client.request("GET", "/providers/rules")
            if data["providers"]["security"]["ruleCount"] == 1:
                return
            time.sleep(0.02)
        pytest.fail("Rule provider did not load")

    try:
        core.start(profile, settings)
        wait_for_rules()
        core.status()
        assert list((tmp_path / "rule-cache").glob("*.dpapi"))
    finally:
        core.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    try:
        core.start(profile, settings)
        wait_for_rules()
        assert any("rule_cache_restored" in line for _, line in core.drain_logs(500))
    finally:
        core.stop()
