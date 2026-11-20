"""The guide channel: a scrolling grid of what's on every channel, drawn in code.

Classic cable-guide layout with the GetSome 80s-future skin: header with clock
and a rotating "NOW ON" promo, a time ruler with a moving now-line, and channel
rows that scroll when there are more than fit on screen.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen

from .. import fs42
from . import overlays
from .overlays import CYAN, PINK, YELLOW, _chrome_text, draw_neon, font

_log = logging.getLogger("getsome.guide")

WINDOW_MIN = 90          # minutes shown across the grid
REFRESH_S = 30


class GuideData:
    """Schedules for every channel, refreshed in the background."""

    def __init__(self, channels: list[fs42.Channel]):
        self.channels = channels
        self.rows: list[tuple[fs42.Channel, list[fs42.Programme]]] = []
        self.t0 = self._window_start()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _window_start() -> dt.datetime:
        now = dt.datetime.now()
        return now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="guide-data", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(REFRESH_S)

    def refresh(self) -> None:
        t0 = self._window_start()
        t1 = t0 + dt.timedelta(minutes=WINDOW_MIN)
        rows = []
        for ch in self.channels:
            try:
                progs = fs42.programmes(ch.name, t0 - dt.timedelta(minutes=1), t1)
            except Exception as e:
                _log.debug("guide: %s: %s", ch.name, e)
                progs = []
            rows.append((ch, progs))
        with self._lock:
            self.rows, self.t0 = rows, t0

    def snapshot(self):
        with self._lock:
            return list(self.rows), self.t0


class GuideRenderer:
    def __init__(self, data: GuideData):
        self.data = data
        self._bg: QImage | None = None
        self._started = time.monotonic()
        self.now_playing = ""
        # live preview window (top right): TV mode sets these
        self.preview_enabled = False
        self.preview_image = lambda: None          # -> QImage | None, the latest frame
        self.preview_info: dict | None = None      # number, name, title, sub, since, seconds, nosignal
        self.hidden = lambda: set()                # channel numbers whose listings are hidden (locked)
        self._noise = overlays.StaticNoise(frames=6)

    # ---------------------------------------------------------------- background
    def _background(self, w: int, h: int) -> QImage:
        if self._bg is not None and self._bg.width() == w and self._bg.height() == h:
            return self._bg
        img = QImage(w, h, QImage.Format_RGB32)
        p = QPainter(img)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor("#05010f"))
        g.setColorAt(0.25, QColor("#160638"))
        g.setColorAt(1.0, QColor("#07021a"))
        p.fillRect(0, 0, w, h, g)
        p.setPen(QPen(QColor(123, 44, 255, 28), 1))
        step = max(24, w // 48)
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        for y in range(0, h, 3):
            p.drawLine(0, y, w, y)
        p.end()
        self._bg = img
        return img

    # ---------------------------------------------------------------- paint
    def paint(self, p: QPainter, w: int, h: int) -> None:
        unit = h / 1080.0
        now = dt.datetime.now()
        rows, t0 = self.data.snapshot()
        p.drawImage(0, 0, self._background(w, h))

        # ---- header: logo, clock and the rotating NOW ON box on the left, preview on the right
        draw_neon(p, "TV GUIDE", "numbers", int(88 * unit), PINK, 56 * unit, 22 * unit, "tl", flicker=True)
        p.setFont(font("text", int(28 * unit)))
        p.setPen(QColor(CYAN))
        p.drawText(QPointF(66 * unit, 180 * unit), "NEON CABLE 42  ·  YOUR CHANNEL LINEUP")
        clock = now.strftime("%-I:%M")
        cw = _chrome_text(p, clock, int(84 * unit), 64 * unit, 286 * unit)
        p.setFont(font("text", int(30 * unit)))
        p.setPen(QColor(YELLOW))
        p.drawText(QPointF(64 * unit + cw + 26 * unit, 280 * unit), now.strftime("%p  ·  %A %-d %B").upper())
        pw = w * 0.36
        pbox = QRectF(w - 40 * unit - pw, 26 * unit, pw, pw * 9 / 16)
        promo_right = (pbox.left() - 36 * unit) if self.preview_enabled else (w - 40 * unit)
        self._promo(p, rows, now, w, h, unit, QRectF(56 * unit, 306 * unit, promo_right - 56 * unit, 134 * unit))
        if self.preview_enabled:
            p.save()                        # the preview changes pen/brush; keep them out of the grid
            self._preview(p, pbox, unit)
            p.restore()

        # ---- grid geometry
        top = 466 * unit
        ruler_h = 50 * unit
        row_h = 80 * unit
        left_w = 0.22 * w
        gx0, gx1 = 40 * unit + left_w, w - 40 * unit
        t1 = t0 + dt.timedelta(minutes=WINDOW_MIN)

        def x_of(t: dt.datetime) -> float:
            f = (t - t0).total_seconds() / (WINDOW_MIN * 60)
            return gx0 + max(0.0, min(1.0, f)) * (gx1 - gx0)

        # ruler
        p.fillRect(QRectF(40 * unit, top, w - 80 * unit, ruler_h), QColor(20, 8, 52, 230))
        p.setPen(QPen(QColor(CYAN), 2))
        p.drawLine(QPointF(40 * unit, top + ruler_h), QPointF(w - 40 * unit, top + ruler_h))
        p.setFont(font("text", int(30 * unit)))
        for k in range(WINDOW_MIN // 30):
            t = t0 + dt.timedelta(minutes=30 * k)
            x = x_of(t)
            p.setPen(QColor(CYAN))
            p.drawText(QPointF(x + 12 * unit, top + ruler_h * 0.72), t.strftime("%-I:%M %p"))
            p.setPen(QPen(QColor(0, 240, 255, 90), 1))
            p.drawLine(QPointF(x, top), QPointF(x, h - 30 * unit))
        p.setPen(QColor(YELLOW))
        p.drawText(QPointF(60 * unit, top + ruler_h * 0.72), "CH")

        # rows (scroll when they don't fit)
        area = QRectF(40 * unit, top + ruler_h + 6 * unit, w - 80 * unit, h - (top + ruler_h + 36 * unit))
        total = len(rows) * row_h
        p.save()
        p.setClipRect(area)
        if total <= area.height() or not rows:
            offset, reps = 0.0, 1
        else:
            speed = 22 * unit                      # px per second (an easy reading pace)
            gap = row_h                             # blank row between loops
            loop = total + gap
            offset = ((time.monotonic() - self._started) * speed) % loop
            reps = 2
        for rep in range(reps):
            base = area.top() - offset + rep * (total + (row_h if reps > 1 else 0))
            for i, (ch, progs) in enumerate(rows):
                y = base + i * row_h
                if y > area.bottom() or y + row_h < area.top():
                    continue
                self._row(p, ch, progs, QRectF(area.left(), y, area.width(), row_h - 6 * unit), left_w, x_of, t0, t1, now, unit)
        p.restore()

        # now line
        if t0 <= now <= t1:
            x = x_of(now)
            p.setPen(QPen(QColor(PINK), 3))
            p.drawLine(QPointF(x, top - 6 * unit), QPointF(x, h - 30 * unit))
            path = QPainterPath()
            path.moveTo(x - 10 * unit, top - 16 * unit)
            path.lineTo(x + 10 * unit, top - 16 * unit)
            path.lineTo(x, top - 2 * unit)
            path.closeSubpath()
            p.fillPath(path, QColor(PINK))

        if not rows:
            draw_neon(p, "NO LISTINGS", "numbers", int(80 * unit), CYAN, w / 2, h * 0.6, "cm")

        # now playing + hint, bottom strip
        strip = QRectF(0, h - 30 * unit, w, 30 * unit)
        p.fillRect(strip, QColor(10, 3, 30, 230))
        p.setFont(font("text", int(22 * unit)))
        p.setPen(QColor(YELLOW))
        if self.now_playing:
            p.drawText(QPointF(40 * unit, h - 8 * unit), f"♪  NOW PLAYING  ·  {self.now_playing.upper()}")
        p.setPen(QColor(150, 140, 210))
        tip = "MENU: SOUND & MUSIC"
        p.drawText(QPointF(w - 40 * unit - QFontMetricsF(p.font()).horizontalAdvance(tip), h - 8 * unit), tip)

    def _row(self, p, ch, progs, r: QRectF, left_w, x_of, t0, t1, now, unit) -> None:
        # channel cell
        cell = QRectF(r.left(), r.top(), left_w - 8 * unit, r.height())
        g = QLinearGradient(cell.topLeft(), cell.topRight())
        g.setColorAt(0, QColor(60, 10, 90, 235))
        g.setColorAt(1, QColor(28, 8, 64, 235))
        p.fillRect(cell, g)
        p.fillRect(QRectF(cell.left(), cell.top(), 5 * unit, cell.height()), QColor(PINK))
        img = overlays.neon_image(str(ch.number), "numbers", int(58 * unit), overlays.PINK)
        p.drawImage(QPointF(cell.left() + 10 * unit, cell.center().y() - img.height() / 2), img)
        p.setFont(font("text", int(32 * unit)))
        p.setPen(QColor("#ffffff"))
        fm = QFontMetricsF(p.font())
        name_x = cell.left() + 30 * unit + img.width() * 0.75
        p.drawText(QPointF(name_x, cell.center().y() + fm.ascent() / 2.6),
                   fm.elidedText(ch.name.upper(), Qt.ElideRight, cell.right() - name_x - 8 * unit))

        if not progs:
            p.setPen(QColor(150, 140, 200))
            p.drawText(QPointF(x_of(t0) + 16 * unit, cell.center().y() + 10 * unit), "No listings")
            return
        for prog in progs:
            if prog.end <= t0 or prog.start >= t1:
                continue
            x0, x1 = x_of(max(prog.start, t0)), x_of(min(prog.end, t1))
            box = QRectF(x0 + 2 * unit, r.top(), max(4.0, x1 - x0 - 4 * unit), r.height())
            on_now = prog.start <= now < prog.end
            fill = QColor(255, 43, 214, 70) if on_now else QColor(36, 14, 86, 225)
            if prog.off_air:
                fill = QColor(20, 14, 40, 200)
            path = QPainterPath()
            path.addRoundedRect(box, 6 * unit, 6 * unit)
            p.fillPath(path, fill)
            p.setPen(QPen(QColor(PINK) if on_now else QColor(123, 44, 255, 200), 2 if on_now else 1))
            p.setBrush(Qt.NoBrush)          # outline only
            p.drawPath(path)
            if box.width() < 40 * unit:
                continue
            title = "Off Air" if prog.off_air else prog.title
            secret = ch.number in self.hidden() and not prog.off_air
            if secret:
                title = "Restricted"
            if prog.start < t0:
                title = "◂ " + title
            p.setFont(font("text", int(34 * unit)))
            fm = QFontMetricsF(p.font())
            p.setPen(QColor(150, 140, 200) if prog.off_air else QColor("#ffffff"))
            p.drawText(QPointF(box.left() + 14 * unit, box.top() + fm.ascent() + 6 * unit),
                       fm.elidedText(title, Qt.ElideRight, box.width() - 24 * unit))
            code, ep = prog.episode()
            sub = "" if secret else "  ".join(x for x in (code, ep) if x)
            if sub and box.height() > 70 * unit:
                p.setFont(font("text", int(26 * unit)))
                fm2 = QFontMetricsF(p.font())
                p.setPen(QColor(CYAN))
                p.drawText(QPointF(box.left() + 14 * unit, box.bottom() - 12 * unit),
                           fm2.elidedText(sub, Qt.ElideRight, box.width() - 24 * unit))

    def _preview(self, p: QPainter, box: QRectF, unit: float) -> None:
        """Live picture from one channel at a time, with its name and what's on."""
        p.fillRect(box, QColor(0, 0, 0))
        info = self.preview_info or {}
        img = self.preview_image() if info and not info.get("nosignal") else None
        since = time.monotonic() - info.get("since", 0) if info else 99
        if img is not None and not img.isNull():
            p.drawImage(box, img)
        if img is None or since < 0.35:
            # static between channels (and while the first frame is on its way)
            p.save()
            p.setClipRect(box)
            p.translate(box.topLeft())
            self._noise.draw(p, int(box.width()), int(box.height()), 1.0)
            p.restore()
        if info.get("nosignal"):
            draw_neon(p, "NO SIGNAL", "numbers", int(48 * unit), CYAN, box.center().x(), box.center().y(), "cm")
        # caption
        cap = QRectF(box.left(), box.bottom() - 84 * unit, box.width(), 84 * unit)
        g = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        g.setColorAt(0, QColor(5, 0, 18, 0))
        g.setColorAt(0.45, QColor(5, 0, 18, 200))
        g.setColorAt(1, QColor(5, 0, 18, 235))
        p.fillRect(cap, g)
        if info:
            num = overlays.neon_image(str(info.get("number", "")), "numbers", int(46 * unit), overlays.PINK)
            p.drawImage(QPointF(cap.left() + 14 * unit, cap.bottom() - num.height() - 6 * unit), num)
            tx = cap.left() + 26 * unit + num.width()
            p.setFont(font("text", int(26 * unit)))
            fm = QFontMetricsF(p.font())
            p.setPen(QColor(CYAN))
            p.drawText(QPointF(tx, cap.bottom() - 46 * unit),
                       fm.elidedText(info.get("name", "").upper(), Qt.ElideRight, cap.right() - tx - 14 * unit))
            line = "  ·  ".join(x for x in (info.get("title", ""), info.get("sub", "")) if x)
            p.setPen(QColor("#ffffff"))
            p.drawText(QPointF(tx, cap.bottom() - 14 * unit),
                       fm.elidedText(line, Qt.ElideRight, cap.right() - tx - 14 * unit))
            # how long until the next channel
            frac = min(1.0, since / max(1.0, info.get("seconds", 8)))
            p.fillRect(QRectF(box.left(), box.bottom() - 4 * unit, box.width() * frac, 4 * unit), QColor(PINK))
        # frame and "PREVIEW" tag with a blinking dot
        p.setPen(QPen(QColor(CYAN), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(box)
        tag = QRectF(box.left() + 12 * unit, box.top() + 12 * unit, 150 * unit, 36 * unit)
        p.fillRect(tag, QColor(5, 0, 18, 200))
        if int(time.monotonic() * 1.5) % 2 == 0:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 40, 70))
            p.drawEllipse(QPointF(tag.left() + 18 * unit, tag.center().y()), 7 * unit, 7 * unit)
        p.setFont(font("text", int(24 * unit)))
        p.setPen(QColor("#ffffff"))
        p.drawText(QPointF(tag.left() + 34 * unit, tag.center().y() + 8 * unit), "PREVIEW")

    def _promo(self, p, rows, now, w, h, unit, box: QRectF) -> None:
        """Rotating 'NOW ON' box in the header."""
        hidden = self.hidden()
        live = [(ch, pr) for ch, progs in rows for pr in progs
                if pr.start <= now < pr.end and not pr.off_air and ch.number not in hidden]
        if not live:
            return
        k = int((time.monotonic() - self._started) // 6) % len(live)
        ch, pr = live[k]
        x0, y0, bw, bh = box.left(), box.top(), box.width(), box.height()
        p.fillRect(box, QColor(12, 4, 32, 200))
        p.setPen(QPen(QColor(CYAN), 1.5))
        p.drawRect(box)
        p.fillRect(QRectF(x0, y0, bw * 0.4, 5 * unit), QColor(PINK))
        p.setFont(font("text", int(28 * unit)))
        p.setPen(QColor(CYAN))
        p.drawText(QPointF(x0 + 20 * unit, y0 + 38 * unit), f"NOW ON {ch.number}  ·  {ch.name.upper()}")
        p.save()
        p.setClipRect(box.adjusted(10 * unit, 0, -10 * unit, 0))
        px = int(46 * unit)
        while QFontMetricsF(font("text", px)).horizontalAdvance(pr.title) > bw - 40 * unit and px > 22:
            px = int(px * 0.92)
        _chrome_text(p, pr.title, px, x0 + 20 * unit, y0 + 88 * unit)
        p.restore()
        code, ep = pr.episode()
        p.setFont(font("text", int(28 * unit)))
        p.setPen(QColor(YELLOW))
        fm = QFontMetricsF(p.font())
        p.drawText(QPointF(x0 + 20 * unit, y0 + 122 * unit),
                   fm.elidedText("  ·  ".join(x for x in (code, ep) if x) or pr.end.strftime("until %-I:%M"),
                                 Qt.ElideRight, bw - 40 * unit))
