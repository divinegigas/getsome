"""Animated "UP NEXT" bumpers, drawn live over the generated jingle clips.

Because they're drawn at play time they always name the real next programme,
even if you tune in halfway through. Three looks:
  1  GRID RUN    - synthwave sun rising over a racing neon grid
  2  VHS GLITCH  - tape-deck OSD, tracking noise and RGB-split titles
  3  NEON SIGN   - a buzzing neon tube sign flickering on
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from ..brand import QColor

from .overlays import CYAN, PINK, YELLOW, _chrome_text, draw_neon, font


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _typed(text: str, t: float, start: float, cps: float = 28) -> str:
    n = int(max(0.0, t - start) * cps)
    return text[:n]


def paint_bumper(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    if info.get("kind"):            # commercials, promos, IDs and break stings (ads_paint.py)
        from .ads_paint import paint

        paint(p, w, h, info, t, dur)
    else:
        style = info.get("style", 1)
        if style == "art":
            from .art_paint import upnext_art

            upnext_art(p, w, h, info, t, dur)
        else:
            {1: _grid_run, 2: _vhs, 3: _neon_sign}.get(style, _grid_run)(p, w, h, info, t, dur)
    # fade in from / out to black
    a = 0.0
    if t < 0.35:
        a = 1 - t / 0.35
    elif t > dur - 0.6:
        a = min(1.0, (t - (dur - 0.6)) / 0.6)
    if a > 0:
        p.fillRect(0, 0, w, h, QColor(0, 0, 0, int(255 * a)))


def _when_line(info: dict) -> str:
    bits = []
    if info.get("start"):
        bits.append(info["start"])
    if info.get("channel"):
        bits.append(f"CHANNEL {info['channel']}")
    return "  ·  ".join(bits)


# --------------------------------------------------------------------------- 1 GRID RUN

def _grid_run(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    unit = h / 1080.0
    _synth_backdrop(p, w, h, t, rise=True)
    _grid_run_text(p, w, h, info, t, unit)


def _synth_backdrop(p: QPainter, w: int, h: int, t: float, rise: bool = True, horizon_f: float = 0.58,
                    speed: float = 1.6) -> None:
    unit = h / 1080.0
    horizon = h * horizon_f
    sky = QLinearGradient(0, 0, 0, horizon)
    sky.setColorAt(0.0, QColor("#05010f"))
    sky.setColorAt(0.7, QColor("#2a0848"))
    sky.setColorAt(1.0, QColor("#6a1060"))
    p.fillRect(QRectF(0, 0, w, horizon), sky)
    # stars
    rng = random.Random(7)
    p.setPen(Qt.NoPen)
    for _ in range(90):
        x, y = rng.random() * w, rng.random() * horizon * 0.8
        tw = 0.5 + 0.5 * math.sin(t * 3 + x)
        p.setBrush(QColor(255, 255, 255, int(60 + 120 * tw)))
        p.drawEllipse(QPointF(x, y), 1.4 * unit + 0.5, 1.4 * unit + 0.5)
    # sun rising
    r = h * 0.24
    cy = horizon - r * 0.15 * (_ease(t / 1.6) if rise else 1.0) - r * 0.1
    cx = w / 2
    sun = QLinearGradient(0, cy - r, 0, cy + r)
    sun.setColorAt(0.0, QColor("#ffe46b"))
    sun.setColorAt(0.5, QColor("#ff7a3d"))
    sun.setColorAt(1.0, QColor("#ff2bd6"))
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    cut = QPainterPath()
    y, gap = cy, 3.0 * unit
    while y < cy + r:
        cut.addRect(QRectF(cx - r, y, 2 * r, gap))
        y += gap * 3.0
        gap *= 1.3
    clip = QPainterPath()
    clip.addRect(QRectF(0, 0, w, horizon))
    p.save()
    p.setClipPath(clip)
    halo = QRadialGradient(QPointF(cx, cy), r * 2.0)
    halo.setColorAt(0.0, QColor(255, 60, 200, 110))
    halo.setColorAt(1.0, QColor(255, 60, 200, 0))
    p.fillRect(QRectF(0, 0, w, horizon), halo)
    p.fillPath(path.subtracted(cut), sun)
    p.restore()
    # racing grid
    ground = QLinearGradient(0, horizon, 0, h)
    ground.setColorAt(0.0, QColor("#1a0532"))
    ground.setColorAt(1.0, QColor("#05010f"))
    p.fillRect(QRectF(0, horizon, w, h - horizon), ground)
    p.setPen(QPen(QColor(255, 43, 214, 200), 1.6 * unit + 0.4))
    for i in range(-20, 21):
        p.drawLine(QPointF(cx + i * 8 * unit, horizon), QPointF(cx + i * w / 8, h))
    phase = (t * speed) % 1.0
    for k in range(18):
        z = (k + 1 - phase)
        y = horizon + (h - horizon) * (1.0 / (z * 0.55)) * 0.22
        if horizon < y < h:
            p.drawLine(QPointF(0, y), QPointF(w, y))
    p.setPen(QPen(QColor(CYAN), 3 * unit + 0.5))
    p.drawLine(QPointF(0, horizon), QPointF(w, horizon))


def _shadow_text(p: QPainter, x: float, y: float, text: str, color: QColor, unit: float) -> None:
    p.setPen(QColor(10, 0, 30, 220))
    for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2)):
        p.drawText(QPointF(x + dx * unit, y + dy * unit), text)
    p.setPen(color)
    p.drawText(QPointF(x, y), text)


def _grid_run_text(p: QPainter, w: int, h: int, info: dict, t: float, unit: float) -> None:
    if t > 0.5:
        flick = t < 1.1 and int(t * 20) % 3 == 0
        draw_neon(p, "UP NEXT", "numbers", int(120 * unit), PINK, w / 2, h * 0.10, "tc",
                  opacity=0.25 if flick else 1.0, flicker=True)
    if t > 1.1:
        slide = _ease((t - 1.1) / 0.7)
        title = info.get("title", "")
        fm = QFontMetricsF(font("text", int(96 * unit)))
        tw = min(fm.horizontalAdvance(title), w * 0.9)
        x = (w - tw) / 2 - (1 - slide) * w * 0.6
        p.save()
        p.setOpacity(slide)
        _chrome_text(p, title, int(96 * unit), x, h * 0.40)
        p.restore()
    if t > 1.9:
        ep = _typed(info.get("episode", ""), t, 1.9)
        p.setFont(font("text", int(46 * unit)))
        fm = QFontMetricsF(p.font())
        x = (w - fm.horizontalAdvance(info.get("episode", ""))) / 2
        p.setPen(QColor(10, 0, 30, 220))
        for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2)):
            p.drawText(QPointF(x + dx * unit, h * 0.49 + dy * unit), ep)
        p.setPen(QColor("#ffffff"))
        p.drawText(QPointF(x, h * 0.49), ep)
    if t > 2.6:
        line = _when_line(info)
        p.setFont(font("text", int(42 * unit)))
        fm = QFontMetricsF(p.font())
        p.setOpacity(_ease((t - 2.6) / 0.5))
        x = (w - fm.horizontalAdvance(line)) / 2
        p.setPen(QColor(10, 0, 30, 220))
        for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2)):
            p.drawText(QPointF(x + dx * unit, h * 0.56 + dy * unit), line)
        p.setPen(QColor(CYAN))
        p.drawText(QPointF(x, h * 0.56), line)
        p.setOpacity(1.0)


# --------------------------------------------------------------------------- 2 VHS GLITCH

def _vhs(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    unit = h / 1080.0
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor("#020818"))
    g.setColorAt(1, QColor("#0a0326"))
    p.fillRect(0, 0, w, h, g)
    rnd = random.Random(int(t * 30))
    # tracking noise band rolling up
    band_y = (1 - (t * 0.22) % 1.0) * h
    for i in range(int(40 * unit) + 6):
        y = band_y + i * 2
        p.fillRect(QRectF(rnd.random() * w * 0.2, y, w * (0.6 + rnd.random() * 0.4), 2),
                   QColor(255, 255, 255, rnd.randint(10, 70)))
    # tape-deck OSD
    p.setFont(font("text", int(44 * unit)))
    p.setPen(QColor("#e8f4ff"))
    if int(t * 2) % 2 == 0 or t > 1.5:
        p.drawText(QPointF(70 * unit, 100 * unit), "PLAY ▶")
    mins, secs = divmod(int(t), 60)
    p.drawText(QPointF(w - 330 * unit, 100 * unit), f"SP  0:{mins:02d}:{secs:02d}")

    glitch = max(0.0, 1.4 - t) + (0.6 if rnd.random() < 0.06 else 0.0)

    def split_text(text: str, kind: str, px: int, y: float, color: QColor):
        off = (rnd.random() - 0.5) * 40 * unit * glitch
        draw_neon(p, text, kind, px, QColor(255, 0, 80), w / 2 + 6 * unit + off, y, "tc", opacity=0.55)
        draw_neon(p, text, kind, px, QColor(0, 220, 255), w / 2 - 6 * unit - off, y, "tc", opacity=0.55)
        draw_neon(p, text, kind, px, color, w / 2 + off * 0.3, y, "tc")

    if t > 0.3:
        split_text("COMING UP", "numbers", int(110 * unit), h * 0.16, QColor(PINK))
    if t > 1.0:
        title = info.get("title", "")
        fm = QFontMetricsF(font("text", int(92 * unit)))
        tw = min(fm.horizontalAdvance(title), w * 0.9)
        x = (w - tw) / 2
        # sliced horizontal glitch while settling
        slices = 7
        sh = 120 * unit / slices
        for s in range(slices):
            p.save()
            p.setClipRect(QRectF(0, h * 0.34 + s * sh, w, sh + 1))
            dx = (rnd.random() - 0.5) * 60 * unit * glitch
            _chrome_text(p, title, int(92 * unit), x + dx, h * 0.43)
            p.restore()
    if t > 1.8:
        p.setFont(font("text", int(46 * unit)))
        p.setPen(QColor(YELLOW))
        ep = info.get("episode", "")
        fm = QFontMetricsF(p.font())
        p.drawText(QPointF((w - fm.horizontalAdvance(ep)) / 2, h * 0.53), _typed(ep, t, 1.8, 35))
    if t > 2.4:
        line = _when_line(info)
        p.setFont(font("text", int(42 * unit)))
        fm = QFontMetricsF(p.font())
        p.setPen(QColor(CYAN))
        p.drawText(QPointF((w - fm.horizontalAdvance(line)) / 2, h * 0.61), line)
    # scanlines + vignette
    p.setPen(QPen(QColor(0, 0, 0, 90), 1))
    for y in range(0, h, 3):
        p.drawLine(0, y, w, y)


# --------------------------------------------------------------------------- 3 NEON SIGN

_FLICKER_ON = [0.10, 0.18, 0.30, 0.34, 0.52, 0.60, 0.66, 0.95]   # toggle times while warming up


def _lit(t: float, start: float, toggles=_FLICKER_ON) -> bool:
    on = False
    for x in toggles:
        if t - start >= x:
            on = not on
    return on if t - start < toggles[-1] else True


def _neon_sign(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    unit = h / 1080.0
    # dark brick wall
    p.fillRect(0, 0, w, h, QColor("#0b0612"))
    bw, bh = 120 * unit, 50 * unit
    p.setPen(QPen(QColor(40, 20, 50), 2))
    row = 0
    y = 0.0
    while y < h:
        off = (bw / 2) if row % 2 else 0
        x = -off
        while x < w:
            p.drawRect(QRectF(x, y, bw, bh))
            x += bw
        y += bh
        row += 1
    # glow cast on the wall once lit
    if _lit(t, 0.2):
        glow = QRadialGradient(QPointF(w / 2, h * 0.3), w * 0.45)
        glow.setColorAt(0, QColor(255, 43, 214, 70))
        glow.setColorAt(1, QColor(255, 43, 214, 0))
        p.fillRect(0, 0, w, h, glow)
    if _lit(t, 0.2):
        draw_neon(p, "NEXT", "numbers", int(150 * unit), PINK, w / 2, h * 0.08, "tc", flicker=True)
    # arrow
    if _lit(t, 0.9, [0.05, 0.12, 0.2]):
        path = QPainterPath()
        ax, ay = w / 2, h * 0.33
        path.moveTo(ax - 160 * unit, ay)
        path.lineTo(ax + 120 * unit, ay)
        path.moveTo(ax + 70 * unit, ay - 40 * unit)
        path.lineTo(ax + 130 * unit, ay)
        path.lineTo(ax + 70 * unit, ay + 40 * unit)
        for wdt, a in ((22, 30), (12, 70), (5, 255)):
            c = QColor(CYAN)
            c.setAlpha(a)
            p.strokePath(path, QPen(c, wdt * unit, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if _lit(t, 1.5, [0.04, 0.09, 0.2, 0.26, 0.4]):
        title = info.get("title", "")
        px = int(110 * unit)
        fm = QFontMetricsF(font("text", px))
        while fm.horizontalAdvance(title) > w * 0.9 and px > 40:
            px = int(px * 0.9)
            fm = QFontMetricsF(font("text", px))
        draw_neon(p, title, "text", px, CYAN, w / 2, h * 0.42, "tc", flicker=True)
    if t > 2.4:
        ep = info.get("episode", "")
        if ep:
            draw_neon(p, ep, "text", int(50 * unit), YELLOW, w / 2, h * 0.60, "tc")
        line = _when_line(info)
        if line:
            draw_neon(p, line, "text", int(46 * unit), PINK, w / 2, h * 0.70, "tc")


# --------------------------------------------------------------------------- intermission

def paint_intermission(p: QPainter, w: int, h: int, info: dict, t: float, remaining: float) -> None:
    """Filler before a timed start: what's next, a countdown, and what's later.
    Five looks; style 1 is below, the others are in intermissions.py."""
    from .intermissions import PAINTERS

    painter = PAINTERS.get(info.get("style", 1))
    if painter is not None:
        p.save()
        painter(p, w, h, info, t, remaining)
        p.restore()
        return
    unit = h / 1080.0
    _synth_backdrop(p, w, h, t, rise=False, horizon_f=0.70, speed=0.35)
    p.fillRect(QRectF(0, 0, w, h), QColor(5, 0, 18, 90))

    def centred(text: str, px: int, y: float, color: QColor):
        p.setFont(font("text", px))
        fm = QFontMetricsF(p.font())
        _shadow_text(p, (w - fm.horizontalAdvance(text)) / 2, y, text, color, unit)

    draw_neon(p, "INTERMISSION", "numbers", int(84 * unit), CYAN, w / 2, h * 0.05, "tc", flicker=True)
    centred(f"COMING UP AT {info.get('start', '')}" if info.get("start") else "COMING UP", int(44 * unit),
            h * 0.25, QColor(YELLOW))
    title = info.get("title", "")
    px = int(104 * unit)
    while QFontMetricsF(font("text", px)).horizontalAdvance(title) > w * 0.9 and px > 40:
        px = int(px * 0.9)
    tw = QFontMetricsF(font("text", px)).horizontalAdvance(title)
    _chrome_text(p, title, px, (w - tw) / 2, h * 0.37)
    if info.get("episode"):
        centred(info["episode"], int(42 * unit), h * 0.44, QColor("#ffffff"))
    # countdown
    rem = max(0, int(remaining))
    mm, ss = divmod(rem, 60)
    hh, mm = divmod(mm, 60)
    clock = f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
    draw_neon(p, clock, "text", int(150 * unit), PINK, w / 2, h * 0.47, "tc")
    if info.get("later"):
        centred("LATER  ▸  " + info["later"], int(36 * unit), h * 0.80, QColor(CYAN))
    if info.get("channel"):
        centred(f"CHANNEL {info['channel']}", int(30 * unit), h * 0.88, QColor(PINK))
