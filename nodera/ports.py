"""Pick local ports and verify ownership using Windows' TCP listener table."""

import ctypes
import os
import socket
from ctypes import wintypes

from nodera.models import ClientError


def bound_socket(port=0, kind=socket.SOCK_STREAM):
    sock = socket.socket(socket.AF_INET, kind)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        return sock
    except Exception:
        sock.close()
        raise


def choose_ports(preferred=0):
    # Mihomo's mixed listener needs both protocols. Windows may reserve a port
    # for UDP while allowing TCP, so ask UDP for a candidate before checking TCP.
    last_error = None
    for attempt in range(32):
        try:
            with bound_socket(preferred if attempt == 0 else 0, socket.SOCK_DGRAM) as udp:
                with bound_socket(udp.getsockname()[1]) as proxy, bound_socket() as controller:
                    return proxy.getsockname()[1], controller.getsockname()[1]
        except OSError as exc:
            last_error = exc
    raise ClientError(
        "Windows не предоставила свободные локальные порты. Проверьте сетевые ограничения системы."
    ) from last_error


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
