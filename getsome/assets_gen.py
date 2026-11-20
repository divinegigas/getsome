"""Generate the stock runtime media FS42 expects, using ffmpeg.

Nothing here ships as a binary blob: bars + tone, static, sign-off card and the
standby/BRB slates are rendered on first run. Drop a file with the same name in
the overrides folder (~/.local/share/getsome/overrides/) to replace any of them.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from . import paths

_log = logging.getLogger("getsome.assets")

W, H = 1280, 720
FONT = paths.FONTS_DIR / "Monoton-Regular.ttf"


def _esc(p: Path) -> str:
    # ffmpeg filter-arg escaping for a path inside drawtext
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _text(txt: str, size: int, y: str, color: str = "0xFF2BD6") -> str:
    return (
        f"drawtext=fontfile='{_esc(FONT)}':text='{txt}':fontsize={size}:fontcolor={color}"
        f":x=(w-text_w)/2:y={y}:shadowcolor=0x00F0FF@0.8:shadowx=4:shadowy=4"
    )


# name -> (ffmpeg input args, output args)
RECIPES: dict[str, tuple[list[str], list[str]]] = {
    # SMPTE colour bars with a 1 kHz reference tone
    "bars_tone.mp4": (
        ["-f", "lavfi", "-i", f"smptehdbars=size={W}x{H}:rate=30:duration=30",
         "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=30"],
        ["-af", "volume=0.25"],
    ),
    # Snow + hiss, used for the "static" off-air option and FS42's own static.mp4
    "static.mp4": (
        ["-f", "lavfi", "-i", f"nullsrc=size=320x180:rate=30:duration=6",
         "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.25:duration=6:sample_rate=48000"],
        ["-vf", f"geq=lum='random(1)*255':cb=128:cr=128,scale=640:360:flags=neighbor,format=yuv420p",
         "-af", "highpass=f=200,lowpass=f=9000"],
    ),
    "signoff.mp4": (
        ["-f", "lavfi", "-i", f"color=c=0x05000F:size={W}x{H}:rate=30:duration=12",
         "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=12"],
        ["-vf", ",".join([_text("GOOD NIGHT", 130, "(h/2)-150"), _text("WE NOW CONCLUDE OUR BROADCAST DAY", 34, "(h/2)+60", "0x00F0FF")]),
         "-af", "volume=0.0"],
    ),
}

STILLS: dict[str, tuple[str, str]] = {
    "standby.png": ("PLEASE STAND BY", "0x1A0030"),
    "brb.png": ("WE'LL BE RIGHT BACK", "0x001A30"),
    "offair.png": ("OFF AIR", "0x000000"),
    "intermission.png": ("INTERMISSION", "0x0b0326"),
}


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found - it is needed to generate runtime media")
    return exe


_encoders: set[str] | None = None


def video_codec_args(quality: str = "normal") -> list[str]:
    """H.264 when this ffmpeg has it; otherwise the built-in MPEG-4 encoder.

    Fedora's stock ffmpeg-free has no libx264, but every ffmpeg has mpeg4 + aac.
    """
    global _encoders
    if _encoders is None:
        out = subprocess.run([_ffmpeg(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        _encoders = {line.split()[1] for line in out.splitlines() if len(line.split()) > 1 and line.startswith(" V")}
    if "libx264" in _encoders:
        crf = {"normal": "23", "fast": "30", "low": "34"}[quality]
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p"]
    q = {"normal": "4", "fast": "8", "low": "14"}[quality]
    return ["-c:v", "mpeg4", "-q:v", q, "-pix_fmt", "yuv420p"]


def runtime_path(name: str) -> Path:
    return paths.fs42_work_dir() / "runtime" / name


def ensure_runtime_assets(force: bool = False) -> None:
    """Create (or copy from overrides) everything in runtime/ that FS42 configs reference."""
    paths.ensure_dirs()
    ff = _ffmpeg()
    for name, (inputs, outputs) in RECIPES.items():
        _make(name, force, [ff, "-y", "-loglevel", "error", *inputs, *outputs,
                            *video_codec_args("low" if name == "static.mp4" else "normal"),
                            "-c:a", "aac", "-shortest"])
    for name, (txt, bg) in STILLS.items():
        _make(name, force, [ff, "-y", "-loglevel", "error", "-f", "lavfi",
                            "-i", f"color=c={bg}:size={W}x{H}:duration=1",
                            "-vf", _text(txt, 90, "(h-text_h)/2"), "-frames:v", "1"])
    # FS42's own defaults use these names
    for alias, src in (("off_air_pattern.mp4", "bars_tone.mp4"),):
        dst = runtime_path(alias)
        if force or not dst.exists():
            shutil.copyfile(runtime_path(src), dst)
    ensure_music(force)
    ensure_upnext(force)
    ensure_nobumps(force)


def ensure_nobumps(force: bool = False) -> None:
    """FS42 fills a timed channel's spare minutes from its bump folder, and only falls
    back to the be-right-back slate (our intermission) when nothing there fits. So the
    folder holds one tiny 3-hour placeholder that can never fit a gap (gaps are < 1 h)."""
    d = runtime_path("nobumps")
    d.mkdir(exist_ok=True)
    dst = d / "never_fits.mp4"
    if dst.exists() and not force:
        return
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=black:size=16x16:rate=1:duration=10800",
                    *video_codec_args("low"), str(dst)], check=True)


UPNEXT_SECONDS = 10.0
UPNEXT_COUNT = 3


def upnext_dir() -> Path:
    return runtime_path("upnext")


MUSIC_VERSION = "3"          # bump to regenerate everyone's generated guide music


def music_dir() -> Path:
    d = runtime_path("music")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _override_music() -> Path | None:
    """overrides/guide_music.(mp3|ogg|flac|wav|m4a) - the user's own guide music."""
    for ext in (".mp3", ".ogg", ".flac", ".wav", ".m4a"):
        o = paths.overrides_dir() / f"guide_music{ext}"
        if o.exists():
            dst = runtime_path(f"custom_guide_music{ext}")
            if not dst.exists() or o.stat().st_mtime > dst.stat().st_mtime:
                shutil.copyfile(o, dst)
            return dst
    return None


