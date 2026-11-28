"""On Demand: every show and film in the library folders, to browse and play whenever you like.

Data only (no Qt); the screens are in tv/ondemand.py.

  catalog()          every show and film as plain dicts (JSON-safe, so a head-end can send them to a
                     cable box): title, year, genres, rating, plot, artwork paths, when it was added,
                     and which restricted channels it's on. Built from the setup window's library scans
                     (library.json / movies.json) plus each item's .nfo, cached in ondemand_catalog.json.
  show_detail(f)     a show's seasons and episodes (paths, codes, titles, plots, stills).
  History            where you stopped in each file, what you finished, and what's next per show
                     (ondemand_history.json, on this computer only; a cable box keeps its own).
  favourites         titles marked as favourites (ondemand_favourites.json, per computer too).
  find_item(path)    the title (and episode) a channel is playing, for "Watch On Demand" in the MENU.
  generated art      titles with no poster (or backdrop) get one made from a frame of the video with the
                     title set over it (ondemand_art/), in the background, so the library looks consistent.

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
            return _with_art(_memo[1])
        try:
            data = json.loads(_catalog_file().read_text())
            if data.get("key") == key:
                _memo = (key, data["catalog"])
                return _with_art(data["catalog"])
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
        return _with_art(cat)


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
    """{"folder", "title", "seasons": [{"season", "name", "episodes": [{path, code, title, plot, thumb...}]}],
    "thumbs_pending": n}. Episodes without a still of their own get one made from a frame of the episode
    (in the background; asking again later picks them up)."""
    stamp = _added(Path(folder), *[d for d in Path(folder).glob("*") if d.is_dir()])
    detail = _show_detail(folder, stamp)
    missing = []
    seasons = []
    for sn in detail["seasons"]:
        eps = []
        for e in sn["episodes"]:
            if not e["thumb"]:
                f = _ep_thumb_file(e["path"])
                if f.exists():
                    e = {**e, "thumb": str(f), "made": True}
                elif e["path"] not in _thumb_failed:
                    missing.append(e["path"])
            eps.append(e)
        seasons.append({**sn, "episodes": eps})
    if missing:
        _queue_thumbs(missing)
    return {**detail, "seasons": seasons, "thumbs_pending": len(missing)}


# ------------------------------------------------------------------ episode stills made from the episode

THUMB_VERSION = 1
_thumb_q: list[list[str]] = []           # batches, newest (the show being looked at) first
_thumb_cv = threading.Condition()
_thumb_worker = False
_thumb_failed: set[str] = set()
_thumb_queued: set[str] = set()


def _ep_thumb_file(path: str) -> Path:
    import hashlib

    return _art_dir() / "episodes" / (hashlib.sha1(f"{THUMB_VERSION}|{path}".encode()).hexdigest()[:24] + ".jpg")


def _queue_thumbs(paths_: list[str]) -> None:
    global _thumb_worker
    new = [x for x in paths_ if x not in _thumb_queued]
    with _thumb_cv:
        if new:
            _thumb_queued.update(new)
            _thumb_q.insert(0, new)
        else:                               # already waiting: move this show to the front
            for i, batch in enumerate(_thumb_q):
                if batch and batch[0] in paths_:
                    _thumb_q.insert(0, _thumb_q.pop(i))
                    break
        _thumb_cv.notify()
        if not _thumb_worker:
            _thumb_worker = True
            threading.Thread(target=_thumb_loop, name="ondemand-stills", daemon=True).start()


def _thumb_loop() -> None:
    while True:
        with _thumb_cv:
            while not any(_thumb_q):
                _thumb_q.clear()
                _thumb_cv.wait()
            batch = next(b for b in _thumb_q if b)
            path = batch.pop(0)
        try:
            if not make_episode_thumb(path):
                _thumb_failed.add(path)
        except Exception as e:
            _log.debug("still for %s: %s", path, e)
            _thumb_failed.add(path)
        finally:
            _thumb_queued.discard(path)


def make_episode_thumb(path: str) -> bool:
    """A 16:9 still from 35 % into the episode (past the opening titles), 480 px wide."""
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    out = _ep_thumb_file(path)
    if out.exists():
        return True
    if not ffmpeg or not os.path.exists(path):
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = _duration(path)
    tmp = out.with_suffix(".part.jpg")
    for at in ([dur * 0.35, dur * 0.2] if dur else []) + [120.0, 10.0, 0.0]:
        cmd = ["nice", "-n", "15"] if shutil.which("nice") else []
        cmd += [ffmpeg, "-loglevel", "error", "-y", "-ss", f"{at:.1f}", "-i", path, "-frames:v", "1",
                "-vf", "scale=480:-2", "-q:v", "4", str(tmp)]
        try:
            subprocess.run(cmd, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
        if tmp.exists() and tmp.stat().st_size > 1500:
            os.replace(tmp, out)
            return True
    return False


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


# ------------------------------------------------------------------ made-up covers

ART_VERSION = 2


def _art_dir() -> Path:
    return paths.data_dir() / "ondemand_art"


def _art_key(item: dict) -> str:
    import hashlib

    return hashlib.sha1(f"{ART_VERSION}|{item['id']}".encode()).hexdigest()[:20]


def _needs(item: dict) -> list[str]:
    art = item.get("art") or {}
    return [k for k in ("poster", "fanart") if not art.get(k) and not (k == "fanart" and art.get("landscape"))]


_gen_lock = threading.Lock()
_gen_running = False
_gen_failed: set[str] = set()


def _with_art(cat: dict) -> dict:
    """The catalog with made-up covers filled in where they exist; starts making the missing ones."""
    todo = []
    items = []
    for it in cat.get("items", []):
        need = _needs(it)
        if not need:
            items.append(it)
            continue
        it = {**it, "art": dict(it.get("art") or {})}
        k = _art_key(it)
        for kind in need:
            f = _art_dir() / f"{k}-{kind}.jpg"
            if f.exists():
                it["art"][kind] = str(f)
                if kind == "fanart" and not it["art"].get("landscape"):
                    it["art"]["landscape"] = str(f)
        if _needs(it) and it["id"] not in _gen_failed:
            todo.append(it)
        items.append(it)
    if todo:
        _start_art(todo)
    return {**cat, "items": items, "art_pending": len(todo)}


def _start_art(todo: list[dict]) -> None:
    global _gen_running
    with _gen_lock:
        if _gen_running:
            return
        _gen_running = True

    def run():
        global _gen_running
        try:
            for it in todo:
                try:
                    if not make_art(it):
                        _gen_failed.add(it["id"])
                except Exception as e:
                    _log.warning("cover for %s: %s", it.get("title"), e)
                    _gen_failed.add(it["id"])
        finally:
            with _gen_lock:
                _gen_running = False
    threading.Thread(target=run, name="ondemand-art", daemon=True).start()


def _sample_video(item: dict) -> str:
    if item["kind"] == "movie":
        return item["path"]
    from . import media_index

    eps = media_index.scan_show(Path(item["folder"])).episodes
    return str(eps[min(1, len(eps) - 1)].path) if eps else ""


def _duration(video: str) -> float:
    import shutil
    import subprocess

    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _wrap_title(title: str, size: int, width: int, font_w: float) -> list[str]:
    per = max(4, int(width / (size * font_w)))
    lines, cur = [], ""
    for word in title.split():
        trial = (cur + " " + word).strip()
        if len(trial) > per and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def make_art(item: dict) -> bool:
    """A frame from the video (30 % in) as the backdrop, and a 600x900 poster cut from it with the
    title set in the edition's font. Written next to nothing but our own data folder."""
    import shutil
    import subprocess
    import tempfile

    from . import brand
    from .assets_gen import _esc

    ffmpeg = shutil.which("ffmpeg")
    video = _sample_video(item)
    if not ffmpeg or not video or not os.path.exists(video):
        return False
    _art_dir().mkdir(parents=True, exist_ok=True)
    k = _art_key(item)
    need = _needs(item)
    dur = _duration(video)
    frame = _art_dir() / f"{k}-frame.jpg"
    for at in ([dur * 0.3, dur * 0.12] if dur else []) + [60.0, 5.0, 0.0]:
        cmd = ["nice", "-n", "15"] if shutil.which("nice") else []
        cmd += [ffmpeg, "-loglevel", "error", "-y", "-ss", f"{at:.1f}", "-i", video, "-frames:v", "1",
                "-vf", "scale=1280:-2", "-q:v", "3", str(frame)]
        try:
            subprocess.run(cmd, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
        if frame.exists() and frame.stat().st_size > 2000:
            break
    if not frame.exists():
        return False
    if "fanart" in need:
        shutil.copy(frame, _art_dir() / f"{k}-fanart.part.jpg")
        os.replace(_art_dir() / f"{k}-fanart.part.jpg", _art_dir() / f"{k}-fanart.jpg")
    if "poster" in need:
        font = paths.FONTS_DIR / brand.FONTS["text"]
        accent = brand.hexcolor("#ff2bd6").replace("#", "0x")
        second = brand.hexcolor("#00f0ff").replace("#", "0x")
        title = item.get("title") or "?"
        font_w = 0.50 if paths.XCABLE else 0.47
        for size in (92, 80, 70, 60, 52, 46):
            lines = _wrap_title(title.upper() if not paths.XCABLE else title, size, 520, font_w)
            if len(lines) <= 3 and all(len(x) * size * font_w <= 540 for x in lines):
                break
        tmp = Path(tempfile.mkdtemp(prefix="nc42art"))
        try:
            vf = ["scale=-2:900", "crop=600:900", "eq=brightness=-0.05:saturation=1.15"]
            for i in range(30):                     # a smooth dark fade over the lower half, for the title
                vf.append(f"drawbox=x=0:y={420 + i * 16}:w=600:h=16:color=black@{0.02 + i * 0.027:.3f}:t=fill")
            vf.append(f"drawbox=x=18:y=18:w=564:h=864:color={accent}@0.9:t=3")
            label = ("TV SERIES" if item["kind"] == "show" else "FEATURE FILM")
            (tmp / "label.txt").write_text(label)
            vf.append(f"drawtext=fontfile='{_esc(font)}':textfile='{_esc(tmp / 'label.txt')}':expansion=none"
                      f":fontsize=30:fontcolor={second}:x=44:y=46:shadowcolor=black@0.8:shadowx=2:shadowy=2")
            vf.append(f"drawbox=x=44:y=88:w=120:h=5:color={accent}:t=fill")
            lh = int(size * 1.08)
            year = str(item.get("year") or "")
            bottom = 900 - 70 - (44 if year else 0)
            for i, line in enumerate(lines):
                f = tmp / f"l{i}.txt"
                f.write_text(line)
                y = bottom - (len(lines) - i) * lh
                vf.append(f"drawtext=fontfile='{_esc(font)}':textfile='{_esc(f)}':expansion=none:fontsize={size}"
                          f":fontcolor=white:x=(w-text_w)/2:y={y}:borderw=3:bordercolor={accent}@0.85"
                          f":shadowcolor=black@0.7:shadowx=4:shadowy=5")
            if year:
                (tmp / "year.txt").write_text(year)
                vf.append(f"drawtext=fontfile='{_esc(font)}':textfile='{_esc(tmp / 'year.txt')}':expansion=none"
                          f":fontsize=36:fontcolor={second}:x=(w-text_w)/2:y={bottom + 6}"
                          f":shadowcolor=black@0.8:shadowx=2:shadowy=2")
            out = _art_dir() / f"{k}-poster.part.jpg"
            subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-i", str(frame), "-vf", ",".join(vf),
                            "-q:v", "3", str(out)], timeout=60, check=False)
            if not out.exists():
                return False
            os.replace(out, _art_dir() / f"{k}-poster.jpg")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    try:
        frame.unlink()
    except OSError:
        pass
    _log.info("on demand: made a cover for %s", item.get("title"))
    return True


