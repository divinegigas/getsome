"""Build channels: write FS42 configs, run FS42's catalog + schedule builds,
and line up in-order shows so the right episode is on air *now*.

Why the line-up step: FS42 starts a brand-new schedule at midnight, so a
sequential channel built at 8 PM has already "used" 20 hours of episodes before
anyone tunes in, and every rebuild would skip ahead again. After building we
look at which episode of each in-order show airs next, nudge FS42's sequence
position by the difference and rebuild (normally one correction is enough).

Targets per in-order show:
  * a start episode the user picked in the GUI (start_pending), else
  * whatever episode of that show was next on air before this rebuild, else
  * the beginning of the show (or its start episode) for a new show.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Callable

from . import channel_writer as cw
from . import fs42, model

_log = logging.getLogger("getsome.builder")
Log = Callable[[str], None]
LOOKAHEAD = dt.timedelta(days=8)   # a weekly time block must still be found


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- schedule probes

def _content_path(block) -> str | None:
    c = getattr(block, "content", None)
    if isinstance(c, list):
        c = c[0] if c else None
    return getattr(c, "path", None)


def next_airing(name: str, tag_dir: str, when: dt.datetime | None = None,
                hours: set[tuple[int, int]] | None = None) -> str | None:
    """Path of the episode from tag_dir that is on now or airs next on channel `name`.
    hours: only count programmes starting in these (weekday, hour) slots, i.e. where the
    show plays in order (it may also be shuffled into other hours of the channel)."""
    fs42.init()
    from fs42.play_point_query import PlayPointQuery

    q = PlayPointQuery()
    t = when or dt.datetime.now()
    stop = t + LOOKAHEAD
    prefix = tag_dir.rstrip("/") + "/"
    while t < stop:
        try:
            b = q.get_programming_block(name, t)
        except Exception:
            return None
        if b is None:
            t += dt.timedelta(minutes=1)
            continue
        p = _content_path(b)
        if p and p.startswith(prefix) and (hours is None or (b.start_time.weekday(), b.start_time.hour) in hours):
            return p
        t = b.end_time + dt.timedelta(seconds=1)
    return None


# ---------------------------------------------------------------- sequences

def _seq(station: str, seq_name: str, tag: str):
    fs42.init()
    from fs42.sequence_io import SequenceIO

    return SequenceIO().get_sequence(station, seq_name, tag)


def _set_index(station: str, seq_name: str, tag: str, idx: int) -> None:
    from fs42.sequence_io import SequenceIO

    SequenceIO().update_current_index(station, seq_name, tag, idx)


def _episode_paths(station: str, seq_name: str, tag: str) -> list[str]:
    s = _seq(station, seq_name, tag)
    return [e.fpath for e in s.episodes] if s else []


def find_code(paths_: list[str], code: str) -> int | None:
    code = code.upper()
    for i, p in enumerate(paths_):
        if f" - {code}" in Path(p).name.upper():
            return i
    return None


def _sequence_hours(name: str) -> dict[str, set[tuple[int, int]]]:
    """tag -> the (weekday, hour) slots where it plays in order, from the channel's
    FS42 conf on disk (the last one written)."""
    import json

    path = cw.conf_path(model.Channel(number=0, name=name))
    try:
        conf = json.loads(path.read_text())["station_conf"]
    except (OSError, ValueError, KeyError):
        return {}
    out: dict[str, set[tuple[int, int]]] = {}
    for d, day in enumerate(cw.DAYS):
        for h, slot in conf.get(day, {}).items():
            if isinstance(slot, dict) and "sequence" in slot and isinstance(slot.get("tags"), str):
                out.setdefault(slot["tags"], set()).add((d, int(h)))
    return out


def _find_breaks(ch: model.Channel, plan, log: Log, step) -> None:
    """Commercial breaks only at real break points: chapter markers (scanned by -r), then act
    breaks (a fade to black with a pause in the sound, breaks.py: quick, cached per file); any
    file with neither gets an end-only marker so FS42 never splits it at arbitrary moments."""
    from . import breaks

    files = fs42.catalog_files(ch.name)
    todo = [(rp, d) for rp, d in files if d > 0 and fs42.break_kind(rp) in ("", "end_only")]
    if todo:
        step(f"finding act breaks in {len(todo)} files (only new files take time)")
        found = breaks.scan(todo, lambda i, n, name: step(f"finding act breaks: {i + 1} of {n}") if i % 5 == 0 else None)
        for rp, d in todo:
            if found.get(rp):
                fs42.set_break_points(rp, found[rp], d)
    counts = {"chapters": 0, "black": 0, "end_only": 0}
    for rp, dur in files:
        kind = fs42.break_kind(rp)
        if not kind and dur > 0:
            fs42.mark_end_only(rp, dur)
            kind = "end_only"
        if kind in counts:
            counts[kind] += 1
    log(f"  break points: {counts['chapters']} files with chapters, {counts['black']} with act breaks, "
        f"{counts['end_only']} with breaks at the end only")


def _episode_file(folder: str, code: str) -> str | None:
    from . import media_index

    try:
        for name, real in media_index.link_names(media_index.scan_show(Path(folder))):
            if f" - {code.upper()}" in name.upper():
                return str(real)
    except OSError:
        pass
    return None


def _alt_starts(ch: model.Channel, plan, was_seq: dict) -> dict[str, dict[str, str]]:
    """For "every episode" folders: the real file each in-order show should continue from,
    so switching modes (or rebuilding) never loses a show's place."""
    import os

    name = ch.name
    old_alts = [t for t in was_seq if t == cw.ALT_TAG or t.startswith(cw.ALT_TAG + "_")] if ch.built_hash else []
    old_next: list[list[str]] = []     # for each old alternating folder: entries from the next one on
    for t in old_alts:
        d = Path(fs42.paths.fs42_work_dir()) / cw.content_rel(model.Channel(0, ch.built_name or name)) / t
        if not d.is_dir():
            continue
        entries = sorted(f for f in d.iterdir() if f.is_symlink())
        nxt = next_airing(ch.built_name or name, f"{cw.content_rel(ch)}/{t}", hours=was_seq[t])
        names = [e.name for e in entries]
        k = names.index(Path(nxt).name) if nxt and Path(nxt).name in names else 0
        old_next.append([os.path.realpath(e) for e in entries[k:] + entries[:k]])

    out: dict[str, dict[str, str]] = {}
    for alt_tag, (ctx, items) in plan.alt.items():
        starts = {}
        for ref, tag in items:
            if ref.effective_mode(ctx) != model.SEQUENTIAL or tag in plan.movie_sets:
                continue
            refs = plan.refs.get(tag, [ref])
            picked = next((r for r in refs if r.start_pending and r.start), None)
            real = _episode_file(ref.folder, picked.start) if picked else None
            if real is None and tag in was_seq and not tag.startswith(cw.ALT_TAG):
                prev = next_airing(name, f"{cw.content_rel(ch)}/{tag}", hours=was_seq[tag]) or _resume_point(ch, tag)
                real = os.path.realpath(prev) if prev else None
            if real is None and old_next:
                top = os.path.realpath(ref.folder) + os.sep
                for seq in old_next:
                    real = next((r for r in seq if r.startswith(top)), None)
                    if real:
                        break
            if real is None and ref.start:
                real = _episode_file(ref.folder, ref.start)
            if real:
                starts[tag] = real
        # whose turn it is: the show that was next in the old folder goes first
        if old_next and old_next[0]:
            for ref, tag in items:
                if tag not in plan.movie_sets and old_next[0][0].startswith(os.path.realpath(ref.folder) + os.sep):
                    starts["__first__"] = tag
                    break
        out[alt_tag] = starts
    return out


