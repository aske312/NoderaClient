"""Approximate client IP location over a socket pinned to a physical interface."""

import ipaddress
import json
import os
import socket
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx

from nodera.locations import country
from nodera.models import ClientError


def physical_interface():
    script = """
$ErrorActionPreference='Stop'
$adapters=@(Get-NetAdapter -Physical | Where-Object Status -eq 'Up')
$indices=@($adapters | ForEach-Object InterfaceIndex)
$routes=@(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' | Where-Object { $_.InterfaceIndex -in $indices -and $_.NextHop -ne '0.0.0.0' } | Sort-Object RouteMetric)
foreach($route in $routes) {
  $ip=Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex | Where-Object { $_.AddressState -eq 'Preferred' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
  if($ip) { @{index=[int]$route.InterfaceIndex;address=$ip.IPAddress} | ConvertTo-Json -Compress; exit 0 }
}
exit 2
"""
    try:
        executable = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        value = json.loads(result.stdout) if result.returncode == 0 else {}
        address = ipaddress.IPv4Address(value.get("address", ""))
        index = value.get("index")
        if (
            type(index) is not int
            or not 0 < index < 2**24
            or address.is_loopback
            or address.is_link_local
        ):
            raise ValueError
        return index, str(address)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        raise ClientError(
            "Не удалось найти прямой физический интерфейс. Укажите точку клиента вручную."
        ) from exc


def detect_client_location(interface=None, transport=None):
    index, address = physical_interface() if interface is None else interface
    if transport is None:
        transport = httpx.HTTPTransport(
            local_address=address,
            socket_options=[
                (socket.IPPROTO_IP, getattr(socket, "IP_UNICAST_IF", 31), struct.pack("!I", index))
            ],
        )
    try:
        with httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(6, connect=4),
        ) as client:
            response = client.get(
                "https://1.1.1.1/dns-query",
                params={"name": "ipwho.is", "type": "A"},
                headers={"Accept": "application/dns-json"},
            )
            response.raise_for_status()
            records = response.json().get("Answer", [])
            addresses = [
                str(ipaddress.IPv4Address(row["data"]))
                for row in records
                if row.get("type") == 1 and ipaddress.IPv4Address(row.get("data", "")).is_global
            ]
            if not addresses:
                raise ValueError
            response = client.get(
                "https://" + addresses[0] + "/",
                headers={"Host": "ipwho.is"},
                extensions={"sni_hostname": "ipwho.is"},
            )
            response.raise_for_status()
            data = response.json()
            if not ipaddress.ip_address(data.get("ip", "")).is_global:
                raise ValueError
            place = country(data.get("country_code", ""))
            lat, lon = float(data.get("latitude")), float(data.get("longitude"))
            if (
                data.get("success") is not True
                or not place
                or not -90 <= lat <= 90
                or not -180 <= lon <= 180
            ):
                raise ValueError
            return {
                "code": place["code"],
                "lat": lat,
                "lon": lon,
                "name": " ".join(str(data.get("city") or place["name"]).split())[:80],
                "source": "ip-physical",
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise ClientError(
            "Прямое определение точки клиента недоступно. Точка VPN не подставлена; можно указать страну и координаты вручную."
        ) from exc
