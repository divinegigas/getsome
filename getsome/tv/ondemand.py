"""On Demand in TV mode: browse every show and film in the library and play whatever you like.

Modes (OnDemand.mode)
  None      cable TV as usual
  "promo"   the On Demand channel (settings.ondemand_channel, default 1): clips of things you were
            watching and things recently added, with their art; OK opens On Demand
  "home"    rows of posters: Continue watching, Favourites, Recently added, TV shows, Movies, then
            genres. Up twice on the top row opens "search". Very quiet guide music plays underneath. Resting on a title for 8 s plays a preview
            in the backdrop; 8 s more and it grows to full screen and simply carries on playing
  "search"  an on-screen keyboard (a keyboard on the remote types straight in) and results as you type
  "details" one show (play/resume, seasons, episodes) or one film (play/resume/start over)
  "play"    full-screen playback with an on-screen bar: OK pause, Left/Right skip (hold to go faster),
            MENU audio/subtitles/picture, BACK stop; the next episode follows by itself

Reached from the BACK menu ("Switch to On Demand" / "Switch to cable") or OK on the On Demand channel.
Typing a channel number, GUIDE, or L (last channel) goes back to cable. Only one thing ever decodes:
the main player is stopped while you browse. Data and history: getsome/ondemand.py.
Restricted titles (on a restricted channel) stay hidden until the PIN is entered.
"""

from __future__ import annotations

import collections
import hashlib
import logging
import math
import os
import queue
import random
import re
import threading
import time

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetricsF, QImage, QImageReader, QLinearGradient, QPainter, QPainterPath, QPen
from ..brand import QColor

from .. import ondemand, paths
from .ads_paint import _ease, _fit, _wrap
from .overlays import CYAN, PINK, YELLOW, _chrome_text, draw_neon, font, paint_lock

_log = logging.getLogger("getsome.ondemand")

BG = QColor(7, 0, 22)
PANEL = QColor(22, 6, 50)
DIM = QColor(150, 140, 210)
PROMO_SECONDS = 16          # each clip on the On Demand channel
NEXT_CARD_S = 25            # "next episode" card for the last this-many seconds of an episode
SAVE_EVERY = 10             # seconds between position saves while playing
OSD_S = 4.0
HOVER_S = 8.0               # resting on a title this long starts its preview in the backdrop...
PREVIEW_S = 8.0             # ...and after this long the preview becomes the real thing
GROW_S = 0.6                # the preview growing to full screen
BGM_LEVEL = 0.14            # browsing music, as a share of the TV volume
PREVIEW_LEVEL = 0.7
HERO_LEFT, HERO_H = 0.30, 0.60     # the backdrop area of the home screen (share of width / height)


