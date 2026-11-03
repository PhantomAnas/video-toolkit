"""Generate the app icon (PNG + ICO) with Qt only:  python scripts/make_icon.py"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "video_toolkit", "assets")


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0, QColor("#0ea5e9"))
    g.setColorAt(1, QColor("#7c3aed"))
    p.fillPath(tile, g)
    # Film strip
    strip = QPainterPath()
    strip.addRoundedRect(QRectF(s * 0.16, s * 0.28, s * 0.68, s * 0.44), s * 0.06, s * 0.06)
    p.fillPath(strip, QColor(255, 255, 255, 235))
    p.setBrush(QColor("#1e1b4b"))
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(5):
        x = s * (0.21 + i * 0.13)
        p.drawRoundedRect(QRectF(x, s * 0.31, s * 0.07, s * 0.06), s * 0.015, s * 0.015)
        p.drawRoundedRect(QRectF(x, s * 0.63, s * 0.07, s * 0.06), s * 0.015, s * 0.015)
    # Scissors cut line + play
    p.setPen(QPen(QColor("#1e1b4b"), max(1.0, s * 0.03), Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(s * 0.5, s * 0.22), QPointF(s * 0.5, s * 0.78))
    tri = QPainterPath()
    tri.moveTo(QPointF(s * 0.56, s * 0.43))
    tri.lineTo(QPointF(s * 0.70, s * 0.50))
    tri.lineTo(QPointF(s * 0.56, s * 0.57))
    tri.closeSubpath()
    p.fillPath(tri, QColor("#7c3aed"))
    p.end()
    return img


def main() -> None:
    QGuiApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)
    draw(256).save(os.path.join(OUT, "icon.png"))
    draw(256).save(os.path.join(OUT, "icon.ico"), "ICO")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
