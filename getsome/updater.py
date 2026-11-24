"""Update GetSome in place from the git checkout it runs from (Settings → Updates).

Steps: `git fetch`, list the new changes, `git pull --ff-only`, refresh the
FieldStation42 submodule, then restart through getsome.sh (which installs any new
Python packages when requirements.txt changed). Your channels, settings and media
live outside the checkout and are never touched.

In the AppImage (paths.APPIMAGE) it works from GitHub Releases instead: the newest release that has this
edition's file (`asset_name()`), its "<file>.sha256" checked after downloading, the new file put in place
of the running AppImage, then a restart. Only contacted when the owner presses the button; nothing about
the computer or its library is sent.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

_log = logging.getLogger("getsome.updater")
REPO = paths.APP_DIR.parent
RELEASES_REPO = os.environ.get("GETSOME_RELEASES_REPO", "divinegigas/getsome")
_pending: dict = {}           # AppImage mode: the release check() found, for apply()


def asset_name() -> str:
    return ("XCable" if paths.XCABLE else "NeonCable42") + "-x86_64.AppImage"


# The AppImage's Python has its own OpenSSL, which looks for the trusted-certificate list where Ubuntu keeps it.
# Use the computer's own list (Fedora/Bazzite: /etc/pki/…), else the copy bundled with certifi.
_CA_FILES = ("/etc/pki/tls/certs/ca-bundle.crt", "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
             "/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem", "/etc/ssl/ca-bundle.pem")


def _ssl_context():
    import ssl

    if os.environ.get("SSL_CERT_FILE") or not paths.APPIMAGE:
        return ssl.create_default_context()
    for f in _CA_FILES:
        if os.path.isfile(f):
            return ssl.create_default_context(cafile=f)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _http(url: str, accept: str = "application/vnd.github+json", timeout: float = 30):
    import urllib.request

    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "NeonCable42-updater"})
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())
    except OSError as e:
        raise UpdateError(f"couldn't reach GitHub ({e}); check the internet connection") from e


def _releases() -> list[dict]:
    import json

    with _http(f"https://api.github.com/repos/{RELEASES_REPO}/releases?per_page=30") as r:
        rel = json.loads(r.read())
    return [x for x in rel if not x.get("draft") and not x.get("prerelease")
            and any(a.get("name") == asset_name() for a in x.get("assets", []))]


def _check_appimage() -> "Check":
    rels = _releases()
    if not rels:
        return Check()
    mine = paths.version()
    tags = [r["tag_name"] for r in rels]
    newer = rels[:tags.index(mine)] if mine in tags else rels[:1]
    if not newer or newer[0]["tag_name"] == mine:
        return Check()
    changes = []
    for r in newer:
        for line in (r.get("body") or "").splitlines():
            line = line.strip().lstrip("-*• ").strip()
            if line and not line.startswith("#"):
                changes.append(line)
    _pending.clear()
    _pending.update(newer[0])
    return Check(len(newer), changes or [newer[0].get("name") or newer[0]["tag_name"]])


def _apply_appimage() -> None:
    import hashlib

    rel = _pending or (_releases() or [None])[0]
    if not rel:
        raise UpdateError("no update found")
    assets = {a["name"]: a for a in rel["assets"]}
    app, sums = assets.get(asset_name()), assets.get(asset_name() + ".sha256")
    if app is None or sums is None:
        raise UpdateError("that release is missing this edition's file")
    with _http(sums["browser_download_url"], "application/octet-stream") as r:
        want = r.read().decode().split()[0].strip().lower()
    target = Path(paths.APPIMAGE)
    part = target.with_name(f".{target.name}.download")
    h = hashlib.sha256()
    try:
        with _http(app["browser_download_url"], "application/octet-stream", timeout=120) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"couldn't save the update next to {target.name}: {e}") from e
    if h.hexdigest() != want:
        part.unlink(missing_ok=True)
        raise UpdateError("the download didn't match its checksum (damaged in transit?); nothing was changed")
    part.chmod(0o755)
    os.replace(part, target)              # the running copy keeps working until the restart
    _log.info("updated %s to %s", target.name, rel["tag_name"])


class UpdateError(Exception):
    pass


def _git(*args: str, timeout: float = 120) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")   # never hang on a password prompt
    try:
        r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError as e:
        raise UpdateError("git is not installed (sudo dnf install git)") from e
    except subprocess.TimeoutExpired as e:
        raise UpdateError("git took too long; check the internet connection") from e
    if r.returncode != 0:
        out = (r.stderr or r.stdout).strip()
        if any(s in out for s in ("could not read Username", "Authentication failed", "terminal prompts disabled",
                                  "Repository not found", "403")):
            # a private repository: this computer has to be signed in to GitHub once
            raise UpdateError("GitHub needs you to sign in (the repository is private). In a terminal run:  "
                              "sudo dnf install gh   then   gh auth login   then   gh auth setup-git   "
                              "and press Check for updates again.")
        msg = out.splitlines()
        raise UpdateError(msg[-1] if msg else f"git {args[0]} failed")
    return r.stdout.strip()


def available() -> bool:
    if paths.APPIMAGE:
        return True
    return (REPO / ".git").exists() and (REPO / "getsome.sh").exists()


def version() -> str:
    """"ad4a29f · 18 Nov 2026" for the running checkout; the release name in the AppImage."""
    if paths.APPIMAGE:
        return paths.version() or "unknown"
    try:
        return _git("log", "-1", "--format=%h · %cd", "--date=format:%d %b %Y %H:%M", timeout=10)
    except UpdateError:
        return "unknown"


def _upstream() -> str:
    try:
        return _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=10)
    except UpdateError:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD", timeout=10)
        return f"origin/{branch}"


def _rewritten(up: str) -> bool:
    """True when GitHub's history no longer contains what this computer has (it was cleaned up /
    squashed), so a normal fast-forward pull is impossible and we move to GitHub's version instead."""
    r = subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", "HEAD", up],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 1          # 0 = normal update, 1 = rewritten, other = error (treat as normal)


