"""Right-hand panel: edit one channel. Changes are saved as you go; building is separate."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import channel_writer, library, model
from . import theme
from .dialogs import ShowPicker, button, ensure_library, h2, hint

ORDER_ITEMS = [("Channel default", model.DEFAULT), ("In order", model.SEQUENTIAL), ("Shuffle", model.SHUFFLE)]


class NoWheelCombo(QComboBox):
    """Scrolling the page over a dropdown must not change it."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, ev):
        ev.ignore()


class EpisodePicker(QDialog):
    """Searchable list of a show's episodes, in play order."""

    def __init__(self, info: library.LibShow, current: str | None, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem

        self.setWindowTitle(f"Start {info.title} at…")
        self.resize(520, 620)
        self.code = current
        lay = QVBoxLayout(self)
        lay.addWidget(theme.ChromeLabel("START AT", 22))
        lay.addWidget(hint(f"{info.title}: the first episode to air. Later episodes follow in order."))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: S03, S03E05 or part of a title…")
        lay.addWidget(self.search)
        self.list = QListWidget()
        first = QListWidgetItem("From the first episode")
        first.setData(Qt.UserRole, None)
        self.list.addItem(first)
        for code, name in zip(info.codes, info.names):
            it = QListWidgetItem(f"{code}    {name}" if name else code)
            it.setData(Qt.UserRole, code)
            self.list.addItem(it)
            if code == current:
                self.list.setCurrentItem(it)
        if current is None:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _it: self._ok())
        lay.addWidget(self.list, 1)
        self.search.textChanged.connect(self._filter)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = button("Start here", "primary")
        ok.clicked.connect(self._ok)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.list.scrollToItem(self.list.currentItem())
        self.search.setFocus()

    def _filter(self, text: str):
        t = text.lower().strip()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _ok(self):
        it = self.list.currentItem()
        if it is None or it.isHidden():
            return
        self.code = it.data(Qt.UserRole)
        self.accept()


