"""Setup window: channel list on the left, channel editor on the right."""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import builder, channel_writer, model, paths
from ..tv import overlays
from . import theme
from .channel_editor import ChannelEditor
from .dialogs import BuildDialog, NewChannelDialog, SettingsDialog, ShowPicker, button, ensure_library, h2, hint


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{paths.APP_NAME} - Channel Setup")
        self.resize(1200, 780)
        self.channels = model.load()
        self.tv_proc: subprocess.Popen | None = None

        bg = theme.SynthBackground()
        self.setCentralWidget(bg)
        outer = QVBoxLayout(bg)
        outer.setContentsMargins(22, 14, 22, 18)
        outer.setSpacing(12)

        # header
        head = QHBoxLayout()
        logo = theme.NeonLabel(paths.APP_NAME.upper(), "numbers", 54, theme.PINK)
        head.addWidget(logo)
        tag = QVBoxLayout()
        tag.addStretch(1)
        tag.addWidget(theme.ChromeLabel("CABLE  TV  SYSTEM", 16))
        sub = QLabel("CHANNEL SETUP")
        sub.setStyleSheet(f"color: {theme.CYAN}; letter-spacing: 6px; font-size: 11px;")
        tag.addWidget(sub)
        tag.addStretch(1)
        head.addLayout(tag)
        head.addStretch(1)
        settings_btn = button("Settings")
        settings_btn.clicked.connect(self._settings)
        head.addWidget(settings_btn)
        self.watch_btn = button("▶  Watch TV", "primary")
        self.watch_btn.setMinimumHeight(44)
        self.watch_btn.setStyleSheet("font-size: 16px; padding: 8px 22px;")
        theme.glow(self.watch_btn)
        self.watch_btn.clicked.connect(self._watch)
        head.addWidget(self.watch_btn)
        outer.addLayout(head)

        # drag the divider to give the channel list or the editor more room
        body = QSplitter(Qt.Horizontal)
        body.setHandleWidth(10)
        body.setChildrenCollapsible(False)

        # channel list
        left = QFrame()
        left.setObjectName("panel")
        left.setMinimumWidth(230)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 14, 14, 14)
        ll.addWidget(h2("Channels"))
        self.guide_note = hint("")
        ll.addWidget(self.guide_note)
        self.list = QListWidget()
        self.list.setIconSize(QSize(64, 40))
        self.list.currentRowChanged.connect(self._select)
        ll.addWidget(self.list, 1)
        new = button("+ New channel", "primary")
        new.clicked.connect(self._new_channel)
        ll.addWidget(new)
        auto = button("Auto-create channels…")
        auto.setToolTip("Point it at your library, say how many channels, and it suggests channels of similar\n"
                        "shows (and movie channels for films). You can edit everything before it creates them.")
        auto.clicked.connect(self.auto_create)
        ll.addWidget(auto)
        self.build_all_btn = button("Build all changed")
        self.build_all_btn.clicked.connect(self._build_changed)
        ll.addWidget(self.build_all_btn)
        rebuild = button("Rebuild all channels…")
        rebuild.setToolTip("Rebuild every channel, e.g. after an update or after changing commercial settings.\n"
                           "Shows that play in order keep their place.")
        rebuild.clicked.connect(self.rebuild_all)
        ll.addWidget(rebuild)
        self.new_eps_btn = button("Check for new episodes")
        self.new_eps_btn.setToolTip("Bought more of a series? This finds new episodes in shows that are\n"
                                    "already on channels and rebuilds those channels to add them.")
        self.new_eps_btn.clicked.connect(self._new_eps_clicked)
        self._found_eps: list = []
        ll.addWidget(self.new_eps_btn)
        tidy = button("Clean up channels…")
        tidy.setToolTip("Fix broken, leftover, empty or duplicate-number channels")
        tidy.clicked.connect(self.cleanup)
        ll.addWidget(tidy)
        body.addWidget(left)

        # editor / empty state
        self.stack = QStackedWidget()
        empty = QFrame()
        empty.setObjectName("panel")
        el = QVBoxLayout(empty)
        el.addStretch(1)
        el.addWidget(theme.NeonLabel("NO SIGNAL", "numbers", 70, theme.CYAN), 0, Qt.AlignHCenter)
        msg = hint("Create your first channel: pick a style, give it a number, add some shows, and build.")
        msg.setAlignment(Qt.AlignCenter)
        el.addWidget(msg)
        first = button("+ New channel", "primary")
        first.clicked.connect(self._new_channel)
        el.addWidget(first, 0, Qt.AlignHCenter)
        el.addStretch(1)
        self.stack.addWidget(empty)
        self.editor = ChannelEditor()
        self.editor.changed.connect(self._refresh_list)
        self.editor.build_requested.connect(lambda: self._build([self._current()]))
        self.editor.delete_requested.connect(self._delete)
        self.stack.addWidget(self.editor)
        body.addWidget(self.stack)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([330, 870])
        outer.addWidget(body, 1)

        self._refresh_list()
        if self.channels:
            self.list.setCurrentRow(0)
        self._start_upkeep()
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1200, self._offer_app_menu)

    def _offer_app_menu(self) -> None:
        """Once: offer to put Neon Cable 42 in the app menu (it's also in Settings > Shortcuts)."""
        from PySide6.QtWidgets import QMessageBox

        from .. import desktop, settings

        s = settings.load()
        if s.get("menu_offered") or desktop.installed():
            return
        s["menu_offered"] = True
        settings.save(s)
        if QMessageBox.question(self, "App menu", "Add Neon Cable 42 to your app menu (Sound & Video / Multimedia)?\n\n"
                                "You can change this any time in Settings > Shortcuts.") == QMessageBox.Yes:
            try:
                desktop.install()
            except Exception as e:
                QMessageBox.warning(self, "App menu", f"Couldn't add it: {e}")

    # ---------------------------------------------------------------- upkeep
    def _start_upkeep(self) -> None:
        """Schedules run out after about a week; top them up (or rebuild expired ones)."""
        from .workers import UpkeepSignals

        self.upkeep = UpkeepSignals(self)
        self.upkeep.done.connect(self._upkeep_done)
        self._upkeep_note = "\nChecking schedules…"
        self._refresh_list()
        self.upkeep.start()

    def _upkeep_done(self, names: list) -> None:
        # upkeep saved fresh build info for the channels it rebuilt; take it over
        if names:
            fresh = {c.name: c for c in model.load()}
            for ch in self.channels:
                f = fresh.get(ch.name)
                if ch.name in names and f is not None:
                    ch.built_hash, ch.built_name = f.built_hash, f.built_name
                    for mine, theirs in zip(ch.all_show_refs(), f.all_show_refs()):
                        mine.start_pending = theirs.start_pending
        self._upkeep_note = f"\nUpdated schedules: {', '.join(names)}" if names else ""
        self._refresh_list()
        self._check_new_episodes(ask=False)       # quietly, after upkeep has finished

    # ---------------------------------------------------------------- new episodes
    def _check_new_episodes(self, ask: bool) -> None:
        from .. import new_episodes
        from .workers import Background

        if getattr(self, "_eps_job", None) is not None:
            return
        self.new_eps_btn.setEnabled(False)
        self.new_eps_btn.setText("Checking for new episodes…")
        self._eps_job = Background(self)
        self._eps_job.done.connect(lambda found: self._new_episodes_done(found or [], ask))
        self._eps_job.start(lambda: new_episodes.find(force=ask))

    def _new_eps_clicked(self) -> None:
        if self._found_eps:
            self._offer_new_episodes(self._found_eps)       # found by the startup check
        else:
            self._check_new_episodes(ask=True)

    def _set_new_eps_button(self, found: list) -> None:
        self._found_eps = found
        self.new_eps_btn.setEnabled(True)
        self.new_eps_btn.setText(f"New episodes found ({len(found)} show{'s' if len(found) != 1 else ''})…"
                                 if found else "Check for new episodes")
        self.new_eps_btn.setObjectName("primary" if found else "")
        self.new_eps_btn.style().unpolish(self.new_eps_btn)
        self.new_eps_btn.style().polish(self.new_eps_btn)

    def _new_episodes_done(self, found: list, ask: bool) -> None:
        self._eps_job = None
        self._set_new_eps_button(found)
        if ask:
            self._offer_new_episodes(found)

    def _offer_new_episodes(self, found: list) -> None:
        from PySide6.QtWidgets import QMessageBox

        from .. import new_episodes

        if not found:
            QMessageBox.information(self, "New episodes", "No new episodes in the shows on your channels.\n\n"
                                    "(Brand-new shows: use Rescan in “Add shows”.)")
            return
        names = {c.name: c for f in found for c in f.channels}
        lines = [f"•  {f.title}: {f.new} new episode{'s' if f.new != 1 else ''}  "
                 f"(on {', '.join(str(c.number) + ' ' + c.name for c in f.channels)})" for f in found]
        if QMessageBox.question(self, "New episodes",
                                "\n".join(lines) + f"\n\nAdd them now? This rebuilds {len(names)} channel"
                                f"{'s' if len(names) != 1 else ''}. In-order shows keep their place; the new "
                                "episodes slot in by season and episode number.") != QMessageBox.Yes:
            return
        new_episodes.refresh_library(found)
        chans = [c for c in self.channels if c.name in names]
        self._set_new_eps_button([])
        self._build(chans)

    @staticmethod
    def _guide_text() -> str:
        g = model.guide_number()
        return f"Channel {g} is the built-in TV guide." if g else "TV guide is off (see Settings)."

    # ---------------------------------------------------------------- list
    def _current(self) -> model.Channel | None:
        row = self.list.currentRow()
        return self.channels[row] if 0 <= row < len(self.channels) else None

    def _state(self, ch: model.Channel) -> tuple[str, str]:
        if not ch.has_content:
            return ("no movies" if ch.kind == model.MOVIES else "no shows"), theme.DIM
        if not ch.built_hash:
            return "not built", theme.SUN
        if channel_writer.preview_hash(ch) != ch.built_hash or any(s.start_pending for s in ch.all_show_refs()):
            return "needs build", theme.SUN
        return "on the air", "#7cff4f"

    def _refresh_list(self) -> None:
        cur = self._current()
        self.channels.sort(key=lambda c: c.number)
        self.list.blockSignals(True)
        self.list.clear()
        from PySide6.QtGui import QPixmap

        for ch in self.channels:
            state, _color = self._state(ch)
            it = QListWidgetItem(f"{ch.name}\n{ch.content_text} · {state}")
            img = overlays.neon_image(str(ch.number), "numbers", 30, overlays.PINK)
            it.setIcon(QIcon(QPixmap.fromImage(img)))
            it.setSizeHint(QSize(280, 54))
            self.list.addItem(it)
        if cur in self.channels:
            self.list.setCurrentRow(self.channels.index(cur))
        self.list.blockSignals(False)
        self.guide_note.setText(self._guide_text() + getattr(self, "_upkeep_note", ""))
        changed = [c for c in self.channels if self._state(c)[0] in ("not built", "needs build")]
        self.build_all_btn.setText(f"Build all changed ({len(changed)})" if changed else "Everything is built")
        self.build_all_btn.setEnabled(bool(changed))
        self.stack.setCurrentIndex(1 if self._current() else 0)

    def _select(self, row: int) -> None:
        ch = self._current()
        if ch:
            self.editor.load(ch, self.channels)
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(0)

    # ---------------------------------------------------------------- actions
    def _new_channel(self) -> None:
        dlg = NewChannelDialog(self.channels, self)
        if dlg.exec() != QDialog.Accepted:
            return
        ch = dlg.result_channel()
        self.channels.append(ch)
        model.save(self.channels)
        self._refresh_list()
        self.list.setCurrentRow(self.channels.index(ch))
        self.editor.load(ch, self.channels)
        self.stack.setCurrentIndex(1)
        self.editor._add_shows()          # show or movie picker, depending on the style
        if ch.has_content and QMessageBox.question(self, "Build now?", f"Build {ch.name} now so it goes on the air?") \
                == QMessageBox.Yes:
            self._build([ch])

    def _delete(self) -> None:
        ch = self._current()
        if not ch:
            return
        if QMessageBox.question(self, "Delete channel",
                                f"Delete channel {ch.number} “{ch.name}”?\n\nYour video files are not touched.") \
                != QMessageBox.Yes:
            return
        builder.remove_channel(ch, self.channels)
        self._refresh_list()
        self.list.setCurrentRow(min(self.list.count() - 1, 0) if self.channels else -1)
        self._select(self.list.currentRow())

    def _build(self, chans: list[model.Channel | None]) -> None:
        chans = [c for c in chans if c is not None and c.has_content]
        if not chans:
            return
        dlg = BuildDialog(chans, self.channels, self)
        dlg.exec()
        self.channels = model.load()
        cur_names = [c.name for c in chans]
        self._refresh_list()
        for i, c in enumerate(self.channels):
            if c.name == cur_names[0]:
                self.list.setCurrentRow(i)
                break
        self._select(self.list.currentRow())

    def auto_create(self) -> None:
        from .autopop_dialog import AutoPopulateDialog

        dlg = AutoPopulateDialog(self.channels, self)
        if dlg.exec() != QDialog.Accepted or not dlg.created:
            return
        self.channels += dlg.created
        model.save(self.channels)
        self._refresh_list()
        self.list.setCurrentRow(self.channels.index(dlg.created[0]))
        self._select(self.list.currentRow())
        if dlg.build_now:
            self._build(dlg.created)
        else:
            QMessageBox.information(self, "Channels created",
                                    f"{len(dlg.created)} channels were added. They go on the air once they're "
                                    "built: press “Build all changed” when you're ready.")

    def rebuild_all(self) -> None:
        chans = [c for c in self.channels if c.has_content]
        if not chans:
            QMessageBox.information(self, "Rebuild all channels", "There are no channels with shows or films yet.")
            return
        mins = max(1, len(chans))
        if QMessageBox.question(
                self, "Rebuild all channels",
                f"Rebuild all {len(chans)} channels now?\n\n"
                f"This picks up updates and changed settings (like commercials). It can take about "
                f"{mins} minute{'s' if mins != 1 else ''} (less if they were built before). "
                "Shows that play in order keep their place, and your media is never changed.") != QMessageBox.Yes:
            return
        self._build(chans)

    def _build_changed(self) -> None:
        self._build([c for c in self.channels if self._state(c)[0] in ("not built", "needs build")])

    def cleanup(self) -> None:
        from .dialogs import CleanupDialog

        cur = self._current()
        dlg = CleanupDialog(self.channels, self)
        dlg.exec()
        if dlg.changed:
            self._refresh_list()
            if cur in self.channels:
                self.list.setCurrentRow(self.channels.index(cur))
            elif self.channels:
                self.list.setCurrentRow(0)
            else:
                self.stack.setCurrentIndex(0)
            if self._current() is not None:
                self.editor.load(self._current(), self.channels)

    def _settings(self) -> None:
        SettingsDialog(self).exec()
        self._refresh_list()

    def _watch(self) -> None:
        if self.tv_proc and self.tv_proc.poll() is None:
            return
        pending = [c for c in self.channels if self._state(c)[0] in ("not built", "needs build")]
        if pending:
            names = ", ".join(c.name for c in pending)
            r = QMessageBox.question(self, "Unbuilt changes",
                                     f"These channels have changes that aren't on the air yet:\n{names}\n\nBuild them first?",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                self._build(pending)
        if not any(c.built_hash for c in self.channels):
            QMessageBox.information(self, "Nothing on the air", "Build a channel first.")
            return
        env = dict(os.environ, GETSOME_FROM_SETUP="1")
        self.tv_proc = subprocess.Popen([sys.executable, "-m", "getsome", "tv"], cwd=str(paths.APP_DIR.parent), env=env)
        if not hasattr(self, "tv_timer"):
            from PySide6.QtCore import QTimer

            self.tv_timer = QTimer(self, interval=500, timeout=self._tv_check)
        self.tv_timer.start()

    def _tv_check(self) -> None:
        """TV mode's exit menu: "quit to setup" brings this window back, "quit to desktop" closes it."""
        if self.tv_proc is None or self.tv_proc.poll() is None:
            return
        self.tv_timer.stop()
        from ..tv.app import EXIT_TO_DESKTOP

        if self.tv_proc.returncode == EXIT_TO_DESKTOP:
            self.close()
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, ev):
        model.save(self.channels)
        super().closeEvent(ev)


def run_setup() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from .. import assets_gen

    assets_gen.start_music_background()
    app.setApplicationName(paths.APP_NAME)
    app.setDesktopFileName("getsome")       # groups windows under the app-menu icon (Wayland app id)
    try:
        from PySide6.QtGui import QIcon

        from .. import desktop

        desktop.refresh_if_installed()
        if desktop.icon_file().exists():
            app.setWindowIcon(QIcon(str(desktop.icon_file())))
    except Exception:
        pass
    overlays.load_fonts()
    app.setStyleSheet(theme.STYLE)
    win = MainWindow()
    win.show()
    _steam_art_later()
    return app.exec()


def _steam_art_later() -> None:
    """If Neon Cable 42 was added to Steam by hand, give its shortcut our artwork (once)."""
    from PySide6.QtCore import QTimer

    def go():
        try:
            from .. import steam_shortcut

            steam_shortcut.ensure_artwork()
        except Exception as e:      # never get in the way of starting up
            import logging

            logging.getLogger("getsome.steam_shortcut").warning("Steam artwork check failed: %s", e)

    QTimer.singleShot(3000, go)
