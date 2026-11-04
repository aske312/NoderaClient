"""Describe known transport hops without inventing an origin or an anycast CDN location."""

import re
from dataclasses import replace

from nodera.locations import country, infer_country

ROLES = {"relay": "Relay", "cdn": "CDN", "server": "Выход"}
DIRECT = {"DIRECT", "REJECT", "REJECT-DROP", "PASS"}


def hop(role, code="", source="unknown"):
    return {"role": role, "country": code if country(code) else "", "source": source}


def clean_hops(values, source):
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        return []
    return [
        dict(
            hop(
                item.get("role", "server") if item.get("role") in ROLES else "server",
                str(item.get("country", "")).upper(),
                source,
            ),
            **({"host": item["host"]} if isinstance(item.get("host"), str) else {}),
        )
        for item in values
        if isinstance(item, dict)
    ]


def node_route(profile, name):
    node = next(
        (item for item in profile.config.get("proxies", []) if item["name"] == name), {"name": name}
    )
    manual = clean_hops(profile.route_overrides.get(name), "manual")
    if manual:
        return manual, True
    extension = profile.config.get("x-nodera", {})
    metadata = extension.get("routes", {}) if isinstance(extension, dict) else {}
    explicit = clean_hops(metadata.get(name) if isinstance(metadata, dict) else None, "profile")
    explicit = explicit or clean_hops(node.get("x-nodera-route"), "profile")
    if explicit:
        return explicit, True
    # Relay labels only disclose the relay name, not the origin's location.
    parts = re.split(r"\s*(?:·\s*)?Relay:\s*", name, maxsplit=1, flags=re.IGNORECASE)
    base = parts[0]
    cdn = bool(re.search(r"\bCDN\b", base, re.IGNORECASE))
    relay = infer_country({"name": parts[1]}) if len(parts) > 1 else None
    arrows = re.split(r"\s*(?:→|->)\s*", base)
    if len(arrows) > 1 and not cdn and len(parts) == 1:
        result = []
        for index, piece in enumerate(arrows):
            location = infer_country({"name": piece})
            result.append(
                hop(
                    "server" if index == len(arrows) - 1 else "relay",
                    location["code"] if location else "",
                    "name",
                )
            )
        return result[:8], True
    legacy = profile.locations.get(name) if not cdn and len(parts) == 1 else None
    location = infer_country({**node, "name": base}, legacy)
    result = []
    if len(parts) > 1:
        result.append(hop("relay", relay["code"] if relay else "", "name" if relay else "unknown"))
    if cdn:
        result.append(hop("cdn"))
    result.append(
        hop(
            "server",
            location["code"] if location else "",
            "manual" if legacy else "name" if location else "unknown",
        )
    )
    # The subscription's Relay label is the ingress before CDN. Its country never
    # supplies the CDN/origin location; explicit per-route metadata takes priority.
    return result, True


