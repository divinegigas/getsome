"""On-screen MENU: audio track and subtitle choice for what's playing.

Choices become preferred languages (settings: audio_lang / sub_lang) and are
re-applied to every new file, so picking Japanese audio + English subtitles on
one episode carries on through the rest of the channel.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from ..brand import QColor

from .overlays import CYAN, PINK, YELLOW, _chrome_text, font

LANGS = {
    "eng": "English", "en": "English", "jpn": "Japanese", "ja": "Japanese", "spa": "Spanish", "es": "Spanish",
    "fre": "French", "fra": "French", "fr": "French", "ger": "German", "deu": "German", "de": "German",
    "ita": "Italian", "it": "Italian", "por": "Portuguese", "pt": "Portuguese", "rus": "Russian", "ru": "Russian",
    "chi": "Chinese", "zho": "Chinese", "zh": "Chinese", "kor": "Korean", "ko": "Korean", "dut": "Dutch",
    "nld": "Dutch", "swe": "Swedish", "nor": "Norwegian", "dan": "Danish", "fin": "Finnish", "pol": "Polish",
    "hun": "Hungarian", "cze": "Czech", "ces": "Czech", "gre": "Greek", "ell": "Greek", "tur": "Turkish",
    "ara": "Arabic", "heb": "Hebrew", "hin": "Hindi", "tha": "Thai", "vie": "Vietnamese", "und": "Unknown",
}
AUTO_CLOSE_S = 12.0
# Picture (zoom) modes, as on a TV remote's "aspect" button. Applied by TVWindow._apply_zoom.
ZOOMS = [("normal", "Normal"), ("zoom", "Zoom (fill, no bars)"), ("cinema", "Cinema zoom (wide films)"),
         ("stretch", "Stretch to fill"), ("4:3", "Force 4:3"), ("16:9", "Force 16:9")]
ZOOM_KEYS = [k for k, _ in ZOOMS]
SIDES = [("black", "Black bars"), ("blur", "Blurred picture")]       # for pictures narrower than the screen


def lang_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return LANGS.get(code.lower(), code.upper())


def track_label(t: dict) -> str:
    bits = [lang_name(t.get("lang"))]
    if t.get("title"):
        bits.append(str(t["title"]))
    codec = (t.get("codec") or "").upper()
    ch = t.get("demux-channel-count") or t.get("audio-channels")
    if t.get("type") == "audio" and ch:
        codec += f" {ch}ch" if isinstance(ch, int) else f" {ch}"
    if codec:
        bits.append(codec.strip())
    if t.get("forced"):
        bits.append("forced")
    return "  ·  ".join(bits)


class TrackMenu:
    ROWS = ("audio", "subs", "picture", "sides", "close")

    def __init__(self):
        self.open = False
        self.row = 0
        self.audio: list[dict] = []
        self.subs: list[dict] = []
        self.audio_i = 0
        self.sub_i = 0            # 0 = off, 1.. = subs[i-1]
        self.zoom_i = 0           # index into ZOOMS
        self.sides_i = 0          # index into SIDES
        self._touched = 0.0

    # ---------------------------------------------------------------- state
    def show(self, tracks: list[dict], zoom: str = "normal", sides: str = "black") -> None:
        self.zoom_i = ZOOM_KEYS.index(zoom) if zoom in ZOOM_KEYS else 0
        self.sides_i = 1 if sides == "blur" else 0
        self.audio = [t for t in tracks if t.get("type") == "audio"]
        self.subs = [t for t in tracks if t.get("type") == "sub"]
        self.audio_i = next((i for i, t in enumerate(self.audio) if t.get("selected")), 0)
        self.sub_i = next((i + 1 for i, t in enumerate(self.subs) if t.get("selected")), 0)
        self.row = 0
        self.open = True
        self._touched = time.monotonic()

    def close(self) -> None:
        self.open = False

    def expired(self) -> bool:
        return self.open and time.monotonic() - self._touched > AUTO_CLOSE_S

    def move(self, d: int) -> None:
        self.row = (self.row + d) % len(self.ROWS)
        self._touched = time.monotonic()

    def change(self, d: int) -> tuple[str, dict | None] | None:
        """Cycle the current row's value. Returns ("audio"|"subs", track or None for off)."""
        self._touched = time.monotonic()
        r = self.ROWS[self.row]
        if r == "audio" and self.audio:
            self.audio_i = (self.audio_i + d) % len(self.audio)
            return "audio", self.audio[self.audio_i]
        if r == "subs":
            self.sub_i = (self.sub_i + d) % (len(self.subs) + 1)
            return "subs", (self.subs[self.sub_i - 1] if self.sub_i else None)
        if r == "picture":
            self.zoom_i = (self.zoom_i + d) % len(ZOOMS)
            return "picture", ZOOMS[self.zoom_i][0]
        if r == "sides":
            self.sides_i = (self.sides_i + d) % len(SIDES)
            return "sides", SIDES[self.sides_i][0]
        return None

    # ---------------------------------------------------------------- paint
    def paint(self, p: QPainter, w: int, h: int) -> None:
        if not self.open:
            return
        unit = h / 1080.0
        pw, ph = 960 * unit, 632 * unit
        x0, y0 = (w - pw) / 2, (h - ph) / 2
        p.save()
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, 120))
        panel = QPainterPath()
        s = 36 * unit
        panel.moveTo(x0 + s, y0)
        panel.lineTo(x0 + pw, y0)
        panel.lineTo(x0 + pw - s, y0 + ph)
        panel.lineTo(x0, y0 + ph)
        panel.closeSubpath()
        g = QLinearGradient(0, y0, 0, y0 + ph)
        g.setColorAt(0, QColor(26, 6, 58, 240))
        g.setColorAt(1, QColor(8, 2, 24, 235))
        p.fillPath(panel, g)
        p.setPen(QPen(QColor(CYAN), 2))
        p.drawPath(panel)
        p.fillRect(QRectF(x0 + s, y0 - 7 * unit, pw * 0.35, 7 * unit), QColor(PINK))
        _chrome_text(p, "MENU", int(60 * unit), x0 + 60 * unit, y0 + 80 * unit)

        rows = [
            ("AUDIO", track_label(self.audio[self.audio_i]) if self.audio else "No audio tracks",
             len(self.audio) > 1),
            ("SUBTITLES", "Off" if not self.sub_i else track_label(self.subs[self.sub_i - 1]),
             bool(self.subs)),
            ("PICTURE", ZOOMS[self.zoom_i][1], True),
            ("SIDES", SIDES[self.sides_i][1], True),
            ("CLOSE", "", True),
        ]
        y = y0 + 130 * unit
        for i, (label, value, active) in enumerate(rows):
            sel = i == self.row
            box = QRectF(x0 + 40 * unit, y, pw - 110 * unit, 76 * unit)
            if sel:
                p.fillRect(box, QColor(255, 43, 214, 60))
                p.fillRect(QRectF(box.left(), box.top(), 6 * unit, box.height()), QColor(PINK))
            p.setFont(font("text", int(34 * unit)))
            p.setPen(QColor(CYAN) if sel else QColor(150, 140, 210))
            fm = QFontMetricsF(p.font())
            p.drawText(QPointF(box.left() + 26 * unit, box.center().y() + fm.ascent() / 2.6), label)
            if value:
                p.setFont(font("text", int(36 * unit)))
                fm = QFontMetricsF(p.font())
                txt = fm.elidedText(value, Qt.ElideRight, box.width() * 0.62)
                vx = box.left() + 260 * unit
                p.setPen(QColor("#ffffff") if active else QColor(120, 110, 160))
                p.drawText(QPointF(vx, box.center().y() + fm.ascent() / 2.6), txt)
                if sel and active:
                    p.setPen(QColor(YELLOW))
                    p.drawText(QPointF(vx - 40 * unit, box.center().y() + fm.ascent() / 2.6), "◂")
                    p.drawText(QPointF(box.right() - 30 * unit, box.center().y() + fm.ascent() / 2.6), "▸")
            y += 86 * unit
        p.setFont(font("text", int(26 * unit)))
        p.setPen(QColor(150, 140, 210))
        p.drawText(QPointF(x0 + 60 * unit, y0 + ph - 22 * unit),
                   "▲▼ choose     ◂ ▸ change     OK / MENU close     Choices are remembered")
        p.restore()