def _hms(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _left(sec: float) -> str:
    m = max(1, int(round(sec / 60)))
    h, m = divmod(m, 60)
    return (f"{h}h {m:02d}m" if h else f"{m} min") + " left"


def _sort_key(title: str):
    t = re.sub(r"^(the|a|an)\s+", "", title or "", flags=re.I).lower()
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", t)]


def _ep_label(ep: dict) -> str:
    return "  ·  ".join(x for x in (ep.get("code", ""), ep.get("title", "")) if x)


# ------------------------------------------------------------------ images, loaded off the GUI thread

class _Bridge(QObject):
    call = Signal(object)          # run a function on the GUI thread (emitted from worker threads)


class Images:
    """Posters and backdrops, decoded (and, on a cable box, downloaded) in background threads,
    scaled once and kept as small files in data/ondemand_thumbs so browsing a big library stays quick.
    get() never blocks: it returns None until the picture is ready."""

    FAILED = QImage()
    BUDGET = 200 << 20           # bytes of decoded pictures kept in memory

    def __init__(self, notify):
        self.notify = notify
        self.cache: collections.OrderedDict = collections.OrderedDict()
        self.bytes = 0
        self.want: set = set()
        self.lock = threading.Lock()
        self.q: queue.LifoQueue = queue.LifoQueue()      # newest requests first (scrolling)
        self.dir = paths.data_dir() / "ondemand_thumbs"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        for i in range(3):
            threading.Thread(target=self._work, name=f"ondemand-img-{i}", daemon=True).start()
        threading.Thread(target=self._prune, name="ondemand-prune", daemon=True).start()

    def get(self, path: str, width: int) -> QImage | None:
        if not path:
            return None
        k = (path, width)
        with self.lock:
            img = self.cache.get(k)
            if img is not None:
                self.cache.move_to_end(k)
                return None if img is self.FAILED else img
            if k not in self.want:
                self.want.add(k)
                self.q.put(k)
        return None

    def _work(self) -> None:
        while True:
            k = self.q.get()
            try:
                img = self._load(*k)
            except Exception as e:
                _log.debug("image %s: %s", k[0], e)
                img = None
            with self.lock:
                self.want.discard(k)
                self.cache[k] = img if img is not None and not img.isNull() else self.FAILED
                if img is not None:
                    self.bytes += img.sizeInBytes()
                while self.bytes > self.BUDGET and len(self.cache) > 8:
                    _k, old = self.cache.popitem(last=False)
                    self.bytes -= old.sizeInBytes() if old is not self.FAILED else 0
            self.notify()

    def _load(self, path: str, width: int) -> QImage | None:
        from .. import cablebox

        src = cablebox.fetch(path) if cablebox.active() else path
        if not src:
            return None
        st = os.stat(src)
        key = hashlib.sha1(f"{src}|{st.st_mtime_ns}|{st.st_size}|{width}".encode()).hexdigest()
        for ext in (".jpg", ".png"):
            f = self.dir / (key + ext)
            if f.exists():
                img = QImage(str(f))
                if not img.isNull():
                    return img
        r = QImageReader(src)
        r.setAutoTransform(True)
        size = r.size()
        if size.isValid() and size.width() > width:
            r.setScaledSize(QSize(width, max(1, int(size.height() * width / size.width()))))
        img = r.read()
        if img.isNull():
            return None
        try:
            if img.hasAlphaChannel():
                img.save(str(self.dir / (key + ".png")), "PNG")
            else:
                img.save(str(self.dir / (key + ".jpg")), "JPG", 88)
        except Exception:
            pass
        return img

    def _prune(self) -> None:
        """Keep the thumbnail folder from growing forever: drop the oldest beyond 4000 files."""
        try:
            files = sorted(self.dir.iterdir(), key=lambda f: f.stat().st_atime)
            for f in files[:-4000]:
                f.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ drawing helpers

def _cover(p: QPainter, img: QImage, r: QRectF, align_top: bool = False) -> None:
    """Fill r with the picture, cropped to its shape."""
    s = max(r.width() / img.width(), r.height() / img.height())
    sw, sh = r.width() / s, r.height() / s
    sy = 0 if align_top else (img.height() - sh) / 2
    p.drawImage(r, img, QRectF((img.width() - sw) / 2, sy, sw, sh))


def _txt(p: QPainter, text: str, px: int, x: float, base: float, color, kind: str = "text",
         max_w: float = 0) -> float:
    p.setFont(font(kind, px))
    fm = QFontMetricsF(p.font())
    if max_w:
        text = fm.elidedText(text, Qt.ElideRight, max_w)
    p.setPen(QColor(0, 0, 0, 170))
    p.drawText(QPointF(x + px / 22, base + px / 22), text)
    p.setPen(color if isinstance(color, QColor) else QColor(color))
    p.drawText(QPointF(x, base), text)
    return fm.horizontalAdvance(text)


def _pill(p: QPainter, r: QRectF, label: str, sel: bool, unit: float) -> None:
    path = QPainterPath()
    path.addRoundedRect(r, r.height() / 2, r.height() / 2)
    if sel:
        p.fillPath(path, QColor(PINK))
        p.setPen(QPen(QColor(255, 255, 255, 220), 2.5 * unit))
    else:
        p.fillPath(path, QColor(20, 4, 46, 200))
        p.setPen(QPen(QColor(0, 240, 255, 170), 2 * unit))
    p.drawPath(path)
    p.setFont(font("text", int(r.height() * 0.46)))
    p.setPen(QColor("#ffffff") if sel else QColor(220, 215, 255))
    p.drawText(r, Qt.AlignCenter, label)


def _bar(p: QPainter, r: QRectF, frac: float, unit: float) -> None:
    p.fillRect(r, QColor(255, 255, 255, 60))
    p.fillRect(QRectF(r.left(), r.top(), r.width() * max(0.0, min(1.0, frac)), r.height()), QColor(PINK))


def _gradient(p: QPainter, r: QRectF, horizontal: bool, stops) -> None:
    g = QLinearGradient(r.left(), r.top(), r.right() if horizontal else r.left(), r.top() if horizontal else r.bottom())
    for at, c in stops:
        g.setColorAt(at, c)
    p.fillRect(r, g)


class _Ease:
    """A value that glides to its target (scrolling)."""

    def __init__(self, v: float = 0.0):
        self.v = self.target = v

    def step(self, dt: float, rate: float = 14.0) -> bool:
        d = self.target - self.v
        if abs(d) < 0.002:
            self.v = self.target
            return False
        self.v += d * min(1.0, dt * rate)
        return True


# ------------------------------------------------------------------ the On Demand screens

class OnDemand:
    def __init__(self, win):
        self.win = win                  # TVWindow
        self.mode: str | None = None
        self.bridge = _Bridge()
        self.bridge.call.connect(lambda fn: fn())
        self._dirty = True
        self.images = Images(self._mark_dirty)
        self.history = ondemand.History()
        self.items: list[dict] | None = None      # None = still loading
        self.note = ""
        self._loading = False
        self._details_cache: dict[str, dict] = {}
        self._locks_seen: frozenset | None = None
        self.rows: list[dict] = []
        self.row = 0
        self.cols: dict[str, int] = {}
        self.row_y = _Ease()
        self.col_x: dict[str, _Ease] = {}
        self._hero = (None, 0.0)          # (art path, when it changed)
        self.det: dict | None = None
        self.p: dict | None = None        # what's playing
        self.promo: dict | None = None
        self.pv: dict | None = None       # the preview playing in the home screen's backdrop
        self.bgm = False                  # the quiet browsing music is playing
        self._pv_failed: set = set()
        self.search = {"q": "", "focus": "keys", "k": 0, "r": 0, "results": [], "y": _Ease()}
        self._up_armed = 0.0                # first Up on the top row: a second one opens search
        self._art_pending = 0
        self._cat_at = 0.0
        self._last_paint = time.monotonic()
        self._last_input = 0.0
        self.tick_timer = QTimer(win, interval=250, timeout=self._tick)
        self.promo_timer = QTimer(win, singleShot=True, timeout=self._promo_next)
        self.seek_timer = QTimer(win, singleShot=True, timeout=self._commit_seek)

    # -------------------------------------------------------------- state
    @property
    def active(self) -> bool:
        """Browsing or playing: On Demand has the remote."""
        return self.mode in ("home", "search", "details", "play")

    @property
    def playing(self) -> bool:
        return self.mode == "play" and self.p is not None

    def _mark_dirty(self) -> None:
        self._dirty = True

    def animating(self) -> bool:
        if self.mode is None:
            return False
        if self.mode in ("promo", "play") or self.pv:
            return True
        busy = self._dirty or time.monotonic() - self._last_input < 1.2 or self.items is None
        self._dirty = False
        return busy or int(time.monotonic() * 4) % 4 == 0      # a few frames a second for the clock

    def _run_bg(self, fn, done) -> None:
        """fn() in a thread, then done(result) on the GUI thread."""
        def work():
            try:
                res = fn()
            except Exception as e:
                _log.warning("on demand: %s", e)
                res = None
            self.bridge.call.emit(lambda: done(res))
        threading.Thread(target=work, name="ondemand-bg", daemon=True).start()

    def load_catalog(self, then=None) -> None:
        if self._loading:
            return
        self._loading = True

        def done(cat):
            self._loading = False
            cat = cat or {"items": [], "note": "Couldn't read the library."}
            self.items = list(cat.get("items") or [])
            self.note = cat.get("note") or ""
            self._art_pending = int(cat.get("art_pending") or 0)
            self._cat_at = time.monotonic()
            self._build_rows()
            self._dirty = True
            if then:
                then()
        self._run_bg(ondemand.catalog, done)

    def _visible(self, item: dict) -> bool:
        locked = self.win._locked_numbers()
        return not (locked and set(item.get("lock") or []) & locked)

    def _hidden_count(self) -> int:
        return sum(1 for i in self.items or [] if not self._visible(i))

    def _build_rows(self) -> None:
        keep = {r["title"]: self.cols.get(r["title"], 0) for r in self.rows}
        cur_title = self.rows[self.row]["title"] if self.rows and self.row < len(self.rows) else None
        self._locks_seen = frozenset(self.win._locked_numbers())
        items = [i for i in self.items or [] if self._visible(i)]
        by_id = {i["id"]: i for i in items}
        rows: list[dict] = []
        cont = self.history.continue_list(by_id)
        if cont:
            rows.append({"title": "Continue watching", "kind": "wide",
                         "entries": [{"item": c["item"], "path": c["path"], "label": c["label"],
                                      "fraction": c["fraction"], "resume": c["resume"], "cont": True}
                                     for c in cont[:20]]})
        favs = [by_id[i] for i in ondemand.favourites() if i in by_id]
        if favs:
            rows.append({"title": "Favourites", "kind": "poster", "entries": [{"item": i} for i in favs]})
        recent = sorted((i for i in items if i.get("added")), key=lambda i: -i["added"])[:24]
        if recent:
            rows.append({"title": "Recently added", "kind": "poster", "entries": [{"item": i} for i in recent]})
        shows = sorted((i for i in items if i["kind"] == "show"), key=lambda i: _sort_key(i["title"]))
        movies = sorted((i for i in items if i["kind"] == "movie"), key=lambda i: _sort_key(i["title"]))
        if shows:
            rows.append({"title": "TV shows", "kind": "poster", "entries": [{"item": i} for i in shows]})
        if movies:
            rows.append({"title": "Movies", "kind": "poster", "entries": [{"item": i} for i in movies]})
        counts = collections.Counter(g for i in items for g in (i.get("genres") or [])[:3])
        for g, n in counts.most_common(14):
            if n < 4:
                break
            sel = sorted((i for i in items if g in (i.get("genres") or [])[:3]), key=lambda i: _sort_key(i["title"]))
            rows.append({"title": g, "kind": "poster", "entries": [{"item": i} for i in sel]})
        if self._hidden_count():
            rows.append({"title": "Restricted", "kind": "lock", "entries": [{"item": None, "lock": True}]})
        self.rows = rows
        for r in rows:
            n = len(r["entries"])
            self.cols[r["title"]] = min(keep.get(r["title"], 0), n - 1) if n else 0
            self.col_x.setdefault(r["title"], _Ease(self.cols[r["title"]]))
        titles = [r["title"] for r in rows]
        old_row = self.row
        self.row = titles.index(cur_title) if cur_title in titles else min(self.row, max(0, len(rows) - 1))
        if self.row != old_row or cur_title not in titles:      # a row came or went above: no jump
            self.row_y.v = self.row_y.target = self.row

    def _entry(self) -> dict | None:
        if not self.rows:
            return None
        r = self.rows[self.row]
        return r["entries"][self.cols.get(r["title"], 0)] if r["entries"] else None

    # -------------------------------------------------------------- entering and leaving
    def _quiet_channel(self) -> None:
        """Stop whatever the cable side was doing (only one thing decodes)."""
        w = self.win
        w.token += 1                      # pending channel loads and tune finishes no longer apply
        w.tuning = False
        w.seg_timer.stop()
        w.seg = None
        w.guide_data.stop()
        w._stop_preview()
        w.state.guide = False
        w.state.bumper = None
        w.state.intermission = None
        w.state.message = ""
        w.state.message_sub = ""
        w.state.channel_fade.hide()
        w.state.banner_fade.hide()
        w.state.pin_entry = None
        if w.state.locked:
            w.state.locked = False
            w._update_vf()
        w.menu.close()
        w.music_menu.close()

    def _flash(self, ms: int = 420) -> None:
        """A burst of static and hiss, like changing channel."""
        w = self.win
        w.state.static_level = 1.0
        w.hiss.start()

        def clear():
            w.state.static_level = 0.0
            w.hiss.stop()
        QTimer.singleShot(ms, clear)

    # -------------------------------------------------------------- music, preview window
    def volume_factor(self) -> float:
        """Share of the TV volume for what On Demand is playing right now."""
        if self.mode == "promo":
            return 0.6
        if self.mode in ("home", "search", "details"):
            return PREVIEW_LEVEL if self.pv else BGM_LEVEL
        return 1.0

    def _margins(self, left: float = 0.0, top: float = 0.0, right: float = 0.0, bottom: float = 0.0) -> None:
        """Where mpv draws the picture, as shares of the screen (the preview sits in the backdrop)."""
        if getattr(self, "_margins_now", (0.0, 0.0, 0.0, 0.0)) == (left, top, right, bottom):
            return
        pl = self.win.video.player
        for k, v in (("left", left), ("top", top), ("right", right), ("bottom", bottom)):
            try:
                pl[f"video-margin-ratio-{k}"] = max(0.0, min(0.95, v))
            except Exception as e:
                _log.debug("margin %s: %s", k, e)
        self._margins_now = (left, top, right, bottom)

    def _bgm_start(self) -> None:
        """Very quiet guide music while browsing (the guide's own tracks, as chosen in Settings)."""
        if self.mode not in ("home", "search", "details") or self.pv:
            return
        from .. import assets_gen

        self._margins()
        playlist = [str(x) for x in assets_gen.guide_playlist()]
        if not playlist:
            self.win.video.stop()
            self.bgm = False
            return
        self.bgm = True
        self.win._apply_volume()
        self.win.video.load_playlist(playlist, shuffle=True)
        self.win.video.set_muted(False)

    def _next_up_for(self, item: dict, eps: list[dict]):
        """(episode, resume seconds, verb) for a show's Play button."""
        if not eps:
            return None
        it = self.history.items.get(item["id"]) or {}
        paths_ = [e["path"] for e in eps]
        last = it.get("last")
        if last in paths_:
            i = paths_.index(last)
            if not self.history.done(last):
                return eps[i], self.history.resume_at(last), "Resume"
            if i + 1 < len(eps):
                return eps[i + 1], self.history.resume_at(eps[i + 1]["path"]), "Play"
            return eps[0], 0.0, "Play again"
        return eps[0], self.history.resume_at(eps[0]["path"]), "Play"

    def _hover_tick(self) -> None:
        """Resting on a title: after HOVER_S its preview starts in the backdrop."""
        w = self.win
        if (self.mode != "home" or self.pv or w.exit_menu.open or w.menu.open or w.state.pin_entry is not None
                or time.monotonic() - self._last_input < HOVER_S):
            return
        e = self._entry()
        if not e or not e.get("item"):
            return
        it = e["item"]
        if (it["id"], e.get("cont")) in self._pv_failed:
            return
        self.pv = {"item": it, "entry": e, "path": None, "start": 0.0, "ep": None, "t_loaded": None}
        if e.get("cont"):
            self._pv_go(e["path"], e["resume"], None)
        elif it["kind"] == "movie":
            self._pv_go(it["path"], self.history.resume_at(it["path"]), None)
        else:
            folder = it["folder"]

            def got(detail, pv=self.pv):
                if self.pv is not pv:
                    return
                up = self._next_up_for(it, ondemand.episodes_in_order(detail or {}))
                if up:
                    self._pv_go(up[0]["path"], up[1], up[0])
                else:
                    self._pv_failed.add((it["id"], None))
                    self.pv = None
            if folder in self._details_cache:
                got(self._details_cache[folder])
            else:
                self._run_bg(lambda: self._detail(folder), got)

    def _pv_go(self, path: str, start: float, ep: dict | None) -> None:
        pv = self.pv
        if not pv:
            return
        pv.update(path=path, start=start, ep=ep)
        self.bgm = False
        self._margins(HERO_LEFT, 0.0, 0.0, 1.0 - HERO_H)
        self.win._set_zoom("zoom")             # fill the backdrop area, no bars
        self.win._apply_volume()
        self.win.video.load(path, start=start if start > 1 else None)
        _log.info("on demand: preview of %s", os.path.basename(path))

    def _pv_stop(self, music: bool = True) -> None:
        if not self.pv:
            return
        self.pv = None
        self.win._set_zoom("normal")
        self._margins()
        if music:
            self._bgm_start()

    def _promote(self) -> None:
        """The preview becomes the real thing: it grows to full screen and keeps playing."""
        pv, self.pv = self.pv, None
        if not pv or not pv["path"]:
            return
        self.play(pv["item"], pv["path"], pv["start"], pv["ep"], back_to="home", keep=True)

    def open_home(self, select: dict | None = None, then=None) -> None:
        self._stop_promo()
        self._save_progress(final=True)
        self._quiet_channel()
        self.p = None
        self.pv = None
        self.mode = "home"
        self._bgm_start()
        self._flash()
        self.tick_timer.start()
        self._last_input = time.monotonic()

        def pick():
            if select is None:
                return
            for ri, r in enumerate(self.rows):
                for ci, e in enumerate(r["entries"]):
                    if e.get("item") and e["item"]["id"] == select["id"] and e.get("cont") == select.get("cont"):
                        self.row, self.cols[r["title"]] = ri, ci
                        self.row_y.v = self.row_y.target = ri
                        self.col_x[r["title"]].v = self.col_x[r["title"]].target = max(0, ci - 2)
                        return
        if self.items is None:
            self.load_catalog(then=lambda: (pick(), then and then()))
        else:
            self.history = ondemand.History()
            self._build_rows()
            pick()
            if then:
                then()
            self.load_catalog()           # pick up a rescan made while TV mode was open

    def open_item(self, item_id: str, episode: str = "") -> None:
        """MENU → Watch On Demand on a channel: straight to that show (on that episode) or film."""
        def go():
            item = next((i for i in self.items or [] if i["id"] == item_id), None)
            if item is not None and self._visible(item):
                self.open_details(item, episode=episode)
        self.open_home(then=go)

    def leave(self) -> None:
        """Back to cable (the caller tunes a channel)."""
        if self.mode is None:
            return
        self._save_progress(final=True)
        self._stop_promo()
        self.tick_timer.stop()
        self.seek_timer.stop()
        was = self.mode
        self.mode = None
        self.p = None
        self.pv = None
        self.bgm = False
        self.det = None
        self._margins()
        if was in ("play", "home", "details"):
            try:
                self.win.video.stop()
            except Exception:
                pass
        self.win.state.black = False

    def shutdown(self) -> None:
        self._save_progress(final=True)

    # -------------------------------------------------------------- the On Demand channel ("promo")
    def start_promo(self) -> None:
        self.mode = "promo"
        self.p = None
        self.pv = None
        self._margins()
        self.promo = {"entries": [], "i": -1, "t0": time.monotonic(), "loaded": False, "ready": False}
        self.tick_timer.start()

        def go():
            if self.mode != "promo":
                return
            entries = self._promo_entries()
            self.promo["entries"] = entries
            if not entries:
                self.promo["ready"] = True
                self.win.video.stop()
                return
            self._run_bg(lambda: self._resolve_clips(entries), lambda _r: self._promo_first())
        if self.items is None:
            self.load_catalog(then=go)
        else:
            go()

    def _promo_entries(self) -> list[dict]:
        items = [i for i in self.items or [] if self._visible(i)]
        by_id = {i["id"]: i for i in items}
        out, seen = [], set()
        for c in self.history.continue_list(by_id)[:4]:
            out.append({"item": c["item"], "path": c["path"], "label": "CONTINUE WATCHING", "sub": c["label"]})
            seen.add(c["item"]["id"])
        recent = sorted((i for i in items if i.get("added")), key=lambda i: -i["added"])
        for i in recent[:10]:
            if i["id"] not in seen:
                out.append({"item": i, "label": "NEW ON DEMAND" if ondemand.is_new(i) else "RECENTLY ADDED"})
                seen.add(i["id"])
        rest = [i for i in items if i["id"] not in seen]
        random.shuffle(rest)
        for i in rest[:max(0, 12 - len(out))]:
            out.append({"item": i, "label": "FROM THE LIBRARY"})
        return out[:12]

    def _detail(self, folder: str) -> dict:
        d = self._details_cache.get(folder)
        if d is None:
            d = ondemand.show_detail(folder)
            self._details_cache[folder] = d
        return d

    def _resolve_clips(self, entries: list[dict]) -> None:
        """A file for each promo: the episode you're on, a recent episode, or the film."""
        for e in entries:
            it = e["item"]
            if e.get("path"):
                e["clip"] = e["path"]
            elif it["kind"] == "movie":
                e["clip"] = it["path"]
            else:
                try:
                    eps = ondemand.episodes_in_order(self._detail(it["folder"]))
                except Exception as ex:
                    _log.debug("promo episodes: %s", ex)
                    eps = []
                if eps:
                    pool = eps[-6:] if e["label"] != "FROM THE LIBRARY" else eps
                    ep = random.choice(pool)
                    e["clip"] = ep["path"]
                    e.setdefault("sub", _ep_label(ep))

    def _promo_first(self) -> None:
        if self.mode != "promo" or not self.promo:
            return
        self.promo["ready"] = True
        self.promo["i"] = -1
        self._promo_next(static=False)

    def _promo_next(self, static: bool = True) -> None:
        pr = self.promo
        if self.mode != "promo" or not pr or not pr["entries"]:
            return
        pr["i"] = (pr["i"] + 1) % len(pr["entries"])
        pr["t0"] = time.monotonic()
        pr["loaded"] = False
        e = pr["entries"][pr["i"]]
        clip = e.get("clip")
        if static:
            self._flash(350)
        if clip:
            self.win._apply_volume()
            self.win.video.load(clip, start=f"{random.randint(12, 55)}%")
        else:
            self.win.video.stop()
        self.promo_timer.start(PROMO_SECONDS * 1000)

    def _stop_promo(self) -> None:
        self.promo_timer.stop()
        self.promo = None

    def promo_selected(self) -> dict | None:
        pr = self.promo
        if not pr or not pr["entries"] or pr["i"] < 0:
            return None
        e = pr["entries"][pr["i"]]
        return {**e["item"], "cont": e["label"] == "CONTINUE WATCHING"}

    # -------------------------------------------------------------- playback
    def play(self, item: dict, path: str, start: float = 0.0, ep: dict | None = None,
             back_to: str = "details", keep: bool = False) -> None:
        """Full-screen playback. keep: the file is already playing (a preview growing up)."""
        w = self.win
        self._save_progress(final=True)
        self._stop_promo()
        self._quiet_channel()
        self.pv = None
        self.bgm = False
        self.mode = "play"
        self.p = {"item": item, "path": path, "ep": ep, "eps": None, "loaded": keep, "paused": False,
                  "osd_until": time.monotonic() + OSD_S, "seek": None, "seek_n": 0, "seek_last": 0.0,
                  "next_dismissed": False, "last_save": time.monotonic(), "back_to": back_to, "error": "",
                  "started": time.monotonic(), "pos": start, "dur": 0.0,
                  "grow": time.monotonic() if keep else None}
        w.video.set_muted(False)
        w._apply_volume()
        if keep:
            w._apply_track_prefs(self.track_key())
        else:
            self._margins()
            w._set_zoom("normal")
            w.state.black = True
            w.video.load(path, start=start if start > 1 else None)
        self.tick_timer.start()
        _log.info("on demand: playing %s from %.0fs", os.path.basename(path), start)
        if item["kind"] == "show":
            folder = item["folder"]
            cached = self._details_cache.get(folder)
            if cached:
                self._set_eps(path, cached)
            else:
                self._run_bg(lambda: self._detail(folder), lambda d, p=path: self._set_eps(p, d))

    def _set_eps(self, path: str, detail: dict | None) -> None:
        if not self.p or self.p["path"] != path or not detail:
            return
        eps = ondemand.episodes_in_order(detail)
        self.p["eps"] = eps
        if self.p["ep"] is None:
            self.p["ep"] = next((e for e in eps if e["path"] == path), None)

    def _next_ep(self) -> dict | None:
        p = self.p
        if not p or not p["eps"] or not p["ep"]:
            return None
        paths_ = [e["path"] for e in p["eps"]]
        try:
            i = paths_.index(p["ep"]["path"])
        except ValueError:
            return None
        return p["eps"][i + 1] if i + 1 < len(p["eps"]) else None

    def zoom_key(self) -> str:
        """The picture-mode memory key (same as on the channels): the show's or film's name."""
        from ..media_index import _safe

        p = self.p
        if not p:
            return ""
        it = p["item"]
        if it["kind"] == "show":
            return _safe(it["title"])
        return _safe(f"{it['title']} ({it['year']})" if it.get("year") else it["title"])

    def track_key(self):
        return ("ondemand", self.p["path"]) if self.p else None

    def on_restart(self) -> None:
        """mpv: frames are flowing (after a load or a seek)."""
        if self.pv and self.pv["path"] and self.pv["t_loaded"] is None:
            self.pv["t_loaded"] = time.monotonic()
        elif self.mode == "play" and self.p:
            self.p["loaded"] = True
            self.win.state.black = False
        elif self.mode == "promo" and self.promo:
            self.promo["loaded"] = True
            self.win.state.black = False
        self._dirty = True

    def on_ended(self, reason: str) -> None:
        if reason != "error":
            return
        if self.pv:
            self._pv_failed.add((self.pv["item"]["id"], self.pv["entry"].get("cont")))
            self._pv_stop()
            return
        if self.mode == "play" and self.p and not self.p["error"]:
            self.p["error"] = "Couldn't play this file."
            QTimer.singleShot(4000, self._after_error)
        elif self.mode == "promo":
            self.promo_timer.start(800)          # skip to the next clip

    def _after_error(self) -> None:
        if self.mode == "play" and self.p and self.p["error"]:
            self._stop_playback()

    def _save_progress(self, final: bool = False) -> None:
        p = self.p
        if self.mode != "play" or not p or not p["loaded"]:
            return
        try:
            pos = self.win.video.player.time_pos
            dur = self.win.video.player.duration
        except Exception:
            pos = dur = None
        if pos is None or not dur:
            pos, dur = p.get("pos"), p.get("dur")
        if pos is None or not dur:
            return
        nxt = self._next_ep()
        label = _ep_label(p["ep"]) if p["ep"] else ""
        self.history.update(p["item"]["id"], p["path"], pos, dur, label=label,
                            nxt={"path": nxt["path"], "label": _ep_label(nxt)} if nxt else None)
        if final or time.monotonic() - p["last_save"] > SAVE_EVERY * 3:
            self.history.save()
            p["last_save"] = time.monotonic()

    def _stop_playback(self) -> None:
        """BACK while playing: remember the place and go back to where we came from."""
        p = self.p
        self._save_progress(final=True)
        self.win.video.stop()
        self.win.state.black = False
        self.p = None
        self.seek_timer.stop()
        if p and p["back_to"] == "details" and self.det is not None:
            self.mode = "details"
            self._refresh_details()
        else:
            self.mode = "home"
            self._build_rows()
        self._last_input = time.monotonic()
        self._bgm_start()

    def _tick(self) -> None:
        if self.mode == "play" and self.p:
            p = self.p
            try:
                pl = self.win.video.player
                pos, dur, eof = pl.time_pos, pl.duration, bool(pl.eof_reached)
            except Exception:
                return
            if pos is not None:
                p["pos"] = pos
            if dur:
                p["dur"] = dur
            if not p["loaded"]:
                return
            if time.monotonic() - p.get("_saved", 0) > SAVE_EVERY:
                p["_saved"] = time.monotonic()
                self._save_progress()
            if eof or (dur and pos is not None and pos >= dur - 0.4 and not p["paused"]):
                self._finished()
        elif self.mode in ("home", "search", "details"):
            now = time.monotonic()
            if self.pv and self.pv["t_loaded"] and now - self.pv["t_loaded"] >= PREVIEW_S:
                self._promote()
                return
            self._hover_tick()
            if self._art_pending and now - self._cat_at > 20 and not self._loading:
                self._cat_at = now
                self.load_catalog()          # covers being made on the main PC: pick them up
            locks = frozenset(self.win._locked_numbers())
            if self.items is not None and locks != self._locks_seen:
                self._build_rows()            # PIN entered, or restricted titles locked again
                if self.mode == "details" and self.det and not self._visible(self.det["item"]):
                    self.mode = "home"
                self._dirty = True

    def _finished(self) -> None:
        p = self.p
        self._save_progress(final=True)
        nxt = self._next_ep()
        if nxt and not p["next_dismissed"]:
            self.play(p["item"], nxt["path"], self.history.resume_at(nxt["path"]), nxt, back_to=p["back_to"])
            self.p["eps"] = p["eps"]
            return
        self._stop_playback()

    def _seek_press(self, d: int) -> None:
        p = self.p
        now = time.monotonic()
        p["seek_n"] = p["seek_n"] + 1 if now - p["seek_last"] < 0.8 else 1
        p["seek_last"] = now
        n = p["seek_n"]
        step = 10 if n < 4 else (30 if n < 10 else 60)
        if d < 0:
            step = -step
        base = p["seek"] if p["seek"] is not None else (p.get("pos") or 0)
        dur = p.get("dur") or 0
        p["seek"] = max(0.0, min(base + step, dur - 2 if dur else base + step))
        p["osd_until"] = now + OSD_S
        self.seek_timer.start(650)

    def _commit_seek(self) -> None:
        p = self.p
        if not p or p["seek"] is None:
            return
        target, p["seek"] = p["seek"], None
        p["pos"] = target
        self.win.video.seek_abs(target)

    # -------------------------------------------------------------- details
    def open_details(self, item: dict, episode: str = "", back: str = "home") -> None:
        self._pv_stop(music=False)
        self.mode = "details"
        self.det = {"item": item, "detail": None, "eps": [], "focus": "buttons", "btn": 0, "season": 0,
                    "ep": 0, "ep_x": _Ease(), "loading": item["kind"] == "show", "want": episode, "back": back}
        if not self.bgm:
            self._bgm_start()
        if item["kind"] == "show":
            folder = item["folder"]
            if folder in self._details_cache:
                self._details_loaded(item, self._details_cache[folder])
            else:
                self._run_bg(lambda: self._detail(folder), lambda d, it=item: self._details_loaded(it, d))
        self._refresh_details()

    def _details_loaded(self, item: dict, detail: dict | None) -> None:
        d = self.det
        if not d or d["item"]["id"] != item["id"]:
            return
        d["loading"] = False
        d["detail"] = detail or {"seasons": []}
        d["eps"] = ondemand.episodes_in_order(d["detail"])
        # open on the episode asked for (from a channel), else the one you're up to
        up = self._next_up()
        want = d.get("want") or (up[0]["path"] if up and up[0] else "")
        for si, s in enumerate(d["detail"]["seasons"]):
            for ei, e in enumerate(s["episodes"]):
                if e["path"] == want:
                    d["season"], d["ep"] = si, ei
                    d["ep_x"].v = d["ep_x"].target = max(0, ei - 1)
                    if d.get("want"):
                        d["focus"] = "episodes"
        self._refresh_details()
        self._dirty = True

    def _next_up(self):
        """(episode, resume seconds, verb) for a show's Play button."""
        d = self.det
        return self._next_up_for(d["item"], d["eps"]) if d else None

    def _refresh_details(self) -> None:
        d = self.det
        if not d:
            return
        it = d["item"]
        btns = []
        if it["kind"] == "movie":
            res = self.history.resume_at(it["path"])
            btns.append(("play", f"▶  Resume  {_hms(res)}" if res else "▶  Play"))
            if res:
                btns.append(("restart", "↺  Start over"))
            btns.append(("watched", "Mark unwatched" if self.history.done(it["path"]) else "✓  Mark watched"))
        else:
            up = self._next_up()
            if up:
                ep, res, verb = up
                btns.append(("play", f"▶  {verb}  {ep.get('code') or ep.get('title', '')}".rstrip()))
                if self.history.items.get(it["id"]):
                    btns.append(("first", "↺  From the first episode"))
            elif d["loading"]:
                btns.append(("wait", "Loading episodes…"))
            else:
                btns.append(("wait", "No episodes found"))
        btns.append(("fav", "♥  In favourites" if ondemand.is_favourite(it["id"]) else "♡  Add to favourites"))
        d["buttons"] = btns
        d["btn"] = min(d["btn"], len(btns) - 1)

    def _season_eps(self) -> list[dict]:
        d = self.det
        seasons = (d.get("detail") or {}).get("seasons") or []
        return seasons[d["season"]]["episodes"] if 0 <= d["season"] < len(seasons) else []

    def _details_ok(self) -> None:
        d = self.det
        it = d["item"]
        if d["focus"] == "buttons":
            key = d["buttons"][d["btn"]][0]
            if key == "fav":
                ondemand.set_favourite(it["id"], not ondemand.is_favourite(it["id"]))
                self._refresh_details()
            elif it["kind"] == "movie":
                if key == "play":
                    self.play(it, it["path"], self.history.resume_at(it["path"]))
                elif key == "restart":
                    self.play(it, it["path"], 0.0)
                elif key == "watched":
                    self.history.set_done(it["id"], it["path"], not self.history.done(it["path"]))
                    self._refresh_details()
            elif key == "play":
                up = self._next_up()
                if up:
                    self.play(it, up[0]["path"], up[1], up[0])
            elif key == "first" and d["eps"]:
                self.play(it, d["eps"][0]["path"], 0.0, d["eps"][0])
        elif d["focus"] == "seasons":
            d["focus"] = "episodes"
        elif d["focus"] == "episodes":
            eps = self._season_eps()
            if eps:
                ep = eps[d["ep"]]
                self.play(it, ep["path"], self.history.resume_at(ep["path"]), ep)

    def _details_menu(self) -> None:
        """MENU: mark the episode (or film) under the cursor watched / unwatched."""
        d = self.det
        it = d["item"]
        if it["kind"] == "movie":
            self.history.set_done(it["id"], it["path"], not self.history.done(it["path"]))
        elif d["focus"] == "episodes" and self._season_eps():
            ep = self._season_eps()[d["ep"]]
            done = not self.history.done(ep["path"])
            self.history.set_done(it["id"], ep["path"], done)
            if done:          # "next up" moves past it
                nxt = None
                paths_ = [e["path"] for e in d["eps"]]
                i = paths_.index(ep["path"]) if ep["path"] in paths_ else -1
                if 0 <= i < len(d["eps"]) - 1:
                    nxt = d["eps"][i + 1]
                rec = self.history.items.setdefault(it["id"], {})
                rec.update(t=time.time(), last=ep["path"], label_last=_ep_label(ep),
                           next=nxt["path"] if nxt else "", next_label=_ep_label(nxt) if nxt else "")
                self.history.save()
        self._refresh_details()

    # -------------------------------------------------------------- input
    def back(self) -> bool:
        """BACK. False = we're at the top (the window opens its BACK menu)."""
        self._last_input = time.monotonic()
        if self.mode == "play":
            p = self.p
            dur, pos = p.get("dur") or 0, p.get("pos") or 0
            nxt = self._next_ep()
            if nxt and dur and dur - pos <= NEXT_CARD_S and not p["next_dismissed"]:
                p["next_dismissed"] = True        # first BACK just hides the "next episode" card
                return True
            self._stop_playback()
            return True
        if self.mode == "home" and self.pv:
            self._pv_stop()
            return True
        if self.mode == "details":
            if self.det and self.det["focus"] == "episodes":
                self.det["focus"] = "seasons"
                return True
            if self.det and self.det["focus"] == "seasons":
                self.det["focus"] = "buttons"
                return True
            self.mode = (self.det or {}).get("back", "home")
            self.det = None
            self._build_rows()
            if self.mode == "search":
                self._run_search()
            return True
        if self.mode == "search":
            if self.search["focus"] == "results":
                self.search["focus"] = "keys"
            else:
                self.mode = "home"
            return True
        return False

    def key(self, action: str | None, digit: str | None) -> bool:
        """A button while On Demand has the remote. False = let the window handle it (volume, mute,
        fullscreen, MENU while playing, channel numbers)."""
        self._last_input = time.monotonic()
        self._dirty = True
        if self.mode == "search" and digit is not None:
            self._type(digit)
            return True
        if digit is not None or action is None:
            return False                     # typing a channel number goes back to cable
        if self.win.state.pin_entry is not None:
            return False
        if self.mode == "play":
            return self._play_key(action)
        if self.mode == "details":
            return self._details_key(action)
        if action in ("volume_up", "volume_down", "mute", "fullscreen"):
            return False
        if self.mode == "search":
            return self._search_key(action)
        if self.pv:
            if action == "enter" and self.pv["path"]:
                self._promote()                  # OK during the preview: watch it now
                return True
            self._pv_stop()
        return self._home_key(action)

    def _play_key(self, action: str) -> bool:
        p = self.p
        now = time.monotonic()
        nxt = self._next_ep()
        card = (nxt and p.get("dur") and p["dur"] - (p.get("pos") or 0) <= NEXT_CARD_S
                and not p["next_dismissed"])
        if action == "enter":
            if card:
                self._save_progress(final=True)
                self.play(p["item"], nxt["path"], self.history.resume_at(nxt["path"]), nxt, back_to=p["back_to"])
                self.p["eps"] = p["eps"]
                return True
            p["paused"] = not p["paused"]
            try:
                self.win.video.player.pause = p["paused"]
            except Exception:
                pass
            p["osd_until"] = now + (1e9 if p["paused"] else OSD_S)
            if p["paused"]:
                self._save_progress(final=True)
            return True
        if action in ("left", "right"):
            self._seek_press(-1 if action == "left" else 1)
            return True
        if action in ("channel_up", "channel_down", "info"):
            p["osd_until"] = now + (OSD_S * 2 if action == "info" else OSD_S)
            p["info"] = action == "info"
            return True
        return False

    def _home_key(self, action: str) -> bool:
        if not self.rows:
            return action in ("channel_up", "channel_down", "left", "right", "enter", "menu", "info")
        r = self.rows[self.row]
        t = r["title"]
        if action == "channel_up" and self.row == 0:
            now = time.monotonic()
            if now - self._up_armed < 4.0:
                self._up_armed = 0.0
                self.open_search()
            else:
                self._up_armed = now           # "▲ again to search"
            return True
        if action != "channel_up":
            self._up_armed = 0.0
        if action == "channel_up":
            self.row = max(0, self.row - 1)
        elif action == "channel_down":
            self.row = min(len(self.rows) - 1, self.row + 1)
        elif action in ("left", "right"):
            n = len(r["entries"])
            self.cols[t] = max(0, min(n - 1, self.cols.get(t, 0) + (1 if action == "right" else -1)))
        elif action == "enter":
            e = self._entry()
            if e is None:
                return True
            if e.get("lock"):
                self.win._open_pin()
            elif e.get("cont"):
                it = e["item"]
                ep = None
                self.play(it, e["path"], e["resume"], ep, back_to="home")
            else:
                self.open_details(e["item"])
        elif action == "info":
            e = self._entry()
            if e and e.get("item"):
                self.open_details(e["item"])
        elif action == "menu":
            e = self._entry()
            if e and e.get("cont"):          # MENU on "Continue watching": take it off the row
                self.history.hide(e["item"]["id"])
                self._build_rows()
        else:
            return False
        self.row_y.target = self.row
        return True

    def _details_key(self, action: str) -> bool:
        d = self.det
        if d is None:
            self.mode = "home"
            return True
        rows = ["buttons"] + (["seasons", "episodes"] if d["item"]["kind"] == "show" and d["eps"] else [])
        if action in ("channel_up", "channel_down"):
            i = rows.index(d["focus"]) if d["focus"] in rows else 0
            i = max(0, min(len(rows) - 1, i + (1 if action == "channel_down" else -1)))
            d["focus"] = rows[i]
        elif action in ("left", "right"):
            k = 1 if action == "right" else -1
            if d["focus"] == "buttons":
                d["btn"] = max(0, min(len(d["buttons"]) - 1, d["btn"] + k))
            elif d["focus"] == "seasons":
                n = len((d.get("detail") or {}).get("seasons") or [])
                ns = max(0, min(n - 1, d["season"] + k))
                if ns != d["season"]:
                    d["season"], d["ep"] = ns, 0
                    d["ep_x"].target = 0
            else:
                d["ep"] = max(0, min(len(self._season_eps()) - 1, d["ep"] + k))
        elif action == "enter":
            self._details_ok()
        elif action == "menu":
            self._details_menu()
        elif action == "info":
            pass
        else:
            return False
        return True

    # -------------------------------------------------------------- search
    KEYS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890") + ["SPACE", "DELETE", "CLEAR"]
    KEY_COLS = 6

    def open_search(self) -> None:
        self._pv_stop()
        self.mode = "search"
        self.search["focus"] = "keys" if not self.search["results"] else self.search["focus"]
        self._run_search()

    @staticmethod
    def _norm(text: str) -> str:
        import unicodedata

        t = unicodedata.normalize("NFKD", text or "")
        t = "".join(c for c in t if not unicodedata.combining(c)).lower()
        return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())

    def _run_search(self) -> None:
        """Titles first (starting with what you typed, then a word starting with it, then anywhere),
        then genres and years. Every word typed must match somewhere."""
        sr = self.search
        q = self._norm(sr["q"])
        words = q.split()
        out = []
        if words:
            for it in self.items or []:
                if not self._visible(it):
                    continue
                t = self._norm(it["title"])
                extra = self._norm(" ".join(it.get("genres") or []) + " " + str(it.get("year") or ""))
                if not all(wd in t or wd in extra for wd in words):
                    continue
                tw = t.split()
                if t.startswith(q):
                    score = 0
                elif any(x.startswith(words[0]) for x in tw):
                    score = 1
                elif all(wd in t for wd in words):
                    score = 2
                else:
                    score = 3
                out.append((score, _sort_key(it["title"]), it))
        out.sort(key=lambda x: (x[0], x[1]))
        sr["results"] = [x[2] for x in out[:120]]
        sr["r"] = min(sr["r"], max(0, len(sr["results"]) - 1))
        if not sr["results"]:
            sr["focus"] = "keys"
        self._dirty = True

    def _type(self, ch: str) -> None:
        sr = self.search
        if ch == "DELETE":
            sr["q"] = sr["q"][:-1]
        elif ch == "CLEAR":
            sr["q"] = ""
        elif ch == "SPACE":
            if sr["q"] and not sr["q"].endswith(" "):
                sr["q"] += " "
        elif len(sr["q"]) < 40:
            sr["q"] += ch.lower() if len(ch) == 1 else ch
        sr["r"] = 0
        self._run_search()

    def type_text(self, key: int, text: str) -> bool:
        """A real keyboard (the remote's back, or a PC keyboard) while searching: letters type directly.
        True = used. Backspace deletes while there's text (with nothing left it means BACK)."""
        if self.mode != "search" or self.win.state.pin_entry is not None:
            return False
        if key == Qt.Key_Backspace and self.search["q"]:
            self._type("DELETE")
            self._last_input = time.monotonic()
            return True
        if len(text) == 1 and (text.isalnum() or text in " '&-.!?:,"):
            self._type("SPACE" if text == " " else text)
            self._last_input = time.monotonic()
            return True
        return False

    def _search_key(self, action: str) -> bool:
        sr = self.search
        cols = self.KEY_COLS
        n = len(self.KEYS)
        letters = 36                       # A-Z and 0-9; then the three wide keys on their own row
        if sr["focus"] == "keys":
            k = sr["k"]
            if action in ("left", "right"):
                d = 1 if action == "right" else -1
                if k >= letters:           # SPACE / DELETE / CLEAR
                    nk = k + d
                    if letters <= nk < n:
                        sr["k"] = nk
                    elif d > 0 and sr["results"]:
                        sr["focus"] = "results"
                else:
                    c = k % cols + d
                    if 0 <= c < cols:
                        sr["k"] = k + d
                    elif d > 0 and sr["results"]:
                        sr["focus"] = "results"
            elif action == "channel_down":
                if k < letters - cols:
                    sr["k"] = k + cols
                elif k < letters:
                    sr["k"] = letters + min(2, (k % cols) // 2)
            elif action == "channel_up":
                if k >= letters:
                    sr["k"] = letters - cols + min(cols - 1, (k - letters) * 2)
                elif k >= cols:
                    sr["k"] = k - cols
            elif action == "enter":
                self._type(self.KEYS[k])
            elif action == "info" or action == "menu":
                pass
            else:
                return False
            return True
        # results grid
        r, total = sr["r"], len(sr["results"])
        rc = sr.get("cols", 5)
        if action == "left":
            if r % rc == 0:
                sr["focus"] = "keys"
            else:
                sr["r"] = r - 1
        elif action == "right":
            sr["r"] = min(total - 1, r + 1)
        elif action == "channel_down":
            if r + rc < total:
                sr["r"] = r + rc
            elif r // rc < (total - 1) // rc:
                sr["r"] = total - 1           # a shorter last row: its last title
        elif action == "channel_up":
            if r >= rc:
                sr["r"] = r - rc
        elif action in ("enter", "info"):
            if total:
                self.open_details(sr["results"][r], back="search")
        else:
            return False
        return True

    def _paint_search(self, p: QPainter, w: int, h: int, dt: float) -> None:
        unit = h / 1080
        sr = self.search
        p.fillRect(QRectF(0, 0, w, h), BG)
        _gradient(p, QRectF(0, 0, w, h * 0.35), False, ((0, QColor(40, 8, 80, 160)), (1, QColor(7, 0, 22, 0))))
        self._header(p, w, h, unit)
        # the search field
        field = QRectF(80 * unit, 130 * unit, w - 160 * unit, 78 * unit)
        path = QPainterPath()
        path.addRoundedRect(field, 39 * unit, 39 * unit)
        p.fillPath(path, QColor(20, 4, 46, 230))
        p.setPen(QPen(QColor(PINK) if sr["focus"] == "keys" else QColor(0, 240, 255, 150), 3 * unit))
        p.drawPath(path)
        # magnifying glass
        cx, cy = field.left() + 46 * unit, field.center().y()
        p.setPen(QPen(QColor(CYAN), 4 * unit))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx - 4 * unit, cy - 4 * unit), 13 * unit, 13 * unit)
        p.drawLine(QPointF(cx + 5 * unit, cy + 5 * unit), QPointF(cx + 15 * unit, cy + 15 * unit))
        q = sr["q"]
        px = int(40 * unit)
        if q:
            tw = _txt(p, q.upper(), px, field.left() + 86 * unit, cy + px * 0.36, QColor("#ffffff"),
                      max_w=field.width() - 300 * unit)
        else:
            tw = 0
            _txt(p, "Search titles, genres or years", px, field.left() + 86 * unit, cy + px * 0.36, DIM)
        if int(time.monotonic() * 2) % 2 == 0:       # cursor
            p.fillRect(QRectF(field.left() + 92 * unit + tw, cy - 22 * unit, 4 * unit, 44 * unit), QColor(PINK))
        count = f"{len(sr['results'])} found" if q else ""
        if count:
            p.setFont(font("text", int(28 * unit)))
            cw = QFontMetricsF(p.font()).horizontalAdvance(count)
            _txt(p, count, int(28 * unit), field.right() - 40 * unit - cw, cy + 10 * unit, CYAN)
        # on-screen keyboard
        kx, ky = 80 * unit, 250 * unit
        ks, gap = 66 * unit, 10 * unit
        cols = self.KEY_COLS
        for i, label in enumerate(self.KEYS):
            if i < 36:
                r = QRectF(kx + (i % cols) * (ks + gap), ky + (i // cols) * (ks + gap), ks, ks)
            else:
                j = i - 36
                wide = (cols * (ks + gap) - gap - 2 * gap) / 3
                r = QRectF(kx + j * (wide + gap), ky + 6 * (ks + gap) + 6 * unit, wide, ks * 0.8)
            sel = sr["focus"] == "keys" and sr["k"] == i
            path = QPainterPath()
            path.addRoundedRect(r, 10 * unit, 10 * unit)
            p.fillPath(path, QColor(PINK) if sel else QColor(26, 8, 58, 220))
            p.setPen(QPen(QColor(255, 255, 255, 200) if sel else QColor(0, 240, 255, 90), (2.5 if sel else 1.2) * unit))
            p.drawPath(path)
            p.setFont(font("text", int((34 if i < 36 else 22) * unit)))
            p.setPen(QColor("#ffffff") if sel else QColor(220, 215, 255))
            p.drawText(r, Qt.AlignCenter, {"DELETE": "⌫ DELETE"}.get(label, label))
        kb_right = kx + cols * (ks + gap)
        # results
        rx = kb_right + 50 * unit
        tw_, th_ = 150 * unit, 225 * unit
        pitch_x, pitch_y = tw_ + 26 * unit, th_ + 64 * unit
        rc = max(1, int((w - 60 * unit - rx + 26 * unit) / pitch_x))
        sr["cols"] = rc
        res = sr["results"]
        if not q:
            _txt(p, "Type with the keyboard here, or the keyboard on your remote.", int(30 * unit), rx,
                 ky + 40 * unit, DIM, max_w=w - rx - 60 * unit)
        elif not res:
            _txt(p, "Nothing matches that yet.", int(32 * unit), rx, ky + 40 * unit, YELLOW)
        sel_row = sr["r"] // rc if res else 0
        visible_rows = max(1, int((h - ky - 120 * unit) / pitch_y))
        top = max(0, sel_row - visible_rows + 1) if sr["focus"] == "results" else 0
        sr["y"].target = top
        if sr["y"].step(dt):
            self._dirty = True
        p.save()
        p.setClipRect(QRectF(rx - 20 * unit, ky - 20 * unit, w, h - ky - 70 * unit))
        for i, it in enumerate(res):
            row, col = divmod(i, rc)
            y = ky + (row - sr["y"].v) * pitch_y
            if y > h or y + pitch_y < ky - 40 * unit:
                continue
            r = QRectF(rx + col * pitch_x, y, tw_, th_)
            sel = sr["focus"] == "results" and i == sr["r"]
            self._tile(p, r, {"item": it}, "poster", sel, unit)
            _txt(p, it["title"], int(22 * unit), r.left(), r.bottom() + (34 if sel else 26) * unit,
                 QColor("#ffffff") if sel else DIM, max_w=tw_ + 10 * unit)
        p.restore()
        if res and sr["focus"] == "results":
            it = res[sr["r"]]
            _txt(p, f"{it['title']}   ·   {ondemand.meta_line(it)}", int(28 * unit), rx, h - 74 * unit, CYAN,
                 max_w=w - rx - 60 * unit)
        self._hints(p, w, h, unit, "▲▼◀▶ choose    OK type / open    ▶ from the keyboard to the results    "
                                   "BACK " + ("keyboard" if sr["focus"] == "results" else "close search"))

    # -------------------------------------------------------------- painting
    def paint(self, p: QPainter, w: int, h: int) -> None:
        now = time.monotonic()
        dt, self._last_paint = min(0.1, now - self._last_paint), now
        if self.mode == "promo":
            self._paint_promo(p, w, h)
        elif self.mode == "home":
            self._paint_home(p, w, h, dt)
        elif self.mode == "search":
            self._paint_search(p, w, h, dt)
        elif self.mode == "details":
            self._paint_details(p, w, h, dt)
        elif self.mode == "play":
            self._paint_play(p, w, h)
        if self.active and self.win.state.pin_entry is not None:
            p.fillRect(QRectF(0, 0, w, h), QColor(6, 0, 20, 215))
            paint_lock(p, w, h, self.win.state)

    def _img(self, path: str, width: int) -> QImage | None:
        return self.images.get(path, width)

    def _header(self, p: QPainter, w: int, h: int, unit: float, sub: str = "") -> None:
        draw_neon(p, "ON DEMAND", "numbers", int(58 * unit), PINK, 56 * unit, 26 * unit)
        if sub:
            _txt(p, sub, int(28 * unit), 60 * unit, 128 * unit, CYAN)
        clock = time.strftime("%-I:%M %p")
        p.setFont(font("text", int(36 * unit)))
        cw = QFontMetricsF(p.font()).horizontalAdvance(clock)
        _txt(p, clock, int(36 * unit), w - 60 * unit - cw, 74 * unit, YELLOW)

    def _hints(self, p: QPainter, w: int, h: int, unit: float, text: str) -> None:
        _gradient(p, QRectF(0, h - 92 * unit, w, 92 * unit), False,
                  ((0, QColor(7, 0, 22, 0)), (0.45, QColor(7, 0, 22, 235)), (1, BG)))
        _txt(p, text, int(25 * unit), 60 * unit, h - 26 * unit, DIM)

    def _backdrop(self, p: QPainter, w: int, h: int, item: dict | None, top_h: float, unit: float) -> None:
        """The selected title's fanart in the top right, fading into the background; while a preview
        plays, mpv draws it in that same area and only the surroundings are painted."""
        r = QRectF(w * HERO_LEFT, 0, w * (1 - HERO_LEFT), top_h)
        live = bool(self.pv and self.pv["t_loaded"])
        if live:
            p.fillRect(QRectF(0, 0, r.left() + 1, h), BG)
            p.fillRect(QRectF(r.left(), top_h - 1, w - r.left(), h - top_h + 1), BG)
            a = _ease((time.monotonic() - self.pv["t_loaded"]) / 0.6)
            if a < 1:                         # fade from the backdrop into the moving picture
                img = self._img((item or {}).get("art", {}).get("fanart") or "", 1920 if h > 1080 else 1280)
                if img is not None:
                    p.setOpacity(1 - a)
                    _cover(p, img, r, align_top=True)
                    p.setOpacity(1)
        else:
            p.fillRect(QRectF(0, 0, w, h), BG)
        if not item:
            return
        art = item.get("art") or {}
        path = art.get("fanart") or art.get("landscape") or ""
        if path != self._hero[0]:
            self._hero = (path, time.monotonic())
        img = None if live else self._img(path, 1920 if h > 1080 else 1280)
        if img is None and not live:
            return
        a = 1.0 if live else _ease((time.monotonic() - self._hero[1]) / 0.45)
        if img is not None:
            p.setOpacity(a)
            _cover(p, img, r, align_top=True)
            p.setOpacity(1)
        _gradient(p, QRectF(r.left() - 1, 0, r.width() * 0.55, top_h), True,
                  ((0, BG), (0.35, QColor(7, 0, 22, 200)), (1, QColor(7, 0, 22, 0))))
        _gradient(p, QRectF(r.left(), top_h * 0.55, r.width(), top_h * 0.45 + 1), False,
                  ((0, QColor(7, 0, 22, 0)), (1, BG)))
        if a < 1:
            self._dirty = True
        if live:
            left = self.pv["t_loaded"] + PREVIEW_S - time.monotonic()
            if left < 4.5:
                msg = f"PLAYING IN {max(1, int(left + 0.99))}   ·   OK now   ·   any button cancels"
                p.setFont(font("text", int(28 * unit)))
                tw = QFontMetricsF(p.font()).horizontalAdvance(msg)
                box = QRectF(w - tw - 100 * unit, top_h - 92 * unit, tw + 40 * unit, 50 * unit)
                p.fillRect(box, QColor(7, 0, 22, 200))
                p.fillRect(QRectF(box.left(), box.top(), 5 * unit, box.height()), QColor(PINK))
                _txt(p, msg, int(28 * unit), box.left() + 22 * unit, box.top() + 35 * unit, YELLOW)

    def _hero_text(self, p: QPainter, item: dict, x: float, y: float, max_w: float, unit: float,
                   extra: str = "", plot_lines: int = 3, plot: str | None = None) -> float:
        art = item.get("art") or {}
        logo = art.get("logo") or ""
        limg = self._img(logo, 800) if logo else None
        box_h = 150 * unit
        if limg is not None:
            s = min(max_w * 0.8 / limg.width(), box_h / limg.height())
            p.drawImage(QRectF(x, y, limg.width() * s, limg.height() * s), limg)
            y += limg.height() * s + 26 * unit
        else:
            px = _fit(item["title"], "text", int(84 * unit), max_w)
            _chrome_text(p, item["title"], px, x, y + px * 0.95)
            y += px * 1.25
        bx = x
        if ondemand.is_new(item):
            r = QRectF(x, y - 4 * unit, 82 * unit, 38 * unit)
            p.fillRect(r, QColor(YELLOW))
            p.setFont(font("text", int(24 * unit)))
            p.setPen(QColor(20, 0, 30))
            p.drawText(r, Qt.AlignCenter, "NEW")
            bx += 100 * unit
        _txt(p, ondemand.meta_line(item), int(32 * unit), bx, y + 26 * unit, CYAN, max_w=max_w - (bx - x))
        y += 48 * unit
        if extra:
            _txt(p, extra, int(32 * unit), x, y + 28 * unit, YELLOW, max_w=max_w)
            y += 48 * unit
        text = item.get("plot", "") if plot is None else plot
        if item.get("tagline") and plot is None and plot_lines >= 3:
            text = f"{item['tagline']}  {text}".strip() if not text.startswith(item["tagline"]) else text
        px = int(30 * unit)
        p.setFont(font("text", px))
        for line in _wrap(text or "", px, max_w, plot_lines):
            p.setPen(QColor(235, 230, 255))
            p.drawText(QPointF(x, y + px), line)
            y += px * 1.35
        return y

    def _tile(self, p: QPainter, r: QRectF, e: dict, kind: str, sel: bool, unit: float) -> None:
        it = e.get("item")
        if sel:
            r = QRectF(r.center().x() - r.width() * 0.54, r.center().y() - r.height() * 0.54,
                       r.width() * 1.08, r.height() * 1.08)
        p.fillRect(r.adjusted(6 * unit, 8 * unit, 6 * unit, 8 * unit), QColor(0, 0, 0, 120))
        if e.get("lock"):
            p.fillRect(r, PANEL)
            p.setPen(QPen(PINK, 5 * unit))
            p.setBrush(Qt.NoBrush)
            cx, cy = r.center().x(), r.center().y() - 20 * unit
            p.drawArc(QRectF(cx - 26 * unit, cy - 60 * unit, 52 * unit, 60 * unit), 0, 180 * 16)
            p.setBrush(QColor(60, 0, 80))
            p.drawRoundedRect(QRectF(cx - 40 * unit, cy - 30 * unit, 80 * unit, 62 * unit), 8 * unit, 8 * unit)
            p.setFont(font("text", int(22 * unit)))
            p.setPen(QColor(230, 220, 255))
            p.drawText(QRectF(r.left(), cy + 46 * unit, r.width(), 70 * unit), Qt.AlignHCenter | Qt.TextWordWrap,
                       f"{self._hidden_count()} hidden\nOK: enter PIN")
        else:
            art = it.get("art") or {}
            if kind == "wide":
                path = art.get("landscape") or art.get("fanart") or art.get("poster") or ""
                img = self._img(path, 512 if unit > 1.2 else 400)
            else:
                path = art.get("poster") or ""
                img = self._img(path, 512 if unit > 1.2 else 300)
            if img is not None:
                _cover(p, img, r)
            else:
                _gradient(p, r, False, ((0, QColor(50, 14, 100)), (1, QColor(14, 2, 36))))
                p.setFont(font("text", int(24 * unit)))
                p.setPen(QColor(230, 220, 255))
                p.drawText(r.adjusted(10 * unit, 10 * unit, -10 * unit, -10 * unit),
                           Qt.AlignCenter | Qt.TextWordWrap, it["title"])
            if kind == "wide":
                _gradient(p, QRectF(r.left(), r.bottom() - r.height() * 0.5, r.width(), r.height() * 0.5), False,
                          ((0, QColor(0, 0, 0, 0)), (1, QColor(0, 0, 0, 210))))
                logo = art.get("logo") or ""
                limg = self._img(logo, 400) if logo else None
                if limg is not None:
                    s = min(r.width() * 0.6 / limg.width(), r.height() * 0.32 / limg.height())
                    p.drawImage(QRectF(r.left() + 12 * unit, r.bottom() - 30 * unit - limg.height() * s,
                                       limg.width() * s, limg.height() * s), limg)
                else:
                    _txt(p, it["title"], int(26 * unit), r.left() + 12 * unit, r.bottom() - 34 * unit,
                         QColor("#ffffff"), max_w=r.width() - 24 * unit)
                if e.get("label"):
                    _txt(p, e["label"], int(20 * unit), r.left() + 12 * unit, r.bottom() - 12 * unit,
                         QColor(230, 225, 255), max_w=r.width() - 24 * unit)
                _bar(p, QRectF(r.left(), r.bottom() - 5 * unit, r.width(), 5 * unit), e.get("fraction", 0), unit)
            elif ondemand.is_new(it):
                b = QRectF(r.left(), r.top() + 10 * unit, 58 * unit, 26 * unit)
                p.fillRect(b, QColor(YELLOW))
                p.setFont(font("text", int(18 * unit)))
                p.setPen(QColor(20, 0, 30))
                p.drawText(b, Qt.AlignCenter, "NEW")
        if sel:
            p.setPen(QPen(QColor(PINK), 4 * unit))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            glow = QColor(PINK)
            glow.setAlpha(70)
            p.setPen(QPen(glow, 12 * unit))
            p.drawRect(r.adjusted(-5 * unit, -5 * unit, 5 * unit, 5 * unit))

    def _paint_home(self, p: QPainter, w: int, h: int, dt: float) -> None:
        unit = h / 1080
        e = self._entry()
        item = e.get("item") if e else None
        top_h = h * 0.60
        self._backdrop(p, w, h, item, top_h, unit)
        self._header(p, w, h, unit)
        if self.items is None:
            draw_neon(p, "Loading the library…", "text", int(52 * unit), CYAN, w / 2, h * 0.5, "cm")
            self._dirty = True
            return
        if not self.rows:
            msg = self.note or "Nothing in the library folders yet."
            draw_neon(p, "NOTHING ON DEMAND YET", "text", int(60 * unit), PINK, w / 2, h * 0.42, "cm")
            px = int(34 * unit)
            p.setFont(font("text", px))
            y = h * 0.52
            for line in _wrap(msg, px, w * 0.7, 3):
                p.setPen(QColor(230, 225, 255))
                tw = QFontMetricsF(p.font()).horizontalAdvance(line)
                p.drawText(QPointF((w - tw) / 2, y), line)
                y += px * 1.4
            self._hints(p, w, h, unit, "BACK  menu (switch to cable)")
            return
        if item:
            extra = ""
            if e.get("cont"):
                extra = (e.get("label") + "  ·  " if e.get("label") else "")
                dur = (self.history.files.get(e["path"]) or {}).get("dur") or 0
                extra += _left(dur - e["resume"]) if e["resume"] and dur else "Up next"
            self._hero_text(p, item, 80 * unit, 170 * unit, w * 0.42, unit, extra=extra)
        elif e and e.get("lock"):
            _chrome_text(p, "Restricted titles", int(72 * unit), 80 * unit, 260 * unit)
            _txt(p, "Some titles are on restricted channels. Press OK and enter the PIN to show them.",
                 int(30 * unit), 80 * unit, 330 * unit, CYAN, max_w=w * 0.88)
        moving = self.row_y.step(dt)
        y0 = h * 0.585
        row_h = {"poster": 330 * unit, "wide": 250 * unit, "lock": 330 * unit}
        tile = {"poster": (170 * unit, 255 * unit), "wide": (300 * unit, 169 * unit), "lock": (170 * unit, 255 * unit)}
        gap = 22 * unit
        # rows above the selected one slide up under the backdrop: clip them
        p.save()
        p.setClipRect(QRectF(0, y0 - 14 * unit, w, h))
        offset = 0.0
        heights = [row_h[r["kind"]] for r in self.rows]
        f = self.row_y.v
        i0 = int(f)
        offset = sum(heights[:i0]) + (heights[i0] * (f - i0) if i0 < len(heights) else 0)
        y = y0 - offset
        for ri, r in enumerate(self.rows):
            rh = heights[ri]
            if y > h:
                break
            if y + rh < y0 - 20 * unit:
                y += rh
                continue
            t = r["title"]
            sel_row = ri == self.row
            _txt(p, t.upper() if sel_row else t, int((32 if sel_row else 28) * unit), 80 * unit, y + 30 * unit,
                 QColor("#ffffff") if sel_row else DIM)
            tw, th = tile[r["kind"]]
            pitch = tw + gap
            col = self.cols.get(t, 0)
            ex = self.col_x.setdefault(t, _Ease(col))
            visible = max(1, int((w - 160 * unit) / pitch))
            # keep the selection in view, two tiles from the left once the row scrolls
            if col - ex.target > visible - 2:
                ex.target = col - (visible - 2)
            elif col - ex.target < 1:
                ex.target = max(0, col - 1)
            moving = ex.step(dt) or moving
            x0 = 80 * unit - ex.v * pitch
            ty = y + 48 * unit
            first = max(0, int(ex.v) - 1)
            for ci in range(first, min(len(r["entries"]), first + visible + 3)):
                x = x0 + ci * pitch
                if x > w or x + tw < 0:
                    continue
                if sel_row and ci == col:
                    continue           # drawn last, on top
                self._tile(p, QRectF(x, ty, tw, th), r["entries"][ci], r["kind"], False, unit)
            if sel_row and r["entries"]:
                self._tile(p, QRectF(x0 + col * pitch, ty, tw, th), r["entries"][col], r["kind"], True, unit)
            y += rh
        p.restore()
        if moving:
            self._dirty = True
        if self.row == 0 or time.monotonic() - self._up_armed < 4.0:
            armed = time.monotonic() - self._up_armed < 4.0
            label = "▲ AGAIN TO SEARCH" if armed else "▲▲  SEARCH"
            p.setFont(font("text", int(26 * unit)))
            lw = QFontMetricsF(p.font()).horizontalAdvance(label) + 40 * unit
            box = QRectF(w - lw - 220 * unit, 40 * unit, lw, 46 * unit)
            path = QPainterPath()
            path.addRoundedRect(box, 23 * unit, 23 * unit)
            p.fillPath(path, QColor(PINK) if armed else QColor(20, 4, 46, 200))
            p.setPen(QPen(QColor(0, 240, 255, 160), 1.5 * unit))
            p.drawPath(path)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, label)
            if armed:
                self._dirty = True
        hint = "▲▼◀▶ browse    OK "
        if e and e.get("cont"):
            hint += "resume    INFO details    MENU remove from row"
        elif e and e.get("lock"):
            hint += "enter PIN"
        else:
            hint += "details"
        self._hints(p, w, h, unit, hint + "    BACK menu (switch to cable)")

    def _paint_details(self, p: QPainter, w: int, h: int, dt: float) -> None:
        unit = h / 1080
        d = self.det
        it = d["item"]
        art = it.get("art") or {}
        p.fillRect(QRectF(0, 0, w, h), BG)
        img = self._img(art.get("fanart") or art.get("landscape") or "", 1920 if h > 1080 else 1280)
        if img is not None:
            _cover(p, img, QRectF(0, 0, w, h))
            p.fillRect(QRectF(0, 0, w, h), QColor(7, 0, 22, 70))
        _gradient(p, QRectF(0, 0, w * 0.75, h), True,
                  ((0, QColor(7, 0, 22, 245)), (0.6, QColor(7, 0, 22, 180)), (1, QColor(7, 0, 22, 0))))
        _gradient(p, QRectF(0, h * 0.45, w, h * 0.55), False, ((0, QColor(7, 0, 22, 0)), (0.5, QColor(7, 0, 22, 225)),
                                                               (1, BG)))
        self._header(p, w, h, unit)
        show = it["kind"] == "show"
        extra, plot = "", None
        if it["kind"] == "movie":
            res = self.history.resume_at(it["path"])
            dur = (self.history.files.get(it["path"]) or {}).get("dur") or 0
            if res and dur:
                extra = _left(dur - res)
            elif self.history.done(it["path"]):
                extra = "✓ Watched"
        elif d["focus"] == "episodes" and self._season_eps():
            ep = self._season_eps()[d["ep"]]
            bits = [_ep_label(ep)]
            if ep.get("aired"):
                bits.append(ep["aired"])
            if ep.get("runtime"):
                bits.append(f"{ep['runtime']} min")
            extra = "  ·  ".join(bits)
            plot = ep.get("plot") or ""
        y = self._hero_text(p, it, 80 * unit, 150 * unit, w * 0.48, unit, extra=extra,
                            plot_lines=4 if show else 6, plot=plot)
        by = max(y + 24 * unit, (500 if show else 560) * unit)
        x = 80 * unit
        for i, (_k, label) in enumerate(d["buttons"]):
            p.setFont(font("text", int(30 * unit)))
            bw = QFontMetricsF(p.font()).horizontalAdvance(label) + 70 * unit
            _pill(p, QRectF(x, by, bw, 64 * unit), label, d["focus"] == "buttons" and i == d["btn"], unit)
            x += bw + 20 * unit
        if show:
            seasons = (d.get("detail") or {}).get("seasons") or []
            sy = by + 104 * unit
            x = 80 * unit
            for si, s in enumerate(seasons):
                sel = si == d["season"]
                label = s["name"]
                p.setFont(font("text", int(30 * unit)))
                tw = QFontMetricsF(p.font()).horizontalAdvance(label)
                if x + tw > w - 60 * unit:
                    break
                col = QColor("#ffffff") if sel else DIM
                _txt(p, label, int(30 * unit), x, sy, col)
                if sel:
                    p.fillRect(QRectF(x, sy + 10 * unit, tw, 5 * unit),
                               QColor(PINK) if d["focus"] == "seasons" else QColor(CYAN))
                x += tw + 42 * unit
            eps = self._season_eps()
            ey = sy + 40 * unit
            tw_, th_ = 320 * unit, 180 * unit
            pitch = tw_ + 24 * unit
            ex = d["ep_x"]
            visible = max(1, int((w - 160 * unit) / pitch))
            if d["ep"] - ex.target > visible - 2:
                ex.target = d["ep"] - (visible - 2)
            elif d["ep"] - ex.target < 1:
                ex.target = max(0, d["ep"] - 1)
            if ex.step(dt):
                self._dirty = True
            x0 = 80 * unit - ex.v * pitch
            fallback = art.get("landscape") or art.get("fanart") or ""
            for ei, ep in enumerate(eps):
                x = x0 + ei * pitch
                if x > w or x + tw_ < 0:
                    continue
                sel = d["focus"] == "episodes" and ei == d["ep"]
                r = QRectF(x, ey, tw_, th_)
                eimg = self._img(ep.get("thumb") or fallback, 400 if unit <= 1.2 else 640)
                if eimg is not None:
                    _cover(p, eimg, r)
                    if not ep.get("thumb"):
                        p.fillRect(r, QColor(7, 0, 22, 120))
                else:
                    p.fillRect(r, PANEL)
                frac = self.history.fraction(ep["path"])
                if frac > 0:
                    _bar(p, QRectF(r.left(), r.bottom() - 6 * unit, r.width(), 6 * unit), frac, unit)
                if self.history.done(ep["path"]):
                    b = QRectF(r.right() - 44 * unit, r.top() + 8 * unit, 36 * unit, 36 * unit)
                    p.setBrush(QColor(PINK))
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(b)
                    p.setPen(QColor("#ffffff"))
                    p.setFont(font("text", int(24 * unit)))
                    p.drawText(b, Qt.AlignCenter, "✓")
                p.setPen(QPen(QColor(PINK) if sel else QColor(0, 240, 255, 90), (4 if sel else 1.5) * unit))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r)
                lab = f"{ep['episode']}. {ep['title']}" if ep.get("episode") is not None else ep.get("title", "")
                _txt(p, lab, int(24 * unit), r.left(), r.bottom() + 32 * unit,
                     QColor("#ffffff") if sel else DIM, max_w=tw_)
            if d["loading"]:
                _txt(p, "Loading episodes…", int(30 * unit), 80 * unit, ey + 60 * unit, CYAN)
        hint = "▲▼◀▶ choose    OK play    "
        if it["kind"] == "movie" or d["focus"] == "episodes":
            hint += "MENU watched / unwatched    "
        self._hints(p, w, h, unit, hint + "BACK back")

    def _paint_promo(self, p: QPainter, w: int, h: int) -> None:
        unit = h / 1080
        pr = self.promo
        if not pr:
            return
        if not pr["entries"]:
            p.fillRect(QRectF(0, 0, w, h), BG)
            self._header(p, w, h, unit)
            if not pr["ready"] or self.items is None:
                draw_neon(p, "Loading the library…", "text", int(48 * unit), CYAN, w / 2, h * 0.5, "cm")
                return
            draw_neon(p, "ON DEMAND", "numbers", int(130 * unit), PINK, w / 2, h * 0.40, "cm", flicker=True)
            note = self.note or "Nothing in the library folders yet."
            draw_neon(p, note, "text", int(34 * unit), CYAN, w / 2, h * 0.58, "cm")
            return
        e = pr["entries"][max(0, pr["i"])]
        it = e["item"]
        t = time.monotonic() - pr["t0"]
        if not pr["loaded"]:
            art = it.get("art") or {}
            img = self._img(art.get("fanart") or art.get("landscape") or "", 1920 if h > 1080 else 1280)
            p.fillRect(QRectF(0, 0, w, h), BG)
            if img is not None:
                _cover(p, img, QRectF(0, 0, w, h))
        _gradient(p, QRectF(0, 0, w * 0.62, h), True,
                  ((0, QColor(7, 0, 22, 235)), (0.55, QColor(7, 0, 22, 160)), (1, QColor(7, 0, 22, 0))))
        _gradient(p, QRectF(0, h * 0.62, w, h * 0.38), False, ((0, QColor(7, 0, 22, 0)), (1, QColor(7, 0, 22, 230))))
        draw_neon(p, "ON DEMAND", "numbers", int(58 * unit), PINK, 56 * unit, 26 * unit)
        a = _ease(t / 0.8)
        p.setOpacity(a)
        _txt(p, e["label"], int(34 * unit), 80 * unit, 250 * unit, YELLOW)
        self._hero_text(p, it, 80 * unit, 280 * unit, w * 0.44, unit, extra=e.get("sub", ""), plot_lines=3)
        p.setOpacity(1)
        # call to action, pulsing
        pulse = 0.65 + 0.35 * (0.5 + 0.5 * math.sin(time.monotonic() * 3.2))
        r = QRectF(80 * unit, h - 210 * unit, 760 * unit, 84 * unit)
        path = QPainterPath()
        path.addRoundedRect(r, 42 * unit, 42 * unit)
        c = QColor(PINK)
        c.setAlpha(int(200 * pulse))
        p.fillPath(path, c)
        p.setPen(QPen(QColor(255, 255, 255, 200), 2 * unit))
        p.drawPath(path)
        p.setFont(font("text", int(34 * unit)))
        p.setPen(QColor("#ffffff"))
        p.drawText(r, Qt.AlignCenter, "PRESS OK  ·  WATCH ANYTHING, ANY TIME")
        # strip of what's coming up on this channel
        n = len(pr["entries"])
        pw_, ph_ = 96 * unit, 144 * unit
        x = w - 60 * unit - min(n, 8) * (pw_ + 14 * unit)
        for k in range(min(n, 8)):
            idx = (pr["i"] + k) % n
            ent = pr["entries"][idx]
            rr = QRectF(x + k * (pw_ + 14 * unit), h - 200 * unit, pw_, ph_)
            img = self._img((ent["item"].get("art") or {}).get("poster") or "", 300)
            if img is not None:
                p.setOpacity(1 if k == 0 else 0.6)
                _cover(p, img, rr)
                p.setOpacity(1)
            else:
                p.fillRect(rr, PANEL)
            if k == 0:
                p.setPen(QPen(QColor(PINK), 3 * unit))
                p.setBrush(Qt.NoBrush)
                p.drawRect(rr)
        _bar(p, QRectF(0, h - 4 * unit, w, 4 * unit), t / PROMO_SECONDS, unit)

    def _paint_play(self, p: QPainter, w: int, h: int) -> None:
        unit = h / 1080
        pl = self.p
        if pl is None:
            return
        it = pl["item"]
        now = time.monotonic()
        if pl.get("grow"):                       # a preview growing to full screen
            f = _ease((now - pl["grow"]) / GROW_S)
            if f >= 1:
                pl["grow"] = None
                self._margins()
                self.win._set_zoom("normal")
                self.win._apply_zoom()
            else:
                self._margins(HERO_LEFT * (1 - f), 0.0, 0.0, (1 - HERO_H) * (1 - f))
                p.fillRect(QRectF(0, 0, w * HERO_LEFT * (1 - f), h), BG)
                p.fillRect(QRectF(0, h * (1 - (1 - HERO_H) * (1 - f)), w, h), BG)
        if not pl["loaded"] or pl["error"]:
            p.fillRect(QRectF(0, 0, w, h), BG)
            art = it.get("art") or {}
            img = self._img(art.get("fanart") or art.get("landscape") or "", 1280)
            if img is not None:
                _cover(p, img, QRectF(0, 0, w, h))
                p.fillRect(QRectF(0, 0, w, h), QColor(7, 0, 22, 170))
            title = it["title"]
            draw_neon(p, title, "text", _fit(title, "text", int(70 * unit), w * 0.8), PINK, w / 2, h * 0.44, "cm")
            sub = pl["error"] or (_ep_label(pl["ep"]) if pl["ep"] else "")
            if sub:
                draw_neon(p, sub, "text", int(38 * unit), CYAN if not pl["error"] else YELLOW, w / 2, h * 0.54, "cm")
            if not pl["error"]:
                dots = "·" * (1 + int(now * 3) % 3)
                draw_neon(p, "Loading " + dots, "text", int(34 * unit), YELLOW, w / 2, h * 0.64, "cm")
            return
        pos = pl["seek"] if pl["seek"] is not None else (pl.get("pos") or 0)
        dur = pl.get("dur") or 0
        nxt = self._next_ep()
        if nxt and dur and dur - (pl.get("pos") or 0) <= NEXT_CARD_S and not pl["next_dismissed"]:
            self._next_card(p, w, h, unit, nxt, dur - (pl.get("pos") or 0))
        if now > pl["osd_until"] and not pl["paused"]:
            return
        op = min(1.0, (pl["osd_until"] - now) / 0.4) if not pl["paused"] else 1.0
        p.save()
        p.setOpacity(max(0.0, op))
        _gradient(p, QRectF(0, h * 0.62, w, h * 0.38), False, ((0, QColor(0, 0, 0, 0)), (1, QColor(5, 0, 18, 235))))
        _gradient(p, QRectF(0, 0, w, h * 0.16), False, ((0, QColor(5, 0, 18, 200)), (1, QColor(0, 0, 0, 0))))
        draw_neon(p, "ON DEMAND", "numbers", int(40 * unit), PINK, 50 * unit, 22 * unit)
        clock = time.strftime("%-I:%M %p")
        p.setFont(font("text", int(34 * unit)))
        _txt(p, clock, int(34 * unit), w - 60 * unit - QFontMetricsF(p.font()).horizontalAdvance(clock), 64 * unit,
             YELLOW)
        x = 80 * unit
        _chrome_text(p, it["title"], _fit(it["title"], "text", int(58 * unit), w * 0.7), x, h - 200 * unit)
        sub = _ep_label(pl["ep"]) if pl["ep"] else ondemand.meta_line(it)
        _txt(p, sub, int(32 * unit), x, h - 150 * unit, CYAN, max_w=w * 0.8)
        bar = QRectF(x, h - 110 * unit, w - 2 * x, 8 * unit)
        _bar(p, bar, pos / dur if dur else 0, unit)
        if dur:
            kx = bar.left() + bar.width() * max(0.0, min(1.0, pos / dur))
            p.setBrush(QColor("#ffffff"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(kx, bar.center().y()), 11 * unit, 11 * unit)
        _txt(p, _hms(pos), int(30 * unit), x, h - 62 * unit, QColor("#ffffff"))
        right = "-" + _hms(dur - pos) if dur else ""
        p.setFont(font("text", int(30 * unit)))
        _txt(p, right, int(30 * unit), bar.right() - QFontMetricsF(p.font()).horizontalAdvance(right),
             h - 62 * unit, QColor("#ffffff"))
        hint = "OK pause    ◀▶ skip (hold for faster)    MENU audio · subtitles · picture    BACK stop"
        p.setFont(font("text", int(24 * unit)))
        _txt(p, hint, int(24 * unit), (w - QFontMetricsF(p.font()).horizontalAdvance(hint)) / 2, h - 22 * unit, DIM)
        if pl.get("info") and pl["ep"] and pl["ep"].get("plot"):
            px = int(28 * unit)
            p.setFont(font("text", px))
            yy = h * 0.62 - 3 * px * 1.4
            for line in _wrap(pl["ep"]["plot"], px, w * 0.6, 3):
                p.setPen(QColor(235, 230, 255))
                p.drawText(QPointF(x, yy), line)
                yy += px * 1.4
        if pl["paused"]:
            cx, cy = w / 2, h * 0.45
            p.setBrush(QColor(255, 43, 214, 200))
            p.setPen(QPen(QColor("#ffffff"), 3 * unit))
            p.drawEllipse(QPointF(cx, cy), 70 * unit, 70 * unit)
            p.setBrush(QColor("#ffffff"))
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(cx - 24 * unit, cy - 30 * unit, 16 * unit, 60 * unit))
            p.drawRect(QRectF(cx + 8 * unit, cy - 30 * unit, 16 * unit, 60 * unit))
        p.restore()

    def _next_card(self, p: QPainter, w: int, h: int, unit: float, nxt: dict, left: float) -> None:
        r = QRectF(w - 640 * unit, h * 0.52, 580 * unit, 190 * unit)
        p.fillRect(r, QColor(14, 2, 36, 230))
        p.setPen(QPen(QColor(PINK), 3 * unit))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        th = QRectF(r.left() + 16 * unit, r.top() + 16 * unit, 280 * unit, 158 * unit)
        art = (self.p["item"].get("art") or {})
        img = self._img(nxt.get("thumb") or art.get("landscape") or art.get("fanart") or "", 400)
        if img is not None:
            _cover(p, img, th)
        else:
            p.fillRect(th, PANEL)
        x = th.right() + 18 * unit
        mw = r.right() - x - 14 * unit
        _txt(p, f"NEXT EPISODE IN {max(0, int(left))}", int(24 * unit), x, r.top() + 44 * unit, YELLOW, max_w=mw)
        _txt(p, nxt.get("code", ""), int(28 * unit), x, r.top() + 84 * unit, QColor("#ffffff"), max_w=mw)
        _txt(p, nxt.get("title", ""), int(26 * unit), x, r.top() + 120 * unit, CYAN, max_w=mw)
        _txt(p, "OK play now  ·  BACK hide", int(20 * unit), x, r.top() + 166 * unit, DIM, max_w=mw)
