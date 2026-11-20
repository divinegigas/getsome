# Neon Cable 42

_(Working name was GetSome: the code package, the `getsome.sh` launcher and the data folders still use
that name for now.)_

A 90s-cable-style TV experience from your local media library, built on
[FieldStation42](https://github.com/shane-mason/FieldStation42) (included
unmodified as a git submodule in `vendor/`). Linux only, offline, no accounts.

**Status:** TV mode and the setup window work. Time blocks and broadcast hours,
the guide channel and the AppImage are next. See `docs/STEP0_REVIEW_AND_PLAN.md`.

## Running it (Fedora, from source)

```bash
sudo dnf install mpv-libs ffmpeg-free python3     # ffmpeg from RPM Fusion also works
git clone --recurse-submodules <this repo> getsome && cd getsome

./getsome.sh           # opens the setup window (first run sets up .venv)
```

In the setup window: **+ New channel** → pick a style → name and number →
tick shows from your library → **Build**. Then **▶ Watch TV**.

* **Series Marathon** plays shows in order and remembers its place. Pick
  a different *Start at* episode per show, e.g. TNG from S03E01 and DS9 from S01E01.
* **Shuffle Classics** plays any episode of any show.
* **24-Hour Mix**: set each show to *In order* or *Shuffle*. In-order shows get their
  own hours; shuffled shows share the rest.

Changes are saved as you make them; a channel shows **needs build** until you build it.
Building keeps every in-order show on the episode it was on.

**Up-next bumpers:** each channel can play an animated ~10 s "UP NEXT" clip after every
show (tick-box in the editor, on by default). There are three looks: grid run, VHS glitch and neon sign. Each
has its own generated synth jingle and names the real next show, episode and start time.

**Movie channels:** add your movie folders in **Settings** (TinyMediaManager / Kodi
`.nfo` files are read for title, year, genre, rating and runtime), then make a channel
with the **Movie Channel** style. Pick films by hand, or filter by genre and decade and
**Tick all shown**. Movies start on the hour and half hour, and the gap shows a live
**intermission** countdown with music. Any channel can use these start times (setting
**Start times**), or stay back to back.

**No NFO files? No problem.** “Add any folder…” in the show or movie picker takes any
folder of videos, such as home movies, old tapes or a folder of commercials. Files play in name order
(or by S01E02 numbers if the names have them), and the banner shows the file name.

**TV guide:** channel 2 (change or turn off in Settings) is a scrolling guide of what's on
every channel now and over the next 90 minutes. Its background music is ten original
RPG-style synth and metal tracks, composed in code: Crystal Overture, Skyship Chase, Village at Dusk,
Clockwork Tower, Desert Caravan, The Tyrant Awakens, Moonlit Sea, Neon Kingdom, Ancient
Ruins and Airship Over the Clouds. Press **MENU** on the guide to pick a track or **Shuffle all**.
The first run composes them in the background (about 2 minutes, in a separate low-priority process
that keeps going after TV mode closes); picking a track that isn't ready yet moves it to the front of
the queue and switches to it when it's done. `./getsome.sh compose-music` composes them in the
foreground; the log is in `~/.local/share/getsome/logs/music.log`.

**Films on TV channels:** any TV channel can take films too (**+ Add movies**), for example a
Star Wars channel with the films and the series. The films act like one more show in the
rotation: **In order (by year)** or **Shuffle**.

**Picking shows:** the show picker has a genre filter and **Tick all shown**. Pointing
“Add any folder…” at a folder of shows offers to add each show separately.

Other commands: `./getsome.sh tv` (straight to TV), `./getsome.sh demo` (fake demo
library), `./getsome.sh list`, `./getsome.sh build [NAME…]`.

On Bazzite, use a Fedora distrobox for now (`distrobox create -i fedora:latest`).
Phase 2 produces a single AppImage that needs nothing installed.

### Command line (optional)

TinyMediaManager / Kodi-style libraries are understood: episode order comes from
the `.nfo` files (season/episode), show names from `tvshow.nfo`. Specials
(season 0), extras folders and trailers are left out.

