"""Turn GetSome channels (model.Channel) into FieldStation42 channel configs.

FS42 wants every show to be a sub-folder ("tag") of the channel's content_dir.
Each show gets an ordered symlink folder there (see media_index), so user media
can live anywhere and sequential play follows NFO season/episode order.

Scheduling, commercial-free, in FS42's hourly slots:
  * time blocks ("Sat 7-11 AM: cartoons") fill their hours first, each with its own line-up
  * the remaining on-air hours run the channel's rotation: every in-order show gets its
    own rotation slot, all shuffled shows share one, moving on every `rotate_hours`
  * hours outside the broadcast day (or with nothing to play) are off the air: the
    sign-off clip, then bars/static, or an infomercials folder
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import media_index, model, paths

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return s or "channel"


class ChannelNumberTaken(ValueError):
    pass


def conf_path(ch: model.Channel) -> Path:
    return paths.fs42_work_dir() / "confs" / f"{slug(ch.name)}.json"


def content_rel(ch: model.Channel) -> str:
    return f"catalog/{slug(ch.name)}"


def sequence_name(ch: model.Channel, tag: str) -> str:
    return f"{slug(ch.name)}-{tag}"


MOVIES_TAG = "Movies"
INFO_TAG = "Infomercials"
ALT_TAG = "Alternating"      # "switch shows every episode" folders: Alternating, Alternating_<block id>
PAID_TITLE = "Paid Programming"
INCREMENT = {model.CONTINUOUS: 0, model.HALF_HOUR: 30, model.HOUR: 60}


def _ensure_titles(refs: list[model.ShowRef]) -> None:
    for s in refs:
        if not s.title:
            s.title = media_index.show_title(Path(s.folder))


def block_movies_tag(b: model.Block) -> str:
    return f"{MOVIES_TAG}_{slug(b.id)}"


@dataclass
class Plan:
    """Everything a channel schedules, deduplicated into FS42 tags (sub-folders).

    A show used in the rotation and in a block (or in two blocks) is one tag, so an
    in-order show keeps one place in its sequence wherever it airs."""
    shows: list[tuple[model.ShowRef, str]] = field(default_factory=list)   # first ref per folder -> tag
    refs: dict[str, list[model.ShowRef]] = field(default_factory=dict)     # tag -> every ref (start_pending)
    movie_sets: dict[str, tuple[list[str], model.ShowRef]] = field(default_factory=dict)  # tag -> (paths, ref)
    contexts: list[tuple[object, list[tuple[model.ShowRef, str]]]] = field(default_factory=list)  # (channel|block, items)
    seq: set[str] = field(default_factory=set)                            # tags played as sequences
    infomercials: str = ""
    # "switch shows every episode": one interleaved folder per context (see _link_alt)
    alt: dict[str, tuple[object, list[tuple[model.ShowRef, str]]]] = field(default_factory=dict)  # tag -> (ctx, items)
    alt_ctx: dict[int, str] = field(default_factory=dict)                  # context index -> alt tag
    used: set[str] = field(default_factory=set)                            # tags that slots really use

    def sequence_refs(self) -> list[tuple[model.ShowRef, str]]:
        """(ref, tag) for every tag that plays in order somewhere on the channel."""
        out = [(s, t) for s, t in self.shows if t in self.seq and t in self.used]
        out += [(ref, t) for t, (_p, ref) in self.movie_sets.items() if t in self.seq and t in self.used]
        out += [(model.ShowRef(folder="", title="Alternating shows", mode=model.SEQUENTIAL), t) for t in self.alt]
        return out


def plan(ch: model.Channel) -> Plan:
    p = Plan()
    used = {MOVIES_TAG, INFO_TAG, ALT_TAG}
    by_folder: dict[str, str] = {}

    def show_tag(s: model.ShowRef) -> str:
        if s.folder in by_folder:
            tag = by_folder[s.folder]
            p.refs[tag].append(s)
            return tag
        base = slug(s.title)
        tag, n = base, 2
        while tag in used:
            tag, n = f"{base}_{n}", n + 1
        used.add(tag)
        by_folder[s.folder] = tag
        p.shows.append((s, tag))
        p.refs[tag] = [s]
        return tag

    _ensure_titles(ch.all_show_refs())
    base_items = []
    if ch.kind != model.MOVIES:
        base_items = [(s, show_tag(s)) for s in ch.shows]
    if ch.movies:
        # a movie channel's films are always picked at random
        ref = ch.movies_ref() if ch.kind != model.MOVIES else model.ShowRef(folder="", title="Movies", mode=model.SHUFFLE)
        p.movie_sets[MOVIES_TAG] = (list(ch.movies), ref)
        base_items.append((ref, MOVIES_TAG))
    p.contexts.append((ch, base_items))
    for b in ch.blocks:
        items = [(s, show_tag(s)) for s in b.shows]
        if b.movies:
            tag = block_movies_tag(b)
            ref = b.movies_ref()
            p.movie_sets[tag] = (list(b.movies), ref)
            items.append((ref, tag))
        p.contexts.append((b, items))
    for i, (ctx, items) in enumerate(p.contexts):
        if int(getattr(ctx, "rotate_hours", 1)) == 0 and len(items) >= 2:
            tag = ALT_TAG if i == 0 else f"{ALT_TAG}_{slug(ctx.id)}"
            p.alt[tag] = (ctx, items)
            p.alt_ctx[i] = tag
            p.seq.add(tag)
            p.used.add(tag)
            continue
        for s, t in items:
            p.used.add(t)
            if s.effective_mode(ctx) == model.SEQUENTIAL:
                p.seq.add(t)
    if ch.off_air == model.INFOMERCIALS and ch.infomercials:
        p.infomercials = ch.infomercials
    return p


def show_tags(ch: model.Channel) -> list[tuple[model.ShowRef, str]]:
    """Stable FS42 tag (sub-folder name) for each show folder on the channel."""
    return plan(ch).shows


def sequence_refs(ch: model.Channel) -> list[tuple[model.ShowRef, str]]:
    """Everything that plays in order somewhere on the channel (shows and film sets)."""
    return plan(ch).sequence_refs()


def _clear_dir_of_links(d: Path) -> None:
    for link in d.iterdir():
        if link.is_symlink():
            link.unlink()
    try:
        d.rmdir()
    except OSError:
        pass


def _link_folder(folder: Path, dest: Path, on_line=None, what: str = "episodes", title: str = "") -> None:
    if not folder.is_dir():
        raise FileNotFoundError(f"folder not found (drive not mounted?): {folder}")
    scanned = media_index.scan_show(folder)
    if title:
        scanned.title = title
    if not scanned.episodes:
        raise ValueError(f"no videos found in {folder}")
    if dest.is_symlink():          # phase-1 layout linked the whole folder
        dest.unlink()
    count = media_index.build_link_folder(scanned, dest)
    if on_line:
        on_line(f"  {scanned.title}: {count} {what}")


def _link_movies(paths_: list[str], dest: Path, on_line=None, in_order: bool = False) -> None:
    movies, missing = [], 0
    for path in paths_:
        f = Path(path)
        if f.is_file():
            movies.append(media_index.read_movie(f))
        else:
            missing += 1
    if not movies:
        raise FileNotFoundError("none of the movie files were found (drive not mounted?)")
    media_index.build_movie_links(movies, dest, in_order=in_order)
    if on_line:
        on_line(f"  {len(movies)} movies" + (f" ({missing} not found, skipped)" if missing else ""))


def _link_alt(ctx, items, p: Plan, dest: Path, starts: dict[str, str], on_line=None) -> None:
    """One episode from each entry in turn: 'NNNNN Show - S01E02.ext' links, played as one
    FS42 sequence. In-order entries keep their order, starting at `starts[tag]` (a real file
    path: where that show was up to); shuffled entries are shuffled. Shorter lists wrap round."""
    import random

    lists: list[list[tuple[str, Path]]] = []
    first = starts.get("__first__")
    if first:
        k = next((i for i, (_r, t) in enumerate(items) if t == first), 0)
        items = items[k:] + items[:k]
    for ref, tag in items:
        in_order = ref.effective_mode(ctx) == model.SEQUENTIAL
        if tag in p.movie_sets:
            movies = [media_index.read_movie(Path(f)) for f in p.movie_sets[tag][0] if Path(f).is_file()]
            if in_order:
                movies.sort(key=lambda m: (m.year or 9999, m.title.lower()))
            entries = [(media_index._safe(m.label) + Path(m.path).suffix.lower(), Path(m.path).resolve()) for m in movies]
        else:
            folder = Path(ref.folder)
            if not folder.is_dir():
                raise FileNotFoundError(f"folder not found (drive not mounted?): {folder}")
            entries = media_index.link_names(media_index.scan_show(folder))
        if not entries:
            continue
        if in_order:
            start = starts.get(tag)
            k = next((i for i, (_n, real) in enumerate(entries) if str(real) == start), 0)
            entries = entries[k:] + entries[:k]
        else:
            random.shuffle(entries)
        lists.append(entries)
    if not lists:
        raise ValueError("nothing to alternate between")
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.iterdir():
        if old.is_symlink():
            old.unlink()
    # enough rounds that every list ends exactly at a wrap (no episode twice in a row when the
    # whole folder loops), unless that would make the folder huge
    import math

    lens = [len(x) for x in lists]
    rounds = math.lcm(*lens)
    if rounds * len(lists) > 30000:
        rounds = max(lens)
    n = 0
    for r in range(rounds):
        for entries in lists:
            name, real = entries[r % len(entries)]
            n += 1
            (dest / f"{n:05d} {name}").symlink_to(real)
    if on_line:
        on_line(f"  alternating {len(lists)} shows, one episode each in turn ({n} slots)")


def _link_all(ch: model.Channel, p: Plan, on_line=None, alt_starts: dict | None = None) -> None:
    content_dir = paths.fs42_work_dir() / content_rel(ch)
    content_dir.mkdir(parents=True, exist_ok=True)
    keep = set()
    for show, tag in p.shows:
        if tag not in p.used:          # only plays inside an alternating folder
            continue
        _link_folder(Path(show.folder), content_dir / tag, on_line)
        keep.add(tag)
    for tag, (files, _ref) in p.movie_sets.items():
        if tag not in p.used:
            continue
        _link_movies(files, content_dir / tag, on_line, in_order=tag in p.seq)
        keep.add(tag)
    for tag, (ctx, items) in p.alt.items():
        _link_alt(ctx, items, p, content_dir / tag, (alt_starts or {}).get(tag, {}), on_line)
        keep.add(tag)
    if p.infomercials:
        _link_folder(Path(p.infomercials), content_dir / INFO_TAG, on_line, "infomercials", PAID_TITLE)
        keep.add(INFO_TAG)
    for old in content_dir.iterdir():
        if old.name in keep:
            continue
        if old.is_symlink():
            old.unlink()
        elif old.is_dir():
            _clear_dir_of_links(old)


def _groups(items: list[tuple[model.ShowRef, str]], ctx) -> list[list[str]]:
    """Rotation groups: each in-order entry alone, all shuffled entries together."""
    seq = [t for s, t in items if s.effective_mode(ctx) == model.SEQUENTIAL]
    shuf = [t for s, t in items if s.effective_mode(ctx) != model.SEQUENTIAL]
    return [[t] for t in seq] + ([shuf] if shuf else [])


def week_grid(ch: model.Channel, p: Plan | None = None, include_empty: bool = False) -> dict[tuple[int, int], tuple[str, object]]:
    """What each (weekday, hour) of the week holds: ("block", Block), ("base", None),
    ("info", None) or ("off", None). The GUI draws this; make_conf turns it into slots.
    include_empty: also place blocks that have nothing in them yet (for the editor)."""
    p = p or plan(ch)
    grid: dict[tuple[int, int], tuple[str, object]] = {}
    for b, items in p.contexts[1:]:
        if not items and not include_empty:
            continue
        for c in b.cells():
            grid.setdefault(c, ("block", b))      # earlier blocks win any overlap
    base_ok = bool(p.contexts[0][1])
    for d in range(7):
        for h in range(24):
            if (d, h) in grid:
                continue
            if base_ok and ch.on_air_hour(h):
                grid[(d, h)] = ("base", None)
            else:
                grid[(d, h)] = ("info", None) if p.infomercials else ("off", None)
    return grid


def _products() -> str:
    from . import settings

    return str(settings.load().get("ads_products", "few"))


def has_ads(ch: model.Channel) -> bool:
    return bool(ch.commercials) and ch.kind != model.MOVIES


# back-to-back channels with commercials start shows on 5-minute marks: FS42 only makes
# room for a break when a show is rounded up to a schedule increment
ADS_INCREMENT = 5
BREAK_SECONDS = 60     # FS42 sizes breaks from this; ~1 min lets chapter episodes break mid-show


def make_conf(ch: model.Channel, p: Plan | None = None) -> dict:
    p = p or plan(ch)
    if not ch.has_content:
        raise ValueError(f"{ch.name} has no " + ("movies" if ch.kind == model.MOVIES else "shows"))
    increment = INCREMENT.get(ch.start_mode, 0)
    ads = has_ads(ch)
    if ads and not increment:
        increment = ADS_INCREMENT

    from . import assets_gen

    # back-to-back channels get up-next bumpers; timed channels fill the gap with an intermission
    back_to_back = INCREMENT.get(ch.start_mode, 0) == 0
    extra = {"end_bump": str(assets_gen.upnext_dir())} if ch.upnext and back_to_back else {}

    def slot(tags: list[str]) -> dict:
        if len(tags) == 1 and tags[0] in p.seq:
            return {"tags": tags[0], "sequence": sequence_name(ch, tags[0]), **extra}
        if len(tags) == 1:
            return {"tags": tags[0], **extra}
        return {"tags": list(tags), "random_tags": True, **extra}

    grid = week_grid(ch, p)
    week: dict[str, dict[str, dict]] = {day: {} for day in DAYS}

    # time blocks: rotate through the block's groups, counting across its airings
    for ci, (b, items) in enumerate(p.contexts[1:], start=1):
        groups = [[p.alt_ctx[ci]]] if ci in p.alt_ctx else _groups(items, b)
        if not groups:
            continue
        rotate = max(1, int(b.rotate_hours))
        for oi, occ in enumerate(b.occurrences()):
            for k, (d, h) in enumerate(occ):
                if grid[(d, h)][0] != "block" or grid[(d, h)][1] is not b:
                    continue
                g = groups[((oi * len(occ) + k) // rotate) % len(groups)]
                week[DAYS[d]][str(h)] = slot(g)

    base_ctx, base_items = p.contexts[0]
    base_groups = [[p.alt_ctx[0]]] if 0 in p.alt_ctx else _groups(base_items, base_ctx)
    rotate = max(1, int(ch.rotate_hours))
    for (d, h), (kind, _b) in grid.items():
        if kind == "base":
            week[DAYS[d]][str(h)] = slot(base_groups[((d * 24 + h) // rotate) % len(base_groups)])
        elif kind == "info":
            week[DAYS[d]][str(h)] = {"tags": INFO_TAG}
        elif kind == "off" and ch.signoff_clip:
            prev = ((d - (h == 0)) % 7, (h - 1) % 24)
            if grid[prev][0] != "off":
                week[DAYS[d]][str(h)] = {"event": "signoff"}
    # FS42 wants the hours of each day in a stable order
    week = {day: dict(sorted(slots.items(), key=lambda kv: int(kv[0]))) for day, slots in week.items()}

    off_video = "runtime/static.mp4" if ch.off_air == model.STATIC else "runtime/bars_tone.mp4"
    return {
        "network_name": ch.name,
        "channel_number": int(ch.number),
        "network_type": "standard",
        "schedule_increment": increment,  # 0 = back to back
        "commercial_free": not ads,
        # with commercials: breaks at chapter markers / fades to black, else all at the end
        # (files with neither carry an end-only marker, see fs42.mark_end_only)
        "break_strategy": "standard" if ads else "end",
        **({"commercial_dir": str(ads_dir(ch)), "break_duration": BREAK_SECONDS} if ads else {}),
        "content_dir": content_rel(ch),
        "sign_off_video": "runtime/signoff.mp4",
        "off_air_video": off_video,
        "standby_image": "runtime/standby.png",
        # spare time before the next start is filled with this slate; TV mode draws the
        # intermission countdown over it
        "be_right_back_media": "runtime/intermission.png" if not back_to_back else "runtime/brb.png",
        # timed channels: FS42 looks for fill bumpers here first; nothing in it fits, so gaps
        # go to the slate above
        **({"bump_dir": str(assets_gen.runtime_path("nobumps"))} if increment and not ads else {}),
        **({"bump_dir": str(break_bumps())} if ads else {}),
        **({"_ads": {"black_detect": bool(ch.black_detect), "own": ch.ad_folder, "products": _products(),
                     "v": 2}} if ads else {}),
        **week,
        "_getsome": {"template": ch.template, "kind": ch.kind, "managed": True},
    }


def ads_dir(ch: model.Channel) -> Path:
    from . import ads

    return ads.channel_dir(slug(ch.name))


def break_bumps() -> Path:
    from . import ads

    return ads.break_bumps_dir()


def conf_hash(conf: dict) -> str:
    body = {k: v for k, v in conf.items() if k != "_getsome"}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def channel_hash(ch: model.Channel, conf: dict) -> str:
    """What a build depends on: the FS42 conf, plus, for channels with commercial breaks, the
    commercials' version and the product-ads setting. So an update that changes the commercials
    (ads.ADS_VERSION / ads.POOL_VERSION) or a settings change marks those channels "needs build"."""
    h = conf_hash(conf)
    if has_ads(ch):
        from . import ads

        extra = f"{h}|{ads.ADS_VERSION}|{ads.POOL_VERSION}|{_products()}"
        h = hashlib.sha1(extra.encode()).hexdigest()[:16]
    return h


def preview_hash(ch: model.Channel) -> str | None:
    """Hash of the conf this channel would produce, without touching disk (None if it can't)."""
    try:
        return channel_hash(ch, make_conf(ch))
    except Exception:
        return None


def check_number(ch: model.Channel, channels: list[model.Channel]) -> None:
    for other in channels:
        if other is not ch and other.number == ch.number and slug(other.name) != slug(ch.name):
            raise ChannelNumberTaken(f'Channel {ch.number} is already "{other.name}".')


def write_conf(ch: model.Channel, on_line=None, alt_starts: dict | None = None) -> str:
    """Refresh the show link folders and write the FS42 conf. Returns the conf hash."""
    p = plan(ch)
    _link_all(ch, p, on_line, alt_starts)
    if has_ads(ch):
        from . import ads

        films = sorted({m for files, _r in p.movie_sets.values() for m in files})
        ads.link_pool(slug(ch.name), ch.number, [(t, s.folder, s.title) for s, t in p.shows], ch.ad_folder,
                      films, _products())
        if on_line:
            on_line(f"  commercials: show promos, trivia and lineup cards, {_products()} product ads"
                    + (", plus your own folder" if ch.ad_folder else ""))
    conf = make_conf(ch, p)
    p = conf_path(ch)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"station_conf": conf}, indent=2))
    return channel_hash(ch, conf)


def remove_conf(name: str) -> bool:
    """Delete a channel's FS42 conf and its link folders (never touches the library)."""
    removed = False
    p = paths.fs42_work_dir() / "confs" / f"{slug(name)}.json"
    if p.exists():
        p.unlink()
        removed = True
    cdir = paths.fs42_work_dir() / "catalog" / slug(name)
    if cdir.is_dir():
        for tagdir in cdir.iterdir():
            if tagdir.is_symlink():
                tagdir.unlink()
            elif tagdir.is_dir():
                _clear_dir_of_links(tagdir)
        try:
            cdir.rmdir()
        except OSError:
            pass
    return removed
