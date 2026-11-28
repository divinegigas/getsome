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
    roots = [paths.data_dir()] + [Path(f) for f in s.get("library_folders", []) + s.get("movie_folders", [])
                                  + s.get("commercial_folders", [])]
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
        "ondemand_channel": int(s.get("ondemand_channel") or 0),
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


def _guide_rows(names, when) -> list[dict]:
    """The guide on a phone: what's on now and next on each channel, titles and times only (no episode
    details, which would read every episode's .nfo from the library drive). One request for all."""
    from . import fs42

    out = []
    for name in names:
        try:
            cur, nxt = fs42.now_and_next(name, _dt(when))
        except Exception:
            cur = nxt = None
        out.append({"name": name, "now": cur.title if cur else "", "next": nxt.title if nxt else "",
                    "next_start": nxt.start.isoformat() if nxt else ""})
    return out


def _calls() -> dict:
    from . import ads, artwork, fs42, ondemand

    return {
        "info": lambda: _info(),
        "segment_at": _segment_at,
        "programme_at": lambda name, when: _prog(fs42.programme_at(name, _dt(when))),
        "programmes": lambda name, start, end, limit=40: [_prog(p) for p in
                                                          fs42.programmes(name, _dt(start), _dt(end), limit)],
        "now_and_next": lambda name, when: [_prog(p) for p in fs42.now_and_next(name, _dt(when))],
        "guide_rows": _guide_rows,
        "card_for": ads.card_for,
        "promo_for": ads.promo_for,
        "on_this_day": lambda path, day=None: list(ads.on_this_day(path, dt.date.fromisoformat(day) if day else None)),
        "episode_details": lambda path: list(ads.episode_details(path)),
        "artwork": artwork.for_programme,
        "movie_card": ads.movie_card,
        # On Demand (no FS42 involved, so these don't wait for the schedule lock)
        "ondemand_catalog": lambda: ondemand.catalog(),
        "ondemand_show": lambda folder: ondemand.show_detail(folder),
    }


NO_LOCK = {"ondemand_catalog", "ondemand_show"}


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
                if body.get("fn") in NO_LOCK:
                    result = fn(*body.get("args", []))
                else:
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


