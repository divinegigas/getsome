"""Steam library artwork for Neon Cable 42, drawn in code in the app's synthwave look
(sky, striped sun, neon grid, neon and chrome lettering, the "42" TV icon). Original; nothing
downloaded. render(kind) -> QImage for the steam_shortcut.ART_KINDS."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

SIZES = {"p": (600, 900), "": (920, 430), "_hero": (1920, 620), "_logo": (1280, 440), "_icon": (256, 256)}


def _app():
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QGuiApplication([])
    from .tv import overlays

    overlays.load_fonts()


def _tv_icon(size: int):
    from PySide6.QtGui import QImage

    from . import desktop

    f = Path(tempfile.gettempdir()) / f"nc42_tv_{size}.png"
    desktop.make_icon(f, size)
    img = QImage(str(f))
    f.unlink(missing_ok=True)
    return img


def _scene(p, w: int, h: int, horizon: float = 0.62, sun_x: float = 0.5, sun_r: float = 0.22) -> None:
    """Sky, striped sun and a perspective neon grid."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QLinearGradient, QPainterPath, QPen, QRadialGradient
    from .brand import QColor

    hy = h * horizon
    sky = QLinearGradient(0, 0, 0, hy)
    sky.setColorAt(0, QColor("#0a0024"))
    sky.setColorAt(0.55, QColor("#3a0a6e"))
    sky.setColorAt(1, QColor("#ff2b8a"))
    p.fillRect(QRectF(0, 0, w, hy), sky)
    # stars
    import random

    rnd = random.Random(42)
    p.setPen(Qt.NoPen)
    for _ in range(int(w * h / 9000)):
        x, y = rnd.random() * w, rnd.random() * hy * 0.7
        p.setBrush(QColor(255, 255, 255, rnd.randint(60, 200)))
        r = rnd.random() * 1.6 + 0.4
        p.drawEllipse(QPointF(x, y), r, r)
    # sun with the classic gaps
    r = min(w, h) * sun_r
    cx, cy = w * sun_x, hy - r * 0.35
    glow = QRadialGradient(QPointF(cx, cy), r * 2.2)
    glow.setColorAt(0, QColor(255, 120, 60, 120))
    glow.setColorAt(1, QColor(255, 43, 214, 0))
    p.setBrush(glow)
    p.drawEllipse(QPointF(cx, cy), r * 2.2, r * 2.2)
    sun = QLinearGradient(0, cy - r, 0, cy + r)
    sun.setColorAt(0, QColor("#ffe600"))
    sun.setColorAt(1, QColor("#ff2bd6"))
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    gaps = QPainterPath()
    for k in range(6):
        gy = cy + r * (0.1 + k * 0.16)
        gaps.addRect(QRectF(cx - r, gy, 2 * r, r * (0.025 + k * 0.012)))
    p.setBrush(sun)
    p.drawPath(path.subtracted(gaps))
    # floor + grid
    floor = QLinearGradient(0, hy, 0, h)
    floor.setColorAt(0, QColor("#1a0033"))
    floor.setColorAt(1, QColor("#05000f"))
    p.fillRect(QRectF(0, hy, w, h - hy), floor)
    pen = QPen(QColor(255, 43, 214, 200), max(1.5, h / 400))
    p.setPen(pen)
    for k in range(1, 14):
        t = (k / 13) ** 2.2
        y = hy + (h - hy) * t
        p.drawLine(QPointF(0, y), QPointF(w, y))
    p.setPen(QPen(QColor(0, 240, 255, 170), max(1.2, h / 500)))
    for k in range(-16, 17):
        p.drawLine(QPointF(cx + k * w * 0.012, hy), QPointF(cx + k * w * 0.16, h))
    p.setPen(QPen(QColor("#00f0ff"), max(2.0, h / 300)))
    p.drawLine(QPointF(0, hy), QPointF(w, hy))


def _title(p, w: int, x: float, y: float, px: int, anchor: str = "cm", lines: bool = False) -> None:
    from .tv.overlays import CYAN, PINK, _chrome_text, draw_neon, font
    from PySide6.QtGui import QFontMetricsF

    if lines:     # stacked, for the tall cover
        draw_neon(p, "NEON", "numbers", px, PINK, x, y, "cm", flicker=False)
        draw_neon(p, "CABLE", "numbers", px, CYAN, x, y + px * 1.05, "cm", flicker=False)
        big = int(px * 2.4)
        tw = QFontMetricsF(font("text", big)).horizontalAdvance("42")
        _chrome_text(p, "42", big, x - tw / 2, y + px * 1.05 + big * 1.05)
        return
    draw_neon(p, "NEON CABLE", "numbers", px, PINK, x, y, anchor, flicker=False)
    tw = QFontMetricsF(font("text", int(px * 1.3))).horizontalAdvance("42")
    _chrome_text(p, "42", int(px * 1.3), x - tw / 2 if anchor == "cm" else x, y + px * 1.55)


def _title_line(p, x: float, cy: float, px: int, max_w: float) -> None:
    """ "NEON CABLE" in pink neon followed by a big chrome "42", fitted to max_w, centred on cy."""
    from PySide6.QtGui import QFontMetricsF

    from .tv.overlays import PINK, _chrome_text, font, neon_image

    while px > 20:
        neon_w = neon_image("NEON CABLE", "numbers", px, PINK).width()
        big = int(px * 1.7)
        chrome_w = QFontMetricsF(font("text", big)).horizontalAdvance("42")
        if neon_w + px * 0.3 + chrome_w <= max_w:
            break
        px = int(px * 0.92)
    img = neon_image("NEON CABLE", "numbers", px, PINK)
    p.drawImage(int(x), int(cy - img.height() / 2), img)
    _chrome_text(p, "42", big, x + img.width() + px * 0.3, cy + big * 0.36)


def render(kind: str):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter

    _app()
    from .tv.overlays import CYAN, draw_neon

    w, h = SIZES[kind]
    if kind == "_icon":
        return _tv_icon(256)
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    if kind == "p":                         # tall cover in the library grid
        _scene(p, w, h, horizon=0.68, sun_r=0.30)
        _title(p, w, w / 2, h * 0.15, 96, lines=True)
        draw_neon(p, "CABLE TV SYSTEM", "text", 34, CYAN, w / 2, h * 0.93, "cm", flicker=False)
    elif kind == "":                        # wide capsule (recent games, Big Picture shelves)
        _scene(p, w, h, horizon=0.72, sun_x=0.80, sun_r=0.26)
        p.drawImage(QRectF(30, h * 0.12, 200, 200), _tv_icon(200))
        _title_line(p, 250, h * 0.36, 70, w - 280)
    elif kind == "_hero":                   # big banner behind the game page (Steam puts the logo on it)
        _scene(p, w, h, horizon=0.66, sun_x=0.68, sun_r=0.36)
    elif kind == "_logo":                   # transparent logo laid over the hero
        p.drawImage(QRectF(10, (h - 280) / 2, 280, 280), _tv_icon(280))
        _title_line(p, 320, h / 2, 130, w - 340)
    p.end()
    return img
