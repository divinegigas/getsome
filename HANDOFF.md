# Neon Cable 42 (formerly GetSome): handoff notes

**Name:** the owner renamed the product to **Neon Cable 42**. Every visible string uses
`paths.APP_NAME` or the literal name (window titles, logo, guide, ads, teletext "NC42 TEXT", app-menu
entry, notifications, icon shows "42"). Internal names stay `getsome`: the package, `getsome.sh`,
env vars `GETSOME_*`, `~/.local/share/getsome` and `~/.config/getsome` (XCable: `xcable`). **Decided at the
AppImage move (24 Nov): no rename.** FS42 schedules and catalogs store absolute paths under the data folder,
so moving it would force full rebuilds; keeping it means the AppImage and a git checkout share the same
channels and settings with no migration (the owner keeps the old git install until happy with the AppImage).

_State at the end of the third session (19 Nov 2026), branch `claude/cable-tv-gui-review-gdkexf`. A fresh
session should start from the prompt in `docs/NEXT_SESSION_PROMPT.md`._

GetSome is a 90s-cable-style TV for a local Linux media library, built on an
**unmodified** FieldStation42 (FS42) submodule. There's a native setup window plus a fullscreen
TV mode. See `CLAUDE.md` for the brief and the ground rules, and
`docs/STEP0_REVIEW_AND_PLAN.md` for the original review and plan.

## Phase status

| Original plan phase | Status |
|---|---|
| 1 TV-mode MVP | ✅ done, confirmed by the owner on Fedora (picture, sound, static, live tune-in) |
| 2 AppImage for Bazzite | 🔨 **built 24 Nov** (two files, GitHub Releases, own updater); see "AppImages" below · **waiting on the owner's first test** |
| 3 Setup GUI | ✅ done |
| 4 Time blocks + broadcast hours + off-air modes | ✅ done · not yet reported on by the owner |
| 5 Guide, overrides, settings, key-remap screen | ✅ guide, overrides and settings · ❌ remap screen (JSON file only) |
| 6 Stretch: commercials at real break points | ✅ v2: cards (promos, trivia, lineups, on this day), five-beat product ads, six music styles · owner liked promo cards, found v1 product ads weak; **v2 not yet reported on** |
| Session 3 | ✅ Steam Big Picture fix (confirmed), restricted channels with PIN + lock schedule, artwork cards + transitions |
| Added along the way | ✅ movie channels, films in TV channels, up-next bumpers, 5 intermission looks, audio/subtitle menu, 20 guide tracks (RPG + metal/rock/punk/reggae/ska), guide preview window + sound toggle, exit menu, keep-awake, every-episode rotation, clean-up tool, new-episode check, usage dots in pickers, one-click updater, app menu + desktop shortcut, rename to Neon Cable 42 |

## What's built (and working)

**Setup window** (`./getsome.sh` → `getsome/gui/`), styled as an 80s vision of the future (synthwave, neon, chrome):
- Channel list on the left (with a draggable divider) and the editor on the right. Edits auto-save to `~/.local/share/getsome/channels.json`. A channel shows "needs build" until it's built.
- Styles: **Series Marathon** (in order), **Shuffle Classics**, **24-Hour Mix** (order chosen per show), **Movie Channel**.
- Per show: order (channel default / in order / shuffle) and **Start at**, a searchable ▸ episode picker.
- **Switch shows** every episode, or every 1–6 h (rotation between in-order shows and the shuffle pool).
  Also per time block.
- **Start times**: back to back / on the half hour / on the hour. Timed channels fill the gap with a live intermission screen.
- **Up-next bumpers** checkbox (back-to-back channels only).
- Show picker: search, genre filter (from `tvshow.nfo`), tick/untick all shown, "Add any folder…". If that folder holds several shows, it offers to add them separately.
- Movie picker: search, genre and decade filters, tick all shown, add any folder (no NFO needed).
- TV channels can include films (**+ Add movies**). The films rotate as one "Movies" entry, in release order or shuffled.
- **Time blocks & broadcast hours** (button on each channel → "THE WEEK", `gui/schedule_editor.py`):
  a 7-day grid laid out like a TV listing (columns are broadcast days, 6 AM to 6 AM). Drag across it
  to make a block, click a block to edit it. A block has days (presets: every day / weekdays / weekends),
  whole-hour start and end (may run past midnight), its own shows and/or films, play order, per-show
  order and start episode, and "switch shows every N hours". Overlapping blocks are refused.
  Hours outside blocks play the channel's regular show list; a channel with no regular shows is
  off the air outside its blocks.
- **Broadcast hours**: "Sign off at night", on the air from/to, optional "Good night" sign-off clip,
  and an off-air mode: bars + 1 kHz tone, static, or a folder of infomercials (shown as
  "Paid Programming", no sign-off). Blocks always air, even outside broadcast hours.
- **Schedule upkeep** (`upkeep.py`): when the setup window opens, when TV mode starts and hourly while it
  runs, and on `getsome upkeep`. Extends schedules ending within 3 days; **rebuilds** ones that already
  ended, with in-order shows resuming after the last episode the old schedule aired.
- **Settings → Updates → "Check for updates"** (`updater.py`): `git fetch`, lists the new commit
  messages, `git pull --ff-only`, refreshes the FS42 submodule, then restarts through `getsome.sh`
  (which installs new Python packages if `requirements.txt` changed). Refuses (with a plain message)
  if tracked program files were edited locally. Never prompts for a password (`GIT_TERMINAL_PROMPT=0`).
  **Commit messages are what the owner sees as "What's new"**, so keep them plain and readable.
- **Show/movie pickers show where things already play**: a green dot = on a channel (hover lists the
  channels and time blocks), yellow = not on any channel; a filter "Everything / Not on any channel yet /
  Already on a channel" (`dialogs.library_usage`, `mark_usage`).
- **Check for new episodes** (channel list button; also a quiet check after upkeep when the setup window
  opens, which turns the button pink: "New episodes found (N shows)…"). `new_episodes.py` compares each
  show folder used on a channel with the real files that channel has linked under its catalog folder
  and offers to rebuild just those channels (`BuildDialog`); the picker's cached counts are refreshed.
  A show is only re-read when its folder or a season folder's mtime changed since the last clean check
  (`~/.local/share/getsome/episode_check.json`). Rebuilds already re-read folders, so in-order shows keep
  their place and new episodes slot in by number. New *shows* still come from Rescan in "Add shows".
- "Every episode" folders now run `lcm` of the show lengths rounds (capped at 30 000 links) so a
  shorter show never repeats an episode where the folder loops.
- **Settings scrolls** (it outgrew a 1080p screen, which hid the App menu button at the bottom); Save /
  Cancel stay fixed. **Settings → Shortcuts** is now first: app menu entry and a **desktop shortcut**
  (`desktop.install_desktop_shortcut`, executable + `gio` trusted). Menu refresh runs
  `update-desktop-database`, `kbuildsycoca6/5` (KDE Plasma caches its menu) and `xdg-desktop-menu`.
  The setup window offers the app-menu entry once on first open (`settings.menu_offered`).
