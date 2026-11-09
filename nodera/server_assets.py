"""Bundled protocol artwork and country flags shared with the server UI."""

import math
import re
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF, QRadialGradient
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

HIGH_LATENCY_MS = 500


def route_visual_state(data):
    if data.get("frozen"):
        return "frozen"
    delay = data.get("delay")
    if data.get("error") or type(delay) in (int, float) and delay <= 0:
        return "error"
    if type(delay) in (int, float) and delay >= HIGH_LATENCY_MS:
        return "slow"
    return "normal"


@lru_cache(maxsize=32)
def protocol_pixmap(protocol):
    asset = PROTOCOL_ASSETS.get(str(protocol).lower(), "Mi")
    return QPixmap(str(RESOURCE / "protocols" / f"{asset}.webp"))


@lru_cache(maxsize=128)
def protocol_status_pixmap(protocol, state):
    """Keep the protocol artwork visible under a static health effect."""
    canvas = QPixmap(80, 80)
    canvas.setDevicePixelRatio(2)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = {"frozen": "#6f9faf", "error": "#b26d73", "slow": "#b49764"}.get(state)
    if color:
        glow = QRadialGradient(QPointF(20, 20), 20)
        center_tint = QColor(color)
        center_tint.setAlpha(160)
        glow.setColorAt(0, center_tint)
        tint = QColor(color)
        tint.setAlpha(55)
        glow.setColorAt(0.55, tint)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(0, 0, 40, 40))
    artwork = protocol_pixmap(protocol)
    painter.drawPixmap(QRectF(6, 6, 28, 28), artwork, QRectF(artwork.rect()))
    if color:
        painter.setPen(QPen(QColor(color), 1))
        painter.setBrush(QColor(111, 159, 175, 28) if state == "frozen" else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(4.5, 4.5, 31, 31), 8, 8)
        painter.setBrush(QColor("#10232d"))
        painter.drawEllipse(QPointF(31, 31), 6, 6)
        painter.setPen(QPen(QColor(color), 1.25, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if state == "frozen":
            for angle in (0, math.pi / 3, 2 * math.pi / 3):
                direction = QPointF(math.cos(angle), math.sin(angle))
                painter.drawLine(QPointF(31, 31) - direction * 4, QPointF(31, 31) + direction * 4)
            # Crystalline highlights across the corners of the frozen artwork.
            painter.drawPolyline(QPolygonF([QPointF(8, 14), QPointF(12, 8), QPointF(18, 11)]))
            painter.drawLine(QPointF(12, 8), QPointF(14, 16))
        elif state == "error":
            painter.drawLine(QPointF(28.5, 28.5), QPointF(33.5, 33.5))
            painter.drawLine(QPointF(33.5, 28.5), QPointF(28.5, 33.5))
        elif state == "slow":
            painter.drawLine(QPointF(31, 27.5), QPointF(31, 31))
            painter.drawLine(QPointF(31, 31), QPointF(33.5, 32.5))
    painter.end()
    return canvas


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
