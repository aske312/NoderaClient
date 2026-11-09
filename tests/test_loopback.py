import os
import socket
from http.server import BaseHTTPRequestHandler

import pytest

from tests.loopback import ThreadingHTTPServer


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive listener")
def test_loopback_server_port_cannot_be_shared_by_reuseaddr_socket():
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        with socket.socket() as contender:
            contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                contender.bind(server.server_address)
    finally:
        server.server_close()
