# Step 0 — FieldStation42 review and proposed plan

Reviewed: `shane-mason/FieldStation42`, `develop` @ `dfa107e` (2026-11-15).

## 1. FS42 at a glance

| Area | Finding |
|---|---|
| License | **MPL-2.0** (file-level copyleft). We can use it unmodified from our own code under any license. Any FS42 *file* we modify stays MPL and its source has to ship with it. Shipping FS42 inside an AppImage is fine if we include its LICENSE. |
| Language/deps | Python ≥3.10, mpv (driven over JSON IPC), ffmpeg/ffprobe, SQLite, PySide6 (already a dependency), FastAPI web console on :4242, Textual TUI |
| Config | One JSON file per channel in `confs/`, plus an optional `confs/main_config.json`. Schema is in `fs42/station_config_schema.json`. All paths are **relative to the working directory**, so we can point FS42 at our own data dir just by setting `cwd`. |
| Scheduling | Hourly slots per weekday (`"0"`–`"23"`), `day_templates`, `slot_overrides`, `tag_overrides`, half-hour splits via tag arrays, `random_tags`, `min/max_duration`, `schedule_increment`/`schedule_offset`, marathons, date-ranged configs, `continued` slots |
| Playback engine | `field_player.py` runs one mpv process and polls a socket file for tune commands. `PlayPointQuery.get_play_point(network, now)` returns the file and offset for "now", so the live-feeling mid-show tune-in is already there. |
| Schedule upkeep | Built ahead with `station_42.py -w/-d/-m`. `LiveScheduleAgent` extends schedules automatically in the background. |
| Guide | A `guide` network type rendered with Tk (`guide_tk.py`), plus a web guide. It already has now/next data (`guide_builder.py`). |
| Breaks | Chapter markers are scanned automatically at catalog build. Break points come from ffmpeg `blackdetect` (`-b <dir>`, labelled "VERY experimental"), cached in SQLite. Order of preference: chapters, then black-detect, then **even splits**. Strategies: `standard`, `center`, `end`. Commercial and bump dirs can be set per channel, per slot, and per show (`tag_overrides`). |
| NFO | `nfo_agent.py` parses Kodi-style `episodedetails`/`tvshow`/`movie` NFOs (the format TinyMediaManager writes), but only for the now-playing overlay and ratings. Guide titles come from filenames. |
| Remote/IR | Flirc USB support (keyboard-style), an evdev remote script for the Pi, and a web remote. No GUI for remapping keys. |

## 2. Your features vs. FS42

✅ = FS42 has it · 🟡 = engine supports it, needs GUI or glue · 🔨 = we build it

**Setup GUI**
| Feature | Status | Notes |
|---|---|---|
| Create/edit channels, name, number, folders | 🟡 | FS42 has a web editor and TUI, but they mostly edit JSON. Ours will be native, template-driven, with folder pickers. |
| Channel templates (marathon, shuffle, 24h) | 🔨 | We generate the FS42 JSON from the templates. |
| Sequential with remembered position | ✅ | `sequence` slots, position stored in the DB |
| Multi-series sequential channel | ✅/🟡 | One sequence per series, or `sequence_strategy: random_show` across child folders |
| Staggered start points | 🟡 | FS42 only takes a 0–1 fraction (`sequence_start`) or `reset_by_episode_path`. The GUI will let you pick an episode and convert that for you. |
| Shuffle that fits the slot | ✅ | Random tag selection plus `max_duration` |
| Mode per channel **and** per show | 🟡 | Maps to slot-level and `tag_overrides` settings |
| Time blocks | ✅/🟡 | Day templates and hourly slots, with 30-minute granularity via splits. The GUI will be a weekly grid. |
| Broadcast hours / sign-off | ✅ | Omitted hours are off-air, plus `event: signoff`, `sign_off_video`, `off_air_video` |
| Off-air: static / off-air screen / infomercials | 🟡 | Static and bars use generated assets. Infomercials are an ordinary commercial-free tag scheduled in the off-hours. |
| Read TMM .nfo for titles | 🟡 | FS42's parser can be reused. We add NFO titles to the guide and the show browser. |
| Write config + rebuild schedule | 🟡 | We write the JSON and run `station_42.py -r` / `-x` / `-w` as subprocesses with progress shown. |
| "Launch straight into TV mode" setting | 🔨 | |

**TV mode**
| Feature | Status | Notes |
|---|---|---|
| Fullscreen, tune in mid-schedule at the right point | ✅ | Via `PlayPointQuery` |
| Only the current channel decodes | ✅ | Single player |
| Big neon channel numbers | 🔨 | FS42 only has plain mpv OSD text |
| Static with hiss covering loading | 🟡 | FS42 fakes this with mpv filters and a `static.mp4`, and doesn't cover buffering. We build an overlay that stays up until the first frame renders. |
| Guide channel, now/next | ✅/🔨 | The data exists. We draw our own Qt guide that matches the look. |
| Generated neon font, static, SMPTE bars + 1 kHz | 🔨 | mpv can generate bars and tone itself (`av://lavfi:smptehdbars` + `sine=1000`), so no media files are needed. |
| User override folder | 🔨 | |