# full-length guide tracks in other styles (beds.compose), composed in the background like the rest
GENRE_SECONDS = 150
GENRE_TRACKS = {
    "g_metal1": ("metal", 11, "Iron Comet"),
    "g_metal2": ("metal", 12, "Obsidian Gate"),
    "g_rock1": ("rock", 21, "Highway Neon"),
    "g_rock2": ("rock", 22, "Arcade Heart"),
    "g_punk1": ("punk", 31, "Skate Park Riot"),
    "g_punk2": ("punk", 32, "Three Chord Laser"),
    "g_reggae1": ("reggae", 41, "Sunset Dub Station"),
    "g_reggae2": ("reggae", 42, "Island Grid"),
    "g_ska1": ("ska", 51, "Checkerboard Express"),
    "g_ska2": ("ska", 52, "Pinball Skank"),
}
SHUFFLES = ("shuffle", "shuffle_rpg")


def music_options() -> list[tuple[str, str]]:
    """(key, display name) for every guide-music choice, ready or not."""
    from . import music

    opts = [("shuffle", "Shuffle all (every style)"), ("shuffle_rpg", "Shuffle RPG tracks only")]
    opts += [(t.key, t.name) for t in music.TRACKS]
    opts += [(key, f"{name}  ({style})") for key, (style, _seed, name) in GENRE_TRACKS.items()]
    opts.append(("classic", "Classic mellow synth"))
    if _override_music():
        opts.append(("custom", "Your own music"))
    return opts


def track_path(key: str) -> Path:
    if key == "custom":
        return _override_music() or music_dir() / "custom.m4a"
    return music_dir() / f"{key}.m4a"