class ChannelEditor(QFrame):
    changed = Signal()            # something about the channel changed (already saved)
    build_requested = Signal()
    delete_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.ch: model.Channel | None = None
        self.channels: list[model.Channel] = []
        self._loading = False
        self._scan_cache: dict[str, library.LibShow] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        col = QVBoxLayout()
        col.addWidget(h2("Channel"))
        self.number = QSpinBox()
        self.number.setRange(1, 999)
        self.number.setFixedWidth(110)
        self.number.valueChanged.connect(self._number_changed)
        col.addWidget(self.number)
        top.addLayout(col)
        col = QVBoxLayout()
        col.addWidget(h2("Name"))
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._name_changed)
        col.addWidget(self.name)
        top.addLayout(col, 1)
        lay.addLayout(top)
        self.error = QLabel("")
        self.error.setObjectName("warn")
        self.error.hide()
        lay.addWidget(self.error)

        lay.addWidget(h2("Style"))
        seg = QHBoxLayout()
        self.tmpl_group = QButtonGroup(self)
        for i, t in enumerate(model.TEMPLATES):
            b = button(t.name, "seg")
            b.setCheckable(True)
            b.setToolTip(t.blurb)
            self.tmpl_group.addButton(b, i)
            seg.addWidget(b)
        self.tmpl_group.idClicked.connect(self._template_clicked)
        lay.addLayout(seg)
        self.blurb = hint("")
        lay.addWidget(self.blurb)

        opts = QHBoxLayout()
        self.mode_label = QLabel("Shows play")
        opts.addWidget(self.mode_label)
        self.mode = NoWheelCombo()
        self.mode.addItem("In order (remembers its place)", model.SEQUENTIAL)
        self.mode.addItem("Shuffled", model.SHUFFLE)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        opts.addWidget(self.mode)
        opts.addSpacing(20)
        self.rotate_label = QLabel("Switch shows")
        tip = ("every episode: one episode of each show in turn (Simpsons, Futurama, Simpsons…).\n"
               "every N hours: each in-order show gets its own hours; shuffled shows share the rest.")
        opts.addWidget(self.rotate_label)
        self.rotate = NoWheelCombo()
        for h in model.ROTATE_CHOICES:
            self.rotate.addItem(model.rotate_text(h), h)
        self.rotate.currentIndexChanged.connect(self._rotate_changed)
        self.rotate.setToolTip(tip)
        opts.addWidget(self.rotate)
        opts.addStretch(1)
        self.opts_widget = QWidget()
        self.opts_widget.setLayout(opts)
        opts.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.opts_widget)

        from PySide6.QtWidgets import QCheckBox

        opts2 = QHBoxLayout()
        opts2.addWidget(QLabel("Start times"))
        self.start_mode = NoWheelCombo()
        for key, label in model.START_MODES:
            self.start_mode.addItem(label, key)
        self.start_mode.setToolTip("Back to back: each programme starts when the last one ends.\n"
                                   "On the hour / half hour: programmes start on the clock like real TV; "
                                   "the gap before shows an intermission countdown.")
        self.start_mode.currentIndexChanged.connect(self._start_mode_changed)
        opts2.addWidget(self.start_mode)
        opts2.addSpacing(20)
        self.upnext = QCheckBox("“Up next” bumpers between shows")
        self.upnext.setToolTip("A ~10 second animated 'UP NEXT' clip with music after every show")
        self.upnext.toggled.connect(self._upnext_changed)
        opts2.addWidget(self.upnext)
        opts2.addStretch(1)
        lay.addLayout(opts2)

        sched = QHBoxLayout()
        sched.addWidget(QLabel("Schedule"))
        self.schedule_text = QLabel("")
        self.schedule_text.setObjectName("dim")
        sched.addWidget(self.schedule_text)
        sched.addSpacing(12)
        self.schedule_btn = button("▸  Time blocks && broadcast hours…", "picker")
        self.schedule_btn.setToolTip("Weekly blocks like “Sat 7–11 AM: cartoons”, sign-off at night, "
                                     "and what plays while the channel is off the air")
        self.schedule_btn.clicked.connect(self._edit_schedule)
        sched.addWidget(self.schedule_btn)
        sched.addStretch(1)
        lay.addLayout(sched)

        lockrow = QHBoxLayout()
        self.restricted = QCheckBox("Restricted (needs the PIN)")
        self.restricted.setToolTip("In TV mode this channel is blurred and silent until the PIN is entered\n"
                                   "(press OK on the channel). Once entered, restricted channels stay unlocked\n"
                                   "until TV mode is closed. Set the PIN in Settings (default 69).")
        self.restricted.toggled.connect(self._restricted_changed)
        lockrow.addWidget(self.restricted)
        self.lock_mode = NoWheelCombo()
        self.lock_mode.addItem("all day", "always")
        self.lock_mode.addItem("except from", "hours")
        self.lock_mode.setToolTip("“Except from … to …”: no PIN needed during those hours, e.g. an R-rated\n"
                                  "movie channel that's locked during the day and opens by itself at 9 PM.")
        self.lock_mode.currentIndexChanged.connect(self._lock_changed)
        lockrow.addWidget(self.lock_mode)
        self.free_from, self.free_to = NoWheelCombo(), NoWheelCombo()
        for combo in (self.free_from, self.free_to):
            for h in list(range(6, 24)) + list(range(0, 6)):
                combo.addItem("12 AM (midnight)" if h == 0 else model.hour_text(h), h)
            combo.currentIndexChanged.connect(self._lock_changed)
        lockrow.addWidget(self.free_from)
        self.lock_to_label = QLabel("to")
        lockrow.addWidget(self.lock_to_label)
        lockrow.addWidget(self.free_to)
        lockrow.addStretch(1)
        lay.addLayout(lockrow)

        adrow = QHBoxLayout()
        self.ads = QCheckBox("Commercial breaks")
        self.ads.setToolTip("Breaks inside episodes where the show fades to black with a pause (its act breaks)\n"
                            "or at chapter markers, and after each show. Never at random moments.\n"
                            "Show cards, trivia and your own commercials (Settings → Commercial folders).")
        self.ads.toggled.connect(self._ads_changed)
        adrow.addWidget(self.ads)
        self.ads_note = QLabel("Your own commercials: Settings → Commercial folders")
        self.ads_note.setObjectName("dim")
        adrow.addWidget(self.ads_note)
        # older channels may have their own ads folder: they keep it, and can stop using it here
        self.own_ads = button("", "picker")
        self.own_ads.clicked.connect(self._pick_own_ads)
        adrow.addWidget(self.own_ads)
        adrow.addStretch(1)
        self.ads_widget = QWidget()
        self.ads_widget.setLayout(adrow)
        adrow.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.ads_widget)

        hdr = QHBoxLayout()
        self.list_title = h2("Shows")
        hdr.addWidget(self.list_title)
        hdr.addStretch(1)
        self.add_movies_btn = button("+ Add movies")
        self.add_movies_btn.setToolTip("Mix films into this channel, e.g. a Star Wars channel with the films and the shows")
        self.add_movies_btn.clicked.connect(self._edit_tv_movies)
        hdr.addWidget(self.add_movies_btn)
        self.add_btn = button("+ Add shows", "primary")
        self.add_btn.clicked.connect(self._add_shows)
        hdr.addWidget(self.add_btn)
        lay.addLayout(hdr)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["SHOW", "EPISODES", "ORDER", "START AT", ""])
        hh = self.table.horizontalHeader()
        # every column can be dragged wider/narrower; the episode column soaks up spare room
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setMinimumSectionSize(40)
        for col, width in ((0, 280), (1, 90), (2, 175), (4, 44)):
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.table, 1)
        self.empty_hint = hint("No shows yet. Click “+ Add shows” to pick them from your library.")
        lay.addWidget(self.empty_hint)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        bottom.addWidget(self.status, 1)
        delete = button("Delete channel", "danger")
        delete.clicked.connect(self.delete_requested.emit)
        bottom.addWidget(delete)
        self.build_btn = button("Build channel", "primary")
        self.build_btn.clicked.connect(self.build_requested.emit)
        bottom.addWidget(self.build_btn)
        lay.addLayout(bottom)

    # ---------------------------------------------------------------- load
    def load(self, ch: model.Channel, channels: list[model.Channel]) -> None:
        self._loading = True
        self.ch, self.channels = ch, channels
        self.number.setValue(ch.number)
        self.name.setText(ch.name)
        keys = [t.key for t in model.TEMPLATES]
        self.tmpl_group.button(keys.index(ch.template) if ch.template in keys else 1).setChecked(True)
        self.blurb.setText(model.TEMPLATE_BY_KEY.get(ch.template, model.TEMPLATES[1]).blurb)
        self.mode.setCurrentIndex(0 if ch.mode == model.SEQUENTIAL else 1)
        self.rotate.setCurrentIndex(model.ROTATE_CHOICES.index(ch.rotate_hours) if ch.rotate_hours in model.ROTATE_CHOICES else 1)
        self.upnext.setChecked(ch.upnext)
        keys_ = [k for k, _ in model.START_MODES]
        self.start_mode.setCurrentIndex(keys_.index(ch.start_mode) if ch.start_mode in keys_ else 0)
        self.ads.setChecked(ch.commercials)
        self.restricted.setChecked(ch.restricted)
        self.lock_mode.setCurrentIndex(1 if ch.lock_mode == "hours" else 0)
        self.free_from.setCurrentIndex(max(0, self.free_from.findData(ch.free_from)))
        self.free_to.setCurrentIndex(max(0, self.free_to.findData(ch.free_to)))
        self._update_lock_ui()
        self.error.hide()
        self._fill_table()
        self._loading = False
        self.refresh_status()

    def _update_ads_ui(self) -> None:
        ch = self.ch
        self.ads_widget.setVisible(ch.kind != model.MOVIES)
        self.ads_note.setVisible(ch.commercials)
        self.own_ads.setVisible(bool(ch.ad_folder))
        self.own_ads.setText(f"Stop using “{Path(ch.ad_folder).name}” on this channel" if ch.ad_folder else "")

    def _ads_changed(self, on: bool) -> None:
        if self._loading or self.ch is None:
            return
        self.ch.commercials = on
        self._update_ads_ui()
        self._commit()

    def _pick_own_ads(self) -> None:
        """Older channels' own ads folder: stop using it (Settings → Commercial folders replaces it)."""
        from PySide6.QtWidgets import QMessageBox

        if self.ch.ad_folder and QMessageBox.question(
                self, "This channel's ads folder",
                f"This channel also plays ads from:\n{self.ch.ad_folder}\n\nStop using it on this channel? "
                "(Add it in Settings → Commercial folders to use it on every channel.)") == QMessageBox.Yes:
            self.ch.ad_folder = ""
            self._update_ads_ui()
            self._commit()

    def _update_kind_ui(self) -> None:
        self._update_ads_ui()
        movies = self.ch.kind == model.MOVIES
        self.opts_widget.setVisible(not movies)
        self.upnext.setVisible(self.ch.start_mode == model.CONTINUOUS)
        self.list_title.setText("MOVIES" if movies else "SHOWS")
        self.add_movies_btn.setVisible(not movies)
        self.add_btn.setText("+ Add movies" if movies else "+ Add shows")
        self.table.setHorizontalHeaderLabels(["MOVIE", "YEAR", "GENRE", "LENGTH", ""] if movies
                                             else ["SHOW", "EPISODES", "ORDER", "START AT", ""])
        self.empty_hint.setText("No movies yet. Click “+ Add movies” to pick them from your library." if movies
                                else "No shows yet. Click “+ Add shows” to pick them from your library.")

    def _fill_movies(self) -> None:
        from .. import media_index

        ch = self.ch
        self.table.setRowCount(0)
        for r, path in enumerate(ch.movies):
            m = library.lookup_movie(path) or self._scan_cache.get(path)
            if m is None and Path(path).is_file():
                m = self._scan_cache[path] = library.movie_from_scan(media_index.read_movie(Path(path)))
            self.table.insertRow(r)
            title = QTableWidgetItem(m.title if m else Path(path).name)
            title.setToolTip(path)
            self.table.setItem(r, 0, title)
            for col, text in ((1, str(m.year or "") if m else ""), (2, " / ".join(m.genres[:3]) if m else ""),
                              (3, m.length_text if m else "")):
                it = QTableWidgetItem(text)
                if col != 2:
                    it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, col, it)
            rm = button("✕", "flat")
            rm.setToolTip("Remove from this channel")
            rm.clicked.connect(lambda _c=False, p_=path: self._remove_movie(p_))
            self.table.setCellWidget(r, 4, rm)
        self.empty_hint.setVisible(not ch.movies)

    def _movies_row(self, r: int) -> None:
        """Films on a TV channel: one row that rotates like a show."""
        ch = self.ch
        self.table.insertRow(r)
        it = QTableWidgetItem(f"🎬  Movies  ({len(ch.movies)} films)")
        it.setToolTip("In order = by release year. They take turns with the shows like another series.")
        self.table.setItem(r, 0, it)
        n = QTableWidgetItem(str(len(ch.movies)))
        n.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(r, 1, n)
        order = NoWheelCombo()
        order.setMinimumWidth(160)
        for label, val in (("Channel default", model.DEFAULT), ("In order (by year)", model.SEQUENTIAL),
                           ("Shuffle", model.SHUFFLE)):
            order.addItem(label, val)
        order.setCurrentIndex(max(0, order.findData(ch.movie_mode)))
        order.currentIndexChanged.connect(lambda _i, w=order: self._movie_mode(w.currentData()))
        self.table.setCellWidget(r, 2, order)
        pick = button("▸  Choose films…", "picker")
        pick.clicked.connect(self._edit_tv_movies)
        self.table.setCellWidget(r, 3, pick)
        rm = button("✕", "flat")
        rm.setToolTip("Remove all films from this channel")
        rm.clicked.connect(self._clear_tv_movies)
        self.table.setCellWidget(r, 4, rm)

    def _movie_mode(self, mode: str) -> None:
        self.ch.movie_mode = mode
        self._commit(refill=True)

    def _clear_tv_movies(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(self, "Remove films", f"Remove all {len(self.ch.movies)} films from this channel?") \
                == QMessageBox.Yes:
            self.ch.movies = []
            self._commit(refill=True)

    def _edit_tv_movies(self) -> None:
        from .dialogs import MoviePicker, ensure_movie_library

        movies = ensure_movie_library(self)
        if movies is None:
            return
        known = {m.path for m in movies}
        extra = [self._scan_cache.get(p) or library.lookup_movie(p) for p in self.ch.movies if p not in known]
        picker = MoviePicker(movies + [m for m in extra if m], set(self.ch.movies), self, editable=True)
        if picker.exec() == QDialog.Accepted:
            for path, m in picker.extra.items():
                self._scan_cache[path] = m
            self.ch.movies = picker.selected()
            self._commit(refill=True)

    def _remove_movie(self, path: str) -> None:
        self.ch.movies.remove(path)
        self._commit(refill=True)

    def _fill_table(self) -> None:
        ch = self.ch
        self._update_kind_ui()
        if ch.kind == model.MOVIES:
            self._fill_movies()
            return
        self.table.setRowCount(0)
        for r, show in enumerate(ch.shows):
            info = library.lookup(show.folder) or self._scan_cache.get(show.folder)
            if info is None and Path(show.folder).is_dir():
                try:   # a folder picked from outside the library: scan just this show
                    info = self._scan_cache[show.folder] = library.scan_one(show.folder)
                except OSError:
                    info = None
            if not show.title:
                show.title = info.title if info else Path(show.folder).name
            self.table.insertRow(r)
            it = QTableWidgetItem(show.title)
            it.setToolTip(show.folder)
            self.table.setItem(r, 0, it)
            eps = QTableWidgetItem(str(info.episodes) if info else "?")
            eps.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 1, eps)

            order = NoWheelCombo()
            order.setMinimumWidth(160)
            for label, val in ORDER_ITEMS:
                order.addItem(label, val)
            order.setCurrentIndex([v for _, v in ORDER_ITEMS].index(show.mode))
            order.currentIndexChanged.connect(lambda _i, s=show, w=order: self._show_mode(s, w.currentData()))
            self.table.setCellWidget(r, 2, order)

            start = button(self._start_label(show, info), "picker")
            start.setToolTip("Which episode airs first. Applied the next time you build.")
            start.setEnabled(show.effective_mode(ch) == model.SEQUENTIAL and info is not None)
            start.clicked.connect(lambda _c=False, s=show, i=info: self._pick_start(s, i))
            self.table.setCellWidget(r, 3, start)

            rm = button("✕", "flat")
            rm.setToolTip("Remove from this channel")
            rm.clicked.connect(lambda _c=False, s=show: self._remove_show(s))
            self.table.setCellWidget(r, 4, rm)
        if ch.movies:
            self._movies_row(len(ch.shows))
        self.empty_hint.setVisible(not ch.shows and not ch.movies)
        refs = ch.shows + ([ch.movies_ref()] if ch.movies else [])
        # with two or more entries there's something to switch between (every episode works
        # for shuffled shows too)
        self.rotate.setEnabled(len(refs) > 1)
        self.rotate_label.setEnabled(len(refs) > 1)

    def _edit_schedule(self) -> None:
        from .schedule_editor import ScheduleDialog

        ScheduleDialog(self.ch, lambda: self._commit(refill=True), self).exec()
        self._commit(refill=True)

    def refresh_status(self) -> None:
        ch = self.ch
        if ch is None:
            return
        self.schedule_text.setText(ch.schedule_text)
        if not ch.has_content:
            self.status.setText("Add some movies, then build." if ch.kind == model.MOVIES else "Add some shows, then build.")
            self.status.setObjectName("dim")
        elif not ch.built_hash:
            self.status.setText("● Not built yet")
            self.status.setObjectName("warn")
        elif channel_writer.preview_hash(ch) != ch.built_hash or any(s.start_pending for s in ch.all_show_refs()):
            self.status.setText("● Changed since last build")
            self.status.setObjectName("warn")
        else:
            self.status.setText("● On the air")
            self.status.setObjectName("ok")
        if ch.has_content and not ch.has_base_content:
            self.status.setText(self.status.text() + "  (only during its time blocks)")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.build_btn.setEnabled(ch.has_content)

    # ---------------------------------------------------------------- edits
    def _commit(self, refill: bool = False) -> None:
        if self._loading or self.ch is None:
            return
        model.save(self.channels)
        if refill:
            self._fill_table()
        self.refresh_status()
        self.changed.emit()

    def _number_changed(self, v: int) -> None:
        if self._loading or self.ch is None:
            return
        other = next((c for c in self.channels if c is not self.ch and c.number == v), None)
        if v == model.guide_number():
            self.error.setText(f"Channel {v} is the TV guide (change that in Settings).")
            self.error.show()
            return
        if other:
            self.error.setText(f'Channel {v} is already “{other.name}”.')
            self.error.show()
            return
        self.error.hide()
        self.ch.number = v
        self._commit()

    def _name_changed(self) -> None:
        if self._loading or self.ch is None:
            return
        name = self.name.text().strip()
        if not name or name == self.ch.name:
            self.name.setText(self.ch.name)
            return
        if model.find(self.channels, name):
            self.error.setText(f'There is already a channel called “{name}”.')
            self.error.show()
            self.name.setText(self.ch.name)
            return
        self.error.hide()
        self.ch.name = name
        self._commit()

    def _template_clicked(self, i: int) -> None:
        t = model.TEMPLATES[i]
        self.ch.template = t.key
        self.ch.mode = t.mode
        self.ch.rotate_hours = t.rotate_hours
        if t.kind != self.ch.kind:
            self.ch.kind = t.kind
            self.ch.start_mode = t.start_mode
            self._loading = True
            self.start_mode.setCurrentIndex([k for k, _ in model.START_MODES].index(t.start_mode))
            self._loading = False
        self.blurb.setText(t.blurb)
        self._loading = True
        self.mode.setCurrentIndex(0 if t.mode == model.SEQUENTIAL else 1)
        self.rotate.setCurrentIndex(model.ROTATE_CHOICES.index(t.rotate_hours))
        self._loading = False
        self._commit(refill=True)

    def _mode_changed(self, _i: int) -> None:
        if self._loading or self.ch is None:
            return
        self.ch.mode = self.mode.currentData()
        self._commit(refill=True)

    def _start_mode_changed(self, _i: int) -> None:
        if self._loading or self.ch is None:
            return
        self.ch.start_mode = self.start_mode.currentData()
        self._commit(refill=True)

    def _restricted_changed(self, on: bool) -> None:
        if self._loading or self.ch is None:
            return
        self.ch.restricted = on          # TV mode only: no rebuild needed
        self._update_lock_ui()
        self._commit()

    def _update_lock_ui(self) -> None:
        on = self.restricted.isChecked()
        hours = self.lock_mode.currentData() == "hours"
        self.lock_mode.setEnabled(on)
        for wdg in (self.free_from, self.lock_to_label, self.free_to):
            wdg.setVisible(hours)
            wdg.setEnabled(on)

    def _lock_changed(self, _i: int) -> None:
        if self._loading or self.ch is None:
            return
        self.ch.lock_mode = self.lock_mode.currentData()
        self.ch.free_from = int(self.free_from.currentData())
        self.ch.free_to = int(self.free_to.currentData())
        self._update_lock_ui()
        self._commit()

    def _upnext_changed(self, on: bool) -> None:
        if self._loading or self.ch is None:
            return
        self.ch.upnext = on
        self._commit()

    def _rotate_changed(self, _i: int) -> None:
        if self._loading or self.ch is None:
            return
        self.ch.rotate_hours = int(self.rotate.currentData())
        self._commit()

    def _show_mode(self, show: model.ShowRef, mode: str) -> None:
        show.mode = mode
        self._commit(refill=True)

    def _start_label(self, show: model.ShowRef, info) -> str:
        if show.effective_mode(self.ch) != model.SEQUENTIAL:
            return "—  (shuffled)"
        if not show.start:
            return "▸  From the first episode"
        name = ""
        if info and show.start in info.codes:
            name = info.names[info.codes.index(show.start)]
        return f"▸  {show.start}  {name}".rstrip()

    def _pick_start(self, show: model.ShowRef, info) -> None:
        if info is None:
            return
        dlg = EpisodePicker(info, show.start, self)
        if dlg.exec() == QDialog.Accepted:
            self._show_start(show, dlg.code)
            self._fill_table()

    def _show_start(self, show: model.ShowRef, code: str | None) -> None:
        show.start = code
        show.start_pending = True
        self._commit()

    def _remove_show(self, show: model.ShowRef) -> None:
        self.ch.shows.remove(show)
        self._commit(refill=True)

    def _add_shows(self) -> None:
        if self.ch.kind == model.MOVIES:
            from .dialogs import MoviePicker, ensure_movie_library

            movies = ensure_movie_library(self)
            if movies is None:
                return
            picker = MoviePicker(movies, set(self.ch.movies), self)
            if picker.exec() == QDialog.Accepted:
                for path in picker.selected():
                    if path not in self.ch.movies:
                        self.ch.movies.append(path)
                for path, m in picker.extra.items():
                    self._scan_cache[path] = m
                self._commit(refill=True)
            return
        shows = ensure_library(self)
        if shows is None:
            return
        picker = ShowPicker(shows, {s.folder for s in self.ch.shows}, self)
        if picker.exec() != QDialog.Accepted:
            return
        for folder in picker.selected():
            info = library.lookup(folder)
            self.ch.shows.append(model.ShowRef(folder=folder, title=info.title if info else ""))
        self._commit(refill=True)