def active_route_views(profile, groups, connected):
    if not connected or not profile or not groups or profile.config.get("mode") == "direct":
        return []
    root = groups[0]
    if profile.config.get("mode") == "global":
        root = next((group for group in groups if group["name"] == "GLOBAL"), {})
    nodes = {item["name"]: item for item in profile.config.get("proxies", [])}
    definitions = {item["name"]: item for item in profile.config.get("proxy-groups", [])}
    choices = {item["name"]: item.get("current", "") for item in groups}
    choices.update(root.get("choices", {}))

    def resolve(name, visited):
        if name in visited or len(visited) >= 16 or name in DIRECT:
            return [], False
        visited = visited | {name}
        group = definitions.get(name)
        if group:
            if group.get("type") == "relay":
                all_hops, known = [], True
                for member in group.get("proxies", []):
                    segment, ordered = resolve(member, visited)
                    all_hops.extend(segment)
                    known = known and ordered
                return all_hops[:8], known
            selected = choices.get(name)
            return resolve(selected, visited) if selected else ([], False)
        route, ordered = node_route(profile, name)
        server = nodes.get(name, {}).get("server")
        if isinstance(server, str):
            has_relay = bool(re.search(r"Relay:\s*", name, re.IGNORECASE))
            has_cdn = bool(re.search(r"\bCDN\b", name, re.IGNORECASE))
            node = nodes.get(name, {})
            cdn_host = None
            for key in ("ws-opts", "http-upgrade-opts", "xhttp-opts", "h2-opts"):
                options = node.get(key, {})
                if not isinstance(options, dict):
                    continue
                headers = options.get("headers", {})
                header_host = (
                    next(
                        (value for key, value in headers.items() if str(key).lower() == "host"),
                        None,
                    )
                    if isinstance(headers, dict)
                    else None
                )
                candidate_host = header_host or options.get("host")
                if isinstance(candidate_host, list):
                    candidate_host = next(
                        (value for value in candidate_host if isinstance(value, str)), None
                    )
                if isinstance(candidate_host, str) and candidate_host:
                    cdn_host = candidate_host
                    break
            cdn_host = cdn_host or node.get("servername") or node.get("sni")
            if has_cdn and isinstance(cdn_host, str):
                edge = next(
                    (item for item in route if item["role"] == "cdn" and not item.get("host")), None
                )
                if edge is not None:
                    edge["host"] = cdn_host
            role = (
                "cdn"
                if has_cdn and (not has_relay or server == cdn_host)
                else "relay"
                if has_relay
                else "server"
                if not has_cdn
                else None
            )
            candidate = next(
                (item for item in route if item["role"] == role and not item.get("host")), None
            )
            if candidate is not None:
                candidate["host"] = server
                candidate["entry"] = True
            elif role == "cdn":
                edge = next((item for item in route if item["role"] == "cdn"), None)
                if edge is not None:
                    edge["entry"] = True
        extension = profile.config.get("x-nodera", {})
        declared = extension.get("routes", {}) if isinstance(extension, dict) else {}
        if (
            clean_hops(profile.route_overrides.get(name), "manual")
            or clean_hops(nodes.get(name, {}).get("x-nodera-route"), "profile")
            or clean_hops(declared.get(name) if isinstance(declared, dict) else None, "profile")
        ):
            return route, ordered
        dialer = nodes.get(name, {}).get("dialer-proxy")
        if isinstance(dialer, str) and dialer not in DIRECT:
            prefix, known = resolve(dialer, visited)
            if not prefix:
                prefix = [hop("relay")]
            prefix = [
                {**item, "role": "relay" if item["role"] == "server" else item["role"]}
                for item in prefix
            ]
            for item in route:
                item.pop("entry", None)
            return (prefix + route)[:8], known and ordered
        return route, ordered

    names = root.get("active", [root.get("current", "")])
    if profile.config.get("mode", "rule") == "rule":
        for rule in reversed(profile.config.get("rules", [])):
            if isinstance(rule, str) and rule.startswith("MATCH,"):
                target = rule.split(",")[1].strip()
                if target in nodes:
                    names = [target]
                elif target in definitions and target != root.get("name"):
                    names = [target]
                break
    result = []
    for name in names:
        if not name or name in DIRECT:
            continue
        hops, known = resolve(name, set())
        if hops:
            result.append({"node": name, "hops": hops, "order_known": known})
    return result


def route_text(route):
    values = []
    for item in route["hops"]:
        location = country(item["country"])
        values.append(
            ROLES[item["role"]]
            + " · "
            + (location["name"] if location else "неизвестно")
            + (" (IP)" if item["source"] == "geoip" else "")
        )
    return (" → " if route["order_known"] else "  •  ").join(values)


def route_for_node(profile, name, choices=None):
    preview = replace(profile, config={**profile.config, "mode": "rule", "rules": []})
    routes = active_route_views(
        preview, [{"name": "preview", "active": [name], "choices": choices or {}}], True
    )
    return routes[0]["hops"] if routes else [hop("server")]
