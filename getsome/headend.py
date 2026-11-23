"""Head-end: share this computer's channels with cable boxes on the home network.

A cable box (another computer running Neon Cable 42, see cablebox.py) asks the head-end what is on,
and streams the files from it; it draws everything else itself. Nothing leaves the home network,
nothing is converted (so the head-end barely works), and there are no accounts.

  UDP 4243  discovery: a box broadcasts b"NC42?" and every head-end answers with its hello JSON
  TCP 4242  HTTP:
    GET  /api/hello                        no auth: name, version, whether pairing is open
    POST /api/pair    {"code", "box"}      the 6-digit code shown in Settings -> a token
    POST /api/call    {"fn", "args"}       token: schedule questions (CALLS below)
    GET  /f/<token>/<quoted path>          token: one file, with HTTP Range (seeking)

Security: only paired boxes (token in the request), and only files inside the folders that make up
the channels (Neon Cable 42's data folder, the library and movie folders, channels' own ad and
infomercial folders), checked after resolving symlinks.
Fedora Workstation's firewall allows ports 1025-65535 by default, so nothing needs opening there.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from . import paths

_log = logging.getLogger("getsome.headend")
PORT, DISCOVERY_PORT = (4252, 4253) if paths.XCABLE else (4242, 4243)   # editions never mix
PROTOCOL = 1
PAIR_MINUTES = 10


# ------------------------------------------------------------------ pairing (shared with the GUI via files)

def _boxes_file() -> Path:
    return paths.config_dir() / "cable_boxes.json"


def _code_file() -> Path:
    return paths.config_dir() / "pair_code.json"


def paired_boxes() -> dict:
    try:
        return json.loads(_boxes_file().read_text())
    except (OSError, ValueError):
        return {}


def forget_boxes() -> None:
    _boxes_file().unlink(missing_ok=True)


def new_pair_code() -> str:
    """Called by Settings: a 6-digit code the box must enter within PAIR_MINUTES."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    paths.ensure_dirs()
    _code_file().write_text(json.dumps({"code": code, "until": time.time() + PAIR_MINUTES * 60}))
    return code


def close_pairing() -> None:
    _code_file().unlink(missing_ok=True)


def _pair(code: str, box: str) -> str | None:
    try:
        c = json.loads(_code_file().read_text())
    except (OSError, ValueError):
        return None
    if time.time() > c.get("until", 0) or not secrets.compare_digest(str(code).strip(), c.get("code", "")):
        return None
    token = secrets.token_urlsafe(24)
    boxes = paired_boxes()
    boxes[token] = {"name": (box or "cable box")[:60], "added": dt.datetime.now().isoformat(timespec="seconds")}
    _boxes_file().write_text(json.dumps(boxes, indent=1))
    close_pairing()
    return token


# ------------------------------------------------------------------ what the box may ask

def _allowed_roots() -> list[str]:
    from . import model, settings

    s = settings.load()
    roots = [paths.data_dir()] + [Path(f) for f in s.get("library_folders", []) + s.get("movie_folders", [])]
    for c in model.load():
        roots += [Path(f) for f in (c.ad_folder, c.infomercials) if f]
        roots += [Path(sh.folder) for sh in c.all_show_refs()]
        roots += [Path(m).parent for m in c.movies]
    out = []
    for r in roots:
        try:
            out.append(os.path.realpath(r).rstrip("/") + "/")
        except OSError:
            pass
    return sorted(set(out))


def _dt(x):
    return dt.datetime.fromisoformat(x) if isinstance(x, str) else x


def _prog(p) -> dict | None:
    if p is None:
        return None
    return {"title": p.title, "start": p.start.isoformat(), "end": p.end.isoformat(), "path": p.path,
            "ep": list(p.episode()) if p.path else ["", ""]}


def _seg(s) -> dict:
    return {"path": s.path, "file_pos": s.file_pos, "file_end": s.file_end, "starts_at": s.starts_at.isoformat(),
            "ends_at": s.ends_at.isoformat(), "skip": s.skip, "title": s.title, "content_type": s.content_type,
            "media_type": s.media_type, "is_stream": s.is_stream}


