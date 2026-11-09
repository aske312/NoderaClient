"""Report the default current route, independently of other traffic groups."""

from nodera.server_assets import HIGH_LATENCY_MS


def current_route(profile, groups):
    if not profile:
        return ""
    if profile.forced_server:
        return profile.forced_server
    mode = profile.config.get("mode", "rule")
    if mode == "direct":
        return "DIRECT"
    root = (
        next((g for g in groups if g["name"] == "GLOBAL"), {})
        if mode == "global"
        else (groups[0] if groups else {})
    )
    name = root.get("name")
    if mode == "rule":
        targets = {node["name"] for node in profile.config.get("proxies", [])}
        targets.update(group["name"] for group in groups)
        for rule in reversed(profile.config.get("rules", [])):
            if isinstance(rule, str) and rule.startswith("MATCH,"):
                target = rule.split(",")[1].strip()
                # MATCH,DIRECT describes bypass traffic, not the server shown on the map.
                if target in targets and target not in {"DIRECT", "REJECT", "REJECT-DROP", "PASS"}:
                    name = target
                break
    choices = {g["name"]: g.get("current", "") for g in groups}
    choices.update(root.get("choices", {}))
    visited = set()
    while name in choices:
        if name in visited:
            return None
        visited.add(name)
        name = choices[name]
    return name or None


def network_quality(connected, probe_state, route_name, delays, profile_id, results, now):
    if not connected:
        return "off", "Подключение отключено."
    if route_name not in {None, "", "DIRECT", "REJECT", "REJECT-DROP", "PASS"}:
        sample = delays.get((profile_id, route_name), {})
        if "value" not in sample or not 0 <= now - sample.get("checked_at", 0) <= 45:
            return "unverified", "Текущий маршрут ещё не проверен."
        if sample["value"] <= 0:
            return "error", "Текущий маршрут не отвечает."
        if any(not success and 0 <= now - stamp <= 60 for stamp, success in results):
            return "unstable", "Текущий маршрут работает с обрывами."
        if sample["value"] >= HIGH_LATENCY_MS:
            return "slow", "Текущий маршрут отвечает медленно."
        return "ok", "Текущий маршрут работает."
    if route_name is None:
        return "unverified", "Текущий маршрут ещё не определён."
    if probe_state != "ok":
        return probe_state, {
            "checking": "Проверяем доступ через Nodera.",
            "error": "Сеть не отвечает. Проверьте маршруты.",
            "unverified": "Доступ в сеть ещё не проверен.",
        }.get(probe_state, "Доступ в сеть ещё не проверен.")
    if any(not success and 0 <= now - stamp <= 60 for stamp, success in results):
        return "unstable", "Связь есть, но возможны обрывы."
    return "ok", "Доступ в сеть проверен."
