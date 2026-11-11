"""Session-local health observations; automatic failover belongs to Mihomo groups."""

PROBE_PREFIX = "__nodera_health_"  # Hide legacy probe names in controller snapshots.


class RouteHealth:
    def __init__(self, cooldown=15, failure_threshold=3):
        self.cooldown = cooldown
        self.failure_threshold = failure_threshold
        self.failures = {}
        self.frozen = {}
        self.last_checked = {}

    def update(self, values, now):
        fresh = {}
        for name, value in values.items():
            stamp = value.get("checked_at")
            if isinstance(stamp, (int, float)):
                if stamp <= self.last_checked.get(name, float("-inf")):
                    continue
                self.last_checked[name] = stamp
            fresh[name] = value
        values = fresh
        healthy = {name for name, value in values.items() if value["value"] > 0}
        for name in list(self.frozen):
            if name in healthy:
                del self.frozen[name]
                self.failures.pop(name, None)
            elif name in values:
                self.frozen[name] = now + self.cooldown
        # A complete outage is not evidence against one particular route.
        if not healthy.difference(self.frozen):
            if values:
                self.failures.clear()
            return
        for name, value in values.items():
            if name in self.frozen:
                continue
            self.failures[name] = 0 if value["value"] > 0 else self.failures.get(name, 0) + 1
            if self.failures[name] >= self.failure_threshold:
                self.frozen[name] = now + self.cooldown
