"""Explicit IP lookups over HTTPS; never send subscription URLs or credentials."""

import ipaddress
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from nodera.locations import country
from nodera.models import ClientError
from nodera.routes import route_for_node
from nodera.storage import atomic_write


def transport_peers(pid):
    """Observe only this core's established TCP peers, never infer a hidden origin."""
    if os.name != "nt" or type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return set()
    script = (
        f"@(Get-NetTCPConnection -OwningProcess {pid} -State Established "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty RemoteAddress "
        "-Unique) | ConvertTo-Json -Compress"
    )
    try:
        executable = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        values = json.loads(result.stdout) if result.returncode == 0 else []
        values = [values] if isinstance(values, str) else values
        return {
            str(ipaddress.ip_address(ip)) for ip in values if ipaddress.ip_address(ip).is_global
        }
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return set()


def valid_host(value):
    text = str(value).strip().strip("[]")
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        if not text or len(text) > 253 or any(char in text for char in "/\\@:?# \r\n\t"):
            return ""
        try:
            text = text.encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError:
            return ""
        return (
            text
            if all(
                part and len(part) <= 63 and all(c.isalnum() or c == "-" for c in part)
                for part in text.rstrip(".").split(".")
            )
            else ""
        )


def endpoint_rows(profile, node_name, choices=None):
    hops = route_for_node(profile, node_name, choices)
    endpoints = []
    for index, hop in enumerate(hops):
        host = valid_host(hop.get("host", ""))
        if host:
            endpoints.append(
                {"role": hop["role"], "host": host, "step": index, "entry": hop.get("entry", False)}
            )
    node = next(
        (node for node in profile.config.get("proxies", []) if node["name"] == node_name), {}
    )
    host = valid_host(node.get("server", ""))
    if host and not any(item["host"] == host for item in endpoints):
        endpoints.append({"role": "entry", "host": host, "step": None, "entry": True})
    return endpoints[:8]