def _info() -> dict:
    from . import fs42, model, settings

    s = settings.load()
    return {
        "channels": [{"number": c.number, "name": c.name, "network_type": c.network_type, "hidden": c.hidden}
                     for c in fs42.channels()],
        "guide_channel": int(s.get("guide_channel") or 0),
        "restricted": [{"number": c.number, "name": c.name, "lock_mode": c.lock_mode, "free_from": c.free_from,
                        "free_to": c.free_to} for c in model.load() if c.restricted],
        "restricted_pin": str(s.get("restricted_pin") or "69"),
        "relock_minutes": int(s.get("relock_minutes", 120)),
        "roots": [paths.data_dir().as_posix()] + _allowed_roots(),
    }


def _segment_at(name, when):
    from . import fs42

    try:
        return _seg(fs42.segment_at(name, _dt(when)))
    except fs42.NoSignal as e:
        return {"nosignal": str(e)}


def _calls() -> dict:
    from . import ads, artwork, fs42

    return {
        "info": lambda: _info(),
        "segment_at": _segment_at,
        "programme_at": lambda name, when: _prog(fs42.programme_at(name, _dt(when))),
        "programmes": lambda name, start, end, limit=40: [_prog(p) for p in
                                                          fs42.programmes(name, _dt(start), _dt(end), limit)],
        "now_and_next": lambda name, when: [_prog(p) for p in fs42.now_and_next(name, _dt(when))],
        "card_for": ads.card_for,
        "promo_for": ads.promo_for,
        "on_this_day": lambda path, day=None: list(ads.on_this_day(path, dt.date.fromisoformat(day) if day else None)),
        "episode_details": lambda path: list(ads.episode_details(path)),
        "artwork": artwork.for_programme,
    }


# ------------------------------------------------------------------ HTTP

