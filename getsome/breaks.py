"""Where commercial breaks go inside an episode that has no chapter markers.

Default (instant, no scanning): `typical(duration)`, the usual act-break positions for an episode of
that length, the way TV was cut (cold open, middle, tag; teaser + acts for hour dramas). Fixed
positions from the running time, not random ones. The owner chose this as the default (scanning a
whole library takes hours the first time).

Optional per channel ("find the real act breaks", Channel.black_detect): `find()` below.

An act break is a fade to black *and* a moment of silence. Decoding only the audio to find the
silences is very fast; each silence is then confirmed with a ~3 s look at the picture around it.
Only silent-and-dark moments count, so breaks are never placed at random. Results go into
FieldStation42's own break-point table (fs42.set_break_points) and are cached per file
(breaks_cache.json), so each episode is only ever scanned once.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

from . import paths

_log = logging.getLogger("getsome.breaks")
VERSION = 2            # 2: entries remember "typical" or "detected"
EDGE = 60.0            # no breaks in the first or last minute
MIN_GAP = 90.0         # breaks at least this far apart
MAX_CANDIDATES = 14    # silences to check per file


def _cache_file() -> Path:
    return paths.data_dir() / "breaks_cache.json"


def _load() -> dict:
    try:
        data = json.loads(_cache_file().read_text())
        return data if data.get("version") == VERSION else {"version": VERSION, "files": {}}
    except (OSError, ValueError):
        return {"version": VERSION, "files": {}}


def _ffmpeg() -> str:
    from .assets_gen import _ffmpeg

    return _ffmpeg()


def silences(path: str, noise_db: int = -45, min_s: float = 0.35) -> list[tuple[float, float]]:
    """(start, end) of every silence, from the audio alone."""
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-nostats", "-vn", "-sn", "-dn", "-i", path,
                        "-af", f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    out, start = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    return out


def dark_near(path: str, at: float, window: float = 3.0) -> float | None:
    """Middle of a black stretch within `window` seconds around `at`, else None."""
    ss = max(0.0, at - window / 2)
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-nostats", "-ss", f"{ss:.2f}", "-t", f"{window:.2f}", "-i", path,
                        "-an", "-sn", "-dn", "-vf", "scale=96:-2,blackdetect=d=0.08:pix_th=0.12:pic_th=0.92",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    for line in r.stderr.splitlines():
        m = re.search(r"black_start:([\d.]+) black_end:([\d.]+)", line)
        if m:
            return ss + (float(m.group(1)) + float(m.group(2))) / 2
    return None


def find(path: str, duration: float) -> list[float]:
    """Act-break times (seconds) for one file: silent *and* dark, away from the ends, spaced out."""
    if duration < 2 * EDGE + MIN_GAP:
        return []
    cands = [(e - s, (s + e) / 2) for s, e in silences(path) if EDGE < (s + e) / 2 < duration - EDGE]
    # the longest pauses first: act breaks usually have the longest silences
    cands = sorted(cands, reverse=True)[:MAX_CANDIDATES]
    found: list[float] = []
    for _len, mid in sorted(cands, key=lambda c: c[1]):
        t = dark_near(path, mid)
        if t is not None and all(abs(t - f) >= MIN_GAP for f in found):
            found.append(t)
    return sorted(found)


def known(realpath: str) -> bool:
    """Whether break points for this file came from us (typical or detected)."""
    return realpath in _load()["files"]


def typical(duration: float) -> list[float]:
    """The usual act-break positions for an episode of this length (seconds)."""
    m = duration / 60
    if m < 8:
        return []                                   # shorts: ads after
    if m < 16:
        fr = [0.5]                                  # ~11-min cartoon: one break in the middle
    elif m < 35:
        fr = [0.12, 0.52, 0.88]                     # ~22-min sitcom: cold open | act 1 | act 2 | tag
    elif m < 56:
        fr = [0.09, 0.28, 0.48, 0.68, 0.87]         # ~42-min drama: teaser + acts
    else:
        n = max(3, round(m / 15))                   # longer (films on a TV channel): every ~15 min
        fr = [k / (n + 1) for k in range(1, n + 1)]
    return [round(duration * f, 2) for f in fr]


def plan(files: list[tuple[str, float]], detect: bool, progress=None) -> dict[str, tuple[list[float], str]]:
    """Breaks to store for each (realpath, duration) that needs them: {realpath: (times, mode)}, mode
    "typical" or "detected". Already-stored results are skipped (detected ones are never downgraded).
    With detect=False this is instant; with detect=True each file is scanned once (cached)."""
    cache = _load()
    out: dict[str, tuple[list[float], str]] = {}
    todo = []
    for rp, dur in files:
        try:
            st = os.stat(rp)
        except OSError:
            continue
        key = f"{st.st_size}:{int(st.st_mtime)}"
        hit = cache["files"].get(rp)
        fresh = hit and hit.get("key") == key
        if fresh and (hit.get("mode") == "detected" or not detect):
            continue                                # stored already (and good enough)
        if detect:
            todo.append((rp, dur, key))
        else:
            out[rp] = (typical(dur), "typical")
            cache["files"][rp] = {"key": key, "breaks": out[rp][0], "mode": "typical"}
    for i, (rp, dur, key) in enumerate(todo):
        if progress:
            progress(i, len(todo), Path(rp).name)
        try:
            pts = find(rp, dur)
        except (OSError, subprocess.SubprocessError) as e:
            _log.warning("couldn't scan %s for act breaks: %s", rp, e)
            pts = []
        # nothing found: fall back to the typical positions (still marked detected: don't scan again)
        out[rp] = (pts or typical(dur), "detected")
        cache["files"][rp] = {"key": key, "breaks": out[rp][0], "mode": "detected"}
        if i % 10 == 9:                  # keep progress if the build is interrupted
            _cache_file().write_text(json.dumps(cache))
    if out:
        _cache_file().write_text(json.dumps(cache))
    return out
