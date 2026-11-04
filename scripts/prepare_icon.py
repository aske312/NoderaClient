"""Render the shared SVG into a multi-resolution Windows ICO (PNG frames)."""

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main():
    root = Path(__file__).resolve().parents[1] / "nodera/resources"
    renderer = QSvgRenderer(str(root / "nodera.svg"))
    if not renderer.isValid():
        raise RuntimeError("Invalid application SVG")
    frames = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        picture = QImage(size, size, QImage.Format.Format_ARGB32)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        renderer.render(painter)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not picture.save(buffer, "PNG"):
            raise RuntimeError("Could not render icon frame")
        frames.append((size, bytes(buffer.data())))
    offset = 6 + 16 * len(frames)
    headers = []
    for size, content in frames:
        headers.append(
            struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(content), offset)
        )
        offset += len(content)
    (root / "nodera.ico").write_bytes(
        struct.pack("<HHH", 0, 1, len(frames))
        + b"".join(headers)
        + b"".join(content for _, content in frames)
    )


if __name__ == "__main__":
    main()
