"""TV mode: one fullscreen window, one mpv, live schedule-driven tuning.

Tuning sequence
  1. key press -> static + hiss immediately, mpv muted, neon number shown
  2. ask FS42 what is on now -> load that file at the matching position
  3. mpv reports frames are flowing -> double-check we are within a second of
     the wall-clock position (seek once if a slow load drifted us)
  4. after a short minimum, static fades out and sound comes back

Within a channel, when a segment (show part, bump, commercial) ends, the next
one is loaded straight away from the schedule with no static.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import json
import logging
import os
import re
import random
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QMainWindow

from .. import assets_gen, fs42, paths, settings
from .guide import GuideData, GuideRenderer
from .hiss import Hiss
from .keys import KeyMap
from .awake import KeepAwake
from .menu import ExitMenu, MusicMenu, TrackMenu, lang_name
from .mpv_widget import MpvWidget
from .ondemand import OnDemand
from .overlays import OverlayRenderer, OverlayState, load_fonts

_log = logging.getLogger("getsome.tv")

LOAD_LEAD = 0.25        # seconds: assume a load takes about this long
MIN_STATIC = 0.45       # seconds of static on every channel change, even when loading is instant
MAX_DRIFT = 1.0         # seconds off the schedule before we seek to correct
DIGIT_TIMEOUT_MS = 1600
NO_SIGNAL_TYPES = {"guide", "web", "executable"}  # FS42 types we don't render
GUIDE_TYPE = "getsome_guide"
ONDEMAND_TYPE = "getsome_ondemand"    # the On Demand preview channel (tv/ondemand.py)
CARDS_BEFORE = 180      # seconds: long gaps before a film show film cards until this much is left


EXIT_TO_DESKTOP = 10        # exit code: the setup window (if it started us) closes too


class TVWindow(QMainWindow):
    def __init__(self, windowed: bool = False, start_channel: int | None = None):
        super().__init__()
        self.setWindowTitle(f"{paths.APP_NAME} TV")
        load_fonts()
        self.state = OverlayState()
        self.renderer = OverlayRenderer(self.state)
        self.settings = settings.load()
        from .. import cablebox

        self.cable_box = cablebox.active()
        if self.cable_box:          # the head-end decides the guide channel, the PIN and relock time
            self.settings.update(cablebox.shared_settings())
        self.video = MpvWidget(self, overlay_painter=self.renderer.paint,
                               hwdec=str(self.settings["hwdec"]), volume=float(self.settings["volume"]))
        self.setCentralWidget(self.video)
        self.hiss = Hiss(volume=float(self.settings["hiss_volume"]))
        self.keys = KeyMap()

        self.channels, real = self._channel_list()
        self.guide_data = GuideData(real)
        if self.cable_box:
            # streaming from the head-end: read well ahead so Wi-Fi hiccups don't show
            for k, v in (("cache", "yes"), ("demuxer-max-bytes", "300MiB"), ("demuxer-readahead-secs", 30),
                         ("cache-secs", 30)):
                try:
                    self.video.player[k] = v
                except Exception as e:
                    _log.debug("cache option %s: %s", k, e)
            # and follow the head-end's line-up while TV mode is open
            self.lineup_timer = QTimer(self, interval=120_000, timeout=self._refresh_lineup)
            self.lineup_timer.start()
        self.renderer.guide = GuideRenderer(self.guide_data)
        self.renderer.guide.preview_enabled = bool(self.settings.get("guide_preview", True))
        self.renderer.guide.preview_image = lambda: self.video.preview_image
        self.preview_timer = QTimer(self, timeout=self._next_preview)
        self._preview_i: int | None = None
        self.menu = TrackMenu()
        self.renderer.menu = self.menu
        self.music_menu = MusicMenu()
        self.renderer.music_menu = self.music_menu
        self.exit_menu = ExitMenu()
        self.renderer.exit_menu = self.exit_menu
        self.exit_choice = "tv"          # what to do after the window closes (see run_tv)
        self.vod = OnDemand(self)        # On Demand: browse and play the library
        self.renderer.vod = self.vod
        self.awake = KeepAwake()
        self.last_input = time.monotonic()
        self.awake_timer = QTimer(self, interval=60_000, timeout=self._check_awake)
        self._np_tick = 0
        self._prefs_for = None
        self.index = 0
        self.prev_index: int | None = None
        self.seg: fs42.Segment | None = None
        self.token = 0
        self.tuning = False
        self.tune_started = 0.0
        self.corrected = False
        self.digits = ""
        self.windowed = windowed
        self._closing = False
        # restricted channels: blurred and silent until the PIN is entered (once per TV session)
        try:
            from .. import model

            self.restricted = {c.number: c for c in model.load() if c.restricted}
        except Exception as e:
            _log.warning("could not read restricted channels: %s", e)
            self.restricted = {}
        # scheduled locks open and close by themselves on the hour
        self.lock_timer = QTimer(self, interval=20_000, timeout=self._check_lock_schedule)
        self.lock_timer.start()
        self.unlocked = False
        self.away_since: float | None = None   # when we last left a restricted channel while unlocked
        self.pin_timer = QTimer(self, singleShot=True, timeout=self._pin_timeout)
        from .gamepad import Gamepads

        self.gamepads = Gamepads()
        self.pad_timer = QTimer(self, interval=16, timeout=self._poll_gamepads)
        self.pad_timer.start()
        self.renderer.guide.hidden = self._locked_numbers

        self.seg_timer = QTimer(self, singleShot=True, timeout=self._segment_finished)
        # coarse timers may fire up to 5% late (~1 s per minute); segments need to be on the dot
        self.seg_timer.setTimerType(Qt.PreciseTimer)
        self.digit_timer = QTimer(self, singleShot=True, timeout=self._commit_digits)
        self.anim_timer = QTimer(self, interval=16, timeout=self._animate)
        self.anim_timer.start()
        self.watchdog = QTimer(self, interval=5000, timeout=self._watchdog)
        self.watchdog.start()
        self._stalls = 0
        self.video.playback_restart.connect(self._on_restart)
        self.video.file_ended.connect(self._on_ended)

        if not windowed:
            self.setCursor(QCursor(Qt.BlankCursor))

        if not self.channels:
            self.state.black = True
            if self.cable_box:
                self.state.message = "NO SIGNAL"
                self.state.message_sub = "Can't reach the head-end. Is the other computer on and sharing?"
            else:
                self.state.message = "NO CHANNELS"
                self.state.message_sub = "Open setup and make a channel first"
            return

        if start_channel is not None:
            idx = self._index_of(start_channel)
        else:
            idx = self._saved_index() if self.settings["remember_last_channel"] else None
        QTimer.singleShot(0, lambda: self.tune(idx if idx is not None else 0))
        self._start_schedule_keeper()
        if self.settings.get("keep_awake", True):
            self.awake.start()
            self.awake_timer.start()

    # ------------------------------------------------------------------ helpers
    def _index_of(self, number: int | None):
        for i, c in enumerate(self.channels):
            if c.number == number:
                return i
        return None

    def _saved_index(self):
        try:
            data = json.loads((paths.config_dir() / "state.json").read_text())
            return self._index_of(int(data.get("channel")))
        except Exception:
            return None

    def _save_state(self):
        if self.channels:
            try:
                (paths.config_dir() / "state.json").write_text(json.dumps({"channel": self.channels[self.index].number}))
            except OSError:
                pass

    def _animate(self):
        if self.menu.expired():
            self.menu.close()
            self.video.update()
        if self.music_menu.expired():
            self.music_menu.close()
        if self.exit_menu.expired():
            self.exit_menu.close()
            self.video.update()
        self._music_tick = getattr(self, "_music_tick", 0) + 1
        if self._music_tick % 120 == 0 and (self.state.guide or getattr(self, "_pending_music", None)):
            self._check_pending_music()
        if self.state.guide and getattr(self, "_guide_shuffling", False):
            self._np_tick += 1
            if self._np_tick % 30 == 0:
                try:
                    path = self.video.player.path
                    if path:
                        self.renderer.guide.now_playing = assets_gen.track_name(path)
                except Exception:
                    pass
        if (self.music_menu.open or self.menu.open or self.exit_menu.open or self.state.animating()
                or self.state.static_shown > 0.001 or self.vod.animating()):
            self.video.update()

    # ------------------------------------------------------------------ tuning
    def tune(self, index: int):
        if not self.channels:
            return
        if self.vod.mode:
            self.vod.leave()                # back to cable (or off the On Demand channel)
        index %= len(self.channels)
        if index != self.index:
            self.prev_index = self.index
        self.index = index
        self.token += 1
        self.tuning = True
        self.tune_started = time.monotonic()
        ch = self.channels[index]

        self.state.static_level = 1.0
        self.state.message = ""
        self.state.message_sub = ""
        self.state.black = False
        self.state.guide = False
        self.state.bumper = None
        self.state.intermission = None
        self.menu.close()
        self.music_menu.close()
        self.state.channel_text = str(ch.number)
        self.state.channel_fade.show(3.0)
        self.state.banner_fade.hide()
        self.video.set_muted(True)
        self.hiss.start()
        self.state.pin_entry = None
        self.state.pin_pick = None
        self.state.pin_error = False
        self._tick_relock()
        self._apply_lock()
        self._save_state()
        self._load_now(self.token)

    def _load_now(self, token: int):
        if token != self.token:
            return
        ch = self.channels[self.index]
        self.seg_timer.stop()
        self.corrected = False
        self.state.bumper = None
        self.state.intermission = None

        if ch.network_type == GUIDE_TYPE:
            self.seg = None
            self.guide_data.start()
            self.state.guide = True
            self._play_guide_music()
            self._start_preview()
            self._finish_tuning(token)
            return
        self.guide_data.stop()
        self._stop_preview()

        if ch.network_type == ONDEMAND_TYPE:
            self.seg = None
            self.vod.start_promo()
            self._finish_tuning(token)
            return

        if ch.network_type in NO_SIGNAL_TYPES:
            self.video.stop()
            self.seg = None
            self.state.black = True
            self.state.message = ch.name.upper()
            self.state.message_sub = f"{ch.network_type} channels arrive in a later phase"
            self._finish_tuning(token)
            return

        when = dt.datetime.now() + dt.timedelta(seconds=LOAD_LEAD)
        try:
            seg = fs42.segment_at(ch.name, when)
            # don't bother loading a sliver that ends in a moment; take the next one
            if (seg.ends_at - when).total_seconds() < 0.3:
                seg = fs42.segment_at(ch.name, seg.ends_at + dt.timedelta(milliseconds=50))
        except fs42.NoSignal as e:
            _log.info("no signal on %s: %s", ch.name, e)
            self._off_air(token, "Updating the schedule - if this stays, open setup and Build")
            return

        # off-air filler comes in short chunks of the same looping clip: keep it running
        prev = self.seg
        if (not self.tuning and prev is not None and seg.content_type == "off_air"
                and prev.content_type == "off_air" and prev.path == seg.path):
            self.seg = seg
            self.seg_timer.start(max(50, int((seg.ends_at - dt.datetime.now()).total_seconds() * 1000)))
            return

        self.seg = seg
        self._setup_bumper(ch, seg)
        if seg.path.endswith("intermission.png"):
            self._intermission(ch, seg, token)
            return
        self.state.intermission = None
        self._apply_volume()                  # commercials quieter, before their first sound
        is_image = seg.media_type == "image" or seg.path.lower().endswith((".png", ".jpg", ".jpeg"))
        if is_image:
            self.video.load(seg.path)
        elif seg.content_type == "off_air":
            self.video.load(seg.path, start=seg.position_at(when), loop=True)
        else:
            self.video.load(seg.path, start=seg.position_at(when), end=seg.file_end)
        remaining = (seg.ends_at - dt.datetime.now()).total_seconds()
        self.seg_timer.start(max(50, int(remaining * 1000)))
        if is_image:
            self._finish_tuning(token)
        _log.info("ch %s: %s @ %.1fs (%s)", ch.number, seg.path.rsplit("/", 1)[-1], seg.position_at(when), seg.title)

    def _next_real(self, name: str, after: dt.datetime):
        nxt = fs42.programme_at(name, after + dt.timedelta(seconds=1))
        for _ in range(48):
            if nxt is None or not nxt.off_air:
                break
            nxt = fs42.programme_at(name, nxt.end + dt.timedelta(seconds=1))
        return nxt

    # ------------------------------------------------------------------ guide preview
    def _preview_channels(self) -> list:
        return [c for c in self.channels if c.network_type == "standard" and not self._is_locked(c)]

    def _start_preview(self) -> None:
        if not self.renderer.guide.preview_enabled or not self._preview_channels():
            return
        if self._preview_i is None:
            # begin with the channel you just came from
            prev = self.channels[self.prev_index] if self.prev_index is not None else None
            real = self._preview_channels()
            self._preview_i = real.index(prev) if prev in real else 0
            self._preview_i -= max(1, int(self.settings.get("guide_preview_step", 1)))
        self._next_preview()
        self.preview_timer.start(max(3, int(self.settings.get("guide_preview_seconds", 8))) * 1000)

    def _preview_sound(self) -> bool:
        return (self.state.guide and self.renderer.guide.preview_enabled
                and self.settings.get("guide_audio", "music") == "preview")

    def _apply_guide_audio(self) -> None:
        """On the guide: hear either the guide music or the preview channel."""
        on = self._preview_sound()
        if self.state.guide and not self.tuning:
            self.video.set_muted(on)
        pl = self.video.preview
        if pl is not None:
            try:
                pl["aid"] = "auto" if on else "no"
                pl.volume = float(self.settings.get("volume", 100))
                pl.mute = not on
            except Exception as e:
                _log.debug("preview audio: %s", e)

    def _stop_preview(self) -> None:
        if self.preview_timer.isActive() or self.video.preview is not None:
            self.preview_timer.stop()
            self.video.stop_preview()       # nothing decodes in the background
            self._preview_i = None
            self.renderer.guide.preview_info = None

    def _next_preview(self) -> None:
        real = self._preview_channels()
        if not real:
            return
        step = max(1, int(self.settings.get("guide_preview_step", 1)))
        self._preview_i = ((self._preview_i or 0) + step) % len(real)
        ch = real[self._preview_i]
        info = {"number": ch.number, "name": ch.name, "title": "", "sub": "", "since": time.monotonic(),
                "seconds": max(3, int(self.settings.get("guide_preview_seconds", 8)))}
        now = dt.datetime.now()
        try:
            seg = fs42.segment_at(ch.name, now + dt.timedelta(seconds=0.3))
        except fs42.NoSignal:
            seg = None
        if seg is None:
            info["nosignal"] = True
            self.video.stop_preview()
        else:
            prog = fs42.programme_at(ch.name, now)
            if seg.content_type in ("off_air", "sign_off") or (prog and prog.off_air):
                info["title"] = "Off Air"
            elif seg.path.endswith("intermission.png"):
                info["title"] = "Intermission"
            elif prog:
                code, ep = prog.episode()
                info["title"], info["sub"] = prog.title, "  ".join(x for x in (code, ep) if x)
            is_image = seg.path.lower().endswith((".png", ".jpg", ".jpeg"))
            try:
                self.video.load_preview(seg.path, None if is_image else seg.position_at(now + dt.timedelta(seconds=0.3)),
                                        None if is_image else seg.file_end)
            except Exception as e:
                _log.debug("preview load failed: %s", e)
                info["nosignal"] = True
        self._apply_guide_audio()
        self.renderer.guide.preview_info = info

    def _intermission(self, ch, seg, token: int) -> None:
        """Gap before a timed start: music + live countdown to the next programme,
        in one of several looks (intermissions.py), picked per gap."""
        from .intermissions import pick_style

        nxt = self._next_real(ch.name, seg.ends_at)
        info = {"channel": ch.number, "title": "", "episode": "", "start": "", "later": "", "upcoming": [],
                "ends_at": seg.ends_at, "started": seg.starts_at,
                "style": pick_style(seg.starts_at.timestamp() + ch.number), "movie": True}
        if nxt is not None:
            code, ep = nxt.episode()
            info.update(title=nxt.title, episode="  ·  ".join(x for x in (code, ep) if x),
                        start=nxt.start.strftime("%-I:%M %p"), movie=fs42._is_movie(nxt.path or ""))
            later = self._next_real(ch.name, nxt.end)
            if later is not None:
                info["later"] = f"{later.start.strftime('%-I:%M')}  {later.title}"
            # a few more for the text-page look
            p = later
            while p is not None and len(info["upcoming"]) < 3:
                info["upcoming"].append((p.start.strftime("%-I:%M %p"), p.title))
                p = self._next_real(ch.name, p.end)
        if info["movie"] and (seg.ends_at - seg.starts_at).total_seconds() > CARDS_BEFORE + 20:
            info["cards"] = None       # long gap before a film: film cards until the last 3 minutes

            def fetch(info=info, name=ch.name, number=ch.number, after=seg.ends_at):
                info["cards"] = self._film_cards(name, number, after)

            threading.Thread(target=fetch, name="film-cards", daemon=True).start()
        self.state.intermission = info
        # music: the guide's RPG tracks plus the rock / metal / punk / reggae / ska / jrpg beds,
        # one picked per gap (the same pick if you tune away and back)
        from .. import ads

        pool = [str(p) for p in assets_gen.guide_playlist()] + [str(p) for p in ads.intermission_beds()]
        if pool and (self.video.player.path or "") not in pool:
            pick = random.Random(int(seg.starts_at.timestamp()) + ch.number).choice(pool)
            self.video.load(pick, loop=True)
        remaining = (seg.ends_at - dt.datetime.now()).total_seconds()
        self.seg_timer.start(max(50, int(remaining * 1000)))
        self._finish_tuning(token)

    @staticmethod
    def _film_cards(name: str, number: int, after: dt.datetime) -> list[dict]:
        """Cards for the films coming up on this channel (the next feature first): a "coming up"
        card each (artwork, synopsis, when it's on) and a trivia card when the NFO has facts."""
        from .. import ads

        cards, seen = [], set()
        try:
            progs = fs42.programmes(name, after - dt.timedelta(seconds=30), after + dt.timedelta(hours=36), limit=60)
        except Exception as e:
            _log.debug("film cards: %s", e)
            return []
        today = dt.date.today()
        for k, prog in enumerate(p for p in progs if p.path and not p.off_air and fs42._is_movie(p.path)):
            if prog.path in seen or prog.start < after - dt.timedelta(minutes=1):
                continue
            seen.add(prog.path)
            try:
                film = ads.movie_card(prog.path)
            except Exception as e:
                _log.debug("film card for %s: %s", prog.path, e)
                continue
            day = prog.start.date()
            clock = prog.start.strftime("%-I:%M %p")
            if not cards:
                when = f"NEXT  ·  {clock}"
            elif day == today:
                when = f"{'TONIGHT' if prog.start.hour >= 17 else 'TODAY'} AT {clock}"
            elif day == today + dt.timedelta(days=1):
                when = f"TOMORROW AT {clock}"
            else:
                when = f"{prog.start.strftime('%A').upper()} AT {clock}"
            film.update(when=when, channel=number, title=film.get("title") or prog.title)
            cards.append({"kind": "film", **film})
            if film.get("facts"):
                fact = film["facts"][k % len(film["facts"])]
                cards.append({"kind": "film_fact", "fact": fact, **film})
            if len(seen) >= 6:
                break
        return cards

    def _setup_ad(self, ch, seg, what: tuple[str, str]) -> None:
        """A generated commercial, promo, channel ID or break sting: drawn live over its jingle."""
        from .. import ads

        kind, key = what
        info = {"kind": kind, "channel": ch.number}
        if kind == "ad":
            i = int(key) - 1
            if not 0 <= i < len(ads.ADS):
                self.state.bumper = None
                return
            info["ad"] = ads.ADS[i]
        elif kind == "card":
            data = ads.card_for(seg.path)
            ck = data.get("kind", "")
            if ck == "promo":
                kind, info["kind"] = "promo", "promo"
                info["promo"] = data
            elif ck == "trivia":
                info.update(data, kind="trivia", channel=ch.number)
            elif ck in ("spotlight", "stills", "wall"):
                info.update(data, kind=ck, channel=ch.number)
                if data.get("tag"):
                    kind = "promo"            # look up when the show is next on (below)
                    info["promo"] = data
            elif ck == "onthisday":
                heading, items = ads.on_this_day(seg.path)
                if items:
                    rnd = random.Random(seg.starts_at.timestamp())
                    info.update(kind="onthisday", heading=heading, items=rnd.sample(items, min(3, len(items))))
                else:
                    ck = "lineup"
            if ck == "lineup" or not ck:
                info.update(kind="lineup", progs=None, evening=seg.ends_at.hour >= 17)

                def fetch(info=info, name=ch.name, after=seg.ends_at):
                    out = []
                    try:
                        for prog in fs42.programmes(name, after, after + dt.timedelta(hours=6), limit=40):
                            if prog.off_air or prog.start < after - dt.timedelta(minutes=1):
                                continue
                            code, ep = prog.episode()
                            out.append((prog.start.strftime("%-I:%M %p"), prog.title,
                                        "  ·  ".join(x for x in (code, ep) if x)))
                            if len(out) >= 4:
                                break
                    except Exception:
                        pass
                    info["progs"] = out

                threading.Thread(target=fetch, daemon=True).start()
        if kind == "promo":
            if "promo" not in info:
                info["promo"] = ads.promo_for(seg.path)
            tag = info["promo"].get("tag", "")

            def find_when(info=info, name=ch.name, tag=tag, after=seg.ends_at):
                # the next time this show airs on the channel (looked up off the GUI thread)
                try:
                    for prog in fs42.programmes(name, after, after + dt.timedelta(hours=12), limit=80):
                        if prog.path and f"/{tag}/" in prog.path:
                            code, ep_title = prog.episode()
                            nfo_title, plot = ads.episode_details(prog.path)
                            info["next_ep"] = "  ·  ".join(x for x in (code, ep_title or nfo_title) if x)
                            info["next_plot"] = plot
                            day = "TONIGHT" if prog.start.date() == dt.date.today() and prog.start.hour >= 17 else (
                                "TODAY" if prog.start.date() == dt.date.today() else "TOMORROW")
                            info["when"] = f"{day} AT {prog.start.strftime('%-I:%M %p')}"
                            return
                    info["when"] = f"ONLY ON {paths.APP_NAME.upper()}"
                except Exception:
                    info["when"] = f"ONLY ON {paths.APP_NAME.upper()}"

            threading.Thread(target=find_when, daemon=True).start()
        elif kind in ("brb", "back"):
            prog = fs42.programme_at(ch.name, seg.starts_at)
            info["title"] = prog.title if prog else ""
            info["art"] = self._art_for(prog.path if prog else None)
            # only say "now back to" when the show really comes next (and "be right back"
            # when it really just stopped); otherwise a neutral "stay tuned"
            try:
                if kind == "back":
                    nxt = fs42.segment_at(ch.name, seg.ends_at + dt.timedelta(milliseconds=200))
                    if nxt.content_type != "feature":
                        info["kind"] = "stay"
                else:
                    prv = fs42.segment_at(ch.name, seg.starts_at - dt.timedelta(milliseconds=200))
                    if prv.content_type != "feature":
                        info["kind"] = "stay"
            except fs42.NoSignal:
                pass
        self.state.bumper = info
        self.state.bumper_start = seg.starts_at
        self.state.bumper_dur = max(1.0, (seg.ends_at - seg.starts_at).total_seconds())

    @staticmethod
    def _art_for(path: str | None) -> dict:
        from .. import artwork

        try:
            return artwork.for_programme(path) if path else {}
        except Exception as e:
            _log.debug("artwork lookup failed for %s: %s", path, e)
            return {}

    def _setup_bumper(self, ch, seg) -> None:
        from .. import ads

        what = ads.kind_of(seg.path)
        if what:
            self._setup_ad(ch, seg, what)
            return
        style = assets_gen.is_generated_upnext(seg.path)
        if not style:
            self.state.bumper = None
            return
        nxt = self._next_real(ch.name, seg.ends_at)
        info = {"style": style, "channel": ch.number, "title": "", "episode": "", "start": ""}
        art = self._art_for(nxt.path if nxt is not None else None)
        # about every other up-next uses the next show's own artwork, when it has some
        if (art.get("fanart") or art.get("logo")) and random.Random(seg.starts_at.timestamp()).random() < 0.5:
            info.update(style="art", art=art)
        if nxt is not None:
            code, ep = nxt.episode()
            info.update(title=nxt.title, episode="  ·  ".join(x for x in (code, ep) if x),
                        start=nxt.start.strftime("%-I:%M %p"))
        else:
            info.update(title="More great TV")
        self.state.bumper = info
        self.state.bumper_start = seg.starts_at
        self.state.bumper_dur = max(1.0, (seg.ends_at - seg.starts_at).total_seconds())

    def _off_air(self, token: int, why: str):
        self.seg = None
        self.video.load(str(assets_gen.runtime_path("bars_tone.mp4")), loop=True)
        self.state.message = ""
        self.state.banner_info = {"header": f"{self.channels[self.index].number}  {self.channels[self.index].name}",
                                  "title": "Off Air", "episode": why}
        self.state.banner_fade.show(6.0)
        # check again in a minute in case a schedule build finished
        self.seg_timer.start(60_000)

    def _segment_finished(self):
        # next show/bump/commercial on the same channel: no static
        self.tuning = False
        self._load_now(self.token)

    # ------------------------------------------------------------------ picture / zoom
    def _zoom_key(self) -> str:
        """The show (or film) on screen, as the zoom memory knows it: the same on every channel."""
        if self.vod.playing:
            return self.vod.zoom_key()
        seg = self.seg
        if seg is None or seg.content_type != "feature" or seg.media_type == "image":
            return ""
        name = re.sub(r"^\d{5} ", "", os.path.splitext(os.path.basename(seg.path))[0])
        m = re.match(r"^(.*) - S\d+E\d+", name, re.I)
        return (m.group(1) if m else name).strip()

    def _zoom_mode(self) -> str:
        key = self._zoom_key()
        return (self.settings.get("zoom_by_show") or {}).get(key, "normal") if key else "normal"

    def _set_zoom(self, mode: str) -> None:
        import math

        p = self.video.player
        values = {"panscan": 0.0, "video-zoom": 0.0, "keepaspect": True, "video-aspect-override": "no"}
        if mode == "zoom":
            values["panscan"] = 1.0
        elif mode == "cinema":                       # fill, then zoom a 2.35:1 picture past its bars
            values.update({"panscan": 1.0, "video-zoom": math.log2(2.35 / (16 / 9))})
        elif mode == "stretch":
            values["keepaspect"] = False
        elif mode in ("4:3", "16:9"):
            values["video-aspect-override"] = mode
        for k, v in values.items():
            try:
                p[k] = v
            except Exception as e:
                _log.debug("zoom %s=%s: %s", k, v, e)
        self._zoom_now = mode

    def _apply_zoom(self) -> None:
        """Each new file: the show's remembered picture mode; commercials, cards and bumpers play normally.
        Then the blurred sides, if they're on and the picture is narrower than the screen."""
        mode = self._zoom_mode()
        if mode != getattr(self, "_zoom_now", "normal"):
            self._set_zoom(mode)
        self._apply_sides()

    def _apply_sides(self) -> None:
        self._sides_vf = self._sides_filter() if self.settings.get("side_fill", "black") == "blur" else ""
        mode = getattr(self, "_zoom_now", "normal")
        if mode in ("4:3", "16:9"):           # the sides filter already makes the right shape
            try:
                self.video.player["video-aspect-override"] = "no" if self._sides_vf else mode
            except Exception:
                pass
        self._update_vf()

    def _sides_filter(self) -> str:
        """lavfi graph: the picture, sharp, in the middle of a screen-shaped frame whose sides are a
        blurred, darkened, stretched copy of it (cheap: blurred at 320x180, then scaled up)."""
        mode = getattr(self, "_zoom_now", "normal")
        if not self._zoom_key() or mode not in ("normal", "4:3", "16:9"):
            return ""                         # commercials/cards, or a mode that already fills the screen
        try:
            vp = self.video.player.video_params or {}
        except Exception:
            vp = {}
        w, h = vp.get("w") or 0, vp.get("h") or 0
        dw, dh = vp.get("dw") or w, vp.get("dh") or h
        if not (w and h and dw and dh):
            return ""
        aspect = {"4:3": 4 / 3, "16:9": 16 / 9}.get(mode, dw / dh)
        screen = self.video.width() / max(1, self.video.height())
        if aspect >= screen * 0.97:
            return ""                         # already fills the width: nothing to fill
        H = min(h, 1080) // 2 * 2
        W = int(H * screen) // 2 * 2
        fw = int(H * aspect) // 2 * 2
        return ("lavfi=[split=2[a][b];"
                f"[a]scale=320:180,boxblur=10:2,eq=brightness=-0.10:saturation=1.15,scale={W}:{H},setsar=1[bg];"
                f"[b]scale={fw}:{H},setsar=1[fg];[bg][fg]overlay=x={(W - fw) // 2}:y=0]")

    # ------------------------------------------------------------------ volume
    def _ad_playing(self) -> bool:
        return self.seg is not None and self.seg.content_type == "commercial"

    def _apply_volume(self) -> None:
        """The TV volume, or during commercials that share of it ("ad_volume" percent): real ads are
        mastered far louder than shows."""
        vol = float(self.settings.get("volume", 100))
        if self._ad_playing():
            vol *= max(0, min(100, int(self.settings.get("ad_volume", 50)))) / 100.0
        elif self.vod.mode:
            vol *= self.vod.volume_factor()   # On Demand's channel clips, browsing music and previews
        try:
            self.video.player.volume = vol
        except Exception as e:
            _log.debug("volume: %s", e)

    def _set_volume(self, key: str, value: int) -> None:
        """Remembered for next time, like the Settings sliders."""
        self.settings[key] = value
        try:
            s = settings.load()
            s[key] = value
            settings.save(s)
        except OSError:
            pass
        self._apply_volume()

    def _apply_track_prefs(self, ident=None) -> None:
        """Pick audio/subtitle tracks matching the preferred languages, once per loaded file.
        ident names the file for On Demand (there's no schedule segment then)."""
        seg = self.seg
        if ident is None:
            if seg is None:
                return
            ident = (seg.path, seg.starts_at)
        key = (self.token, ident)
        if key == self._prefs_for:
            return
        self._prefs_for = key
        try:
            tracks = self.video.player.track_list or []
        except Exception:
            return
        alang = (self.settings.get("audio_lang") or "").lower()
        slang = (self.settings.get("sub_lang") or "").lower()
        if alang:
            for t in tracks:
                if t.get("type") == "audio" and (t.get("lang") or "").lower() == alang:
                    self.video.player.aid = t["id"]
                    break
        if slang == "off":
            self.video.player.sid = "no"
        elif slang:
            subs = [t for t in tracks if t.get("type") == "sub" and (t.get("lang") or "").lower() == slang]
            subs.sort(key=lambda t: bool(t.get("forced")))      # full subtitles before forced-only
            if subs:
                self.video.player.sid = subs[0]["id"]

    def _play_guide_music(self, choice: str | None = None) -> None:
        playlist = [str(p) for p in assets_gen.guide_playlist(choice)]
        if not playlist:
            self.video.stop()
        elif len(playlist) == 1:
            self.video.load(playlist[0], loop=True)
        else:
            self.video.load_playlist(playlist, shuffle=True)
        self._guide_shuffling = len(playlist) > 1
        self.renderer.guide.now_playing = assets_gen.track_name(playlist[0]) if len(playlist) == 1 else ""
        self._np_tick = 29

    @staticmethod
    def _music_options():
        ready = {k for k, _n, _p in assets_gen.ready_tracks()}
        return [(k, n, (bool(ready) if k in assets_gen.SHUFFLES else k in ready)) for k, n in assets_gen.music_options()]

    def _open_music_menu(self) -> None:
        current = settings.load().get("guide_music", "shuffle")
        self.music_menu.show(self._music_options(), {"fantasy": "crystal"}.get(current, current),
                             self.settings.get("guide_audio", "music"), self.renderer.guide.preview_enabled)
        assets_gen.start_music_background()
        self.video.update()

    def _check_pending_music(self) -> None:
        """A track picked while still composing: switch to it once it's ready."""
        pending = getattr(self, "_pending_music", None)
        if self.music_menu.open:
            self.music_menu.refresh(self._music_options())
        if pending and assets_gen.track_ready(pending):
            self._pending_music = None
            if self.state.guide:
                self._play_guide_music(pending)

    def _music_menu_key(self, action: str | None) -> None:
        if action == "channel_up":
            self.music_menu.move(-1)
        elif action == "channel_down":
            self.music_menu.move(1)
        elif action in ("left", "right"):
            changed = self.music_menu.change(-1 if action == "left" else 1)
            if changed and changed[0] == "sound":
                s = settings.load()
                s["guide_audio"] = self.settings["guide_audio"] = changed[1]
                settings.save(s)
                self._apply_guide_audio()
            choice = changed[1] if changed and changed[0] == "music" else None
            if choice:
                s = settings.load()
                s["guide_music"] = choice
                settings.save(s)
                if choice in assets_gen.SHUFFLES or assets_gen.track_ready(choice):
                    self._pending_music = None
                    self._play_guide_music(choice)
                else:
                    # keep the current music; compose this one next and switch when it's done
                    self._pending_music = choice
                    assets_gen.request_track(choice)
        elif action in ("enter", "menu", "quit", "last_channel", "info"):
            self.music_menu.close()
        self.video.update()

    def _open_menu(self) -> None:
        if self.state.guide:
            self._open_music_menu()
            return
        seg = self.seg
        if not self.vod.playing and (seg is None or self.state.guide or seg.media_type == "image"):
            self.state.banner_info = {"header": "No audio or subtitle options here"}
            self.state.banner_fade.show(2.0)
            return
        try:
            tracks = list(self.video.player.track_list or [])
        except Exception:
            tracks = []
        self.menu.show(tracks, self._zoom_mode(), self.settings.get("side_fill", "black"),
                       int(self.settings.get("volume", 100)), int(self.settings.get("ad_volume", 50)),
                       extras=self._menu_extras())
        self.state.banner_fade.hide()
        self.video.update()

    def _menu_extras(self) -> tuple:
        """MENU's On Demand rows: "Watch On Demand" (on a channel) and the favourite switch. On a channel
        the title is looked up in the background (on a cable box the head-end answers)."""
        from .. import ondemand

        if self.vod.playing:
            it = self.vod.p["item"]
            self.menu.od = {"id": it["id"], "title": it["title"], "episode": ""}
            self.menu.od_fav = ondemand.is_favourite(it["id"])
            return ("favourite",)
        seg = self.seg
        if seg is None or seg.content_type != "feature" or self.state.locked or self.state.guide:
            return ()
        self.menu.od = None                  # looking it up
        path = seg.path

        def done(res):
            if not self.menu.open:
                return
            res = res or {}
            self.menu.od = res if res.get("id") else {}
            self.menu.od_fav = ondemand.is_favourite(res["id"]) if res.get("id") else False
            self.video.update()
        self.vod._run_bg(lambda: ondemand.find_item(path), done)
        return ("ondemand", "favourite")

    def _menu_action(self, row: str) -> None:
        from .. import ondemand

        od = self.menu.od
        if not od:
            self.state.banner_info = {"header": "Still looking…" if od is None else "Not in the On Demand library"}
            self.state.banner_fade.show(2.0)
            return
        if row == "ondemand":
            self.menu.close()
            self.vod.open_item(od["id"], od.get("episode", ""))
        elif row == "favourite":
            on = not ondemand.is_favourite(od["id"])
            ondemand.set_favourite(od["id"], on)
            self.menu.od_fav = on
            self.state.banner_info = {"header": ("ADDED TO FAVOURITES" if on else "REMOVED FROM FAVOURITES"),
                                      "title": od.get("title", "")}
            self.state.banner_fade.show(2.5)
        self.video.update()

    def _menu_change(self, d: int) -> None:
        res = self.menu.change(d)
        if not res:
            return
        kind, t = res
        p = self.video.player
        if kind in ("volume", "ad_volume"):
            self._set_volume(kind, int(t))
            self.video.update()
            return
        if kind == "sides":              # blurred sides for narrow pictures: one setting for everything
            s = settings.load()
            s["side_fill"] = self.settings["side_fill"] = t
            settings.save(s)
            self._apply_sides()
            self.video.update()
            return
        if kind == "picture":            # remembered for this show / film, on every channel
            key = self._zoom_key()
            if key:
                s = settings.load()
                zooms = dict(s.get("zoom_by_show") or {})
                if t == "normal":
                    zooms.pop(key, None)
                else:
                    zooms[key] = t
                s["zoom_by_show"] = self.settings["zoom_by_show"] = zooms
                settings.save(s)
            self._set_zoom(t)
            self._apply_sides()
            self.video.update()
            return
        if kind == "audio":
            p.aid = t["id"]
            self.settings["audio_lang"] = t.get("lang") or ""
        elif t is None:
            p.sid = "no"
            self.settings["sub_lang"] = "off"
        else:
            p.sid = t["id"]
            self.settings["sub_lang"] = t.get("lang") or ""
        try:
            s = settings.load()
            s["audio_lang"], s["sub_lang"] = self.settings["audio_lang"], self.settings["sub_lang"]
            settings.save(s)
        except OSError:
            pass
        self.video.update()

    def _menu_key(self, key: int, action: str | None) -> None:
        if action == "channel_up":
            self.menu.move(-1)
        elif action == "channel_down":
            self.menu.move(1)
        elif action == "left":
            self._menu_change(-1)
        elif action == "right":
            self._menu_change(1)
        elif action == "enter":
            row = self.menu.ROWS[self.menu.row]
            if row == "close":
                self.menu.close()
            elif row in ("ondemand", "favourite"):
                self._menu_action(row)
            else:
                self._menu_change(1)
        elif action in ("menu", "quit", "last_channel", "info"):
            self.menu.close()
        self.video.update()

    def _on_restart(self):
        if self.vod.mode:
            if self.vod.playing:
                self._apply_track_prefs(self.vod.track_key())
            self.vod.on_restart()
            if self.vod.mode in ("play", "promo") and not (self.vod.p or {}).get("grow"):
                self._apply_zoom()
            if self.tuning:
                self._finish_tuning(self.token)
            return
        self._apply_track_prefs()
        self._apply_zoom()
        token = self.token
        seg = self.seg
        if (seg is not None and not seg.is_stream and not self.corrected
                and self.state.intermission is None and not self.state.guide):
            expected = seg.position_at(dt.datetime.now())
            pos = self.video.position()
            if pos is not None and abs(pos - expected) > MAX_DRIFT and expected < seg.file_end - 1:
                _log.info("drift %.2fs - seeking to %.1f", pos - expected, expected)
                self.corrected = True
                self.video.seek_abs(expected + 0.15)
                return  # wait for the seek's own playback-restart
        if self.tuning:
            self._finish_tuning(token)

    def _finish_tuning(self, token: int):
        wait = MIN_STATIC - (time.monotonic() - self.tune_started)
        if wait > 0:
            QTimer.singleShot(int(wait * 1000), lambda: self._finish_tuning(token))
            return
        if token != self.token or not self.tuning:
            return
        self.tuning = False
        self.state.static_level = 0.0
        self.video.set_muted(self.state.locked)
        self.hiss.stop()
        if self.state.guide:
            self._apply_guide_audio()
        self._show_banner()

    def _watchdog(self):
        """Self-heal: if playback stalls or drifts badly from the schedule, reload."""
        seg = self.seg
        if (self.tuning or seg is None or seg.is_stream or seg.media_type == "image"
                or self.state.intermission is not None or self.state.guide):
            self._stalls = 0
            return
        now = dt.datetime.now()
        if (seg.ends_at - now).total_seconds() < 3:
            return
        pos, expected = self.video.position(), seg.position_at(now)
        if pos is None or abs(pos - expected) > 3.0:
            self._stalls += 1
            if self._stalls >= 2:
                _log.warning("playback off schedule (pos=%s expected=%.1f) - resyncing", pos, expected)
                self._stalls = 0
                self._load_now(self.token)
        else:
            self._stalls = 0

    def _on_ended(self, reason: str):
        if self.vod.mode:
            if not self._closing:
                self.vod.on_ended(reason)
            return
        if (reason == "error" and not self._closing and not self.state.guide
                and self.state.intermission is None):
            _log.warning("playback error on channel %s", self.channels[self.index].number if self.channels else "?")
            # stay on static until the schedule moves on
            self.state.static_level = 0.8
            self.hiss.start()

    def _show_banner(self):
        ch = self.channels[self.index]
        if (ch.network_type in (GUIDE_TYPE, ONDEMAND_TYPE) or self.vod.active or self.state.bumper is not None
                or self.state.intermission is not None):
            return
        now = dt.datetime.now()
        try:
            cur, nxt = fs42.now_and_next(ch.name, now)
        except Exception:
            cur, nxt = None, None
        info = {"header": f"{ch.number}  {ch.name}", "title": "", "episode": "", "next": ""}
        if (self.seg is not None and self.seg.content_type in ("off_air", "sign_off")) or (cur and cur.off_air):
            info["title"] = "Off Air"
            if nxt:
                info["episode"] = f"Back on the air at {nxt.start.strftime('%-I:%M %p')}"
        elif self.state.locked:
            info["title"] = "Restricted channel"
            info["episode"] = self.state.lock_note or "Press OK and enter the PIN to watch"
            nxt = None
        elif cur:
            info["title"] = cur.title
            code, ep_title = cur.episode()
            info["episode"] = "  ·  ".join(x for x in (code, ep_title) if x)
        if nxt:
            ncode, _ = nxt.episode()
            info["next"] = f"Next {nxt.start.strftime('%-I:%M')}  {nxt.title}" + (f"  {ncode}" if ncode else "")
        self.state.banner_lines = []
        self.state.banner_info = info
        self.state.banner_fade.show(5.0)

    # ------------------------------------------------------------------ input
    def keyPressEvent(self, ev):
        key = ev.key()
        if not self.exit_menu.open and self.vod.type_text(key, ev.text()):
            self._input_seen()
            self.video.update()
            return
        action = self.keys.action_for(key)
        if action is None and KeyMap.digit_for(key) is None:
            # helps set up IR remotes: the log names every button we don't use
            from PySide6.QtGui import QKeySequence

            _log.info("unassigned key: %r (code %s)", QKeySequence(key).toString(), key)
        if self._duplicate(action, "key"):
            return
        if not self._handle(action, key):
            super().keyPressEvent(ev)

    # ------------------------------------------------------------------ game controllers
    def _poll_gamepads(self) -> None:
        try:
            actions = self.gamepads.poll()
        except Exception as e:          # a controller must never take TV mode down
            _log.warning("game controller: %s", e)
            return
        if not actions or not self.isActiveWindow():
            return                      # drained either way; only act while we're in front
        for action in actions:
            if not self._duplicate(action, "pad"):
                self._handle(action, 0)

    def _duplicate(self, action: str | None, source: str) -> bool:
        """Steam Input can turn one controller press into a key press *and* a (virtual) controller
        press: drop the same action arriving from the other source within a moment."""
        now = time.monotonic()
        last = getattr(self, "_last_action", None)
        self._last_action = (action, source, now)
        return bool(action and last and last[0] == action and last[1] != source and now - last[2] < 0.15)

    def _handle(self, action: str | None, key: int) -> bool:
        """One button press from a keyboard, remote (key) or controller (key 0). False = not ours."""
        self._input_seen()
        if action == "guide":
            g = next((i for i, c in enumerate(self.channels) if c.network_type == GUIDE_TYPE), None)
            if self.exit_menu.open or self.menu.open or self.music_menu.open or self.state.pin_entry is not None:
                return True
            if g is not None and self.vod.active:
                self.vod.leave()
                self.tune(g)
            elif g is not None:
                self.tune(self.prev_index if g == self.index and self.prev_index is not None else g)
            return True
        if self.exit_menu.open:
            self._exit_menu_key(action)
            return
        if self.state.pin_entry is not None and self._pin_key(key, action):
            return True
        if action in ("back", "quit"):
            if self.vod.active and self.menu.open:
                self.menu.close()
                self.video.update()
                return True
            self.menu.close()
            self.music_menu.close()
            if self.vod.active and self.vod.back():
                self.video.update()
                return True
            self.exit_menu.show(on_demand=self.vod.active)
            self.video.update()
            return True
        if self.menu.open:
            self._menu_key(key, action)
            return True
        if self.music_menu.open:
            self._music_menu_key(action)
            return True
        digit = KeyMap.digit_for(key) if key else None
        if self.vod.active:
            if action == "last_channel":
                self._back_to_cable()
                return True
            if self.vod.key(action, digit):
                self.video.update()
                return True
        if digit is not None:
            self._digit(digit)
            return True
        if action is None:
            return False
        if action == "menu":
            self._open_menu()
            return True
        if action == "channel_up":
            self.tune(self.index + 1)
        elif action == "channel_down":
            self.tune(self.index - 1)
        elif action == "last_channel" and self.prev_index is not None:
            self.tune(self.prev_index)
        elif action == "enter" and self.state.locked and not self.digits:
            self._open_pin()
        elif action == "enter" and self.vod.mode == "promo" and not self.digits:
            self.vod.open_home(select=self.vod.promo_selected())
        elif action == "enter":
            self._commit_digits()
        elif action == "info" and self.channels:
            self._show_banner()
        elif action == "mute" and self._preview_sound() and self.video.preview is not None:
            self.video.preview.mute = not self.video.preview.mute
            self.state.banner_info = {"header": "MUTE" if self.video.preview.mute else "SOUND ON"}
            self.state.banner_fade.show(1.5)
        elif action == "mute" and self.state.locked:
            self._open_pin()
        elif action == "mute":
            self.video.player.mute = not self.video.player.mute
            self.state.banner_info = {"header": "MUTE" if self.video.player.mute else "SOUND ON"}
            self.state.banner_fade.show(1.5)
        elif action in ("volume_up", "volume_down"):
            vol = max(0, min(130, int(self.settings.get("volume", 100)) + (5 if action == "volume_up" else -5)))
            self._set_volume("volume", vol)
            note = f"  (ads {int(self.settings.get('ad_volume', 50))}%)" if self._ad_playing() else ""
            self.state.banner_info = {"header": f"VOLUME {int(vol)}  " + "|" * int(vol / 5) + note}
            self.state.banner_fade.show(1.5)
        elif action == "fullscreen":
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        return True

    # ------------------------------------------------------------------ line-up
    def _channel_list(self):
        chans = [c for c in fs42.channels()]
        real = [c for c in chans if c.network_type == "standard"]
        g = int(self.settings.get("guide_channel") or 0)
        if g and real and all(c.number != g for c in chans):
            chans.append(fs42.Channel(g, "TV Guide", GUIDE_TYPE))
        od = int(self.settings.get("ondemand_channel") or 0)
        if od and all(c.number != od for c in chans):      # a real channel on that number keeps it
            chans.append(fs42.Channel(od, "On Demand", ONDEMAND_TYPE))
        chans.sort(key=lambda c: c.number)
        return chans, real

    def _back_to_cable(self) -> None:
        """Leave On Demand: back to the channel you were on (not the On Demand channel itself)."""
        self.vod.leave()
        if not self.channels:
            self.video.stop()
            self.state.black = True
            self.state.message = "NO CHANNELS"
            self.state.message_sub = "Open setup and make a channel first"
            self.video.update()
            return
        idx = self.index
        if self.channels[idx].network_type == ONDEMAND_TYPE:
            prev = self.prev_index
            if prev is not None and prev < len(self.channels) and self.channels[prev].network_type != ONDEMAND_TYPE:
                idx = prev
            else:
                idx = next((i for i, c in enumerate(self.channels) if c.network_type == "standard"), idx)
        self.tune(idx)

    def _refresh_lineup(self) -> None:
        """Cable box: the head-end's channels changed (built, renamed, deleted): take the new line-up,
        staying on the same channel number when it still exists."""
        from .. import cablebox, model

        if not cablebox.refresh():
            return
        self.settings.update(cablebox.shared_settings())
        cur = self.channels[self.index].number if self.channels else None
        self.channels, real = self._channel_list()
        self.guide_data.channels = real
        self.restricted = {c.number: c for c in model.load() if c.restricted}
        _log.info("head-end line-up changed: %d channels", len(self.channels))
        if not self.channels:
            return
        nums = [c.number for c in self.channels]
        self.prev_index = None
        self.index = nums.index(cur) if cur in nums else 0
        if self.vod.active:
            return                          # browsing or playing On Demand: nothing to re-tune now
        self.state.banner_info = {"header": "CHANNEL LINE-UP UPDATED"}
        self.state.banner_fade.show(3.0)
        if cur not in nums:
            self.tune(self.index)           # the channel we were on is gone
        else:
            self._load_now(self.token)      # same channel: pick up its new schedule

    # ------------------------------------------------------------------ restricted channels
    # strong enough to hide details and text, light enough to tell roughly what's on
    BLUR_VF = "lavfi=[scale=200:-2:flags=area,boxblur=4:2]"

    def _is_locked(self, ch) -> bool:
        if self.unlocked or ch is None or ch.network_type == GUIDE_TYPE:
            return False
        rc = self.restricted.get(ch.number)
        return rc is not None and rc.locked_at(dt.datetime.now().hour)

    def _locked_numbers(self) -> set:
        if self.unlocked:
            return set()
        hour = dt.datetime.now().hour
        return {n for n, rc in self.restricted.items() if rc.locked_at(hour)}

    def _tick_relock(self) -> None:
        """Silent relock: after the PIN, restricted channels lock again once you've spent
        settings.relock_minutes in a row away from them. Watching one keeps them open."""
        if not self.unlocked or not self.channels:
            self.away_since = None
            return
        minutes = int(self.settings.get("relock_minutes") or 0)
        if (not self.vod.active and self.channels[self.index].number in self.restricted) or minutes <= 0:
            self.away_since = None
            return
        now = time.monotonic()
        if self.away_since is None:
            self.away_since = now
        elif now - self.away_since >= minutes * 60:
            _log.info("restricted channels locked again after %d min away", minutes)
            self.unlocked = False
            self.away_since = None

    def _check_lock_schedule(self) -> None:
        self._tick_relock()
        if self.vod.mode:
            return                          # On Demand hides restricted titles itself
        if not self.restricted or not self.channels or self.tuning:
            return
        if self._is_locked(self.channels[self.index]) != self.state.locked:
            _log.info("lock schedule: channel %s is now %s", self.channels[self.index].number,
                      "locked" if not self.state.locked else "open")
            if self.state.locked:
                self.state.pin_entry = None
            self._apply_lock()
            self._show_banner()

    def _apply_lock(self) -> None:
        """Blur and silence the current channel if it's restricted and still locked."""
        ch = self.channels[self.index] if self.channels else None
        locked = self._is_locked(ch)
        was = self.state.locked
        self.state.locked = locked
        rc = self.restricted.get(ch.number) if ch is not None else None
        self.state.lock_note = ""
        if locked and rc is not None and rc.lock_mode == "hours" and rc.free_from != rc.free_to:
            from .. import model

            self.state.lock_note = f"Opens by itself at {model.hour_text(rc.free_from)}"
        if locked == was and not locked:
            return
        pl = self.video.player
        self._update_vf()
        try:
            if locked:
                pl.mute = True
            elif not self.tuning:
                pl.mute = False
        except Exception as e:
            _log.warning("restricted channel sound: %s", e)
        self.video.update()

    def _update_vf(self) -> None:
        """The one place mpv's video filters are set: blurred sides (Settings: side_fill) and the
        restricted-channel blur. Filters need frames in memory, so hardware decoding pauses meanwhile."""
        chain = []
        if self.state.locked:
            chain.append(self.BLUR_VF)
        elif getattr(self, "_sides_vf", ""):
            chain.append(self._sides_vf)
        vf = ",".join(chain)
        pl = self.video.player
        if vf == getattr(self, "_vf_now", ""):
            return
        try:
            pl["hwdec"] = "no" if vf else str(self.settings.get("hwdec", "no"))
            pl["vf"] = vf
            self._vf_now = vf
        except Exception as e:
            _log.warning("video filter: %s", e)

    def _open_pin(self) -> None:
        self.state.pin_pick = None
        self.state.pin_entry = ""
        self.state.pin_error = False
        self.pin_timer.start(30_000)
        self.video.update()

    def _pin_timeout(self) -> None:
        self.state.pin_entry = None
        self.video.update()

    def _pin_key(self, key: int, action: str | None) -> bool:
        """Keys while the PIN pad is open. Returns False to let the key act normally."""
        digit = KeyMap.digit_for(key) if key else None
        self.pin_timer.start(30_000)
        pick = self.state.pin_pick
        if digit is not None:
            self.state.pin_error = False
            self.state.pin_pick = None
            if len(self.state.pin_entry or "") < 12:
                self.state.pin_entry = (self.state.pin_entry or "") + digit
        elif action in ("channel_up", "channel_down"):
            # no number buttons (game controller): Up/Down picks a digit, Right moves on, OK confirms
            self.state.pin_error = False
            step = 1 if action == "channel_up" else -1
            self.state.pin_pick = (0 if step == 1 else 9) if pick is None else (pick + step) % 10
        elif action == "right":
            if pick is not None and len(self.state.pin_entry or "") < 12:
                self.state.pin_entry = (self.state.pin_entry or "") + str(pick)
            self.state.pin_pick = None
        elif action in ("back", "quit", "left"):
            if action == "left" and pick is not None:
                self.state.pin_pick = None
            elif action == "left" and self.state.pin_entry:
                self.state.pin_entry = self.state.pin_entry[:-1]
            else:
                self.state.pin_entry = None
                self.state.pin_pick = None
        elif action == "enter":
            if pick is not None:
                self.state.pin_entry = (self.state.pin_entry or "") + str(pick)
                self.state.pin_pick = None
            if self.state.pin_entry == str(self.settings.get("restricted_pin") or "69"):
                _log.info("restricted channels unlocked for this session")
                self.unlocked = True
                self.state.pin_entry = None
                self.pin_timer.stop()
                self._apply_lock()
                self.away_since = None
                self.state.banner_info = {"header": "UNLOCKED", "title": "Restricted channels are open"}
                self.state.banner_fade.show(3.0)
            else:
                self.state.pin_entry = ""
                self.state.pin_error = True
        else:
            self.state.pin_entry = None      # e.g. info or menu: close the pad and carry on
            self.state.pin_pick = None
            self.video.update()
            return False
        self.video.update()
        return True

    def _exit_menu_key(self, action: str | None) -> None:
        if action in ("channel_up", "left"):
            self.exit_menu.move(-1)
        elif action in ("channel_down", "right"):
            self.exit_menu.move(1)
        elif action in ("back", "quit", "menu"):
            self.exit_menu.close()
        elif action == "enter":
            choice = self.exit_menu.choice()
            self.exit_menu.close()
            if choice == "ondemand":
                self.vod.open_home()
            elif choice == "cable":
                self._back_to_cable()
            elif choice != "tv":
                self.exit_choice = choice
                self.close()
        self.video.update()

    # ------------------------------------------------------------------ keep awake
    def _input_seen(self) -> None:
        self.last_input = time.monotonic()
        if self.settings.get("keep_awake", True) and not self.awake.active:
            self.awake.start()

    def _check_awake(self) -> None:
        hours = float(self.settings.get("keep_awake_hours") or 0)
        if hours and self.awake.active and time.monotonic() - self.last_input > hours * 3600:
            _log.info("no button pressed for %g hours: letting the computer sleep", hours)
            self.awake.stop()

    def _digit(self, d: str):
        if not self.channels:
            return
        maxlen = max(len(str(c.number)) for c in self.channels)
        self.digits = (self.digits + d)[-maxlen:]
        self.state.channel_text = self.digits + "-" * (maxlen - len(self.digits))
        self.state.channel_fade.show(DIGIT_TIMEOUT_MS / 1000 + 0.5)
        self.video.update()
        if len(self.digits) >= maxlen:
            self._commit_digits()
        else:
            self.digit_timer.start(DIGIT_TIMEOUT_MS)

    def _commit_digits(self):
        self.digit_timer.stop()
        if not self.digits:
            return
        number, self.digits = int(self.digits), ""
        idx = self._index_of(number)
        if idx is None:
            # no such channel: flash the number with dashes, stay put
            self.state.channel_text = f"{number}?"
            self.state.channel_fade.show(1.5)
        else:
            self.tune(idx)

    # ------------------------------------------------------------------ upkeep
    def _start_schedule_keeper(self):
        if self.cable_box:
            return                  # the head-end keeps the schedules
        self._start_schedule_keeper_local()

    def _start_schedule_keeper_local(self):
        """Keep schedules days ahead, in the background: extend ones running short and
        rebuild any that ran out while TV mode wasn't running (see upkeep.py)."""
        from .. import upkeep

        def keep():
            while True:
                try:
                    touched = upkeep.run()
                    if touched:
                        _log.info("schedules updated: %s", touched)
                except Exception as e:
                    _log.warning("schedule upkeep failed: %s", e)
                time.sleep(3600)

        threading.Thread(target=keep, name="schedule-keeper", daemon=True).start()

    def closeEvent(self, ev):
        self._closing = True
        self.vod.shutdown()
        self.preview_timer.stop()
        self.awake.stop()
        self.awake_timer.stop()
        self.pad_timer.stop()
        self.gamepads.close()
        self._save_state()
        self.anim_timer.stop()
        self.watchdog.stop()
        self.guide_data.stop()
        self.seg_timer.stop()
        self.hiss.shutdown()
        self.video.shutdown()
        super().closeEvent(ev)


def run_tv(windowed: bool = False, start_channel: int | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    from .. import cablebox

    cablebox.install()              # a cable box asks the head-end instead of its own channels
    assets_gen.start_music_background()
    app.setApplicationName(paths.APP_NAME)
    app.setDesktopFileName(paths.APP_SLUG)       # groups windows under the app-menu icon (Wayland app id)
    try:
        from PySide6.QtGui import QIcon

        from .. import desktop

        desktop.refresh_if_installed()
        if desktop.icon_file().exists():
            app.setWindowIcon(QIcon(str(desktop.icon_file())))
    except Exception:
        pass
    win = TVWindow(windowed=windowed, start_channel=start_channel)
    if windowed:
        win.resize(1280, 720)
        win.show()
    else:
        win.showFullScreen()
    from .. import steam
    from ..gui.main_window import _steam_art_later

    _steam_art_later()
    if steam.from_steam():
        # Big Picture stays in front unless we come forward ourselves (see steam.py)
        def _front():
            win.raise_()
            win.activateWindow()
        for ms in (300, 1200, 3000, 6000):
            QTimer.singleShot(ms, _front)
    rc = app.exec()
    if win.exit_choice == "desktop":
        return EXIT_TO_DESKTOP
    if win.exit_choice == "setup" and not os.environ.get("GETSOME_FROM_SETUP"):
        # started on its own (launch straight into TV, or "getsome tv"): open setup in its place
        os.chdir(paths.APP_DIR.parent)
        os.execv(sys.executable, [sys.executable, "-m", "getsome", "setup"])
    return rc
