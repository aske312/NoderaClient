"""Persistent, deterministic events scoped to fictional subscriptions."""

import copy
import json
import time
from dataclasses import replace
from threading import RLock

from nodera.models import ClientError
from nodera.storage import atomic_write
from nodera.subscription import SubscriptionUnavailable

DEMO_PROFILES = {
    "demo-pro": {"name": "Демо PRO", "pro": True},
    "demo-standard": {"name": "Демо Стандарт", "pro": False},
    "demo-outage": {"name": "Демо · Отказ всех", "pro": False, "preset": "outage"},
    "demo-slow": {"name": "Демо · Медленно", "pro": False, "preset": "slow"},
    "demo-unstable": {"name": "Демо · Обрывы", "pro": True, "unstable": True},
    "demo-failover": {"name": "Демо · Отказ узла", "pro": True, "preset": "primary-down"},
    "demo-expired": {"name": "Демо · Срок истёк", "pro": True, "subscription": "expired"},
    "demo-exhausted": {"name": "Демо · Лимит 100%", "pro": False, "subscription": "exhausted"},
    "demo-removed": {"name": "Демо · Удалена", "pro": True, "subscription": "removed"},
    "demo-update-error": {"name": "Демо · Сбой API", "pro": False, "subscription": "unavailable"},
}
DEMO_IDS = tuple(DEMO_PROFILES)
PRESETS = {
    "mixed": "Смешанные каналы",
    "healthy": "Все каналы работают",
    "primary-down": "Отказ основного канала",
    "slow": "Высокая задержка",
    "outage": "Все каналы недоступны",
    "recovered": "Восстановление каналов",
}
STATES = {
    "healthy": "Работает",
    "down": "Не работает",
    "slow": "Высокая задержка",
    "unstable": "Нестабильный",
}
SUBSCRIPTIONS = {
    "normal": "В норме",
    "expired": "Истёк срок",
    "exhausted": "Исчерпан трафик",
    "unavailable": "Ошибка обновления",
    "removed": "Подписка удалена",
}


def subscription_profile(profile, state):
    usage = {
        "upload": 128 * 1024**2,
        "download": 3 * 1024**3,
        "total": 100 * 1024**3,
        "expire": int(time.time()) + (-86400 if state == "expired" else 90 * 86400),
    }
    if state == "exhausted":
        usage["download"] = usage["total"]
    return replace(
        profile,
        usage=usage,
        access_state={"expired": "expired", "exhausted": "exhausted", "removed": "missing"}.get(
            state, ""
        ),
    )


