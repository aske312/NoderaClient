"""Exclusive Windows listeners for isolated loopback integration fixtures."""

import os
import select
import socket
from http.server import ThreadingHTTPServer as BaseHTTPServer
from socketserver import ThreadingTCPServer as BaseTCPServer


def relay_connections(client, upstream, timeout=5):
    """Forward both directions until each peer has finished sending."""
    reading = [client, upstream]
    while reading:
        readable, _, _ = select.select(reading, [], [], timeout)
        if not readable:
            return
        for source in readable:
            target = upstream if source is client else client
            data = source.recv(65536)
            if data:
                target.sendall(data)
                continue
            reading.remove(source)
            # A request-side FIN still permits the server's response. Propagate
            # only the write-side close and continue forwarding the other peer.
            try:
                target.shutdown(socket.SHUT_WR)
            except OSError:
                pass


class ThreadingHTTPServer(BaseHTTPServer):
    # Windows SO_REUSEADDR can share an endpoint with another socket. Test servers
    # use ephemeral exclusive ports instead; this is not Unix's TIME_WAIT reuse.
    allow_reuse_address = os.name != "nt"
    allow_reuse_port = False
    request_queue_size = 128

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ThreadingTCPServer(BaseTCPServer):
    allow_reuse_address = os.name != "nt"
    allow_reuse_port = False
    request_queue_size = 128

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
