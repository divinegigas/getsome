"""Add GetSome to the desktop's app menu (Sound & Video), with a generated neon icon.

Writes ~/.local/share/applications/getsome.desktop, the freedesktop.org standard
that GNOME, KDE and Bazzite all read. Right-clicking the icon offers "Watch TV" and
"Channel setup". Nothing outside the home folder is touched; remove() undoes it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import paths

APP_ID = "getsome"          # also the Wayland app id (QGuiApplication.setDesktopFileName)


def _apps_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications"


def desktop_file() -> Path:
    return _apps_dir() / f"{APP_ID}.desktop"


def icon_file() -> Path:
    return paths.data_dir() / "icon" / "getsome.png"


def launcher() -> Path:
    return paths.APP_DIR.parent / "getsome.sh"


def installed() -> bool:
    return desktop_file().exists()


def make_icon(dest: Path, size: int = 256) -> None:
    """A neon TV set with '42' on the screen, drawn in code (no downloaded art)."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

    if QGuiApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QGuiApplication([])
    from .tv import overlays

    overlays.load_fonts()
    s = size / 256
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    # antennas
    p.setPen(QPen(QColor("#00f0ff"), 7 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(128 * s, 62 * s), QPointF(84 * s, 18 * s))
    p.drawLine(QPointF(128 * s, 62 * s), QPointF(172 * s, 18 * s))
    # cabinet
    body = QRectF(16 * s, 58 * s, 224 * s, 172 * s)
    g = QLinearGradient(body.topLeft(), body.bottomLeft())
    g.setColorAt(0, QColor("#2a0a5e"))
    g.setColorAt(1, QColor("#0b0326"))
    p.setPen(QPen(QColor("#ff2bd6"), 8 * s))
    p.setBrush(g)
    p.drawRoundedRect(body, 34 * s, 34 * s)
    # screen with a synthwave glow
    scr = QRectF(36 * s, 78 * s, 160 * s, 132 * s)
    sg = QLinearGradient(scr.topLeft(), scr.bottomLeft())
    sg.setColorAt(0, QColor("#140044"))
    sg.setColorAt(0.65, QColor("#7b2cff"))
    sg.setColorAt(1, QColor("#ff2bd6"))
    p.setPen(QPen(QColor("#00f0ff"), 4 * s))
    p.setBrush(sg)
    p.drawRoundedRect(scr, 22 * s, 22 * s)
    f = QFont(overlays.font("numbers", int(84 * s)))
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(scr, Qt.AlignCenter, "42")
    # knobs
    p.setPen(Qt.NoPen)
    for y, c in ((108, "#00f0ff"), (150, "#ffe600")):
        p.setBrush(QColor(c))
        p.drawEllipse(QPointF(218 * s, y * s), 10 * s, 10 * s)
    p.end()
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest))


def _entry() -> str:
    exe = f'"{launcher()}"'
    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Neon Cable 42
GenericName=Cable TV
Comment=90s-style cable TV from your own shows and movies
Exec={exe}
Icon={icon_file()}
Terminal=false
Categories=AudioVideo;Video;TV;Player;
Keywords=tv;cable;channels;retro;guide;
StartupWMClass={APP_ID}
Actions=watch;setup;

[Desktop Action watch]
Name=Watch TV
Exec={exe} tv

[Desktop Action setup]
Name=Channel setup
Exec={exe} setup
"""


def install() -> Path:
    make_icon(icon_file())
    d = desktop_file()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(_entry())
    d.chmod(0o755)
    _refresh()
    return d


def remove() -> None:
    desktop_file().unlink(missing_ok=True)
    _refresh()


def refresh_if_installed() -> None:
    """Keep the menu entry pointing at this copy (e.g. if the folder moved)."""
    try:
        if installed() and desktop_file().read_text() != _entry():
            install()
    except OSError:
        pass


def _refresh() -> None:
    """Ask the desktop to re-read its menus: GNOME, and KDE Plasma (which keeps a cache)."""
    for cmd in (["update-desktop-database", str(_apps_dir())], ["kbuildsycoca6"], ["kbuildsycoca5"],
                ["xdg-desktop-menu", "forceupdate"]):
        tool = shutil.which(cmd[0])
        if tool:
            try:
                subprocess.run([tool, *cmd[1:]], capture_output=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if cmd[0].startswith("kbuildsycoca"):
                break                     # one KDE version is enough


# --------------------------------------------------------------------------- desktop shortcut

def _desktop_dir() -> Path:
    tool = shutil.which("xdg-user-dir")
    if tool:
        try:
            out = subprocess.run([tool, "DESKTOP"], capture_output=True, text=True, timeout=5).stdout.strip()
            if out and Path(out).is_dir() and Path(out) != Path.home():
                return Path(out)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return Path.home() / "Desktop"


def desktop_shortcut() -> Path:
    return _desktop_dir() / f"{APP_ID}.desktop"


def desktop_shortcut_installed() -> bool:
    return desktop_shortcut().exists()


def install_desktop_shortcut() -> Path:
    make_icon(icon_file())
    d = desktop_shortcut()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(_entry())
    d.chmod(0o755)                       # KDE runs executable .desktop files on double-click
    gio = shutil.which("gio")
    if gio:                              # GNOME wants it marked trusted
        subprocess.run([gio, "set", str(d), "metadata::trusted", "true"], capture_output=True)
    return d


def remove_desktop_shortcut() -> None:
    desktop_shortcut().unlink(missing_ok=True)