def _dirty() -> list[str]:
    return [line for line in _git("status", "--porcelain", "--untracked-files=no").splitlines()
            if not line.endswith("vendor/FieldStation42")]


@dataclass
class Check:
    behind: int = 0
    changes: list[str] = field(default_factory=list)     # newest first
    blocked: str = ""                                    # why it can't update automatically
    rewritten: bool = False                              # GitHub's history was cleaned up


def check() -> Check:
    if paths.APPIMAGE:
        return _check_appimage()
    if not available():
        raise UpdateError(f"this copy of {paths.APP_NAME} isn't a git checkout, so it can't update itself")
    _git("fetch", "--quiet", "origin")
    up = _upstream()
    behind = int(_git("rev-list", "--count", f"HEAD..{up}") or 0)
    changes = _git("log", "--format=%s", f"HEAD..{up}").splitlines() if behind else []
    res = Check(behind, changes, rewritten=behind > 0 and _rewritten(up))
    if behind:
        dirty = _dirty()
        if dirty:
            res.blocked = (f"some {paths.APP_NAME} program files were edited on this computer "
                           f"({len(dirty)} file(s)), so the update would overwrite them")
    return res


def apply() -> None:
    """Install the update. Normally a fast-forward pull; if GitHub's history was rewritten
    (old versions removed), switch to GitHub's version and drop the old history from this
    computer too. Local edits to program files always stop it (nothing is ever thrown away)."""
    if paths.APPIMAGE:
        return _apply_appimage()
    up = _upstream()
    if _rewritten(up):
        if _dirty():
            raise UpdateError(f"some {paths.APP_NAME} program files were edited on this computer, "
                              "so the update would overwrite them")
        _log.info("history on GitHub was rewritten: moving to %s", up)
        _git("reset", "--hard", "--quiet", up, timeout=120)
        try:      # forget the old versions locally as well (your settings and channels aren't in git)
            _git("reflog", "expire", "--expire=now", "--all", timeout=120)
            _git("gc", "--prune=now", "--quiet", timeout=600)
        except UpdateError as e:
            _log.warning("could not clean up old history: %s", e)
    else:
        _git("pull", "--ff-only", "--quiet", timeout=300)
    _git("submodule", "update", "--init", "--recursive", timeout=600)


def restart(args: list[str]) -> None:
    """Replace this process with a fresh copy (the new AppImage, or through getsome.sh)."""
    if paths.APPIMAGE:
        os.execv(paths.APPIMAGE, [paths.APPIMAGE, *args])
    os.chdir(REPO)
    script = str(REPO / paths.LAUNCHER)
    os.execv("/bin/bash", ["/bin/bash", script, *args])
