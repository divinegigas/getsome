"""Find and fix broken, leftover, empty or clashing channels ("Clean up channels…").

What it looks for:
  * leftover   - an FS42 config GetSome wrote for a channel that's no longer in the
                 channel list (e.g. a failed first build, then a rename). TV mode still
                 lists it, which is how you get a "second channel 9".
  * broken     - a channel whose build never finished: it has a config but no schedule.
                 Fix: take it off the TV (config removed) and mark it "not built"; its
                 settings stay, so it can be built again.
  * empty      - a channel with no shows or films. Fix: delete it.
  * duplicate  - two channels with the same number (or the guide's number).
                 Fix: move the extra one to the next free number.
Only configs GetSome wrote (with its "_getsome" marker) are ever touched, and never media.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from . import channel_writer as cw
from . import fs42, model, paths

_log = logging.getLogger("getsome.cleanup")


@dataclass
class Issue:
    kind: str                        # leftover | broken | empty | duplicate
    text: str                        # plain description shown to the owner
    channel: model.Channel | None = None
    conf_name: str = ""              # network name in the FS42 config (leftover/broken)
    new_number: int = 0              # duplicate: where it moves to
    checked: bool = True


def _getsome_confs() -> list[tuple[Path, dict]]:
    out = []
    for f in sorted((paths.fs42_work_dir() / "confs").glob("*.json")):
        try:
            conf = json.loads(f.read_text())["station_conf"]
        except Exception:
            continue
        if "_getsome" in conf:            # never touch hand-written FS42 configs
            out.append((f, conf))
    return out


def _owned_stems(channels: list[model.Channel]) -> set[str]:
    stems = set()
    for c in channels:
        stems.add(cw.slug(c.name))
        if c.built_name:
            stems.add(cw.slug(c.built_name))
    return stems


def leftover_confs(channels: list[model.Channel]) -> list[tuple[Path, dict]]:
    owned = _owned_stems(channels)
    return [(f, conf) for f, conf in _getsome_confs() if f.stem not in owned]


def find_issues(channels: list[model.Channel]) -> list[Issue]:
    issues: list[Issue] = []
    for f, conf in leftover_confs(channels):
        num, name = conf.get("channel_number", "?"), conf.get("network_name", f.stem)
        issues.append(Issue("leftover", f"Leftover channel {num} “{name}” that isn't in your channel list "
                                        "any more. Remove it from the TV.", conf_name=name))

    confs = {f.stem: conf for f, conf in _getsome_confs()}
    for ch in channels:
        conf = confs.get(cw.slug(ch.built_name or ch.name)) or confs.get(cw.slug(ch.name))
        if conf is None:
            continue
        name = conf.get("network_name", ch.name)
        if fs42.schedule_end(name) is None:
            what = "never finished building" if not ch.built_hash else "lost its schedule (its last build failed)"
            issues.append(Issue("broken", f"Channel {ch.number} “{ch.name}” {what}. Take it off the TV until it "
                                          "builds properly (its shows and settings are kept).",
                                channel=ch, conf_name=name))

    for ch in channels:
        if not ch.has_content:
            issues.append(Issue("empty", f"Channel {ch.number} “{ch.name}” has no shows or films. Delete it.",
                                channel=ch))

    taken = {model.guide_number()} - {0}
    claimed: set[int] = set()
    # built channels keep their number first
    for ch in sorted(channels, key=lambda c: (c.number, not c.built_hash)):
        if ch.number in taken or ch.number in claimed:
            n = ch.number + 1
            while n in taken or n in claimed or any(c.number == n for c in channels):
                n += 1
            claimed.add(n)
            why = "the TV guide's number" if ch.number == model.guide_number() else "used by another channel"
            issues.append(Issue("duplicate", f"Channel {ch.number} “{ch.name}”: that number is {why}. "
                                             f"Move it to channel {n}.", channel=ch, new_number=n))
        else:
            claimed.add(ch.number)
    return issues


def _drop_conf(name: str, log) -> None:
    try:
        fs42.run_station42(["-x", name], None)          # delete its schedule, if any
    except Exception as e:
        _log.debug("-x %s: %s", name, e)
    cw.remove_conf(name)
    log(f"removed the FS42 config for “{name}”")


def fix(issues: list[Issue], channels: list[model.Channel], log=_log.info) -> None:
    """Apply the checked issues. Saves channels.json."""
    from . import builder

    for it in [i for i in issues if i.checked]:
        try:
            if it.kind == "leftover":
                _drop_conf(it.conf_name, log)
            elif it.kind == "broken" and it.channel in channels:
                _drop_conf(it.conf_name, log)
                it.channel.built_hash = None
                it.channel.built_name = None
            elif it.kind == "empty" and it.channel in channels:
                builder.remove_channel(it.channel, channels, log)
                log(f"deleted empty channel “{it.channel.name}”")
            elif it.kind == "duplicate" and it.channel in channels:
                ch = it.channel
                was_current = bool(ch.built_hash) and cw.preview_hash(ch) == ch.built_hash
                ch.number = it.new_number
                # a built channel's config just gets its new number: no rebuild needed
                p = cw.conf_path(model.Channel(number=0, name=ch.built_name or ch.name))
                if ch.built_hash and p.exists():
                    data = json.loads(p.read_text())
                    data["station_conf"]["channel_number"] = ch.number
                    p.write_text(json.dumps(data, indent=2))
                    if was_current:
                        ch.built_hash = cw.preview_hash(ch)
                log(f"moved “{ch.name}” to channel {ch.number}")
        except Exception as e:
            _log.exception("cleanup step failed")
            log(f"could not fix: {it.text} ({e})")
    model.save(channels)


def remove_leftovers_on(number: int, channels: list[model.Channel], log=_log.info) -> None:
    """Before a build: drop leftover configs sitting on the same channel number, so a
    failed earlier attempt can't show up in TV mode as a second copy of this channel."""
    for _f, conf in leftover_confs(channels):
        if int(conf.get("channel_number", -1)) == int(number):
            _drop_conf(conf.get("network_name", _f.stem), log)


def remove_all(channels: list[model.Channel], progress=None, log=_log.info) -> int:
    """Fresh start: delete every channel (schedule, FS42 config, link folders, commercial pool).
    The owner's media, settings, library scans and generated runtime media are kept.
    progress(i, n, name) is called before each channel. Returns how many channels were removed."""
    from . import builder

    todo = list(channels)
    leftovers = leftover_confs(channels)
    n = len(todo) + len(leftovers)
    done = 0
    for i, ch in enumerate(todo):
        if progress:
            progress(i, n, ch.name)
        builder.remove_channel(ch, channels, log)
        log(f"removed channel {ch.number} “{ch.name}”")
        done += 1
    for k, (f, conf) in enumerate(leftovers):
        if progress:
            progress(len(todo) + k, n, conf.get("network_name", f.stem))
        _drop_conf(conf.get("network_name", f.stem), log)
    # per-channel commercial pools (symlinks + json only) and empty catalog folders
    for sub in ("ads", "catalog"):
        root = paths.fs42_work_dir() / sub
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir() or d.is_symlink():
                continue
            for f in d.iterdir():
                if f.is_symlink() or (sub == "ads" and f.suffix == ".json"):
                    f.unlink()
            try:
                d.rmdir()
            except OSError:
                pass
    model.save(channels)
    return done
