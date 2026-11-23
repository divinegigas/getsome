"""libmpv rendered inside a QOpenGLWidget, with a hook for drawing Qt overlays on top.

Using mpv's render API (rather than embedding a native window id) works the same on
X11 and Wayland, and lets us paint the neon numbers / static in the same frame.
"""

from __future__ import annotations

import ctypes
import locale
import logging

import mpv
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QOpenGLContext, QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .. import paths

_log = logging.getLogger("getsome.mpv")


def _get_proc_address(_ctx, name: bytes):
    glctx = QOpenGLContext.currentContext()
    if glctx is None:
        return 0
    addr = glctx.getProcAddress(name.decode("utf-8"))
    try:
        return int(addr)
    except TypeError:
        return ctypes.cast(addr, ctypes.c_void_p).value or 0


class MpvWidget(QOpenGLWidget):
    """Owns the single mpv instance. Only the current channel ever decodes."""

    frame_ready = Signal()          # mpv has a new frame (emitted from mpv's thread)
    playback_restart = Signal()     # a load or seek finished and frames are flowing
    file_ended = Signal(str)        # reason: eof / error / stop / ...

    def __init__(self, parent=None, overlay_painter=None, hwdec: str = "no", volume: float = 100):
        super().__init__(parent)
        # mpv requires the C numeric locale
        locale.setlocale(locale.LC_NUMERIC, "C")
        self.player = mpv.MPV(
            vo="libmpv",
            config=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            ytdl=False,
            idle=True,
            keep_open="yes",
            hr_seek="yes",
            hwdec=hwdec,
            volume=volume,
            image_display_duration="inf",
            audio_client_name=f"{paths.APP_NAME}",
            network_timeout=10,
            loglevel="warn",
        )
        self._ctx: mpv.MpvRenderContext | None = None
        self._hwdec = hwdec
        # optional second, muted player for the guide's preview window (see enable_preview)
        self.preview: mpv.MPV | None = None
        self._pctx: mpv.MpvRenderContext | None = None
        self._pfbo = None
        self._pdirty = False
        self.preview_image = None          # QImage of the latest preview frame
        self.overlay_painter = overlay_painter  # callable(QPainter, w, h)
        self.frame_ready.connect(self.update, Qt.QueuedConnection)
        self.setUpdateBehavior(QOpenGLWidget.NoPartialUpdate)

        @self.player.event_callback("playback-restart")
        def _restart(_event):
            try:
                self.player.pause = False
            except Exception:
                pass
            self.playback_restart.emit()

        @self.player.event_callback("end-file")
        def _ended(event):
            try:
                reason = event.data.reason
                name = {0: "eof", 2: "stop", 3: "quit", 4: "error", 5: "redirect"}.get(int(reason), str(reason))
            except Exception:
                name = "unknown"
            self.file_ended.emit(name)

    # -- GL -----------------------------------------------------------------
    def initializeGL(self):
        self._proc_fn = mpv.MpvGlGetProcAddressFn(_get_proc_address)
        self._ctx = mpv.MpvRenderContext(
            self.player, "opengl", opengl_init_params={"get_proc_address": self._proc_fn}
        )
        self._ctx.update_cb = self.frame_ready.emit

    def paintGL(self):
        ratio = self.devicePixelRatioF()
        w, h = int(self.width() * ratio), int(self.height() * ratio)
        self._render_preview()
        painter = QPainter(self)
        painter.beginNativePainting()
        if self._ctx is not None:
            self._ctx.render(flip_y=True, opengl_fbo={"w": w, "h": h, "fbo": self.defaultFramebufferObject()})
        painter.endNativePainting()
        if self.overlay_painter:
            painter.setRenderHint(QPainter.Antialiasing)
            self.overlay_painter(painter, self.width(), self.height())
        painter.end()

    # -- guide preview ----------------------------------------------------------
    PREVIEW_W, PREVIEW_H = 640, 360

    def enable_preview(self) -> mpv.MPV:
        """A second, silent player for the guide's preview box. It only decodes while
        something is loaded in it; stop_preview() unloads it."""
        if self.preview is None:
            self.preview = mpv.MPV(
                vo="libmpv", config=False, input_default_bindings=False, input_vo_keyboard=False,
                osc=False, ytdl=False, idle=True, keep_open="yes", hr_seek="yes", hwdec=self._hwdec,
                aid="no", mute=True, image_display_duration="inf", loglevel="warn",
                vd_lavc_fast=True, vd_lavc_skiploopfilter="all",
            )
        return self.preview

    def load_preview(self, path: str, start: float | None = None, end: float | None = None) -> None:
        from .. import cablebox

        path = cablebox.media(path)
        pl = self.enable_preview()
        opts = [f"start={start:.3f}" if start and start > 0 else "start=none",
                f"end={end:.3f}" if end and end > 0 else "end=none", "loop-file=no"]
        args = ["loadfile", path, "replace"]
        if self._loadfile_takes_index():
            args.append("-1")
        args.append(",".join(opts))
        pl.command(*args)
        pl.pause = False

    def stop_preview(self) -> None:
        if self.preview is not None:
            try:
                self.preview.command("stop")
                self.preview.mute = True
                self.preview["aid"] = "no"
            except Exception:
                pass
        self.preview_image = None

    def _preview_update(self) -> None:
        self._pdirty = True
        self.frame_ready.emit()

    def _render_preview(self) -> None:
        if self.preview is None:
            return
        from PySide6.QtOpenGL import QOpenGLFramebufferObject

        if self._pctx is None:
            self._pctx = mpv.MpvRenderContext(
                self.preview, "opengl", opengl_init_params={"get_proc_address": self._proc_fn})
            self._pctx.update_cb = self._preview_update
            self._pfbo = QOpenGLFramebufferObject(self.PREVIEW_W, self.PREVIEW_H)
        if not self._pdirty:
            return
        self._pdirty = False
        if not self._pctx.update():
            return
        self._pctx.render(flip_y=True, opengl_fbo={"w": self.PREVIEW_W, "h": self.PREVIEW_H,
                                                    "fbo": self._pfbo.handle()})
        self.preview_image = self._pfbo.toImage()
        QOpenGLFramebufferObject.bindDefault()

    # -- control --------------------------------------------------------------
    def _loadfile_takes_index(self) -> bool:
        """mpv 0.38 inserted an <index> argument before loadfile's per-file options."""
        if not hasattr(self, "_takes_index"):
            import re

            m = re.search(r"(\d+)\.(\d+)", str(self.player.mpv_version))
            self._takes_index = bool(m) and (int(m.group(1)), int(m.group(2))) >= (0, 38)
        return self._takes_index

    def load(self, path: str, start: float | None = None, end: float | None = None, loop: bool = False):
        """Load a file with per-file options (never global ones, which would also
        retarget whatever is currently playing)."""
        from .. import cablebox

        path = cablebox.media(path)       # a cable box streams from the head-end
        opts = [
            f"start={start:.3f}" if start and start > 0 else "start=none",
            f"end={end:.3f}" if end and end > 0 else "end=none",
            f"loop-file={'inf' if loop else 'no'}",
        ]
        args = ["loadfile", path, "replace"]
        if self._loadfile_takes_index():
            args.append("-1")
        args.append(",".join(opts))
        self.player["loop-playlist"] = "no"
        self.player.command(*args)
        # keep-open pauses at the end of a file; that pause can land after this
        # load, so un-pause now and again once the new file is actually running
        self.player.pause = False

    def load_playlist(self, paths_: list[str], shuffle: bool = True) -> None:
        """Play several files on repeat (guide music)."""
        import random

        files = list(paths_)
        if shuffle:
            random.shuffle(files)
        args = ["loadfile", files[0], "replace"]
        if self._loadfile_takes_index():
            args.append("-1")
        args.append("start=none,end=none,loop-file=no")
        self.player.command(*args)
        for f in files[1:]:
            self.player.command("loadfile", f, "append")
        self.player["loop-playlist"] = "inf"
        self.player.pause = False

    def stop(self):
        self.player.command("stop")

    def set_muted(self, muted: bool):
        self.player.mute = muted

    def position(self) -> float | None:
        try:
            return self.player.time_pos
        except Exception:
            return None

    def seek_abs(self, seconds: float):
        try:
            self.player.seek(seconds, "absolute+exact")
        except Exception as e:  # e.g. still loading
            _log.debug("seek failed: %s", e)

    def shutdown(self):
        self.makeCurrent()
        if self._pctx is not None:
            self._pctx.free()
            self._pctx = None
        self._pfbo = None
        if self._ctx is not None:
            self._ctx.free()
            self._ctx = None
        self.doneCurrent()
        if self.preview is not None:
            self.preview.terminate()
        self.player.terminate()
