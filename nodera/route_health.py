"""Session-local quarantine, confirmed by repeated failure with a healthy alternative."""

import copy
import hashlib
import re

PROBE_PREFIX = "__nodera_health_"


def probe_name(name):
    return PROBE_PREFIX + hashlib.sha256(name.encode()).hexdigest()[:20]


class RouteHealth:
    def __init__(self, cooldown=120):
        self.cooldown = cooldown
        self.failures = {}
        self.frozen = {}

    def update(self, values, now):
        healthy = {name for name, value in values.items() if value["value"] > 0}
        for name in list(self.frozen):
            if now >= self.frozen[name] and name in healthy:
                del self.frozen[name]
                self.failures.pop(name, None)
        # A recovering probe still in quarantine cannot justify blocking the
        # last route that remains eligible for normal connections.
        if not healthy.difference(self.frozen):
            self.failures.clear()
            return
        for name, value in values.items():
            if name in self.frozen:
                continue
            self.failures[name] = 0 if value["value"] > 0 else self.failures.get(name, 0) + 1
            if self.failures[name] >= 2:
                self.frozen[name] = now + self.cooldown


def quarantine_config(config, frozen):
    result = copy.deepcopy(config)
    if not frozen:
        return result
    originals = []
    for index, node in enumerate(result.get("proxies", [])):
        if node.get("name") in frozen:
            originals.append({**node, "name": probe_name(node["name"])})
            result["proxies"][index] = {"name": node["name"], "type": "reject"}
    result.setdefault("proxies", []).extend(originals)
    for group in result.get("proxy-groups", []):
        # Probe copies stay outside automatic/include-all groups and the UI.
        exclude = "^" + re.escape(PROBE_PREFIX)
        if group.get("exclude-filter"):
            exclude = "(?:" + group["exclude-filter"] + ")|" + exclude
        group["exclude-filter"] = exclude
    return result