- **App menu entry** (Settings → Shortcuts, or `getsome add-to-menu` / `remove-from-menu`; `desktop.py`):
  `~/.local/share/applications/getsome.desktop` in Sound & Video, with right-click actions Watch TV /
  Channel setup and a neon TV icon drawn in code (`~/.local/share/getsome/icon/getsome.png`). Both
  windows call `setDesktopFileName("getsome")` so GNOME groups them under the icon; the entry is
  refreshed on start if the checkout moved. `getsome.sh` shows a desktop notification on missing
  ffmpeg/libmpv when there's no terminal.
- Settings: TV and movie library folders, launch straight into TV (off by default), guide channel number (0 = off), guide music, hardware decoding, volumes.
- Build dialog: progress bar, step text and a details log. Real FS42 errors are surfaced.

**TV mode** (`./getsome.sh tv`, `getsome/tv/`):
- One embedded libmpv instance (render API in a `QOpenGLWidget`), so only the current channel decodes.
- Live tune-in at the correct offset. Drift is corrected after loading, and a watchdog resyncs if playback stalls or drifts more than 3 s. The segment timer is a `PreciseTimer`.
- Procedural static with a hiss (the hiss is a second, audio-only mpv). Neon channel numbers with a CRT colour split and etched scanlines.
- HUD banner: channel · show · episode code and NFO title (or a movie's year, rating, runtime and genre) · up next.
- **Guide channel** (default 2): a scrolling grid for every channel, a rotating "NOW ON" box, a clock, and "♪ now playing".
- **Up-next bumpers**: about 10 s each, three animated styles drawn live over generated jingle clips, so they always name the real next show.
- **Intermission** on timed channels: next feature, countdown, what's later, and music.
- **MENU** key: audio track and subtitle picker; choices persist as preferred languages. On the guide, MENU picks the music.
- Keys: PgUp/PgDn, ↑/↓, number keys, L (previous channel), I (info), M, [ and ], F, Menu/O/Tab.
  **BACK** (Backspace, Back, Esc, Q or Stop) opens the **exit menu**: Back to TV / Quit to setup /
  Quit to desktop (closes after 20 s untouched). Remappable in `~/.config/getsome/keymap.json`
  (action `back`). Buttons with no action are logged as "unassigned key" to help map IR remotes.
- Exit menu plumbing: "Quit to setup" just ends TV mode when the setup window started it
  (`GETSOME_FROM_SETUP=1`; the window comes back to the front); when TV was started on its own it
  `execv`s `getsome setup`. "Quit to desktop" exits with code 10 (`EXIT_TO_DESKTOP`) and a setup
  window that started TV closes too.
- **Keeps the computer awake** while TV mode is on (`tv/awake.py`, D-Bus via `jeepney`): GNOME
  SessionManager (idle + suspend), freedesktop ScreenSaver, KDE PowerManagement, and logind
  `idle:sleep`. All are released automatically if the process dies. Settings: "Keep the computer awake
  while watching TV … until no button is pressed for N hours" (default 4, like a TV's auto-off).
- **Guide preview window** (top right of the guide): a second, muted, audio-less libmpv instance
  (`MpvWidget.enable_preview`) renders into a 640x360 FBO in the same GL context; the frame is read back
  (`preview_image`) and painted by `GuideRenderer._preview`. It tunes a channel live every N seconds
  (settings `guide_preview`, `guide_preview_seconds` = 8, `guide_preview_step` = 1/2/3/5), starting
  with the channel you came from, with a static burst between channels. It is stopped (`stop_preview`)
  as soon as you leave the guide, so outside the guide only the current channel decodes. The guide
  grid now starts lower (shorter), with smaller rows, and scrolls at 22 px/s (was 38).
- **Clean up channels…** (channel list, and Settings → Maintenance; `cleanup.py`): finds leftover FS42
  configs not in the channel list (the cause of a "second channel 9" after a failed first build and a
  rename), channels whose build never finished, empty channels, and duplicate/guide-clashing numbers;
  fixes the ticked ones. Builds now drop leftover configs on the same number first, and a failed
  *first* build removes its half-made config.
- **"Switch shows every episode"** (`rotate_hours = 0`, channel or block): FS42 has no strict
  per-episode rotation (only random picks or splitting each hour by the clock), so `channel_writer._link_alt`
  writes one interleaved folder per context (`Alternating`, `Alternating_<block id>`) with links
  `NNNNN Show - S01E02.ext` (one entry of each show in turn; shorter lists wrap round; shuffled entries
  are shuffled at build time) and FS42 plays it as a single sequence. Before each rebuild
  `builder._alt_starts` finds where every in-order show is up to (start_pending pick → its old hourly
  sequence → the old interleaved folder → its start episode) and whose turn it is (`__first__`), and
  the folder is written starting there; the sequence then starts at index 0. Show tags used only inside
  an interleaved folder aren't linked separately (`Plan.used`). `fs42.display_title` strips the
  `NNNNN ` prefix; films in an interleaved folder are recognised by having no SxxEyy code.
- **Guide music in every style**: ten full-length (150 s) genre tracks (`assets_gen.GENRE_TRACKS`, two
  each of metal, rock, punk, reggae, ska) composed by the background composer with `beds.compose`.
  "Shuffle all (every style)" mixes them with the ten RPG tracks; "Shuffle RPG tracks only" keeps the old
  rotation. Long pieces change section every 8 bars (new chords + motifs, same key/tempo) with a drum
  breakdown in the third section.
- **Music beds** (`beds.py`): original short pieces in six styles: jrpg, metal, rock, punk, reggae
  (one-drop, skank, melodica) and ska (upstrokes, walking bass, horn stabs), each with its own groove and
  a seeded motif lead. Used by every card and product ad (each clip a different style) and, as 60 s
  loops in `runtime/beds/`, by intermissions (picked per gap from beds + the guide's RPG tracks).
- **Commercial pool v2** (`ADS_VERSION 4`): mostly **cards** now: show promos, **"Did you know?"
  trivia** built only from the owner's NFOs (premiere date + network, cast roles, seasons and years from
  episode `<aired>`, first episode, most frequent director, longest season, episode count; films:
  director, tagline, cast, studio), **"Tonight on N" lineups** (live from the schedule) and **"On this
  day / This week in TV history"** (episodes first aired on today's date, from `cards.json`'s
  `onthisday` table). Product ads are proper five-beat commercials (hook, reveal, selling points,
  testimonial with a cartoon face, price slam + call to action); Settings → Commercial breaks →
  "Fake product ads: A few (5 per channel, default) / Lots / None". Card carrier clips are
  `runtime/ads/card_<genre>_<15|20>_<n>.mp4`; per-channel links `card_*` + `cards.json`
  (old `promo_*` + `promos.json` from v1 still play until a rebuild).
