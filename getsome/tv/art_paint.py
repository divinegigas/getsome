"""Title cards and transitions built from the owner's own artwork (artwork.py):
fanart backdrops with a slow Ken Burns pan, clear logos, posters and episode stills.

  upnext_art     "UP NEXT" bumper: the next show's backdrop, logo and episode still
  sting_art      "We'll be right back" / "Now back to" over the show's backdrop and logo
  spotlight      card: one show's backdrop and logo, "only on channel N", when it's next on
  stills         card: a filmstrip of a show's episode stills with their titles
  wall           card: a channel ID over moving columns of every poster on the channel
Only the owner's local files are shown; the rest is drawn in code.
"""

from __future__ import annotations

import zlib

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QLinearGradient, QPainter, QPen
from ..brand import QColor

from .. import paths  # noqa: E402
from .ads_paint import _ease, _fit, _image, _text
from .overlays import CYAN, PINK, YELLOW, _chrome_text, draw_neon, font

BG = QColor(6, 0, 20)


def _seed(s: str) -> int:
    return zlib.crc32(s.encode("utf-8", "replace"))


def kenburns(p: QPainter, w: int, h: int, path: str, t: float, dur: float, dim: int = 150) -> bool:
    """Full-screen backdrop with a slow zoom and pan (direction picked per image). False if no image."""
    img = _image(path)
    p.fillRect(QRectF(0, 0, w, h), BG)
    if img is None:
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor(30, 6, 70))
        g.setColorAt(1, BG)
        p.fillRect(QRectF(0, 0, w, h), g)
        return False
    k = _seed(path)
    f = max(0.0, min(1.0, t / max(1.0, dur)))
    z0, z1 = (1.06, 1.20) if k & 1 else (1.20, 1.06)
    z = z0 + (z1 - z0) * f
    dx = (1 if k & 2 else -1) * (f - 0.5)
    dy = (1 if k & 4 else -1) * (f - 0.5) * 0.5
    sw, sh = img.width(), img.height()
    scale = max(w / sw, h / sh) * z
    tw, th = sw * scale, sh * scale
    x = (w - tw) / 2 + dx * (tw - w)
    y = (h - th) / 2 + dy * (th - h)
    p.drawImage(QRectF(x, y, tw, th), img)
    if dim:
        p.fillRect(QRectF(0, 0, w, h), QColor(6, 0, 20, dim))
    return True


def _scan(p: QPainter, w: int, h: int, alpha: int = 28) -> None:
    p.setPen(QPen(QColor(0, 0, 0, alpha), 1))
    for y in range(0, h, 3):
        p.drawLine(0, y, w, y)


def logo_or_title(p: QPainter, logo: str, title: str, cx: float, cy: float, max_w: float, max_h: float,
                  appear: float = 1.0, align: str = "c") -> QRectF:
    """The show's clear logo fitted in a box (scaling in with `appear` 0..1), else its title in chrome."""
    a = _ease(appear)
    img = _image(logo)
    if img is not None:
        s = min(max_w / img.width(), max_h / img.height()) * (0.85 + 0.15 * a)
        lw, lh = img.width() * s, img.height() * s
        x = cx - lw / 2 if align == "c" else cx
        r = QRectF(x, cy - lh / 2, lw, lh)
        p.setOpacity(a)
        p.drawImage(r, img)
        p.setOpacity(1)
        return r
    if not title:
        return QRectF(cx, cy, 0, 0)
    px = _fit(title, "text", int(max_h * 0.62), max_w)
    tw = QFontMetricsF(font("text", px)).horizontalAdvance(title)
    x = cx - tw / 2 if align == "c" else cx
    p.setOpacity(a)
    _chrome_text(p, title, px, x, cy + px * 0.35)
    p.setOpacity(1)
    return QRectF(x, cy - px / 2, tw, px)


