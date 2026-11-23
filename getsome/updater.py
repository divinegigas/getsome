"""Update GetSome in place from the git checkout it runs from (Settings → Updates).

Steps: `git fetch`, list the new changes, `git pull --ff-only`, refresh the
FieldStation42 submodule, then restart through getsome.sh (which installs any new
Python packages when requirements.txt changed). Your channels, settings and media
live outside the checkout and are never touched.

Only works for a git checkout (not a future AppImage); `available()` says so.
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
    return (REPO / ".git").exists() and (REPO / "getsome.sh").exists()


def version() -> str:
    """"ad4a29f · 18 Nov 2026" for the running checkout."""
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
    """Replace this process with a fresh GetSome started through getsome.sh."""
    os.chdir(REPO)
    script = str(REPO / paths.LAUNCHER)
    os.execv("/bin/bash", ["/bin/bash", script, *args])
