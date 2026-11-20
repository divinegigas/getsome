"""Everything drawn on top of the video: neon channel numbers, static, banners.

All visuals are generated in code. Neon text is a glyph path stroked several
times with widening, fading pens (the glow), then a hot white-pink core. The
result is cached as an image so it costs nothing per frame.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QLinearGradient, QPainter, QPainterPath, QPen

from .. import paths

PINK = QColor(255, 43, 214)
CYAN = QColor(0, 240, 255)
YELLOW = QColor(255, 230, 0)

_fonts: dict[str, str] = {}


def load_fonts() -> None:
    for key, file in (("numbers", "Monoton-Regular.ttf"), ("text", "TiltNeon.ttf")):
        fid = QFontDatabase.addApplicationFont(str(paths.FONTS_DIR / file))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        _fonts[key] = fams[0] if fams else "Sans Serif"


def font(kind: str, px: int) -> QFont:
    f = QFont(_fonts.get(kind, "Sans Serif"))
    f.setPixelSize(max(1, px))
    return f


# --------------------------------------------------------------------------- neon text

_neon_cache: dict[tuple, QImage] = {}


def neon_image(text: str, kind: str, px: int, color: QColor) -> QImage:
    key = (text, kind, px, color.rgba())
    img = _neon_cache.get(key)
    if img is not None:
        return img
    f = font(kind, px)
    path = QPainterPath()
    path.addText(0, 0, f, text)
    br = path.boundingRect()
    pad = px * 0.45
    img = QImage(int(br.width() + pad * 2), int(br.height() + pad * 2), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(pad - br.left(), pad - br.top())
    # outer glow: wide, faint strokes
    base = max(1.0, px / 40)
    for width, alpha in ((14, 18), (10, 28), (7, 45), (4.5, 80), (2.6, 140)):
        c = QColor(color)
        c.setAlpha(alpha)
        p.strokePath(path, QPen(c, base * width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    # tube core
    core = QColor(color).lighter(165)
    p.strokePath(path, QPen(core, base * 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, QColor(255, 255, 255, 60) if kind == "text" else QColor(255, 255, 255, 25))
    if kind == "numbers" and px >= 80:
        # etch CRT scanlines into the glyph itself (only where it has pixels)
        p.resetTransform()
        p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        pen = QPen(QColor(0, 0, 0, 90), max(1.0, px / 160))
        p.setPen(pen)
        step = max(3, px // 70)
        for yy in range(0, img.height(), step):
            p.drawLine(0, yy, img.width(), yy)
    p.end()
    if len(_neon_cache) > 64:
        _neon_cache.clear()
    _neon_cache[key] = img
    return img


def draw_neon(p: QPainter, text: str, kind: str, px: int, color: QColor, x: float, y: float,
              anchor: str = "tl", opacity: float = 1.0, flicker: bool = False) -> QRectF:
    img = neon_image(text, kind, px, color)
    w, h = img.width(), img.height()
    if "r" in anchor:
        x -= w
    elif "c" in anchor:
        x -= w / 2
    if "b" in anchor:
        y -= h
    elif "m" in anchor:
        y -= h / 2
    if flicker and random.random() < 0.04:
        opacity *= 0.55
    p.setOpacity(max(0.0, min(1.0, opacity)))
    p.drawImage(QPointF(x, y), img)
    p.setOpacity(1.0)
    return QRectF(x, y, w, h)


# --------------------------------------------------------------------------- static


class StaticNoise:
    """Procedural TV snow: a pool of low-res noise frames shown at random offsets."""

    W, H = 320, 200

    def __init__(self, frames: int = 10):
        rng = np.random.default_rng()
        self._frames = []
        for _ in range(frames):
            n = rng.integers(0, 256, size=(self.H, self.W), dtype=np.uint8)
            # horizontal streaks: some rows brighter/darker, like real snow
            rows = rng.normal(1.0, 0.18, size=(self.H, 1)).clip(0.5, 1.4)
            n = (n * rows).clip(0, 255).astype(np.uint8)
            img = QImage(n.tobytes(), self.W, self.H, self.W, QImage.Format_Grayscale8).copy()
            self._frames.append(img)
        self._band = 0.0

    def draw(self, p: QPainter, w: int, h: int, opacity: float) -> None:
        if opacity <= 0:
            return
        p.setOpacity(opacity)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        img = random.choice(self._frames)
        # jitter the source so repeated frames don't read as a loop
        sx, sy = random.randint(0, 40), random.randint(0, 30)
        p.drawImage(QRectF(0, 0, w, h), img, QRectF(sx, sy, self.W - 40, self.H - 30))
        # slow rolling dark band (vertical hold drift)
        self._band = (self._band + 0.012) % 1.0
        band_y = self._band * h
        p.fillRect(QRectF(0, band_y, w, h * 0.12), QColor(0, 0, 0, 70))
        p.setOpacity(1.0)


# --------------------------------------------------------------------------- overlay state


@dataclass
class Fade:
    """Simple timed visibility: fully visible for `hold`, then fades over `fade` seconds."""

    shown_at: float = 0.0
    hold: float = 0.0
    fade: float = 0.4

    def show(self, hold: float) -> None:
        self.shown_at, self.hold = time.monotonic(), hold

    def hide(self) -> None:
        self.hold = -1e9

    def opacity(self) -> float:
        t = time.monotonic() - self.shown_at
        if t < self.hold:
            return 1.0
        if t < self.hold + self.fade:
            return 1.0 - (t - self.hold) / self.fade
        return 0.0


@dataclass
class OverlayState:
    channel_text: str = ""
    channel_fade: Fade = field(default_factory=Fade)
    banner_lines: list[str] = field(default_factory=list)   # legacy plain lines
    banner_info: dict | None = None                          # header / title / episode / next
    banner_fade: Fade = field(default_factory=Fade)
    static_level: float = 0.0          # 0..1 target
    static_shown: float = 0.0          # eased value actually drawn
    message: str = ""                  # centred neon message (OFF AIR, NO CHANNELS ...)
    message_sub: str = ""
    black: bool = False                # paint black under overlays (no video)
    guide: bool = False                # the guide channel is showing
    bumper: dict | None = None         # up-next bumper info while one plays
    bumper_start: object = None        # wall-clock datetime the bumper started
    bumper_dur: float = 10.0
    intermission: dict | None = None   # movie-channel filler info (has "ends_at")
    locked: bool = False               # restricted channel, PIN not entered: video blurred, sound off
    pin_entry: str | None = None       # digits typed so far while the PIN pad is open
    pin_error: bool = False            # last PIN was wrong
    lock_note: str = ""                # e.g. "Opens by itself at 9 PM"
    pin_pick: int | None = None        # digit being chosen with Up/Down (game controller)

    def animating(self) -> bool:
        return (self.guide or self.locked or self.bumper is not None or self.intermission is not None or self.static_shown > 0.001 or self.static_level > 0 or self.channel_fade.opacity() > 0
                or self.banner_fade.opacity() > 0 or bool(self.message))


class OverlayRenderer:
    def __init__(self, state: OverlayState):
        self.s = state
        self.noise = StaticNoise()
        self._last = time.monotonic()
        self.guide = None            # GuideRenderer, set by the TV window
        self.menu = None             # TrackMenu, set by the TV window
        self.music_menu = None       # MusicMenu (guide channel)
        self.exit_menu = None        # ExitMenu (BACK button)

    def paint(self, p: QPainter, w: int, h: int) -> None:
        s = self.s
        now = time.monotonic()
        dt, self._last = now - self._last, now
        # ease static towards its target (fast in, quick fade out)
        rate = 30.0 if s.static_level > s.static_shown else 6.0
        s.static_shown += (s.static_level - s.static_shown) * min(1.0, dt * rate)

        if s.black:
            p.fillRect(0, 0, w, h, Qt.black)
        if s.guide and self.guide is not None:
            self.guide.paint(p, w, h)
        if s.locked:
            # nothing drawn over the blurred picture may give it away (promo art, titles)
            p.fillRect(0, 0, w, h, QColor(10, 0, 25, 70))
        if s.intermission is not None and not s.locked:
            import datetime as _dt

            from .bumpers import paint_intermission

            info = s.intermission
            now_ = _dt.datetime.now()
            paint_intermission(p, w, h, info, (now_ - info["started"]).total_seconds(),
                               (info["ends_at"] - now_).total_seconds())
        if s.bumper is not None and s.bumper_start is not None and not s.locked:
            import datetime as _dt

            from .bumpers import paint_bumper

            t = (_dt.datetime.now() - s.bumper_start).total_seconds()
            if 0 <= t <= s.bumper_dur + 0.5:
                paint_bumper(p, w, h, s.bumper, t, s.bumper_dur)
        self.noise.draw(p, w, h, s.static_shown)

        unit = h / 1080.0
        if s.locked and s.static_shown < 0.5:
            paint_lock(p, w, h, s)
        if s.message:
            draw_neon(p, s.message, "numbers", int(150 * unit), PINK, w / 2, h * 0.42, "cm", flicker=True)
            if s.message_sub:
                draw_neon(p, s.message_sub, "text", int(48 * unit), CYAN, w / 2, h * 0.60, "cm")

        op = s.channel_fade.opacity()
        if op > 0 and s.channel_text:
            px, x, y = int(260 * unit), w - 70 * unit, 50 * unit
            # CRT colour split: cyan and magenta ghosts either side of the tube
            split = 9 * unit
            draw_neon(p, s.channel_text, "numbers", px, CYAN, x - split, y, "tr", opacity=op * 0.55)
            draw_neon(p, s.channel_text, "numbers", px, QColor(255, 60, 120), x + split, y + split * 0.4, "tr",
                      opacity=op * 0.40)
            draw_neon(p, s.channel_text, "numbers", px, PINK, x, y, "tr", opacity=op, flicker=True)

        op = s.banner_fade.opacity()
        if op > 0 and (s.banner_info or s.banner_lines):
            info = s.banner_info or dict(zip(("header", "title", "next"), s.banner_lines))
            draw_hud(p, info, w, h, op)

        if self.menu is not None and self.menu.open:
            self.menu.paint(p, w, h)
        if self.music_menu is not None and self.music_menu.open:
            self.music_menu.paint(p, w, h)
        if self.exit_menu is not None and self.exit_menu.open:
            self.exit_menu.paint(p, w, h)


def paint_lock(p: QPainter, w: int, h: int, s: OverlayState) -> None:
    """The restricted-channel panel: a padlock, and the PIN pad once OK is pressed."""
    unit = h / 1080.0
    p.save()
    cx, cy = w / 2, h * 0.40
    # padlock: shackle + body, in neon outline
    pen = QPen(PINK, 10 * unit)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(cx - 55 * unit, cy - 150 * unit, 110 * unit, 130 * unit), 0, 180 * 16)
    p.drawLine(QPointF(cx - 55 * unit, cy - 85 * unit), QPointF(cx - 55 * unit, cy - 50 * unit))
    p.drawLine(QPointF(cx + 55 * unit, cy - 85 * unit), QPointF(cx + 55 * unit, cy - 50 * unit))
    p.setBrush(QColor(40, 0, 60, 200))
    p.drawRoundedRect(QRectF(cx - 85 * unit, cy - 55 * unit, 170 * unit, 125 * unit), 14 * unit, 14 * unit)
    p.setPen(QPen(CYAN, 8 * unit))
    p.drawLine(QPointF(cx, cy - 10 * unit), QPointF(cx, cy + 25 * unit))
    p.restore()
    draw_neon(p, "RESTRICTED", "numbers", int(96 * unit), PINK, cx, cy + 150 * unit, "cm", flicker=True)
    if s.pin_entry is None:
        draw_neon(p, "Press OK and enter the PIN to watch", "text", int(44 * unit), CYAN, cx, cy + 260 * unit, "cm")
        if s.lock_note:
            draw_neon(p, s.lock_note, "text", int(36 * unit), YELLOW, cx, cy + 330 * unit, "cm")
        return
    n = max(4, len(s.pin_entry) + 1 + (s.pin_pick is not None))
    r, gap = 22 * unit, 70 * unit
    x0 = cx - gap * (n - 1) / 2
    p.save()
    for i in range(n):
        filled = i < len(s.pin_entry)
        p.setPen(QPen(CYAN, 5 * unit))
        p.setBrush(CYAN if filled else Qt.NoBrush)
        c = QPointF(x0 + i * gap, cy + 260 * unit)
        if i == len(s.pin_entry) and s.pin_pick is not None:
            p.setPen(QPen(PINK, 5 * unit))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r * 1.25, r * 1.25)
            p.setFont(font("text", int(34 * unit)))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(c.x() - r, c.y() - r * 1.2, 2 * r, 2.4 * r), Qt.AlignCenter, str(s.pin_pick))
            continue
        p.drawEllipse(c, r, r)
    p.restore()
    msg = "Wrong PIN - try again" if s.pin_error else "Type the PIN, then OK  ·  BACK cancels"
    draw_neon(p, msg, "text", int(40 * unit), PINK if s.pin_error else CYAN, cx, cy + 350 * unit, "cm")
    if not s.pin_error:
        draw_neon(p, "Controller: Up/Down picks a digit, Right moves on", "text", int(32 * unit), YELLOW,
                  cx, cy + 410 * unit, "cm")


def _scanlines(p: QPainter, r: QRectF, opacity: float) -> None:
    p.save()
    p.setOpacity(opacity)
    p.setPen(QPen(QColor(0, 0, 0, 110), 1))
    y = r.top()
    while y < r.bottom():
        p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        y += 3
    p.restore()


def _chrome_text(p: QPainter, text: str, px: int, x: float, baseline: float) -> float:
    """Chrome-gradient text with a magenta edge; returns its width."""
    f = font("text", px)
    path = QPainterPath()
    path.addText(x, baseline, f, text)
    br = path.boundingRect()
    g = QLinearGradient(0, br.top(), 0, br.bottom())
    g.setColorAt(0.0, QColor("#ffffff"))
    g.setColorAt(0.48, QColor("#a8e6ff"))
    g.setColorAt(0.52, QColor("#5b3bb0"))
    g.setColorAt(0.8, QColor("#ff9be8"))
    g.setColorAt(1.0, QColor("#ffffff"))
    glow = QColor(PINK)
    glow.setAlpha(90)
    p.strokePath(path, QPen(glow, max(2.0, px / 10), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, g)
    return br.width()


def draw_hud(p: QPainter, info: dict, w: int, h: int, op: float) -> None:
    """Retro-future info panel, bottom left.

    info: header ("3  Trek"), title (show), episode ("S01E05  ·  Phage"), next ("Next 9:30 ...")."""
    unit = h / 1080.0
    header = info.get("header", "")
    title = info.get("title", "")
    episode = info.get("episode", "")
    nxt = info.get("next", "")

    f_head, f_title, f_next = font("text", int(34 * unit)), font("text", int(64 * unit)), font("text", int(34 * unit))
    from PySide6.QtGui import QFontMetricsF

    m_title = QFontMetricsF(f_title)
    m_small = QFontMetricsF(f_head)
    f_ep = font("text", int(40 * unit))
    m_ep = QFontMetricsF(f_ep)
    content_w = max(m_small.horizontalAdvance(header.upper()) + 260 * unit,
                    m_title.horizontalAdvance(title), m_ep.horizontalAdvance(episode),
                    m_small.horizontalAdvance(nxt) + 40 * unit)
    pw = min(w * 0.86, content_w + 100 * unit)
    ph = (34 + (78 if title else 0) + (54 if episode else 0) + (50 if nxt else 0) + 46) * unit
    x0, y1 = 60 * unit, h - 60 * unit
    y0 = y1 - ph
    slant = 40 * unit

    p.save()
    p.setOpacity(op)
    panel = QPainterPath()
    panel.moveTo(x0, y0)
    panel.lineTo(x0 + pw, y0)
    panel.lineTo(x0 + pw - slant, y1)
    panel.lineTo(x0, y1)
    panel.closeSubpath()
    bg = QLinearGradient(0, y0, 0, y1)
    bg.setColorAt(0.0, QColor(26, 6, 58, 215))
    bg.setColorAt(1.0, QColor(8, 2, 24, 200))
    p.fillPath(panel, bg)
    p.setPen(QPen(QColor(0, 240, 255, 180), 1.5 * unit + 0.5))
    p.drawPath(panel)
    # magenta accent bar along the top and corner brackets
    p.fillRect(QRectF(x0, y0 - 6 * unit, pw * 0.38, 6 * unit), QColor(PINK))
    p.fillRect(QRectF(x0 + pw * 0.40, y0 - 6 * unit, pw * 0.06, 6 * unit), QColor(CYAN))
    br = QPen(QColor(CYAN), 3 * unit + 0.5)
    p.setPen(br)
    c = 18 * unit
    p.drawLine(QPointF(x0 - 10 * unit, y1 + 10 * unit - c), QPointF(x0 - 10 * unit, y1 + 10 * unit))
    p.drawLine(QPointF(x0 - 10 * unit, y1 + 10 * unit), QPointF(x0 - 10 * unit + c, y1 + 10 * unit))

    # header row: channel, plus a clock on the right
    y = y0 + 22 * unit
    p.setFont(f_head)
    p.setPen(QColor(CYAN))
    p.drawText(QPointF(x0 + 28 * unit, y + m_small.ascent()), "▶ " + header.upper())
    p.setPen(QColor(YELLOW))
    clock = time.strftime("%-I:%M %p")
    p.drawText(QPointF(x0 + pw - slant - 30 * unit - m_small.horizontalAdvance(clock), y + m_small.ascent()), clock)
    y += m_small.height() + 8 * unit
    if title:
        _chrome_text(p, title, int(64 * unit), x0 + 26 * unit, y + m_title.ascent())
        y += m_title.height() + 6 * unit
    if episode:
        p.setFont(f_ep)
        p.setPen(QColor(255, 255, 255, 235))
        p.drawText(QPointF(x0 + 30 * unit, y + m_ep.ascent()), episode)
        y += m_ep.height() + 8 * unit
    if nxt:
        p.setFont(f_next)
        p.setPen(QColor(255, 230, 0, 230))
        p.drawText(QPointF(x0 + 30 * unit, y + m_small.ascent()), nxt.replace("Next ", "NEXT ▸ ", 1))
    p.setClipPath(panel)
    p.setOpacity(op * 0.6)
    p.setPen(QPen(QColor(0, 0, 0, 110), 1))
    yy = y0
    while yy < y1:
        p.drawLine(QPointF(x0, yy), QPointF(x0 + pw, yy))
        yy += 3
    p.restore()
