"""Separate rotating records of observed connection routes and byte counters."""

import threading
import time

from nodera.flow_monitor import FlowMonitor
from nodera.journal import Journal


class ConnectionJournal:
    def __init__(self, root):
        self.journal = Journal(root, filename="connections.jsonl", max_bytes=8 * 1024 * 1024)
        self.monitor = FlowMonitor()
        self.active = {}
        self.interval = 15
        self._lock = threading.RLock()

    def configure(self, persist=True, size_mb=8, interval=15):
        with self._lock:
            self.journal.configure(1000, persist)
            self.journal.max_bytes = size_mb * 1024 * 1024
            self.interval = interval

    def emit(self, event, row, previous=None, reason="", secrets=()):
        self.journal.record(
            event,
            {
                "id": row["id"],
                "process": row["process"],
                "host": row["host"],
                "ip": row["ip"],
                "port": row["port"],
                "network": row["network"],
                "inbound": row["inbound"],
                "route": row["route"],
                "rule": row["rule"],
                "upload": row["upload"],
                "download": row["download"],
                "delta_upload": max(0, row["upload"] - (previous["upload"] if previous else 0)),
                "delta_download": max(
                    0, row["download"] - (previous["download"] if previous else 0)
                ),
                "last_observed": True,
                "reason": reason,
            },
            secrets,
        )

    def update(self, connections, now=None, secrets=()):
        with self._lock:
            if not isinstance(connections, list):
                return
            now = time.monotonic() if now is None else now
            self.monitor.update(connections, now)
            current = {row["id"]: row for row in self.monitor.rows()}
            for key, row in current.items():
                previous = self.active.get(key)
                if previous is None:
                    self.emit("opened", row, secrets=secrets)
                    self.active[key] = {"row": row, "written": row, "at": now}
                else:
                    changed = row["route"] != previous["written"]["route"]
                    traffic = (row["upload"], row["download"]) != (
                        previous["written"]["upload"],
                        previous["written"]["download"],
                    )
                    if changed or (traffic and now - previous["at"] >= self.interval):
                        self.emit("traffic", row, previous["written"], secrets=secrets)
                        previous.update(written=row, at=now)
                    previous["row"] = row
            for key in set(self.active) - set(current):
                previous = self.active.pop(key)
                self.emit("closed", previous["row"], previous["written"], "not observed", secrets)
            self.monitor.flows = current

    def close(self, reason, secrets=()):
        with self._lock:
            for previous in self.active.values():
                self.emit("closed", previous["row"], previous["written"], reason, secrets)
            self.active.clear()
            self.monitor.clear()