- **Commercials** (per channel, off by default; editor row "Commercial breaks"): `ads.py` + `tv/ads_paint.py`.
  * Pool: 14 invented 90s/2000s products (food, soda, tech, kids, local, "hero" looks), channel IDs
    (2–10 s), one promo per show (title/year/genres/plot from `tvshow.nfo`, local `poster.jpg` /
    `fanart.jpg`, "TONIGHT AT 9:00 PM" looked up live), plus the owner's own ad folder (`own_*`).
    Generated ones are jingle carrier clips (`runtime/ads/`, `ADS_VERSION`) drawn over live, like
    the up-next bumpers. Per channel `fs42/ads/<slug>/` holds symlinks + `promos.json`; FS42
    `commercial_dir` points there. Break stings ("We'll be right back" / "Now back to…" / "Stay
    tuned") are FS42 pre/post bumps in `runtime/breakbumps/{pre,post}/`; TV mode only says "back to"
    when the show really comes next.
  * Placement: conf `commercial_free: false`, `break_strategy: standard`, `break_duration: 60`.
    FS42 cuts at chapter markers, else fades to black (`-b`, opt-in "also at fades to black", slow,
    cached). **A file with neither would be split evenly by FS42 (arbitrary moments, against the
    brief)**, so `builder._find_breaks` writes a whole-file chapter marked `getsome_end_only`
    (`fs42.mark_end_only`, via FS42's own `FluidStatements`) → all its ads go at the end. Those
    markers are cleared before a fade-to-black scan. Note FS42 puts a single break at the end, so a
    file needs at least 2 usable chapter boundaries (and enough ad time) for a mid-show break.
  * Room for ads: FS42 only makes ad time by rounding a show up to the schedule increment, so a
    back-to-back channel with commercials uses increment 5 (shows start on 5-minute marks; ~2–6 min
    of ads each). Timed channels fill their gap with ads, leftovers fall to the intermission/BRB slate.
- **Intermissions come in five looks**, picked per gap (`tv/intermissions.py`): sunset (original),
  film leader, movie-palace marquee, hyperspace, and a teletext-style text page listing the next few
  programmes. The wording switches between films and TV episodes.
- Off-air: SMPTE bars with a 1 kHz tone, or static, per channel; the INFO banner says when it's back.

**Engine glue**:
- `fs42.py` is the only module that touches FS42 internals.
- `channel_writer.py` turns a channel into FS42 JSON plus per-show symlink folders.
- `builder.py` runs FS42 builds for each channel, then lines up in-order shows with the clock.
- `media_index.py` reads TV and movie NFOs; `library.py` keeps the scan caches; `assets_gen.py` generates runtime media.
- `music.py` and `synth.py` compose the guide tracks and jingles in code.

**CLI**: `getsome setup|tv|demo|scan|add-channel|remove-channel|list|build [NAME…]|extend|upkeep|compose-music`.

**Session 3 additions**:
- **Steam** (`steam.py`, `getsome.sh`): owner reported Big Picture → sound but no picture. Under Steam
  (`SteamGameId`/`SteamAppId`/`SteamOverlayGameId`/`SteamEnv`), `getsome.sh` drops
  `gameoverlayrenderer.so` from `LD_PRELOAD` and restores `SYSTEM_LD_LIBRARY_PATH`; `steam.prepare_qt()`
  forces `QT_QPA_PLATFORM=xcb` (Steam is an XWayland window and can't hand Wayland focus over, so KWin
  kept our fullscreen window behind Big Picture), only if libxcb-cursor exists; TV mode raises/activates
  itself at 0.3/1.2/3/6 s; with no subcommand it opens TV mode. Writes
  `~/.local/share/getsome/logs/steam_launch.log`. Overrides: `GETSOME_STEAM_PLATFORM=wayland`,
  or set `QT_QPA_PLATFORM` in Steam's launch options. **Not testable in the sandbox: needs owner's verdict.**
- **Restricted channels**: `Channel.restricted` (editor checkbox next to Time blocks; no rebuild),
  `settings.restricted_pin` (default "69", Settings → Restricted channels). TV mode (`app._apply_lock`):
  mpv `vf=lavfi=[scale=160:-2:flags=area,boxblur=6:3]` + `hwdec=no` + mute while locked; overlay
  `overlays.paint_lock` (padlock, "RESTRICTED", PIN dots); bumper/intermission/card overlays are not
  drawn while locked; info banner says "Restricted channel"; guide hides its titles
  (`GuideRenderer.hidden`) and the preview window skips it. OK (or M) opens the PIN pad; digits, OK,
  Left = delete, Back = cancel, channel keys close it and tune. One correct PIN unlocks all restricted
  channels until TV mode closes (`TVWindow.unlocked`).
  **Lock schedule** (owner request): `Channel.lock_mode` "always" | "hours" with `free_from`/`free_to`
  (whole hours, may wrap midnight; `Channel.locked_at(hour)`). Editor row: "Restricted … all day /
  except from [9 PM] to [6 AM]". TV mode re-checks every 20 s (`_check_lock_schedule`), so a channel opens
  and re-locks by itself while you watch; the lock panel and banner say "Opens by itself at 9 PM".
  **Silent relock** (`TVWindow._tick_relock`, setting `relock_minutes`, default 120, 0 = never; Settings →
  Restricted channels "Lock again after … away from them"): after the PIN, restricted channels lock again
  once that many minutes *in a row* were spent on non-restricted channels (guide included). Being on any
  restricted channel resets the count, so staying on one keeps them open indefinitely. No message on relock.
- **Age ratings in the pickers**: `media_index.clean_rating` (`<mpaa>`/`<certification>`, prefers the
  `US:` entry, strips `Rated `/country prefixes), `Show.rating` / `LibShow.rating` (`CACHE_VERSION` 3,
  movie cache now versioned too → one rescan). `dialogs.mark_usage` draws the usage dot + the rating in
  **plain text** as one icon (`_badge`, `BADGE_SIZE`) so ratings line up like a column; the owner asked for
  no colour coding (colour stays the dots' meaning). Tiers (`rating_tier`, also numeric ages like FSK 16)
  only drive the filter. "All ratings" filter combo in both pickers.
  Blur softened after owner feedback ("a vague idea of the content"): `scale=200 + boxblur=4:2`, veil alpha 70.
- **Artwork** (`artwork.py`, `tv/art_paint.py`): finds TMM/Kodi art (poster, fanart, clearlogo/logo,
  banner, landscape, clearart, `<episode>-thumb.jpg`; films `<stem>-poster.jpg` …), cached.
  `artwork.for_programme(path)` resolves a catalog symlink to the show/film. Uses: break stings over the
  show's backdrop + logo (`sting_art`), ~half of up-next bumpers get style `"art"` when the next show has
  fanart or a logo (`upnext_art`, seeded by start time), and new pool cards (`ads.link_pool`):
  `card_spot_<tag>` (spotlight, 15 s), `card_stills_<tag>` (filmstrip of up to 6 episode stills with NFO
  titles, 20 s, needs ≥3 thumbs), `card_wall_N` (channel ID over scrolling poster columns, needs ≥2 posters).
  `ads_paint._image` now decodes at most 1920 px (posters on the wall 480 px) into a 90-entry LRU.
  The demo generates fake art for three of its four shows. New cards need a channel rebuild.

**Session 4 (20 Nov) tweaks** (owner feedback: everything working; asked for these):
- Generated ads, cards, IDs and break stings at a third of the volume: `ads.AD_VOLUME = 0.33` applied when
  the carrier clips are encoded, `ADS_VERSION` 5 (regenerated on the next build; ~-9.6 dB measured).
  Up-next bumpers, intermission beds and the owner's own ad clips are unchanged.
- Promo card ("coming up … tonight at 9:30", the owner's favourite): the live `find_when` lookup also
  fills `next_ep` (code · episode title) and `next_plot` (that episode's NFO `<plot>`, via
  `ads.episode_details`), drawn under the genres in place of the show's plot (which stays the fallback).
- Trivia: the episode-count facts ("this channel has N episodes", "season N is the longest") are gone, and
  "role is played by name" was replaced by "<actor> (<role>) stars in [all] N episodes of <show>. Catch them
  all here on Channel N!" (counted from `<actor>` in the episode NFOs, top 4 with ≥2 episodes; falls back to
  tvshow.nfo's cast "… stars as <role> …"). `show_facts` now takes the channel number. Needs a rebuild.
  The demo now writes an invented cast into its NFOs.

**Session 4, later:** git history squashed to one "Fresh start" commit (owner asked; the new updater
follows it). **Rebuild all channels…** button under the channel list and in Settings → Maintenance
(`MainWindow.rebuild_all`, asks first). Channels with commercial breaks now include `ads.ADS_VERSION`,
`ads.POOL_VERSION` and the product-ads setting in their build hash, so updates/settings that change the
commercials mark them "needs build" automatically (this update does that once for every commercial channel).
- **Auto-create channels…** (button under "+ New channel"; `autopop.py` + `gui/autopop_dialog.py`). Owner's
  choices: keep existing channels and use *every* show in the picked folders (even ones already on a
  channel; mainly for new setups like a Bazzite PC), films only on separate movie channels, no automatic
  PIN restriction, no time blocks. Page 1: tick/add TV and movie folders (added to the library settings,
  scanned with `ScanDialog` if the cache is stale). Page 2: counts (suggested: ~8 shows per channel but at
  least one per main genre with a few shows; films: one per main genre with enough films, max 8), first
  channel number (after the highest existing), "Commercial breaks on the new TV channels" (off), and an
  editable tree: double-click to rename/renumber, style combo per TV channel, untick titles, drag titles
  between channels (a show dragged onto a movie channel turns it into a TV channel; films dragged onto a TV
  channel become its films). Create, or Create & build. Clashing numbers/names are refused.
  Grouping: weighted genre overlap (`_WEIGHT`: distinctive genres count more than "drama"), + decade
  nudge, + rating strain; family vs mature never share a channel (so asking for too few channels can give
  more); average-linkage merging with a size-balance term. Names from the strongest genre not yet used
  (`TV_NAMES` / `MOVIE_NAMES`, invented, never real networks), "90s " / "2010s " prefix when ≥70% share a
  decade; "Kid Zone" for kid-safe groups with kids/family genres. Episodic genres → Shuffle Classics, else
  Series Marathon. `media_index.rating_tier` is now shared by the pickers and autopop.

**Remote OK button under Steam (20 Nov)**: owner reported OK stopped working in TV menus (Enter still
did). Menus were fine in code (tested Return/Enter/Select). Cause: the Big Picture fix forced an X11 (xcb)
window, and X11 can't deliver evdev key codes above 255: a remote's KEY_OK (352) and KEY_SELECT (353)
never reach the app. Now `steam.window_type()`: Wayland on a Wayland desktop by default (overlay/libs/raise
fixes stay), X11 in Game Mode (gamescope) or when Settings → Shortcuts → "When Steam starts it" =
"Compatibility window" (`settings.steam_window` "x11"). **Owner to confirm** that Big Picture still shows
the picture with the Wayland window; if not, the X11 fallback costs the OK key (a future fix would be
reading the remote via evdev, which needs input-device permissions).

**Home network: head-end + cable box (22 Nov)** (owner: watch on the Bazzite PC without a second library).
- `headend.py` (`getsome serve`, started by Settings → Home network → "Share my channels…", which also writes
  `~/.config/autostart/neon-cable-42-headend.desktop`; pid in data dir): ThreadingHTTPServer on 4242 + UDP
  4243 discovery (box broadcasts `NC42?`). Pairing: Settings shows a 6-digit code (`pair_code.json`, 10 min,
  closed when the dialog closes) → token in `config/cable_boxes.json`. `/api/call` whitelist (info,
  segment_at, programme_at, programmes, now_and_next, card_for, promo_for, on_this_day, episode_details,
  artwork) serialised with a lock; programmes carry their `episode()` result (NFOs are on the head-end).
  `/f/<token>/<quoted path>` serves files with Range, only under realpath'd roots (data dir, library/movie
  folders, channels' show/film/ad/infomercial folders). The server runs hourly upkeep and keeps the PC awake
  while a box streamed in the last 2 min. Fedora Workstation's firewall zone already allows 1025-65535.
- `cablebox.py`: `settings.cable_box` {host, port, token, name}. `install()` (run_tv) monkeypatches fs42.channels/
  segment_at/programme_at/programmes/now_and_next, ads.card_for/promo_for/on_this_day/episode_details,
  artwork.for_programme and model.load (restricted list); TV mode takes guide channel / PIN / relock from the
  head-end (`shared_settings`) and skips its schedule keeper. `media(path)` → URL for mpv (load and
  load_preview; locally generated runtime files stay local), `fetch(path)` → cached artwork for `_image`.
  Unreachable head-end → NoSignal ("can't reach the head-end"); no channels → "NO SIGNAL" message.
- Settings → Home network (`gui/network_dialogs.py`): share toggle, status, "Pair a cable box…", "Forget cable
  boxes", "Connect to a head-end…" (discovery list, address, code), "Stop being a cable box". The setup
  window's note says when it's a cable box.
- Tested in the sandbox with two data dirs on one machine (discovery, pairing, wrong code/token/path refused,
  Range, TV tuning/playing over HTTP, restricted lock, guide). **Untested: real two-PC LAN, Bazzite.** The box
  still needs libmpv + ffmpeg (it generates its own bars/music); if Bazzite lacks them, the AppImage (phase 2)
  solves it, or `rpm-ostree install mpv-libs`. In Steam Game Mode the box gets an X11 window (remote OK lost there).
- First real setup (cable box: Bazzite deck edition, GNOME, NVIDIA; boots to Game Mode): Bazzite ships ffmpeg; mpv-libs needed `rpm-ostree install` and a
  restart (getsome.sh now says so on Atomic systems). Fixed: the setup window's Watch TV refused ("Build a
  channel first") on a box with no channels of its own; `MainWindow._watch` now skips the local checks in
  cable-box mode, pings the head-end (`cablebox.hello`) and explains if it can't be reached.

**Sharing service robustness (23 Nov, owner report: after the last updates the PCs can't see each other)**:
not reproduced in the sandbox (real path via getsome.sh + venv: enable → ensure_current → self-restart all
answer on 4242). Hardening: `serve` logs to `logs/headend.log` (rotating; `enable()` also sends the
process's stdout/stderr there, so start-up crashes are kept; no duplicate lines); binding the port retries
for 10 s (a copy being replaced may still hold it: before, the new copy died silently and nothing was
shared); restarts are counted through execv (`GETSOME_HEADEND_RESTARTS`), >3 in 2 min → stay up without
auto-restart; the service's own hourly upkeep refreshes the signature so it can't restart itself in a
loop. Settings → Home network shows "Sharing is ticked, but the sharing service isn't running" with the
last logged problem (`headend.last_error`) and a "Start sharing again" button. **Waiting on the owner's
headend.log if it still fails.**

**Blurred sides for 4:3 (23 Nov, owner request)**: TV MENU row SIDES: Black bars / Blurred picture
(`settings.side_fill`, one global choice). `TVWindow._apply_sides` (after `_apply_zoom` on every playback
restart and on menu changes) builds `_sides_filter()` only for features (`_zoom_key()`), in Normal / Force 4:3 /
Force 16:9, when the picture is narrower than the screen (`player.video_params` dw/dh, NOT `player["video-params"]`,
which python-mpv maps to options/): lavfi split → bg scale 320x180, boxblur 10:2, eq darker, scale to W×H
(screen aspect, H = video height ≤ 1080) | fg scaled to its aspect at H → overlay centred. Force 4:3/16:9 drop
`video-aspect-override` while the filter runs (it already makes the shape). `_update_vf` is now the single place
setting mpv `vf` (+ `hwdec=no` while any filter runs): the restricted lock blur replaces the sides while locked.
Tested: 4:3 clip fills the width with blurred sides, middle sharp; 16:9 clip untouched; lock still blurs.

**Cable box showed the old line-up and buffered a lot (23 Nov, owner report)**: the head-end service
(`getsome serve`, started once at login) never reloaded: FS42's StationManager keeps the station list in
memory and the process kept its old code, so after the owner rebuilt everything it served the old channels,
whose catalog links were gone (the box retried and buffered). Fixes:
- `headend.serve` keeper checks `_signature()` (git HEAD, FS42 confs + mtimes, channels.json, settings.json)
  every 15 s; on change it shuts the server down and `execv`s itself (same pid, fresh FS42 state, new code).
- pidfile now "pid\nHEAD"; `headend.ensure_current()` (setup window start) restarts a service that runs older
  code (or has an old-style pidfile), so the update reaches services started before this fix.
- Box: `cablebox.refresh()` + `TVWindow._refresh_lineup` every 2 min (new channel list, restricted set, guide
  channel/PIN; stays on the same channel number, re-tunes if it's gone; banner "CHANNEL LINE-UP UPDATED").
  mpv network buffering on the box: cache=yes, demuxer-max-bytes 300MiB, readahead/cache-secs 30.
Tested in the sandbox: removing a channel and building a new one on the head-end restarted the service by
itself within ~15 s and the box's line-up followed.

**Commercials inside episodes (23 Nov)**: owner: scanning by default takes hours on a library, so the
**default is typical positions**: `breaks.typical(duration)` (<8 min none; <16 min middle; <35 min 12/52/88 %
= cold open | act 1 | act 2 | tag; <56 min 9/28/48/68/87 % = teaser + acts; longer every ~15 min), instant.
Per channel "find the real act breaks (slower first build)" (`Channel.black_detect`, now part of the
channel hash) uses `breaks.find` (silencedetect on audio → 3 s blackdetect at each of the 14 longest
silences; silent *and* dark, ≥90 s apart; falls back to typical if nothing found). `breaks.plan` caches per
file (size+mtime) with mode typical/detected in `breaks_cache.json` (VERSION 2); detected is never
downgraded; FS42's own earlier fade-to-black points (not in our cache) are kept. Chapters always win.
`fs42.set_break_points` writes FS42's break_points table in its own segment format and drops our end-only
chapter. Tested: 22-min episode in a half-hour slot → cold open 2:40, act 1, act 2, tag ending at 29:40
(build 6 s); detector on a test episode found both act breaks, ignoring a silent-not-dark and a
dark-not-silent decoy. `ads.POOL_VERSION` 3 → commercial channels rebuild once (fast with typical).
Channel editor: own-ads-folder button removed (Settings → Commercial folders); older channels' `ad_folder`
stays with a "Stop using … on this channel" button.

**Library scan fixes (23 Nov, owner report: new shows, some without NFOs, not appearing after Rescan)**:
- Show picker's Rescan now refreshes the open list in place (`ShowPicker._fill`), keeps ticks, says how many
  new shows; it used to only say "reopen Add shows". `ensure_library` rescans by itself when
  `library.has_new_folders()` (show folders on disk the last scan didn't see).
- `media_index.find_shows` opens category folders ("TV shows/Documentaries/…": no videos at its top level,
  not a show itself, ≥2 sub-folders with videos or show layout; `_category_shows`) into their shows.
- `scan_show`: a show with only season-0 "specials" (TMM's half-matched documentaries) plays them.
- More video types (`VIDEO_EXTS`: m2ts, mts, vob, 3gp, rmvb, rm, asf, f4v, m2v, ogm, mpe, dv). FS42 only
  catalogs mp4/mpg/mpeg/avi/mov/mkv/ts/m4v/webm/wmv (media_processor.VIDEO_FORMATS), so links are named with
  `media_index.fs42_suffix` (vob/mpe/m2v→.mpg, m2ts/mts→.ts, others→.mkv); players probe by content.
  Also for own-commercial links.
- Folders without episodes are recorded (`library.skipped()`, cache key "skipped" with a plain reason from
  `why_no_episodes`) and listed under the picker as "Not listed: …".

**Picture / zoom in the TV menu (23 Nov, owner request)**: MENU has a PICTURE row (`menu.ZOOMS`): Normal,
Zoom (mpv `panscan=1`: a 16:9 picture boxed inside a 4:3 frame fills the screen), Cinema zoom (panscan 1 +
`video-zoom=log2(2.35/1.78)`), Stretch (`keepaspect=no`), Force 4:3 / 16:9 (`video-aspect-override`).
Remembered per show/film in `settings.zoom_by_show` (key from the file name: show title before " - SxxEyy",
else the film's file name; same on every channel; Normal removes the entry). `TVWindow._apply_zoom` runs on
every playback restart: features get their show's mode, commercials/cards/bumpers/images play Normal.
Measured in the sandbox on a windowboxed clip: Normal 160-1119×90-629, Zoom/Cinema full screen, Stretch and
16:9 full width with the clip's own bars, 4:3 = Normal.

**Film cards before features (23 Nov, owner request)**: the countdown screen only shows for the last 3
minutes (`tv/app.CARDS_BEFORE` = 180). In a longer gap before a film, `TVWindow._intermission` starts a
thread (`_film_cards`) that collects up to six films coming up on the channel in the next 36 h (next
feature first) via `fs42.programmes` + `ads.movie_card(path)` (title, year, rating, runtime, genres, NFO
plot/tagline, `movie_facts`, `artwork.movie_art`; finds the film in the movie-library cache by file name,
because catalog links resolve symlinks). `bumpers.paint_intermission` draws `art_paint.film_cards`
while remaining > 180 s: 12 s per card ("coming up": backdrop Ken Burns, poster, logo/title, meta,
tagline, synopsis, when-bar "NEXT · 9:00 PM / TONIGHT AT / TOMORROW AT / <WEEKDAY> AT"; "did you know?":
fact + poster), fades between cards, corner "FEATURE PRESENTATION IN mm:ss". Gaps before TV episodes are
unchanged. Cable boxes get `movie_card` from the head-end.

**Commercial folders in Settings (23 Nov)**: `settings.commercial_folders` (list, edited like the library
folders). `ads.link_pool` links every video in them (subfolders, no dot-files, de-duplicated) as `own_NNN`
after the channel's own `ad_folder`, for every channel with commercial breaks. `ads.commercials_signature()`
(folder list + every subfolder's mtime, cached 10 s) is part of `channel_hash`, so adding/removing folders
or clips marks those channels "needs build" (and, once, every commercial channel after this update).
The head-end serves files from these folders to cable boxes.

**New-channel flow (23 Nov, owner request)**: name comes last. `NewChannelDialog` = style + number only
(placeholder name "Channel N"); then the picker; then `NameChannelDialog` (pre-filled with the first
show's title / film's title, selected; empty or untouched = that suggestion; a taken name is suggested as
"… 2"). `dialogs.enter_presses(dialog, ok)` makes Enter press only the "continue" button (the style
cards are QPushButtons and used to catch Enter) in the new-channel, name and both picker dialogs.

**XCable edition (23 Nov)**: the owner wanted a second, separate install for a separate library, hard to
confuse with Neon Cable 42: "premium cable" (black and gold, gentleman's club), groovy bass-led jazz,
no restrictions by default. Built as an edition of the same code (see CLAUDE.md "Two editions"):
- `xcable.sh` → `GETSOME_EDITION=xcable`; `paths` gives name/slug/launcher and the XDG folders; desktop
  entries, Wayland app id, head-end autostart and ports (4252/4253; discovery ignores the other edition),
  Steam shortcut matching (`paths.LAUNCHER` in Exe) and updater restart all follow it. Settings default
  `ads_products` = "none" in XCable.
- Look: `brand.QColor` subclass replaces PySide's QColor in every drawing module (tv/*, gui/theme,
  schedule_editor, steam_art, desktop icon); hue mapping magenta→gold, cyan→champagne, violet/navy→dark
  oxblood, yellow→amber, greens→olive gold, whites→ivory (copies of mapped colours aren't re-mapped;
  PySide can't copy a Python QColor subclass directly, so copies go by value). Stylesheet via
  `brand.restyle`. Fonts Limelight (numbers/headings) + Playfair Display (text) with lining figures
  (`QFont.setFeature("lnum")`, Qt ≥ 6.7). Picker usage dots stay green/yellow (meaningful).
- Music: `beds.JAZZ_GENRES` lounge (walking upright, brushes + swung ride, Rhodes Charleston comping, sax),
  groove (16th slap bass with pops, funk drums with ghost notes, Rhodes stabs, sax), velvet (slow: lazy
  upright, rim, Rhodes pad, vibes); bass mixed up front. XCable's guide tracks (`assets_gen.GENRE_TRACKS`,
  eight invented titles, "Shuffle all (late-night jazz)"), card/intermission beds, up-next jingles and
  break stings/IDs use them; `ads.intermission_beds` only returns the edition's styles.
- Art: Steam cover "X" over "CABLE", "XCABLE" logo/banner, TV icon with "X"; tagline "LATE-NIGHT PREMIUM CABLE".
- Untested by the owner. Possible follow-ups: Art Deco versions of the bumper/intermission scenes (they are
  recoloured synthwave now), jazzier sign-off clip.

**Remote OK button in Game Mode (23 Nov)**: on the cable box (Bazzite deck edition, Game Mode) OK did nothing:
gamescope runs apps under XWayland and X11 can't receive key codes above 255 (KEY_OK 352 / KEY_SELECT 353).
Fix: Settings → Remote control → "Fix the OK button…" runs `remote_helper.py install` as root via
`pkexec /usr/bin/python3 -I` (stdlib only). It listens (≤25 s) on keyboard-like `/dev/input/event*`
(from `/proc/bus/input/devices`) for KEY_OK/KEY_SELECT preceded by MSC_SCAN, writes
`/etc/udev/hwdb.d/70-neon-cable-42-remote.hwdb` (`evdev:input:b<bus>v<vendor>p<product>*` +
`KEYBOARD_KEY_<scan>=enter`, one block per remote), then `systemd-hwdb update` + `udevadm trigger`.
"Remove the fix" deletes it. Nothing typed is stored. Receivers that don't report scancodes can't be
remapped this way (the dialog says so). Tested with a fake event device; untested on the real remote.

**Remove all channels (22 Nov)**: Settings → Maintenance → "Remove all channels…" (danger button; closes
Settings, then `MainWindow.remove_all`). Refuses while TV mode runs; confirmation box with Cancel as default
and a "Remove all channels" button. `cleanup.remove_all` removes every channel via `builder.remove_channel`
(FS42 `-x` + conf + link folders), then leftover Neon Cable 42 confs, then the per-channel commercial pools
(`fs42/ads/<slug>`, symlinks + json) and empty catalog folders, with a progress dialog. Media, settings,
library caches and runtime media are kept.

**Game controllers in TV mode (20 Nov)** (`tv/gamepad.py`): reads `/dev/input/js*` (joydev; readable by
the logged-in user via uaccess, no packages), non-blocking, polled every 16 ms, rescans every 2 s for
hot-plug. Buttons/axes are identified by evdev code via JSIOCGBTNMAP/JSIOCGAXMAP (falls back to xpad order),
so layouts match across Xbox/PlayStation/8BitDo. Map (`BUTTONS`): A/south OK · B/east Back · X/west Info ·
Y/north previous channel · Start Menu · Back/View/Select "guide" (new action: jump to the guide channel,
again = back) · LB/RB channel down/up · d-pad/left stick channel up/down/left/right with key-like repeat
(0.45 s, then every 0.16 s) · LT/RT volume (only axes resting at -1, so a cheap pad's right stick on Z/RZ is
ignored). Only acts while the TV window is active. `TVWindow._handle(action, key)` is now the single input
path for keys and controllers; `_duplicate` drops the same action from key+pad within 0.15 s (Steam Input
keyboard templates) and `Gamepads._emit` the same action from two pads within 0.12 s (Steam's virtual pad).
PIN pad with a controller: Up/Down picks a digit (shown in a pink circle), Right moves on, Left deletes,
OK confirms (the pending digit counts). Not remappable yet (keymap.json is keyboard only).

**Steam library artwork** (owner asked for it to "just appear", like RetroDECK / Steam ROM Manager):
- Settings → Shortcuts → **Add to Steam** (`steam_shortcut.add_to_steam`): for every Steam account
  (`userdata/<id>`, not 0; roots `~/.steam/steam`, `~/.local/share/Steam`, Flatpak `~/.var/app/com.valvesoftware.Steam/…`)
  adds a shortcut to `config/shortcuts.vdf` (own binary-VDF reader/writer, round-trips Steam's bytes exactly;
  backup `shortcuts.vdf.before-neon-cable-42`; atomic replace): AppName "Neon Cable 42", Exe/StartDir quoted
  `getsome.sh`, LaunchOptions `tv`, appid = crc32(exe+name)|0x80000000, icon = our `_icon.png`. Refuses
  while Steam runs (`/proc/*/comm` steam/steamwebhelper; Steam rewrites shortcuts.vdf on exit). An existing
  shortcut of ours (Exe contains `getsome.sh` or the name matches) is never duplicated; only missing art added.
- **Startup safety net** (`ensure_artwork`, 3 s after the setup window or TV mode opens): any Steam
  shortcut of ours without artwork gets the missing images (only files in `config/grid/`, safe while Steam
  runs, shown after a Steam restart). Never overwrites images the owner set (any of png/jpg/jpeg/webp).
- Art (`steam_art.render`, drawn in code): `<appid>p.png` 600x900 cover, `<appid>.png` 920x430, `_hero`
  1920x620 (no text; Steam overlays the logo), `_logo` transparent, `_icon` (the app's TV icon).
- Limit: Steam never asks an app for art when the owner adds it by hand; the safety net covers that
  case after one launch. A hand-added shortcut keeps its own launch options and icon (we don't edit
  shortcuts.vdf while Steam may be running). `GETSOME_STEAM_ROOT` points the module at a test folder.

## Decisions made (and why)
- **Our own Qt front end over an unmodified FS42 submodule** (owner approved). FS42's player can't layer neon and static over video cleanly. FS42 can still be updated by bumping the submodule; only `getsome/fs42.py` should need changes.
- **Symlink folders per show**, named `Show Title - S01E02.ext`, built from the NFO season and episode numbers. FS42 sorts sequences by path (which would put Season 10 before Season 2) and takes titles from file names. Specials (season 0), extras and trailers are skipped. Folders without NFOs play in natural name order, and the HUD shows the cleaned file name.
- **Display titles come from our NFO data**, not FS42's: FS42 title-cases names ("Bob'S Burgers").
- **Sequence line-up after every build**: FS42 starts new schedules at **midnight**, so a channel built in the evening has already "used up" episodes, and rebuilds would skip ahead. `builder.py` measures which episode airs next and shifts FS42's sequence index, normally in one extra pass.
- **FS42 exits 0 even when a step fails**, so `builder._run` parses its output for "Failed to…" and "Error…".
- **Commercial-free scheduling for now** (`schedule_increment` 0 for back to back, 30 or 60 for timed starts). Timed gaps fall through to `be_right_back_media` (`runtime/intermission.png`) because the channel's `bump_dir` points at `runtime/nobumps/`, which holds only a 3-hour placeholder clip that never fits a gap.
- **Up-next bumpers** are FS42 `end_bump`s pointing at `runtime/upnext/` (10 s jingles). TV mode recognises `upnext_N.mp4` and draws the matching animation.
- **Software video decoding by default** (`hwdec: no`), with "auto-safe" as an option. The black-frame scare during development turned out to come from the sandbox's software GL, not from hwdec.
- **Guide music**: 10 *original* tracks in an RPG synth/metal style. Copyrighted melodies are never reproduced (owner understands). The composer runs as a separate low-priority process with pid-checked locks, writes about 1 MB of `.m4a` per track, and logs to `~/.local/share/getsome/logs/music.log`.
- **Bumper visuals are drawn live** rather than pre-rendered, so they're always accurate, even when you tune in mid-bumper.
- **Fonts**: Monoton and Tilt Neon (SIL OFL), bundled. VCR OSD Mono was avoided because its license isn't open.

- **Time blocks map onto FS42's hourly slots** (`channel_writer.plan` / `make_conf`). Every show folder
  used anywhere on a channel (rotation or blocks) is **one** FS42 tag, so an in-order show keeps a single
  place in its sequence wherever it airs. Block films get their own tag `Movies_<block id>`
  (`fs42._is_movie` matches both). Infomercials are tag `Infomercials`. `week_grid()` is the single
  source for what each hour holds; the GUI draws it, `make_conf` turns it into slots.
- **Sign-off** is FS42's `{"event": "signoff"}` on the first off-air hour after on-air hours; the other
  off-air hours are left empty, so FS42 plays `off_air_video` (`bars_tone.mp4` or `static.mp4`).
- **Line-up with blocks** (`builder.py`): "what airs next" only counts programmes starting in the hours
  where that show runs as a sequence (`_sequence_hours`, read from the conf), looks 8 days ahead so weekly
  blocks are found, and only shows that were already in order keep a place (a shuffled show switched to
  in order starts at its start episode).
- **Upkeep and builds share a pid lock** (`upkeep.schedule_lock`, file `fs42/upkeep.lock`), so the setup
  window and TV mode never run FS42 on the same channel at once. A rebuild of an expired channel
  applies any unbuilt edits too.

## Known issues / risks
- **Untested by the owner**: the audio/subtitle menu on real multi-language files, the hwdec option, the IR remote (key names not yet reported), the movie-channel intermission at real lengths, and the mixed films + shows channel.
- **Untested by the owner (newest, commercials)**: real chapter files, the fade-to-black scan on the
  real library (could take hours the first time), ad-pool variety over a long evening (FS42 sometimes
  repeats an ad within one break), promo artwork from real TMM folders.
- **Untested by the owner (newer)**: keep-awake on real GNOME/KDE (tested only against a mock D-Bus;
  logind could not be tested in the sandbox), the exit menu with the IR remote's back button, the new
  intermission looks on a real TV.
- **Untested by the owner (new)**: time blocks, broadcast hours, sign-off, the static and infomercial
  off-air modes, and schedule upkeep on a real library.
- **Back-to-back channels don't start blocks exactly on the hour**: the programme running at 7:58 finishes
  first, so an 8 PM block may begin at 8:10. Choose "On the hour" start times for punctual blocks.
  Likewise sign-off waits for the current programme to end.
- Blocks are whole hours only (FS42 schedules in hourly slots).
- Broadcast hours are the same every day (no separate weekend hours yet).
- If a schedule expired while the external drive is unplugged, the upkeep rebuild fails (logged) and the
  channel stays off air until the drive is back and the setup window or TV mode is opened again.
- With a small selection, FS42's random pick can repeat a film twice in a row.
- With very short content (under about 15 min), timed channels quantize to 5-minute starts (FS42 `_calc_target_duration`).
- The guide fetches at most 40 programmes per row; fine for real lengths, but cluttered with tiny demo clips.
- Multi-episode files take the first `<episodedetails>` block for their numbering.
- The first library scan of a big external drive takes about a minute (cached afterwards). The library cache is versioned (`CACHE_VERSION = 2`) so new fields trigger a rescan.
- `start_pending` on the in-order Movies pseudo-entry is unused: it always resumes, or starts from the earliest film.
- Build time: roughly 1 minute per channel the first time, mostly FS42 probing files (cached afterwards). The line-up pass adds one more `-x`/`-w` round.
- Dev sandbox only: Xvfb sometimes dies between long jobs (restart it), and llvmpipe randomly renders mpv video black for a whole run (standalone mpv does the same). Neither applies on real hardware.

## Owner feedback
**Session 3 feedback (19 Nov):** in-between bumpers good; trivia, upcoming shows and everything using
their artwork "looks great"; generated product ads "comically bad", leave them for now (they plan to
collect real commercials for the own-ads folder), lean on title cards and artwork transitions instead
(done). Adding to Steam works, but Big Picture launch gave sound and no picture (fix pushed, awaiting test).

**Confirmed working by the owner:** clean-up button, guide preview window, the usage dots in the pickers,
the new-episode check, every-episode rotation, schedule upkeep, update button, exit menu.

**Still open / waiting on:**
- ✅ Steam Big Picture: owner says "flawless". ✅ Restricted channels look good (asked for softer blur
  and lock schedules: done, awaiting their check).
- Artwork cards/transitions on the real library.
- Verdict on the six music styles. Earlier music note: "could be more metal and muddy".
- Whether the app-menu entry now shows in their start menu (Settings used to be taller than the screen,
  hiding the button; fixed, plus a KDE menu refresh). The owner seems to be on **KDE Plasma** ("start
  menu", "Multimedia" folder).
- IR remote: key names never reported; the exit menu listens on Backspace / Back / Esc / Q / Stop.

## Next steps (recommended order)
1. **Owner feedback round** on Steam, PIN lock and artwork cards; tune whatever they flag. Possible
   follow-ups: "Fake product ads" default to None once their own ad folder exists; an artwork intermission
   look for movie channels (poster + fanart); lock badge on restricted rows in the guide.
2. **Key remap screen** ("press a button to assign"), editing `~/.config/getsome/keymap.json` from
   Settings and/or TV mode. Useful for the IR remote and for Steam controller layouts.
3. **Add to Steam** (asked for; "might help with the living-room experience"). Two levels:
   * *Already possible by hand*: Steam → Games → "Add a Non-Steam Game to My Library" lists desktop apps,
     and Neon Cable 42 is one once it's in the app menu. Launch options `tv` start straight in TV mode.
   * *A button* (moderate): write an entry into `~/.steam/steam/userdata/<id>/config/shortcuts.vdf`
     (binary VDF; Flatpak Steam lives under `~/.var/app/com.valvesoftware.Steam/`), for every Steam user
     id, **only while Steam is closed** (it rewrites the file on exit). Exe `getsome.sh`, LaunchOptions
     `tv`, generated library art in `userdata/<id>/config/grid/` (capsule, hero, logo, icon: draw them
     in code like the app icon). Plus a recommended **Steam Input** layout mapping the controller to our
     keys (d-pad up/down = channel, A = OK, B = Back, Start = Menu, etc.). Gamepad support directly in
     TV mode (Qt Gamepad / SDL) would be the next step after that.
4. **Commercials follow-ups**: facts ticker on movie-channel intermissions, per-show "no breaks"
   toggle, avoid repeating an ad within one break, more product scripts.
5. **AppImages: owner's test of the first release** (see "AppImages" below). Then, once the owner is
   happy, they can delete `~/getsome` (the git copy); the data folders stay.

**Long-term goals (owner's wishlist, not scheduled):**
- **Plasma Bigscreen integration** (KDE's TV/10-foot interface; the owner hasn't set it up yet): appear
  as a Bigscreen app with a proper tile, remote/gamepad navigation in the setup window, maybe launching
  straight into TV mode.
- Steam Big Picture / Steam Deck-style living-room polish, building on item 3.

## Volume sliders (26 Nov)
TV-mode MENU has two slider rows: **VOLUME** (the TV volume, 0–130, saved as `settings["volume"]`, the
same value as Settings → TV volume and the remote's volume keys, which now also save it) and **ADS**
(`settings["ad_volume"]`, default 50: commercials play at that percent of the TV volume; 0 = silent).
`TVWindow._apply_volume` is the one place that sets mpv's volume: called before each file loads, and
it lowers only FS42 `content_type == "commercial"` (own ads, generated ads, promos and IDs in the
commercial pool), not bumps, cards in intermissions or shows. Also in Settings as "Commercials (% of TV)".

## AppImages (24 Nov)
Two files, one per edition: **NeonCable42-x86_64.AppImage** and **XCable-x86_64.AppImage**, published as
**GitHub Releases** of this repo by `.github/workflows/appimage.yml` (push a tag `vYYYY.MM.DD` or "Run
workflow" in the Actions tab with a version). The release notes are the commit subjects since the previous
tag, and they are the "What's new" list in the AppImage's updater.
- **Build**: `packaging/build_appimage.sh <neon|xcable|both> [out]` on Ubuntu 24.04 (glibc 2.39; Fedora and
  Bazzite are newer). Inside: python-build-standalone 3.13 with every requirement (PySide6-Essentials
  instead of the full PySide6), our code, FS42 (minus docs/tests), ffmpeg/ffprobe and libmpv from Ubuntu
  with the libraries they need (collected through ldd; never glibc, GL/EGL/Vulkan/VA drivers, X11/Wayland,
  PulseAudio/PipeWire/ALSA, D-Bus, udev: see `EXCL` in the script and `packaging/excludelist`).
  `packaging/AppRun` sets `GETSOME_EDITION`, `GETSOME_VERSION` (the tag), `GETSOME_LIBMPV`,
  `GETSOME_FS42_DIR`, `PYTHONNOUSERSITE` and keeps the Steam fixes from `getsome.sh`. About 690 MB
  unpacked, roughly a third of that as an AppImage. Local test without appimagetool:
  `PYTHON_DIR=<relocatable python> packaging/build_appimage.sh neon` then `build/AppDir-neon/AppRun list`.
  The build exports `PYTHONNOUSERSITE=1` so pip can't skip packages already in the build machine's home
  folder (this hid a missing `requests`, which FS42 imports at start-up).
- **In the code**: `paths.APPIMAGE` (set by the AppImage runtime), `paths.launcher_path()` (what menus,
  Steam, autostart run), `paths.launch_cmd()` (start a fresh copy: head-end service, music composer),
  `paths.launch_hint()` (for messages), `paths.version()` (tag, or git HEAD). `getsome/__init__.py` points
  python-mpv at `GETSOME_LIBMPV`. Moving from the git copy is automatic: the app-menu entry and desktop
  shortcut are rewritten on start (`desktop.refresh_if_installed`), the sharing service restarts from the
  AppImage because its version differs (`headend.ensure_current`, which also rewrites the autostart
  entry), and Settings → "Add to Steam" (with Steam closed) re-points the existing shortcut.
- **Updater, AppImage mode** (`updater.py`): reads the repo's releases from the GitHub API, picks the newest
  with this edition's file, downloads it and its `.sha256`, checks the hash, replaces the AppImage in place
  and restarts it. Uses the computer's own certificate list (Fedora keeps it in `/etc/pki`, which the
  bundled OpenSSL doesn't look in), else certifi's. The git checkout keeps the git updater.
- **Offline audit (24 Nov)**: network code exists only in `updater.py` (only when "Check for updates" is
  pressed), `headend.py` (home network, only when sharing is switched on) and `cablebox.py` (only in cable-box
  mode, talking to the head-end). FS42 imports its web server module at start-up (hence `requests`) but only
  runs it with `--server` or no arguments; we always pass arguments. Its TMDB lookups live in that server.
- **Tested here**: AppDirs of both editions: `list`, demo build of three channels through FS42, setup
  window (XCable look and fonts), bundled libmpv decoding frames. TV mode in the sandbox draws black with
  both the git copy and the AppImage (llvmpipe, see Known issues), so the picture on real hardware is the
  owner's test. Not testable here: FUSE mount, Steam launch, Bazzite.

## How to verify quickly (dev)
```bash
Xvfb :99 -screen 0 1280x720x24 &                 # sandbox only
export DISPLAY=:99 GETSOME_DATA_DIR=/tmp/gs XDG_CONFIG_HOME=/tmp/gscfg
python3 -m getsome demo                          # fake library + 3 channels, built
python3 -m getsome list
python3 -m getsome tv --windowed                 # or drive TVWindow from a script and grab frames
python3 -m getsome upkeep                        # top up / rebuild schedules
# time travel for schedule tests (apt install faketime): build "10 days ago", then upkeep today
faketime -f "-10d" python3 -m getsome build "Neon TV"
FAKETIME_DONT_FAKE_MONOTONIC=1 faketime -f "@2026-11-19 03:10:00" python3 -m getsome tv --windowed --channel 7
```
Mock TMM libraries are easy to fake: copy a tiny `ffmpeg -f lavfi testsrc2` clip into `Show (Year)/Season N/Show - SxxEyy.mkv` with `<episodedetails>` NFOs, plus `tvshow.nfo` and `<movie>` NFOs.
