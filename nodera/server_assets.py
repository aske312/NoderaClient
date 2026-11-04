"""Bundled protocol artwork and country flags shared with the server UI."""

import re
from functools import lru_cache

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from nodera.locations import RESOURCE, country_hints
from nodera.routes import route_for_node

PROTOCOL_ASSETS = {
    "vless": "VLESS",
    "hysteria2": "HY2",
    "hy2": "HY2",
    "tuic": "TUIC",
    "ss": "SS",
    "shadowsocks": "SS",
    "shadowsocksr": "SS",
    "trojan": "TRJ",
    "wireguard": "WG",
    "amneziawg": "AWG",
}


@lru_cache(maxsize=32)
def protocol_pixmap(protocol):
    asset = PROTOCOL_ASSETS.get(str(protocol).lower(), "Mi")
    return QPixmap(str(RESOURCE / "protocols" / f"{asset}.webp"))


@lru_cache(maxsize=300)
def flag_pixmap(code):
    # Codes are identifiers, never paths supplied by a subscription.
    code = str(code).lower()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        code = "xx"
    path = RESOURCE / "flags" / f"{code}.svg"
    if not path.is_file():
        path = RESOURCE / "flags" / "xx.svg"
    renderer = QSvgRenderer(str(path))
    pixmap = QPixmap(QSize(48, 36))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def node_countries(profile, name, geo=None, choices=None):
    hops = route_for_node(profile, name, choices)
    observations = (geo or {}).get("rows", [])
    codes = [hop.get("country", "") for hop in hops]
    codes += [row.get("country", "") for row in observations]
    if not profile.route_overrides.get(name):
        codes += sorted(country_hints(name))
        for first, second in re.findall(
            r"([\U0001f1e6-\U0001f1ff])([\U0001f1e6-\U0001f1ff])", name
        ):
            codes.append("".join(chr(ord(char) - 0x1F1E6 + 65) for char in (first, second)))
        node = next(
            (item for item in profile.config.get("proxies", []) if item["name"] == name), {}
        )
        codes += [node.get(key, "") for key in ("country", "country-code", "country_code")]
    result = list(
        dict.fromkeys(
            code.upper()
            for code in codes
            if isinstance(code, str)
            and len(code) == 2
            and code.isascii()
            and code.isalpha()
            and (RESOURCE / "flags" / f"{code.lower()}.svg").is_file()
        )
    )
    if any(
        not hop.get("country")
        and not any(row.get("step") == step and row.get("country") for row in observations)
        for step, hop in enumerate(hops)
    ):
        result.append("XX")
    return list(dict.fromkeys(result)) or ["XX"]
