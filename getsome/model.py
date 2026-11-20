"""GetSome's own channel model: the source of truth that FS42 configs are generated from.

Stored in ~/.local/share/getsome/channels.json. The setup GUI and the CLI both
edit this; `channel_writer.write_conf()` turns a Channel into FS42 JSON.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths

_log = logging.getLogger("getsome.model")

SHUFFLE, SEQUENTIAL, DEFAULT = "shuffle", "sequential", "default"


CONTINUOUS, HALF_HOUR, HOUR = "continuous", "half_hour", "hour"
START_MODES = [(CONTINUOUS, "Back to back"), (HALF_HOUR, "On the hour & half hour"), (HOUR, "On the hour")]
TV, MOVIES = "tv", "movies"


@dataclass
class Template:
    key: str
    name: str
    blurb: str
    mode: str
    rotate_hours: int
    kind: str = TV
    start_mode: str = CONTINUOUS


TEMPLATES: list[Template] = [
    Template("marathon", "Series Marathon",
             "Episodes play in order and the channel remembers its place. "
             "With several series, the channel switches series every few hours.",
             SEQUENTIAL, 2),
    Template("shuffle", "Shuffle Classics",
             "Any episode of any show, picked at random. Great for sitcoms and cartoons.",
             SHUFFLE, 1),
    Template("mix", "24-Hour Mix",
             "Round-the-clock rotation. Set each show to play in order or shuffled; "
             "in-order shows get their own hours, shuffled shows share the rest.",
             SHUFFLE, 1),
    Template("movies", "Movie Channel",
             "Feature films from your movie library, starting on the hour and half hour "
             "with an intermission countdown in between. Pick by hand or by genre and decade.",
             SHUFFLE, 1, MOVIES, HALF_HOUR),
]
TEMPLATE_BY_KEY = {t.key: t for t in TEMPLATES}
ROTATE_CHOICES = [0, 1, 2, 3, 4, 6]     # 0 = every episode (one episode of each show in turn)


def rotate_text(h: int) -> str:
    return "every episode" if h == 0 else ("every hour" if h == 1 else f"every {h} hours")


@dataclass
class ShowRef:
    folder: str
    title: str = ""
    mode: str = DEFAULT                 # default | shuffle | sequential
    start: str | None = None            # "S03E01" - first episode to air (sequential only)
    start_pending: bool = False         # start changed and not yet applied by a build

    def effective_mode(self, channel: "Channel") -> str:
        return channel.mode if self.mode == DEFAULT else self.mode


DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_PRESETS = [("Every day", [0, 1, 2, 3, 4, 5, 6]), ("Weekdays", [0, 1, 2, 3, 4]),
               ("Weekends", [5, 6]), ("Weeknights", [0, 1, 2, 3, 4])]

BARS, STATIC, INFOMERCIALS = "bars", "static", "infomercials"
OFF_AIR_MODES = [(BARS, "Colour bars + 1 kHz tone"), (STATIC, "Static (snow + hiss)"),
                 (INFOMERCIALS, "Infomercials (a folder of videos)")]


def hour_text(h: int) -> str:
    """0 -> "12 AM", 13 -> "1 PM", 24 -> "12 AM" (midnight)."""
    h %= 24
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def days_text(days: list[int]) -> str:
    d = sorted(set(days))
    for name, preset in (("Every day", [0, 1, 2, 3, 4, 5, 6]), ("Weekdays", [0, 1, 2, 3, 4]), ("Weekends", [5, 6])):
        if d == preset:
            return name
    # runs of consecutive days: "Mon–Wed, Fri"
    parts, i = [], 0
    while i < len(d):
        j = i
        while j + 1 < len(d) and d[j + 1] == d[j] + 1:
            j += 1
        parts.append(DAY_NAMES[d[i]] if i == j else f"{DAY_NAMES[d[i]]}–{DAY_NAMES[d[j]]}")
        i = j + 1
    return ", ".join(parts) or "No days"


@dataclass
class Block:
    """A recurring time block, e.g. "Sat 7–11 AM: cartoons", with its own line-up.

    Hours are whole hours (FS42 schedules in hourly slots). `end` may be at or before
    `start`, meaning the block runs past midnight into the next day.
    """
    id: str
    name: str = "New block"
    days: list[int] = field(default_factory=lambda: [5])   # 0 = Monday
    start: int = 20
    end: int = 23
    mode: str = SHUFFLE                 # default play order for its shows
    rotate_hours: int = 1
    shows: list[ShowRef] = field(default_factory=list)
    movies: list[str] = field(default_factory=list)
    movie_mode: str = DEFAULT

    @property
    def length(self) -> int:
        n = (self.end - self.start) % 24
        return n or 24

    def occurrences(self) -> list[list[tuple[int, int]]]:
        """Each airing of the block as a list of (weekday, hour) cells, Monday first."""
        out = []
        for d in sorted(set(self.days)):
            cells = []
            for k in range(self.length):
                h = self.start + k
                cells.append(((d + h // 24) % 7, h % 24))
            out.append(cells)
        return out

    def cells(self) -> set[tuple[int, int]]:
        return {c for occ in self.occurrences() for c in occ}

    @property
    def has_content(self) -> bool:
        return bool(self.shows or self.movies)

    @property
    def when_text(self) -> str:
        return f"{days_text(self.days)}  {hour_text(self.start)}–{hour_text(self.end)}"

    def movies_ref(self) -> "ShowRef":
        return ShowRef(folder="", title="Movies", mode=self.movie_mode)

    @staticmethod
    def new_id() -> str:
        import secrets

        return secrets.token_hex(3)

    @staticmethod
    def from_dict(d: dict) -> "Block":
        shows = [ShowRef(**{k: v for k, v in s.items() if k in ShowRef.__dataclass_fields__}) for s in d.get("shows", [])]
        fields_ = {k: v for k, v in d.items() if k in Block.__dataclass_fields__ and k != "shows"}
        fields_.setdefault("id", Block.new_id())
        return Block(shows=shows, **fields_)


@dataclass
class Channel:
    number: int
    name: str
    template: str = "shuffle"
    mode: str = SHUFFLE
    rotate_hours: int = 1
    shows: list[ShowRef] = field(default_factory=list)
    kind: str = TV                      # tv | movies
    movies: list[str] = field(default_factory=list)   # movie file paths (movie channels, or films on a TV channel)
    movie_mode: str = DEFAULT           # films on a TV channel: default | sequential (by year) | shuffle
    start_mode: str = CONTINUOUS        # continuous | half_hour | hour
    upnext: bool = True                 # ~10 s "up next" bumper after every show
    blocks: list[Block] = field(default_factory=list)   # time blocks (take priority over the rotation)
    broadcast: bool = False             # False = on the air 24 hours (outside of empty hours)
    sign_on: int = 6                    # broadcast day starts at this hour...
    sign_off: int = 2                   # ...and ends at this hour (may be after midnight)
    signoff_clip: bool = True           # play the "good night" clip when going off the air
    off_air: str = BARS                 # bars | static | infomercials
    infomercials: str = ""              # folder of videos for the infomercials off-air mode
    commercials: bool = False           # commercial breaks (TV channels): after shows and at real break points
    black_detect: bool = False          # also find breaks at fades to black (slow first scan)
    ad_folder: str = ""                 # the owner's own commercials, mixed into the generated pool
    restricted: bool = False            # TV mode blurs and mutes it until the PIN is entered (settings.restricted_pin)
    lock_mode: str = "always"           # always | hours (locked except from free_from to free_to)
    free_from: int = 21                 # restricted channel needs no PIN from this hour...
    free_to: int = 6                    # ...until this hour (may be after midnight)
    built_hash: str | None = None       # hash of the FS42 conf last built
    built_name: str | None = None       # name it was built under (to clean up after a rename)

    @property
    def has_base_content(self) -> bool:
        """Content for the all-day rotation (hours not covered by a block)."""
        return bool(self.movies) or (self.kind != MOVIES and bool(self.shows))

    @property
    def has_content(self) -> bool:
        return self.has_base_content or any(b.has_content for b in self.blocks)

    def on_air_hour(self, hour: int) -> bool:
        """Inside the broadcast day (ignores blocks, which always air)."""
        if not self.broadcast or self.sign_on == self.sign_off:
            return True
        return (hour - self.sign_on) % 24 < (self.sign_off - self.sign_on) % 24

    def locked_at(self, hour: int) -> bool:
        """Does this channel need the PIN at this hour (0-23)?"""
        if not self.restricted:
            return False
        if self.lock_mode != "hours" or self.free_from == self.free_to:
            return True
        free = (hour - self.free_from) % 24 < (self.free_to - self.free_from) % 24
        return not free

    def all_show_refs(self) -> list[ShowRef]:
        refs = list(self.shows) if self.kind != MOVIES else []
        for b in self.blocks:
            refs += b.shows
        return refs

    @property
    def schedule_text(self) -> str:
        bits = []
        if self.blocks:
            bits.append(f"{len(self.blocks)} time block" + ("" if len(self.blocks) == 1 else "s"))
        if self.broadcast and self.sign_on != self.sign_off:
            bits.append(f"on the air {hour_text(self.sign_on)}–{hour_text(self.sign_off)}")
        return " · ".join(bits) or "24 hours, no time blocks"

    @property
    def content_text(self) -> str:
        def n(k: int, word: str) -> str:
            return f"{k} {word}" + ("" if k == 1 else "s")

        blocks = f" · {n(len(self.blocks), 'block')}" if self.blocks else ""
        if self.kind == MOVIES:
            return n(len(self.movies), "movie") + blocks
        text = n(len(self.shows), "show")
        return text + (f" + {n(len(self.movies), 'film')}" if self.movies else "") + blocks

    def movies_ref(self) -> "ShowRef":
        """The films on a TV channel, treated like one more show in the rotation."""
        return ShowRef(folder="", title="Movies", mode=self.movie_mode)

    @staticmethod
    def from_dict(d: dict) -> "Channel":
        shows = [ShowRef(**{k: v for k, v in s.items() if k in ShowRef.__dataclass_fields__}) for s in d.get("shows", [])]
        blocks = [Block.from_dict(b) for b in d.get("blocks", [])]
        fields_ = {k: v for k, v in d.items() if k in Channel.__dataclass_fields__ and k not in ("shows", "blocks")}
        return Channel(shows=shows, blocks=blocks, **fields_)


def _store() -> Path:
    return paths.data_dir() / "channels.json"


def load() -> list[Channel]:
    p = _store()
    if not p.exists():
        chans = _migrate_from_confs()
        if chans:
            save(chans)
        return chans
    try:
        data = json.loads(p.read_text())
        return sorted((Channel.from_dict(c) for c in data.get("channels", [])), key=lambda c: c.number)
    except Exception as e:
        _log.error("could not read %s: %s", p, e)
        raise


def save(channels: list[Channel]) -> None:
    paths.ensure_dirs()
    p = _store()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "channels": [asdict(c) for c in sorted(channels, key=lambda c: c.number)]}, indent=2))
    tmp.replace(p)


def find(channels: list[Channel], name: str) -> Channel | None:
    for c in channels:
        if c.name.lower() == name.lower():
            return c
    return None


def guide_number() -> int:
    from . import settings

    return int(settings.load().get("guide_channel") or 0)


def next_free_number(channels: list[Channel], start: int = 2) -> int:
    used = {c.number for c in channels} | {guide_number()}
    n = start
    while n in used:
        n += 1
    return n


def _migrate_from_confs() -> list[Channel]:
    """Channels created by the phase-1 CLI live only as FS42 confs with a _getsome block."""
    out = []
    for f in sorted((paths.fs42_work_dir() / "confs").glob("*.json")):
        try:
            conf = json.loads(f.read_text())["station_conf"]
        except Exception:
            continue
        meta = conf.get("_getsome")
        if not meta:
            continue
        mode = meta.get("mode", SHUFFLE)
        out.append(Channel(
            number=int(conf["channel_number"]), name=conf["network_name"],
            template="marathon" if mode == SEQUENTIAL else "shuffle", mode=mode, rotate_hours=1,
            shows=[ShowRef(folder=p) for p in meta.get("folders", [])],
            # already built by phase 1: keep the episode it is on when rebuilding
            built_hash="imported", built_name=conf["network_name"],
        ))
    if out:
        _log.info("imported %d channels from earlier configs", len(out))
    return out
