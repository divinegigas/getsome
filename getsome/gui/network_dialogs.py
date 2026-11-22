"""Settings → Home network: share this computer's channels (head-end), or watch another
computer's channels (cable box). See headend.py and cablebox.py."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QVBoxLayout)

from .. import cablebox, headend, settings
from .dialogs import button, h2, hint


def add_network_section(dlg, lay) -> None:
    lay.addWidget(h2("Home network"))

    # ---- head-end
    share = QCheckBox("Share my channels with cable boxes on my home network")
    share.setChecked(headend.enabled())
    lay.addWidget(share)
    row = QHBoxLayout()
    status = QLabel("")
    status.setObjectName("dim")
    status.setWordWrap(True)
    row.addWidget(status, 1)
    pair_btn = button("Pair a cable box…")
    forget_btn = button("Forget cable boxes")
    row.addWidget(pair_btn)
    row.addWidget(forget_btn)
    lay.addLayout(row)

    def refresh_share():
        on = headend.enabled()
        n = len(headend.paired_boxes())
        if on:
            where = ", ".join(headend.addresses()) or "this computer"
            status.setText(f"Sharing from {where}. {n} cable box{'es' if n != 1 else ''} paired."
                           + ("" if headend.running() else "  (starting…)"))
        else:
            status.setText("Not sharing. Tick the box to let another computer in the house watch these channels "
                           "without a library of its own.")
        pair_btn.setEnabled(on)
        forget_btn.setEnabled(bool(n))

    def toggle(on: bool):
        if on:
            headend.enable()
            QTimer.singleShot(2500, refresh_share)
        else:
            headend.disable()
        refresh_share()

    share.toggled.connect(toggle)
    pair_btn.clicked.connect(lambda: (PairCodeDialog(dlg).exec(), refresh_share()))
    forget_btn.clicked.connect(lambda: (
        QMessageBox.question(dlg, "Forget cable boxes", "Disconnect every paired cable box? They'll need a new "
                             "pairing code to watch again.") == QMessageBox.Yes and headend.forget_boxes(),
        refresh_share()))
    refresh_share()

    # ---- cable box
    row = QHBoxLayout()
    box_status = QLabel("")
    box_status.setObjectName("dim")
    box_status.setWordWrap(True)
    row.addWidget(box_status, 1)
    connect_btn = button("Connect to a head-end…")
    stop_btn = button("Stop being a cable box")
    row.addWidget(connect_btn)
    row.addWidget(stop_btn)
    lay.addLayout(row)

    def refresh_box():
        conf = cablebox.config()
        if conf:
            box_status.setText(f"This computer is a cable box: Watch TV shows the channels of "
                               f"{conf.get('name')} ({conf.get('host')}).")
        else:
            box_status.setText("Or make this computer a cable box: it watches another computer's channels.")
        stop_btn.setVisible(bool(conf))
        dlg.s["cable_box"] = settings.load().get("cable_box") or {}   # Save mustn't undo this

    def stop():
        if QMessageBox.question(dlg, "Stop being a cable box",
                                "Go back to this computer's own channels?") == QMessageBox.Yes:
            cablebox.unpair()
            refresh_box()

    connect_btn.clicked.connect(lambda: (ConnectDialog(dlg).exec(), refresh_box()))
    stop_btn.clicked.connect(stop)
    refresh_box()
    lay.addWidget(hint("Home network only: nothing goes on the internet. Only paired cable boxes can watch, and they "
                       "can only reach the files your channels use."))


class PairCodeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pair a cable box")
        lay = QVBoxLayout(self)
        lay.addWidget(h2("Pairing code"))
        code = headend.new_pair_code()
        big = QLabel(f"{code[:3]} {code[3:]}")
        f = big.font()
        f.setPointSize(40)
        f.setBold(True)
        big.setFont(f)
        big.setAlignment(Qt.AlignCenter)
        big.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(big)
        lay.addWidget(hint("On the other computer: open Neon Cable 42 → Settings → Home network → "
                           "“Connect to a head-end…”, pick this computer and type this code. "
                           f"It works for {headend.PAIR_MINUTES} minutes."))
        ok = button("Done", "primary")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok, 0, Qt.AlignRight)

    def done(self, r):
        headend.close_pairing()
        super().done(r)


class ConnectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to a head-end")
        self.resize(520, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(h2("Connect to a head-end"))
        lay.addWidget(hint("Head-ends found on your home network. Not there? Make sure “Share my channels” is "
                           "ticked on the other computer, or type its address below."))
        self.found = QListWidget()
        self.found.itemClicked.connect(lambda it: self.addr.setText(it.data(Qt.UserRole)))
        lay.addWidget(self.found, 1)
        search = button("Search again")
        search.clicked.connect(self._search)
        lay.addWidget(search, 0, Qt.AlignLeft)
        row = QHBoxLayout()
        row.addWidget(QLabel("Address"))
        self.addr = QLineEdit()
        self.addr.setPlaceholderText("e.g. 192.168.1.20")
        row.addWidget(self.addr, 1)
        row.addWidget(QLabel("Code"))
        self.code = QLineEdit()
        self.code.setPlaceholderText("6 digits")
        self.code.setMaxLength(7)
        row.addWidget(self.code)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        go = button("Connect", "primary")
        go.clicked.connect(self._connect)
        row.addWidget(cancel)
        row.addWidget(go)
        lay.addLayout(row)
        QTimer.singleShot(100, self._search)

    def _search(self):
        self.found.clear()
        self.found.addItem("Searching…")
        QTimer.singleShot(50, self._do_search)

    def _do_search(self):
        found = cablebox.discover()
        self.found.clear()
        for h in found:
            it = QListWidgetItem(f"{h.get('name', h['host'])}   ({h['host']})"
                                 + ("   · ready to pair" if h.get("pairing") else ""))
            it.setData(Qt.UserRole, h["host"])
            self.found.addItem(it)
        if not found:
            self.found.addItem("No head-end found yet.")
            self.found.item(0).setFlags(Qt.NoItemFlags)
        elif len(found) == 1:
            self.addr.setText(found[0]["host"])

    def _connect(self):
        host = self.addr.text().strip()
        host, _, port = host.partition(":")
        code = self.code.text().replace(" ", "").strip()
        if not host or len(code) != 6 or not code.isdigit():
            QMessageBox.information(self, "Connect", "Pick or type the head-end's address, and type the 6-digit "
                                    "code it shows (Settings → Home network → Pair a cable box).")
            return
        try:
            conf = cablebox.pair(host, code, int(port or 4242))
        except cablebox.Unreachable as e:
            QMessageBox.warning(self, "Connect", f"That didn't work: {e}\n\nCheck the code (it lasts "
                                f"{headend.PAIR_MINUTES} minutes) and that both computers are on the same network.")
            return
        QMessageBox.information(self, "Connected", f"This computer is now a cable box for {conf['name']}. "
                                "Press Watch TV to see its channels.")
        self.accept()