def _framed(p: QPainter, r: QRectF, path: str, unit: float, color: QColor = CYAN) -> bool:
    img = _image(path, 960)
    if img is None:
        return False
    # fill the frame (crop to its shape)
    s = max(r.width() / img.width(), r.height() / img.height())
    src_w, src_h = r.width() / s, r.height() / s
    src = QRectF((img.width() - src_w) / 2, (img.height() - src_h) / 2, src_w, src_h)
    p.fillRect(r.adjusted(8 * unit, 8 * unit, 8 * unit, 8 * unit), QColor(0, 0, 0, 150))
    p.drawImage(r, img, src)
    p.setPen(QPen(color, 4 * unit))
    p.setBrush(Qt.NoBrush)
    p.drawRect(r)
    return True


# --------------------------------------------------------------------------- transitions

def upnext_art(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    unit = h / 1080
    art = info.get("art", {})
    kenburns(p, w, h, art.get("fanart") or art.get("landscape") or art.get("still", ""), t, dur, dim=140)
    # left-to-right shade so the text reads
    g = QLinearGradient(0, 0, w, 0)
    g.setColorAt(0, QColor(6, 0, 20, 220))
    g.setColorAt(0.65, QColor(6, 0, 20, 60))
    g.setColorAt(1, QColor(6, 0, 20, 0))
    p.fillRect(QRectF(0, 0, w, h), g)
    _scan(p, w, h)
    x0 = w * 0.07
    a = _ease(t / 0.6)
    p.fillRect(QRectF(x0, h * 0.17, w * 0.30 * a, 8 * unit), QColor(PINK))
    draw_neon(p, "UP NEXT", "numbers", int(110 * unit), PINK, x0, h * 0.19, "tl", flicker=True)
    r = logo_or_title(p, art.get("logo", ""), info.get("title", ""), x0, h * 0.50, w * 0.48, h * 0.24,
                      (t - 0.5) / 1.0, align="l")
    y = max(r.bottom(), h * 0.56) + 60 * unit
    if info.get("episode") and t > 1.4:
        _text(p, info["episode"], _fit(info["episode"], "text", int(46 * unit), w * 0.5), x0, y, QColor(YELLOW),
              align="l")
        y += 64 * unit
    when = "  ·  ".join(b for b in (info.get("start", ""), f"CHANNEL {info.get('channel', '')}") if b)
    if t > 1.8:
        _text(p, when, int(40 * unit), x0, y, QColor(CYAN), align="l")
    still = art.get("still") or (art.get("thumbs") or [("", "")])[0][0]
    if still and t > 2.0:
        k = _ease((t - 2.0) / 0.7)
        sw = w * 0.32
        r = QRectF(w * 0.62 + (1 - k) * w * 0.4, h * 0.56, sw, sw * 9 / 16)
        _framed(p, r, still, unit)


def sting_art(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    """Break stings over the show's own backdrop. Kinds brb / back / stay (like ads_paint._sting)."""
    unit = h / 1080
    art = info.get("art", {})
    kenburns(p, w, h, art.get("fanart") or art.get("landscape", ""), t, max(dur, 6.0), dim=170)
    _scan(p, w, h)
    sweep = (t / max(0.5, dur)) * w * 1.4 - w * 0.2
    g = QLinearGradient(sweep - w * 0.3, 0, sweep + w * 0.3, 0)
    g.setColorAt(0, QColor(0, 0, 0, 0))
    g.setColorAt(0.5, QColor(255, 43, 214, 70))
    g.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(QRectF(0, 0, w, h), g)
    title = info.get("title", "")
    kind = info.get("kind")
    if kind == "brb":
        draw_neon(p, "WE'LL BE RIGHT BACK", "numbers", _fit("WE'LL BE RIGHT BACK", "numbers", int(100 * unit), w * 0.9),
                  CYAN, w / 2, h * 0.30, "cm")
        _text(p, "WITH MORE", int(40 * unit), w / 2, h * 0.46, QColor(YELLOW))
        logo_or_title(p, art.get("logo", ""), title, w / 2, h * 0.62, w * 0.55, h * 0.20, t / 0.8)
    elif kind == "back":
        _text(p, "NOW BACK TO", int(50 * unit), w / 2, h * 0.24, QColor(CYAN))
        logo_or_title(p, art.get("logo", ""), title, w / 2, h * 0.50, w * 0.70, h * 0.32, t / 0.8)
    else:
        draw_neon(p, "STAY TUNED", "numbers", int(120 * unit), PINK, w / 2, h * 0.42, "cm")
    _text(p, f"CHANNEL {info.get('channel', '')}", int(34 * unit), w / 2, h * 0.88, QColor(PINK))


# --------------------------------------------------------------------------- cards

def spotlight(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    unit = h / 1080
    # first the picture on its own, then it dims under the logo
    dim = int(40 + 120 * _ease((t - 1.5) / 1.2))
    kenburns(p, w, h, info.get("fanart", ""), t, dur, dim=dim)
    _scan(p, w, h, 20)
    logo_or_title(p, info.get("logo", ""), info.get("title", ""), w / 2, h * 0.40, w * 0.62, h * 0.30,
                  (t - 1.6) / 1.0)
    meta = "  ·  ".join(x for x in (info.get("year", ""), " / ".join(info.get("genres", [])[:3])) if x)
    if meta and t > 2.6:
        _text(p, meta, int(40 * unit), w / 2, h * 0.64, QColor(YELLOW))
    if t > 3.4:
        k = _ease((t - 3.4) / 0.6)
        bar = QRectF(w * 0.5 - w * 0.36 * k, h * 0.72, w * 0.72 * k, 96 * unit)
        p.fillRect(bar, QColor(255, 43, 214, 210))
        if k > 0.95:
            line = (info.get("when") or "").upper() or f"ONLY ON CHANNEL {info.get('channel', '')}"
            _text(p, line, _fit(line, "numbers", int(56 * unit), bar.width() * 0.9), w / 2,
                  bar.center().y() + 20 * unit, QColor("#ffffff"), "numbers", outline=None)
    _text(p, f"CHANNEL {info.get('channel', '')}  ·  {paths.APP_NAME.upper()}", int(30 * unit), w / 2, h * 0.94, QColor(CYAN))


def stills(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    unit = h / 1080
    kenburns(p, w, h, info.get("fanart", ""), t, dur, dim=200)
    items = info.get("stills", [])
    # header: logo/title + "from the vault"
    logo_or_title(p, info.get("logo", ""), info.get("title", ""), w * 0.06, h * 0.13, w * 0.40, h * 0.14,
                  t / 0.6, align="l")
    _text(p, "EPISODES FROM THE VAULT", int(38 * unit), w * 0.94 - QFontMetricsF(font("text", int(38 * unit)))
          .horizontalAdvance("EPISODES FROM THE VAULT"), h * 0.15, QColor(PINK), align="l")
    # the filmstrip
    band = QRectF(0, h * 0.27, w, h * 0.50)
    p.fillRect(band, QColor(12, 6, 18, 235))
    hole_w, hole_h = 26 * unit, 18 * unit
    fw = band.height() * 0.70 * 16 / 9
    gap = 60 * unit
    span = len(items) * (fw + gap)
    speed = (span + w) / max(4.0, dur - 1.0) if items else 0
    off = w - speed * max(0.0, t - 0.4)
    # sprocket holes move with the film
    p.setBrush(QColor(60, 40, 70))
    p.setPen(Qt.NoPen)
    step = 56 * unit
    start = off % step - step
    x = start
    while x < w:
        for y in (band.top() + 14 * unit, band.bottom() - 14 * unit - hole_h):
            p.drawRoundedRect(QRectF(x, y, hole_w, hole_h), 4 * unit, 4 * unit)
        x += step
    for i, it in enumerate(items):
        x = off + i * (fw + gap)
        if x > w or x + fw < 0:
            continue
        r = QRectF(x, band.top() + band.height() * 0.12, fw, band.height() * 0.70 * 0.86)
        _framed(p, r, it.get("thumb", ""), unit, QColor(255, 255, 255, 200))
        label = "  ".join(b for b in (it.get("code", ""), it.get("name", "")) if b)
        if label:
            px = _fit(label, "text", int(32 * unit), fw)
            _text(p, label, px, x + fw / 2, r.bottom() + 46 * unit, QColor(YELLOW))
    when = (info.get("when") or "").upper() or f"MORE {info.get('title', '').upper()} ON CHANNEL {info.get('channel', '')}"
    if t > 1.0:
        _text(p, when, _fit(when, "numbers", int(54 * unit), w * 0.86), w / 2, h * 0.90, QColor(CYAN), "numbers")


def wall(p: QPainter, w: int, h: int, info: dict, t: float, dur: float) -> None:
    unit = h / 1080
    p.fillRect(QRectF(0, 0, w, h), BG)
    posters = [x for x in info.get("posters", [])[:30] if _image(x, 480) is not None]
    if posters:
        cols = 7
        cw = w / cols
        ph = cw * 1.5
        k = 0
        for c in range(cols):
            direction = 1 if c % 2 else -1
            y0 = (direction * t * 40 * unit) % ph - ph
            y = y0
            while y < h:
                img = _image(posters[k % len(posters)], 480)
                k += 3
                r = QRectF(c * cw + 6 * unit, y + 6 * unit, cw - 12 * unit, ph - 12 * unit)
                p.drawImage(r, img)
                y += ph
        p.fillRect(QRectF(0, 0, w, h), QColor(6, 0, 20, 175))
    _scan(p, w, h, 40)
    a = _ease(t / 0.8)
    p.setOpacity(a)
    draw_neon(p, str(info.get("channel", "")), "numbers", int(330 * unit), PINK, w / 2, h * 0.44, "cm", flicker=True)
    p.setOpacity(1)
    _text(p, "YOU'RE WATCHING", int(46 * unit), w / 2, h * 0.16, QColor(CYAN))
    tw = QFontMetricsF(font("text", int(76 * unit))).horizontalAdvance(f"{paths.APP_NAME.upper()}")
    _chrome_text(p, f"{paths.APP_NAME.upper()}", int(76 * unit), w / 2 - tw / 2, h * 0.80)
    if info.get("name") and t > 1.2:
        _text(p, info["name"].upper(), _fit(info["name"].upper(), "text", int(44 * unit), w * 0.8), w / 2, h * 0.90,
              QColor(YELLOW))



# --------------------------------------------------------------------------- film cards (intermissions)

CARD_SECONDS = 12.0


def film_cards(p: QPainter, w: int, h: int, info: dict, t: float, remaining: float) -> None:
    """Long gap before a film: a rotation of cards about the films coming up on the channel
    (TVWindow._film_cards), with a small countdown to the next feature in the corner."""
    from .intermissions import countdown

    cards = info.get("cards") or []
    k = int(t // CARD_SECONDS) % len(cards)
    tc = t % CARD_SECONDS
    card = cards[k]
    if card["kind"] == "film_fact":
        _film_fact(p, w, h, card, tc)
    else:
        _film_coming(p, w, h, card, tc)
    # fade between cards
    a = 0.0
    if tc < 0.5:
        a = 1 - tc / 0.5
    elif tc > CARD_SECONDS - 0.5:
        a = (tc - (CARD_SECONDS - 0.5)) / 0.5
    if a > 0:
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, int(255 * min(1.0, a))))
    # corner: time to the feature (it switches to the countdown screen at 3:00)
    unit = h / 1080
    label = f"FEATURE PRESENTATION IN  {countdown(remaining)}"
    px = int(30 * unit)
    tw = QFontMetricsF(font("text", px)).horizontalAdvance(label)
    box = QRectF(w - tw - 70 * unit, 34 * unit, tw + 40 * unit, 58 * unit)
    p.fillRect(box, QColor(6, 0, 20, 200))
    p.setPen(QPen(QColor(CYAN), 2 * unit))
    p.setBrush(Qt.NoBrush)
    p.drawRect(box)
    _text(p, label, px, box.center().x(), box.center().y() + px * 0.36, QColor(YELLOW if remaining < 300 else CYAN))


def _meta(card: dict) -> str:
    bits = [card.get("year", ""), card.get("rating", ""), card.get("runtime", ""), " / ".join(card.get("genres", []))]
    return "  ·  ".join(b for b in bits if b)


def _film_coming(p: QPainter, w: int, h: int, card: dict, t: float) -> None:
    from .ads_paint import _wrap

    unit = h / 1080
    art = card.get("art", {})
    kenburns(p, w, h, art.get("fanart") or art.get("landscape", ""), t, CARD_SECONDS, dim=175)
    _scan(p, w, h, 18)
    left = w * 0.06
    poster = art.get("poster", "")
    if poster:
        ph = h * 0.62
        img_r = QRectF(left - (1 - _ease(t / 0.8)) * w * 0.3, h * 0.19, ph * 2 / 3, ph)
        if _framed(p, img_r, poster, unit):
            left = img_r.right() + w * 0.04
    text_w = w * 0.94 - left
    first = card.get("when", "").startswith("NEXT")
    _text(p, ("COMING UP NEXT ON CHANNEL " if first else "COMING UP ON CHANNEL ") + str(card.get("channel", "")),
          int(34 * unit), left, h * 0.18, QColor(PINK), align="l")
    r = logo_or_title(p, art.get("logo", ""), card.get("title", ""), left, h * 0.29, text_w, h * 0.17,
                      t / 0.8, align="l")
    y = max(r.bottom(), h * 0.34) + 50 * unit
    meta = _meta(card)
    if meta:
        _text(p, meta, _fit(meta, "text", int(36 * unit), text_w), left, y, QColor(YELLOW), align="l")
        y += 58 * unit
    if card.get("tagline") and t > 0.8:
        tag = f"“{card['tagline']}”"
        _text(p, tag, _fit(tag, "text", int(36 * unit), text_w), left, y, QColor(CYAN), align="l")
        y += 58 * unit
    if card.get("plot") and t > 1.3:
        for line in _wrap(card["plot"], int(34 * unit), text_w, 5 if not card.get("tagline") else 4):
            _text(p, line, int(34 * unit), left, y, QColor("#ffffff"), align="l")
            y += 46 * unit
    if card.get("when") and t > 2.0:
        k = _ease((t - 2.0) / 0.5)
        bar = QRectF(left - 20 * unit, h * 0.84, (text_w + 20 * unit) * k, 86 * unit)
        p.fillRect(bar, QColor(255, 43, 214, 210))
        if k > 0.95:
            when = card["when"]
            _text(p, when, _fit(when, "numbers", int(52 * unit), bar.width() * 0.92), left,
                  bar.center().y() + 19 * unit, QColor("#ffffff"), "numbers", outline=None, align="l")


def _film_fact(p: QPainter, w: int, h: int, card: dict, t: float) -> None:
    from .ads_paint import _wrap

    unit = h / 1080
    art = card.get("art", {})
    kenburns(p, w, h, art.get("fanart") or art.get("landscape", ""), t, CARD_SECONDS, dim=205)
    _scan(p, w, h, 18)
    p.fillRect(QRectF(w * 0.06, h * 0.12, w * 0.5, 8 * unit), QColor(PINK))
    draw_neon(p, "DID YOU KNOW?", "numbers", _fit("DID YOU KNOW?", "numbers", int(84 * unit), w * 0.8), CYAN,
              w * 0.06, h * 0.14, "tl", flicker=True)
    left = w * 0.06
    poster = art.get("poster", "")
    if poster:
        ph = h * 0.52
        r = QRectF(left, h * 0.32, ph * 2 / 3, ph)
        if _framed(p, r, poster, unit):
            left = r.right() + w * 0.05
    text_w = w * 0.92 - left
    px = int(50 * unit)
    lines = _wrap(card.get("fact", ""), px, text_w, 5)
    y = h * 0.42
    for i, line in enumerate(lines):
        if t > 0.6 + i * 0.25:
            _text(p, line, px, left, y, QColor("#ffffff"), align="l")
        y += 68 * unit
    foot = "  ·  ".join(b for b in (card.get("title", ""), card.get("when", "")) if b)
    if t > 1.5:
        _text(p, foot, _fit(foot, "text", int(38 * unit), text_w), left, y + 30 * unit, QColor(YELLOW), align="l")
