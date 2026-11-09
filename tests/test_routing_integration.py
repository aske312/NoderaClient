"""Verify subscription decisions by observing real loopback outbound traffic."""

import copy
import os
import socket
import socketserver
import struct
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import httpx
import pytest
import yaml

from nodera.bundled_core import bundled_location
from nodera.channel_selection import receive_exact
from nodera.controller import Controller
from nodera.core import Core
from nodera.models import Profile, Settings
from nodera.subscription import fetch_profile
from tests.loopback import ThreadingHTTPServer


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_dns_failure_and_recovery_do_not_require_restarting_core(tmp_path):
    class DNS(socketserver.BaseRequestHandler):
        def handle(self):
            query, stream = self.request
            cursor = 12
            while query[cursor]:
                cursor += query[cursor] + 1
            kind = struct.unpack("!H", query[cursor + 1 : cursor + 3])[0]
            question = query[12 : cursor + 5]
            self.server.queries += 1
            answer = not self.server.broken and kind == 1
            header = struct.pack(
                "!6H",
                struct.unpack("!H", query[:2])[0],
                0x8183 if self.server.broken else 0x8180,
                1,
                int(answer),
                0,
                0,
            )
            body = (
                b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 0, 4) + socket.inet_aton("127.0.0.1")
                if answer
                else b""
            )
            stream.sendto(header + question + body, self.client_address)

    resolver = socketserver.ThreadingUDPServer(("127.0.0.1", 0), DNS)
    resolver.daemon_threads = True
    resolver.broken, resolver.queries = True, 0
    thread = threading.Thread(
        target=lambda: resolver.serve_forever(poll_interval=0.05), daemon=True
    )
    thread.start()
    core = Core(tmp_path / "runtime")
    address = f"127.0.0.1:{resolver.server_address[1]}"
    try:
        with endpoint("origin") as origin, endpoint("proxy") as proxy:
            profile = Profile(
                "DNS recovery",
                "",
                {
                    "ipv6": False,
                    "proxies": [
                        {"name": "A", "type": "http", "server": "127.0.0.1", "port": proxy}
                    ],
                    "proxy-groups": [{"name": "Choice", "type": "select", "proxies": ["A"]}],
                    "dns": {
                        "enable": True,
                        "ipv6": False,
                        "enhanced-mode": "redir-host",
                        "nameserver": [address],
                        "default-nameserver": [address],
                    },
                    "rules": ["MATCH,DIRECT"],
                },
                "",
            )
            core.start(profile, Settings(tun=False, system_proxy=False))
            pid = core.process.pid
            with httpx.Client(
                proxy=f"http://127.0.0.1:{core.mixed_port}", trust_env=False, timeout=4
            ) as client:
                url = f"http://dns-release.test:{origin}/"
                with pytest.raises(httpx.HTTPError):
                    client.get(url).raise_for_status()
                assert resolver.queries > 0
                resolver.broken = False
                core.flush_dns()
                assert client.get(url).raise_for_status().text == "origin"
                assert core.running and core.process.pid == pid
    finally:
        core.stop()
        resolver.shutdown()
        resolver.server_close()
        thread.join(timeout=2)


