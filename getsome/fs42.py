"""The single place GetSome touches FieldStation42 internals.

FS42 stays unmodified in vendor/. If an upstream update moves things around,
this module is the only one that should need changes.

Two ways in:
  * in-process imports for read-only queries (what is on channel N right now?)
  * `station_42.py` subprocesses for anything that writes (catalog/schedule builds)
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import paths

MOVIES_TAG = "Movies"   # must match channel_writer.MOVIES_TAG (time blocks use "Movies_<id>")

_log = logging.getLogger("getsome.fs42")
_loaded = False


def init() -> None:
    """Point FS42 at our work dir and make its package importable.

    FS42 resolves all its paths relative to the current working directory, so we
    chdir into the work dir once at startup. GetSome itself only uses absolute paths.
    """
    global _loaded
    if _loaded:
        return
    paths.ensure_dirs()
    src = str(paths.fs42_source_dir())
    if src not in sys.path:
        sys.path.insert(0, src)
    os.chdir(paths.fs42_work_dir())
    _loaded = True


# --------------------------------------------------------------------------- channels


@dataclass(frozen=True)
class Channel:
    number: int
    name: str
    network_type: str
    hidden: bool = False


def channels(include_hidden: bool = False) -> list[Channel]:
    init()
    from fs42.station_manager import StationManager

    out = []
    for s in StationManager().stations:
        ch = Channel(int(s["channel_number"]), s["network_name"], s.get("network_type", "standard"), bool(s.get("hidden", False)))
        if include_hidden or not ch.hidden:
            out.append(ch)
    return sorted(out, key=lambda c: c.number)


def station_conf(name: str) -> dict | None:
    init()
    from fs42.station_manager import StationManager

    return StationManager().station_by_name(name)


# --------------------------------------------------------------------------- now playing


@dataclass
class Segment:
    """One contiguous piece of a file that should be on screen right now."""

    path: str
    file_pos: float          # where in the file "now" is
    file_end: float          # where in the file this segment stops
    starts_at: dt.datetime   # wall-clock time the segment began (file_pos == skip at that time)
    ends_at: dt.datetime     # wall-clock time the segment ends
    skip: float              # file position at starts_at
    title: str
    content_type: str = "feature"
    media_type: str = "video"
    is_stream: bool = False

    def position_at(self, when: dt.datetime) -> float:
        return self.skip + (when - self.starts_at).total_seconds()


class NoSignal(Exception):
    """Nothing is scheduled (off-air gap, schedule missing or expired)."""


def segment_at(name: str, when: dt.datetime) -> Segment:
    init()
    from fs42.play_point_query import PlayPointQuery, ScheduleQueryNotInBounds
    from fs42.liquid_manager import play_point_in_block

    q = PlayPointQuery()
    conf = station_conf(name)
    try:
        if conf and conf.get("network_type") == "streaming":
            pp = q.get_play_point(name, when)
            e = pp.plan[0]
            return Segment(e.path, 0, 0, when, when + dt.timedelta(seconds=e.duration), 0, pp.block_title, "stream", "video", True)
        block = q.get_programming_block(name, when)
    except ScheduleQueryNotInBounds as e:
        raise NoSignal(str(e)) from e
    except Exception as e:  # FS42 raises ScheduleNotFound (not a subclass) for missing schedules
        raise NoSignal(str(e)) from e
    if block is None:
        raise NoSignal(f"no programming on {name} at {when}")

    pp = play_point_in_block(block, when, conf or {})
    if pp is None or not pp.plan:
        raise NoSignal(f"no programming on {name} at {when}")

    # Work out when the current entry started on the wall clock.
    entry = pp.plan[pp.index]
    starts_at = when - dt.timedelta(seconds=pp.offset)
    duration = float(entry.duration) if entry.duration and entry.duration > 0 else (block.end_time - starts_at).total_seconds()
    return Segment(
        path=entry.path,
        file_pos=entry.skip + pp.offset,
        file_end=entry.skip + duration,
        starts_at=starts_at,
        ends_at=starts_at + dt.timedelta(seconds=duration),
        skip=float(entry.skip),
        title=pp.block_title or "",
        content_type=getattr(entry, "content_type", "feature"),
        media_type=getattr(entry, "media_type", "video"),
        is_stream=getattr(entry, "is_stream", False),
    )


@dataclass
class Programme:
    title: str
    start: dt.datetime
    end: dt.datetime
    path: str | None = None          # the main content file (a show-folder link)

    @property
    def off_air(self) -> bool:
        return self.title.replace(" ", "").lower() == "offair"

    def episode(self) -> tuple[str, str]:
        return episode_info(self.path) if self.path else ("", "")


def _content_path(block) -> str | None:
    c = getattr(block, "content", None)
    if isinstance(c, list):
        c = c[0] if c else None
    return getattr(c, "path", None)


def programme_at(name: str, when: dt.datetime) -> Programme | None:
    init()
    from fs42.play_point_query import PlayPointQuery

    try:
        b = PlayPointQuery().get_programming_block(name, when)
    except Exception:
        return None
    if b is None:
        return None
    path = _content_path(b)
    return Programme(display_title(path) or b.title or "", b.start_time, b.end_time, path)


_title_cache: dict[str, str] = {}
_LINK_TITLE = re.compile(r"^(.*) - S\d+E\d+", re.I)


ALT_TAG = "Alternating"  # must match channel_writer.ALT_TAG
_ALT_PREFIX = re.compile(r"^\d{5} ")


def _is_movie(path: str) -> bool:
    p = path.replace(os.sep, "/")
    if re.search(rf"/{MOVIES_TAG}(_[A-Za-z0-9]+)?/", p):
        return True
    # in an "every episode" folder, films are the entries without an SxxEyy code
    return bool(re.search(rf"/{ALT_TAG}(_[A-Za-z0-9]+)?/", p)) and not _CODE.search(os.path.basename(p))


def display_title(path: str | None) -> str:
    """The real title for a scheduled file (FS42's own title-cases things: "Bob'S Burgers")."""
    if not path:
        return ""
    if path in _title_cache:
        return _title_cache[path]
    title = ""
    name = _ALT_PREFIX.sub("", os.path.basename(path))
    if _is_movie(path):
        from . import media_index

        try:
            title = media_index.read_movie(Path(os.path.realpath(path))).title
        except OSError:
            title = ""
    else:
        m = _LINK_TITLE.match(os.path.splitext(name)[0])
        if m:
            title = m.group(1)
    _title_cache[path] = title
    return title


def movie_details(path: str) -> str:
    """"1985  ·  PG  ·  1h 56m  ·  Adventure" from the movie's NFO (or file name)."""
    from . import media_index

    try:
        mv = media_index.read_movie(Path(os.path.realpath(path)))
    except OSError:
        return ""
    bits = []
    if mv.year:
        bits.append(str(mv.year))
    if mv.rating:
        bits.append(mv.rating)
    if mv.runtime:
        h, m = divmod(mv.runtime, 60)
        bits.append(f"{h}h {m:02d}m" if h else f"{m}m")
    if mv.genres:
        bits.append(" / ".join(mv.genres[:2]))
    return "  ·  ".join(bits)


def programmes(name: str, start: dt.datetime, end: dt.datetime, limit: int = 40) -> list[Programme]:
    """Consecutive programme blocks overlapping [start, end)."""
    out, t = [], start
    while t < end and len(out) < limit:
        p = programme_at(name, t)
        if p is None:
            t += dt.timedelta(minutes=5)
            continue
        if not out or out[-1].start != p.start:
            out.append(p)
        t = p.end + dt.timedelta(seconds=1)
    return out


def now_and_next(name: str, when: dt.datetime) -> tuple[Programme | None, Programme | None]:
    """The current programme and the next real one (skipping off-air filler)."""
    cur = programme_at(name, when)
    if cur and cur.off_air:
        cur.title = "Off Air"
    nxt = programme_at(name, cur.end + dt.timedelta(seconds=1)) if cur else None
    for _ in range(48):  # look up to two days ahead for the next real programme
        if nxt is None or not nxt.off_air:
            break
        nxt = programme_at(name, nxt.end + dt.timedelta(seconds=1))
    return cur, nxt


_ep_cache: dict[str, tuple[str, str]] = {}
_CODE = re.compile(r" - (S\d+E\d+)", re.I)


def episode_info(path: str) -> tuple[str, str]:
    """("S01E05", "Episode title") for a scheduled show-folder link; ("", "") if unknown."""
    if path in _ep_cache:
        return _ep_cache[path]
    if _is_movie(path):
        _ep_cache[path] = ("", movie_details(path))
        return _ep_cache[path]
    code, title = "", ""
    m = _CODE.search(os.path.basename(path))
    if m and not m.group(1).upper().startswith(("S99", "S999")):
        code = m.group(1).upper()
    try:
        real = os.path.realpath(path)
        nfo = os.path.splitext(real)[0] + ".nfo"
        if os.path.exists(nfo):
            with open(nfo, errors="replace") as f:
                xml = f.read()
            t = re.search(r"<title>\s*(.*?)\s*</title>", xml, re.S | re.I)
            if t:
                title = (t.group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                         .replace("&quot;", '"').replace("&apos;", "'"))
    except OSError:
        pass
    if not code and not title:
        # no NFO and no SxxEyy: show the cleaned-up file name (home videos, old tapes...)
        stem = os.path.splitext(os.path.basename(os.path.realpath(path)))[0]
        title = re.sub(r"\s+", " ", re.sub(r"[._]+", " ", stem)).strip()
    _ep_cache[path] = (code, title)
    return code, title


def schedule_end(name: str) -> dt.datetime | None:
    init()
    from fs42.play_point_query import PlayPointQuery

    try:
        return PlayPointQuery().get_extents(name)[1]
    except Exception:
        return None


# --------------------------------------------------------------------------- builds


def run_station42(args: Iterable[str], on_line: Callable[[str], None] | None = None) -> int:
    """Run FS42's CLI in our work dir. Returns the exit code."""
    init()
    cmd = [sys.executable, str(paths.fs42_source_dir() / "station_42.py"), *args]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(paths.fs42_source_dir()) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    # plain, unwrapped log lines from FS42's rich console
    env.update({"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"})
    _log.info("running %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=paths.fs42_work_dir(), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    for line in proc.stdout:
        if on_line:
            on_line(ansi.sub("", line.rstrip("\n")))
    return proc.wait()


def rebuild_all(on_line: Callable[[str], None] | None = None, names: list[str] | None = None) -> int:
    """Catalog rescan + fresh schedule (one week ahead) for the named channels, or all.

    Chapter-marker scanning is skipped: it reads every file a second time and is
    only needed for commercial breaks (phase 6). Episode lengths are cached by
    FS42, so only new files are probed on later builds.
    """
    names = list(names or [])
    for args in (["-r", *names, "--skip_chapter_scan"], ["-x", *names], ["-w", *names]):
        rc = run_station42(args, on_line)
        if rc != 0:
            return rc
    return 0


def extend_schedules(names: list[str] | None = None, on_line=None) -> int:
    return run_station42(["-w", *(names or [])], on_line)


# --------------------------------------------------------------------------- break points
# FS42 cuts commercial breaks at chapter markers, else at detected fades to black. For a
# file with neither it would split the episode evenly, i.e. at arbitrary moments, which
# GetSome never does. So such files get one whole-file "chapter" marked END_ONLY: FS42
# then plays the episode uninterrupted with every commercial at the end. This writes data
# through FS42's own statements; no FS42 code is changed.

END_ONLY = "getsome_end_only"


def catalog_files(name: str) -> list[tuple[str, float]]:
    """(realpath, duration) of every feature in a channel's FS42 catalog."""
    init()
    from fs42.catalog_api import CatalogAPI

    conf = station_conf(name)
    if not conf:
        return []
    out = []
    for e in CatalogAPI.get_entries(conf):
        if getattr(e, "content_type", "feature") not in ("feature", None):
            continue
        rp = getattr(e, "realpath", None) or os.path.realpath(e.path)
        out.append((rp, float(e.duration or 0)))
    return out


def _fluid_db():
    import sqlite3

    from fs42.fluid_builder import FluidBuilder

    return sqlite3.connect(FluidBuilder().db_path)


def break_kind(realpath: str) -> str:
    """"chapters", "black", "end_only" or "" (nothing known yet)."""
    init()
    from fs42.fluid_statements import FluidStatements

    con = _fluid_db()
    try:
        ch = FluidStatements.get_chapter_points(con, realpath)
        if ch:
            return "end_only" if any(isinstance(c, dict) and c.get(END_ONLY) for c in ch) else "chapters"
        return "black" if FluidStatements.get_break_points(con, realpath) else ""
    finally:
        con.close()


def mark_end_only(realpath: str, duration: float) -> None:
    init()
    from fs42.fluid_statements import FluidStatements

    con = _fluid_db()
    try:
        FluidStatements.add_chapter_points(con, realpath, [
            {"chapter_start": 0.0, "chapter_end": float(duration), END_ONLY: True}])
    finally:
        con.close()


def set_break_points(realpath: str, times: list[float], duration: float) -> None:
    """Store act breaks (seconds) as FS42 break points, in the segment format its own fade-to-black
    scan writes (MediaProcessor.black_detect), and drop our end-only marker so FS42 uses them."""
    init()
    from fs42.fluid_statements import FluidStatements
    from fs42.media_processor import MediaProcessor

    segs, prev = [], 0.0
    for t in sorted(times):
        segs.append({"chapter_start": prev, "chapter_end": float(t)})
        prev = float(t)
    segs.append({"chapter_start": prev, "chapter_end": float(duration)})
    segs = MediaProcessor.calc_black_segments(segs, float(duration))
    con = _fluid_db()
    try:
        ch = FluidStatements.get_chapter_points(con, realpath)
        if ch and any(isinstance(c, dict) and c.get(END_ONLY) for c in ch):
            FluidStatements.delete_chapter_points(con, realpath)
        FluidStatements.add_break_points(con, realpath, segs)
    finally:
        con.close()


def clear_end_only(realpaths: list[str]) -> None:
    """Forget our END_ONLY markers (before a fade-to-black scan, which FS42 only uses
    when a file has no chapters)."""
    init()
    from fs42.fluid_statements import FluidStatements

    con = _fluid_db()
    try:
        for rp in realpaths:
            ch = FluidStatements.get_chapter_points(con, rp)
            if ch and any(isinstance(c, dict) and c.get(END_ONLY) for c in ch):
                FluidStatements.delete_chapter_points(con, rp)
    finally:
        con.close()
