"""Game controllers (Xbox/XInput, PlayStation, 8BitDo, Steam's virtual pad …) in TV mode.

Reads the kernel's joystick devices (/dev/input/js*), which a logged-in desktop user may read
without extra permissions or packages. Buttons are identified by their evdev code (via the
JSIOCGBTNMAP / JSIOCGAXMAP ioctls), so the layout is the same on every controller: "south" is A on
Xbox and Cross on PlayStation. Devices are picked up when plugged in (rescanned every 2 s).

Actions are the same names the keyboard map uses (keys.DEFAULT_KEYMAP), plus "guide".
Held d-pad/stick directions repeat like a held key.
"""

from __future__ import annotations

import errno
import fcntl
import glob
import logging
import os
import struct
import time

_log = logging.getLogger("getsome.gamepad")

JS_EVENT_BUTTON, JS_EVENT_AXIS, JS_EVENT_INIT = 0x01, 0x02, 0x80
EVENT = struct.Struct("<IhBB")          # time (ms), value, type, number

# evdev codes
BTN_SOUTH, BTN_EAST, BTN_C, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x132, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2, BTN_SELECT, BTN_START, BTN_MODE = 0x136, 0x137, 0x138, 0x139, 0x13a, 0x13b, 0x13c
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
ABS_X, ABS_Y, ABS_Z, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x02, 0x05, 0x10, 0x11
ABS_GAS, ABS_BRAKE = 0x09, 0x0a

BUTTONS = {
    BTN_SOUTH: "enter", BTN_EAST: "back", BTN_WEST: "info", BTN_NORTH: "last_channel",
    BTN_START: "menu", BTN_SELECT: "guide", BTN_TL: "channel_down", BTN_TR: "channel_up",
    BTN_TL2: "volume_down", BTN_TR2: "volume_up",
    BTN_DPAD_UP: "channel_up", BTN_DPAD_DOWN: "channel_down", BTN_DPAD_LEFT: "left", BTN_DPAD_RIGHT: "right",
}
# xpad's order, for the rare device whose button map can't be read
XPAD_ORDER = [BTN_SOUTH, BTN_EAST, BTN_WEST, BTN_NORTH, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE]
XPAD_AXES = [ABS_X, ABS_Y, ABS_Z, 0x03, 0x04, ABS_RZ, ABS_HAT0X, ABS_HAT0Y]

STICK = 0.6          # how far a stick must move to count as a d-pad press
REPEAT_DELAY, REPEAT_EVERY = 0.45, 0.16


def _ioc_read(nr: int, size: int) -> int:
    return (2 << 30) | (size << 16) | (ord("j") << 8) | nr


JSIOCGAXES = _ioc_read(0x11, 1)
JSIOCGBUTTONS = _ioc_read(0x12, 1)
JSIOCGNAME = lambda n: _ioc_read(0x13, n)        # noqa: E731
JSIOCGAXMAP = _ioc_read(0x32, 64)
JSIOCGBTNMAP = _ioc_read(0x34, 1024)


class Pad:
    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.name = path
        self.btn_codes: list[int] = list(XPAD_ORDER)
        self.axis_codes: list[int] = list(XPAD_AXES)
        try:
            buf = bytearray(128)
            fcntl.ioctl(self.fd, JSIOCGNAME(128), buf)
            self.name = buf.split(b"\0", 1)[0].decode("utf-8", "replace") or path
            nb = bytearray(1)
            fcntl.ioctl(self.fd, JSIOCGBUTTONS, nb)
            na = bytearray(1)
            fcntl.ioctl(self.fd, JSIOCGAXES, na)
            bmap = bytearray(1024)
            fcntl.ioctl(self.fd, JSIOCGBTNMAP, bmap)
            self.btn_codes = list(struct.unpack_from(f"<{nb[0]}H", bmap))
            amap = bytearray(64)
            fcntl.ioctl(self.fd, JSIOCGAXMAP, amap)
            self.axis_codes = list(amap[:na[0]])
        except OSError:
            pass            # not a real joystick device (tests) or an old kernel: xpad order
        self.axes: dict[int, float] = {}

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


