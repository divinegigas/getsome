"""The channel's week: time blocks on a 7-day grid, broadcast hours and off-air mode.

The grid runs like a TV listing: each column is a broadcast day starting at 6 AM, so
the small hours after midnight sit at the bottom of the evening before.
Drag across the grid to make a block; click a block to edit it.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QFont, QPainter, QPen
from ..brand import QColor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QToolTip, QVBoxLayout,
    QWidget,
)

from .. import channel_writer, library, model
from . import theme
from .dialogs import ShowPicker, button, choose_folder, ensure_library, h2, hint

DAY_START = 6          # the grid's first row, like a TV listing's broadcast day
BLOCK_COLORS = ["#ff2bd6", "#00f0ff", "#ffb000", "#7cff4f", "#b26bff", "#ff6b4f", "#4fa3ff", "#ffe600"]
BASE_COLOR = "#3a2a7a"
OFF_COLOR = "#0a0618"
INFO_COLOR = "#1f6f66"


def row_hour(r: int) -> int:
    return (DAY_START + r) % 24


def to_cell(col: int, row: int) -> tuple[int, int]:
    """Grid column/row -> real (weekday, hour)."""
    h = row_hour(row)
    return ((col + (1 if h < DAY_START else 0)) % 7, h)


def to_grid(d: int, h: int) -> tuple[int, int]:
    """Real (weekday, hour) -> grid column/row."""
    return ((d - (1 if h < DAY_START else 0)) % 7, (h - DAY_START) % 24)


def block_color(ch: model.Channel, b: model.Block) -> str:
    i = next((k for k, x in enumerate(ch.blocks) if x is b), 0)
    return BLOCK_COLORS[i % len(BLOCK_COLORS)]


def hour_items(combo, midnight_label: str = "12 AM (midnight)") -> None:
    for h in list(range(DAY_START, 24)) + list(range(0, DAY_START)):
        combo.addItem(midnight_label if h == 0 else model.hour_text(h), h)


def overlap(ch: model.Channel, b: model.Block) -> tuple[model.Block, tuple[int, int]] | None:
    """The first other block sharing an hour with b, and that hour."""
    mine = b.cells()
    for other in ch.blocks:
        if other.id == b.id:
            continue
        common = mine & other.cells()
        if common:
            return other, min(common, key=lambda c: (c[0], (c[1] - DAY_START) % 24))
    return None


# --------------------------------------------------------------------------- grid


class WeekGrid(QWidget):
    block_clicked = Signal(object)                 # model.Block
    range_selected = Signal(list, int, int)        # days, start hour, end hour

    GUTTER, HEADER = 62, 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ch: model.Channel | None = None
        self.grid: dict = {}
        self.drag: tuple[QPoint, QPoint] | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(560, 24 * 18 + self.HEADER)
        self.setCursor(Qt.CrossCursor)

    def set_channel(self, ch: model.Channel) -> None:
        self.ch = ch
        self.refresh()

    def refresh(self) -> None:
        try:
            self.grid = channel_writer.week_grid(self.ch, include_empty=True) if self.ch else {}
        except Exception:
            self.grid = {}
        self.update()

    # geometry
    def _cw(self) -> float:
        return (self.width() - self.GUTTER - 2) / 7

    def _rh(self) -> float:
        return (self.height() - self.HEADER - 2) / 24

    def _cell_at(self, pos) -> tuple[int, int] | None:
        x, y = pos.x() - self.GUTTER, pos.y() - self.HEADER
        if x < 0 or y < 0:
            return None
        col, row = int(x // self._cw()), int(y // self._rh())
        if 0 <= col < 7 and 0 <= row < 24:
            return col, row
        return None

    def _rect(self, col: int, row: int, rows: int = 1) -> QRectF:
        return QRectF(self.GUTTER + col * self._cw() + 1, self.HEADER + row * self._rh() + 1,
                      self._cw() - 2, rows * self._rh() - 2)

    def _entry(self, col: int, row: int):
        return self.grid.get(to_cell(col, row), ("off", None))

    # painting
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#07021a"))
        small = QFont(self.font())
        small.setPixelSize(11)
        p.setFont(small)
        cw, rh = self._cw(), self._rh()
        for c, name in enumerate(model.DAY_NAMES):
            p.setPen(QColor(theme.CYAN))
            p.drawText(QRectF(self.GUTTER + c * cw, 0, cw, self.HEADER), Qt.AlignCenter, name.upper())
        for r in range(24):
            h = row_hour(r)
            if r % 2 == 0 or rh > 22:
                p.setPen(QColor(theme.DIM))
                p.drawText(QRectF(0, self.HEADER + r * rh, self.GUTTER - 8, rh), Qt.AlignRight | Qt.AlignVCenter,
                           model.hour_text(h))
        if not self.ch:
            return
        label = QFont(self.font())
        label.setPixelSize(12)
        label.setBold(True)
        for c in range(7):
            r = 0
            while r < 24:
                kind, b = self._entry(c, r)
                n = 1
                while r + n < 24 and self._same(self._entry(c, r + n), (kind, b)):
                    n += 1
                self._draw_run(p, c, r, n, kind, b, label)
                r += n
        # drag selection
        if self.drag:
            (c0, r0), (c1, r1) = self.drag
            rect = self._rect(min(c0, c1), min(r0, r1), abs(r1 - r0) + 1)
            rect.setWidth((abs(c1 - c0) + 1) * cw - 2)
            p.setPen(QPen(QColor("#ffffff"), 2, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(rect, 4, 4)
        p.end()

    @staticmethod
    def _same(a, b) -> bool:
        return a[0] == b[0] and a[1] is b[1]

    def _draw_run(self, p: QPainter, c: int, r: int, n: int, kind: str, b, font: QFont) -> None:
        rect = self._rect(c, r, n)
        if kind == "block":
            col = QColor(block_color(self.ch, b))
            fill = QColor(col)
            fill.setAlpha(150 if b.has_content else 50)
            p.setPen(QPen(col, 1.5, Qt.SolidLine if b.has_content else Qt.DashLine))
            p.setBrush(fill)
            text = b.name if b.has_content else f"{b.name}\n(empty)"
        elif kind == "base":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(BASE_COLOR))
            text = ""
        elif kind == "info":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(INFO_COLOR))
            text = "Paid programming" if n >= 2 else ""
        else:
            p.setPen(QPen(QColor("#2c2357"), 1))
            p.setBrush(QBrush(QColor("#1a1238"), Qt.BDiagPattern))
            text = "Off air" if n >= 2 else ""
        p.drawRoundedRect(rect, 3, 3)
        if text and rect.height() >= 14:
            p.setFont(font)
            p.setPen(QColor("#ffffff") if kind == "block" else QColor(theme.DIM))
            p.drawText(rect.adjusted(4, 2, -4, -2), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text)

    # mouse
    def mousePressEvent(self, ev):
        cell = self._cell_at(ev.position().toPoint())
        if cell is None or ev.button() != Qt.LeftButton:
            return
        self.drag = (cell, cell)
        self.update()

    def mouseMoveEvent(self, ev):
        cell = self._cell_at(ev.position().toPoint())
        if self.drag and cell:
            self.drag = (self.drag[0], cell)
            self.update()
        elif cell and self.ch:
            kind, b = self._entry(*cell)
            d, h = to_cell(*cell)
            what = {"block": f"{b.name}  ·  {b.when_text}" if b else "", "base": "Regular rotation",
                    "info": "Paid programming (off-air hours)", "off": "Off the air"}[kind]
            QToolTip.showText(ev.globalPosition().toPoint(),
                              f"{model.DAY_NAMES[d]} {model.hour_text(h)}: {what}", self)

    def mouseReleaseEvent(self, ev):
        if not self.drag:
            return
        (c0, r0), (c1, r1) = self.drag
        self.drag = None
        self.update()
        if (c0, r0) == (c1, r1):
            kind, b = self._entry(c0, r0)
            if kind == "block":
                self.block_clicked.emit(b)
                return
        cols = range(min(c0, c1), max(c0, c1) + 1)
        rmin, rmax = min(r0, r1), max(r0, r1)
        start, end = row_hour(rmin), row_hour(rmax + 1)
        # a block starting after midnight belongs to the next calendar day
        days = sorted({(c + (1 if start < DAY_START else 0)) % 7 for c in cols})
        self.range_selected.emit(days, start, end)


# --------------------------------------------------------------------------- block editor


class BlockDialog(QDialog):
    """Edit one time block: when it airs and what it plays."""

    def __init__(self, ch: model.Channel, block: model.Block, is_new: bool, parent=None):
        super().__init__(parent)
        from .channel_editor import ORDER_ITEMS, NoWheelCombo

        self.ch = ch
        self.b = copy.deepcopy(block)
        self.deleted = False
        self._scan_cache: dict = {}
        self.setWindowTitle("New time block" if is_new else f"Edit {block.name}")
        self.resize(760, 720)
        lay = QVBoxLayout(self)
        lay.addWidget(theme.ChromeLabel("TIME BLOCK", 22))

        lay.addWidget(h2("Name"))
        self.name = QLineEdit(self.b.name)
        self.name.setPlaceholderText("e.g. Saturday Morning Cartoons")
        lay.addWidget(self.name)

        lay.addWidget(h2("Days"))
        days = QHBoxLayout()
        self.day_btns = []
        for i, dn in enumerate(model.DAY_NAMES):
            btn = button(dn, "seg")
            btn.setCheckable(True)
            btn.setChecked(i in self.b.days)
            btn.toggled.connect(self._update_summary)
            self.day_btns.append(btn)
            days.addWidget(btn)
        days.addSpacing(16)
        for label, preset in model.DAY_PRESETS[:3]:
            pb = button(label, "flat")
            pb.clicked.connect(lambda _c=False, ds=preset: self._set_days(ds))
            days.addWidget(pb)
        days.addStretch(1)
        lay.addLayout(days)

        when = QHBoxLayout()
        when.addWidget(QLabel("From"))
        self.start = NoWheelCombo()
        hour_items(self.start, "12 AM (midnight)")
        self.start.setCurrentIndex(self.start.findData(self.b.start))
        when.addWidget(self.start)
        when.addWidget(QLabel("to"))
        self.end = NoWheelCombo()
        hour_items(self.end, "12 AM (midnight)")
        self.end.setCurrentIndex(self.end.findData(self.b.end % 24))
        when.addWidget(self.end)
        self.start.currentIndexChanged.connect(self._update_summary)
        self.end.currentIndexChanged.connect(self._update_summary)
        when.addSpacing(20)
        self.summary = QLabel("")
        self.summary.setObjectName("dim")
        when.addWidget(self.summary, 1)
        lay.addLayout(when)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Shows play"))
        self.mode = NoWheelCombo()
        self.mode.addItem("Shuffled", model.SHUFFLE)
        self.mode.addItem("In order (remembers its place)", model.SEQUENTIAL)
        self.mode.setCurrentIndex(self.mode.findData(self.b.mode))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        opts.addWidget(self.mode)
        opts.addSpacing(20)
        self.rotate_label = QLabel("Switch shows")
        opts.addWidget(self.rotate_label)
        self.rotate = NoWheelCombo()
        for hh in model.ROTATE_CHOICES:
            self.rotate.addItem(model.rotate_text(hh), hh)
        self.rotate.setCurrentIndex(max(0, self.rotate.findData(self.b.rotate_hours)))
        self.rotate.currentIndexChanged.connect(lambda _i: setattr(self.b, "rotate_hours", self.rotate.currentData()))
        opts.addWidget(self.rotate)
        opts.addStretch(1)
        lay.addLayout(opts)

        hdr = QHBoxLayout()
        hdr.addWidget(h2("What plays"))
        hdr.addStretch(1)
        films = button("+ Add movies")
        films.clicked.connect(self._pick_movies)
        hdr.addWidget(films)
        add = button("+ Add shows", "primary")
        add.clicked.connect(self._add_shows)
        hdr.addWidget(add)
        lay.addLayout(hdr)

        self.ORDER_ITEMS, self.Combo = ORDER_ITEMS, NoWheelCombo
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["SHOW", "ORDER", "START AT", ""])
        hh_ = self.table.horizontalHeader()
        hh_.setSectionResizeMode(QHeaderView.Interactive)
        hh_.setSectionResizeMode(2, QHeaderView.Stretch)
        for col, width in ((0, 250), (1, 175), (3, 44)):
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.table, 1)
        self.empty = hint("Nothing in this block yet. Add shows and/or movies. "
                          "A show already on this channel keeps its place in order wherever it airs.")
        lay.addWidget(self.empty)

        self.error = QLabel("")
        self.error.setObjectName("warn")
        self.error.setWordWrap(True)
        self.error.hide()
        lay.addWidget(self.error)

        row = QHBoxLayout()
        if not is_new:
            rm = button("Delete block", "danger")
            rm.clicked.connect(self._delete)
            row.addWidget(rm)
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = button("Save block", "primary")
        ok.clicked.connect(self._save)
        row.addWidget(ok)
        lay.addLayout(row)
        self._update_summary()
        self._fill()

    # ---- when
    def _set_days(self, days: list[int]) -> None:
        for i, btn in enumerate(self.day_btns):
            btn.setChecked(i in days)

    def _read_when(self) -> None:
        self.b.days = [i for i, btn in enumerate(self.day_btns) if btn.isChecked()]
        self.b.start = int(self.start.currentData())
        self.b.end = int(self.end.currentData())

    def _update_summary(self, *_a) -> None:
        self._read_when()
        n = self.b.length
        late = " (runs past midnight)" if self.b.end <= self.b.start and self.b.end != 0 else ""
        self.summary.setText(f"{model.days_text(self.b.days)}, {n} hour" + ("s" if n != 1 else "") + late)

    # ---- content
    def _mode_changed(self, _i: int) -> None:
        self.b.mode = self.mode.currentData()
        self._fill()

    def _groups(self) -> int:
        refs = self.b.shows + ([self.b.movies_ref()] if self.b.movies else [])
        seq = sum(1 for s in refs if s.effective_mode(self.b) == model.SEQUENTIAL)
        return seq + (1 if any(s.effective_mode(self.b) != model.SEQUENTIAL for s in refs) else 0)

    def _fill(self) -> None:
        self.table.setRowCount(0)
        for r, show in enumerate(self.b.shows):
            info = library.lookup(show.folder) or self._scan_cache.get(show.folder)
            if info is None and Path(show.folder).is_dir():
                try:
                    info = self._scan_cache[show.folder] = library.scan_one(show.folder)
                except OSError:
                    info = None
            if not show.title:
                show.title = info.title if info else Path(show.folder).name
            self.table.insertRow(r)
            it = QTableWidgetItem(show.title)
            it.setToolTip(show.folder)
            self.table.setItem(r, 0, it)
            order = self.Combo()
            for label, val in self.ORDER_ITEMS:
                order.addItem("Block default" if val == model.DEFAULT else label, val)
            order.setCurrentIndex(max(0, order.findData(show.mode)))
            order.currentIndexChanged.connect(lambda _i, s=show, w=order: self._show_mode(s, w.currentData()))
            self.table.setCellWidget(r, 1, order)
            seq = show.effective_mode(self.b) == model.SEQUENTIAL
            start = button(self._start_label(show, info, seq), "picker")
            start.setEnabled(seq and info is not None)
            start.setToolTip("Which episode airs first. Applied the next time you build.")
            start.clicked.connect(lambda _c=False, s=show, i=info: self._pick_start(s, i))
            self.table.setCellWidget(r, 2, start)
            rm = button("✕", "flat")
            rm.clicked.connect(lambda _c=False, s=show: self._remove_show(s))
            self.table.setCellWidget(r, 3, rm)
        if self.b.movies:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(f"🎬  Movies  ({len(self.b.movies)} films)"))
            order = self.Combo()
            for label, val in (("Block default", model.DEFAULT), ("In order (by year)", model.SEQUENTIAL),
                               ("Shuffle", model.SHUFFLE)):
                order.addItem(label, val)
            order.setCurrentIndex(max(0, order.findData(self.b.movie_mode)))
            order.currentIndexChanged.connect(lambda _i, w=order: self._movie_mode(w.currentData()))
            self.table.setCellWidget(r, 1, order)
            pick = button("▸  Choose films…", "picker")
            pick.clicked.connect(self._pick_movies)
            self.table.setCellWidget(r, 2, pick)
            rm = button("✕", "flat")
            rm.clicked.connect(self._clear_movies)
            self.table.setCellWidget(r, 3, rm)
        self.empty.setVisible(not self.b.has_content)
        many = len(self.b.shows) + (1 if self.b.movies else 0) > 1
        self.rotate.setEnabled(many)
        self.rotate_label.setEnabled(many)

    def _start_label(self, show, info, seq: bool) -> str:
        if not seq:
            return "—  (shuffled)"
        if not show.start:
            return "▸  Where it left off / first episode"
        name = info.names[info.codes.index(show.start)] if info and show.start in info.codes else ""
        return f"▸  {show.start}  {name}".rstrip()

    def _pick_start(self, show, info) -> None:
        from .channel_editor import EpisodePicker

        if info is None:
            return
        dlg = EpisodePicker(info, show.start, self)
        if dlg.exec() == QDialog.Accepted:
            show.start = dlg.code
            show.start_pending = True
            self._fill()

    def _show_mode(self, show, mode: str) -> None:
        show.mode = mode
        self._fill()

    def _movie_mode(self, mode: str) -> None:
        self.b.movie_mode = mode
        self._fill()

    def _remove_show(self, show) -> None:
        self.b.shows.remove(show)
        self._fill()

    def _clear_movies(self) -> None:
        self.b.movies = []
        self._fill()

    def _add_shows(self) -> None:
        shows = ensure_library(self)
        if shows is None:
            return
        picker = ShowPicker(shows, {s.folder for s in self.b.shows}, self)
        if picker.exec() != QDialog.Accepted:
            return
        known = {s.folder: s for s in self.ch.all_show_refs()}
        for folder in picker.selected():
            info = library.lookup(folder)
            title = info.title if info else (known[folder].title if folder in known else "")
            self.b.shows.append(model.ShowRef(folder=folder, title=title))
        self._fill()

    def _pick_movies(self) -> None:
        from .dialogs import MoviePicker, ensure_movie_library

        movies = ensure_movie_library(self)
        if movies is None:
            return
        known = {m.path for m in movies}
        extra = [self._scan_cache.get(p) or library.lookup_movie(p) for p in self.b.movies if p not in known]
        picker = MoviePicker(movies + [m for m in extra if m], set(self.b.movies), self, editable=True)
        if picker.exec() == QDialog.Accepted:
            for path, m in picker.extra.items():
                self._scan_cache[path] = m
            self.b.movies = picker.selected()
            self._fill()

    # ---- finish
    def _delete(self) -> None:
        if QMessageBox.question(self, "Delete block", f"Delete the block “{self.b.name}”?") == QMessageBox.Yes:
            self.deleted = True
            self.accept()

    def _save(self) -> None:
        self._read_when()
        self.b.name = self.name.text().strip() or "Time block"
        if not self.b.days:
            self.error.setText("Pick at least one day.")
            self.error.show()
            return
        clash = overlap(self.ch, self.b)
        if clash:
            other, (d, h) = clash
            self.error.setText(f"This overlaps “{other.name}” ({model.DAY_NAMES[d]} {model.hour_text(h)}). "
                               "Change the days or hours, or shorten the other block first.")
            self.error.show()
            return
        self.accept()

    def result_block(self) -> model.Block:
        return self.b


# --------------------------------------------------------------------------- the whole week


class ScheduleDialog(QDialog):
    """Time blocks + broadcast hours for one channel. Edits apply (and save) immediately."""

    def __init__(self, ch: model.Channel, on_change, parent=None):
        super().__init__(parent)
        from .channel_editor import NoWheelCombo

        self.ch, self.on_change = ch, on_change
        self.setWindowTitle(f"Schedule: {ch.number} {ch.name}")
        self.resize(1100, 820)
        lay = QVBoxLayout(self)
        lay.addWidget(theme.ChromeLabel("THE WEEK", 24))
        lay.addWidget(hint("Time blocks take over their hours every week, like “Sat 7–11 AM: cartoons”. "
                           "The rest of the day plays the channel's regular rotation (its main show list). "
                           "Drag across the grid to make a block; click a block to change it."))

        body = QHBoxLayout()
        self.grid = WeekGrid()
        self.grid.block_clicked.connect(self._edit_block)
        self.grid.range_selected.connect(self._new_block)
        body.addWidget(self.grid, 3)

        side = QVBoxLayout()
        side.addWidget(h2("Time blocks"))
        self.blocks = QListWidget()
        self.blocks.itemActivated.connect(lambda it: self._edit_block(it.data(Qt.UserRole)))
        self.blocks.itemClicked.connect(lambda it: self._edit_block(it.data(Qt.UserRole)))
        side.addWidget(self.blocks, 1)
        new = button("+ New block", "primary")
        new.clicked.connect(lambda: self._new_block([5], 7, 11))
        side.addWidget(new)
        side.addSpacing(10)

        # broadcast hours
        side.addWidget(h2("Broadcast hours"))
        self.broadcast = QCheckBox("Sign off at night")
        self.broadcast.setToolTip("Off: the channel is on the air around the clock.")
        side.addWidget(self.broadcast)
        hours = QHBoxLayout()
        hours.addWidget(QLabel("On the air"))
        self.sign_on = NoWheelCombo()
        hour_items(self.sign_on, "12 AM (midnight)")
        hours.addWidget(self.sign_on)
        hours.addWidget(QLabel("to"))
        self.sign_off = NoWheelCombo()
        hour_items(self.sign_off, "12 AM (midnight)")
        hours.addWidget(self.sign_off)
        side.addLayout(hours)
        self.signoff_clip = QCheckBox("Play the “Good night” sign-off first")
        side.addWidget(self.signoff_clip)
        side.addWidget(hint("Time blocks always air, even outside these hours."))
        side.addSpacing(10)

        side.addWidget(h2("When off the air"))
        self.off_air = NoWheelCombo()
        for key, label in model.OFF_AIR_MODES:
            self.off_air.addItem(label, key)
        side.addWidget(self.off_air)
        self.info_btn = button("", "picker")
        self.info_btn.clicked.connect(self._pick_infomercials)
        side.addWidget(self.info_btn)
        self.info_hint = hint("")
        side.addWidget(self.info_hint)
        side.addStretch(1)
        side.addWidget(self._legend())
        sidew = QWidget()
        sidew.setLayout(side)
        sidew.setFixedWidth(330)
        body.addWidget(sidew)
        lay.addLayout(body, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        done = button("Done", "primary")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        lay.addLayout(row)

        self._loading = True
        self.broadcast.setChecked(ch.broadcast)
        self.sign_on.setCurrentIndex(self.sign_on.findData(ch.sign_on))
        self.sign_off.setCurrentIndex(self.sign_off.findData(ch.sign_off))
        self.signoff_clip.setChecked(ch.signoff_clip)
        self.off_air.setCurrentIndex(max(0, self.off_air.findData(ch.off_air)))
        self._loading = False
        for w in (self.broadcast, self.signoff_clip):
            w.toggled.connect(self._settings_changed)
        for w in (self.sign_on, self.sign_off, self.off_air):
            w.currentIndexChanged.connect(self._settings_changed)
        self.grid.set_channel(ch)
        self._refresh()

    def _legend(self) -> QWidget:
        w = QFrame()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for color, text in ((BASE_COLOR, "Regular rotation"), (INFO_COLOR, "Paid programming"),
                            ("#1a1238", "Off the air")):
            r = QHBoxLayout()
            sw = QLabel()
            sw.setFixedSize(16, 16)
            sw.setStyleSheet(f"background: {color}; border: 1px solid #3a2a7a; border-radius: 3px;")
            r.addWidget(sw)
            lab = QLabel(text)
            lab.setObjectName("dim")
            r.addWidget(lab, 1)
            lay.addLayout(r)
        return w

    # ---- refresh
    def _refresh(self) -> None:
        ch = self.ch
        on = ch.broadcast
        for w in (self.sign_on, self.sign_off, self.signoff_clip):
            w.setEnabled(on)
        info = ch.off_air == model.INFOMERCIALS
        self.info_btn.setVisible(info)
        self.info_btn.setText(f"▸  {Path(ch.infomercials).name}" if ch.infomercials else "▸  Choose a folder of videos…")
        self.info_btn.setToolTip(ch.infomercials)
        if info and not ch.infomercials:
            self.info_hint.setText("Pick a folder of infomercials (any videos). Until then, bars + tone are used.")
        elif info:
            self.info_hint.setText("They play in random order with no sign-off, like late-night cable.")
        else:
            self.info_hint.setText("")
        self.info_hint.setVisible(bool(self.info_hint.text()))
        if not ch.has_base_content and ch.blocks:
            self.info_hint.setText((self.info_hint.text() + "\n" if self.info_hint.text() else "")
                                   + "This channel has no regular shows, so it is off the air outside its blocks.")
            self.info_hint.show()

        self.blocks.clear()
        for b in ch.blocks:
            it = QListWidgetItem(f"{b.name}\n{b.when_text}" + ("" if b.has_content else "   (empty)"))
            it.setData(Qt.UserRole, b)
            it.setForeground(QColor(block_color(ch, b)))
            self.blocks.addItem(it)
        if not ch.blocks:
            it = QListWidgetItem("No blocks yet")
            it.setFlags(Qt.NoItemFlags)
            self.blocks.addItem(it)
        self.grid.refresh()

    def _changed(self) -> None:
        self.on_change()
        self._refresh()

    def _settings_changed(self, *_a) -> None:
        if self._loading:
            return
        ch = self.ch
        ch.broadcast = self.broadcast.isChecked()
        ch.sign_on = int(self.sign_on.currentData())
        ch.sign_off = int(self.sign_off.currentData())
        ch.signoff_clip = self.signoff_clip.isChecked()
        ch.off_air = self.off_air.currentData()
        if ch.off_air == model.INFOMERCIALS and not ch.infomercials:
            self._pick_infomercials()
        self._changed()

    def _pick_infomercials(self) -> None:
        d = choose_folder(self, "Folder of infomercials", self.ch.infomercials)
        if d:
            self.ch.infomercials = d
            self._changed()

    # ---- blocks
    def _new_block(self, days: list[int], start: int, end: int) -> None:
        names = {(5,): "Saturday Morning", (6,): "Sunday", (0, 1, 2, 3, 4): "Weeknights"}
        name = names.get(tuple(days), "Time block")
        if tuple(days) == (5,) and start >= 12:
            name = "Saturday Night"
        b = model.Block(id=model.Block.new_id(), name=name, days=list(days), start=start, end=end, mode=self.ch.mode)
        dlg = BlockDialog(self.ch, b, True, self)
        if dlg.exec() == QDialog.Accepted:
            self.ch.blocks.append(dlg.result_block())
            self._changed()

    def _edit_block(self, b) -> None:
        if not isinstance(b, model.Block):
            return
        dlg = BlockDialog(self.ch, b, False, self)
        if dlg.exec() != QDialog.Accepted:
            return
        i = next(k for k, x in enumerate(self.ch.blocks) if x is b)
        if dlg.deleted:
            del self.ch.blocks[i]
        else:
            self.ch.blocks[i] = dlg.result_block()
        self._changed()
