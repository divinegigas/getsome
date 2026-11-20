"""Local artwork next to the owner's shows and films (TinyMediaManager / Kodi naming).

Show folder:  poster, fanart, clearlogo / logo, banner, landscape, clearart / characterart,
              and episode stills "<episode file stem>-thumb.jpg" in the season folders.
Film:         "<file stem>-poster.jpg", "-fanart", "-clearlogo", "-landscape" ... or the plain names
              (poster.jpg ...) when the film has a folder of its own.
Read only; nothing is ever written next to the media.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
KINDS = {
    "poster": ("poster", "folder", "cover"),
    "fanart": ("fanart", "backdrop", "background"),
    "logo": ("clearlogo", "logo"),
    "banner": ("banner",),
    "landscape": ("landscape", "thumb"),
    "clearart": ("clearart", "characterart"),
}
MAX_THUMBS = 12


def _first(folder: Path, names: tuple[str, ...], prefix: str = "") -> str:
    for n in names:
        for ext in IMG_EXT:
            f = folder / f"{prefix}{n}{ext}"
            if f.is_file():
                return str(f)
    return ""


@functools.lru_cache(maxsize=512)
def show_art(folder: str) -> dict:
    """Artwork of a show folder: {kind: path} plus "thumbs": [(path, episode stem)]."""
    d = Path(folder)
    out: dict = {k: _first(d, names) for k, names in KINDS.items()}
    if not out["poster"]:
        out["poster"] = next((str(f) for f in sorted(d.glob("*-poster.*")) if f.suffix.lower() in IMG_EXT), "")
    if not out["landscape"] and out["fanart"]:
        out["landscape"] = out["fanart"]
    thumbs: list[tuple[str, str]] = []
    try:
        seasons = sorted((s for s in d.iterdir() if s.is_dir() and not s.name.startswith(".")),
                         key=lambda s: s.name)
        for s in seasons:
            if s.name.lower() in ("specials", "season 0", "season 00", "extras"):
                continue
            for f in sorted(s.glob("*-thumb.*")):
                if f.suffix.lower() in IMG_EXT:
                    thumbs.append((str(f), f.stem[:-6]))
    except OSError:
        pass
    if len(thumbs) > MAX_THUMBS:     # spread across the run of the show
        step = len(thumbs) / MAX_THUMBS
        thumbs = [thumbs[int(i * step)] for i in range(MAX_THUMBS)]
    out["thumbs"] = thumbs
    return out


@functools.lru_cache(maxsize=512)
def movie_art(path: str) -> dict:
    f = Path(path)
    d, stem = f.parent, f.stem
    out = {k: _first(d, names, stem + "-") for k, names in KINDS.items()}
    # a folder per film: the plain names belong to it
    for k, names in KINDS.items():
        if not out[k]:
            out[k] = _first(d, names)
    if not out["landscape"] and out["fanart"]:
        out["landscape"] = out["fanart"]
    out["thumbs"] = []
    return out


def _show_folder(real: Path) -> Path | None:
    for parent in list(real.parents)[:3]:
        if (parent / "tvshow.nfo").is_file():
            return parent
    if real.parent.name.lower().startswith("season"):
        return real.parent.parent
    return None


@functools.lru_cache(maxsize=1024)
def for_programme(path: str) -> dict:
    """Artwork for a scheduled file (a symlink in the channel's catalog folder):
    the show's art (plus "still": this episode's thumb) or the film's art. {} if none."""
    if not path:
        return {}
    try:
        real = Path(os.path.realpath(path))
    except OSError:
        return {}
    show = _show_folder(real)
    if show is not None:
        art = dict(show_art(str(show)))
        art["still"] = _first(real.parent, ("thumb",), real.stem + "-")
    else:
        art = dict(movie_art(str(real)))
        art["still"] = ""
    return art if any(v for k, v in art.items() if k != "thumbs") else {}
