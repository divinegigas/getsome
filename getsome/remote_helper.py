"""Make a TV remote's OK button work everywhere (run as root through pkexec by Settings → Remote control).

Why: X11 windows, and so Steam Game Mode (gamescope runs apps under XWayland), can't receive key codes
above 255. A remote's OK button usually sends KEY_OK (352) or KEY_SELECT (353), so it never arrives.
Fix: a udev hwdb rule that turns that remote's OK scancode into KEY_ENTER (28), for that remote only.

  install [seconds]   listen on keyboard-like input devices until a key with a code above 255 that we
                      know (OK/Select/Enter-like) is pressed; write the rule; reload hwdb
  remove              delete the rule; reload hwdb
Prints one JSON object per line for the GUI: {"status": ...}. Nothing typed is stored or printed; only
the OK button's scancode and the remote's USB ids end up in the rule file.

Standard library only (it runs as root with `python3 -I`).
"""

from __future__ import annotations

import json
import os
import re
import select
import struct
import subprocess
import sys
import time

RULE = "/etc/udev/hwdb.d/70-neon-cable-42-remote.hwdb"
DEV_DIR, DEVICES = "/dev/input", "/proc/bus/input/devices"
EV_KEY, EV_MSC, MSC_SCAN = 0x01, 0x04, 0x04
# remote buttons that X11 can't receive -> what they should become (hwdb key names)
REMAP = {352: "enter",      # KEY_OK
         353: "enter"}      # KEY_SELECT
EVENT = struct.Struct("llHHi")        # struct input_event on 64-bit Linux


def say(**kw) -> None:
    print(json.dumps(kw), flush=True)


def devices() -> list[dict]:
    """Keyboard-like input devices from /proc/bus/input/devices: bus, vendor, product, name, event node."""
    out, cur = [], {}
    try:
        text = open(DEVICES).read()
    except OSError:
        return out
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur.get("event") and cur.get("kbd"):
                out.append(cur)
            cur = {}
        elif line.startswith("I:"):
            m = re.search(r"Bus=(\w+) Vendor=(\w+) Product=(\w+)", line)
            if m:
                cur.update(bus=m.group(1).upper(), vendor=m.group(2).upper(), product=m.group(3).upper())
        elif line.startswith("N:"):
            cur["name"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("H:"):
            m = re.search(r"\b(event\d+)\b", line)
            cur["event"] = m.group(1) if m else ""
            cur["kbd"] = "kbd" in line
    return out


def match_line(d: dict) -> str:
    return f"evdev:input:b{d['bus']:0>4}v{d['vendor']:0>4}p{d['product']:0>4}*"


def listen(seconds: float) -> tuple[dict, str, int] | None:
    """Wait for an OK-like key on any keyboard device; returns (device, scancode hex, keycode)."""
    fds = {}
    for d in devices():
        try:
            fds[os.open(f"{DEV_DIR}/{d['event']}", os.O_RDONLY | os.O_NONBLOCK)] = d
        except OSError:
            pass
    if not fds:
        return None
    say(status="listening")
    last_scan: dict[int, int] = {}
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            ready, _, _ = select.select(list(fds), [], [], 0.5)
            for fd in ready:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except OSError:
                    continue
                for k in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _s, _us, typ, code, value = EVENT.unpack_from(data, k)
                    if typ == EV_MSC and code == MSC_SCAN:
                        last_scan[fd] = value & 0xFFFFFFFF
                    elif typ == EV_KEY and value == 1 and code in REMAP:
                        if fd not in last_scan:
                            say(status="noscan")       # this receiver doesn't report scancodes
                            continue
                        return fds[fd], f"{last_scan[fd]:x}", code
    finally:
        for fd in fds:
            os.close(fd)
    return None


def reload() -> None:
    subprocess.run(["systemd-hwdb", "update"], check=False)
    subprocess.run(["udevadm", "trigger", "--subsystem-match=input", "--action=change"], check=False)


def install(seconds: float) -> int:
    found = listen(seconds)
    if not found:
        say(status="nothing", message="No OK button press was seen. Is the remote's receiver plugged in? "
                                      "If OK already works, there's nothing to fix.")
        return 1
    dev, scan, code = found
    lines = ["# Neon Cable 42: the remote's OK button becomes Enter, so X11 windows and Steam Game Mode",
             "# receive it (they can't receive key codes above 255). Remove with Settings → Remote control.",
             f"# {dev.get('name', 'remote')}: scancode {scan} was key {code}",
             match_line(dev), f" KEYBOARD_KEY_{scan}={REMAP[code]}", ""]
    os.makedirs(os.path.dirname(RULE), exist_ok=True)
    old = ""
    try:
        old = open(RULE).read()
    except OSError:
        pass
    # keep fixes for other remotes (one block per remote)
    blocks = [b for b in old.split("\n\n") if b.strip() and match_line(dev) not in b]
    with open(RULE, "w") as f:
        f.write("\n\n".join(blocks + ["\n".join(lines)]).strip() + "\n")
    reload()
    say(status="done", name=dev.get("name", "remote"))
    return 0


def remove() -> int:
    try:
        os.remove(RULE)
    except FileNotFoundError:
        pass
    reload()
    say(status="removed")
    return 0


if __name__ == "__main__":
    if os.geteuid() != 0:
        say(status="error", message="needs to run as root (through pkexec)")
        sys.exit(2)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        sys.exit(install(float(sys.argv[2]) if len(sys.argv) > 2 else 20))
    if cmd == "remove":
        sys.exit(remove())
    say(status="error", message="usage: install [seconds] | remove")
    sys.exit(2)
