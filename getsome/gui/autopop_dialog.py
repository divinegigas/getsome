"""Auto-create channels: pick library folders and how many channels, get a suggested lineup
(autopop.py), edit it (rename, renumber, change style, untick or drag titles between
channels), then create, and optionally build, the channels. Existing channels are kept."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox, QSpinBox, QStackedWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import paths  # noqa: E402
from .. import autopop, library, model, settings
from .dialogs import ScanDialog, button, choose_folder, h2, hint

ROLE = Qt.UserRole
TV_STYLES = [("shuffle", "Shuffle Classics"), ("marathon", "Series Marathon"), ("mix", "24-Hour Mix")]


def _folder_list(folders: list[str]) -> QListWidget:
    lst = QListWidget()
    lst.setMaximumHeight(110)
    for f in folders:
        it = QListWidgetItem(f)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked)
        lst.addItem(it)
    return lst


def _checked(lst: QListWidget) -> list[str]:
    return [lst.item(i).text() for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]


class AutoPopulateDialog(QDialog):
    def __init__(self, channels: list[model.Channel], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto-create channels")
        self.resize(980, 720)
        self.channels = channels
        self.created: list[model.Channel] = []
        self.build_now = False
        self.shows: list = []
        self.movies: list = []
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(self.stack)
        self.stack.addWidget(self._page_folders())
        self.stack.addWidget(self._page_lineup())

    # ---------------------------------------------------------------- page 1: folders
    def _page_folders(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(h2("Auto-create channels"))
        lay.addWidget(hint(f"Pick the folders to use and {paths.APP_NAME} suggests channels of similar shows, using the "
                           "genres, age ratings and years in your .nfo files. Films get movie channels of their own. "
                           "You can change everything before anything is created, and your existing channels "
                           "are kept as they are."))
        s = settings.load()
        lay.addWidget(QLabel("TV show folders"))
        self.tv_list = _folder_list(list(s.get("library_folders", [])))
        lay.addWidget(self.tv_list)
        add = button("Add a TV folder…")
        add.clicked.connect(lambda: self._add_folder(self.tv_list, "Choose a folder of TV shows"))
        lay.addWidget(add, 0, Qt.AlignLeft)
        lay.addWidget(QLabel("Movie folders"))
        self.mv_list = _folder_list(list(s.get("movie_folders", [])))
        lay.addWidget(self.mv_list)
        addm = button("Add a movie folder…")
        addm.clicked.connect(lambda: self._add_folder(self.mv_list, "Choose a folder of movies"))
        lay.addWidget(addm, 0, Qt.AlignLeft)
        lay.addWidget(hint("Untick a folder to leave it out. Every show in the ticked folders is used, even ones "
                           "already on a channel."))
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        nxt = button("Next: suggest channels  →", "primary")
        nxt.clicked.connect(self._read_library)
        row.addWidget(cancel)
        row.addWidget(nxt)
        lay.addLayout(row)
        return w

    def _add_folder(self, lst: QListWidget, title: str) -> None:
        d = choose_folder(self, title)
        if d and not lst.findItems(d, Qt.MatchExactly):
            it = QListWidgetItem(d)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            lst.addItem(it)

    def _read_library(self) -> None:
        tv, mv = _checked(self.tv_list), _checked(self.mv_list)
        if not tv and not mv:
            QMessageBox.information(self, "Auto-create channels", "Tick or add at least one folder.")
            return
        # new folders join the library (the pickers and "check for new episodes" use it too)
        s = settings.load()
        s["library_folders"] = list(dict.fromkeys(list(s.get("library_folders", [])) + tv))
        s["movie_folders"] = list(dict.fromkeys(list(s.get("movie_folders", [])) + mv))
        settings.save(s)
        shows, movies = [], []
        if tv:
            shows = library.cached()
            if shows is None:
                dlg = ScanDialog(self)
                if dlg.exec() != QDialog.Accepted:
                    return
                shows = dlg.shows
            shows = [sh for sh in shows if sh.root in tv or any(sh.folder.startswith(f.rstrip("/") + "/") for f in tv)]
        if mv:
            movies = library.cached_movies()
            if movies is None:
                dlg = ScanDialog(self, movies=True)
                if dlg.exec() != QDialog.Accepted:
                    return
                movies = dlg.shows
            movies = [m for m in movies if any(m.path.startswith(f.rstrip("/") + "/") for f in mv)]
        if not shows and not movies:
            QMessageBox.information(self, "Auto-create channels", "No shows or films were found in those folders.")
            return
        self.shows, self.movies = shows, movies
        n_tv, n_mv = autopop.suggested_counts(len(shows), len(movies), movies, shows)
        self.found.setText(f"Found {len(shows)} shows and {len(movies)} films.")
        for spin, n, have in ((self.n_tv, n_tv, shows), (self.n_mv, n_mv, movies)):
            spin.blockSignals(True)
            spin.setRange(0 if not have else 1, max(1, len(have)))
            spin.setValue(n)
            spin.setEnabled(bool(have))
            spin.blockSignals(False)
        self.first.blockSignals(True)
        self.first.setValue(self._default_first())
        self.first.blockSignals(False)
        self._suggest()
        self.stack.setCurrentIndex(1)

    def _taken(self) -> set[int]:
        taken = {c.number for c in self.channels}
        if model.guide_number():
            taken.add(model.guide_number())
        return taken

    def _default_first(self) -> int:
        taken = self._taken() - {model.guide_number()}
        return max([3] + [n + 1 for n in taken])

    # ---------------------------------------------------------------- page 2: lineup
    def _page_lineup(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(h2("Suggested channels"))
        self.found = hint("")
        lay.addWidget(self.found)
        row = QHBoxLayout()
        row.addWidget(QLabel("TV channels"))
        self.n_tv = QSpinBox()
        row.addWidget(self.n_tv)
        row.addSpacing(16)
        row.addWidget(QLabel("Movie channels"))
        self.n_mv = QSpinBox()
        row.addWidget(self.n_mv)
        row.addSpacing(16)
        row.addWidget(QLabel("First channel number"))
        self.first = QSpinBox()
        self.first.setRange(1, 999)
        row.addWidget(self.first)
        row.addStretch(1)
        for spin in (self.n_tv, self.n_mv, self.first):
            spin.valueChanged.connect(lambda _v: self._suggest())
        lay.addLayout(row)
        self.ads = QCheckBox("Commercial breaks on the new TV channels")
        lay.addWidget(self.ads)
        lay.addWidget(hint("Rename a channel or change its number by double-clicking it. Drag a title onto another "
                           "channel to move it, or untick it to leave it out. Changing the numbers above starts the "
                           "suggestion again."))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["CHANNEL / TITLE", "NUMBER / RATING", "STYLE / GENRES", ""])
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 130)
        self.tree.setColumnWidth(2, 260)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.invisibleRootItem().setFlags(Qt.ItemIsEnabled)      # no drops between channels
        self.tree.itemChanged.connect(self._item_changed)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        back = button("←  Folders")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        row.addWidget(back)
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        create = button("Create channels")
        create.clicked.connect(lambda: self._create(False))
        build = button("Create && build", "primary")
        build.setToolTip("Create the channels and build them straight away (about a minute per channel)")
        build.clicked.connect(lambda: self._create(True))
        for b in (cancel, create, build):
            row.addWidget(b)
        lay.addLayout(row)
        return w

    def _suggest(self) -> None:
        props = autopop.propose(self.shows, self.movies, self.n_tv.value() if self.shows else 0,
                                self.n_mv.value() if self.movies else 0, self.first.value(), self._taken())
        self.tree.blockSignals(True)
        self.tree.clear()
        for p in props:
            top = QTreeWidgetItem([p.name, str(p.number), "", ""])
            top.setData(0, ROLE, {"kind": p.kind, "template": p.template})
            top.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsDropEnabled)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self.tree.addTopLevelItem(top)
            if p.kind == model.TV:
                combo = QComboBox()
                for key, label in TV_STYLES:
                    combo.addItem(label, key)
                combo.setCurrentIndex(max(0, combo.findData(p.template)))
                combo.currentIndexChanged.connect(
                    lambda _i, t=top, c=combo: t.setData(0, ROLE, {**t.data(0, ROLE), "template": c.currentData()}))
                self.tree.setItemWidget(top, 2, combo)
            else:
                top.setText(2, "Movie Channel")
            for it in p.items:
                child = QTreeWidgetItem([it.title, it.rating or "–", " / ".join(sorted(it.genres))[:60], ""])
                child.setData(0, ROLE, it)
                child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                child.setCheckState(0, Qt.Checked)
                top.addChild(child)
            self._count(top)
        self.tree.blockSignals(False)

    def _count(self, top: QTreeWidgetItem) -> None:
        n = sum(1 for i in range(top.childCount()) if top.child(i).checkState(0) == Qt.Checked)
        kind = top.data(0, ROLE)["kind"]
        top.setText(3, f"{n} {'films' if kind == model.MOVIES and not self._has_shows(top) else 'titles'}")

    @staticmethod
    def _has_shows(top: QTreeWidgetItem) -> bool:
        return any(not top.child(i).data(0, ROLE).movie for i in range(top.childCount()))

    def _item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        top = item if item.parent() is None else item.parent()
        self.tree.blockSignals(True)
        self._count(top)
        self.tree.blockSignals(False)

    # ---------------------------------------------------------------- create
    def _lineup(self) -> list[model.Channel] | None:
        out: list[model.Channel] = []
        taken = self._taken()
        names = {c.name.lower() for c in self.channels}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            items = []

            def walk(node):              # a drop onto a title nests it; take every descendant
                for k in range(node.childCount()):
                    ch = node.child(k)
                    if ch.checkState(0) == Qt.Checked:
                        items.append(ch.data(0, ROLE))
                    walk(ch)
            walk(top)
            if not items:
                continue
            name = top.text(0).strip()
            try:
                number = int(top.text(1))
            except ValueError:
                QMessageBox.warning(self, "Channel number", f"“{top.text(1)}” isn't a channel number ({name}).")
                return None
            if number in taken:
                QMessageBox.warning(self, "Channel number",
                                    f"Channel {number} ({name}) is already in use. Change its number first.")
                return None
            if not name or name.lower() in names:
                QMessageBox.warning(self, "Channel name",
                                    f"There's already a channel called “{name}”. Rename it first." if name
                                    else "Every channel needs a name.")
                return None
            taken.add(number)
            names.add(name.lower())
            data = top.data(0, ROLE)
            movies_ = [it for it in items if it.movie]
            shows_ = [it for it in items if not it.movie]
            if shows_:      # shows (even if dragged onto a movie channel) make it a TV channel
                tmpl = data["template"] if data["kind"] == model.TV else "shuffle"
                p = autopop.Proposal(name, number, model.TV, tmpl, shows_)
                ch = p.channel(self.ads.isChecked())
                ch.movies = [m.key for m in movies_]
            else:
                ch = autopop.Proposal(name, number, model.MOVIES, "movies", movies_).channel()
            out.append(ch)
        if not out:
            QMessageBox.information(self, "Auto-create channels", "Nothing is ticked.")
            return None
        return out

    def _create(self, build: bool) -> None:
        lineup = self._lineup()
        if lineup is None:
            return
        self.created = lineup
        self.build_now = build
        self.accept()