def track_ready(key: str) -> bool:
    if key == "custom":
        return _override_music() is not None
    v = music_dir() / f"{key}.version"
    return track_path(key).exists() and v.exists() and v.read_text().strip() == MUSIC_VERSION


def _pid_alive(pid: int) -> bool:
    """Is `pid` a running GetSome process? (Process numbers get reused, so check what it is.)"""
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"getsome" in f.read()
    except OSError:
        return True      # no /proc to check: assume it's ours


def _take_lock(path: Path) -> bool:
    """Create a pid lock file; a lock left by a process that died is taken over."""
    import os

    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                pid = int(path.read_text().strip() or "0")
            except (OSError, ValueError):
                pid = 0
            if pid and _pid_alive(pid):
                return False
            path.unlink(missing_ok=True)       # stale: its owner is gone
    return False


def generate_track(key: str) -> bool:
    """Compose one guide track into music/<key>.m4a. Safe to call from several processes."""
    from . import music, synth

    if key == "custom" or track_ready(key):
        return True
    lock = music_dir() / f"{key}.lock"
    if not _take_lock(lock):
        return False          # another live process is composing it
    try:
        _log.info("composing guide track: %s", key)
        if key == "crystal":
            audio = synth.guide_music()          # the hand-written original
        elif key == "classic":
            audio = synth.guide_music_classic()
        elif key in GENRE_TRACKS:
            from . import beds

            style, seed, _name = GENRE_TRACKS[key]
            audio = beds.compose(style, seed, GENRE_SECONDS)
        else:
            audio = music.render(music.TRACK_BY_KEY[key])
        wav = music_dir() / f".{key}.wav"
        tmp = music_dir() / f".{key}.m4a"
        synth.write_wav(audio, wav)
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "160k",
                        "-f", "mp4", "-movflags", "+faststart", str(tmp)], check=True)
        wav.unlink(missing_ok=True)
        tmp.replace(track_path(key))
        (music_dir() / f"{key}.version").write_text(MUSIC_VERSION)
        _log.info("composed %s", key)
        return True
    finally:
        lock.unlink(missing_ok=True)


def composer_log() -> Path:
    d = paths.data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "music.log"


def request_track(key: str) -> None:
    """Ask the composer to do this track next (e.g. it was just picked in the menu)."""
    (music_dir() / "priority").write_text(key)
    start_music_background()


def compose_all() -> int:
    """Composer process main loop: every missing track, honouring priority requests."""
    import os
    import time as _time

    try:
        os.nice(10)              # stay out of the way of video playback
    except OSError:
        pass
    if not _take_lock(music_dir() / "composer.lock"):
        return 0                 # another composer is already at work
    try:
        keys = [k for k, _ in music_options() if k not in SHUFFLES and k != "custom"]
        busy_rounds = 0
        while True:
            pending = [k for k in keys if not track_ready(k)]
            if not pending:
                return 0
            pri_file = music_dir() / "priority"
            pri = pri_file.read_text().strip() if pri_file.exists() else ""
            from . import settings

            choice = settings.load().get("guide_music", "shuffle")
            order = sorted(pending, key=lambda k: (k != pri, k != choice, keys.index(k)))
            did = False
            for k in order:
                try:
                    if generate_track(k):
                        did = True
                        break
                except Exception:
                    _log.exception("could not compose %s", k)
                    keys.remove(k)          # don't retry a broken track forever
                    did = True
                    break
            if not did:                     # everything left is locked by someone else
                busy_rounds += 1
                if busy_rounds > 60:
                    return 1
                _time.sleep(5)
    finally:
        (music_dir() / "composer.lock").unlink(missing_ok=True)


def start_music_background() -> None:
    """Start the composer as its own low-priority process if any tracks are missing.

    A separate process keeps composing after TV mode closes and never competes with
    the video for Python's attention."""
    import sys

    if all(track_ready(k) for k, _ in music_options() if k not in SHUFFLES and k != "custom"):
        return
    lock = music_dir() / "composer.lock"
    if lock.exists():
        try:
            if _pid_alive(int(lock.read_text().strip() or "0")):
                return
        except (OSError, ValueError):
            pass
    log = open(composer_log(), "a")
    subprocess.Popen([sys.executable, "-m", "getsome", "compose-music"], cwd=str(paths.APP_DIR.parent),
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)


