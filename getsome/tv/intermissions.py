"""More intermission looks, drawn live like the bumpers (all original designs).

  1  SUNSET      - the original: synthwave sun, neon countdown (in bumpers.py)
  2  FILM LEADER - a projector countdown leader: sweeping circle, sprockets, grain
  3  MARQUEE     - a movie-palace marquee with chasing bulbs in front of a stage curtain
  4  HYPERSPACE  - a warp-speed starfield with a cockpit-style countdown ring
  5  TELETEXT    - a blocky text-service page listing what's on next

Each gets `info` (title, episode, start, later, channel, upcoming = [(time, title), ...]),
`t` (seconds since the intermission began) and `remaining` (seconds to the feature).
"""

from __future__ import annotations

import datetime as dt
import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

from .. import paths
from .overlays import CYAN, PINK, YELLOW, _chrome_text, draw_neon, font

STYLES = 5


def countdown(remaining: float) -> str:
    rem = max(0, int(remaining))
    mm, ss = divmod(rem, 60)
    hh, mm = divmod(mm, 60)
    return f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"


def _fit_px(text: str, kind: str, px: int, max_w: float, floor: int = 30) -> int:
    while QFontMetricsF(font(kind, px)).horizontalAdvance(text) > max_w and px > floor:
        px = int(px * 0.92)
    return px


def _centred(p: QPainter, text: str, px: int, cx: float, baseline: float, color: QColor, kind: str = "text",
             shadow: QColor | None = None) -> None:
    p.setFont(font(kind, px))
    x = cx - QFontMetricsF(p.font()).horizontalAdvance(text) / 2
    if shadow is not None:
        p.setPen(shadow)
        p.drawText(QPointF(x + px * 0.05, baseline + px * 0.05), text)
    p.setPen(color)
    p.drawText(QPointF(x, baseline), text)


# --------------------------------------------------------------------------- 2 FILM LEADER

CREAM = QColor(240, 226, 196)


def film_leader(p: QPainter, w: int, h: int, info: dict, t: float, remaining: float) -> None:
    unit = h / 1080.0
    frame = int(t * 24)
    rng = random.Random(frame)
    flick = 0.88 + 0.12 * rng.random()
    p.fillRect(QRectF(0, 0, w, h), QColor(int(34 * flick), int(29 * flick), int(24 * flick)))
    # vignette
    vg = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.7)
    vg.setColorAt(0.55, QColor(0, 0, 0, 0))
    vg.setColorAt(1.0, QColor(0, 0, 0, 200))
    p.fillRect(QRectF(0, 0, w, h), vg)

    cx, cy, r = w / 2, h * 0.45, h * 0.27
    # crosshair and rings
    p.setPen(QPen(QColor(CREAM.red(), CREAM.green(), CREAM.blue(), 150), 3 * unit))
    p.drawLine(QPointF(cx, cy - r * 1.35), QPointF(cx, cy + r * 1.35))
    p.drawLine(QPointF(cx - r * 1.9, cy), QPointF(cx + r * 1.9, cy))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(CREAM, 6 * unit))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.setPen(QPen(CREAM, 3 * unit))
    p.drawEllipse(QPointF(cx, cy), r * 0.82, r * 0.82)
    # the sweep: one turn per second
    frac = remaining % 1.0
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 46))
    p.drawPie(QRectF(cx - r * 0.82, cy - r * 0.82, r * 1.64, r * 1.64), 90 * 16, int(-(1 - frac) * 360 * 16))
    # big number: minutes while there are minutes, then seconds
    rem = max(0, int(math.ceil(remaining)))
    big, small = (str(rem // 60), "MIN") if rem >= 120 else (str(rem), "SEC")
    px = int(r * 1.05)
    _centred(p, big, px, cx, cy + px * 0.36, QColor(20, 16, 12), "numbers")
    _centred(p, big, px, cx - 3 * unit, cy + px * 0.36 - 3 * unit, CREAM, "numbers")
    _centred(p, small, int(30 * unit), cx, cy + r * 0.62, CREAM)

    # sprocket holes running down both edges
    hole_h, gap = 70 * unit, 120 * unit
    off = (t * 24 * 18 * unit) % gap
    p.setBrush(QColor(8, 6, 4))
    for x in (22 * unit, w - 22 * unit - 46 * unit):
        y = -gap + off
        while y < h:
            p.drawRoundedRect(QRectF(x, y, 46 * unit, hole_h), 8 * unit, 8 * unit)
            y += gap
    # text
    _centred(p, "FEATURE PRESENTATION" if info.get("movie", True) else "COMING UP NEXT", int(54 * unit), cx, h * 0.10, CREAM, shadow=QColor(0, 0, 0, 180))
    title = info.get("title") or "THE FEATURE"
    tpx = _fit_px(title, "text", int(84 * unit), w * 0.8)
    _centred(p, title, tpx, cx, h * 0.86, QColor(255, 196, 92), shadow=QColor(0, 0, 0, 200))
    sub = "  ·  ".join(x for x in (f"STARTS {info['start']}" if info.get("start") else "", countdown(remaining)) if x)
    _centred(p, sub, int(38 * unit), cx, h * 0.93, CREAM)
    # grain, dust and the odd scratch
    p.setPen(Qt.NoPen)
    for _ in range(260):
        a = rng.randint(10, 60)
        p.setBrush(QColor(255, 255, 255, a) if rng.random() < 0.5 else QColor(0, 0, 0, a + 40))
        s = rng.uniform(1, 3.5) * unit
        p.drawEllipse(QPointF(rng.uniform(0, w), rng.uniform(0, h)), s, s)
    if rng.random() < 0.35:
        x = rng.uniform(w * 0.1, w * 0.9)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1.5 * unit))
        p.drawLine(QPointF(x, 0), QPointF(x + rng.uniform(-8, 8), h))