class MusicMenu:
    """MENU on the guide channel: what you hear (music or the preview) and which music."""

    ROWS = ("sound", "music", "close")
    SOUNDS = (("music", "Guide music"), ("preview", "Preview channel"))

    def __init__(self):
        self.open = False
        self.row = 0
        self.options: list[tuple[str, str, bool]] = []   # key, name, ready
        self.i = 0
        self.sound = "music"
        self.preview_available = True
        self._touched = 0.0

    def show(self, options: list[tuple[str, str, bool]], current: str, sound: str = "music",
             preview_available: bool = True) -> None:
        self.sound, self.preview_available = sound, preview_available
        self.options = options
        self.i = next((k for k, o in enumerate(options) if o[0] == current), 0)
        self.row = 0
        self.open = True
        self._touched = time.monotonic()

    def close(self) -> None:
        self.open = False

    def expired(self) -> bool:
        return self.open and time.monotonic() - self._touched > AUTO_CLOSE_S

    def move(self, d: int) -> None:
        self.row = (self.row + d) % len(self.ROWS)
        self._touched = time.monotonic()

    def change(self, d: int) -> tuple[str, str] | None:
        """("sound", "music"|"preview") or ("music", track key)."""
        self._touched = time.monotonic()
        if self.ROWS[self.row] == "sound":
            if not self.preview_available:
                return None
            self.sound = "preview" if self.sound == "music" else "music"
            return "sound", self.sound
        if self.ROWS[self.row] != "music" or not self.options:
            return None
        self.i = (self.i + d) % len(self.options)
        return "music", self.options[self.i][0]

    def refresh(self, options: list[tuple[str, str, bool]]) -> None:
        """Update which tracks are ready while the menu is open."""
        key = self.options[self.i][0] if self.options else None
        self.options = options
        self.i = next((k for k, o in enumerate(options) if o[0] == key), 0)

    def paint(self, p: QPainter, w: int, h: int) -> None:
        if not self.open:
            return
        unit = h / 1080.0
        pw, ph = 960 * unit, 466 * unit
        x0, y0 = (w - pw) / 2, (h - ph) / 2
        p.save()
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, 120))
        panel = QPainterPath()
        s_ = 36 * unit
        panel.moveTo(x0 + s_, y0)
        panel.lineTo(x0 + pw, y0)
        panel.lineTo(x0 + pw - s_, y0 + ph)
        panel.lineTo(x0, y0 + ph)
        panel.closeSubpath()
        g = QLinearGradient(0, y0, 0, y0 + ph)
        g.setColorAt(0, QColor(26, 6, 58, 240))
        g.setColorAt(1, QColor(8, 2, 24, 235))
        p.fillPath(panel, g)
        p.setPen(QPen(QColor(CYAN), 2))
        p.drawPath(panel)
        p.fillRect(QRectF(x0 + s_, y0 - 7 * unit, pw * 0.35, 7 * unit), QColor(PINK))
        _chrome_text(p, "GUIDE SOUND", int(56 * unit), x0 + 60 * unit, y0 + 80 * unit)
        key, name, ready = self.options[self.i] if self.options else ("", "No music yet", False)
        n_ready = sum(1 for o in self.options if o[2] and not o[0].startswith("shuffle"))
        hear = dict(self.SOUNDS)[self.sound] if self.preview_available else "Guide music (preview is off)"
        rows = [("HEAR", hear), ("TRACK", f"{name}" + ("" if ready else "  (composing…)")), ("CLOSE", "")]
        y = y0 + 130 * unit
        for i, (label, value) in enumerate(rows):
            sel = i == self.row
            box = QRectF(x0 + 40 * unit, y, pw - 110 * unit, 76 * unit)
            if sel:
                p.fillRect(box, QColor(255, 43, 214, 60))
                p.fillRect(QRectF(box.left(), box.top(), 6 * unit, box.height()), QColor(PINK))
            p.setFont(font("text", int(34 * unit)))
            p.setPen(QColor(CYAN) if sel else QColor(150, 140, 210))
            fm = QFontMetricsF(p.font())
            p.drawText(QPointF(box.left() + 26 * unit, box.center().y() + fm.ascent() / 2.6), label)
            if value:
                p.setFont(font("text", int(38 * unit)))
                fm = QFontMetricsF(p.font())
                vx = box.left() + 220 * unit
                p.setPen(QColor("#ffffff"))
                p.drawText(QPointF(vx, box.center().y() + fm.ascent() / 2.6),
                           fm.elidedText(("♪ " if label == "TRACK" else "") + value, Qt.ElideRight, box.width() * 0.68))
                if sel:
                    p.setPen(QColor(YELLOW))
                    p.drawText(QPointF(vx - 40 * unit, box.center().y() + fm.ascent() / 2.6), "◂")
                    p.drawText(QPointF(box.right() - 30 * unit, box.center().y() + fm.ascent() / 2.6), "▸")
            y += 86 * unit
        p.setFont(font("text", int(26 * unit)))
        p.setPen(QColor(150, 140, 210))
        total = len([o for o in self.options if not o[0].startswith("shuffle")])
        p.drawText(QPointF(x0 + 60 * unit, y0 + ph - 22 * unit),
                   f"◂ ▸ change     OK / MENU close     {n_ready} of {total} tracks ready"
                   + ("" if n_ready >= total else "  (composing in the background)"))
        p.restore()


