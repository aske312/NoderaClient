from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


class ClientError(Exception):
    """An error safe to display without exposing credentials."""


def visible_proxies(config):
    """Transport dependencies stay in Mihomo but are never selectable/tested alone."""
    nodes = config.get("proxies", [])
    dependencies = {node.get("dialer-proxy") for node in nodes if isinstance(node, dict)}
    return [
        node
        for node in nodes
        if isinstance(node, dict)
        and not (
            str(node.get("name", "")).startswith("__312_transport_")
            and node.get("name") in dependencies
        )
    ]


@dataclass
class Profile:
    name: str
    url: str
    config: dict[str, Any]
    updated_at: str
    usage: dict[str, int] = field(default_factory=dict)
    selections: dict[str, str] = field(default_factory=dict)
    locations: dict[str, str] = field(default_factory=dict)
    route_overrides: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    local_rules: list[str] = field(default_factory=list)
    forced_server: str = ""
    settings_initialized: bool = True
    channel_capabilities: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ProfileLibrary:
    profiles: list[Profile] = field(default_factory=list)
    active_id: str | None = None

    @property
    def active(self):
        return next((profile for profile in self.profiles if profile.id == self.active_id), None)

    def upsert(self, profile, activate=True):
        profiles = [item for item in self.profiles if item.id != profile.id]
        index = next(
            (i for i, item in enumerate(self.profiles) if item.id == profile.id), len(profiles)
        )
        profiles.insert(index, profile)
        return ProfileLibrary(profiles, profile.id if activate else self.active_id)


@dataclass
class Settings:
    device_id: str = field(default_factory=lambda: str(uuid4()))
    mixed_port: int = 0
    tun: bool = True
    system_proxy: bool = True
    update_hours: int = 6
    sidebar_collapsed: bool = False
    poll_seconds: int = 3
    log_level: str = "info"
    log_limit: int = 2000
    save_logs: bool = True
    geo_auto: bool = True
    tun_stack: str = "profile"
    tun_mtu: int = 0
    ipv6_mode: str = "profile"
    close_to_tray: bool = True
    auto_connect: bool = False
    tray_behavior_version: int = 1
    flow_poll_seconds: int = 1
    flow_history_minutes: int = 5
    save_connection_logs: bool = True
    connection_log_mb: int = 8
    connection_log_interval: int = 15
    map_auto_zoom: bool = True
    client_point: dict[str, Any] = field(default_factory=dict)
    client_location_attempted: bool = False
    channel_preferences: dict[str, dict[str, Any]] = field(default_factory=dict)
