"""Action <-> key mapping.

USB IR remotes present themselves as keyboards, so everything here is plain Qt
key handling. The map is stored as JSON (action -> list of key names, using Qt's
QKeySequence names such as "PgUp", "Media Next", "8") in
~/.config/getsome/keymap.json. The phase-5 remap screen will edit that file.
"""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from .. import paths

_log = logging.getLogger("getsome.keys")

DEFAULT_KEYMAP: dict[str, list[str]] = {
    "channel_up": ["PgUp", "Up", "+", "=", "Media Next", "ChannelUp"],
    "channel_down": ["PgDown", "Down", "-", "Media Previous", "ChannelDown"],
    "last_channel": ["L", "Media Last"],
    "enter": ["Return", "Enter", "Select"],
    "info": ["I", "Info"],
    "menu": ["Menu", "O", "Tab"],
    "left": ["Left"],
    "right": ["Right"],
    "mute": ["M", "Volume Mute"],
    "volume_up": ["Volume Up", "]"],
    "volume_down": ["Volume Down", "["],
    "fullscreen": ["F", "F11"],
    # BACK opens the exit menu (back to TV / quit to setup / quit to desktop).
    # IR remotes send Backspace, Back or Esc for their back button.
    "back": ["Backspace", "Back", "Esc", "Q", "Stop"],
    "quit": [],          # older keymaps: treated like "back"
}
DIGITS = {int(getattr(Qt.Key, f"Key_{d}").value): str(d) for d in range(10)}


def _key_code(name: str) -> int | None:
    seq = QKeySequence(name)
    if seq.count() == 0:
        return None
    combo = seq[0]
    return combo.key().value if hasattr(combo, "key") else int(combo)


class KeyMap:
    def __init__(self):
        self.path = paths.config_dir() / "keymap.json"
        self.mapping = {k: list(v) for k, v in DEFAULT_KEYMAP.items()}
        if self.path.exists():
            try:
                user = json.loads(self.path.read_text())
                for action, keys in user.items():
                    if action in self.mapping and isinstance(keys, list):
                        self.mapping[action] = [str(k) for k in keys]
            except Exception as e:
                _log.warning("ignoring bad keymap %s: %s", self.path, e)
        self._lookup: dict[int, str] = {}
        for action, keys in self.mapping.items():
            for name in keys:
                code = _key_code(name)
                if code is not None:
                    self._lookup.setdefault(code, action)

    def action_for(self, key: int) -> str | None:
        return self._lookup.get(int(key))

    @staticmethod
    def digit_for(key: int) -> str | None:
        return DIGITS.get(key)