**Controls**
| Feature | Status |
|---|---|
| USB IR remotes (PgUp/PgDn, digits) | 🟡 Basic keys exist. We handle them in the Qt window. |
| Remap screen ("press a button to assign") | 🔨 |

**Stretch**
| Feature | Status | Notes |
|---|---|---|
| Breaks at chapter markers | ✅ | |
| ffmpeg black-frame detection | ✅ | Experimental, run per folder |
| Toggle detection globally / per show | 🟡 | We decide which show folders get `-b` and set `--skip_chapter_scan` |
| **Never at random intervals** | ⚠️ gap | When a file has no break points, FS42 **splits it evenly**, which cuts mid-scene. See decision 2. |
| Commercial/bumper pools per channel | ✅/🟡 | `commercial_dir`/`bump_dir` exist. The GUI adds folder flags and assignment. |

## 3. Proposed architecture

```
getsome (our app, Python)
├── setup/     PySide6 Widgets GUI: channels, templates, weekly grid, pools, settings
├── model/     our own project file (YAML/JSON) -> generator -> FS42 confs/*.json
├── tv/        PySide6 fullscreen window, libmpv embedded (render API, one instance)
│              Qt overlays: neon numbers, GLSL static + generated hiss, guide
│              tuner: asks FS42 PlayPointQuery for (file, offset) and loads it in our mpv
├── input/     key map + "press to assign" capture
└── vendor/FieldStation42   git submodule, pinned, UNMODIFIED
user data: ~/.local/share/getsome/{confs,runtime,catalog}   (FS42 runs with cwd here)
```

- **Our own front end instead of `field_player.py`.** FS42's player is a polling loop around an external mpv window, and its overlays run as separate processes. Drawing neon numbers and static over the video, and hiding the buffering, works much better in one Qt window with mpv embedded. We still use FS42 for everything about *what* plays and *where in the file*. That uses a small FS42 API surface (`PlayPointQuery`, `StationManager`, the guide/schedule queries), and I'll keep it behind one adapter module so upstream updates only touch that file.
- **The generator is one-way.** The GUI's model is the source of truth and FS42 JSON is build output. If you need something the GUI doesn't cover, a per-channel "raw FS42 overrides" JSON box gets merged in.
- **Stack:** Python 3.12, PySide6/Qt 6, python-mpv (libmpv), ffmpeg. The font is OFL, e.g. *Monoton* or *Tilt Neon*. VCR OSD Mono is excluded because its license isn't open.
- **Packaging:** an AppImage containing CPython, PySide6, libmpv, ffmpeg and FS42, built in a container for glibc compatibility, using the static AppImage runtime so it needs no libfuse2 on Bazzite. GPU/GL libraries come from the host and aren't bundled. If AppImage plus libmpv turns out fragile on Bazzite, the fallback is a single-file `.flatpak` bundle.

## 4. Phases (each one ends runnable)

| # | Phase | What you can run at the end |
|---|---|---|
| 1 | **Skeleton + TV mode MVP:** submodule, data dir, FS42 adapter, Qt fullscreen window with libmpv, live tune-in, PgUp/PgDn and digit tuning, neon numbers, static+hiss transition. Ships with a sample channel config. | `./getsome tv` flips through channels built from a sample config pointing at your folders |
| 2 | **AppImage early.** Prove the libmpv/Qt bundle works before the app gets big. | `GetSome-x86_64.AppImage` launches phase 1 on Fedora and Bazzite |
| 3 | **Setup GUI core:** channel list, templates, folder browser, show detection with NFO titles, per-channel and per-show sequential/shuffle, Build button with progress, Watch button | Make a channel in the GUI and watch it |
| 4 | **Scheduling:** weekly block grid, broadcast hours/sign-off, off-air mode (static / bars+tone / infomercial folder), multi-series channels with staggered starts | Saturday-morning cartoons and a channel that signs off |
| 5 | **Guide + polish:** Qt guide channel (now/next), user override folder, settings incl. "launch into TV mode" (off by default), remap screen | Full feature set apart from the stretch goals |
| 6 | **Stretch, breaks:** commercial/bumper pool flags per folder and per-channel assignment, chapter → black-detect pipeline, global and per-show toggles, "never random" fallback | Commercials only at real break points |

## 5. Decisions needed

1. **Integration approach:** own Qt front end over an unmodified FS42 submodule *(recommended)*, or keep FS42's `field_player.py` and add our features around it (less code, but neon numbers and static over video would be clunky).
2. **Files with no break points:** (a) automatically use `end` (one block of commercials at the end) for any show whose files lack markers, with no FS42 change *(recommended to start)*; (b) also offer a small upstream PR adding a "require break points, else end" option.
3. **Testing:** I can build and run headless here and screenshot the Qt window. Real-display, audio, IR remote and Bazzite testing will need you at each phase.
