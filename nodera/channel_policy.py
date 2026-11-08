"""Client constraints for selecting from channels granted by a subscription."""

from dataclasses import dataclass, field

from nodera.models import ClientError, visible_proxies

PROTOCOLS = (
    "vless",
    "vmess",
    "trojan",
    "ss",
    "ssr",
    "hysteria2",
    "hysteria",
    "tuic",
    "anytls",
    "wireguard",
    "socks5",
    "http",
    "snell",
    "ssh",
    "mieru",
)
NETWORKS = ("", "tcp", "ws", "grpc", "h2", "http", "httpupgrade", "xhttp", "quic", "udp")
PROTOCOL_LABELS = {
    "vless": "VLESS",
    "vmess": "VMess",
    "trojan": "Trojan",
    "ss": "Shadowsocks",
    "ssr": "ShadowsocksR",
    "hysteria2": "Hysteria 2",
    "hysteria": "Hysteria",
    "tuic": "TUIC",
    "anytls": "AnyTLS",
    "wireguard": "WireGuard",
    "socks5": "SOCKS5",
    "http": "HTTP",
    "snell": "Snell",
    "ssh": "SSH",
    "mieru": "Mieru",
}


def node_network(node):
    protocol = str(node.get("type", "")).lower()
    default = (
        "quic"
        if protocol in {"hysteria", "hysteria2", "tuic"}
        else "udp"
        if protocol == "wireguard"
        else "tcp"
    )
    return str(node.get("network", default)).lower()


def channel_capabilities(config, saved=None):
    nodes = visible_proxies(config)
    protocols = {str(n.get("type", "")).lower() for n in nodes}
    networks = {node_network(n) for n in nodes}
    saved = saved if isinstance(saved, dict) else {}
    protocols.update(p for p in saved.get("protocols", []) if isinstance(p, str))
    networks.update(n for n in saved.get("networks", []) if isinstance(n, str))
    return {
        "protocols": [p for p in PROTOCOLS if p in protocols],
        "networks": [n for n in NETWORKS if n and n in networks],
    }


@dataclass
class ChannelPolicy:
    max_channels: int = 0
    protocols: list[str] = field(default_factory=list)
    network: str = ""
    require_tls: bool = False

    def validate(self):
        if (
            type(self.max_channels) is not int
            or not 0 <= self.max_channels <= 64
            or not isinstance(self.protocols, list)
            or any(not isinstance(p, str) or p not in PROTOCOLS for p in self.protocols)
            or len(self.protocols) != len(set(self.protocols))
            or self.network not in NETWORKS
            or type(self.require_tls) is not bool
        ):
            raise ClientError("Некорректные параметры подбора каналов.")
        return self

    def matches(self, node):
        protocol = node.get("type", "").lower()
        network = node_network(node)
        tls = node.get("tls") is True or protocol in {
            "trojan",
            "hysteria",
            "hysteria2",
            "tuic",
            "anytls",
        }
        return (
            (not self.protocols or protocol in self.protocols)
            and (not self.network or network == self.network)
            and (not self.require_tls or tls)
        )


def valid_preferences(preferences):
    if not isinstance(preferences, dict):
        return False
    try:
        for key, value in preferences.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                return False
            ChannelPolicy(**value).validate()
    except (TypeError, ClientError):
        return False
    return True
