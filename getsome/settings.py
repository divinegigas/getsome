"""User settings (~/.config/getsome/settings.json). The setup GUI edits these in phase 5."""

from __future__ import annotations

import json
import logging
from typing import Any

from . import paths

_log = logging.getLogger("getsome.settings")

DEFAULTS: dict[str, Any] = {
    # The setup window has offered once to add Neon Cable 42 to the app menu
    "menu_offered": False,
    # Start in TV mode instead of the setup screen
    "launch_into_tv": False,
    # mpv hardware decoding. "no" is the safe default; "auto-safe" offloads
    # decoding to the GPU (worth trying for 4K/HEVC) but some driver/compositor
    # combinations show a black picture with it.
    "hwdec": "no",
    "volume": 100,
    "hiss_volume": 35,
    "ad_volume": 50,            # commercials play at this percent of the TV volume (0 = silent)
    "remember_last_channel": True,
    # Stop the screen blanking / computer sleeping while TV mode is on
    "keep_awake": True,
    # ...but let it sleep after this many hours without a button press (0 = never)
    "keep_awake_hours": 4,
    # Pictures narrower than the screen (4:3): "black" bars or a "blur"red copy of the picture at the sides
    "side_fill": "black",
    # Picture (zoom) mode per show, picked in the TV menu: {show or film title: mode} (tv/menu.ZOOMS)
    "zoom_by_show": {},
    # Preferred audio / subtitle language codes picked in the TV menu ("" = file default, sub "off" = none)
    "audio_lang": "",
    "sub_lang": "",
    # Guide channel music: "shuffle" (every style), "shuffle_rpg", a track key (music.TRACKS or
    # assets_gen.GENRE_TRACKS), "classic" or "custom"
    "guide_music": "shuffle",
    # Live preview window on the guide: on/off, seconds per channel, and which channels
    # (1 = every channel in turn, 5 = every 5th channel)
    "guide_preview": True,
    "guide_preview_seconds": 8,
    "guide_preview_step": 1,
    # What you hear on the guide: "music" (guide music) or "preview" (the preview channel's sound)
    "guide_audio": "music",
    # Fake product ads in commercial breaks: "none", "few" or "lots" (show cards always play)
    "ads_products": "none" if paths.XCABLE else "few",
    # Window type when Steam starts Neon Cable 42 on a Wayland desktop: "wayland" (every remote button
    # works) or "x11" (fallback if Big Picture shows sound but no picture; the remote's OK key is lost)
    "steam_window": "wayland",
    # Cable box: the head-end this computer watches ({host, port, token, name}; {} = not a cable box)
    "cable_box": {},
    # PIN that unlocks restricted channels in TV mode (digits only)
    "restricted_pin": "69",
    # After the PIN: lock restricted channels again after this many minutes in a row spent
    # on other channels (0 = stay open until TV mode closes)
    "relock_minutes": 120,
    # Channel number of the built-in TV guide (0 = no guide channel)
    "guide_channel": 2,
    # Channel number of the On Demand preview channel (0 = none; On Demand is still in the BACK menu).
    # A real channel on the same number keeps it.
    "ondemand_channel": 1,
    # On Demand: seconds resting on a title before its preview starts (0 = no previews), seconds of preview
    # before it carries on as the real thing (0 = only when OK is pressed), and two volumes as a percent of
    # the TV volume: the quiet browsing music (0 = none) and previews
    "ondemand_preview_after": 3,
    "ondemand_preview_for": 15,
    "ondemand_music_volume": 14,
    "ondemand_preview_volume": 70,
    # Folders that contain show folders (e.g. ".../Media/TV shows")
    "library_folders": [],
    # Folders that contain movies
    "movie_folders": [],
    # Folders of the owner's own commercials (subfolders too), used on every channel with commercial breaks
    "commercial_folders": [],
}


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    p = paths.config_dir() / "settings.json"
    if p.exists():
        try:
            user = json.loads(p.read_text())
            data.update({k: v for k, v in user.items() if k in DEFAULTS})
        except Exception as e:
            _log.warning("ignoring unreadable %s: %s", p, e)
    return data


def save(data: dict[str, Any]) -> None:
    paths.ensure_dirs()
    p = paths.config_dir() / "settings.json"
    p.write_text(json.dumps({k: data.get(k, v) for k, v in DEFAULTS.items()}, indent=2))
