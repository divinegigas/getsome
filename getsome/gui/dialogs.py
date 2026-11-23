"""Dialogs: new-channel templates, show picker, library scan, build progress, settings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from .. import paths  # noqa: E402
from .. import library, model, settings
from . import theme
from .workers import BuildWorker, ScanWorker


def h2(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("h2")
    return lab


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(True)
    return lab


def enter_presses(dialog: QDialog, ok: QPushButton) -> None:
    """Make Enter press `ok` (the "continue" button) and nothing else in this dialog."""
    for b in dialog.findChildren(QPushButton):
        b.setAutoDefault(False)
        b.setDefault(False)
    ok.setAutoDefault(True)
    ok.setDefault(True)


def button(text: str, kind: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    return b


def choose_folder(parent, title: str, start: str = "") -> str | None:
    d = QFileDialog.getExistingDirectory(parent, title, start or str(Path.home()))
    return d or None


# ------------------------------------------------------------------ "is it on a channel?" dots

GREEN, YELLOW = "#7cff4f", "#ffd400"
_dots: dict[str, object] = {}


def _dot(color: str):
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    if color not in _dots:
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(2, 2, 12, 12)
        p.end()
        _dots[color] = QIcon(pm)
    return _dots[color]


def library_usage() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(show folder -> channels, movie file -> channels), e.g. ["5 Toon Zone", "7 Block Test (Saturday cartoons)"]."""
    shows: dict[str, list[str]] = {}
    movies: dict[str, list[str]] = {}

    def add(d: dict, key: str, label: str) -> None:
        if label not in d.setdefault(key, []):
            d[key].append(label)

    for c in model.load():
        name = f"{c.number} {c.name}"
        if c.kind != model.MOVIES:
            for sh in c.shows:
                add(shows, sh.folder, name)
        for m in c.movies:
            add(movies, m, name)
        for b in c.blocks:
            for sh in b.shows:
                add(shows, sh.folder, f"{name} ({b.name})")
            for m in b.movies:
                add(movies, m, f"{name} ({b.name})")
    return shows, movies


# ------------------------------------------------------------------ age ratings (from the NFOs)

# tier -> label in the "All ratings" filter
RATING_TIERS = {
    "family": "Family (G, TV-Y, TV-G)",
    "pg": "PG (PG, TV-PG)",
    "teen": "Teen (PG-13, TV-14)",
    "mature": "Mature (R, TV-MA, NC-17)",
    "none": "No rating",
}
from ..media_index import rating_tier  # noqa: E402  (shared with autopop)


_badges: dict[tuple[str, str], object] = {}
BADGE_SIZE = (82, 20)


def _badge(dot_color: str, rating: str):
    """The usage dot followed by the age rating in plain text, as one icon so the ratings line up
    like a column. Plain on purpose: colour stays the dots' job (on a channel or not)."""
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

    key = (dot_color, rating)
    if key not in _badges:
        w, h = BADGE_SIZE
        pm = QPixmap(w * 2, h * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(dot_color))
        p.drawEllipse(2, 4, 12, 12)
        from PySide6.QtWidgets import QApplication

        f = QFont()
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(QApplication.palette().text().color())
        p.drawText(24, 0, w - 26, h, Qt.AlignLeft | Qt.AlignVCenter, rating or "–")
        p.end()
        _badges[key] = QIcon(pm)
    return _badges[key]


def rating_filter() -> QComboBox:
    box = QComboBox()
    box.addItem("All ratings", None)
    for tier, label in RATING_TIERS.items():
        box.addItem(label, tier)
    box.setToolTip("Age ratings come from your .nfo files (<mpaa>). “–” means the NFO has none.")
    return box


def rating_ok(it: QListWidgetItem, want) -> bool:
    return want is None or it.data(Qt.UserRole + 3) == want


def mark_usage(it: QListWidgetItem, channels: list[str] | None, rating: str = "") -> None:
    """Green dot + channel list on hover if it's on a channel already, yellow dot if not,
    then the age-rating badge."""
    it.setIcon(_badge(GREEN if channels else YELLOW, rating))
    tip = ("Playing on:\n" + "\n".join(f"•  {c}" for c in channels)) if channels else "Not on any channel yet"
    it.setToolTip(f"Rated {rating}\n{tip}" if rating else f"No rating in the NFO\n{tip}")
    it.setData(Qt.UserRole + 2, bool(channels))
    it.setData(Qt.UserRole + 3, rating_tier(rating))


def usage_filter() -> QComboBox:
    box = QComboBox()
    box.addItem("Everything", None)
    box.addItem("● Not on any channel yet", False)
    box.addItem("● Already on a channel", True)
    box.setItemIcon(1, _dot(YELLOW))
    box.setItemIcon(2, _dot(GREEN))
    box.setItemText(1, "Not on any channel yet")
    box.setItemText(2, "Already on a channel")
    box.setToolTip("Green dot: already on a channel (hover to see which). Yellow dot: not on any channel yet.")
    return box


def usage_ok(it: QListWidgetItem, want) -> bool:
    return want is None or bool(it.data(Qt.UserRole + 2)) == want


# ------------------------------------------------------------------ new channel