class _Handler(BaseHTTPRequestHandler):
    server_version = "NeonCable42"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):     # quiet; errors are logged below
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _token_ok(self, token: str | None) -> bool:
        return bool(token) and token in paired_boxes()

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        if self.path == "/api/hello":
            return self._json(self.server.hello())
        if self.path.startswith("/f/"):
            parts = self.path.split("/", 3)
            if len(parts) < 4 or not self._token_ok(parts[2]):
                return self._json({"error": "not paired"}, 403)
            return self._file(unquote(parts[3]))
        self._json({"error": "not found"}, 404)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        body = self._body()
        if self.path == "/api/pair":
            token = _pair(body.get("code", ""), body.get("box", ""))
            if not token:
                return self._json({"error": "That code is wrong or has expired."}, 403)
            _log.info("paired cable box %r", body.get("box"))
            return self._json({"token": token, **self.server.hello()})
        if self.path == "/api/call":
            if not self._token_ok(self.headers.get("X-NC42-Token")):
                return self._json({"error": "not paired"}, 403)
            fn = self.server.calls.get(body.get("fn", ""))
            if fn is None:
                return self._json({"error": "unknown call"}, 400)
            try:
                with self.server.lock:          # FS42's queries aren't made for many threads
                    result = fn(*body.get("args", []))
            except Exception as e:
                _log.warning("call %s failed: %s", body.get("fn"), e)
                return self._json({"error": str(e)}, 500)
            return self._json({"result": result})
        self._json({"error": "not found"}, 404)

    def _file(self, path: str) -> None:
        try:
            real = os.path.realpath(path)
        except OSError:
            return self._json({"error": "not found"}, 404)
        if not any(real.startswith(r) for r in self.server.roots()) or not os.path.isfile(real):
            _log.warning("refused file outside the channels: %s", path)
            return self._json({"error": "not allowed"}, 403)
        self.server.streaming()
        size = os.path.getsize(real)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a:
                    start = int(a)
                    end = int(b) if b else end
                elif b:
                    start = max(0, size - int(b))
            except ValueError:
                pass
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        import mimetypes

        self.send_header("Content-Type", mimetypes.guess_type(real)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(real, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass                     # the box tuned away mid-file: normal


class HeadEnd(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int = PORT):
        super().__init__(("0.0.0.0", port), _Handler)
        self.lock = threading.Lock()
        self.calls = _calls()
        self._roots: list[str] = []
        self._roots_at = 0.0
        self._last_stream = 0.0

    def hello(self) -> dict:
        try:
            pairing = time.time() < json.loads(_code_file().read_text()).get("until", 0)
        except (OSError, ValueError):
            pairing = False
        return {"app": paths.APP_NAME, "protocol": PROTOCOL, "name": socket.gethostname(),
                "port": self.server_address[1], "pairing": pairing}

    def roots(self) -> list[str]:
        if time.monotonic() - self._roots_at > 30:          # channels can change while we run
            self._roots, self._roots_at = _allowed_roots(), time.monotonic()
        return self._roots

    def streaming(self) -> None:
        self._last_stream = time.monotonic()

    def busy(self) -> bool:
        return time.monotonic() - self._last_stream < 120


def _discovery(server: HeadEnd) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError as e:
        _log.warning("discovery unavailable (%s); boxes can still connect by address", e)
        return
    while True:
        try:
            data, addr = s.recvfrom(64)
        except OSError:
            continue
        if data.strip() == b"NC42?":
            try:
                s.sendto(json.dumps(server.hello()).encode(), addr)
            except OSError:
                pass


def serve(port: int = PORT) -> None:
    """`getsome serve`: run until stopped. Keeps the computer awake while a box is streaming, and
    keeps schedules topped up (the setup window or TV mode may not be open on the head-end)."""
    from . import upkeep

    server = HeadEnd(port)
    pidfile().write_text(str(os.getpid()))
    threading.Thread(target=_discovery, args=(server,), name="discovery", daemon=True).start()

    def keeper():
        awake = None
        last_upkeep = 0.0
        while True:
            if time.monotonic() - last_upkeep > 3600:
                last_upkeep = time.monotonic()
                try:
                    upkeep.run()
                except Exception as e:
                    _log.warning("upkeep failed: %s", e)
            try:
                if server.busy():
                    if awake is None:
                        from .tv.awake import KeepAwake

                        awake = KeepAwake()
                    if not awake.active:
                        awake.start()
                elif awake is not None and awake.active:
                    awake.stop()
            except Exception as e:
                _log.debug("keep-awake: %s", e)
            time.sleep(20)

    threading.Thread(target=keeper, name="headend-keeper", daemon=True).start()
    _log.info("sharing channels on port %d", port)
    try:
        server.serve_forever()
    finally:
        pidfile().unlink(missing_ok=True)


# ------------------------------------------------------------------ running it in the background

def pidfile() -> Path:
    return paths.data_dir() / "headend.pid"


def running() -> bool:
    try:
        pid = int(pidfile().read_text())
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/cmdline").read_bytes().find(b"serve") >= 0
    except (OSError, ValueError):
        return False


def _autostart_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / f"{'xcable' if paths.XCABLE else 'neon-cable-42'}-headend.desktop"


def enable() -> None:
    """Start sharing now and every time this user logs in."""
    import subprocess

    from . import desktop

    f = _autostart_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"[Desktop Entry]\nType=Application\nName={paths.APP_NAME} head-end\n"
                 f'Exec="{desktop.launcher()}" serve\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n')
    if not running():
        subprocess.Popen(["/bin/bash", str(desktop.launcher()), "serve"], cwd=str(paths.APP_DIR.parent),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def disable() -> None:
    _autostart_file().unlink(missing_ok=True)
    try:
        os.kill(int(pidfile().read_text()), 15)
    except (OSError, ValueError):
        pass
    pidfile().unlink(missing_ok=True)


def enabled() -> bool:
    return _autostart_file().exists()


def addresses() -> list[str]:
    """This computer's home-network addresses (for showing in Settings)."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))          # no packet is sent; picks the outgoing interface
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return out


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]
