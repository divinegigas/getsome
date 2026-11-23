"""Draws the generated commercials live over their jingle carrier clips (see ads.py).

Everything here is original: invented products, slogans and looks in a late-80s to
early-2000s style. Promos use the owner's own show info (NFO) and local artwork.
Kinds: ad (by product style), id (channel ID), promo, brb / back (break stings).
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPolygonF, QRadialGradient)

from .. import paths  # noqa: E402
from .overlays import CYAN, PINK, YELLOW, _chrome_text, draw_neon, font

from collections import OrderedDict

_img_cache: "OrderedDict[tuple[str, int], QImage | None]" = OrderedDict()
IMG_CACHE_MAX = 90


def _image(path: str, max_side: int = 1920) -> QImage | None:
    """The owner's artwork, decoded at most max_side pixels on its long side (4K fanart and
    a wall of posters would otherwise take hundreds of MB), in a small LRU cache."""
    if not path:
        return None
    from .. import cablebox

    if cablebox.active():
        path = cablebox.fetch(path)           # artwork lives on the head-end
        if not path:
            return None
    key = (path, max_side)
    if key in _img_cache:
        _img_cache.move_to_end(key)
        return _img_cache[key]
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImageReader

    r = QImageReader(path)
    r.setAutoTransform(True)
    size = r.size()
    if size.isValid() and max(size.width(), size.height()) > max_side:
        k = max_side / max(size.width(), size.height())
        r.setScaledSize(QSize(max(1, int(size.width() * k)), max(1, int(size.height() * k))))
    img = r.read()
    _img_cache[key] = None if img.isNull() else img
    while len(_img_cache) > IMG_CACHE_MAX:
        _img_cache.popitem(last=False)
    return _img_cache[key]


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _fit(text: str, kind: str, px: int, max_w: float) -> int:
    while px > 18 and QFontMetricsF(font(kind, px)).horizontalAdvance(text) > max_w:
        px = int(px * 0.92)
    return px


def _text(p: QPainter, text: str, px: int, cx: float, base: float, color: QColor, kind: str = "text",
          outline: QColor | None = QColor(0, 0, 0, 200), align: str = "c") -> float:
    p.setFont(font(kind, px))
    w = QFontMetricsF(p.font()).horizontalAdvance(text)
    x = cx - w / 2 if align == "c" else cx
    if outline is not None:
        p.setPen(outline)
        for dx, dy in ((3, 3), (-2, 2), (2, -2), (-2, -2)):
            p.drawText(QPointF(x + dx * px / 60, base + dy * px / 60), text)
    p.setPen(color)
    p.drawText(QPointF(x, base), text)
    return w


def _wrap(text: str, px: int, max_w: float, lines: int) -> list[str]:
    fm = QFontMetricsF(font("text", px))
    out, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if fm.horizontalAdvance(trial) > max_w and cur:
            out.append(cur)
            cur = word
            if len(out) == lines:
                break
        else:
            cur = trial
    if cur and len(out) < lines:
        out.append(cur)
    if len(out) == lines and len(" ".join(out)) < len(text):
        out[-1] = fm.elidedText(out[-1] + " …", Qt.ElideRight, max_w)
    return out


def paint(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    kind = info.get("kind")
    p.save()
    if kind == "ad":
        ad = info["ad"]
        {"food": _food, "soda": _soda, "tech": _tech, "kids": _kids, "local": _local}.get(ad.kind, _hero)(
            p, w, h, ad, t, dur)
    elif kind == "promo":
        _promo(p, w, h, info, t, dur)
    elif kind == "trivia":
        _trivia(p, w, h, info, t, dur)
    elif kind == "lineup":
        _lineup(p, w, h, info, t, dur)
    elif kind == "onthisday":
        _on_this_day(p, w, h, info, t, dur)
    elif kind == "id":
        _station_id(p, w, h, info, t, dur)
    elif kind in ("spotlight", "stills", "wall"):
        from . import art_paint

        getattr(art_paint, kind)(p, w, h, info, t, dur)
    elif info.get("art"):
        from .art_paint import sting_art

        sting_art(p, w, h, info, t, dur)
    else:
        _sting(p, w, h, info, t, dur)
    p.restore()


# --------------------------------------------------------------------------- shared ad scenes

def _scenes(p: QPainter, w: int, h: int, ad, t: float, dur: float, color: QColor, hero_y: float = 0.36) -> None:
    """The beats of a real commercial: hook, product reveal, selling points, a testimonial,
    then the price slam and call to action."""
    unit = h / 1080
    c1 = QColor(ad.colors[1])
    beats = [0.0, 0.16, 0.38, 0.60, 0.80, 1.0]
    k = next(i for i in range(5) if t < beats[i + 1] * dur or i == 4)
    local = t - beats[k] * dur
    span = (beats[k + 1] - beats[k]) * dur
    if k == 0 and ad.hook:                                   # the hook: a question in your face
        s = _ease(local / 0.5)
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, 120))
        px = _fit(ad.hook, "text", int(96 * unit), w * 0.86)
        p.setOpacity(s)
        _text(p, ad.hook, px, w / 2 + (1 - s) * 60 * unit, h * 0.52, QColor("#ffffff"))
        p.setOpacity(1)
    elif k <= 1:                                             # product reveal
        s = _ease(local / 0.7)
        px = _fit(ad.name.upper(), "numbers", int(130 * unit), w * 0.86)
        p.save()
        p.translate(w / 2, h * hero_y)
        p.scale(0.3 + 0.7 * s, 0.3 + 0.7 * s)
        p.rotate((1 - s) * -8)
        _text(p, ad.name.upper(), px, 0, 0, color, "numbers")
        p.restore()
        if local > 0.8:
            _text(p, ad.tagline, _fit(ad.tagline, "text", int(54 * unit), w * 0.86), w / 2, h * 0.62, c1)
    elif k == 2:                                             # selling points slide in
        _text(p, ad.name.upper(), _fit(ad.name.upper(), "numbers", int(70 * unit), w * 0.8), w / 2, h * 0.17,
              color, "numbers")
        step = span / max(1, len(ad.lines))
        for i, line in enumerate(ad.lines):
            q = (local - i * step) / 0.45
            if q <= 0:
                continue
            x = w / 2 + (1 - _ease(q)) * w
            _text(p, "✓ " + line, _fit("✓ " + line, "text", int(72 * unit), w * 0.88), x, h * (0.46 + 0.15 * i),
                  QColor("#ffffff"))
    elif k == 3 and ad.quote:                                # testimonial
        s = _ease(local / 0.5)
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, 110))
        face = QPointF(w * 0.17, h * 0.50)
        r = 110 * unit * s
        p.setPen(QPen(QColor("#ffffff"), 5 * unit))
        p.setBrush(c1)
        p.drawEllipse(face, r, r)
        p.setBrush(QColor(20, 10, 30))
        p.drawEllipse(QPointF(face.x() - r * 0.35, face.y() - r * 0.2), r * 0.1, r * 0.1)
        p.drawEllipse(QPointF(face.x() + r * 0.35, face.y() - r * 0.2), r * 0.1, r * 0.1)
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(face.x() - r * 0.5, face.y() - r * 0.3, r, r * 0.9), 200 * 16, 140 * 16)
        bw_ = w * 0.64
        lines = _wrap(f"“{ad.quote}”", int(58 * unit), bw_ * 0.88, 3)
        bh_ = (len(lines) * 74 + 70) * unit
        bubble = QRectF(w * 0.30, h * 0.46 - bh_ / 2, bw_, bh_)
        p.setPen(QPen(QColor("#ffffff"), 4 * unit))
        p.setBrush(QColor(255, 255, 255, 240))
        p.setOpacity(s)
        p.drawRoundedRect(bubble, 30 * unit, 30 * unit)
        y = bubble.top() + 35 * unit + 58 * unit
        for line in lines:
            _text(p, line, int(58 * unit), bubble.center().x(), y, QColor(20, 10, 30), outline=None)
            y += 74 * unit
        _text(p, "— " + ad.who, int(40 * unit), bubble.center().x(), bubble.bottom() + 70 * unit, QColor(YELLOW))
        p.setOpacity(1)
    else:                                                    # price slam + call to action
        burst = _ease(local / 0.35)
        if ad.slam:
            cx, cy, r = w * 0.5, h * 0.34, 220 * unit * (0.4 + 0.6 * burst) * (1 + 0.04 * math.sin(t * 12))
            poly = QPolygonF()
            for i in range(28):
                rr = r * (1.0 if i % 2 == 0 else 0.74)
                a = math.radians(i * 360 / 28 + t * 20)
                poly.append(QPointF(cx + rr * math.cos(a) * 1.5, cy + rr * math.sin(a)))
            p.setPen(QPen(QColor("#ffffff"), 6 * unit))
            p.setBrush(QColor(YELLOW))
            p.drawPolygon(poly)
            _text(p, ad.slam, _fit(ad.slam, "numbers", int(110 * unit), r * 2.4), cx, cy + 36 * unit,
                  QColor("#ff2b2b"), "numbers", outline=None)
        _text(p, ad.name.upper(), _fit(ad.name.upper(), "numbers", int(64 * unit), w * 0.8), w / 2, h * 0.70,
              color, "numbers")
        if ad.kind != "local":            # local ads flash their own call-to-action bar
            _text(p, ad.closer, _fit(ad.closer, "text", int(52 * unit), w * 0.9), w / 2, h * 0.82, c1)


def _burst(p: QPainter, w: int, h: int, c0: QColor, c1: QColor, t: float, cx: float, cy: float, rays: int = 18) -> None:
    p.fillRect(QRectF(0, 0, w, h), c0.darker(250))
    r = math.hypot(w, h)
    rot = t * 18
    p.setPen(Qt.NoPen)
    for i in range(rays):
        a0 = math.radians(rot + i * 360 / rays)
        a1 = math.radians(rot + (i + 0.5) * 360 / rays)
        poly = QPolygonF([QPointF(cx, cy), QPointF(cx + r * math.cos(a0), cy + r * math.sin(a0)),
                          QPointF(cx + r * math.cos(a1), cy + r * math.sin(a1))])
        p.setBrush(c0 if i % 2 else c1.darker(160))
        p.drawPolygon(poly)
    g = QRadialGradient(QPointF(cx, cy), r * 0.35)
    g.setColorAt(0, QColor(255, 255, 255, 120))
    g.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillRect(QRectF(0, 0, w, h), g)


# --------------------------------------------------------------------------- ad looks

def _food(p, w, h, ad, t, dur):
    c0, c1 = QColor(ad.colors[0]), QColor(ad.colors[1])
    _burst(p, w, h, c0, c1, t, w / 2, h * 0.45)
    # cereal-box / pastry-box shape bouncing in the corner
    unit = h / 1080
    bob = math.sin(t * 4) * 12 * unit
    box = QRectF(w * 0.78, h * 0.58 + bob, w * 0.15, h * 0.34)
    p.setPen(QPen(QColor("#ffffff"), 4 * unit))
    g = QLinearGradient(box.topLeft(), box.bottomRight())
    g.setColorAt(0, c0)
    g.setColorAt(1, c0.darker(170))
    p.setBrush(g)
    p.drawRoundedRect(box, 10 * unit, 10 * unit)
    _text(p, ad.name.split()[0].upper(), _fit(ad.name.split()[0].upper(), "numbers", int(40 * unit), box.width() * 0.9),
          box.center().x(), box.top() + box.height() * 0.4, c1, "numbers")
    p.setPen(Qt.NoPen)
    rng = random.Random(7)
    for _ in range(14):
        x = box.left() + rng.random() * box.width()
        y = box.top() + box.height() * (0.55 + 0.4 * rng.random())
        p.setBrush(QColor.fromHsv(rng.randrange(360), 200, 255))
        p.drawEllipse(QPointF(x, y), 7 * unit, 7 * unit)
    _scenes(p, w, h, ad, t, dur, QColor("#ffffff"))


def _soda(p, w, h, ad, t, dur):
    c0, c1 = QColor(ad.colors[0]), QColor(ad.colors[1])
    unit = h / 1080
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, c0.darker(200))
    g.setColorAt(1, QColor(5, 0, 18))
    p.fillRect(QRectF(0, 0, w, h), g)
    rng = random.Random(3)
    p.setPen(QPen(QColor(255, 255, 255, 140), 2 * unit))
    p.setBrush(QColor(255, 255, 255, 30))
    for i in range(70):
        x = rng.random() * w
        sp = 0.15 + rng.random() * 0.35
        y = h - ((t * sp + rng.random()) % 1.0) * h * 1.1
        r = (4 + rng.random() * 12) * unit
        p.drawEllipse(QPointF(x + math.sin(t * 3 + i) * 8 * unit, y), r, r)
    # the can
    can = QRectF(w * 0.06, h * 0.30, w * 0.13, h * 0.50)
    cg = QLinearGradient(can.topLeft(), can.topRight())
    cg.setColorAt(0, c0.darker(150))
    cg.setColorAt(0.35, c0.lighter(140))
    cg.setColorAt(1, c0.darker(170))
    p.setPen(QPen(QColor(220, 220, 230), 3 * unit))
    p.setBrush(cg)
    p.drawRoundedRect(can, 18 * unit, 18 * unit)
    p.save()
    p.translate(can.center())
    p.rotate(-90)
    _text(p, ad.name.split()[0].upper(), _fit(ad.name.split()[0].upper(), "numbers", int(56 * unit), can.height() * 0.85),
          0, 20 * unit, c1, "numbers")
    p.restore()
    _scenes(p, w, h, ad, t, dur, c1)


def _tech(p, w, h, ad, t, dur):
    c0, c1 = QColor(ad.colors[0]), QColor(ad.colors[1])
    unit = h / 1080
    p.fillRect(QRectF(0, 0, w, h), QColor(4, 6, 30))
    # perspective grid floor
    hz = h * 0.62
    p.setPen(QPen(QColor(c0.red(), c0.green(), c0.blue(), 150), 1.5))
    for i in range(-20, 21):
        p.drawLine(QPointF(w / 2 + i * 12 * unit, hz), QPointF(w / 2 + i * 160 * unit, h))
    off = (t * 0.6) % 1.0
    for k in range(12):
        f = ((k + off) / 12) ** 2
        y = hz + f * (h - hz)
        p.drawLine(QPointF(0, y), QPointF(w, y))
    # rotating wireframe cube
    cx, cy, s = w * 0.88, h * 0.80, 90 * unit
    ay, ax = t * 0.9, 0.5
    pts = []
    for x in (-1, 1):
        for y in (-1, 1):
            for z in (-1, 1):
                x1 = x * math.cos(ay) - z * math.sin(ay)
                z1 = x * math.sin(ay) + z * math.cos(ay)
                y1 = y * math.cos(ax) - z1 * math.sin(ax)
                z2 = y * math.sin(ax) + z1 * math.cos(ax)
                k = 3 / (z2 + 4)
                pts.append(QPointF(cx + x1 * s * k, cy + y1 * s * k))
    p.setPen(QPen(c1, 3 * unit))
    for i in range(8):
        for j in range(i + 1, 8):
            if bin(i ^ j).count("1") == 1:
                p.drawLine(pts[i], pts[j])
    _scenes(p, w, h, ad, t, dur, c1, hero_y=0.40)
    # blinking cursor line along the bottom
    if int(t * 2) % 2:
        p.fillRect(QRectF(w * 0.05, h * 0.9, 24 * unit, 6 * unit), c1)


def _kids(p, w, h, ad, t, dur):
    c0, c1 = QColor(ad.colors[0]), QColor(ad.colors[1])
    unit = h / 1080
    p.fillRect(QRectF(0, 0, w, h), c0)
    stripe = 90 * unit
    off = (t * 160 * unit) % (stripe * 2)
    p.setPen(Qt.NoPen)
    p.setBrush(c1)
    x = -h - stripe * 2 + off
    while x < w + h:
        p.drawPolygon(QPolygonF([QPointF(x, 0), QPointF(x + stripe, 0), QPointF(x + stripe + h, h), QPointF(x + h, h)]))
        x += stripe * 2
    p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, 140))
    # zig-zag "NEW!" starburst
    cx, cy, r = w * 0.84, h * 0.20, 110 * unit
    pulse = 1 + 0.08 * math.sin(t * 9)
    poly = QPolygonF()
    for i in range(24):
        rr = r * pulse * (1.0 if i % 2 == 0 else 0.72)
        a = math.radians(i * 15 + t * 30)
        poly.append(QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a)))
    p.setBrush(QColor(YELLOW))
    p.setPen(QPen(QColor("#ff2b2b"), 5 * unit))
    p.drawPolygon(poly)
    _text(p, "NEW!", int(56 * unit), cx, cy + 20 * unit, QColor("#ff2b2b"), "numbers", outline=None)
    _scenes(p, w, h, ad, t, dur, QColor("#ffffff"))
    # tiny legal line, as tradition demands
    p.setFont(font("text", int(22 * unit)))
    p.setPen(QColor(255, 255, 255, 200))
    p.drawText(QPointF(w * 0.04, h * 0.96), "Each sold separately. Parents, ask before you call.")


def _local(p, w, h, ad, t, dur):
    c0, c1 = QColor(ad.colors[0]), QColor(ad.colors[1])
    unit = h / 1080
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, c0.darker(260))
    g.setColorAt(1, QColor(10, 0, 30))
    p.fillRect(QRectF(0, 0, w, h), g)
    # cheap "video toaster" bevel frame
    p.setPen(QPen(c1, 10 * unit))
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(30 * unit, 30 * unit, w - 60 * unit, h - 60 * unit))
    _scenes(p, w, h, ad, t, dur, c0.lighter(150))
    # flashing call-to-action bar
    if t > dur * 0.82:
        bar = QRectF(0, h * 0.86, w, h * 0.10)
        p.fillRect(bar, QColor(255, 230, 0) if int(t * 3) % 2 else QColor(255, 60, 60))
        _text(p, ad.closer.upper(), _fit(ad.closer.upper(), "numbers", int(64 * unit), w * 0.9), w / 2,
              bar.center().y() + 22 * unit, QColor(10, 0, 30), "numbers", outline=None)


def _hero(p, w, h, ad, t, dur):
    c0, c1 = QColor(ad.colors[0]), QColor(ad.colors[1])
    unit = h / 1080
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(10, 0, 30))
    g.setColorAt(0.6, c0.darker(260))
    g.setColorAt(1, QColor(5, 0, 15))
    p.fillRect(QRectF(0, 0, w, h), g)
    # speed lines
    rng = random.Random(11)
    for i in range(60):
        y = rng.random() * h
        ln = (80 + rng.random() * 300) * unit
        x = (w + ln) - ((t * (600 + rng.random() * 900) * unit + rng.random() * w) % (w + 2 * ln))
        col = QColor(c1 if i % 3 else c0)
        col.setAlpha(150)
        p.setPen(QPen(col, (2 + rng.random() * 3) * unit))
        p.drawLine(QPointF(x, y), QPointF(x + ln, y))
    _scenes(p, w, h, ad, t, dur, QColor("#ffffff"))


# --------------------------------------------------------------------------- channel bits

def _station_id(p, w, h, info, t, dur):
    unit = h / 1080
    g = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.7)
    g.setColorAt(0, QColor(40, 6, 90))
    g.setColorAt(1, QColor(4, 0, 14))
    p.fillRect(QRectF(0, 0, w, h), g)
    s = _ease(t / 0.5)
    num = str(info.get("channel", ""))
    p.setOpacity(s)
    draw_neon(p, num, "numbers", int(300 * unit), PINK, w / 2, h * 0.42, "cm", flicker=True)
    p.setOpacity(1)
    if dur >= 4:
        _text(p, "YOU'RE WATCHING", int(44 * unit), w / 2, h * 0.16, QColor(CYAN))
    tw = QFontMetricsF(font("text", int(70 * unit))).horizontalAdvance(f"{paths.APP_NAME.upper()}")
    _chrome_text(p, f"{paths.APP_NAME.upper()}", int(70 * unit), w / 2 - tw / 2, h * 0.80)


def _sting(p, w, h, info, t, dur):
    """"We'll be right back" / "Now back to ..." / "Stay tuned" between ads and the show."""
    unit = h / 1080
    p.fillRect(QRectF(0, 0, w, h), QColor(6, 0, 20))
    sweep = (t / max(0.5, dur)) * w * 1.4 - w * 0.2
    g = QLinearGradient(sweep - w * 0.3, 0, sweep + w * 0.3, 0)
    g.setColorAt(0, QColor(0, 0, 0, 0))
    g.setColorAt(0.5, QColor(255, 43, 214, 90))
    g.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(QRectF(0, 0, w, h), g)
    title = info.get("title", "")
    if info["kind"] == "brb":
        draw_neon(p, "WE'LL BE RIGHT BACK", "numbers", _fit("WE'LL BE RIGHT BACK", "numbers", int(110 * unit), w * 0.9),
                  CYAN, w / 2, h * 0.40, "cm")
        if title:
            _text(p, f"with more {title}", _fit(f"with more {title}", "text", int(50 * unit), w * 0.86),
                  w / 2, h * 0.62, QColor(YELLOW))
    elif info["kind"] == "back" and title:
        _text(p, "NOW BACK TO", int(50 * unit), w / 2, h * 0.34, QColor(CYAN))
        px = _fit(title, "text", int(110 * unit), w * 0.86)
        tw = QFontMetricsF(font("text", px)).horizontalAdvance(title)
        _chrome_text(p, title, px, w / 2 - tw / 2, h * 0.56)
    else:
        draw_neon(p, "STAY TUNED", "numbers", int(120 * unit), PINK, w / 2, h * 0.42, "cm")
    _text(p, f"CHANNEL {info.get('channel', '')}", int(34 * unit), w / 2, h * 0.86, QColor(PINK))


def _promo(p, w, h, info, t, dur):
    unit = h / 1080
    pr = info.get("promo", {})
    fan, poster = _image(pr.get("fanart", "")), _image(pr.get("poster", ""))
    p.fillRect(QRectF(0, 0, w, h), QColor(6, 0, 20))
    if fan is not None:
        # slow push-in on the backdrop
        z = 1.05 + 0.06 * (t / max(1.0, dur))
        sw, sh = fan.width(), fan.height()
        scale = max(w / sw, h / sh) * z
        p.drawImage(QRectF((w - sw * scale) / 2, (h - sh * scale) / 2, sw * scale, sh * scale), fan)
    p.fillRect(QRectF(0, 0, w, h), QColor(6, 0, 20, 170 if fan is not None else 0))
    # poster with a neon edge
    left = w * 0.06
    if poster is not None:
        ph = h * 0.66
        pw = ph * poster.width() / max(1, poster.height())
        slide = (1 - _ease(t / 0.8)) * -pw
        r = QRectF(left + slide, h * 0.17, pw, ph)
        p.drawImage(r, poster)
        p.setPen(QPen(QColor(CYAN), 3 * unit))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        left = r.right() + w * 0.04
    text_w = w * 0.94 - left
    _text(p, "COMING UP ON CHANNEL " + str(pr.get("channel", info.get("channel", ""))), int(36 * unit), left,
          h * 0.20, QColor(PINK), align="l")
    title = pr.get("title", "")
    px = _fit(title, "text", int(104 * unit), text_w)
    _chrome_text(p, title, px, left, h * 0.32)
    meta = "  ·  ".join(x for x in (pr.get("year", ""), " / ".join(pr.get("genres", []))) if x)
    if meta:
        _text(p, meta, int(38 * unit), left, h * 0.40, QColor(YELLOW), align="l")
    # the episode that's coming up (looked up live) and its own synopsis; else the show's plot
    ep = info.get("next_ep", "")
    y = h * 0.50
    if ep and t > 0.8:
        _text(p, ep, _fit(ep, "text", int(42 * unit), text_w), left, y, QColor(CYAN), align="l")
    if ep:
        y += 60 * unit
    plot = info.get("next_plot") or pr.get("plot", "")
    if plot and t > 1.2:
        for line in _wrap(plot, int(38 * unit), text_w, 4):
            _text(p, line, int(38 * unit), left, y, QColor("#ffffff"), align="l")
            y += 52 * unit
    when = info.get("when", "")
    if when and t > dur * 0.45:
        bar = QRectF(left - 20 * unit, h * 0.78, text_w + 20 * unit, 90 * unit)
        p.fillRect(bar, QColor(255, 43, 214, 200))
        _text(p, when.upper(), _fit(when.upper(), "numbers", int(56 * unit), bar.width() * 0.92), left,
              bar.center().y() + 20 * unit, QColor("#ffffff"), "numbers", outline=None, align="l")


# --------------------------------------------------------------------------- show cards

def _card_backdrop(p: QPainter, w: int, h: int, fanart: str, t: float, dur: float) -> None:
    p.fillRect(QRectF(0, 0, w, h), QColor(6, 0, 20))
    fan = _image(fanart)
    if fan is not None:
        z = 1.05 + 0.06 * (t / max(1.0, dur))
        sw, sh = fan.width(), fan.height()
        scale = max(w / sw, h / sh) * z
        p.drawImage(QRectF((w - sw * scale) / 2, (h - sh * scale) / 2, sw * scale, sh * scale), fan)
        p.fillRect(QRectF(0, 0, w, h), QColor(6, 0, 20, 185))
    else:
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor(30, 6, 70))
        g.setColorAt(1, QColor(6, 0, 20))
        p.fillRect(QRectF(0, 0, w, h), g)
        p.setPen(QPen(QColor(123, 44, 255, 40), 1))
        step = max(24, w // 40)
        for x in range(0, w, step):
            p.drawLine(QPointF(x, 0), QPointF(x, h))
        for y in range(0, h, step):
            p.drawLine(QPointF(0, y), QPointF(w, y))


def _header(p: QPainter, w: int, h: int, text: str, color=CYAN) -> None:
    unit = h / 1080
    p.fillRect(QRectF(0, h * 0.06, w * 0.62, 8 * unit), QColor(PINK))
    draw_neon(p, text, "numbers", _fit(text, "numbers", int(84 * unit), w * 0.9), color, w * 0.06, h * 0.08, "tl",
              flicker=True)


def _poster_left(p: QPainter, w: int, h: int, path: str, t: float) -> float:
    """Draw the poster on the left (sliding in); returns where the text column starts."""
    unit = h / 1080
    poster = _image(path)
    if poster is None:
        return w * 0.08
    ph = h * 0.58
    pw = ph * poster.width() / max(1, poster.height())
    slide = (1 - _ease(t / 0.7)) * -pw
    r = QRectF(w * 0.06 + slide, h * 0.27, pw, ph)
    p.drawImage(r, poster)
    p.setPen(QPen(QColor(CYAN), 3 * unit))
    p.setBrush(Qt.NoBrush)
    p.drawRect(r)
    return r.right() + w * 0.04


def _trivia(p, w, h, info, t, dur):
    unit = h / 1080
    _card_backdrop(p, w, h, info.get("fanart", ""), t, dur)
    _header(p, w, h, "DID YOU KNOW?")
    left = _poster_left(p, w, h, info.get("poster", ""), t)
    text_w = w * 0.94 - left
    fact = info.get("fact", "")
    px = int(64 * unit)
    lines = _wrap(fact, px, text_w, 5)
    while len(lines) >= 5 and px > 30:
        px = int(px * 0.9)
        lines = _wrap(fact, px, text_w, 5)
    shown = int(max(0.0, t - 0.6) * 45)          # typed on
    y = h * 0.40
    used = 0
    for line in lines:
        part = line[: max(0, shown - used)]
        used += len(line)
        if part:
            _text(p, part, px, left, y, QColor("#ffffff"), align="l")
        y += px * 1.35
    if info.get("title") and t > 1.5:
        _text(p, "— " + info["title"], int(42 * unit), left, min(h * 0.88, y + 40 * unit), QColor(YELLOW), align="l")
    _text(p, f"CHANNEL {info.get('channel', '')}", int(34 * unit), w * 0.88, h * 0.95, QColor(PINK))


def _lineup(p, w, h, info, t, dur):
    unit = h / 1080
    _card_backdrop(p, w, h, "", t, dur)
    progs = info.get("progs")
    title = "TONIGHT ON " + str(info.get("channel", "")) if info.get("evening") else \
        "COMING UP ON " + str(info.get("channel", ""))
    _header(p, w, h, title)
    if progs is None:
        _text(p, "…", int(60 * unit), w / 2, h * 0.5, QColor("#ffffff"))
        return
    y = h * 0.34
    for i, (when, name, sub) in enumerate(progs[:4]):
        q = (t - 0.4 - i * 0.35) / 0.4
        if q <= 0:
            continue
        x = w * 0.08 + (1 - _ease(q)) * w * 0.5
        row = QRectF(x, y - 70 * unit, w * 0.84, 120 * unit)
        p.fillRect(row, QColor(255, 43, 214, 60) if i == 0 else QColor(20, 8, 52, 200))
        p.fillRect(QRectF(row.left(), row.top(), 8 * unit, row.height()), QColor(PINK if i == 0 else CYAN))
        draw_neon(p, when, "text", int(54 * unit), YELLOW, row.left() + 30 * unit, row.top() + 20 * unit, "tl")
        tx = row.left() + 330 * unit
        _text(p, name, _fit(name, "text", int(58 * unit), row.width() - 360 * unit), tx, row.top() + 62 * unit,
              QColor("#ffffff"), align="l")
        if sub:
            _text(p, sub, _fit(sub, "text", int(32 * unit), row.width() - 360 * unit), tx, row.top() + 104 * unit,
                  QColor(CYAN), align="l")
        y += 150 * unit


def _on_this_day(p, w, h, info, t, dur):
    unit = h / 1080
    items = info.get("items") or []
    _card_backdrop(p, w, h, items[0].get("fanart", "") if items else "", t, dur)
    _header(p, w, h, info.get("heading", "ON THIS DAY"), YELLOW)
    import datetime as dt

    today = dt.date.today()
    _text(p, f"{today.strftime('%B')} {today.day}", int(44 * unit), w * 0.06, h * 0.25, QColor(CYAN), align="l")
    left = _poster_left(p, w, h, items[0].get("poster", "") if items else "", t)
    y = h * 0.38
    for i, it in enumerate(items[:3]):
        q = (t - 0.6 - i * 0.5) / 0.5
        if q <= 0:
            continue
        p.setOpacity(_ease(q))
        year = it.get("date", "")[:4]
        draw_neon(p, year, "numbers", int(76 * unit), PINK, left, y - 70 * unit, "tl")
        tx = left + 260 * unit
        name = f"{it.get('show', '')}  {it.get('code', '')}"
        _text(p, name, _fit(name, "text", int(50 * unit), w * 0.94 - tx), tx, y, QColor("#ffffff"), align="l")
        if it.get("title"):
            ep = f"“{it['title']}”"
            _text(p, ep, _fit(ep, "text", int(38 * unit), w * 0.94 - tx), tx, y + 50 * unit, QColor(CYAN), align="l")
        if year.isdigit():
            ago = today.year - int(year)
            _text(p, f"{ago} years ago" if ago != 1 else "1 year ago", int(30 * unit), tx, y + 92 * unit,
                  QColor(YELLOW), align="l")
        p.setOpacity(1)
        y += 190 * unit
