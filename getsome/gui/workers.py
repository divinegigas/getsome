"""Background jobs (library scans, builds) so the window never freezes."""

from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QThread, Signal

from .. import builder, library, model

_log = logging.getLogger("getsome.gui")


class ScanWorker(QThread):
    progress = Signal(int, int, str)
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, movies: bool = False):
        super().__init__()
        self.movies = movies

    def run(self):
        try:
            fn = library.scan_movie_library if self.movies else library.scan
            shows = fn(lambda i, n, name: self.progress.emit(i, n, name))
            self.finished_ok.emit(shows)
        except Exception as e:
            _log.exception("library scan failed")
            self.failed.emit(str(e))


class BuildWorker(QThread):
    line = Signal(str)
    step = Signal(str)
    channel_started = Signal(int, int, str)     # index, total, name
    done = Signal(list)                         # [(name, error)]

    def __init__(self, channels: list[model.Channel], all_channels: list[model.Channel]):
        super().__init__()
        self.channels, self.all_channels = channels, all_channels

    def run(self):
        failures = []
        try:
            for i, ch in enumerate(self.channels):
                self.channel_started.emit(i, len(self.channels), f"{ch.number} {ch.name}")
                fails = builder.build([ch], self.all_channels, log=self.line.emit,
                                      step=lambda s, ch=ch: self.step.emit(s))
                failures += [(c.name, err) for c, err in fails]
        except Exception as e:
            self.line.emit(traceback.format_exc())
            failures.append(("build", str(e)))
        self.done.emit(failures)



class UpkeepSignals(QObject):
    """Upkeep runs on a plain daemon thread (closing the window mid-update is fine:
    a half-finished rebuild is redone next time); this hands the result to the GUI."""
    done = Signal(list)                         # names of channels it updated

    def start(self) -> None:
        from .. import upkeep

        upkeep.run_in_background(self.done.emit)


class UpdateWorker(QThread):
    """updater.check() or updater.apply() off the GUI thread."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, what: str):
        super().__init__()
        self.what = what

    def run(self):
        from .. import updater

        try:
            self.done.emit(updater.check() if self.what == "check" else updater.apply())
        except Exception as e:
            _log.exception("update %s failed", self.what)
            self.failed.emit(str(e))


class Background(QObject):
    """Run fn() on a daemon thread and hand its result (or None on error) to the GUI."""
    done = Signal(object)

    def start(self, fn) -> None:
        import threading

        def work():
            try:
                res = fn()
            except Exception:
                _log.exception("background job failed")
                res = None
            self.done.emit(res)

        threading.Thread(target=work, daemon=True).start()
