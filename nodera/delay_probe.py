"""Bounded per-route probes without changing a group's automatic selection."""

import time
from concurrent.futures import ThreadPoolExecutor

from nodera.controller import Controller
from nodera.models import ClientError


def probe_delays(port, secret, names, cancelled=lambda: False, aliases=None):
    def check(name):
        if cancelled():
            return name, None
        try:
            with Controller(port, secret) as controller:
                value = controller.delay((aliases or {}).get(name, name))
        except ClientError:
            value = 0
        return name, {"value": value, "checked_at": time.time()}

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="route-delay") as pool:
        return {
            name: value
            for name, value in pool.map(check, dict.fromkeys(names))
            if value is not None
        }
