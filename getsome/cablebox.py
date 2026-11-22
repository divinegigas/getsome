"""Cable box: watch the channels of a head-end (headend.py) on this computer, with no library or
channels of its own. Everything on screen is still drawn here; the head-end answers the schedule
questions and streams the files.

Activation: settings["cable_box"] = {"host", "port", "token", "name"} (set by pairing in Settings) and
TV mode calling install(), which reroutes the few functions TV mode asks (fs42.channels/segment_at/
programme_at/programmes/now_and_next, ads.card_for/promo_for/on_this_day/episode_details,
artwork.for_programme, model.load for restricted channels) to the head-end. media(path) turns a
head-end path into a URL for mpv; fetch(path) downloads a small file (artwork) into a cache.
Generated media this computer makes itself (bars, static, guide music, beds) stays local.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from . import paths

_log = logging.getLogger("getsome.cablebox")
_conf: dict | None = None
_info: dict = {}


class Unreachable(Exception):
    pass


def config() -> dict:
    from . import settings

    c = settings.load().get("cable_box") or {}
    return c if c.get("host") and c.get("token") else {}


def active() -> bool:
    return _conf is not None


def base() -> str:
    return f"http://{_conf['host']}:{_conf.get('port', 4242)}"


# ------------------------------------------------------------------ talking to the head-end

def _post(url: str, body: dict, token: str | None = None, timeout: float = 6.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-NC42-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"error": f"head-end answered {e.code}"}
    except (OSError, ValueError) as e:
        raise Unreachable(str(e)) from e


def call(fn: str, *args, timeout: float = 6.0):
    res = _post(base() + "/api/call", {"fn": fn, "args": list(args)}, _conf["token"], timeout)
    if "error" in res:
        raise Unreachable(res["error"])
    return res["result"]


def discover(wait: float = 1.5) -> list[dict]:
    """Head-ends on the home network (UDP broadcast). [{host, port, name, pairing}]"""
    from .headend import DISCOVERY_PORT

    out, seen = [], set()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    try:
        for _ in range(2):
            s.sendto(b"NC42?", ("255.255.255.255", DISCOVERY_PORT))
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                hello = json.loads(data)
            except ValueError:
                continue
            if addr[0] not in seen:
                seen.add(addr[0])
                out.append({"host": addr[0], **hello})
    except OSError as e:
        _log.info("discovery failed: %s", e)
    finally:
        s.close()
    return out


def hello(host: str, port: int = 4242) -> dict:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/hello", timeout=4) as r:
            return json.loads(r.read())
    except (OSError, ValueError) as e:
        raise Unreachable(str(e)) from e


def pair(host: str, code: str, port: int = 4242) -> dict:
    """Pair with a head-end using the code it shows; saves and returns the cable-box settings."""
    from . import settings

    res = _post(f"http://{host}:{port}/api/pair", {"code": code, "box": socket.gethostname()})
    if "token" not in res:
        raise Unreachable(res.get("error", "pairing failed"))
    conf = {"host": host, "port": port, "token": res["token"], "name": res.get("name", host)}
    s = settings.load()
    s["cable_box"] = conf
    settings.save(s)
    return conf


def unpair() -> None:
    from . import settings

    s = settings.load()
    s["cable_box"] = {}
    settings.save(s)


# ------------------------------------------------------------------ files

def _local_runtime() -> str:
    return (paths.fs42_work_dir() / "runtime").as_posix().rstrip("/") + "/"


def media(path: str) -> str:
    """What mpv should open: generated media made here stays local, everything else streams."""
    if not active() or not path or "://" in path:
        return path
    if path.startswith(_local_runtime()) and os.path.exists(path):
        return path
    return f"{base()}/f/{_conf['token']}/{quote(path, safe='')}"


def _cache_dir() -> Path:
    d = paths.data_dir() / "cablebox_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(path: str) -> str:
    """A small head-end file (artwork) as a local file; "" if it can't be had."""
    if not active() or not path:
        return path
    if path.startswith(_local_runtime()) and os.path.exists(path):
        return path
    ext = os.path.splitext(path)[1][:6]
    local = _cache_dir() / (hashlib.sha1(path.encode()).hexdigest() + ext)
    if local.exists():
        return str(local)
    try:
        with urllib.request.urlopen(media(path), timeout=8) as r:
            data = r.read(40 << 20)
        tmp = local.with_suffix(local.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(local)
        return str(local)
    except (OSError, ValueError) as e:
        _log.debug("couldn't fetch %s: %s", path, e)
        return ""


# ------------------------------------------------------------------ rerouting TV mode

def _d(x):
    return dt.datetime.fromisoformat(x) if x else None


def install() -> bool:
    """Called before TV mode starts. Returns True when this computer is a cable box."""
    global _conf, _info
    conf = config()
    if not conf:
        return False
    _conf = conf
    from . import ads, artwork, fs42, model

    class RemoteProgramme(fs42.Programme):
        def episode(self):
            return tuple(self._ep) if getattr(self, "_ep", None) else ("", "")

    def prog(d):
        if not d:
            return None
        p = RemoteProgramme(d["title"], _d(d["start"]), _d(d["end"]), d.get("path"))
        p._ep = d.get("ep") or ["", ""]
        return p

    def segment_at(name, when):
        try:
            d = call("segment_at", name, when.isoformat())
        except Unreachable as e:
            raise fs42.NoSignal(f"can't reach the head-end: {e}") from e
        if "nosignal" in d:
            raise fs42.NoSignal(d["nosignal"])
        return fs42.Segment(d["path"], d["file_pos"], d["file_end"], _d(d["starts_at"]), _d(d["ends_at"]),
                            d["skip"], d["title"], d["content_type"], d["media_type"], d["is_stream"])

    def safe(fn, default):
        def wrapped(*a, **k):
            try:
                return fn(*a, **k)
            except Unreachable as e:
                _log.warning("head-end unreachable: %s", e)
                return default() if callable(default) else default
        return wrapped

    try:
        _info = call("info", timeout=8)
    except Unreachable as e:
        _log.warning("head-end %s unreachable: %s", conf.get("host"), e)
        _info = {"channels": [], "restricted": [], "guide_channel": 0}

    fs42.channels = lambda include_hidden=False: [
        fs42.Channel(c["number"], c["name"], c["network_type"], c["hidden"]) for c in _info.get("channels", [])
        if include_hidden or not c["hidden"]]
    fs42.segment_at = segment_at
    fs42.programme_at = safe(lambda name, when: prog(call("programme_at", name, when.isoformat())), None)
    fs42.programmes = safe(lambda name, start, end, limit=40: [
        prog(p) for p in call("programmes", name, start.isoformat(), end.isoformat(), limit)], list)
    fs42.now_and_next = safe(lambda name, when: tuple(prog(p) for p in call("now_and_next", name, when.isoformat())),
                             (None, None))
    ads.card_for = safe(lambda path: call("card_for", path), dict)
    ads.promo_for = safe(lambda path: call("promo_for", path), dict)
    ads.on_this_day = safe(lambda path, when=None: tuple(call("on_this_day", path, when.isoformat() if when else None)),
                           ("ON THIS DAY", []))
    ads.episode_details = safe(lambda path: tuple(call("episode_details", path)), ("", ""))
    artwork.for_programme = safe(lambda path: call("artwork", path) if path else {}, dict)
    model.load = lambda: [model.Channel(r["number"], r["name"], restricted=True, lock_mode=r["lock_mode"],
                                        free_from=r["free_from"], free_to=r["free_to"])
                          for r in _info.get("restricted", [])]
    _log.info("cable box mode: watching %s (%s)", conf.get("name"), conf.get("host"))
    return True


def shared_settings() -> dict:
    """The head-end's settings that decide what the box shows."""
    keys = ("guide_channel", "restricted_pin", "relock_minutes")
    return {k: _info[k] for k in keys if k in _info}