class GeoClient:
    def __init__(self, root: Path, transport=None, proxy=None):
        self.path = root / "geo-cache.json"
        self.transport = transport
        self.proxy = proxy
        self.blocked_until = 0
        self.cache = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.cache = {
                    key: value
                    for key, value in data.items()
                    if isinstance(value, dict) and time.time() - value.get("timestamp", 0) < 86400
                }
        except (OSError, ValueError, TypeError):
            pass

    def client(self):
        return httpx.Client(
            timeout=httpx.Timeout(5, connect=3),
            trust_env=False,
            transport=self.transport,
            proxy=self.proxy if self.transport is None else None,
            follow_redirects=False,
        )

    def resolve(self, host, dns=None, cancelled=None):
        host = valid_host(host)
        if not host:
            raise ClientError("В подписке нет корректного адреса этого участка.")
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        if "." not in host or host.endswith((".local", ".lan", ".internal")):
            raise ClientError("Локальное имя не передаётся внешнему DNS-сервису.")
        result = []
        with self.client() as client:
            for kind in ("A", "AAAA"):
                # An unavailable/disabled core resolver must not disable CDN discovery.
                resolvers = ([None] if dns else []) + [
                    "https://1.1.1.1/dns-query",
                    "https://8.8.8.8/resolve",
                ]
                for resolver in resolvers:
                    query, visited, found = host, set(), False
                    for _ in range(8):
                        if cancelled and cancelled.is_set():
                            raise ClientError("Проверка IP остановлена.")
                        if query in visited:
                            break
                        visited.add(query)
                        try:
                            data = (
                                dns(query, kind)
                                if resolver is None
                                else client.get(
                                    resolver,
                                    params={"name": query, "type": kind},
                                    headers={"Accept": "application/dns-json"},
                                )
                                .raise_for_status()
                                .json()
                            )
                            aliases = []
                            for answer in data.get("Answer", []):
                                if answer.get("type") == 5:
                                    alias = valid_host(answer.get("data", ""))
                                    if (
                                        alias
                                        and "." in alias
                                        and not alias.endswith((".local", ".lan", ".internal"))
                                    ):
                                        aliases.append(alias)
                                if answer.get("type") != (1 if kind == "A" else 28):
                                    continue
                                ip = ipaddress.ip_address(answer["data"])
                                # Fake-IP answers cannot identify a CDN edge.
                                if ip.is_global:
                                    address = str(ip)
                                    if address not in result:
                                        result.append(address)
                                    found = True
                            if found or not aliases:
                                break
                            query = aliases[0]
                        except (
                            httpx.HTTPError,
                            ValueError,
                            KeyError,
                            TypeError,
                            AttributeError,
                            ClientError,
                        ):
                            break
                    if found:
                        break
        if not result:
            raise ClientError("DNS не вернул A/AAAA-адреса. Повторите проверку позже.")
        return result[:8]

    def lookup(self, address, force=False):
        ip = ipaddress.ip_address(address)
        key = str(ip)
        if not ip.is_global:
            return {
                "ip": key,
                "country": "",
                "status": "Локальный / служебный IP",
                "timestamp": time.time(),
            }
        cached = self.cache.get(key)
        if not force and cached and time.time() - cached.get("timestamp", 0) < 86400:
            return dict(cached, cached=True)
        if time.time() < self.blocked_until:
            raise ClientError("Геосервис временно ограничил запросы; доступны проверенные адреса.")
        try:
            with self.client() as client:
                response = client.get("https://ipwho.is/" + key)
                if response.status_code == 429:
                    self.blocked_until = time.time() + 900
                    raise ClientError("Лимит ipwho.is исчерпан. Доступны ранее проверенные адреса.")
                response.raise_for_status()
                data = response.json()
            if data.get("success") is not True or ipaddress.ip_address(data.get("ip", "")) != ip:
                raise ClientError("Геосервис не подтвердил запрошенный IP.")
            code = str(data.get("country_code", "")).upper()
            if not country(code):
                raise ClientError("Геосервис не определил страну IP.")
            lat, lon = float(data["latitude"]), float(data["longitude"])
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError
            connection = data.get("connection") or {}
            result = {
                "ip": key,
                "country": code,
                "city": str(data.get("city", ""))[:100],
                "lat": lat,
                "lon": lon,
                "org": str(connection.get("org", ""))[:150],
                "asn": str(connection.get("asn", "")),
                "timestamp": time.time(),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "status": "Проверено по IP",
                "provider": "ipwho.is",
            }
            self.cache[key] = result
            self.cache = dict(
                sorted(self.cache.items(), key=lambda pair: pair[1].get("timestamp", 0))[-512:]
            )
            try:
                atomic_write(self.path, json.dumps(self.cache).encode())
            except OSError:
                pass
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ClientError("IP определён, но геосервис сейчас недоступен.") from exc

    def check_node(
        self, profile, name, choices=None, dns=None, cancelled=None, force=False, peers=None
    ):
        endpoints = endpoint_rows(profile, name, choices)
        rows = []
        for endpoint in endpoints:
            if cancelled and cancelled.is_set():
                break
            try:
                addresses = self.resolve(endpoint["host"], dns, cancelled)
            except ClientError as exc:
                rows.append({**endpoint, "ip": "", "country": "", "status": str(exc)})
                continue
            for address in addresses:
                if cancelled and cancelled.is_set():
                    break
                try:
                    location = self.lookup(address, force=force)
                except ClientError as exc:
                    location = {"ip": address, "country": "", "status": str(exc)}
                observed = bool(endpoint.get("entry") and address in (peers or set()))
                rows.append(
                    {
                        **endpoint,
                        **location,
                        "observed": observed,
                        "status": location["status"]
                        + (" · TCP-соединение ядра" if observed else " · Адрес из DNS/профиля"),
                    }
                )
        return {
            "node": name,
            "rows": rows,
            "timestamp": time.time(),
            "note": "Проверены DNS/IP адреса участков. Это геоданные IP, не физический адрес сервера."
            if endpoints
            else "Скрытый адрес origin не передан в подписке. Добавьте его в редакторе маршрута.",
        }


def verified_locations(routes, results):
    locations = []
    for route_index, route in enumerate(routes):
        rows = results.get(route["node"], {}).get("rows", [])
        for step, hop in enumerate(route["hops"]):
            verified = [
                row
                for row in rows
                if row.get("step") == step
                and valid_host(row.get("host", "")) == valid_host(hop.get("host", ""))
                and row.get("country")
                and time.time() - row.get("timestamp", 0) < 86400
            ]
            variants = {row["country"]: row for row in verified}
            observed = [row for row in verified if row.get("observed")]
            if observed:
                variants = {row["country"]: row for row in observed}
            if not variants:
                place = country(hop["country"])
                if place:
                    locations.append(
                        {
                            **place,
                            "route": route_index,
                            "node": route["node"],
                            "host": hop.get("host", ""),
                            "step": step,
                            "role": hop["role"],
                            "source": hop["source"],
                            "order_known": route["order_known"],
                            "entry": hop.get("entry", False),
                        }
                    )
            for code, row in variants.items():
                locations.append(
                    {
                        **country(code),
                        "lat": row["lat"],
                        "lon": row["lon"],
                        "ip": row["ip"],
                        "city": " ".join(str(row.get("city") or "").split())[:100],
                        "observed": row.get("observed", False),
                        "route": route_index,
                        "node": route["node"],
                        "host": hop.get("host", ""),
                        "step": step,
                        "role": hop["role"],
                        "source": "geoip",
                        "order_known": route["order_known"] and len(variants) == 1,
                        "entry": hop.get("entry", False) and len(variants) == 1,
                    }
                )
    return locations
