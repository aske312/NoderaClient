"""In-memory session model: no sockets, subprocesses, TUN or proxy changes."""

import copy
import time

from nodera.core import Core
from nodera.models import ClientError, visible_proxies
from nodera.route_health import RouteHealth


class DemoCore(Core):
    demo = True

    def __init__(self, root, scenarios=None):
        super().__init__(root)
        self.scenarios = scenarios
        self._running = False
        self.profile = None
        self.closed = set()
        self.forced_server = ""

    @property
    def running(self):
        return self._running

    def start(self, profile, settings):
        if self.scenarios:
            self.scenarios.check_subscription(profile.id)
        self.route_health = RouteHealth()
        self.frozen_routes = {}
        self.started_at = time.monotonic()
        self.closed.clear()
        self._running = True
        self.apply_profile(profile)
        self._record("Демонстрационный сеанс: передача трафика отключена.")
        return "Demo"

    def stop(self, reason="requested"):
        self._running = False

    def cancel_start(self):
        self._stop_requested.set()

    def apply_profile(self, profile):
        self.profile = copy.deepcopy(profile)
        self.forced_server = profile.forced_server
        self.node_names = [n["name"] for n in visible_proxies(profile.config)]
        return self.groups()

    def groups(self):
        if not self.profile:
            return []
        result = []
        kinds = {
            "select": "Selector",
            "url-test": "URLTest",
            "fallback": "Fallback",
            "load-balance": "LoadBalance",
        }
        for group in self.profile.config.get("proxy-groups", []):
            members = [
                n
                for n in visible_proxies(self.profile.config)
                if n["name"] in group.get("proxies", [])
            ]
            if not members:
                continue
            current = self.forced_server or self.profile.selections.get(
                group["name"], members[0]["name"]
            )
            if not self.forced_server and group["type"] in {"url-test", "fallback", "load-balance"}:
                healthy = [
                    n["name"]
                    for n in members
                    if self.delay(n["name"]) > 0 and n["name"] not in self.frozen_routes
                ]
                if healthy:
                    current = (
                        min(healthy, key=self.delay) if group["type"] == "url-test" else healthy[0]
                    )
            result.append(
                {
                    "name": group["name"],
                    "type": kinds.get(group["type"], "Selector"),
                    "current": current,
                    "active": [current],
                    "choices": {group["name"]: current},
                    "members": [
                        {"name": n["name"], "type": n["type"], "delay": self.delay(n["name"])}
                        for n in members
                    ],
                }
            )
        return result

    def select(self, group, member):
        self._validate_member(member)
        self.profile.selections[group] = member
        return self.groups()

    def _validate_member(self, member):
        if member not in self.node_names:
            raise ClientError("Демонстрационный маршрут не найден.")

    def force_server(self, member):
        self._validate_member(member)
        self.forced_server = member
        return self.groups()

    def restore_policy(self):
        self.forced_server = ""
        return self.groups()

    def restore_selections(self, selections):
        self.profile.selections = dict(selections)

    def delay(self, member):
        if self.scenarios and self.profile:
            try:
                self.scenarios.check_subscription(self.profile.id)
            except ClientError:
                return 0
            return self.scenarios.condition(self.profile.id, member, self.node_names.index(member))[
                "delay"
            ]
        return 24 + sum(member.encode()) % 95

    def recheck_routes(self):
        return [g["name"] for g in self.groups()]

    def check_connectivity(self):
        if not self.running:
            raise ClientError("Демонстрационный сеанс остановлен.")
        if self.scenarios:
            self.scenarios.check_subscription(self.profile.id)
        if not self.groups() or not self.delay(self.groups()[0]["current"]):
            raise ClientError("Демо · выбранный маршрут недоступен.")
        return True

    def connection_snapshot(self):
        if not self.running:
            return []
        if self.scenarios:
            try:
                self.scenarios.check_subscription(self.profile.id)
            except ClientError:
                return []
        seconds = max(1, int(time.monotonic() - self.started_at))
        route = self.forced_server or self.groups()[0]["current"]
        if self.delay(route) <= 0:
            return []
        return [
            {
                "id": f"demo-flow-{index}",
                "metadata": {
                    "network": "tcp",
                    "type": "HTTP",
                    "host": host,
                    "sourceIP": "192.0.2.10",
                    "sourcePort": 40000 + index,
                    "destinationIP": f"198.51.100.{index + 1}",
                    "destinationPort": "443",
                    "process": "demo-browser.exe",
                    "processPath": "",
                },
                "upload": seconds * 1024,
                "download": seconds * 32768,
                "chains": [route, self.groups()[0]["name"]],
                "rule": "Match",
                "rulePayload": "",
                "start": "2026-01-01T00:00:00Z",
            }
            for index, host in enumerate(("example.invalid", "video.invalid"))
            if f"demo-flow-{index}" not in self.closed
        ]

    def close_connections(self, identities):
        self.closed.update(identities)
        return list(identities), 0

    def status(self):
        seconds = max(0, int(time.monotonic() - self.started_at))
        flows = self.connection_snapshot()
        return {
            "download": seconds * 65536,
            "upload": seconds * 2048,
            "download_rate": 65536 if flows else 0,
            "upload_rate": 2048 if flows else 0,
            "uptime": seconds,
            "connections": len(flows),
            "flows": flows,
        }

    def set_log_level(self, level):
        self._record(f"Демо: уровень журнала {level}.")

    def flush_dns(self):
        return None

    def probe_aliases(self):
        return {}

    def update_route_health(self, values):
        previous = dict(self.frozen_routes)
        self.route_health.update(values, time.monotonic())
        self.frozen_routes = dict(self.route_health.frozen)
        return previous != self.frozen_routes