def _resume_point(ch: model.Channel, tag: str) -> str | None:
    """The episode after the end of an old (expired) schedule: FS42 keeps each sequence's
    position at the point its last schedule build stopped."""
    station = ch.built_name or ch.name
    try:
        s = _seq(station, f"{cw.slug(station)}-{tag}", tag)
    except Exception:
        return None
    if not s or not s.episodes:
        return None
    return s.episodes[s.current_index % len(s.episodes)].fpath


# ---------------------------------------------------------------- build

def _run(args: list[str], log: Log) -> None:
    failures: list[str] = []
    errors: list[str] = []

    def on_line(line: str) -> None:
        log("    " + line)
        text = line.strip()
        # FS42 exits 0 even when a step fails; it reports failures in its summary instead
        if text.startswith("Failed to") or text.startswith("Error"):
            failures.append(text)
        if text.endswith("Error: " + text.split("Error: ")[-1]) and "Error:" in text and len(errors) < 3:
            errors.append(text.split("Error: ", 1)[-1])

    rc = fs42.run_station42(args, on_line)
    if rc != 0 or failures:
        why = "; ".join(errors[-1:] or failures[:1]) or f"exit {rc}"
        raise BuildError(f"FieldStation42 step failed ({' '.join(args)}): {why} (see details)")


