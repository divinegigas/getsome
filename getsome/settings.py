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
    "remember_last_channel": True,
    # Stop the screen blanking / computer sleeping while TV mode is on
    "keep_awake": True,
    # ...but let it sleep after this many hours without a button press (0 = never)
    "keep_awake_hours": 4,
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
    "ads_products": "few",
    # PIN that unlocks restricted channels in TV mode (digits only)
    "restricted_pin": "69",
    # After the PIN: lock restricted channels again after this many minutes in a row spent
    # on other channels (0 = stay open until TV mode closes)
    "relock_minutes": 120,
    # Channel number of the built-in TV guide (0 = no guide channel)
    "guide_channel": 2,
    # Folders that contain show folders (e.g. ".../Media/TV shows")
    "library_folders": [],
    # Folders that contain movies
    "movie_folders": [],
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