# --------------------------------------------------------------------------- 3 MARQUEE

def marquee(p: QPainter, w: int, h: int, info: dict, t: float, remaining: float) -> None:
    unit = h / 1080.0
    # stage curtain: soft vertical folds that sway a little
    folds = 18
    for i in range(folds):
        x0, x1 = w * i / folds, w * (i + 1) / folds
        g = QLinearGradient(x0, 0, x1, 0)
        sway = 0.08 * math.sin(t * 0.7 + i)
        g.setColorAt(0.0, QColor(70, 0, 40))
        g.setColorAt(0.5 + sway, QColor(150, 10, 80))
        g.setColorAt(1.0, QColor(60, 0, 35))
        p.fillRect(QRectF(x0, 0, x1 - x0 + 1, h), g)
    top = QLinearGradient(0, 0, 0, h * 0.18)
    top.setColorAt(0, QColor(10, 0, 20, 230))
    top.setColorAt(1, QColor(10, 0, 20, 0))
    p.fillRect(QRectF(0, 0, w, h * 0.18), top)
    floor = QLinearGradient(0, h * 0.8, 0, h)
    floor.setColorAt(0, QColor(10, 0, 20, 0))
    floor.setColorAt(1, QColor(10, 0, 20, 240))
    p.fillRect(QRectF(0, h * 0.8, w, h * 0.2), floor)

    # the marquee board
    bx, by, bw, bh = w * 0.09, h * 0.12, w * 0.82, h * 0.50
    board = QRectF(bx, by, bw, bh)
    p.setPen(QPen(QColor(255, 210, 120), 4 * unit))
    g = QLinearGradient(0, by, 0, by + bh)
    g.setColorAt(0, QColor(28, 10, 40))
    g.setColorAt(1, QColor(12, 4, 22))
    p.setBrush(g)
    p.drawRoundedRect(board, 18 * unit, 18 * unit)
    # chasing bulbs round the edge
    step = 46 * unit
    pts = []
    x = bx + step / 2
    while x < bx + bw:
        pts.append(QPointF(x, by + 20 * unit))
        x += step
    y = by + step / 2 + 20 * unit
    while y < by + bh - 20 * unit:
        pts.append(QPointF(bx + bw - 20 * unit, y))
        y += step
    x = bx + bw - step / 2
    while x > bx:
        pts.append(QPointF(x, by + bh - 20 * unit))
        x -= step
    y = by + bh - step / 2 - 20 * unit
    while y > by + 20 * unit:
        pts.append(QPointF(bx + 20 * unit, y))
        y -= step
    phase = int(t * 9)
    p.setPen(Qt.NoPen)
    for i, pt in enumerate(pts):
        lit = (i + phase) % 3 == 0
        if lit:
            glow = QRadialGradient(pt, 22 * unit)
            glow.setColorAt(0, QColor(255, 236, 160, 230))
            glow.setColorAt(1, QColor(255, 180, 60, 0))
            p.setBrush(glow)
            p.drawEllipse(pt, 22 * unit, 22 * unit)
        p.setBrush(QColor(255, 250, 220) if lit else QColor(110, 80, 40))
        p.drawEllipse(pt, 8 * unit, 8 * unit)

    # letterboard
    cx = w / 2
    draw_neon(p, "NEXT ATTRACTION", "text", int(46 * unit), CYAN, cx, by + 52 * unit, "tc", flicker=True)
    title = (info.get("title") or "THE FEATURE").upper()
    tpx = _fit_px(title, "text", int(104 * unit), bw * 0.78)
    fm = QFontMetricsF(font("text", tpx))
    strip = QRectF(cx - fm.horizontalAdvance(title) / 2 - 30 * unit, by + bh * 0.40,
                   fm.horizontalAdvance(title) + 60 * unit, tpx * 1.25)
    p.setBrush(QColor(246, 238, 214))
    p.setPen(QPen(QColor(60, 40, 20), 2 * unit))
    p.drawRect(strip)
    p.setPen(QPen(QColor(200, 190, 165), 1))
    for k in range(1, 3):
        yy = strip.top() + strip.height() * k / 3
        p.drawLine(QPointF(strip.left(), yy), QPointF(strip.right(), yy))
    _centred(p, title, tpx, cx, strip.top() + tpx * 1.0, QColor(18, 12, 10))
    if info.get("episode"):
        _centred(p, info["episode"], int(36 * unit), cx, strip.bottom() + 52 * unit, QColor(255, 230, 190))
    # showtime + countdown under the board
    if info.get("start"):
        draw_neon(p, f"SHOWTIME {info['start']}", "text", int(52 * unit), YELLOW, cx, h * 0.66, "tc")
    draw_neon(p, countdown(remaining), "numbers", int(96 * unit), PINK, cx, h * 0.74, "tc")
    if info.get("later"):
        _centred(p, "LATER  ▸  " + info["later"], int(34 * unit), cx, h * 0.95, QColor(255, 220, 240))


