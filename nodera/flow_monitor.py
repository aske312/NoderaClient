"""Bounded in-memory observations from Mihomo; no browser history or HTTPS contents."""

import ipaddress
import ntpath
import time
from urllib.parse import urlsplit

MAX_FLOWS = 5000
BROWSERS = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "opera.exe",
    "browser.exe",
    "vivaldi.exe",
    "brave.exe",
    "zen.exe",
    "waterfox.exe",
    "librewolf.exe",
    "arc.exe",
    "thorium.exe",
}


def text(value, limit=512):
    return " ".join(str(value or "").split())[:limit]


def domain(value):
    value = text(value)
    try:
        return str(ipaddress.ip_address(value.strip("[]")))
    except ValueError:
        pass
    try:
        parsed = urlsplit(value if "://" in value else "//" + value)
        return (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def address(value):
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


class FlowMonitor:
    def __init__(self, retention_seconds=300):
        self.flows = {}
        self.active_routes = set()
        self.truncated = False
        self.retention_seconds = retention_seconds

    def clear(self):
        self.flows.clear()
        self.active_routes.clear()
        self.truncated = False

    def update(self, connections, now=None):
        if not isinstance(connections, list):
            return
        now = time.monotonic() if now is None else now
        self.truncated = len(connections) > MAX_FLOWS
        active = set()
        routes = set()
        for connection in connections[:MAX_FLOWS]:
            if not isinstance(connection, dict) or not connection.get("id"):
                continue
            metadata = connection.get("metadata")
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, dict):
                continue
            key = text(connection["id"], 128)
            if key in active:
                continue
            previous = self.flows.get(key)
            upload, download = connection.get("upload", 0), connection.get("download", 0)
            if type(upload) is not int or type(download) is not int or min(upload, download) < 0:
                continue
            path = text(metadata.get("processPath"), 1024)
            process = ntpath.basename(path or text(metadata.get("process"))) or "Не определено"
            host = domain(metadata.get("host"))
            ip = address(text(metadata.get("destinationIP")))
            chains = connection.get("chains") or []
            if isinstance(chains, list):
                routes.update(item for item in chains[:16] if isinstance(item, str))
            elapsed = max(now - previous["seen"], 0.001) if previous else 1
            row = {
                "id": key,
                "process": process,
                "path": path,
                "pid": text(metadata.get("processId") or metadata.get("pid"), 16),
                "browser": process.lower() in BROWSERS,
                "host": host,
                "ip": ip,
                "site": host or ip or "Не определён",
                "port": text(metadata.get("destinationPort"), 8),
                "network": text(metadata.get("network"), 12).upper(),
                "inbound": text(metadata.get("type"), 40),
                "route": " → ".join(text(item, 120) for item in reversed(chains[:16]))
                if isinstance(chains, list)
                else "",
                "rule": text(connection.get("rule"), 80),
                "upload": upload,
                "download": download,
                "up_rate": max(0, int((upload - previous["upload"]) / elapsed))
                if previous and previous["active"]
                else 0,
                "down_rate": max(0, int((download - previous["download"]) / elapsed))
                if previous and previous["active"]
                else 0,
                "active": True,
                "first": previous["first"] if previous else now,
                "seen": now,
            }
            self.flows[key] = row
            active.add(key)
        self.active_routes = routes
        for key, row in list(self.flows.items()):
            if key not in active:
                if now - row["seen"] > self.retention_seconds:
                    del self.flows[key]
                else:
                    row.update(active=False, up_rate=0, down_rate=0)
        if len(self.flows) > MAX_FLOWS:
            keep = sorted(
                self.flows.values(), key=lambda row: (row["active"], row["seen"]), reverse=True
            )[:MAX_FLOWS]
            self.flows = {row["id"]: row for row in keep}

    def rows(self, query="", process="", site="", active_only=True, browsers_only=False):
        return [
            row
            for row in self.flows.values()
            if (not active_only or row["active"])
            and (not browsers_only or row["browser"])
            and (not process or (row["path"] or row["process"]).casefold() == process)
            and (not site or row["site"] == site)
            and (
                not query
                or query.casefold()
                in " ".join(
                    str(row[key])
                    for key in ("process", "path", "site", "ip", "route", "network", "rule")
                ).casefold()
            )
        ]


def grouped_flows(rows, kind):
    groups = {}
    for row in rows:
        key = (row["path"] or row["process"]).casefold() if kind == "process" else row["site"]
        group = groups.setdefault(
            key,
            {
                "key": key,
                "name": row["process"] if kind == "process" else row["site"],
                "path": row["path"],
                "sites": set(),
                "processes": set(),
                "routes": set(),
                "active": 0,
                "count": 0,
                "download": 0,
                "upload": 0,
                "down_rate": 0,
                "up_rate": 0,
            },
        )
        group["sites"].add(row["site"])
        group["processes"].add(row["process"])
        if row["route"]:
            group["routes"].add(row["route"])
        group["count"] += 1
        group["active"] += int(row["active"])
        for field in ("upload", "download", "up_rate", "down_rate"):
            group[field] += row[field]
    return sorted(
        groups.values(), key=lambda row: (-row["active"], -row["download"], row["name"].casefold())
    )
