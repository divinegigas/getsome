"""Running under Steam (non-Steam game shortcut, Big Picture, Game Mode).

Started from Big Picture, the TV used to play sound with no picture. Likely causes, all
handled here or in getsome.sh:
  * Steam's overlay (LD_PRELOAD gameoverlayrenderer.so) hooks OpenGL; getsome.sh drops it.
  * Steam's runtime libraries (LD_LIBRARY_PATH) shadow the system's; getsome.sh restores them.
  * On a Wayland desktop Qt opens a native Wayland window, but Steam is an X11 (XWayland)
    window and can't hand focus over, so the compositor's focus-stealing prevention keeps our
    fullscreen window *behind* Big Picture. Under Steam we therefore open an X11 window too,
    and raise it a few times while starting up.
Update (20 Nov): the X11 window made a remote's OK button stop working (X11 can't deliver key codes
above 255; KEY_OK is 352), so on a Wayland desktop we now keep the Wayland window by default and keep
X11 only as a setting (settings.steam_window = "x11") and in Game Mode (gamescope).
Overrides: GETSOME_STEAM_PLATFORM=wayland|x11; QT_QPA_PLATFORM always wins.
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
    if not e.get("QT_QPA_PLATFORM") and window_type() == "x11" and e.get("DISPLAY"):
        import ctypes.util

        # Qt 6.5+ can't open X11 windows without libxcb-cursor (Fedora: xcb-util-cursor)
        if ctypes.util.find_library("xcb-cursor"):
            e["QT_QPA_PLATFORM"] = "xcb"
    _write_log()


def game_mode() -> bool:
    """Steam's Game Mode / gamescope session (Steam Deck, Bazzite): only X11 windows show there."""
    e = os.environ
    return bool(e.get("GAMESCOPE_WAYLAND_DISPLAY") or e.get("SteamDeck") == "1"
                or "gamescope" in e.get("XDG_CURRENT_DESKTOP", "").lower())


def window_type() -> str:
    """Which window to open under Steam. "wayland" passes every remote button through (X11 drops
    keys with codes above 255, such as a remote's OK = KEY_OK 352 and Select); "x11" is the fallback
    if Big Picture shows sound but no picture. Game Mode always gets X11."""
    e = os.environ
    if game_mode():
        return "x11"
    forced = e.get("GETSOME_STEAM_PLATFORM", "")
    if forced:
        return "x11" if forced in ("xcb", "x11") else "wayland"
    if not e.get("WAYLAND_DISPLAY"):
        return "x11"                 # an X11 desktop: nothing to choose
    try:
        from . import settings

        return "x11" if settings.load().get("steam_window") == "x11" else "wayland"
    except Exception:
        return "wayland"


def _write_log() -> None:
    try:
        d = paths.data_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        lines = [f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} started from Steam"]
        lines += [f"  {k}={os.environ.get(k, '')}" for k in _KEYS if k in os.environ]
        (d / "steam_launch.log").write_text("\n".join(lines) + "\n")
    except OSError:
        pass