def ensure_music(force: bool = False) -> None:
    _override_music()


def ready_tracks() -> list[tuple[str, str, Path]]:
    return [(k, n, track_path(k)) for k, n in music_options() if k not in SHUFFLES and track_ready(k)]


def guide_playlist(choice: str | None = None) -> list[Path]:
    """Files to play on the guide for the chosen option (falls back to whatever is ready)."""
    from . import settings

    choice = choice or settings.load().get("guide_music", "shuffle")
    choice = {"fantasy": "crystal"}.get(choice, choice)       # name used by an earlier version
    ready = ready_tracks()
    if choice not in SHUFFLES:
        for k, _n, p in ready:
            if k == choice:
                return [p]
    pool = [p for k, _n, p in ready if k not in ("classic", "custom")
            and (choice != "shuffle_rpg" or k not in GENRE_TRACKS)] or [p for _k, _n, p in ready]
    if not pool:
        legacy = runtime_path("guide_music.wav")     # from an earlier version
        return [legacy] if legacy.exists() else []
    return pool


def track_name(path: str | None) -> str:
    if not path:
        return ""
    stem = Path(path).stem
    for k, n in music_options():
        if k == stem or Path(path).name.startswith("custom_guide_music"):
            return GENRE_TRACKS[k][2] if k in GENRE_TRACKS else (n if k == stem else "Your own music")
    return "Guide music"


def guide_music_path() -> Path:
    """One track (used by the movie-channel intermission)."""
    import random

    pl = guide_playlist()
    return random.choice(pl) if pl else runtime_path("guide_music.wav")


def ensure_upnext(force: bool = False) -> None:
    """~10 s "up next" carrier clips: black video + a generated synth jingle.

    TV mode draws the animated UP NEXT graphics live over these (it knows what's next).
    Your own clips in overrides/upnext/ are added to the rotation as-is."""
    from . import synth

    d = upnext_dir()
    d.mkdir(parents=True, exist_ok=True)
    ff = _ffmpeg()
    for i in range(UPNEXT_COUNT):
        dst = d / f"upnext_{i + 1}.mp4"
        if dst.exists() and not force:
            continue
        _log.info("generating %s", dst.name)
        wav = d / f".jingle_{i + 1}.wav"
        synth.write_wav(synth.jingle(i, UPNEXT_SECONDS), wav)
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c=black:size=320x180:rate=10:duration={UPNEXT_SECONDS}",
                        "-i", str(wav), *video_codec_args("low"), "-c:a", "aac", "-b:a", "160k",
                        "-shortest", str(dst)], check=True)
        wav.unlink()
    custom = paths.overrides_dir() / "upnext"
    if custom.is_dir():
        for f in custom.iterdir():
            if f.is_file() and not f.name.startswith("."):
                dst = d / f"custom_{f.name}"
                if not dst.exists() or f.stat().st_mtime > dst.stat().st_mtime:
                    shutil.copyfile(f, dst)


def is_generated_upnext(path: str) -> int | None:
    """Style number (1..n) if path is one of our generated up-next clips."""
    import re

    m = re.search(r"/upnext/upnext_(\d+)\.mp4$", path)
    return int(m.group(1)) if m else None


def _make(name: str, force: bool, cmd: list[str]) -> None:
    dst = runtime_path(name)
    override = paths.overrides_dir() / name
    if override.exists():
        if force or not dst.exists() or override.stat().st_mtime > dst.stat().st_mtime:
            shutil.copyfile(override, dst)
        return
    if dst.exists() and not force:
        return
    _log.info("generating %s", name)
    subprocess.run([*cmd, str(dst)], check=True)