def build_channel(ch: model.Channel, log: Log = print, step: Callable[[str], None] | None = None) -> None:
    step = step or (lambda s: log(f"[{ch.number}] {s}"))
    name = ch.name
    # remember where in-order shows were, before anything is rewritten
    plan = cw.plan(ch)
    pairs = plan.sequence_refs()
    was_seq = _sequence_hours(ch.built_name or ch.name) if ch.built_hash else {}
    targets: dict[str, tuple[str, str]] = {}   # tag -> ("code", "S03E01") | ("path", path)
    alt_starts = _alt_starts(ch, plan, was_seq) if plan.alt else {}
    for show, tag in pairs:
        if tag in plan.alt:
            # the interleaved folder is written starting where each show was up to
            targets[tag] = ("index", "0")
            continue
        # the same show can sit in several blocks: any freshly picked start episode wins
        picked = next((r for r in plan.refs.get(tag, []) if r.start_pending and r.start), None)
        if picked:
            targets[tag] = ("code", picked.start)
            continue
        # only a show that was already in order has a place to keep (a shuffled one
        # switched to in-order starts from its start episode)
        kept = ch.built_hash and tag in was_seq
        prev = next_airing(name, f"{cw.content_rel(ch)}/{tag}", hours=was_seq[tag]) if kept else None
        if prev is None and kept:
            prev = _resume_point(ch, tag)
        if prev:
            targets[tag] = ("path", prev)
        elif show.start:
            targets[tag] = ("code", show.start)
        else:
            targets[tag] = ("index", "0")

    if ch.built_name and cw.slug(ch.built_name) != cw.slug(ch.name):
        step(f"removing old config for {ch.built_name}")
        cw.remove_conf(ch.built_name)

    step("checking show folders")
    new_hash = cw.write_conf(ch, log, alt_starts)

    ads = cw.has_ads(ch)
    if ads:
        step("reading episode lengths and chapter markers (only new files take time)")
        _run(["-r", name], log)
        _find_breaks(ch, plan, log, step)
    else:
        step("reading episode lengths (only new files take time)")
        _run(["-r", name, "--skip_chapter_scan"], log)
    _run(["-x", name], log)
    _run(["-a", name], log)

    # resolve targets to sequence indexes
    want: dict[str, tuple[str, int, int]] = {}   # tag -> (sequence name, target index, episode count)
    base: dict[str, int] = {}                     # tag -> index the schedule build starts from
    for show, tag in pairs:
        if tag not in targets:
            continue
        seq_name = cw.sequence_name(ch, tag)
        eps = _episode_paths(name, seq_name, tag)
        if not eps:
            continue
        kind, val = targets[tag]
        if kind == "index":
            idx = int(val)
        elif kind == "code":
            idx = find_code(eps, val)
        else:
            idx = eps.index(val) if val in eps else None
        if idx is None:
            log(f"  {show.title}: start episode {val} not found, starting from the top")
            idx = 0
        want[tag] = (seq_name, idx, len(eps))
        base[tag] = idx
        _set_index(name, seq_name, tag, idx)

    step("building the schedule")
    _run(["-w", name], log)

    # Line up: the schedule starts at midnight, so episodes get used up before "now".
    # Measure how far past the target each show is and start that much earlier.
    seq_hours = _sequence_hours(name)
    for attempt in range(3):
        shifts = {}
        for tag, (seq_name, idx, n) in want.items():
            eps = _episode_paths(name, seq_name, tag)
            airing = next_airing(name, f"{cw.content_rel(ch)}/{tag}", hours=seq_hours.get(tag))
            if airing in eps:
                shift = (eps.index(airing) - idx) % n
                if shift:
                    shifts[tag] = shift
        if not shifts:
            break
        step("lining up in-order shows with the clock" + (" (fine-tuning)" if attempt else ""))
        _run(["-x", name], log)
        for tag, (seq_name, idx, n) in want.items():
            base[tag] = (base[tag] - shifts.get(tag, 0)) % n
            _set_index(name, seq_name, tag, base[tag])
        _run(["-w", name], log)

    for ref in ch.all_show_refs():
        ref.start_pending = False
    for show, tag in pairs:
        if tag in want:
            airing = next_airing(name, f"{cw.content_rel(ch)}/{tag}", hours=seq_hours.get(tag))
            if airing and tag in plan.alt:
                log(f"  every-episode rotation: next up {fs42._ALT_PREFIX.sub('', Path(airing).stem)}")
            elif airing:
                log(f"  {show.title}: next up {Path(airing).stem.split(' - ')[-1]}")
    ch.built_hash = new_hash
    ch.built_name = ch.name
    step("done")