def _signature() -> str:
    """Changes when the app is updated or the channels change (builds rewrite the FS42 confs, the
    channel list or settings are edited). FS42 keeps its station list in memory and this process keeps
    its code, so the service restarts itself when this changes."""
    import subprocess

    parts = []
    if paths.APPIMAGE:                    # an update replaces the AppImage file
        try:
            st = os.stat(paths.APPIMAGE)
            parts.append(f"{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            pass
    else:
        try:
            parts.append(subprocess.run(["git", "-C", str(paths.APP_DIR.parent), "rev-parse", "HEAD"],
                                        capture_output=True, text=True, timeout=10).stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    confs = paths.fs42_work_dir() / "confs"
    try:
        parts += [f"{f.name}:{f.stat().st_mtime_ns}" for f in sorted(confs.glob("*.json"))]
    except OSError:
        pass
    for f in (paths.data_dir() / "channels.json", paths.config_dir() / "settings.json"):
        try:
            parts.append(f"{f.name}:{f.stat().st_mtime_ns}")
        except OSError:
            pass
    return "|".join(parts)


def serve(port: int = PORT) -> None:
    """`getsome serve`: run until stopped. Keeps the computer awake while a box is streaming, keeps
    schedules topped up (the setup window or TV mode may not be open on the head-end), and restarts
    itself when the app is updated or the channels change (so boxes always get the current line-up)."""
    import sys
    from logging.handlers import RotatingFileHandler

    from . import upkeep

    # a log the owner (or the next session) can read: the service has no terminal
    try:
        log_file().parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=1)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        if not sys.stderr.isatty():               # stderr already goes to this file (enable())
            for h in list(root.handlers):
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    root.removeHandler(h)
        root.addHandler(fh)
        logging.getLogger().setLevel(logging.INFO)
    except OSError:
        pass
    # restarts in the last 2 minutes (passed on through execv): more than 3 means something keeps
    # changing; then stay up instead of restarting again
    now = time.time()
    recent = [float(t) for t in os.environ.get("GETSOME_HEADEND_RESTARTS", "").split(",") if t]
    recent = [t for t in recent if now - t < 120]
    auto_restart = len(recent) < 3
    if not auto_restart:
        _log.warning("restarted %d times in 2 minutes: not restarting automatically for now", len(recent))
    server = None
    for attempt in range(20):                     # a copy being replaced may still hold the port briefly
        try:
            server = HeadEnd(port)
            break
        except OSError as e:
            if attempt == 19:
                _log.error("can't share on port %d: %s (is another copy already sharing?)", port, e)
                raise
            time.sleep(0.5)
    server.restart = False
    started_sig = _signature()
    pidfile().write_text(f"{os.getpid()}\n{_code_version()}")
    threading.Thread(target=_discovery, args=(server,), name="discovery", daemon=True).start()

    def warm():                          # On Demand's catalog: ready before a box asks for it
        try:
            from . import ondemand

            ondemand.catalog()
        except Exception as e:
            _log.warning("on demand catalog: %s", e)
    threading.Thread(target=warm, name="ondemand-warm", daemon=True).start()

    def keeper():
        nonlocal started_sig
        awake = None
        last_upkeep = 0.0
        while True:
            time.sleep(15)
            if auto_restart and _signature() != started_sig:
                _log.info("channels or app changed: restarting the sharing service")
                server.restart = True
                server.shutdown()          # serve_forever returns in the main thread
                return
            if time.monotonic() - last_upkeep > 3600:
                last_upkeep = time.monotonic()
                try:
                    upkeep.run()
                except Exception as e:
                    _log.warning("upkeep failed: %s", e)
                started_sig = _signature()     # our own top-ups don't count as "the channels changed"
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

    threading.Thread(target=keeper, name="headend-keeper", daemon=True).start()
    _log.info("sharing channels on port %d", port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if not server.restart:
            pidfile().unlink(missing_ok=True)
    if server.restart:                     # same command, fresh process (new code, new line-up)
        os.chdir(paths.APP_DIR.parent)
        os.environ["GETSOME_HEADEND_RESTARTS"] = ",".join(str(t) for t in recent + [now])
        if paths.APPIMAGE:                 # start the (possibly updated) AppImage file, not the old mount
            os.execv(paths.APPIMAGE, [paths.APPIMAGE, "serve", "--port", str(port)])
        os.execv(sys.executable, [sys.executable, "-m", "getsome", "serve", "--port", str(port)])


def log_file() -> Path:
    return paths.data_dir() / "logs" / "headend.log"


def last_error() -> str:
    """The last warning or error the sharing service logged (for Settings)."""
    try:
        lines = log_file().read_text(errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines[-200:]):
        if " ERROR " in line or " WARNING " in line or "Traceback" in line or "Error:" in line:
            return line.split(": ", 1)[-1][:200]
    return ""


# ------------------------------------------------------------------ running it in the background

def pidfile() -> Path:
    return paths.data_dir() / "headend.pid"


def _code_version() -> str:
    return paths.version()

def ensure_current() -> None:
    """Setup window start: a sharing service still running older code (from before an update) is
    restarted, so cable boxes get the current line-up and fixes."""
    if not enabled() or not running():
        return
    try:
        lines = pidfile().read_text().split()
    except OSError:
        return
    if len(lines) < 2 or lines[1] != _code_version():
        _log.info("restarting the sharing service (it was running older code)")
        disable_process_only()
        enable()


def disable_process_only() -> None:
    try:
        os.kill(int(pidfile().read_text().split()[0]), 15)
    except (OSError, ValueError, IndexError):
        pass
    for _ in range(30):                  # wait for the port to be free
        if not running():
            break
        time.sleep(0.1)
    pidfile().unlink(missing_ok=True)


def running() -> bool:
    try:
        pid = int(pidfile().read_text().split()[0])
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/cmdline").read_bytes().find(b"serve") >= 0
    except (OSError, ValueError, IndexError):
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
        log_file().parent.mkdir(parents=True, exist_ok=True)
        out = open(log_file(), "a")           # start-up failures (before logging starts) land here too
        subprocess.Popen(paths.launch_cmd() + ["serve"], cwd=str(Path.home()),
                         stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        out.close()


def disable() -> None:
    _autostart_file().unlink(missing_ok=True)
    disable_process_only()


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
