import os
import socket
from contextlib import contextmanager

import pytest

from nodera.ports import choose_ports, listener_owners


def test_port_selection_escapes_a_tcp_excluded_udp_candidate(monkeypatch):
    calls = []

    class Bound:
        def __init__(self, port):
            self.port = port

        def getsockname(self):
            return "127.0.0.1", self.port

    @contextmanager
    def bind(port=0, kind=socket.SOCK_STREAM):
        calls.append((port, kind))
        if kind == socket.SOCK_DGRAM:
            yield Bound(port or 49800)
        elif port == 49800:
            raise PermissionError(10013, "TCP range excluded by Windows")
        else:
            yield Bound(port or 62000)

    monkeypatch.setattr("nodera.ports.bound_socket", bind)
    monkeypatch.setattr("nodera.ports.secrets.randbelow", lambda limit: 10000)
    proxy, controller = choose_ports()
    assert proxy != 49800 and controller != proxy
    assert len(calls) < 32


def test_automatic_ports_are_distinct_and_skip_an_occupied_preference():
    proxy, controller = choose_ports()
    assert 1024 <= proxy <= 65535 and 1024 <= controller <= 65535
    assert proxy != controller
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        preferred = sock.getsockname()[1]
        proxy, controller = choose_ports(preferred)
        assert preferred not in (proxy, controller)


def test_mixed_port_skips_a_preference_occupied_only_by_udp():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        if os.name == "nt":
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        udp.bind(("127.0.0.1", 0))
        preferred = udp.getsockname()[1]
        # A TCP-only check would accept this port even though Mihomo cannot use it.
        with socket.socket() as tcp:
            tcp.bind(("127.0.0.1", preferred))
        proxy, controller = choose_ports(preferred)
        assert proxy != preferred
        assert proxy != controller
        with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            tcp.bind(("127.0.0.1", proxy))
            probe.bind(("127.0.0.1", proxy))


@pytest.mark.skipif(os.name != "nt", reason="Windows TCP API")
def test_windows_listener_ownership_is_the_real_process():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        assert os.getpid() in listener_owners(port)[port]