# --------------------------------------------------------------------------- 4 HYPERSPACE

_STARS = [(random.Random(i).uniform(0, 2 * math.pi), random.Random(i * 7 + 1).uniform(0.05, 1.0),
           random.Random(i * 13 + 5).random()) for i in range(260)]


def hyperspace(p: QPainter, w: int, h: int, info: dict, t: float, remaining: float) -> None:
    unit = h / 1080.0
    p.fillRect(QRectF(0, 0, w, h), QColor(2, 0, 10))
    cx, cy = w / 2, h * 0.46
    reach = math.hypot(w, h) * 0.6
    speed = 0.22
    for ang, z0, hue in _STARS:
        z = (z0 - t * speed) % 1.0 + 0.02
        z_prev = min(1.02, z + 0.035)
        d1, d0 = reach * 0.05 / z, reach * 0.05 / z_prev
        if d0 > reach:
            continue
        dx, dy = math.cos(ang), math.sin(ang)
        col = QColor(CYAN) if hue < 0.45 else (QColor(PINK) if hue < 0.65 else QColor(235, 235, 255))
        col.setAlpha(int(min(255, 60 + 200 * (1 - z))))
        p.setPen(QPen(col, max(1.0, 3.2 * unit * (1 - z))))
        p.drawLine(QPointF(cx + dx * d0, cy + dy * d0), QPointF(cx + dx * min(d1, reach), cy + dy * min(d1, reach)))
    # cockpit ring
    r = h * 0.17
    p.setBrush(QColor(2, 0, 16, 200))
    p.setPen(QPen(QColor(CYAN), 3 * unit))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.setBrush(Qt.NoBrush)
    for k in range(12):
        a0 = (t * 40 + k * 30) * 16
        p.setPen(QPen(QColor(PINK) if k % 3 == 0 else QColor(CYAN), 6 * unit))
        p.drawArc(QRectF(cx - r * 1.15, cy - r * 1.15, r * 2.3, r * 2.3), int(a0), 16 * 16)
    p.setPen(QPen(QColor(0, 240, 255, 120), 2 * unit))
    for a in (0, 90, 180, 270):
        rad = math.radians(a)
        p.drawLine(QPointF(cx + math.cos(rad) * r * 1.25, cy + math.sin(rad) * r * 1.25),
                   QPointF(cx + math.cos(rad) * r * 1.45, cy + math.sin(rad) * r * 1.45))
    clock = countdown(remaining)
    cpx = _fit_px(clock, "text", int(110 * unit), r * 1.7)
    _centred(p, clock, cpx, cx, cy + cpx * 0.36, QColor("#ffffff"), shadow=QColor(0, 240, 255, 160))
    _centred(p, "ARRIVAL IN", int(26 * unit), cx, cy - r * 0.45, QColor(CYAN))
    # text
    draw_neon(p, "STAND BY FOR THE " + ("FEATURE" if info.get("movie", True) else "NEXT SHOW"), "text", int(52 * unit), CYAN, cx, h * 0.04, "tc", flicker=True)
    title = info.get("title") or "THE FEATURE"
    tpx = _fit_px(title, "text", int(96 * unit), w * 0.86)
    tw = QFontMetricsF(font("text", tpx)).horizontalAdvance(title)
    _chrome_text(p, title, tpx, cx - tw / 2, h * 0.80)
    bits = [b for b in (f"DEPARTS {info['start']}" if info.get("start") else "",
                        f"CHANNEL {info['channel']}" if info.get("channel") else "") if b]
    if bits:
        _centred(p, "   ·   ".join(bits), int(36 * unit), cx, h * 0.88, QColor(YELLOW))
    if info.get("later"):
        _centred(p, "LATER  ▸  " + info["later"], int(30 * unit), cx, h * 0.95, QColor(200, 190, 255))


