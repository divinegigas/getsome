"""Find new episodes in shows that are already on channels ("Check for new episodes").

A rebuild already re-reads every show folder, so new files go into rotation as soon
as a channel is built; what was missing is knowing *when* to rebuild. This compares
each show folder used on a channel with the episodes that channel actually has linked
(its catalog link folders), and lists the channels to rebuild. In-order shows keep
their place: the new episodes slot in by season/episode number.

To stay quick on a big external drive, a show is only re-read when its folder (or a
season folder) changed since the last check that found nothing new.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import channel_writer as cw
from . import media_index, model, paths

_log = logging.getLogger("getsome.new_episodes")


@dataclass
class Found:
    folder: str
    title: str
    new: int                                   # new episodes not yet on the channel(s)
    channels: list[model.Channel] = field(default_factory=list)


def _state_file() -> Path:
    return paths.data_dir() / "episode_check.json"


def _load_state() -> dict[str, float]:
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _folder_stamp(folder: Path) -> float:
    """Newest modification time of the show folder and its direct sub-folders (seasons):
    adding a file to Season 2 changes Season 2's time."""
    stamp = folder.stat().st_mtime
    for sub in folder.iterdir():
        if sub.is_dir():
            stamp = max(stamp, sub.stat().st_mtime)
    return stamp


def _linked(ch: model.Channel) -> set[str]:
    """Real files a built channel currently has (every link under its catalog folder)."""
    root = paths.fs42_work_dir() / cw.content_rel(model.Channel(0, ch.built_name or ch.name))
    out = set()
    if root.is_dir():
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                if os.path.islink(p):
                    out.add(os.path.realpath(p))
    return out


def find(channels: list[model.Channel] | None = None, force: bool = False) -> list[Found]:
    chans = [c for c in (model.load() if channels is None else channels) if c.built_hash]
    state = _load_state()
    by_folder: dict[str, list[model.Channel]] = {}
    titles: dict[str, str] = {}
    for c in chans:
        for s in c.all_show_refs():
            by_folder.setdefault(s.folder, [])
            if c not in by_folder[s.folder]:
                by_folder[s.folder].append(c)
            titles[s.folder] = s.title or Path(s.folder).name
    linked_cache: dict[str, set[str]] = {}
    found: list[Found] = []
    for folder, users in by_folder.items():
        f = Path(folder)
        try:
            if not f.is_dir():
                continue                       # drive not plugged in: nothing to say
            stamp = _folder_stamp(f)
            if not force and state.get(folder) == stamp:
                continue
            eps = {str(e.path.resolve()) for e in media_index.scan_show(f).episodes}
        except OSError as e:
            _log.debug("skip %s: %s", folder, e)
            continue
        missing_on = []
        new = 0
        for c in users:
            linked = linked_cache.setdefault(c.name, _linked(c))
            n = len(eps - linked)
            if n:
                missing_on.append(c)
                new = max(new, n)
        if missing_on:
            found.append(Found(folder, titles[folder], new, missing_on))
        else:
            state[folder] = stamp              # up to date: skip it next time unless it changes
    try:
        _state_file().write_text(json.dumps(state))
    except OSError:
        pass
    return sorted(found, key=lambda x: x.title.lower())


def refresh_library(found: list[Found]) -> None:
    """Update the show picker's cached episode counts for these shows."""
    from . import library

    for f in found:
        try:
            library.update_cached(library.scan_one(f.folder))
        except OSError:
            pass