class Gamepads:
    """Poll from a Qt timer: gamepads.poll() returns the actions to perform now."""

    def __init__(self, pattern: str | None = None):
        self.pattern = pattern or os.environ.get("GETSOME_JS_GLOB", "/dev/input/js*")
        self.pads: dict[str, Pad] = {}
        self._held: dict[str, float] = {}       # direction action -> when it next repeats
        self._dirs: dict[tuple[str, str], str] = {}   # (pad, axis group) -> direction action held
        self._last_scan = 0.0
        self._last: tuple[str, str, float] | None = None   # (action, pad, when): de-duplicates

    def _scan(self) -> None:
        self._last_scan = time.monotonic()
        for path in sorted(glob.glob(self.pattern)):
            if path in self.pads:
                continue
            try:
                pad = Pad(path)
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.ENOENT, errno.EPERM):
                    _log.debug("can't open %s: %s", path, e)
                continue
            self.pads[path] = pad
            _log.info("game controller connected: %s (%s)", pad.name, path)

    def close(self) -> None:
        for p in self.pads.values():
            p.close()
        self.pads.clear()

    def poll(self) -> list[str]:
        if time.monotonic() - self._last_scan > 2.0:
            self._scan()
        out: list[str] = []
        for path, pad in list(self.pads.items()):
            while True:
                try:
                    data = os.read(pad.fd, EVENT.size * 32)
                except BlockingIOError:
                    break
                except OSError:                 # unplugged
                    _log.info("game controller disconnected: %s", pad.name)
                    pad.close()
                    del self.pads[path]
                    self._release(path)
                    break
                if not data:
                    break
                for k in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _t, value, typ, num = EVENT.unpack_from(data, k)
                    self._event(pad, typ, num, value, out)
        # repeat held directions
        now = time.monotonic()
        for action, due in list(self._held.items()):
            if now >= due:
                out.append(action)
                self._held[action] = now + REPEAT_EVERY
        return out

    def _emit(self, pad: Pad, action: str, out: list[str]) -> None:
        """Steam Input shows a virtual copy of the controller next to the real one: the same press
        then arrives from two devices at once. Keep the first."""
        now = time.monotonic()
        last = self._last
        self._last = (action, pad.path, now)
        if last and last[0] == action and last[1] != pad.path and now - last[2] < 0.12:
            return
        out.append(action)

    def _release(self, path: str) -> None:
        for key in [k for k in self._dirs if k[0] == path]:
            self._held.pop(self._dirs.pop(key), None)

    def _press_dir(self, pad: Pad, group: str, action: str | None, out: list[str]) -> None:
        key = (pad.path, group)
        old = self._dirs.get(key)
        if old == action:
            return
        if old:
            self._held.pop(old, None)
            del self._dirs[key]
        if action:
            self._dirs[key] = action
            self._emit(pad, action, out)
            if action in ("channel_up", "channel_down", "left", "right"):
                self._held[action] = time.monotonic() + REPEAT_DELAY

    def _event(self, pad: Pad, typ: int, num: int, value: int, out: list[str]) -> None:
        if typ & JS_EVENT_INIT:
            if typ & JS_EVENT_AXIS and num < len(pad.axis_codes):
                pad.axes[pad.axis_codes[num]] = value / 32767.0     # remember resting positions
            return
        if typ & JS_EVENT_BUTTON:
            code = pad.btn_codes[num] if num < len(pad.btn_codes) else -1
            action = BUTTONS.get(code)
            if not action:
                return
            if code in (BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT):
                self._press_dir(pad, f"dpad{code}", action if value else None, out)
            elif value:
                self._emit(pad, action, out)
            return
        if typ & JS_EVENT_AXIS:
            code = pad.axis_codes[num] if num < len(pad.axis_codes) else -1
            v = value / 32767.0
            rest = pad.axes.get(code)
            pad.axes[code] = v
            if code in (ABS_HAT0Y, ABS_Y):
                act = "channel_up" if v < -STICK else "channel_down" if v > STICK else None
                self._press_dir(pad, f"y{code}", act, out)
            elif code in (ABS_HAT0X, ABS_X):
                act = "left" if v < -STICK else "right" if v > STICK else None
                self._press_dir(pad, f"x{code}", act, out)
            elif code in (ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE):
                # analog triggers rest fully released (-1). On some cheap pads Z/RZ are the right
                # stick instead (resting at 0): those are ignored.
                if rest is None or rest > -0.5:
                    pad.axes[code] = rest if rest is not None else v
                    return
                pad.axes[code] = rest        # keep the resting value, compare against it
                act = ("volume_down" if code in (ABS_Z, ABS_BRAKE) else "volume_up") if v > 0.0 else None
                self._press_dir(pad, f"t{code}", act, out)