class TestScenarios:
    __test__ = False

    def __init__(self, root, *, clock=time.monotonic):
        self.path = root / "test-scenarios.json"
        self.clock = clock
        self.lock = RLock()
        self.profiles = {identity: self.defaults(identity) for identity in DEMO_IDS}
        self.events = []
        self.due = {}
        self.phases = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for identity in DEMO_IDS:
                    value = data.get("profiles", {}).get(identity, {})
                    if (
                        isinstance(value, dict)
                        and value.get("preset") in PRESETS
                        and value.get("subscription") in SUBSCRIPTIONS
                        and isinstance(value.get("nodes"), dict)
                        and all(
                            isinstance(name, str)
                            and isinstance(node, dict)
                            and node.get("state") in STATES
                            and type(node.get("latency")) is int
                            and 1 <= node["latency"] <= 10000
                            and type(node.get("udp")) is bool
                            for name, node in value["nodes"].items()
                        )
                        and type(value.get("auto")) is bool
                        and type(value.get("interval")) is int
                        and 2 <= value["interval"] <= 300
                        and type(value.get("step")) is int
                        and value["step"] >= 0
                    ):
                        self.profiles[identity] = {**self.defaults(identity), **value}
            except (ValueError, OSError, AttributeError, TypeError):
                pass

    @staticmethod
    def defaults(identity=None):
        definition = DEMO_PROFILES.get(identity, {})
        return {
            "preset": definition.get(
                "preset", "mixed" if identity in (None, "demo-pro", "demo-standard") else "healthy"
            ),
            "subscription": definition.get("subscription", "normal"),
            "nodes": {"DE · Frankfurt": {"state": "unstable", "latency": 70, "udp": True}}
            if definition.get("unstable")
            else {},
            "auto": False,
            "interval": 3 if definition.get("unstable") else 15,
            "step": 0,
        }

    def settings(self, identity):
        if identity not in DEMO_IDS:
            raise ClientError("Сценарии доступны только фиктивным подпискам.")
        with self.lock:
            return copy.deepcopy(self.profiles[identity])

    def save(self, identity, message, **changes):
        self.settings(identity)
        with self.lock:
            self.profiles[identity].update(copy.deepcopy(changes))
            self.events.append(f"{time.strftime('%H:%M:%S')} · {identity} · {message}")
            self.events = self.events[-60:]
            atomic_write(
                self.path, json.dumps({"profiles": self.profiles}, ensure_ascii=False).encode()
            )

    def preset(self, identity, preset):
        if preset not in PRESETS:
            raise ClientError("Неизвестный тестовый сценарий.")
        self.save(identity, PRESETS[preset], preset=preset, nodes={})

    def node(self, identity, name, state, latency=1500, udp=True):
        if state not in STATES or type(latency) is not int or not 1 <= latency <= 10000:
            raise ClientError("Некорректное состояние тестового канала.")
        nodes = self.settings(identity)["nodes"]
        nodes[name] = {"state": state, "latency": latency, "udp": bool(udp)}
        self.save(identity, name + " · " + STATES[state], nodes=nodes)

    def subscription(self, identity, state):
        if state not in SUBSCRIPTIONS:
            raise ClientError("Неизвестное состояние подписки.")
        self.save(identity, SUBSCRIPTIONS[state], subscription=state)

    def automatic(self, identity, enabled, interval):
        if type(interval) is not int or not 2 <= interval <= 300:
            raise ClientError("Интервал событий: от 2 до 300 секунд.")
        self.save(
            identity,
            "Автособытия включены" if enabled else "Автособытия остановлены",
            auto=bool(enabled),
            interval=interval,
        )
        self.due[identity] = self.clock() + interval

    def advance(self, identity):
        value = self.settings(identity)
        sequence = ("primary-down", "slow", "outage", "recovered")
        preset = sequence[value["step"] % len(sequence)]
        self.save(
            identity, "Событие: " + PRESETS[preset], preset=preset, nodes={}, step=value["step"] + 1
        )
        self.due[identity] = self.clock() + value["interval"]

    def tick(self):
        changed = []
        for identity in DEMO_IDS:
            value = self.settings(identity)
            if value["auto"]:
                self.due.setdefault(identity, self.clock() + value["interval"])
                if self.clock() >= self.due[identity]:
                    self.advance(identity)
                    changed.append(identity)
            value = self.settings(identity)
            if any(
                isinstance(node, dict) and node.get("state") == "unstable"
                for node in value["nodes"].values()
            ):
                phase = int(self.clock() // value["interval"])
                previous = self.phases.setdefault(identity, phase)
                if previous != phase:
                    self.phases[identity] = phase
                    self.save(
                        identity,
                        "Нестабильные каналы: отказ"
                        if phase % 2 == 0
                        else "Нестабильные каналы: восстановление",
                    )
                    if identity not in changed:
                        changed.append(identity)
        return changed

    def reset(self, identity):
        self.save(identity, "Сценарии сброшены", **self.defaults(identity))
        self.due.pop(identity, None)
        self.phases.pop(identity, None)

    def condition(self, identity, name, index):
        index = {"DE": 0, "NL": 1, "FI": 2, "FR": 3, "SE": 4, "PL": 5}.get(
            name.split(" · ")[0], index
        )
        settings = self.settings(identity)
        preset = settings["preset"]
        state = "healthy"
        if (
            preset == "outage"
            or preset == "primary-down"
            and index == 0
            or preset == "mixed"
            and index == 1
        ):
            state = "down"
        elif preset == "slow" or preset == "mixed" and index == 2:
            state = "slow"
        value = settings["nodes"].get(name, {})
        state = value.get("state", state)
        down = (
            state == "down"
            or state == "unstable"
            and int(self.clock() // settings["interval"]) % 2 == 0
        )
        latency = value.get("latency", 1500 if state == "slow" else 40 + index * 25)
        if type(latency) is not int or not 1 <= latency <= 10000:
            latency = 1500
        return {
            "state": state,
            "delay": 0 if down else latency,
            "udp": bool(value.get("udp", True)) and not down,
        }

    def check_subscription(self, identity, *, updating=False):
        state = self.settings(identity)["subscription"]
        if updating and state == "unavailable":
            raise ClientError("Демо · сервер обновления недоступен.")
        if state in {"expired", "exhausted", "removed"}:
            raise SubscriptionUnavailable("missing" if state == "removed" else state)

    def apply_profile(self, profile):
        return subscription_profile(profile, self.settings(profile.id)["subscription"])
