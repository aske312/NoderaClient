"""Small native installer artwork rendered from Nodera's existing vector assets."""

import json
import os
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

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    if os.name == "nt":
        for filename in ("segoeui.ttf", "seguisb.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    destination = ROOT / "build/installer"
    destination.mkdir(parents=True, exist_ok=True)
    logo = QSvgRenderer(str(ROOT / "nodera/resources/nodera.svg"))
    if not logo.isValid():
        raise RuntimeError("Invalid Nodera SVG")
    background = QImage(1192, 864, QImage.Format.Format_RGB32)
    painter = QPainter(background)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 1192, 864)
    gradient.setColorAt(0, QColor("#0c202b"))
    gradient.setColorAt(1, QColor("#071019"))
    painter.fillRect(background.rect(), gradient)
    painter.setPen(QPen(QColor(75, 154, 163, 14), 1))
    for x in range(0, 1192, 48):
        painter.drawLine(x, 0, x, 864)
    for y in range(0, 864, 48):
        painter.drawLine(0, y, 1192, y)
    data = json.loads((ROOT / "nodera/resources/world-map.json").read_text(encoding="utf-8"))
    path = QPainterPath()
    for ring in data["land"]:
        path.moveTo((ring[0][0] + 180) * 3.2 + 20, (85 - ring[0][1]) * 3.2 + 330)
        for lon, lat in ring[1:]:
            path.lineTo((lon + 180) * 3.2 + 20, (85 - lat) * 3.2 + 330)
        path.closeSubpath()
    painter.setPen(QPen(QColor(93, 190, 190, 22), 1))
    painter.setBrush(QColor(57, 109, 124, 12))
    painter.drawPath(path)
    painter.end()
    background.save(str(destination / "background.png"))

    side = QImage(492, 942, QImage.Format.Format_RGB32)
    side.fill(QColor("#0a1821"))
    painter = QPainter(side)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    logo.render(painter, QRectF(104, 205, 284, 284))
    painter.setPen(QColor("#e4eff2"))
    painter.setFont(QFont("Segoe UI", 32, QFont.Weight.DemiBold))
    painter.drawText(QRectF(20, 515, 452, 85), Qt.AlignmentFlag.AlignCenter, "Nodera")
    painter.setPen(QColor("#7e9aa8"))
    painter.setFont(QFont("Segoe UI", 12))
    painter.drawText(
        QRectF(24, 610, 444, 50), Qt.AlignmentFlag.AlignCenter, "Ваш маршрут. Ваш выбор."
    )
    painter.setPen(QColor("#69c8aa"))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(QRectF(20, 825, 452, 40), Qt.AlignmentFlag.AlignCenter, "WINDOWS · x64")
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
