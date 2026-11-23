"""Neon Cable 42 in the Steam library, with its own artwork (the way RetroDECK / Steam ROM Manager
do it): Steam has no way to ask an app for artwork, so we write it where Steam looks.

  add_to_steam()     Settings → Shortcuts → "Add to Steam": a non-Steam shortcut (launch option
                     "tv") in every Steam account's shortcuts.vdf, plus the artwork. Steam must be
                     closed, because it rewrites shortcuts.vdf when it exits.
  ensure_artwork()   on every start: if a Steam shortcut for Neon Cable 42 exists (e.g. added by hand
                     with "Add a Non-Steam Game") and has no artwork yet, add the missing images.
                     Only image files are written, so this is safe while Steam runs (it shows them
                     after a Steam restart). Images the owner set themselves are never replaced.

Artwork (drawn in code, nothing downloaded) goes to userdata/<account>/config/grid/<appid>…:
  p.png 600x900 cover · .png 920x430 wide capsule · _hero.png 1920x620 · _logo.png · _icon.png
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
import zlib
from pathlib import Path

from . import desktop, paths

_log = logging.getLogger("getsome.steam_shortcut")
ART_KINDS = ("p", "", "_hero", "_logo", "_icon")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


# ------------------------------------------------------------------ binary VDF (shortcuts.vdf)

def vdf_load(data: bytes) -> dict:
    pos = 0

    def cstr() -> str:
        nonlocal pos
        end = data.index(b"\x00", pos)
        s = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return s

    def mapping() -> dict:
        nonlocal pos
        out: dict = {}
        while True:
            t = data[pos]
            pos += 1
            if t == 0x08:
                return out
            key = cstr()
            if t == 0x00:
                out[key] = mapping()
            elif t == 0x01:
                out[key] = cstr()
            elif t == 0x02:
                out[key] = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            elif t == 0x03:
                out[key] = ("float", struct.unpack_from("<f", data, pos)[0])
                pos += 4
            elif t == 0x07:
                out[key] = ("uint64", struct.unpack_from("<Q", data, pos)[0])
                pos += 8
            else:
                raise ValueError(f"unknown VDF field type {t:#x} at {pos - 1}")

    return mapping()


def vdf_dump(obj: dict) -> bytes:
    out = bytearray()

    def mapping(m: dict) -> None:
        for k, v in m.items():
            key = k.encode("utf-8") + b"\x00"
            if isinstance(v, dict):
                out.extend(b"\x00" + key)
                mapping(v)
            elif isinstance(v, str):
                out.extend(b"\x01" + key + v.encode("utf-8") + b"\x00")
            elif isinstance(v, tuple) and v[0] == "float":
                out.extend(b"\x03" + key + struct.pack("<f", v[1]))
            elif isinstance(v, tuple) and v[0] == "uint64":
                out.extend(b"\x07" + key + struct.pack("<Q", v[1]))
            else:
                out.extend(b"\x02" + key + struct.pack("<I", int(v) & 0xFFFFFFFF))
        out.append(0x08)

    mapping(obj)
    return bytes(out)


# ------------------------------------------------------------------ where Steam keeps things

def steam_roots() -> list[Path]:
    home = Path.home()
    cands = [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
             home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
             home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam"]
    if os.environ.get("GETSOME_STEAM_ROOT"):          # tests
        cands = [Path(os.environ["GETSOME_STEAM_ROOT"])]
    out, seen = [], set()
    for c in cands:
        try:
            real = c.resolve()
        except OSError:
            continue
        if (real / "userdata").is_dir() and real not in seen:
            seen.add(real)
            out.append(real)
    return out


def accounts() -> list[Path]:
    """userdata/<account id>/config folders of every Steam account that has signed in here."""
    out = []
    for root in steam_roots():
        for d in sorted((root / "userdata").iterdir()):
            if d.is_dir() and d.name.isdigit() and d.name != "0":
                out.append(d / "config")
    return out


def steam_running() -> bool:
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            name = (proc / "comm").read_text().strip()
        except OSError:
            continue
        if name in ("steam", "steamwebhelper"):
            return True
    return False


def shortcut_appid(exe: str, name: str) -> int:
    """The id Steam gives a non-Steam shortcut (and names its grid images after)."""
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def _is_ours(entry: dict) -> bool:
    exe = str(entry.get("Exe") or entry.get("exe") or "")
    name = str(entry.get("AppName") or entry.get("appname") or "")
    return paths.LAUNCHER in exe or name.strip().lower() == paths.APP_NAME.lower()


def _entry_appid(entry: dict) -> int:
    aid = entry.get("appid")
    if isinstance(aid, int) and aid:
        return aid & 0xFFFFFFFF
    return shortcut_appid(str(entry.get("Exe") or entry.get("exe") or ""),
                          str(entry.get("AppName") or entry.get("appname") or ""))


def _read_shortcuts(cfg: Path) -> dict:
    f = cfg / "shortcuts.vdf"
    if not f.exists():
        return {"shortcuts": {}}
    data = vdf_load(f.read_bytes())
    data.setdefault("shortcuts", {})
    return data


def our_shortcuts() -> list[tuple[Path, int]]:
    ""f"(account config folder, appid) of every Steam shortcut that starts {paths.APP_NAME}."""
    out = []
    for cfg in accounts():
        try:
            data = _read_shortcuts(cfg)
        except (OSError, ValueError) as e:
            _log.warning("can't read %s: %s", cfg / "shortcuts.vdf", e)
            continue
        for entry in data["shortcuts"].values():
            if isinstance(entry, dict) and _is_ours(entry):
                out.append((cfg, _entry_appid(entry)))
    return out