@contextmanager
def endpoint(label):
    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            self.send_response(200)
            self.end_headers()
            self.handle_one_request()

        def do_GET(self):
            body = label.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_fetched_subscription_routes_real_requests_and_live_updates(tmp_path):
    with endpoint("A") as port_a, endpoint("B") as port_b, endpoint("origin") as origin:
        config = {
            "mode": "rule",
            "proxies": [
                {"name": name, "type": "http", "server": "127.0.0.1", "port": port}
                for name, port in (("A", port_a), ("B", port_b))
            ],
            "proxy-groups": [{"name": "Choice", "type": "select", "proxies": ["A", "B"]}],
            "rules": [
                "DOMAIN,blocked.suffix.test,REJECT",
                "DOMAIN,exact.suffix.test,Choice",
                "DOMAIN-SUFFIX,suffix.test,B",
                "DOMAIN-KEYWORD,needle,A",
                "AND,((NETWORK,TCP),(DOMAIN,logical.test)),A",
                "SUB-RULE,(DOMAIN,child.test),child",
                "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
                "MATCH,B",
            ],
            "sub-rules": {"child": ["MATCH,A"]},
        }
        # Exercise the same download/parser boundary as a real HTTPS subscription.
        transport = httpx.MockTransport(
            lambda _: httpx.Response(200, content=yaml.safe_dump(config).encode())
        )
        profile = fetch_profile("https://subscription.invalid/test", "Routing", "test", transport)
        profile = replace(profile, selections={"Choice": "A"})
        core = Core(tmp_path / "runtime")
        try:
            core.start(profile, Settings(tun=False, system_proxy=False))
            pid = core.process.pid

            def request(host):
                # New connection per request: updated rules apply to new flows.
                with httpx.Client(
                    proxy=f"http://127.0.0.1:{core.mixed_port}", trust_env=False, timeout=3
                ) as client:
                    response = client.get(f"http://{host}:{origin}/")
                    response.raise_for_status()
                    return response.text

            def rejected(host):
                with pytest.raises(httpx.HTTPError):
                    request(host)

            assert request("exact.suffix.test") == "A"  # First matching rule wins.
            assert request("other.suffix.test") == "B"
            assert request("has-needle.test") == "A"
            assert request("logical.test") == "A"
            assert request("child.test") == "A"
            assert request("127.0.0.1") == "origin"
            assert request("unmatched.test") == "B"
            rejected("blocked.suffix.test")

            core.select("Choice", "B")
            assert request("exact.suffix.test") == "B"
            core.force_server("A")
            assert request("unmatched.test") == "A"
            assert request("blocked.suffix.test") == "A"
            core.restore_policy()
            assert request("unmatched.test") == "B"
            rejected("blocked.suffix.test")

            updated = copy.deepcopy(profile.config)
            updated["rules"][0] = "DOMAIN,blocked.suffix.test,A"
            updated["rules"][-1] = "MATCH,A"
            profile = replace(profile, config=updated)
            core.apply_profile(profile)
            assert core.process.pid == pid
            assert request("blocked.suffix.test") == "A"
            assert request("unmatched.test") == "A"

            # Local rules precede subscription rules; removing them restores policy.
            core.apply_profile(replace(profile, local_rules=["DOMAIN,blocked.suffix.test,REJECT"]))
            rejected("blocked.suffix.test")
            core.apply_profile(profile)
            assert request("blocked.suffix.test") == "A"

            # Subscription modes deliberately bypass the rule list.
            core.apply_profile(replace(profile, config={**updated, "mode": "global"}))
            core.select("GLOBAL", "B")
            assert request("127.0.0.1") == "B"
            direct = {**updated, "mode": "direct", "rules": ["MATCH,REJECT"]}
            core.apply_profile(replace(profile, config=direct))
            assert request("127.0.0.1") == "origin"

            process = Path(sys.executable).name
            by_process = {**updated, "rules": [f"PROCESS-NAME,{process},A", "MATCH,REJECT"]}
            core.apply_profile(replace(profile, config=by_process))
            assert request("127.0.0.1") == "A"
        finally:
            core.stop()


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_udp_rules_reject_only_matching_destination_and_update_live(tmp_path):
    stopped = threading.Event()
    receivers = []
    threads = []

    def echo(sock):
        while not stopped.is_set():
            try:
                body, address = sock.recvfrom(65535)
                sock.sendto(body, address)
            except socket.timeout:
                continue
            except OSError:
                return

    def datagram(proxy_port, destination_port):
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=2) as tcp:
            tcp.settimeout(2)
            tcp.sendall(b"\x05\x01\x00")
            assert receive_exact(tcp, 2) == b"\x05\x00"
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                udp.bind(("127.0.0.1", 0))
                udp.settimeout(1)
                tcp.sendall(
                    b"\x05\x03\x00\x01\x7f\x00\x00\x01" + struct.pack("!H", udp.getsockname()[1])
                )
                assert receive_exact(tcp, 4) == b"\x05\x00\x00\x01"
                host = socket.inet_ntoa(receive_exact(tcp, 4))
                port = struct.unpack("!H", receive_exact(tcp, 2))[0]
                header = b"\x00\x00\x00\x01\x7f\x00\x00\x01" + struct.pack("!H", destination_port)
                udp.sendto(header + b"routing-probe", (host, port))
                assert udp.recv(65535) == header + b"routing-probe"

    core = Core(tmp_path / "runtime")
    try:
        for _ in range(2):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", 0))
            sock.settimeout(0.1)
            receivers.append(sock)
            thread = threading.Thread(target=echo, args=(sock,), daemon=True)
            thread.start()
            threads.append(thread)
        allowed, blocked = [sock.getsockname()[1] for sock in receivers]
        profile = Profile(
            "UDP rules",
            "",
            {
                "proxies": [{"name": "Local", "type": "direct"}],
                "rules": [
                    f"AND,((NETWORK,UDP),(DST-PORT,{blocked})),REJECT",
                    "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
                    "MATCH,REJECT",
                ],
            },
            "",
        )
        core.start(profile, Settings(tun=False, system_proxy=False))
        datagram(core.mixed_port, allowed)
        with pytest.raises(socket.timeout):
            datagram(core.mixed_port, blocked)
        datagram(core.mixed_port, allowed)
        config = {**profile.config, "rules": ["MATCH,DIRECT"]}
        core.apply_profile(replace(profile, config=config))
        datagram(core.mixed_port, blocked)
    finally:
        core.stop()
        stopped.set()
        for sock in receivers:
            sock.close()
        for thread in threads:
            thread.join(timeout=2)


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_rule_provider_actually_blocks_traffic_and_cached_policy_survives_offline(tmp_path):
    with endpoint("origin") as origin, endpoint("A") as proxy_port:
        core = Core(tmp_path / "runtime")
        settings = Settings(tun=False, system_proxy=False)
        with endpoint("blocked.test\n") as rule_port:
            # HTTP is confined to the loopback fixture; production imports require HTTPS.
            profile = Profile(
                "Rules",
                "",
                {
                    "proxies": [
                        {"name": "A", "type": "http", "server": "127.0.0.1", "port": proxy_port}
                    ],
                    "hosts": {"blocked.test": "127.0.0.1"},
                    "rule-providers": {
                        "blocklist": {
                            "type": "http",
                            "behavior": "domain",
                            "format": "text",
                            "interval": 3600,
                            "proxy": "DIRECT",
                            "url": f"http://127.0.0.1:{rule_port}/rules",
                        }
                    },
                    "rules": ["RULE-SET,blocklist,REJECT", "MATCH,A"],
                },
                "",
            )

            def verify():
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    with Controller(core.port, core.secret) as controller:
                        providers = controller.request("GET", "/providers/rules")["providers"]
                    if providers["blocklist"]["ruleCount"] == 1:
                        break
                    time.sleep(0.02)
                else:
                    pytest.fail("Rule provider did not load")
                with httpx.Client(
                    proxy=f"http://127.0.0.1:{core.mixed_port}", trust_env=False, timeout=3
                ) as client:
                    # A working fallback proves rejection isn't a broken network.
                    assert client.get(f"http://unmatched.test:{origin}/").text == "A"
                    with pytest.raises(httpx.HTTPError):
                        client.get(f"http://blocked.test:{origin}/").raise_for_status()

            try:
                core.start(profile, settings)
                verify()
                core.status()  # Persist the downloaded provider in the encrypted cache.
            finally:
                core.stop()
        # The provider server is now stopped, but both traffic destinations stay alive.
        try:
            core.start(profile, settings)
            verify()
            assert any("rule_cache_restored" in line for _, line in core.drain_logs(500))
            # Removing RULE-SET must permit the very same destination.
            config = {**profile.config, "rule-providers": {}, "rules": ["MATCH,A"]}
            core.apply_profile(replace(profile, config=config))
            with httpx.Client(
                proxy=f"http://127.0.0.1:{core.mixed_port}", trust_env=False, timeout=3
            ) as client:
                assert client.get(f"http://blocked.test:{origin}/").text == "A"
        finally:
            core.stop()
