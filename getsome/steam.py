"""Running under Steam (non-Steam game shortcut, Big Picture, Game Mode).

Started from Big Picture, the TV used to play sound with no picture. Likely causes, all
handled here or in getsome.sh:
  * Steam's overlay (LD_PRELOAD gameoverlayrenderer.so) hooks OpenGL; getsome.sh drops it.
  * Steam's runtime libraries (LD_LIBRARY_PATH) shadow the system's; getsome.sh restores them.
  * On a Wayland desktop Qt opens a native Wayland window, but Steam is an X11 (XWayland)
    window and can't hand focus over, so the compositor's focus-stealing prevention keeps our
    fullscreen window *behind* Big Picture. Under Steam we therefore open an X11 window too,
    and raise it a few times while starting up.
Overrides: GETSOME_STEAM_PLATFORM=wayland keeps Qt's normal choice; QT_QPA_PLATFORM always wins.
"""

from __future__ import annotations

import datetime as dt
import os

from . import paths

_KEYS = ("GETSOME_FROM_STEAM", "SteamGameId", "SteamAppId", "SteamOverlayGameId", "SteamEnv",
         "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY", "DISPLAY", "QT_QPA_PLATFORM",
         "LD_PRELOAD", "LD_LIBRARY_PATH", "SYSTEM_LD_LIBRARY_PATH", "SDL_VIDEODRIVER", "GAMESCOPE_WAYLAND_DISPLAY")


def from_steam() -> bool:
    e = os.environ
    return bool(e.get("GETSOME_FROM_STEAM") or e.get("SteamGameId") or e.get("SteamAppId")
                or e.get("SteamOverlayGameId") or e.get("SteamEnv") == "1")


def prepare_qt() -> None:
    """Call before the QApplication exists."""
    if not from_steam():
        return
    e = os.environ
    if not e.get("QT_QPA_PLATFORM") and e.get("GETSOME_STEAM_PLATFORM", "xcb") != "wayland" and e.get("DISPLAY"):
        import ctypes.util

        # Qt 6.5+ can't open X11 windows without libxcb-cursor (Fedora: xcb-util-cursor)
        if ctypes.util.find_library("xcb-cursor"):
            e["QT_QPA_PLATFORM"] = "xcb"
    _write_log()


def _write_log() -> None:
    try:
        d = paths.data_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        lines = [f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} started from Steam"]
        lines += [f"  {k}={os.environ.get(k, '')}" for k in _KEYS if k in os.environ]
        (d / "steam_launch.log").write_text("\n".join(lines) + "\n")
    except OSError:
        pass