# ------------------------------------------------------------------ artwork

def _has_art(grid: Path, appid: int, kind: str) -> bool:
    return any((grid / f"{appid}{kind}{ext}").exists() for ext in IMG_EXT)


def write_artwork(cfg: Path, appid: int, only_missing: bool = True) -> int:
    """Draw the images Steam shows for this shortcut. Returns how many were written."""
    from . import steam_art

    grid = cfg / "grid"
    grid.mkdir(parents=True, exist_ok=True)
    n = 0
    for kind in ART_KINDS:
        if only_missing and _has_art(grid, appid, kind):
            continue
        steam_art.render(kind).save(str(grid / f"{appid}{kind}.png"))
        n += 1
    return n


def ensure_artwork() -> int:
    ""f"Startup safety net: add artwork to {paths.APP_NAME} shortcuts that have none. Returns images written."""
    total = 0
    for cfg, appid in our_shortcuts():
        try:
            total += write_artwork(cfg, appid, only_missing=True)
        except OSError as e:
            _log.warning("can't write Steam artwork in %s: %s", cfg, e)
    if total:
        _log.info("added %d Steam artwork images (Steam shows them after a restart)", total)
    return total


# ------------------------------------------------------------------ add to Steam

class SteamError(Exception):
    pass


def add_to_steam() -> str:
    """Create (or refresh) the Steam shortcut and its artwork in every Steam account. Returns a
    plain-English summary; raises SteamError with one when it can't."""
    cfgs = accounts()
    if not cfgs:
        raise SteamError("Steam isn't set up on this computer yet (or you haven't signed in to it once).")
    if steam_running():
        raise SteamError("Steam is open. Close it first (in Steam: Steam menu → Exit), then press this again.")
    launcher = desktop.launcher()
    exe, start = f'"{launcher}"', f'"{launcher.parent}"'
    added = refreshed = 0
    for cfg in cfgs:
        f = cfg / "shortcuts.vdf"
        try:
            data = _read_shortcuts(cfg)
        except ValueError as e:
            raise SteamError(f"Steam's shortcut list in {cfg} couldn't be read ({e}); nothing was changed.") from e
        sc = data["shortcuts"]
        mine = next(((k, v) for k, v in sc.items() if isinstance(v, dict) and _is_ours(v)), None)
        if mine:
            appid = _entry_appid(mine[1])
            refreshed += 1
        else:
            appid = shortcut_appid(exe, paths.APP_NAME)
            icon = cfg / "grid" / f"{appid}_icon.png"
            key = str(max([int(k) for k in sc if k.isdigit()], default=-1) + 1)
            sc[key] = {
                "appid": appid, "AppName": paths.APP_NAME, "Exe": exe, "StartDir": start,
                "icon": str(icon), "ShortcutPath": "", "LaunchOptions": "tv", "IsHidden": 0,
                "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0, "DevkitGameID": "",
                "DevkitOverrideAppID": 0, "LastPlayTime": 0, "FlatpakAppID": "", "tags": {},
            }
            if f.exists():
                shutil.copy2(f, f.with_name("shortcuts.vdf.before-neon-cable-42"))
            tmp = f.with_name("shortcuts.vdf.tmp")
            tmp.write_bytes(vdf_dump(data))
            os.replace(tmp, f)
            added += 1
        write_artwork(cfg, appid, only_missing=bool(mine))
    who = f"{len(cfgs)} Steam accounts" if len(cfgs) > 1 else "your Steam account"
    if added and not refreshed:
        return (f"Added to {who} with its artwork. Open Steam: {paths.APP_NAME} is in your library and starts "
                "straight in TV mode.")
    if added:
        return f"Added to Steam where it was missing, with artwork, for {who}."
    return (f"{paths.APP_NAME} was already in Steam; any missing artwork has been added. "
            "Restart Steam to see it.")
