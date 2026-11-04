import os
import socket

import pytest

from nodera.ports import choose_ports, listener_owners


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


@pytest.mark.skipif(os.name != "nt", reason="Windows TCP API")
def test_windows_listener_ownership_is_the_real_process():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        assert os.getpid() in listener_owners(port)[port]