class ExitMenu:
    """BACK button: back to TV, quit to the setup window, or quit to the desktop."""

    OPTIONS = (("tv", "BACK TO TV"), ("setup", "QUIT TO SETUP"), ("desktop", "QUIT TO DESKTOP"))
    AUTO_CLOSE_S = 20.0            # pressed by accident: drop back to TV

    def __init__(self):
        self.open = False
        self.row = 0
        self._touched = 0.0

    def show(self) -> None:
        self.open = True
        self.row = 0
        self._touched = time.monotonic()

    def close(self) -> None:
        self.open = False

    def expired(self) -> bool:
        return self.open and time.monotonic() - self._touched > self.AUTO_CLOSE_S

    def move(self, d: int) -> None:
        self.row = (self.row + d) % len(self.OPTIONS)
        self._touched = time.monotonic()

    def choice(self) -> str:
        return self.OPTIONS[self.row][0]

    def paint(self, p: QPainter, w: int, h: int) -> None:
        if not self.open:
            return
        unit = h / 1080.0
        pw, ph = 820 * unit, 470 * unit
        x0, y0 = (w - pw) / 2, (h - ph) / 2
        p.save()
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, 150))
        panel = QPainterPath()
        s = 36 * unit
        panel.moveTo(x0 + s, y0)
        panel.lineTo(x0 + pw, y0)
        panel.lineTo(x0 + pw - s, y0 + ph)
        panel.lineTo(x0, y0 + ph)
        panel.closeSubpath()
        g = QLinearGradient(0, y0, 0, y0 + ph)
        g.setColorAt(0, QColor(26, 6, 58, 245))
        g.setColorAt(1, QColor(8, 2, 24, 240))
        p.fillPath(panel, g)
        p.setPen(QPen(QColor(PINK), 2))
        p.drawPath(panel)
        p.fillRect(QRectF(x0 + s, y0 - 7 * unit, pw * 0.35, 7 * unit), QColor(CYAN))
        _chrome_text(p, "SWITCH OFF?", int(56 * unit), x0 + 60 * unit, y0 + 78 * unit)
        y = y0 + 120 * unit
        for i, (_key, label) in enumerate(self.OPTIONS):
            sel = i == self.row
            box = QRectF(x0 + 50 * unit, y, pw - 120 * unit, 76 * unit)
            if sel:
                p.fillRect(box, QColor(255, 43, 214, 70))
                p.fillRect(QRectF(box.left(), box.top(), 6 * unit, box.height()), QColor(PINK))
            p.setFont(font("text", int(40 * unit)))
            fm = QFontMetricsF(p.font())
            p.setPen(QColor("#ffffff") if sel else QColor(150, 140, 210))
            p.drawText(QPointF(box.left() + 34 * unit, box.center().y() + fm.ascent() / 2.6),
                       ("▸  " if sel else "   ") + label)
            y += 88 * unit
        p.setFont(font("text", int(26 * unit)))
        p.setPen(QColor(150, 140, 210))
        p.drawText(QPointF(x0 + 60 * unit, y0 + ph - 24 * unit), "▲▼ choose     OK select     BACK return to TV")
        p.restore()