# --------------------------------------------------------------------------- 5 TELETEXT

TT = {"w": QColor(255, 255, 255), "y": QColor(255, 255, 0), "c": QColor(0, 255, 255), "g": QColor(0, 255, 0),
      "m": QColor(255, 0, 255), "r": QColor(255, 40, 40), "b": QColor(30, 60, 255), "k": QColor(0, 0, 0)}
COLS, ROWS = 40, 25


def teletext(p: QPainter, w: int, h: int, info: dict, t: float, remaining: float) -> None:
    p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0))
    # a 4:3-ish page centred on screen
    ph = h * 0.94
    cw = min(w * 0.96 / COLS, ph / ROWS * 0.82)
    rh = ph / ROWS
    x0, y0 = (w - cw * COLS) / 2, (h - ph) / 2
    px = int(rh * 0.78)
    f = font("text", px)
    fm = QFontMetricsF(f)

    def cell(col: int, row: int, ncol: int = 1, nrow: int = 1) -> QRectF:
        return QRectF(x0 + col * cw, y0 + row * rh, ncol * cw, nrow * rh)

    def put(col: int, row: int, text: str, fg: str = "w", bg: str | None = None, double: bool = False) -> None:
        text = text[:COLS - col]
        if bg:
            p.fillRect(cell(col, row, len(text), 2 if double else 1), TT[bg])
        p.setFont(font("text", px * 2) if double else f)
        m = QFontMetricsF(p.font())
        p.setPen(TT[fg])
        for i, ch in enumerate(text):
            r = cell(col + i, row, 1, 2 if double else 1)
            # every character sits in its own cell, like a real character generator
            p.drawText(QPointF(r.center().x() - m.horizontalAdvance(ch) / 2,
                               r.top() + (r.height() + m.ascent() - m.descent()) / 2), ch)

    def blocks(col: int, row: int, pattern: str, color: str) -> None:
        # "mosaic" graphics: each character cell is 2x3 chunky pixels
        for i, bits in enumerate(pattern.split()):
            r = cell(col + i, row)
            for k, b in enumerate(bits):
                if b == "1":
                    bx, byy = k % 2, k // 2
                    p.fillRect(QRectF(r.left() + bx * r.width() / 2, r.top() + byy * r.height() / 3,
                                      r.width() / 2 + 0.5, r.height() / 3 + 0.5), TT[color])

    now = dt.datetime.now()
    put(0, 0, f"P{100 + int(info.get('channel') or 0):03d}", "w")
    put(6, 0, "XC TEXT" if paths.XCABLE else "NC42 TEXT", "y")
    put(20, 0, now.strftime("%a %d %b"), "c")
    put(32, 0, now.strftime("%H:%M:%S"), "y")
    # banner
    p.fillRect(cell(0, 2, COLS, 3), TT["b"])
    wave = ["111111 101010 010101 111111", "110011 111111 001100 110011"][int(t * 2) % 2]
    blocks(1, 2, wave, "c")
    blocks(1, 3, wave[::-1], "m")
    blocks(35, 2, wave, "m")
    blocks(35, 3, wave[::-1], "c")
    movie = info.get("movie", True)
    put(7 if movie else 10, 2, "MOVIE NIGHT" if movie else "COMING UP", "y", double=True)
    put(0, 6, " FEATURE PRESENTATION " if movie else " NEXT PROGRAMME ", "k", "y")
    title = (info.get("title") or "THE FEATURE").upper()
    put(1, 8, title[:19], "w", double=True)
    if len(title) > 19:
        put(1, 10, title[19:38], "w", double=True)
    if info.get("episode"):
        put(1, 12, info["episode"][:38], "c")
    put(1, 14, "STARTS", "g")
    put(8, 14, info.get("start", "--:--"), "w")
    flash = int(t * 1.4) % 2 == 0
    put(1, 16, "COUNTDOWN", "m")
    put(11, 15, countdown(remaining), "y" if flash else "w", double=True)
    # what's on after
    put(0, 18, " LATER ON THIS CHANNEL ", "k", "c")
    upcoming = info.get("upcoming") or ([tuple(info["later"].split("  ", 1))] if info.get("later") else [])
    for i, item in enumerate(upcoming[:3]):
        when, name = (item + ("",))[:2] if isinstance(item, tuple) else ("", str(item))
        put(1, 19 + i, when[:8], "c")
        put(10, 19 + i, name.upper()[:29], "w")
    if not upcoming:
        put(1, 19, "MORE GREAT TV ALL NIGHT", "w")
    # fastext bar
    for i, (label, colr) in enumerate((("NOW", "r"), ("LATER", "g"), ("GUIDE", "y"), ("INDEX", "c"))):
        put(i * 10, 24, label, colr)
    # faint scanlines
    p.setPen(QPen(QColor(0, 0, 0, 70), 1))
    y = 0.0
    while y < h:
        p.drawLine(QPointF(0, y), QPointF(w, y))
        y += 3


PAINTERS = {2: film_leader, 3: marquee, 4: hyperspace, 5: teletext}


def pick_style(seed: float) -> int:
    """A style for one intermission, the same every time you tune back in to it."""
    return random.Random(int(seed)).randint(1, STYLES)
