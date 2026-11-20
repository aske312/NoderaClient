"""Exclusive Windows listeners for isolated loopback integration fixtures."""

import os
import socket
from http.server import ThreadingHTTPServer as BaseHTTPServer
from socketserver import ThreadingTCPServer as BaseTCPServer


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
