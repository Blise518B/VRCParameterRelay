"""Generates vrc_parameter_relay/assets/icon.ico (a toggle-switch glyph)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication

OUT = Path(__file__).parent.parent / "vrc_parameter_relay" / "assets" / "icon.ico"


def draw(size: int) -> QImage:
    """User's icon style: white disc, black circle ring, black glyph inside."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    black = QColor("#0b0b0b")

    # white disc with a thick black ring, close to the edge like the reference
    p.setBrush(QColor("#ffffff"))
    p.setPen(QPen(black, 15 * s))
    p.drawEllipse(QRectF(12 * s, 12 * s, 232 * s, 232 * s))

    # relay glyph inside: transmitting dot + two signal arcs, all black
    p.setPen(Qt.NoPen)
    p.setBrush(black)
    dot_c = (100 * s, 156 * s)
    p.drawEllipse(QRectF(dot_c[0] - 17 * s, dot_c[1] - 17 * s, 34 * s, 34 * s))

    arc_pen = QPen(black, 14 * s, Qt.SolidLine, Qt.RoundCap)
    p.setPen(arc_pen)
    p.setBrush(Qt.NoBrush)
    for radius in (48, 80):
        r = radius * s
        p.drawArc(QRectF(dot_c[0] - r, dot_c[1] - r, 2 * r, 2 * r), 12 * 16, 66 * 16)

    p.end()
    return img


def main() -> None:
    QApplication(sys.argv[:1])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = draw(256)
    if not img.save(str(OUT), "ICO"):
        raise SystemExit("failed to write ico")
    img.save(str(OUT.with_suffix(".png")), "PNG")
    favicon = OUT.parent.parent / "web" / "favicon.png"
    draw(64).save(str(favicon), "PNG")
    print(f"wrote {OUT} + {favicon}")


if __name__ == "__main__":
    main()
