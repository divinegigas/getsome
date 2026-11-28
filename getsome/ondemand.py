"""On Demand: every show and film in the library folders, to browse and play whenever you like.

Data only (no Qt); the screens are in tv/ondemand.py.

  catalog()          every show and film as plain dicts (JSON-safe, so a head-end can send them to a
                     cable box): title, year, genres, rating, plot, artwork paths, when it was added,
                     and which restricted channels it's on. Built from the setup window's library scans
                     (library.json / movies.json) plus each item's .nfo, cached in ondemand_catalog.json.
  show_detail(f)     a show's seasons and episodes (paths, codes, titles, plots, stills).
  History            where you stopped in each file, what you finished, and what's next per show
                     (ondemand_history.json, on this computer only; a cable box keeps its own).

Nothing is ever written next to the media.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from . import paths

_log = logging.getLogger("getsome.ondemand")

CATALOG_VERSION = 1
DONE_FRACTION = 0.93          # past this much of a file it counts as watched...
DONE_REMAINING = 150          # ...or with this many seconds (end credits) left
MIN_RESUME = 45               # less than this far in: start from the beginning next time
NEW_DAYS = 14                 # "NEW" badge for things added this recently


# ------------------------------------------------------------------ small NFO helpers

def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _tag(xml: str, tag: str) -> str:
    from .media_index import _tag as tag_, _unescape

    v = tag_(xml, tag)
    return _unescape(re.sub(r"<[^>]+>", "", v)).strip() if v else ""


def _year(xml: str, fallback_name: str = "") -> int | None:
    for v in (_tag(xml, "year"), _tag(xml, "premiered")[:4], _tag(xml, "aired")[:4]):
        if v.isdigit() and 1880 < int(v) < 2200:
            return int(v)
    m = re.search(r"\((\d{4})\)", fallback_name)
    return int(m.group(1)) if m else None


def _added(*paths_: Path) -> float:
    """When something arrived in the library: the newest of these files' or folders' times."""
    best = 0.0
    for p in paths_:
        try:
            st = p.stat()
            best = max(best, st.st_mtime, st.st_ctime)
        except OSError:
            pass
    return best


def _real(p: str) -> str:
    try:
        return os.path.realpath(p)
    except OSError:
        return p


# ------------------------------------------------------------------ restricted titles

def _restricted_map() -> dict[str, list[int]]:
    """Real path of every show folder / film on a restricted channel -> those channel numbers.
    Such titles stay hidden in On Demand until the PIN is entered (same PIN, same session)."""
    from . import model

    out: dict[str, list[int]] = {}
    try:
        chans = [c for c in model.load() if c.restricted]
    except Exception:
        return out
    for c in chans:
        items = [sh.folder for sh in c.all_show_refs()] + list(c.movies)
        for b in c.blocks:
            items += list(b.movies)
        for it in items:
            out.setdefault(_real(it), [])
            if c.number not in out[_real(it)]:
                out[_real(it)].append(c.number)
    return out


# ------------------------------------------------------------------ catalog

def _catalog_file() -> Path:
    return paths.data_dir() / "ondemand_catalog.json"


def _sources_key() -> list:
    """What the catalog was made from: the scan caches and the channel list (restrictions)."""
    key = [CATALOG_VERSION]
    for name in ("library.json", "movies.json", "channels.json"):
        try:
            key.append((paths.data_dir() / name).stat().st_mtime)
        except OSError:
            key.append(0)
    return key


def _show_item(s, locks: dict) -> dict:
    from . import artwork

    folder = Path(s.folder)
    xml = _read(folder / "tvshow.nfo")
    art = artwork.show_art(s.folder)
    seasons = []
    try:
        seasons = [d for d in folder.iterdir() if d.is_dir() and d.name.lower().startswith("season")]
    except OSError:
        pass
    return {
        "id": "s:" + s.folder, "kind": "show", "folder": s.folder, "title": s.title,
        "year": _year(xml, folder.name), "genres": list(s.genres or []), "rating": s.rating or "",
        "plot": _tag(xml, "plot") or _tag(xml, "outline"), "studio": _tag(xml, "studio"),
        "seasons": len(s.seasons), "episodes": s.episodes,
        "art": {k: art.get(k, "") for k in ("poster", "fanart", "logo", "landscape")},
        "added": _added(folder, *seasons), "lock": locks.get(_real(s.folder), []),
    }


def _movie_item(m, locks: dict) -> dict:
    from . import artwork
    from .media_index import _movie_nfo

    video = Path(m.path)
    nfo = _movie_nfo(video)
    xml = _read(nfo) if nfo else ""
    art = artwork.movie_art(m.path)
    return {
        "id": "m:" + m.path, "kind": "movie", "path": m.path, "title": m.title, "year": m.year,
        "genres": list(m.genres or []), "rating": m.rating or "", "runtime": m.runtime or 0,
        "plot": _tag(xml, "plot") or _tag(xml, "outline"), "tagline": _tag(xml, "tagline"),
        "art": {k: art.get(k, "") for k in ("poster", "fanart", "logo", "landscape")},
        "added": _added(video), "lock": locks.get(_real(m.path), []),
    }


_lock = threading.Lock()
_memo: tuple[list, dict] | None = None


def catalog() -> dict:
    """{"items": [...], "note": ""}: every show and film the setup window's library scans found.
    note explains an empty catalog (no folders yet, never scanned)."""
    global _memo
    from . import library

    key = _sources_key()
    with _lock:
        if _memo and _memo[0] == key:
            return _memo[1]
        try:
            data = json.loads(_catalog_file().read_text())
            if data.get("key") == key:
                _memo = (key, data["catalog"])
                return data["catalog"]
        except (OSError, ValueError, KeyError):
            pass
        shows, movies = library.cached(), library.cached_movies()
        note = ""
        if not library.folders() and not library.movie_folders():
            note = "Add your TV and movie folders in setup (Settings → Library) to fill On Demand."
        elif shows is None and movies is None:
            note = "Open setup and press Rescan in “Add shows” so On Demand can see the library."
        locks = _restricted_map()
        items: list[dict] = []
        t0 = time.monotonic()
        for s in shows or []:
            try:
                items.append(_show_item(s, locks))
            except Exception as e:          # one odd folder must not empty the catalog
                _log.warning("on demand: skipped show %s: %s", s.folder, e)
        for m in movies or []:
            try:
                items.append(_movie_item(m, locks))
            except Exception as e:
                _log.warning("on demand: skipped film %s: %s", m.path, e)
        cat = {"items": items, "note": note}
        _log.info("on demand catalog: %d titles in %.1fs", len(items), time.monotonic() - t0)
        try:
            tmp = _catalog_file().with_suffix(".part")
            tmp.write_text(json.dumps({"key": key, "catalog": cat}))
            tmp.replace(_catalog_file())
        except OSError as e:
            _log.debug("catalog cache: %s", e)
        _memo = (key, cat)
        return cat


@functools.lru_cache(maxsize=64)
def _show_detail(folder: str, stamp: float) -> dict:
    from . import media_index

    sh = media_index.scan_show(Path(folder))
    seasons: dict[int, list] = {}
    for e in sh.episodes:
        xml = _read(e.path.with_suffix(".nfo"))
        thumb = ""
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            f = e.path.with_name(e.path.stem + "-thumb" + ext)
            if f.is_file():
                thumb = str(f)
                break
        runtime = _tag(xml, "runtime")
        code = f"S{e.season:02d}E{e.episode:02d}" if e.season is not None else ""
        seasons.setdefault(e.season if e.season is not None else -1, []).append({
            "path": str(e.path), "code": code, "season": e.season, "episode": e.episode,
            "title": e.title or _tag(xml, "title") or ("" if code else e.path.stem),
            "plot": _tag(xml, "plot"), "aired": _tag(xml, "aired"), "thumb": thumb,
            "runtime": int(runtime) if runtime.isdigit() else 0,
        })
    return {"folder": folder, "title": sh.title,
            "seasons": [{"season": n, "name": ("Season %d" % n) if n > 0 else ("Specials" if n == 0 else "Other"),
                         "episodes": eps} for n, eps in sorted(seasons.items(), key=lambda kv: (kv[0] < 0, kv[0]))]}


def show_detail(folder: str) -> dict:
    """{"folder", "title", "seasons": [{"season", "name", "episodes": [{path, code, title, plot, thumb...}]}]}"""
    stamp = _added(Path(folder), *[d for d in Path(folder).glob("*") if d.is_dir()])
    return _show_detail(folder, stamp)


def episodes_in_order(detail: dict) -> list[dict]:
    return [e for s in detail.get("seasons", []) for e in s["episodes"]]


def is_new(item: dict, now: float | None = None) -> bool:
    return bool(item.get("added")) and (now or time.time()) - item["added"] < NEW_DAYS * 86400


def meta_line(item: dict) -> str:
    bits = [str(item["year"])] if item.get("year") else []
    if item.get("rating"):
        bits.append(item["rating"])
    if item["kind"] == "show":
        n = item.get("seasons") or 0
        bits.append(f"{n} season{'s' if n != 1 else ''}" if n else f"{item.get('episodes', 0)} episodes")
    elif item.get("runtime"):
        h, m = divmod(int(item["runtime"]), 60)
        bits.append(f"{h}h {m:02d}m" if h else f"{m}m")
    if item.get("genres"):
        bits.append(" / ".join(item["genres"][:3]))
    return "  ·  ".join(bits)


# ------------------------------------------------------------------ viewing history

def _history_file() -> Path:
    return paths.data_dir() / "ondemand_history.json"


class History:
    """Positions and finished files. Kept on this computer only; "Clear On Demand history" in
    Settings removes it."""

    def __init__(self):
        self.files: dict[str, dict] = {}      # path -> {pos, dur, t, done}
        self.items: dict[str, dict] = {}      # item id -> {t, last, next, next_label}
        self.hidden: list[str] = []           # removed from "Continue watching"
        try:
            data = json.loads(_history_file().read_text())
            self.files = dict(data.get("files") or {})
            self.items = dict(data.get("items") or {})
            self.hidden = list(data.get("hidden") or [])
        except (OSError, ValueError):
            pass
        self._dirty_at = 0.0

    def save(self) -> None:
        try:
            paths.ensure_dirs()
            tmp = _history_file().with_suffix(".part")
            tmp.write_text(json.dumps({"version": 1, "files": self.files, "items": self.items,
                                       "hidden": self.hidden}))
            tmp.replace(_history_file())
        except OSError as e:
            _log.warning("couldn't save on demand history: %s", e)

    # -- per file
    def resume_at(self, path: str) -> float:
        f = self.files.get(path) or {}
        return 0.0 if f.get("done") or f.get("pos", 0) < MIN_RESUME else float(f["pos"])

    def fraction(self, path: str) -> float:
        f = self.files.get(path) or {}
        if f.get("done"):
            return 1.0
        return max(0.0, min(1.0, f.get("pos", 0) / f["dur"])) if f.get("dur") else 0.0

    def done(self, path: str) -> bool:
        return bool((self.files.get(path) or {}).get("done"))

    def update(self, item_id: str, path: str, pos: float, dur: float, label: str = "",
               nxt: dict | None = None) -> bool:
        """Remember where we are; returns True when this just counted as finished."""
        if not dur or pos is None:
            return False
        f = self.files.setdefault(path, {})
        was = bool(f.get("done"))
        f.update(pos=round(float(pos), 1), dur=round(float(dur), 1), t=time.time())
        finished = pos >= dur * DONE_FRACTION or (dur > DONE_REMAINING * 4 and dur - pos <= DONE_REMAINING)
        if finished:
            f["done"] = True
        it = self.items.setdefault(item_id, {})
        it.update(t=time.time(), last=path, label_last=label)
        if nxt:
            it.update(next=nxt.get("path", ""), next_label=nxt.get("label", ""))
        elif finished:
            it.update(next="", next_label="")
        if item_id in self.hidden:
            self.hidden.remove(item_id)
        return finished and not was

    def set_done(self, item_id: str, path: str, done: bool) -> None:
        f = self.files.setdefault(path, {})
        f["done"] = done
        if not done:
            f["pos"] = 0
        f["t"] = time.time()
        if done and item_id.startswith("m:"):
            self.items.pop(item_id, None)     # a finished film leaves "Continue watching"
        self.save()

    def hide(self, item_id: str) -> None:
        if item_id not in self.hidden:
            self.hidden.append(item_id)
        self.save()

    def last_watched(self, item_id: str) -> float:
        return float((self.items.get(item_id) or {}).get("t", 0))

    def continue_list(self, by_id: dict[str, dict]) -> list[dict]:
        """Newest first: [{item, path, label, resume, fraction}] for shows with an episode part-watched
        or a next episode waiting, and films part-watched."""
        out = []
        for iid, it in sorted(self.items.items(), key=lambda kv: -kv[1].get("t", 0)):
            item = by_id.get(iid)
            if item is None or iid in self.hidden:
                continue
            last = it.get("last", "")
            if not self.done(last):
                path, label = last, it.get("label_last", "")
            elif item["kind"] == "show" and it.get("next"):
                path, label = it["next"], it.get("next_label", "")
            else:
                continue                      # finished film, or the last episode of the show
            if item["kind"] == "show" and not label:
                m = re.search(r"[Ss](\d{1,3})[ ._-]?[Ee](\d{1,4})", os.path.basename(path))
                label = f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}" if m else ""
            out.append({"item": item, "path": path, "label": label, "resume": self.resume_at(path),
                        "fraction": self.fraction(path)})
        return out


def clear_history() -> None:
    try:
        _history_file().unlink()
    except OSError:
        pass
