"""Filesystem locations used by GetSome.

Everything user-specific lives under XDG dirs so the app itself (repo checkout or
AppImage) stays read-only:

    $XDG_CONFIG_HOME/getsome/      settings.json, keymap.json
    $XDG_DATA_HOME/getsome/fs42/   FS42 working dir (confs/, runtime/, catalog/)
    $XDG_DATA_HOME/getsome/overrides/  user art/clips that replace generated assets
"""

import os
from pathlib import Path

APP_NAME = "Neon Cable 42"          # the name people see (folders and the package stay "getsome")
APP_DIR = Path(__file__).resolve().parent
ASSETS_DIR = APP_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"


def _repo_root() -> Path:
    return APP_DIR.parent


def fs42_source_dir() -> Path:
    """Location of the (unmodified) FieldStation42 checkout."""
    env = os.environ.get("GETSOME_FS42_DIR")
    if env:
        return Path(env)
    return _repo_root() / "vendor" / "FieldStation42"


def _xdg(var: str, fallback: str) -> Path:
    base = os.environ.get(var) or str(Path.home() / fallback)
    return Path(base) / "getsome"


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    env = os.environ.get("GETSOME_DATA_DIR")
    if env:
        return Path(env)
    return _xdg("XDG_DATA_HOME", ".local/share")


def fs42_work_dir() -> Path:
    """FS42 resolves every path relative to its cwd; this is that cwd."""
    return data_dir() / "fs42"


def overrides_dir() -> Path:
    return data_dir() / "overrides"


def ensure_dirs() -> None:
    work = fs42_work_dir()
    for d in (config_dir(), work / "confs", work / "runtime", work / "catalog", overrides_dir()):
        d.mkdir(parents=True, exist_ok=True)
