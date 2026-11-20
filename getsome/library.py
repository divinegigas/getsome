"""The user's TV library: which folders hold shows, and a cached scan of them.

Scanning a big library on an external drive takes a while, so results are kept
in ~/.local/share/getsome/library.json and only refreshed on request.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import media_index, paths, settings

_log = logging.getLogger("getsome.library")


CACHE_VERSION = 3     # 2: shows carry genres; 3: ratings (shows and films)


@dataclass
class LibShow:
    folder: str
    title: str
    episodes: int
    seasons: list[int]
    codes: list[str] = field(default_factory=list)    # "S01E01" in play order
    names: list[str] = field(default_factory=list)    # episode titles (may be "")
    root: str = ""
    genres: list[str] = field(default_factory=list)
    rating: str = ""

    @property
    def season_text(self) -> str:
        if not self.seasons:
            return "-"
        return f"{self.seasons[0]}-{self.seasons[-1]}" if len(self.seasons) > 1 else str(self.seasons[0])


@dataclass
class LibMovie:
    path: str
    title: str
    year: int | None = None
    genres: list[str] = field(default_factory=list)
    rating: str = ""
    runtime: int | None = None

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title

    @property
    def length_text(self) -> str:
        if not self.runtime:
            return ""
        h, m = divmod(self.runtime, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m"


def movie_from_scan(m: media_index.Movie) -> LibMovie:
    return LibMovie(str(m.path), m.title, m.year, list(m.genres or []), m.rating, m.runtime)


def movie_folders() -> list[str]:
    return list(settings.load().get("movie_folders", []))


def set_movie_folders(new: list[str]) -> None:
    s = settings.load()
    s["movie_folders"] = new
    settings.save(s)


def _movie_cache() -> Path:
    return paths.data_dir() / "movies.json"


_mmemo: tuple[float, list[str], list[LibMovie]] | None = None


def scan_movie_library(progress: Callable[[int, int, str], None] | None = None) -> list[LibMovie]:
    out: list[LibMovie] = []
    roots = movie_folders()
    for i, root in enumerate(roots):
        if progress:
            progress(i, len(roots), Path(root).name)
        try:
            out += [movie_from_scan(m) for m in media_index.scan_movies(Path(root))]
        except OSError as e:
            _log.warning("cannot read movie folder %s: %s", root, e)
    _movie_cache().write_text(json.dumps({"version": CACHE_VERSION, "scanned_at": time.time(), "folders": roots,
                                          "movies": [asdict(m) for m in out]}))
    return out


def cached_movies() -> list[LibMovie] | None:
    global _mmemo
    p = _movie_cache()
    if not p.exists():
        return None
    try:
        mtime, fl = p.stat().st_mtime, movie_folders()
        if _mmemo and _mmemo[0] == mtime and _mmemo[1] == fl:
            return _mmemo[2]
        data = json.loads(p.read_text())
        if data.get("folders") != fl or data.get("version") != CACHE_VERSION:
            return None
        movies = [LibMovie(**m) for m in data.get("movies", [])]
        _mmemo = (mtime, fl, movies)
        return movies
    except Exception:
        return None


def lookup_movie(path: str) -> LibMovie | None:
    for m in cached_movies() or []:
        if m.path == path:
            return m
    return None


def _cache() -> Path:
    return paths.data_dir() / "library.json"


def folders() -> list[str]:
    return list(settings.load().get("library_folders", []))


def set_folders(new: list[str]) -> None:
    s = settings.load()
    s["library_folders"] = new
    settings.save(s)


def show_from_scan(sh: media_index.Show, root: str = "") -> LibShow:
    codes, names = [], []
    for e in sh.episodes:
        codes.append(f"S{e.season:02d}E{e.episode:02d}" if e.season is not None else e.path.stem[:24])
        names.append(e.title)
    return LibShow(str(sh.folder), sh.title, len(sh.episodes), sh.seasons, codes, names, root, list(sh.genres or []), sh.rating or "")


def scan(progress: Callable[[int, int, str], None] | None = None) -> list[LibShow]:
    shows: list[LibShow] = []
    todo: list[tuple[str, Path]] = []
    for root in folders():
        try:
            todo += [(root, f) for f in media_index.find_shows(Path(root))]
        except OSError as e:
            _log.warning("cannot read library folder %s: %s", root, e)
    for i, (root, folder) in enumerate(todo):
        if progress:
            progress(i, len(todo), folder.name)
        try:
            sh = media_index.scan_show(folder)
        except OSError as e:
            _log.warning("skipping %s: %s", folder, e)
            continue
        if sh.episodes:
            shows.append(show_from_scan(sh, root))
    if progress:
        progress(len(todo), len(todo), "")
    shows.sort(key=lambda s: s.title.lower())
    _cache().write_text(json.dumps({"version": CACHE_VERSION, "scanned_at": time.time(), "folders": folders(),
                                    "shows": [asdict(s) for s in shows]}))
    return shows


_memo: tuple[float, list[str], list[LibShow]] | None = None


def cached() -> list[LibShow] | None:
    """Last scan, or None if never scanned or the library folders changed since."""
    global _memo
    p = _cache()
    if not p.exists():
        return None
    try:
        mtime, fl = p.stat().st_mtime, folders()
        if _memo and _memo[0] == mtime and _memo[1] == fl:
            return _memo[2]
        data = json.loads(p.read_text())
        if data.get("folders") != fl or data.get("version") != CACHE_VERSION:
            return None          # different folders, or an older scan without genres: rescan
        shows = [LibShow(**s) for s in data.get("shows", [])]
        _memo = (mtime, fl, shows)
        return shows
    except Exception:
        return None


def lookup(folder: str) -> LibShow | None:
    for s in cached() or []:
        if s.folder == folder:
            return s
    return None


def scan_one(folder: str) -> LibShow:
    return show_from_scan(media_index.scan_show(Path(folder)))


def update_cached(show: LibShow) -> None:
    """Refresh one show in the library cache (e.g. after new episodes were found)."""
    p = _cache()
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return
    shows = data.get("shows", [])
    for i, s in enumerate(shows):
        if s.get("folder") == show.folder:
            shows[i] = {**asdict(show), "root": s.get("root", show.root)}
            break
    else:
        return
    p.write_text(json.dumps(data))
