"""Presentation of subscription data; no guesses about server-only settings."""

import math
import time
from urllib.parse import urlsplit

from nodera.models import Profile, Settings

STRATEGIES = {
    "select": "Ручной выбор",
    "fallback": "Резервирование",
    "url-test": "Лучший отклик",
    "load-balance": "Балансировка",
}


def effective_tun(config: dict, settings: Settings) -> bool:
    return settings.tun


def format_bytes(value: int) -> str:
    number = float(max(0, value))
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if number < 1024:
            return f"{number:.1f} {unit}" if unit != "Б" else f"{int(number)} Б"
        number /= 1024
    return f"{number:.1f} ПБ"


def quota_view(profile: Profile | None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    usage = profile.usage if profile else {}
    used = usage.get("upload", 0) + usage.get("download", 0)
    total, expiry = usage.get("total"), usage.get("expire")
    traffic = (
        "Не передан"
        if total is None
        else "Без лимита"
        if total == 0
        else format_bytes(max(0, total - used))
    )
    days = (
        "Не указан"
        if expiry is None
        else "Без срока"
        if expiry == 0
        else "Истёк"
        if expiry <= now
        else f"{math.ceil((expiry - now) / 86400)} дн."
    )
    return {
        "traffic": traffic,
        "used": used,
        "total": total,
        "days": days,
        "expiry": expiry,
        "exhausted": total is not None and total > 0 and used >= total,
        "expired": expiry is not None and expiry > 0 and expiry <= now,
    }


def rule_rows(config: dict) -> list[tuple[str, str, str]]:
    result = []
    for rule in config.get("rules", []):
        if not isinstance(rule, str):
            continue
        parts = rule.split(",")
        if parts[-1].lower() == "no-resolve":
            parts.pop()
        if len(parts) >= 2:
            result.append((parts[0], ",".join(parts[1:-1]) or "Все остальные запросы", parts[-1]))
    return result


def policy_view(profile: Profile | None) -> dict:
    config = profile.config if profile else {}
    if profile and profile.local_rules:
        config = {**config, "rules": list(profile.local_rules) + list(config.get("rules", []))}
    rows = rule_rows(config)
    groups = config.get("proxy-groups", [])
    dns = config.get("dns", {})
    resolvers = []
    nameservers = dns.get("nameserver", [])
    for value in nameservers if isinstance(nameservers, list) else []:
        if isinstance(value, str):
            # Resolver URLs may carry credentials or a private query path.
            try:
                host = urlsplit(value if "://" in value else "udp://" + value).hostname
            except ValueError:
                continue
            if host and host not in resolvers:
                resolvers.append(host)
    strategy = STRATEGIES.get(groups[0].get("type"), "Из профиля") if groups else "Не задана"
    final = next((target for kind, _, target in reversed(rows) if kind == "MATCH"), None)
    mode = config.get("mode", "rule")
    route = {"direct": "Прямое подключение", "global": "Глобальный режим"}.get(mode)
    if route is None:
        route = (
            "Остальное — напрямую"
            if final == "DIRECT"
            else "Остальное — блокируется"
            if final in {"REJECT", "REJECT-DROP"}
            else "По правилам подписки"
        )
    return {
        "strategy": strategy,
        "strategy_known": bool(groups and groups[0].get("type") in STRATEGIES),
        "routing": route,
        "rules": rows,
        "blocked": sum(target in {"REJECT", "REJECT-DROP"} for _, _, target in rows),
        "processes": sum(
            "PROCESS-" in kind or "PROCESS-" in condition for kind, condition, _ in rows
        ),
        "providers": len(config.get("rule-providers", {})),
        "resolvers": resolvers,
        "dns_enabled": bool(dns.get("enable")),
        "dns_mode": dns.get("enhanced-mode", "Не задан"),
        "route_dns": bool(dns.get("respect-rules")),
        "protocols": sorted(
            {str(node.get("type", "")).upper() for node in config.get("proxies", [])}
        ),
    }
