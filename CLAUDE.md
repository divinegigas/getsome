# CLAUDE.md: Neon Cable 42 project rules

The product is called **Neon Cable 42** (`paths.APP_NAME`); "GetSome" was a placeholder. Internals
(package `getsome`, `getsome.sh`, `~/.local/share/getsome`, `~/.config/getsome`, repo) keep the old
name until the AppImage move (see HANDOFF). Never show "GetSome" in anything the owner sees.

Read `HANDOFF.md` first for the current state, known issues and next steps.

## The brief
A 90s-cable-style TV experience from the owner's **local** media library, for personal use.
It is built on **FieldStation42** (`vendor/FieldStation42`, a git submodule, MPL-2.0), reusing its
scheduling and playback engine and adding a proper GUI and polish.
- **Platform:** Fedora first. Bazzite (immutable) later, via a single-file AppImage. Offline, local files only, no accounts.
- **Setup window:** making channels must be as easy as possible. Sensible defaults, templates, folder pickers instead of typed paths. Sequential (remembers its place, staggered start points) or shuffle, per channel and per show. Time blocks, broadcast hours, sign-off, and off-air modes (static, bars, infomercials). Reads TinyMediaManager `.nfo` files. "Launch straight into TV" is OFF by default.
- **TV mode:** fullscreen, live-feeling (tuning in at 12:38 lands about 38 min in), only the current channel decodes, big neon channel numbers, a static-and-hiss transition that covers loading, a guide channel, SMPTE bars with a 1 kHz tone off-air, and a user override folder for custom assets.
- **Controls:** USB IR remotes act as keyboards (PgUp/PgDn, digits), and keys are remappable.
- **Stretch:** commercials and bumpers only at real break points (chapters, then ffmpeg black detection, with toggles), **never at random intervals**; commercial and bumper pool folders per channel.
- **Look:** an 80s vision of the future (synthwave, neon magenta and cyan, chrome). Guide music: RPG-flavoured synth with some metal.

## Hard rules
- **Never modify FS42 core.** All FS42 access goes through `getsome/fs42.py` (queries) and `station_42.py` subprocesses (builds). If FS42 lacks something, work around it on our side and document it in HANDOFF.md.
- **Never reproduce copyrighted works**: no melodies from game or film soundtracks, no logos or characters. Music, visuals and jingles are original and generated in code (`synth.py`, `music.py`, `assets_gen.py`, `tv/bumpers.py`).
- **Fonts**: bundled SIL OFL only (Monoton, Tilt Neon). Assets are generated, never downloaded at runtime.
- **Never touch the user's media**: channels use symlink folders under the FS42 work dir.
- FS42 returns exit code 0 on failure, so check its output (see `builder._run`).
- Keep sequential shows' positions across rebuilds: the builder's line-up pass, and `built_hash` / `start_pending`.
- When a setting or cached format changes, bump its version (`library.CACHE_VERSION`, `assets_gen.MUSIC_VERSION`, `ads.ADS_VERSION`) so existing installs regenerate.
- The owner is not deeply technical. Give exact copy-paste commands (`cd ~/getsome` first), and explain *what changed and what to test* in plain terms.

## Tech stack
Python ≥3.10 · PySide6 / Qt 6 (Widgets, QOpenGLWidget) · python-mpv (libmpv render API) · ffmpeg / ffprobe · numpy (synth) · FieldStation42 (SQLite schedules).
Dependencies are in `requirements.txt` (FS42's own requirements minus the Pi-only evdev and pyserial).

## Run
```bash
sudo dnf install mpv-libs ffmpeg-free python3     # Fedora
./getsome.sh            # setup window (first run creates .venv)
./getsome.sh tv         # TV mode (--windowed, --channel N)
./getsome.sh demo | list | build [NAME…] | upkeep | scan FOLDER | compose-music
```
- **User data:** `~/.local/share/getsome/` (`channels.json`, `library.json`, `movies.json`, `fs42/` work dir with `confs/`, `runtime/`, `catalog/`, `overrides/`, `logs/`).
- **Settings and keymap:** `~/.config/getsome/settings.json` and `keymap.json`.
- **For isolated testing:** `GETSOME_DATA_DIR=… XDG_CONFIG_HOME=…`. Headless: `Xvfb :99` + `DISPLAY=:99` (in the sandbox, llvmpipe sometimes renders mpv black, and Xvfb sometimes dies between long jobs; restart it).

## Code map
```
getsome/__main__.py      CLI
getsome/paths.py         XDG locations
getsome/fs42.py          FS42 adapter (channels, segment_at, programmes, titles, builds)
getsome/model.py         Channel / ShowRef / Block (time blocks) / templates; channels.json
getsome/channel_writer.py  Channel -> plan (tags) -> FS42 conf with hourly slots + symlink folders
getsome/builder.py       build + sequence line-up with the clock
getsome/upkeep.py        keep schedules ahead of the clock (extend / rebuild expired), shared lock
getsome/cleanup.py       find/fix leftover, broken, empty, duplicate-number channels
getsome/artwork.py       finds poster/fanart/logo/episode-thumb art next to shows and films
getsome/steam.py         running under Steam (Big Picture): env fixes, X11 window, launch log
getsome/ads.py           commercial pool: invented ads, IDs, show promos, own folder; per-channel links
getsome/updater.py       Settings > Check for updates (git pull + restart)
getsome/media_index.py   TV/movie NFO parsing, scans, link folders
getsome/library.py       library folders + scan caches
getsome/assets_gen.py    runtime media, up-next clips, guide-music composer process
getsome/synth.py, music.py   synthesizer + composer (10 original guide tracks)
getsome/beds.py          short original beds: jrpg, metal, rock, punk, reggae, ska (cards, ads, intermissions)
getsome/gui/             setup window (theme, main_window, channel_editor, schedule_editor, dialogs, workers)
getsome/tv/              TV mode (app, mpv_widget, overlays, bumpers, intermissions, ads_paint, art_paint, guide, menu, awake, hiss, keys)
```

## Owner's setup
Fedora desktop (KDE Plasma) with an AMD graphics card. TV and movie libraries on an external drive
(under `/run/media/…`, so it can be unplugged), organised by TinyMediaManager: `Show (Year)/Season N/`
with `.nfo` files and artwork. The repo is cloned at `~/getsome`.
Privacy: don't record personal details (paths, user names, hardware, library contents) in these docs.

## Git
Work on branch `claude/cable-tv-gui-review-gdkexf` (the only branch) and push there. Commit in logical steps
with clear, plain messages: they are the "What's new" list the owner sees in Settings → Check for updates.

**Updater and history rewrites** (`updater.py`): Check for updates fetches, and if the local HEAD is an
ancestor of the remote branch it does `pull --ff-only`. If not (the remote history was rewritten, e.g.
squashed to remove old personal details), it `reset --hard`s to the remote, expires the reflog and runs
`gc --prune=now`, so old versions disappear from the owner's computer too. Local edits to tracked files
block both paths. A history rewrite is therefore safe to push **only after** the owner has installed an
updater that has this (added 19 Nov 2026); never rewrite history otherwise, and keep a single branch.
