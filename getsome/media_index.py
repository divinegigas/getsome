"""Understand a TV library laid out by TinyMediaManager (or anything Kodi-like).

For each show folder we work out the episode order from the per-episode .nfo
files (<season>/<episode>), falling back to SxxEyy / 1x02 in the file name.
Specials (season 0) and extras (trailers, featurettes, ...) are left out.

FS42 plays sequences in plain path order and takes display titles from file
names, so channels don't point at the raw folders. Instead each show gets a
folder of symlinks named "Show Title - S01E02.ext", which sort correctly
(Season 2 before Season 10) and give FS42 clean titles.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".webm", ".flv", ".divx", ".ogv"}
EXTRA_DIRS = {"extras", "extra", "featurettes", "trailers", "trailer", "behind the scenes", "deleted scenes",
              "interviews", "scenes", "shorts", "other", "others", "samples", "sample", ".actors", "backdrops"}
EXTRA_SUFFIX = re.compile(r"[-_. ](trailer|sample|featurette|behindthescenes|deleted|interview|scene|short|other|extra)$", re.I)
SXE = [re.compile(r"[Ss](\d{1,3})[ ._-]?[Ee](\d{1,4})"), re.compile(r"\b(\d{1,2})[xX](\d{2,3})\b")]


@dataclass
class Episode:
    path: Path
    season: int | None
    episode: int | None
    title: str = ""


@dataclass
class Show:
    folder: Path
    title: str
    episodes: list[Episode]   # in play order
    skipped_specials: int = 0
    unnumbered: int = 0
    genres: list[str] | None = None
    rating: str = ""            # tvshow.nfo <mpaa>, e.g. "TV-14"

    @property
    def seasons(self) -> list[int]:
        return sorted({e.season for e in self.episodes if e.season is not None})


def _tag(xml: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", xml, re.S | re.I)
    return m.group(1) if m else None


def _unescape(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'"))


def show_title(folder: Path) -> str:
    nfo = folder / "tvshow.nfo"
    if nfo.exists():
        try:
            t = _tag(nfo.read_text(errors="replace"), "title")
            if t:
                return _unescape(t).strip()
        except OSError:
            pass
    return re.sub(r"\s*\(\d{4}\)\s*$", "", folder.name).strip() or folder.name


def clean_rating(raw: str | None) -> str:
    """An NFO <mpaa>/<certification> as a short rating: "US:TV-MA" -> "TV-MA", "Rated R" -> "R",
    "UK:15 / US:R" -> "R" (the US one when there are several)."""
    parts = [p.strip() for p in _unescape(raw or "").split(" / ") if p.strip()]
    if not parts:
        return ""
    pick = next((p for p in parts if p.upper().startswith("US:")), parts[0])
    pick = re.sub(r"^[A-Za-z]{2,3}:\s*", "", pick)
    pick = re.sub(r"^Rated\s+", "", pick, flags=re.I).strip()
    return pick[:12]


def show_rating(folder: Path) -> str:
    nfo = folder / "tvshow.nfo"
    if not nfo.exists():
        return ""
    try:
        xml = nfo.read_text(errors="replace")
    except OSError:
        return ""
    return clean_rating(_tag(xml, "mpaa") or _tag(xml, "certification"))


def show_genres(folder: Path) -> list[str]:
    nfo = folder / "tvshow.nfo"
    if not nfo.exists():
        return []
    try:
        xml = nfo.read_text(errors="replace")
    except OSError:
        return []
    return [_unescape(g.strip()) for g in re.findall(r"<genre>\s*(.*?)\s*</genre>", xml, re.S | re.I) if g.strip()]


def looks_like_library(folder: Path) -> list[Path]:
    """If `folder` holds several show folders (rather than being a show), return them."""
    folder = Path(folder)
    if looks_like_show(folder):
        return []
    try:
        subs = [d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        return []
    shows = [d for d in subs if looks_like_show(d)]
    return sorted(shows, key=lambda d: _natural(d.name)) if len(shows) >= 2 else []


def _episode_info(video: Path) -> Episode:
    nfo = video.with_suffix(".nfo")
    if nfo.exists():
        try:
            # multi-episode TMM files hold several <episodedetails>; the first one wins
            xml = nfo.read_text(errors="replace")
            s, e = _tag(xml, "season"), _tag(xml, "episode")
            if s is not None and e is not None and s.lstrip("-").isdigit() and e.lstrip("-").isdigit():
                return Episode(video, int(s), int(e), _unescape(_tag(xml, "title") or ""))
        except OSError:
            pass
    for rx in SXE:
        m = rx.search(video.stem)
        if m:
            return Episode(video, int(m.group(1)), int(m.group(2)))
    return Episode(video, None, None)


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def scan_show(folder: Path, include_specials: bool = False) -> Show:
    """Episodes of a show folder in play order.

    Works for any folder of videos: TV-style layouts (tvshow.nfo / Season folders)
    get extras and trailers filtered out; anything else (home videos, old tapes,
    a folder of commercials) keeps every video, ordered by name."""
    folder = Path(folder)
    tv_layout = looks_like_show(folder)
    eps: list[Episode] = []
    for root, dirs, files in os.walk(folder, followlinks=True):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not (tv_layout and d.lower() in EXTRA_DIRS)]
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() not in VIDEO_EXTS or f.startswith("."):
                continue
            if tv_layout and EXTRA_SUFFIX.search(p.stem):
                continue
            eps.append(_episode_info(p))

    specials = [e for e in eps if e.season == 0]
    if not include_specials:
        eps = [e for e in eps if e.season != 0]
    numbered = [e for e in eps if e.season is not None]
    unnumbered = [e for e in eps if e.season is None]
    numbered.sort(key=lambda e: (e.season, e.episode, _natural(str(e.path))))
    # one file per episode number (alternate versions would otherwise play twice)
    seen, ordered = set(), []
    for e in numbered:
        key = (e.season, e.episode)
        if key not in seen:
            seen.add(key)
            ordered.append(e)
    unnumbered.sort(key=lambda e: _natural(str(e.path.relative_to(folder))))
    return Show(folder, show_title(folder), ordered + unnumbered, genres=show_genres(folder), rating=show_rating(folder),
                skipped_specials=0 if include_specials else len(specials), unnumbered=len(unnumbered))


def looks_like_show(folder: Path) -> bool:
    if (folder / "tvshow.nfo").exists():
        return True
    try:
        return any(d.is_dir() and d.name.lower().startswith(("season", "specials")) for d in folder.iterdir())
    except OSError:
        return False


def find_shows(library: Path) -> list[Path]:
    """Show folders directly under a library folder (or the folder itself if it is a show)."""
    library = Path(library)
    if looks_like_show(library):
        return [library]
    return sorted((d for d in library.iterdir() if d.is_dir() and not d.name.startswith(".")),
                  key=lambda d: _natural(d.name))


def _safe(name: str) -> str:
    return re.sub(r"[/\x00]", "-", name).strip() or "Show"


def link_names(show: Show) -> list[tuple[str, Path]]:
    """('Title - S01E02.ext', real file) for each episode, in play order."""
    title = _safe(show.title)
    max_s = max([e.season or 0 for e in show.episodes] + [1])
    max_e = max([e.episode or 0 for e in show.episodes] + [1])
    sw, ew = max(2, len(str(max_s))), max(2, len(str(max_e)))
    used: set[str] = set()
    out = []
    n_unnumbered = 0
    for e in show.episodes:
        if e.season is not None:
            stem = f"{title} - S{e.season:0{sw}d}E{e.episode:0{ew}d}"
        else:
            # after all numbered episodes, in folder order
            n_unnumbered += 1
            stem = f"{title} - S{'9' * sw}E{n_unnumbered:0{max(ew, 3)}d}"
        name, k = stem + e.path.suffix.lower(), 2
        while name in used:
            name, k = f"{stem} ({k}){e.path.suffix.lower()}", k + 1
        used.add(name)
        out.append((name, e.path.resolve()))
    return out


def build_link_folder(show: Show, dest: Path) -> int:
    """(Re)create dest/ as symlinks 'Title - S01E02.ext' in play order. Returns episode count."""
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.iterdir():
        if old.is_symlink():
            old.unlink()
    for name, real in link_names(show):
        (dest / name).symlink_to(real)
    return len(show.episodes)


# --------------------------------------------------------------------------- movies

@dataclass
class Movie:
    path: Path
    title: str
    year: int | None = None
    genres: list[str] | None = None
    rating: str = ""            # MPAA / certification, e.g. "Rated R" -> "R"
    runtime: int | None = None  # minutes

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


def _movie_nfo(video: Path) -> Path | None:
    for cand in (video.with_suffix(".nfo"), video.parent / "movie.nfo"):
        if cand.exists():
            return cand
    return None


def read_movie(video: Path) -> Movie:
    nfo = _movie_nfo(video)
    if nfo:
        try:
            xml = nfo.read_text(errors="replace")
            if re.search(r"<movie[\s>]", xml, re.I):
                title = _unescape(_tag(xml, "title") or "") or video.stem
                year = _tag(xml, "year")
                genres = [_unescape(g.strip()) for g in re.findall(r"<genre>\s*(.*?)\s*</genre>", xml, re.S | re.I)]
                mpaa = clean_rating(_tag(xml, "mpaa") or _tag(xml, "certification"))
                runtime = _tag(xml, "runtime")
                return Movie(video, title.strip(),
                             int(year) if year and year.strip().isdigit() else None,
                             [g for g in genres if g], mpaa,
                             int(runtime) if runtime and runtime.strip().isdigit() else None)
        except OSError:
            pass
    # no NFO: "Some.Movie.1987.720p" / "Some Movie (1987)" -> title + year from the name
    stem = video.stem
    m = re.search(r"^(.*?)[\s._(\[]+((?:19|20)\d{2})(?:[\s._)\]]|$)", stem)
    if m and m.group(1).strip(" ._-"):
        title, year = m.group(1), int(m.group(2))
    else:
        title, year = stem, None
    title = re.sub(r"\s+", " ", re.sub(r"[._]+", " ", title)).strip(" -")
    return Movie(video, title or stem, year, [], "", None)


def scan_movies(root: Path) -> list[Movie]:
    """Every movie file under root (Kodi/TMM layout or loose files). Extras and trailers are skipped."""
    out: list[Movie] = []
    for r, dirs, files in os.walk(Path(root), followlinks=True):
        dirs[:] = [d for d in dirs if d.lower() not in EXTRA_DIRS and not d.startswith(".")]
        for f in files:
            p = Path(r) / f
            if p.suffix.lower() not in VIDEO_EXTS or f.startswith(".") or EXTRA_SUFFIX.search(p.stem):
                continue
            out.append(read_movie(p))
    out.sort(key=lambda m: (_natural(re.sub(r"^(the|a|an)\s+", "", m.title, flags=re.I)), m.year or 0))
    return out


def build_movie_links(movies: list[Movie], dest: Path, in_order: bool = False) -> int:
    """(Re)create dest/ as 'Title (Year).ext' symlinks to the movie files.

    in_order: prefix a running number, release year first, so FS42's path-sorted
    sequence plays the films chronologically."""
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.iterdir():
        if old.is_symlink():
            old.unlink()
    used: set[str] = set()
    if in_order:
        movies = sorted(movies, key=lambda m: (m.year or 9999, _natural(m.title)))
    for i, m in enumerate(movies):
        stem = (f"{i + 1:03d} " if in_order else "") + _safe(m.label)
        name, k = stem + m.path.suffix.lower(), 2
        while name in used:
            name, k = f"{stem} [{k}]{m.path.suffix.lower()}", k + 1
        used.add(name)
        (dest / name).symlink_to(Path(m.path).resolve())
    return len(movies)