class NewChannelDialog(QDialog):
    """Pick a style and a number. The name comes at the end (NameChannelDialog), once there's content."""

    def __init__(self, channels: list[model.Channel], parent=None):
        super().__init__(parent)
        self.setWindowTitle("New channel")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.addWidget(theme.ChromeLabel("NEW CHANNEL", 26))
        lay.addWidget(h2("Pick a style"))
        self.group = QButtonGroup(self)
        for i, t in enumerate(model.TEMPLATES):
            b = theme.card_button(t.name, t.blurb)
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.button(0).setChecked(True)

        row = QHBoxLayout()
        row.addWidget(h2("Channel number"))
        self.number = QSpinBox()
        self.number.setRange(1, 999)
        self.number.setValue(model.next_free_number(channels))
        row.addWidget(self.number)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(hint("You'll name the channel at the end, after picking what's on it."))
        self.error = QLabel("")
        self.error.setObjectName("warn")
        lay.addWidget(self.error)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = button("Next: pick content", "primary")
        ok.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self.channels = channels
        enter_presses(self, ok)
        self.number.setFocus()

    def _accept(self):
        if any(c.number == self.number.value() for c in self.channels):
            self.error.setText(f"Channel {self.number.value()} is taken.")
            return
        if self.number.value() == model.guide_number():
            self.error.setText(f"Channel {self.number.value()} is the TV guide. Pick another number.")
            return
        self.accept()

    def result_channel(self) -> model.Channel:
        t = model.TEMPLATES[self.group.checkedId()]
        name, k = f"Channel {self.number.value()}", 2          # placeholder until NameChannelDialog
        while model.find(self.channels, name):
            name, k = f"Channel {self.number.value()} ({k})", k + 1
        return model.Channel(number=self.number.value(), name=name,
                             template=t.key, mode=t.mode, rotate_hours=t.rotate_hours,
                             kind=t.kind, start_mode=t.start_mode)


