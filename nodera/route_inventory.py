"""Deduplicated, offline summary of known subscription endpoints."""

import ipaddress

from nodera.geolocation import endpoint_rows
from nodera.routes import route_for_node


def route_inventory(profile, results, choices=None, cache=None):
    if not profile:
        return []
    cache = cache or {}
    inventory = {}
    by_host = {}
    for result in results.values():
        for observation in result.get("rows", []):
            by_host.setdefault(observation.get("host"), []).append(observation)
    for node in profile.config.get("proxies", []):
        name = node["name"]
        hops = route_for_node(profile, name, choices)
        endpoints = endpoint_rows(profile, name, choices)
        observations = results.get(name, {}).get("rows", [])
        for endpoint in endpoints:
            matches = [
                row for row in observations if row.get("host") == endpoint["host"]
            ] or by_host.get(endpoint["host"], [])
            if not matches:
                try:
                    address = str(ipaddress.ip_address(endpoint["host"]))
                except ValueError:
                    address = ""
                matches = [{**endpoint, **cache.get(address, {}), "ip": address}]
            for row in matches:
                key = row.get("ip") or endpoint["host"]
                entry = inventory.setdefault(
                    key,
                    {"host": endpoint["host"], "roles": set(), "nodes": set(), "countries": set()},
                )
                entry["roles"].add(endpoint["role"])
                entry["nodes"].add(name)
                if row.get("country"):
                    entry["countries"].add(row["country"])
                elif endpoint.get("step") is not None and endpoint["step"] < len(hops):
                    hint = hops[endpoint["step"]].get("country")
                    if hint:
                        entry["countries"].add(hint)
                        entry.setdefault("status", "Страна из профиля/названия; IP не проверен")
                if row.get("timestamp", 0) >= entry.get("timestamp", 0):
                    entry.update(
                        {
                            key: value
                            for key, value in row.items()
                            if key
                            in {
                                "ip",
                                "country",
                                "city",
                                "org",
                                "asn",
                                "timestamp",
                                "status",
                                "provider",
                            }
                        }
                    )
        for hop in hops:
            if hop.get("host"):
                continue
            key = ("undisclosed", hop["role"], hop.get("country", ""))
            entry = inventory.setdefault(
                key,
                {"host": "Адрес не передан", "roles": set(), "nodes": set(), "countries": set()},
            )
            entry["roles"].add(hop["role"])
            entry["nodes"].add(name)
            if hop.get("country"):
                entry["countries"].add(hop["country"])
    return list(inventory.values())
