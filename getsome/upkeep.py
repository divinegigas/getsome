"""Keep every built channel's schedule running ahead of the clock.

FS42 builds schedules a week at a time. Before this module, only a running TV mode
extended them, so a week without TV left channels saying "Schedule ended".
Now upkeep runs when the setup window opens, when TV mode starts (and hourly while
it runs), and on `getsome upkeep`:

  * schedule ends within MIN_AHEAD  -> add a week (`station_42.py -w`), quick
  * schedule already ended / missing -> rebuild the channel. Extending would only add
    a week after the old end, which may still be in the past. In-order shows resume
    at the episode after the one the old schedule stopped on (builder._resume_point).

Only one upkeep runs at a time (pid lock), so the setup window and TV mode never
build the same channel at once.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import time
from typing import Callable

from . import builder, fs42, model, paths

_log = logging.getLogger("getsome.upkeep")
MIN_AHEAD = dt.timedelta(days=3)
EXPIRED_SLACK = dt.timedelta(minutes=30)   # ending this soon counts as ended: rebuild


def _lock():
    return paths.fs42_work_dir() / "upkeep.lock"


@contextlib.contextmanager
def schedule_lock(wait: float = 0, on_wait: Callable[[], None] | None = None):
    """Hold the upkeep lock; yields True if it was taken. With wait > 0, keep trying
    that many seconds (calling on_wait once) before giving up."""
    from . import assets_gen

    paths.ensure_dirs()
    lock = _lock()
    deadline = time.monotonic() + wait
    got = assets_gen._take_lock(lock)
    if not got and on_wait:
        on_wait()
    while not got and time.monotonic() < deadline:
        time.sleep(1)
        got = assets_gen._take_lock(lock)
    try:
        yield got
    finally:
        if got:
            lock.unlink(missing_ok=True)


def status(ch: model.Channel, now: dt.datetime | None = None) -> str:
    """"ok", "short" (extend) or "expired" (rebuild) for a built channel; "" if not built."""
    if not ch.built_hash or not ch.has_content:
        return ""
    now = now or dt.datetime.now()
    end = fs42.schedule_end(ch.name)
    if end is None or end <= now + EXPIRED_SLACK:
        return "expired"
    if end < now + MIN_AHEAD:
        return "short"
    return "ok"


def needs_work(channels: list[model.Channel] | None = None) -> list[tuple[model.Channel, str]]:
    chans = model.load() if channels is None else channels
    out = []
    for ch in chans:
        try:
            st = status(ch)
        except Exception as e:      # a broken channel must not stop the others
            _log.warning("could not check %s: %s", ch.name, e)
            continue
        if st in ("short", "expired"):
            out.append((ch, st))
    return out


def run(log: Callable[[str], None] | None = None) -> list[str]:
    """Extend or rebuild whatever needs it. Returns the names of channels it touched.
    Returns [] straight away if another GetSome process is already doing upkeep."""
    from . import assets_gen

    log = log or _log.info
    touched: list[str] = []
    with schedule_lock() as got:
        if not got:
            _log.info("schedule upkeep or a build is already running elsewhere")
            return []
        chans = model.load()
        todo = needs_work(chans)
        if not todo:
            return []
        assets_gen.ensure_runtime_assets()
        for ch, st in todo:
            try:
                if st == "short":
                    log(f"{ch.number} {ch.name}: schedule runs out soon, adding a week")
                    for _ in range(2):        # one week normally does it
                        fs42.extend_schedules([ch.name])
                        if status(ch) == "ok":
                            break
                else:
                    log(f"{ch.number} {ch.name}: schedule has ended, rebuilding (in-order shows pick up where they stopped)")
                    builder.build_channel(ch, log=lambda line: _log.debug(line), step=lambda s: _log.info("  %s", s))
                    model.save(chans)
                touched.append(ch.name)
            except Exception as e:
                _log.exception("upkeep failed for %s", ch.name)
                log(f"{ch.number} {ch.name}: could not update the schedule: {e}")
    return touched


def run_in_background(on_done: Callable[[list[str]], None] | None = None) -> None:
    import threading

    def work():
        try:
            names = run()
        except Exception:
            _log.exception("schedule upkeep failed")
            names = []
        if on_done:
            on_done(names)

    threading.Thread(target=work, name="schedule-upkeep", daemon=True).start()