class NameChannelDialog(QDialog):
    """Last step of a new channel: its name, suggested from the first show or film added."""

    def __init__(self, ch: model.Channel, channels: list[model.Channel], suggestion: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Name your channel")
        self.setMinimumWidth(480)
        self.ch, self.channels = ch, channels
        base = suggestion or ch.name
        self.suggestion, k = base, 2                 # never suggest a name that's already taken
        while model.find(channels, self.suggestion) not in (None, ch):
            self.suggestion, k = f"{base} {k}", k + 1
        lay = QVBoxLayout(self)
        lay.addWidget(h2(f"Channel {ch.number}: name"))
        self.name = QLineEdit(self.suggestion)
        self.name.setPlaceholderText(self.suggestion)
        self.name.selectAll()                 # type to replace it, or just press Enter to keep it
        lay.addWidget(self.name)
        lay.addWidget(hint("Leave it as it is, or empty, to name it after the first show or film you added."))
        self.error = QLabel("")
        self.error.setObjectName("warn")
        lay.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = button("Done", "primary")
        ok.clicked.connect(self._accept)
        row.addWidget(ok)
        lay.addLayout(row)
        enter_presses(self, ok)
        self.name.setFocus()

    def result_name(self) -> str:
        return self.name.text().strip() or self.suggestion

    def _accept(self):
        name = self.result_name()
        other = model.find(self.channels, name)
        if other is not None and other is not self.ch:
            self.error.setText(f'There is already a channel called "{name}". Pick another name.')
            return
        self.accept()


# ------------------------------------------------------------------ library scan


class ScanDialog(QDialog):
    def __init__(self, parent=None, movies: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Scanning library")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.addWidget(h2("Reading your movie library" if movies else "Reading your TV library"))
        self.label = hint("Looking for movies..." if movies else "Looking for shows...")
        lay.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        lay.addWidget(self.bar)
        lay.addWidget(hint("Reads each show's .nfo files. Only needed when you add shows to your library."))
        self.shows: list = []
        self.worker = ScanWorker(movies)
        self.worker.progress.connect(self._progress)
        self.worker.finished_ok.connect(self._done)
        self.worker.failed.connect(self._failed)
        QTimer.singleShot(0, self.worker.start)

    def _progress(self, i, n, name):
        self.bar.setRange(0, max(1, n))
        self.bar.setValue(i)
        if name:
            self.label.setText(f"{i + 1} / {n}   {name}")

    def _done(self, shows):
        self.shows = shows
        self.accept()

    def _failed(self, err):
        QMessageBox.warning(self, "Scan failed", err)
        self.reject()

    def closeEvent(self, ev):
        if self.worker.isRunning():
            ev.ignore()
        else:
            super().closeEvent(ev)


def ensure_library(parent) -> list[library.LibShow] | None:
    """Make sure there is a library folder and a scan of it; returns the shows."""
    if not library.folders():
        QMessageBox.information(parent, "Where are your TV shows?",
                                "Pick the folder that holds your show folders "
                                "(for example  …/Media/TV shows).")
        d = choose_folder(parent, "Choose your TV shows folder")
        if not d:
            return None
        library.set_folders([d])
    shows = library.cached()
    if shows is None:
        dlg = ScanDialog(parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        shows = dlg.shows
    return shows


def ensure_movie_library(parent) -> list[library.LibMovie] | None:
    if not library.movie_folders():
        QMessageBox.information(parent, "Where are your movies?",
                                "Pick the folder that holds your movies (for example  …/Media/Movies).")
        d = choose_folder(parent, "Choose your movies folder")
        if not d:
            return None
        library.set_movie_folders([d])
    movies = library.cached_movies()
    if movies is None:
        dlg = ScanDialog(parent, movies=True)
        if dlg.exec() != QDialog.Accepted:
            return None
        movies = dlg.shows
    return movies


class MoviePicker(QDialog):
    """Pick movies by hand, or filter by genre / decade and tick everything shown."""

    def __init__(self, movies: list[library.LibMovie], already: set[str], parent=None, editable: bool = False):
        """editable: films already on the channel can be unticked (selected() is then the full list)."""
        super().__init__(parent)
        self.editable = editable
        self.setWindowTitle("Choose films" if editable else "Add movies")
        self.resize(760, 720)
        self.extra: dict[str, library.LibMovie] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(theme.ChromeLabel("ADD MOVIES", 24))
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search titles…")
        row.addWidget(self.search, 1)
        self.genre = QComboBox()
        self.genre.addItem("All genres", None)
        for g in sorted({g for m in movies for g in m.genres}):
            self.genre.addItem(g, g)
        row.addWidget(self.genre)
        self.decade = QComboBox()
        self.decade.addItem("All years", None)
        for d in sorted({(m.year // 10) * 10 for m in movies if m.year}):
            self.decade.addItem(f"{d}s", d)
        row.addWidget(self.decade)
        self.used = usage_filter()
        self.used.currentIndexChanged.connect(self._filter)
        row.addWidget(self.used)
        self.rated = rating_filter()
        self.rated.currentIndexChanged.connect(self._filter)
        row.addWidget(self.rated)
        lay.addLayout(row)
        self.usage = library_usage()[1]
        self.list = QListWidget()
        self.list.setIconSize(QSize(*BADGE_SIZE))
        for m in movies:
            self._add_item(m, m.path in already)
        self.list.itemClicked.connect(self._toggle)
        self.list.itemChanged.connect(lambda _it: self._count())
        lay.addWidget(self.list, 1)
        self.count = hint("")
        lay.addWidget(self.count)
        for w_ in (self.search,):
            w_.textChanged.connect(self._filter)
        self.genre.currentIndexChanged.connect(self._filter)
        self.decade.currentIndexChanged.connect(self._filter)

        row = QHBoxLayout()
        all_btn = button("Tick all shown")
        all_btn.setToolTip("Tick every movie matching the search and filters, e.g. all 1980s Sci-Fi")
        all_btn.clicked.connect(lambda: self._set_shown(True))
        none_btn = button("Untick shown")
        none_btn.clicked.connect(lambda: self._set_shown(False))
        rescan = button("Rescan")
        rescan.clicked.connect(self._rescan)
        browse = button("Add any folder…")
        browse.setToolTip("Every video in a folder becomes a movie. No NFO files needed.")
        browse.clicked.connect(self._browse)
        for b in (all_btn, none_btn, rescan, browse):
            row.addWidget(b)
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = button("Done" if editable else "Add selected", "primary")
        ok.clicked.connect(self.accept)
        self._ok = ok
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self._count()
        enter_presses(self, self._ok)

    def _add_item(self, m: library.LibMovie, on_channel: bool = False, checked: bool = False, top: bool = False):
        bits = [m.label]
        if m.genres:
            bits.append(" / ".join(m.genres[:3]))
        if m.length_text:
            bits.append(m.length_text)
        it = QListWidgetItem("      ".join(bits) + ("   (on this channel)" if on_channel and not self.editable else ""))
        it.setData(Qt.UserRole, m.path)
        it.setData(Qt.UserRole + 1, (m.title.lower(), set(m.genres), m.year))
        mark_usage(it, self.usage.get(m.path), m.rating)
        if on_channel and self.editable:
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
        elif on_channel:
            it.setFlags(Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked)
        else:
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        if top:
            self.list.insertItem(0, it)
        else:
            self.list.addItem(it)

    def _toggle(self, it: QListWidgetItem):
        if it.flags() & Qt.ItemIsUserCheckable:
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _filter(self, *_):
        t = self.search.text().lower().strip()
        g, d = self.genre.currentData(), self.decade.currentData()
        for i in range(self.list.count()):
            it = self.list.item(i)
            title, genres, year = it.data(Qt.UserRole + 1)
            ok = (not t or t in title) and (g is None or g in genres) and \
                (d is None or (year is not None and d <= year < d + 10)) and usage_ok(it, self.used.currentData()) \
                and rating_ok(it, self.rated.currentData())
            it.setHidden(not ok)

    def _set_shown(self, on: bool):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not it.isHidden() and it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _count(self):
        n = len(self.selected())
        self.count.setText(f"{n} selected" if n else "Tick movies, or filter by genre/decade and use “Tick all shown”.")

    def _rescan(self):
        dlg = ScanDialog(self, movies=True)
        if dlg.exec() == QDialog.Accepted:
            QMessageBox.information(self, "Movies rescanned",
                                    f"Found {len(dlg.shows)} movies. Reopen “Add movies” to see them.")

    def _browse(self):
        from .. import media_index

        d = choose_folder(self, "Choose a folder of videos")
        if not d:
            return
        for mv in reversed(media_index.scan_movies(Path(d))):
            lm = library.movie_from_scan(mv)
            self.extra[lm.path] = lm
            self._add_item(lm, checked=True, top=True)
        self._count()

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).flags() & Qt.ItemIsUserCheckable and self.list.item(i).checkState() == Qt.Checked]


# ------------------------------------------------------------------ show picker


class ShowPicker(QDialog):
    """Pick shows by hand, or filter by genre and tick everything shown."""

    def __init__(self, shows: list[library.LibShow], already: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add shows")
        self.resize(760, 720)
        self.all_shows = shows
        lay = QVBoxLayout(self)
        lay.addWidget(theme.ChromeLabel("ADD SHOWS", 24))
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search shows…")
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search, 1)
        self.genre = QComboBox()
        self.genre.addItem("All genres", None)
        for g in sorted({g for sh in shows for g in sh.genres}):
            self.genre.addItem(g, g)
        self.genre.currentIndexChanged.connect(self._filter)
        if self.genre.count() == 1:
            self.genre.setToolTip("No genres yet - press Rescan to read them from your tvshow.nfo files")
        row.addWidget(self.genre)
        self.used = usage_filter()
        self.used.currentIndexChanged.connect(self._filter)
        row.addWidget(self.used)
        self.rated = rating_filter()
        self.rated.currentIndexChanged.connect(self._filter)
        row.addWidget(self.rated)
        lay.addLayout(row)
        self.usage = library_usage()[0]
        self.list = QListWidget()
        self.list.setIconSize(QSize(*BADGE_SIZE))
        for sh in shows:
            self._add_item(sh.folder, sh.title, f"{sh.episodes} episodes · season {sh.season_text}",
                           sh.genres, on_channel=sh.folder in already, rating=sh.rating)
        self.list.itemClicked.connect(self._toggle)
        self.list.itemChanged.connect(lambda _it: self._count())
        lay.addWidget(self.list, 1)
        self.count = hint("")
        lay.addWidget(self.count)
        lay.addWidget(hint("Not in the list? “Add any folder…” takes any folder of videos: home movies, "
                           "old tapes, a folder of commercials, or a whole folder of shows. No NFO files needed."))

        row = QHBoxLayout()
        all_btn = button("Tick all shown")
        all_btn.setToolTip("Tick every show matching the search and genre, e.g. all Animation")
        all_btn.clicked.connect(lambda: self._set_shown(True))
        none_btn = button("Untick shown")
        none_btn.clicked.connect(lambda: self._set_shown(False))
        rescan = button("Rescan")
        rescan.setToolTip("Look for new shows and episodes in your library folders")
        rescan.clicked.connect(self._rescan)
        browse = button("Add any folder…")
        browse.setToolTip("Any folder of videos works, no NFO files needed. Files play in name order "
                          "(or by S01E02 numbers if the names have them).")
        browse.clicked.connect(self._browse)
        for b_ in (all_btn, none_btn, rescan, browse):
            row.addWidget(b_)
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = button("Add selected", "primary")
        ok.clicked.connect(self.accept)
        self._ok = ok
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self._count()
        self.search.setFocus()
        enter_presses(self, self._ok)

    def _add_item(self, folder: str, title: str, detail: str, genres: list[str], on_channel=False,
                  checked=False, top=False, rating: str = ""):
        bits = [title, detail] + ([" / ".join(genres[:3])] if genres else [])
        it = QListWidgetItem("      ".join(bits) + ("   (on this channel)" if on_channel else ""))
        it.setData(Qt.UserRole, folder)
        it.setData(Qt.UserRole + 1, (title.lower(), set(genres)))
        mark_usage(it, self.usage.get(folder), rating)
        if on_channel:
            it.setFlags(Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked)
        else:
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        if top:
            self.list.insertItem(0, it)
        else:
            self.list.addItem(it)

    def _toggle(self, it: QListWidgetItem):
        if it.flags() & Qt.ItemIsUserCheckable:
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _filter(self, *_):
        t = self.search.text().lower().strip()
        g = self.genre.currentData()
        for i in range(self.list.count()):
            it = self.list.item(i)
            title, genres = it.data(Qt.UserRole + 1)
            it.setHidden(not ((not t or t in title) and (g is None or g in genres)
                              and usage_ok(it, self.used.currentData())
                              and rating_ok(it, self.rated.currentData())))

    def _set_shown(self, on: bool):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if not it.isHidden() and it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _count(self):
        n = len(self.selected())
        self.count.setText(f"{n} selected" if n else "Tick shows, or filter by genre and use “Tick all shown”.")

    def _rescan(self):
        dlg = ScanDialog(self)
        if dlg.exec() == QDialog.Accepted:
            QMessageBox.information(self, "Library rescanned",
                                    f"Found {len(dlg.shows)} shows. Reopen “Add shows” to see them.")

    def _browse(self):
        from .. import media_index

        d = choose_folder(self, "Choose a show folder (or a folder of shows)")
        if not d:
            return
        shows = media_index.looks_like_library(Path(d))
        if shows:
            r = QMessageBox.question(
                self, "A folder of shows",
                f"“{Path(d).name}” holds {len(shows)} shows.\n\n"
                "Yes: add them as separate shows (ticked, untick any you don't want).\n"
                "No: treat the whole folder as one show.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                known = {self.list.item(i).data(Qt.UserRole): self.list.item(i) for i in range(self.list.count())}
                for sd in reversed(shows):
                    if str(sd) in known:
                        it = known[str(sd)]
                        if it.flags() & Qt.ItemIsUserCheckable:
                            it.setCheckState(Qt.Checked)
                        continue
                    self._add_item(str(sd), media_index.show_title(sd), "(folder)",
                                   media_index.show_genres(sd), checked=True, top=True,
                                   rating=media_index.show_rating(sd))
                self._count()
                return
        self._add_item(d, Path(d).name, "(folder)", [], checked=True, top=True,
                       rating=media_index.show_rating(Path(d)))
        self._count()

    def selected(self) -> list[str]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.flags() & Qt.ItemIsUserCheckable and it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole))
        return out


# ------------------------------------------------------------------ build progress


class BuildDialog(QDialog):
    def __init__(self, channels: list[model.Channel], all_channels: list[model.Channel], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Building channels")
        self.resize(640, 220)
        lay = QVBoxLayout(self)
        lay.addWidget(theme.ChromeLabel("BUILDING", 24))
        self.chan = h2("")
        lay.addWidget(self.chan)
        self.stepl = hint("Starting…")
        lay.addWidget(self.stepl)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        lay.addWidget(self.bar)
        self.details_btn = button("Show details", "flat")
        self.details_btn.clicked.connect(self._toggle_details)
        lay.addWidget(self.details_btn, 0, Qt.AlignLeft)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log.hide()
        lay.addWidget(self.log, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = button("Close", "primary")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        row.addWidget(self.close_btn)
        lay.addLayout(row)
        self.failures: list = []
        self.worker = BuildWorker(channels, all_channels)
        self.worker.line.connect(self._line)
        self.worker.step.connect(lambda s: self.stepl.setText(s.split("] ", 1)[-1]))
        self.worker.channel_started.connect(self._chan)
        self.worker.done.connect(self._done)
        QTimer.singleShot(0, self.worker.start)

    def _toggle_details(self):
        show = self.log.isHidden()
        self.log.setVisible(show)
        self.details_btn.setText("Hide details" if show else "Show details")
        if show:
            self.resize(self.width(), max(self.height(), 520))

    def _line(self, text: str):
        self.log.appendPlainText(text)

    def _chan(self, i, n, name):
        self.chan.setText(f"CHANNEL {name.upper()}   ({i + 1} OF {n})")

    def _done(self, failures):
        self.failures = failures
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        if failures:
            self.stepl.setText("Some channels could not be built:\n" + "\n".join(f"• {n}: {e}" for n, e in failures))
            self.stepl.setObjectName("warn")
            self.stepl.setStyleSheet(f"color: {theme.SUN};")
            if self.log.isHidden():
                self._toggle_details()
        else:
            self.stepl.setText("All done. Your channels are on the air.")
        self.close_btn.setEnabled(True)

    def closeEvent(self, ev):
        if self.worker.isRunning():
            ev.ignore()
        else:
            super().closeEvent(ev)


# ------------------------------------------------------------------ settings


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.s = settings.load()
        from PySide6.QtWidgets import QScrollArea

        outer = QVBoxLayout(self)
        outer.addWidget(theme.ChromeLabel("SETTINGS", 26))
        # the settings outgrew a 1080p screen: they scroll, Save/Cancel stay put
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("settingsScroll")
        scroll.setStyleSheet("QScrollArea#settingsScroll, QWidget#settingsBody { background: transparent; }")
        scroll.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("settingsBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 12, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.outer = outer

        lay.addWidget(h2("Shortcuts"))
        row = QHBoxLayout()
        self.menu_label = QLabel("")
        self.menu_label.setObjectName("dim")
        self.menu_label.setWordWrap(True)
        row.addWidget(self.menu_label, 1)
        self.menu_btn = button("")
        self.menu_btn.clicked.connect(self._toggle_menu)
        row.addWidget(self.menu_btn)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.desk_label = QLabel("")
        self.desk_label.setObjectName("dim")
        self.desk_label.setWordWrap(True)
        row.addWidget(self.desk_label, 1)
        self.desk_btn = button("")
        self.desk_btn.clicked.connect(self._toggle_desktop)
        row.addWidget(self.desk_btn)
        lay.addLayout(row)
        row = QHBoxLayout()
        steam_label = QLabel(f"Steam: adds {paths.APP_NAME} to your Steam library with its own artwork, starting "
                             "straight in TV mode (great for Big Picture). Close Steam first.")
        steam_label.setObjectName("dim")
        steam_label.setWordWrap(True)
        row.addWidget(steam_label, 1)
        steam_btn = button("Add to Steam")
        steam_btn.clicked.connect(self._add_to_steam)
        row.addWidget(steam_btn)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("When Steam starts it"))
        self.steam_window = QComboBox()
        self.steam_window.addItem("Normal window (every remote button works)", "wayland")
        self.steam_window.addItem("Compatibility window (only if Big Picture shows no picture)", "x11")
        self.steam_window.setCurrentIndex(max(0, self.steam_window.findData(self.s.get("steam_window", "wayland"))))
        self.steam_window.setToolTip("The compatibility window can't receive some remote buttons, such as OK.\n"
                                     "Use Enter on a keyboard instead if you need it.")
        row.addWidget(self.steam_window, 1)
        lay.addLayout(row)
        self._menu_ui()

        lay.addWidget(h2("TV library folders"))
        self.folders = QListWidget()
        self.folders.addItems(self.s.get("library_folders", []))
        self.folders.setMaximumHeight(110)
        self.folders.setTextElideMode(Qt.ElideMiddle)
        self.folders.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.folders)
        row = QHBoxLayout()
        add = button("Add folder…")
        add.clicked.connect(self._add_folder)
        rm = button("Remove")
        rm.clicked.connect(lambda: [self.folders.takeItem(self.folders.row(i)) for i in self.folders.selectedItems()])
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(hint("Folders that contain your show folders, e.g. “…/Media/TV shows”."))

        lay.addWidget(h2("Movie library folders"))
        self.mfolders = QListWidget()
        self.mfolders.addItems(self.s.get("movie_folders", []))
        self.mfolders.setMaximumHeight(80)
        self.mfolders.setTextElideMode(Qt.ElideMiddle)
        self.mfolders.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.mfolders)
        row = QHBoxLayout()
        addm = button("Add folder…")
        addm.clicked.connect(lambda: self._add_to(self.mfolders, "Choose a movies folder"))
        rmm = button("Remove")
        rmm.clicked.connect(lambda: [self.mfolders.takeItem(self.mfolders.row(i)) for i in self.mfolders.selectedItems()])
        row.addWidget(addm)
        row.addWidget(rmm)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(h2("Starting up"))
        self.launch_tv = QCheckBox("Launch straight into TV mode (skip this setup window)")
        self.launch_tv.setChecked(bool(self.s.get("launch_into_tv")))
        lay.addWidget(self.launch_tv)
        lay.addWidget(hint(f"You can always reach setup again with:  ./{paths.LAUNCHER} setup"))
        self.remember = QCheckBox("Start on the last channel watched")
        self.remember.setChecked(bool(self.s.get("remember_last_channel", True)))
        lay.addWidget(self.remember)
        row = QHBoxLayout()
        self.keep_awake = QCheckBox("Keep the computer awake while watching TV")
        self.keep_awake.setChecked(bool(self.s.get("keep_awake", True)))
        row.addWidget(self.keep_awake)
        row.addWidget(QLabel("until no button is pressed for"))
        self.awake_hours = QComboBox()
        for hrs, label in ((1, "1 hour"), (2, "2 hours"), (3, "3 hours"), (4, "4 hours"), (6, "6 hours"),
                           (8, "8 hours"), (0, "ever (never sleep)")):
            self.awake_hours.addItem(label, hrs)
        self.awake_hours.setCurrentIndex(max(0, self.awake_hours.findData(int(self.s.get("keep_awake_hours", 4)))))
        self.keep_awake.toggled.connect(self.awake_hours.setEnabled)
        self.awake_hours.setEnabled(self.keep_awake.isChecked())
        row.addWidget(self.awake_hours)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(h2("TV guide channel"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Guide on channel"))
        self.guide = QSpinBox()
        self.guide.setRange(0, 999)
        self.guide.setSpecialValueText("off")
        self.guide.setValue(int(self.s.get("guide_channel", 2) or 0))
        self.guide.setFixedWidth(110)
        row.addWidget(self.guide)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(hint("Lists what's on every channel. Set to 0 to turn it off."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Guide music"))
        self.gmusic = QComboBox()
        from .. import assets_gen

        for key, name in assets_gen.music_options():
            self.gmusic.addItem(name, key)
        cur = {"fantasy": "crystal"}.get(self.s.get("guide_music", "shuffle"), self.s.get("guide_music", "shuffle"))
        self.gmusic.setCurrentIndex(max(0, self.gmusic.findData(cur)))
        row.addWidget(self.gmusic, 1)
        lay.addLayout(row)
        lay.addWidget(hint("Ten original RPG-style synth & metal tracks; also changeable with MENU on the guide. "
                           "Or drop your own guide_music.mp3 in ~/.local/share/getsome/overrides/"))
        row = QHBoxLayout()
        self.gpreview = QCheckBox("Preview window on the guide, showing")
        self.gpreview.setChecked(bool(self.s.get("guide_preview", True)))
        row.addWidget(self.gpreview)
        self.gpreview_step = QComboBox()
        for n, label in ((1, "every channel"), (2, "every 2nd channel"), (3, "every 3rd channel"),
                         (5, "every 5th channel")):
            self.gpreview_step.addItem(label, n)
        self.gpreview_step.setCurrentIndex(max(0, self.gpreview_step.findData(int(self.s.get("guide_preview_step", 1)))))
        row.addWidget(self.gpreview_step)
        row.addWidget(QLabel("for"))
        self.gpreview_secs = QSpinBox()
        self.gpreview_secs.setRange(3, 60)
        self.gpreview_secs.setSuffix(" s each")
        self.gpreview_secs.setValue(int(self.s.get("guide_preview_seconds", 8)))
        row.addWidget(self.gpreview_secs)
        row.addStretch(1)
        self.gpreview.toggled.connect(self.gpreview_step.setEnabled)
        self.gpreview.toggled.connect(self.gpreview_secs.setEnabled)
        self.gpreview_step.setEnabled(self.gpreview.isChecked())
        self.gpreview_secs.setEnabled(self.gpreview.isChecked())
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Guide sound"))
        self.gaudio = QComboBox()
        self.gaudio.addItem("Guide music", "music")
        self.gaudio.addItem("The preview channel's sound", "preview")
        self.gaudio.setCurrentIndex(max(0, self.gaudio.findData(self.s.get("guide_audio", "music"))))
        row.addWidget(self.gaudio, 1)
        self.gpreview.toggled.connect(self.gaudio.setEnabled)
        self.gaudio.setEnabled(self.gpreview.isChecked())
        lay.addLayout(row)
        lay.addWidget(hint("Also switchable with MENU while you're on the guide."))

        lay.addWidget(h2("Commercial breaks"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Fake product ads"))
        self.products = QComboBox()
        for key, label in (("few", "A few (mostly show promos, trivia and lineups)"),
                           ("lots", "Lots (all 14 products)"), ("none", "None (only show cards)")):
            self.products.addItem(label, key)
        self.products.setCurrentIndex(max(0, self.products.findData(self.s.get("ads_products", "few"))))
        row.addWidget(self.products, 1)
        lay.addLayout(row)
        lay.addWidget(hint("For channels with “Commercial breaks” ticked. Rebuild after changing this (Maintenance → Rebuild all channels, below)."))

        lay.addWidget(h2("Restricted channels"))
        row = QHBoxLayout()
        row.addWidget(QLabel("PIN"))
        self.pin = QLineEdit(str(self.s.get("restricted_pin") or "69"))
        from PySide6.QtGui import QRegularExpressionValidator
        from PySide6.QtCore import QRegularExpression

        self.pin.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{1,12}"), self.pin))
        self.pin.setMaxLength(12)
        self.pin.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.pin.setMaximumWidth(160)
        row.addWidget(self.pin)
        row.addSpacing(20)
        row.addWidget(QLabel("Lock again after"))
        self.relock = QComboBox()
        for m, label in ((30, "30 minutes"), (60, "1 hour"), (120, "2 hours"), (180, "3 hours"),
                         (240, "4 hours"), (0, "never (until TV mode closes)")):
            self.relock.addItem(label, m)
        self.relock.setCurrentIndex(max(0, self.relock.findData(int(self.s.get("relock_minutes", 120)))))
        row.addWidget(self.relock)
        row.addWidget(QLabel("away from them"))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(hint("Channels ticked “Restricted” are blurred and silent in TV mode until this PIN is "
                           "typed on the remote (press OK on the channel). Watching a restricted channel keeps them open; they lock again quietly after the time above spent on "
                           "other channels, and always when TV mode closes."))

        lay.addWidget(h2("Picture & sound"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Video decoding"))
        self.hwdec = QComboBox()
        self.hwdec.addItem("Software (safest)", "no")
        self.hwdec.addItem("Graphics card (less CPU, try it on AMD/Intel)", "auto-safe")
        self.hwdec.setCurrentIndex(0 if self.s.get("hwdec", "no") == "no" else 1)
        row.addWidget(self.hwdec, 1)
        lay.addLayout(row)
        self.volume = self._slider(lay, "TV volume", int(self.s.get("volume", 100)), 130)
        self.hiss = self._slider(lay, "Static hiss volume", int(self.s.get("hiss_volume", 35)), 100)

        from .. import updater

        from .network_dialogs import add_network_section, add_remote_section

        add_network_section(self, lay)
        add_remote_section(self, lay)

        lay.addWidget(h2("Updates"))
        row = QHBoxLayout()
        self.update_label = QLabel(f"Version {updater.version()}" if updater.available()
                                   else f"This copy of {paths.APP_NAME} can't update itself.")
        self.update_label.setObjectName("dim")
        self.update_label.setWordWrap(True)
        row.addWidget(self.update_label, 1)
        self.update_btn = button("Check for updates")
        self.update_btn.setEnabled(updater.available())
        self.update_btn.clicked.connect(self._check_updates)
        row.addWidget(self.update_btn)
        lay.addLayout(row)
        lay.addWidget(hint(f"Downloads the newest {paths.APP_NAME} and restarts. Your channels, settings and media are kept."))

        if hasattr(parent, "cleanup"):
            lay.addWidget(h2("Maintenance"))
            row = QHBoxLayout()
            row.addWidget(QLabel("Broken, leftover, empty or duplicate channels"), 1)
            tidy = button("Clean up channels…")
            tidy.clicked.connect(parent.cleanup)
            row.addWidget(tidy)
            lay.addLayout(row)
            row = QHBoxLayout()
            row.addWidget(QLabel("Rebuild every channel (after an update or a commercials change)"), 1)
            rb = button("Rebuild all channels…")
            rb.clicked.connect(lambda: (self._save_settings(), parent.rebuild_all()))
            row.addWidget(rb)
            lay.addLayout(row)
            row = QHBoxLayout()
            row.addWidget(QLabel("Start fresh: delete every channel (your media and settings are kept)"), 1)
            ra = button("Remove all channels…", "danger")
            ra.clicked.connect(lambda: (self._save_settings(), self.accept(), parent.remove_all()))
            row.addWidget(ra)
            lay.addLayout(row)

        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        ok = button("Save", "primary")
        ok.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(ok)
        outer.addLayout(row)
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen else 900
        self.resize(max(680, body.sizeHint().width() + 60), min(body.sizeHint().height() + 140, int(avail * 0.9)))

    # ---------------------------------------------------------------- app menu
    def _menu_ui(self) -> None:
        from .. import desktop

        on = desktop.installed()
        self.menu_label.setText(f"{paths.APP_NAME} is in your app menu (Sound & Video / Multimedia). "
                                "Right-click it for Watch TV." if on else
                                f"Put {paths.APP_NAME} in your app menu, under Sound & Video / Multimedia.")
        self.menu_btn.setText("Remove from app menu" if on else "Add to app menu")
        d = desktop.desktop_shortcut_installed()
        self.desk_label.setText(f"There's a {paths.APP_NAME} shortcut on your desktop." if d else
                                "A double-click shortcut on your desktop (shows on KDE; GNOME hides desktop icons).")
        self.desk_btn.setText("Remove desktop shortcut" if d else "Add desktop shortcut")

    def _toggle_menu(self) -> None:
        from .. import desktop

        try:
            if desktop.installed():
                desktop.remove()
            else:
                desktop.install()
                QMessageBox.information(self, "App menu", "Added! Look under Sound & Video (GNOME) or Multimedia "
                                        f"(KDE), or just search for “{paths.APP_NAME}”.\n\nIf it doesn't show up "
                                        "straight away, log out and back in once.")
        except Exception as e:
            QMessageBox.warning(self, "App menu", f"Couldn't change the app menu: {e}")
        self._menu_ui()

    def _toggle_desktop(self) -> None:
        from .. import desktop

        try:
            if desktop.desktop_shortcut_installed():
                desktop.remove_desktop_shortcut()
            else:
                desktop.install_desktop_shortcut()
        except Exception as e:
            QMessageBox.warning(self, "Desktop shortcut", f"Couldn't make the shortcut: {e}")
        self._menu_ui()

    def _add_to_steam(self) -> None:
        from PySide6.QtGui import QGuiApplication

        from .. import steam_shortcut

        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            msg = steam_shortcut.add_to_steam()
        except steam_shortcut.SteamError as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.information(self, "Add to Steam", str(e))
            return
        except Exception as e:      # unexpected: say so plainly, nothing half-written (atomic replace)
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Add to Steam", f"Couldn't add it to Steam: {e}")
            return
        QGuiApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Add to Steam", msg)

    # ---------------------------------------------------------------- updates
    def _check_updates(self) -> None:
        from .workers import UpdateWorker

        self.update_btn.setEnabled(False)
        self.update_label.setText("Checking for updates…")
        self._upd = UpdateWorker("check")
        self._upd.done.connect(self._update_checked)
        self._upd.failed.connect(self._update_failed)
        self._upd.start()

    def _update_failed(self, why: str) -> None:
        self.update_btn.setEnabled(True)
        self.update_label.setText(f"Couldn't update: {why}")

    def _update_checked(self, res) -> None:
        from .. import updater

        self.update_btn.setEnabled(True)
        if not res.behind:
            self.update_label.setText(f"✔ You have the latest version ({updater.version()}).")
            return
        if res.blocked:
            self.update_label.setText(f"{res.behind} update(s) available, but {res.blocked}. "
                                      "Ask for help updating by hand.")
            return
        self.update_label.setText(f"{res.behind} update(s) available.")
        shown = res.changes[:12]
        more = f"\n…and {len(res.changes) - len(shown)} more" if len(res.changes) > len(shown) else ""
        main = self.parent()
        tv_on = getattr(main, "tv_proc", None) is not None and main.tv_proc.poll() is None
        text = ("What's new:\n\n" + "\n".join(f"•  {c}" for c in shown) + more +
                ("\n\nThis update also clears out the old version history (on GitHub and on this computer). "
                 "Your channels, settings and media are not affected." if getattr(res, "rewritten", False) else "")
                + f"\n\nInstall now? {paths.APP_NAME} will restart (the first start after an update can take a minute)."
                + ("\n\nTV mode is open and will be closed." if tv_on else ""))
        if QMessageBox.question(self, f"Update {paths.APP_NAME}", text) != QMessageBox.Yes:
            return
        if tv_on:
            main.tv_proc.terminate()
        self._save_settings()
        from .workers import UpdateWorker

        self.update_btn.setEnabled(False)
        self.update_label.setText("Installing the update…")
        self._upd = UpdateWorker("apply")
        self._upd.done.connect(lambda _r: self._restart())
        self._upd.failed.connect(self._update_failed)
        self._upd.start()

    def _restart(self) -> None:
        from .. import updater

        self.update_label.setText("Restarting…")
        try:
            if hasattr(self.parent(), "channels"):
                model.save(self.parent().channels)
        except Exception:
            pass
        updater.restart(["setup"])

    def _slider(self, lay, label: str, value: int, maximum: int) -> QSlider:
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setMinimumWidth(150)
        row.addWidget(lab)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, maximum)
        sl.setValue(value)
        val = QLabel(str(value))
        val.setMinimumWidth(32)
        sl.valueChanged.connect(lambda v: val.setText(str(v)))
        row.addWidget(sl, 1)
        row.addWidget(val)
        lay.addLayout(row)
        return sl

    def _add_folder(self):
        self._add_to(self.folders, "Choose a TV shows folder")

    def _add_to(self, lst: QListWidget, title: str):
        d = choose_folder(self, title)
        if d and not lst.findItems(d, Qt.MatchExactly):
            lst.addItem(d)

    def _save(self):
        self._save_settings()
        self.accept()

    def _save_settings(self):
        self.s["library_folders"] = [self.folders.item(i).text() for i in range(self.folders.count())]
        self.s["movie_folders"] = [self.mfolders.item(i).text() for i in range(self.mfolders.count())]
        self.s["launch_into_tv"] = self.launch_tv.isChecked()
        self.s["remember_last_channel"] = self.remember.isChecked()
        self.s["keep_awake"] = self.keep_awake.isChecked()
        self.s["keep_awake_hours"] = int(self.awake_hours.currentData())
        self.s["hwdec"] = self.hwdec.currentData()
        self.s["volume"] = self.volume.value()
        self.s["hiss_volume"] = self.hiss.value()
        self.s["guide_channel"] = self.guide.value()
        self.s["guide_music"] = self.gmusic.currentData()
        self.s["guide_preview"] = self.gpreview.isChecked()
        self.s["guide_audio"] = self.gaudio.currentData()
        self.s["ads_products"] = self.products.currentData()
        self.s["restricted_pin"] = self.pin.text().strip() or "69"
        self.s["relock_minutes"] = int(self.relock.currentData())
        self.s["steam_window"] = self.steam_window.currentData()
        self.s["guide_preview_step"] = int(self.gpreview_step.currentData())
        self.s["guide_preview_seconds"] = int(self.gpreview_secs.value())
        settings.save(self.s)


# ------------------------------------------------------------------ clean up


class CleanupDialog(QDialog):
    """Find broken, leftover, empty or clashing channels and fix the ticked ones."""

    def __init__(self, channels: list[model.Channel], parent=None):
        from .. import cleanup

        super().__init__(parent)
        self.channels = channels
        self.setWindowTitle("Clean up channels")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        lay.addWidget(theme.ChromeLabel("CLEAN UP", 26))
        self.issues = cleanup.find_issues(channels)
        self.boxes: list[tuple[QCheckBox, object]] = []
        if not self.issues:
            lay.addWidget(QLabel("✔  Everything looks tidy: no broken, leftover, empty or clashing channels."))
        else:
            lay.addWidget(hint("Untick anything you want to keep as it is. Your shows and films are never touched."))
            for it in self.issues:
                cb = QCheckBox(it.text)
                cb.setChecked(it.checked)
                cb.setStyleSheet("QCheckBox { padding: 6px 0; }")
                lay.addWidget(cb)
                self.boxes.append((cb, it))
        self.log = QLabel("")
        self.log.setObjectName("dim")
        self.log.setWordWrap(True)
        lay.addWidget(self.log)
        row = QHBoxLayout()
        row.addStretch(1)
        close = button("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.go = button("Clean up selected", "primary")
        self.go.setVisible(bool(self.issues))
        self.go.clicked.connect(self._fix)
        row.addWidget(self.go)
        lay.addLayout(row)
        self.changed = False

    def _fix(self) -> None:
        import threading

        from PySide6.QtCore import QObject, Signal

        from .. import cleanup

        for cb, it in self.boxes:
            it.checked = cb.isChecked()
            cb.setEnabled(False)
        self.go.setEnabled(False)
        self.log.setText("Cleaning up…")
        lines: list[str] = []

        class Bridge(QObject):
            finished = Signal()

        self._bridge = Bridge()
        self._bridge.finished.connect(lambda: self._done(lines))

        def work():
            try:
                from .. import upkeep

                with upkeep.schedule_lock(wait=600) as _got:
                    cleanup.fix(self.issues, self.channels, lines.append)
            except Exception as e:
                lines.append(f"Something went wrong: {e}")
            self._bridge.finished.emit()

        threading.Thread(target=work, daemon=True).start()

    def _done(self, lines: list[str]) -> None:
        self.changed = True
        self.log.setText("Done.\n" + "\n".join("•  " + ln for ln in lines))
