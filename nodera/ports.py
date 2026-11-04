"""Pick local ports and verify ownership using Windows' TCP listener table."""

import ctypes
import os
import socket
from ctypes import wintypes

from nodera.models import ClientError


def bound_socket(port=0):
    sock = socket.socket()
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        return sock
    except Exception:
        sock.close()
        raise


def choose_ports(preferred=0):
    try:
        try:
            proxy = bound_socket(preferred)
        except OSError:
            proxy = bound_socket()
        with proxy, bound_socket() as controller:
            return proxy.getsockname()[1], controller.getsockname()[1]
    except OSError as exc:
        raise ClientError(
            "Windows не предоставила свободные локальные порты. Проверьте сетевые ограничения системы."
        ) from exc


class TcpRow(ctypes.Structure):
    _fields_ = [
        (name, wintypes.DWORD)
        for name in ("state", "address", "port", "remote_address", "remote_port", "pid")
    ]


def listener_owners(*ports):
    """IPv4 listener PIDs. No inspection or termination of other applications."""
    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    api.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    ]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    for _ in range(5):
        buffer = ctypes.create_string_buffer(size.value) if size.value else None
        result = api(buffer, ctypes.byref(size), False, socket.AF_INET, 3, 0)
        if result == 122:
            continue
        if result != 0 or buffer is None:
            break
        count = wintypes.DWORD.from_buffer(buffer).value
        row_size = ctypes.sizeof(TcpRow)
        offset = ctypes.sizeof(wintypes.DWORD)
        if offset + count * row_size > len(buffer):
            break
        owners = {port: set() for port in ports}
        for index in range(count):
            row = TcpRow.from_buffer(buffer, offset + index * row_size)
            port = socket.ntohs(row.port & 0xFFFF)
            if port in owners and row.state == 2:
                owners[port].add(row.pid)
        return owners
    raise ClientError("Windows не позволила проверить локальные порты ядра.")
