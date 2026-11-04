"""Read Windows IPv4 assignments and choose a free local fake-IP pool."""

import ctypes
import ipaddress
import os
import socket
import struct
from ctypes import wintypes

from nodera.models import ClientError


class AddressRow(ctypes.Structure):
    _fields_ = [
        (name, wintypes.DWORD) for name in ("address", "index", "mask", "broadcast", "size")
    ]
    _fields_ += [("unused", wintypes.USHORT), ("kind", wintypes.USHORT)]


def ipv4_assignments():
    if os.name != "nt":
        return []
    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetIpAddrTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL]
    api.restype = wintypes.DWORD
    size = wintypes.ULONG()
    for _ in range(5):
        buffer = ctypes.create_string_buffer(size.value) if size.value else None
        result = api(buffer, ctypes.byref(size), False)
        if result == 122:
            continue
        if result or buffer is None:
            break
        count = wintypes.DWORD.from_buffer(buffer).value
        offset, row_size = ctypes.sizeof(wintypes.DWORD), ctypes.sizeof(AddressRow)
        if offset + count * row_size > len(buffer):
            break
        values = []
        for index in range(count):
            row = AddressRow.from_buffer(buffer, offset + index * row_size)
            if not row.address or not row.mask:
                continue
            address = socket.inet_ntoa(struct.pack("=I", row.address))
            mask = socket.inet_ntoa(struct.pack("=I", row.mask))
            values.append(ipaddress.ip_interface(f"{address}/{mask}"))
        return values
    raise ClientError("Windows не позволила проверить адреса сетевых адаптеров.")


def choose_tun_range(assignments=None):
    assigned = ipv4_assignments() if assignments is None else assignments
    occupied = [ipaddress.ip_interface(value).network for value in assigned]
    reserved = ipaddress.ip_network("198.18.0.0/15")
    candidates = [ipaddress.ip_network(value) for value in ("198.19.0.0/16", "198.18.0.0/16")]
    for prefix in range(17, 25):
        candidates.extend(reserved.subnets(new_prefix=prefix))
    for network in candidates:
        if not any(network.overlaps(item) for item in occupied):
            return f"{network.network_address + 1}/{network.prefixlen}"
    raise ClientError(
        "Нет свободного диапазона адресов TUN. Отключите другой TUN или выберите Proxy."
    )