# ------------------------------------------------------------------ "Watch On Demand" from a channel

def find_item(path: str) -> dict:
    """The catalog title a scheduled file (a channel's link) belongs to: {"id", "episode"} or {}."""
    if not path:
        return {}
    real = _real(path)
    items = catalog().get("items", [])
    for it in items:
        if it["kind"] == "movie" and _real(it["path"]) == real:
            return {"id": it["id"], "title": it["title"], "episode": ""}
    best = None
    for it in items:
        if it["kind"] == "show":
            root = _real(it["folder"]).rstrip("/") + "/"
            if real.startswith(root) and (best is None or len(root) > len(best[0])):
                best = (root, it)
    if best is None:
        return {}
    it = best[1]
    ep = ""
    try:
        for e in episodes_in_order(show_detail(it["folder"])):
            if _real(e["path"]) == real:
                ep = e["path"]
                break
    except OSError:
        pass
    return {"id": it["id"], "title": it["title"], "episode": ep}


# ------------------------------------------------------------------ favourites

def _fav_file() -> Path:
    return paths.data_dir() / "ondemand_favourites.json"


def favourites() -> list[str]:
    try:
        return [str(x) for x in json.loads(_fav_file().read_text()).get("ids", [])]
    except (OSError, ValueError, AttributeError):
        return []


def is_favourite(item_id: str) -> bool:
    return item_id in favourites()


def set_favourite(item_id: str, on: bool) -> None:
    ids = [i for i in favourites() if i != item_id]
    if on:
        ids.insert(0, item_id)            # newest first
    try:
        paths.ensure_dirs()
        _fav_file().write_text(json.dumps({"ids": ids}))
    except OSError as e:
        _log.warning("couldn't save favourites: %s", e)


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