def build(channels: list[model.Channel], all_channels: list[model.Channel], log: Log = print,
          step: Callable[[str], None] | None = None) -> list[tuple[model.Channel, str]]:
    """Build several channels; saves the model after each success. Returns (channel, error) failures."""
    from . import upkeep

    with upkeep.schedule_lock(wait=900, on_wait=lambda: log("waiting for a schedule update to finish…")) as got:
        if not got:
            return [(ch, "another schedule update is still running; try again in a minute") for ch in channels]
        return _build_locked(channels, all_channels, log, step)


def _build_locked(channels, all_channels, log: Log, step) -> list[tuple[model.Channel, str]]:
    from . import ads, assets_gen, cleanup

    assets_gen.ensure_runtime_assets()
    log("preparing music and card clips (a minute or two the first time only)")
    ads.ensure_clips()
    failures = []
    for ch in channels:
        try:
            cw.check_number(ch, all_channels)
            # a failed earlier attempt (since renamed) must not linger as a second copy of this number
            cleanup.remove_leftovers_on(ch.number, all_channels, log)
            build_channel(ch, log, step)
            model.save(all_channels)
        except Exception as e:
            _log.exception("build failed for %s", ch.name)
            log(f"!! {ch.name}: {e}")
            failures.append((ch, str(e)))
            if not ch.built_hash and not isinstance(e, cw.ChannelNumberTaken):
                # a first build that failed: keep the half-made channel out of TV mode
                try:
                    fs42.run_station42(["-x", ch.name], None)
                except Exception:
                    pass
                cw.remove_conf(ch.name)
    return failures


def remove_channel(ch: model.Channel, all_channels: list[model.Channel], log: Log = print) -> None:
    if ch.built_hash:
        try:
            fs42.run_station42(["-x", ch.name], None)
        except Exception:
            pass
    cw.remove_conf(ch.built_name or ch.name)
    all_channels.remove(ch)
    model.save(all_channels)
