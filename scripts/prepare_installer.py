"""Small native installer artwork rendered from Nodera's existing vector assets."""

import json
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtSvg import QSvgRenderer

from nodera.versioning import build_info

ROOT = Path(__file__).resolve().parents[1]


def rtf_escape(text):
    return "".join(
        "\\" + character
        if character in "\\{}"
        else f"\\u{ord(character) if ord(character) < 32768 else ord(character) - 65536}?"
        if ord(character) > 127
        else character
        for character in text
    )


def license_rtf(terms, license_text):
    parts = [
        r"{\rtf1\ansi\deff0{\fonttbl{\f0 Segoe UI;}}",
        r"{\colortbl;\red228\green239\blue242;}",
        r"\viewkind4\uc1\pard\f0\cf1\fs22\li220\ri220\sl300\slmult1 ",
    ]
    for line in terms.splitlines():
        if not line.strip():
            continue
        heading = re.match(r"^\d+\. ", line) or line.startswith("NODERA")
        style = r"\sb260\sa160\b\fs25 " if heading else r"\sb0\sa180\b0\fs22 "
        parts.extend((style, rtf_escape(line), r"\par "))
    parts.append(r"\sb320\sa180\b\fs25 MIT License\par\b0\fs22 ")
    for paragraph in license_text.removeprefix("MIT License").strip().split("\n\n"):
        parts.extend((r"\sb0\sa180 ", rtf_escape(" ".join(paragraph.splitlines())), r"\par "))
    return "".join(parts) + "}"


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    if os.name == "nt":
        for filename in ("segoeui.ttf", "seguisb.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    destination = ROOT / "build/installer"
    destination.mkdir(parents=True, exist_ok=True)
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    for language in ("ru", "en"):
        terms = (ROOT / f"packaging/license-{language}.txt").read_text(encoding="utf-8")
        (destination / f"license-{language}.rtf").write_text(
            license_rtf(terms, license_text), encoding="ascii"
        )
    logo = QSvgRenderer(str(ROOT / "nodera/resources/nodera.svg"))
    if not logo.isValid():
        raise RuntimeError("Invalid Nodera SVG")
    background = QImage(1192, 784, QImage.Format.Format_RGB32)
    painter = QPainter(background)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, background.width(), background.height())
    gradient.setColorAt(0, QColor("#0c202b"))
    gradient.setColorAt(1, QColor("#071019"))
    painter.fillRect(background.rect(), gradient)
    painter.setPen(QPen(QColor(75, 154, 163, 14), 1))
    for x in range(0, background.width(), 48):
        painter.drawLine(x, 0, x, background.height())
    for y in range(0, background.height(), 48):
        painter.drawLine(0, y, background.width(), y)
    data = json.loads((ROOT / "nodera/resources/world-map.json").read_text(encoding="utf-8"))
    path = QPainterPath()
    for ring in data["land"]:
        path.moveTo((ring[0][0] + 180) * 3.2 + 20, (85 - ring[0][1]) * 3.2 + 330)
        for lon, lat in ring[1:]:
            path.lineTo((lon + 180) * 3.2 + 20, (85 - lat) * 3.2 + 330)
        path.closeSubpath()
    painter.setPen(QPen(QColor(93, 190, 190, 34), 1))
    painter.setBrush(QColor(57, 109, 124, 18))
    painter.drawPath(path)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    route = QPainterPath()
    route.moveTo(405, 770)
    route.cubicTo(620, 430, 930, 940, 1140, 490)
    painter.setPen(QPen(QColor(105, 200, 170, 65), 2))
    painter.drawPath(route)
    for x, y in ((405, 770), (1140, 490)):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(105, 200, 170, 18))
        painter.drawEllipse(QRectF(x - 16, y - 16, 32, 32))
        painter.setBrush(QColor(105, 200, 170, 110))
        painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
    painter.end()
    background.save(str(destination / "background.png"))

    side = QImage(492, 942, QImage.Format.Format_RGB32)
    side.fill(QColor("#0a1821"))
    painter = QPainter(side)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Route arcs echo the map in the client, with the shared mark at their centre.
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for radius, alpha in ((155, 35), (205, 23), (265, 15)):
        painter.setPen(QPen(QColor(105, 200, 170, alpha), 1.5))
        painter.drawEllipse(QRectF(246 - radius, 340 - radius, radius * 2, radius * 2))
    route = QPainterPath()
    route.moveTo(48, 455)
    route.cubicTo(115, 150, 370, 560, 450, 235)
    painter.setPen(QPen(QColor("#408f91"), 2))
    painter.drawPath(route)
    for x, y in ((48, 455), (450, 235)):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(105, 200, 170, 25))
        painter.drawEllipse(QRectF(x - 13, y - 13, 26, 26))
        painter.setBrush(QColor("#69c8aa"))
        painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
    logo.render(painter, QRectF(119, 213, 254, 254))
    painter.setPen(QColor("#e4eff2"))
    painter.setFont(QFont("Segoe UI", 40, QFont.Weight.DemiBold))
    painter.drawText(QRectF(20, 515, 452, 85), Qt.AlignmentFlag.AlignCenter, "Nodera")
    painter.setFont(QFont("Segoe UI", 18))
    version_rect = QRectF(20, side.height() - 43, 452, 34)
    metadata_path = ROOT / "nodera/resources/build-info.json"
    identity = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.is_file()
        else build_info()
    )
    version_text = f"v{identity['version']} build:{identity['build']}"
    # A dark upper edge and a light lower edge make the text look debossed.
    for offset, color in ((2, "#24404b"), (-2, "#020b11"), (0, "#4c6671")):
        painter.setPen(QColor(color))
        painter.drawText(
            version_rect.translated(0, offset), Qt.AlignmentFlag.AlignCenter, version_text
        )
    painter.end()
    side.save(str(destination / "side.png"))

    mark = QImage(116, 116, QImage.Format.Format_ARGB32)
    mark.fill(Qt.GlobalColor.transparent)
    painter = QPainter(mark)
    logo.render(painter, QRectF(0, 0, 116, 116))
    painter.end()
    mark.save(str(destination / "mark.png"))
    print("Prepared Nodera installer artwork")
    del app


if __name__ == "__main__":
    main()