```bash
# see what GetSome finds first (changes nothing)
./getsome.sh scan ~/Videos/TV


# a 24-hour shuffle channel from one or more show folders
./getsome.sh add-channel 5 "Toon Zone" ~/Videos/TV/Animaniacs ~/Videos/TV/Tiny\ Toons

# play each show in order and remember the place; hours rotate between the shows
./getsome.sh add-channel 8 "Trek" ~/Videos/TV/Star\ Trek* --sequential

./getsome.sh list            # channels and how far ahead they are scheduled
./getsome.sh remove-channel "Trek"
./getsome.sh build           # rescan folders after adding episodes
```

Show folders can be anywhere, including removable drives. Channel configs go in
`~/.local/share/getsome/fs42/` and link to your episodes; nothing in your library
is modified. `build` re-scans the show folders, so run it after adding episodes.

## TV mode controls

USB IR remotes act as keyboards, so the usual buttons work out of the box.

| Action | Keys |
|---|---|
| Channel up / down | Page Up / Page Down, ↑ / ↓, + / − |
| Tune directly | number keys (auto-tunes after a short pause, or press Enter) |
| Previous channel | Backspace |
| Info banner (now / next) | I |
| Menu: audio track & subtitles (on the guide: music) | Menu (remote), O or Tab. ▲▼ choose, ◂ ▸ change, OK closes |
| Mute / volume | M, [ and ] (or the remote's volume keys) |
| Fullscreen toggle | F |
| Quit | Esc or Q |

To remap keys, edit `~/.config/getsome/keymap.json`, e.g.
`{"channel_up": ["PgUp", "Media Next"]}`. The "press a button to assign" screen arrives in phase 5.

## Settings

Most settings are in the setup window under **Settings**. They live in `~/.config/getsome/settings.json`:

| Key | Default | |
|---|---|---|
| `hwdec` | `"no"` | Set to `"auto-safe"` for GPU decoding (useful for 4K/HEVC). If the picture goes black, set it back. |
| `volume` / `hiss_volume` | 100 / 35 | |
| `remember_last_channel` | true | |
| `launch_into_tv` | false | Skip the setup window and go straight to TV (`./getsome.sh setup` still opens it) |
| `library_folders` | [] | Folders holding your show folders (set in the setup window) |

## Custom visuals

Everything visual is generated in code: neon numbers using the bundled OFL fonts
*Monoton* and *Tilt Neon*, procedural static and hiss, and SMPTE bars with a 1 kHz tone.
To replace a stock asset, put a file with the same name in
`~/.local/share/getsome/overrides/`. Currently supported: `bars_tone.mp4`, `static.mp4`,
`signoff.mp4`, `standby.png`, `brb.png`, `offair.png`, `hiss.wav` and `guide_music.mp3`
(or .ogg/.flac/.wav). Clips you put in `overrides/upnext/` join the up-next rotation as-is. Then run
`./getsome.sh build`.

## How it fits together

```
getsome/fs42.py          the only module that touches FieldStation42 internals
getsome/model.py         channel list (channels.json) - the source of truth
getsome/channel_writer.py  turns a channel into FS42 config JSON
getsome/builder.py       runs FS42 builds and lines up in-order shows with the clock
getsome/library.py       library folders + cached scan;  media_index.py reads NFOs
getsome/gui/             setup window
getsome/assets_gen.py    renders runtime media with ffmpeg
getsome/tv/              Qt window + embedded libmpv, overlays, hiss, key map
vendor/FieldStation42    upstream, unmodified (MPL-2.0)
```

TV mode asks FieldStation42 what should be on each channel at this moment and
loads that file at the matching position, so tuning in at 12:38 starts about
38 minutes into the show. Only the channel you're watching is decoded.

## Licenses

GetSome's own code: TBD by the owner. FieldStation42 is MPL-2.0 (see
`vendor/FieldStation42/LICENSE`). The fonts are SIL OFL 1.1 (see `getsome/assets/fonts/`).
