"""80s retro-futurist look: night-violet sky, synthwave sun, neon perspective grid,
magenta/cyan neon trim and chrome headings. All drawn in code."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from ..brand import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QWidget

from ..tv import overlays

PINK = "#ff2bd6"
CYAN = "#00f0ff"
SUN = "#ffb000"
VIOLET = "#7b2cff"
INK = "#e9e4ff"
DIM = "#9a8fc7"
PANEL = "rgba(12, 6, 32, 0.80)"
FIELD = "#140a33"

STYLE = f"""
* {{ color: {INK}; font-size: 14px; }}
QMainWindow, QDialog {{ background: #07021a; }}
QToolTip {{ background: #1b0f44; color: {INK}; border: 1px solid {CYAN}; }}

QFrame#panel {{ background: {PANEL}; border: 1px solid rgba(0,240,255,0.45); border-radius: 6px; }}
QLabel#h2 {{ color: {CYAN}; font-size: 13px; font-weight: 700; letter-spacing: 3px; }}
QLabel#dim, QLabel#hint {{ color: {DIM}; }}
QLabel#hint {{ font-size: 12px; }}
QLabel#warn {{ color: {SUN}; }}
QLabel#ok {{ color: #7cff4f; }}

QPushButton {{
  background: rgba(20, 10, 52, 0.9); border: 1px solid {VIOLET}; border-radius: 4px;
  padding: 7px 14px; font-weight: 700; letter-spacing: 1px;
}}
QPushButton:hover {{ border-color: {CYAN}; background: rgba(0, 240, 255, 0.10); }}
QPushButton:pressed {{ background: rgba(0, 240, 255, 0.25); }}
QPushButton:disabled {{ color: #5a5280; border-color: #2c2357; }}
QPushButton#primary {{ border: 2px solid {PINK}; color: #ffffff; background: rgba(255, 43, 214, 0.18); }}
QPushButton#primary:hover {{ background: rgba(255, 43, 214, 0.35); }}
QPushButton#danger {{ border-color: #ff4f6d; color: #ff9fb0; }}
QPushButton#seg {{ padding: 10px 12px; text-align: left; }}
QPushButton#seg:checked {{ border: 2px solid {PINK}; background: rgba(255, 43, 214, 0.22); color: #ffffff; }}
QPushButton#picker {{ text-align: left; font-weight: 400; letter-spacing: 0; padding: 5px 10px; }}
QPushButton#picker:disabled {{ color: #5a5280; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: rgba(0, 240, 255, 0.35); }}
QPushButton#card {{ text-align: left; padding: 10px 14px; }}
QPushButton#card:checked {{ border: 2px solid {PINK}; background: rgba(255, 43, 214, 0.22); }}
QLabel#cardTitle {{ font-weight: 700; font-size: 15px; color: #ffffff; }}
QLabel#cardBody {{ color: {DIM}; }}
QPushButton#flat {{ border: none; background: transparent; color: {DIM}; padding: 2px 6px; }}
QPushButton#flat:hover {{ color: #ff4f6d; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QListWidget, QTableWidget {{
  background: {FIELD}; border: 1px solid #3a2a7a; border-radius: 4px; padding: 5px;
  selection-background-color: rgba(255, 43, 214, 0.45);
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {CYAN}; }}
QComboBox QAbstractItemView {{ background: #120830; border: 1px solid {CYAN}; }}
QSpinBox {{ font-size: 20px; font-weight: 700; color: {PINK}; }}
QHeaderView::section {{
  background: #1b0f44; color: {CYAN}; border: none; border-bottom: 1px solid {VIOLET};
  padding: 6px; font-weight: 700; letter-spacing: 1px;
}}
QTableWidget {{ gridline-color: #24184f; }}
QTableWidget::item {{ padding: 4px; }}
QListWidget::item {{ padding: 4px; border-bottom: 1px solid #1d1245; }}
QListWidget::item:selected {{ background: rgba(255, 43, 214, 0.25); border-left: 3px solid {PINK}; }}
QListWidget::item:hover {{ background: rgba(0, 240, 255, 0.08); }}
QProgressBar {{ background: {FIELD}; border: 1px solid {VIOLET}; border-radius: 4px; text-align: center; height: 18px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PINK}, stop:1 {CYAN}); }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #3a2a7a; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #3a2a7a; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: #3a2a7a; }}
QSlider::handle:horizontal {{ background: {PINK}; width: 14px; margin: -6px 0; border-radius: 7px; }}
"""
from ..brand import restyle  # noqa: E402

STYLE = restyle(STYLE)       # XCable: the same sheet in black & gold



def glow(widget: QWidget, color: str = PINK, radius: int = 18) -> None:
    fx = QGraphicsDropShadowEffect(widget)
    fx.setColor(QColor(color))
    fx.setBlurRadius(radius)
    fx.setOffset(0, 0)
    widget.setGraphicsEffect(fx)


def heading_font(px: int) -> QFont:
    return overlays.font("text", px)


class SynthBackground(QWidget):
    """Window backdrop: gradient sky, striped sun, scrolling-free neon grid, scanlines."""

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        horizon = h * 0.64

        sky = QLinearGradient(0, 0, 0, horizon)
        sky.setColorAt(0.0, QColor("#05010f"))
        sky.setColorAt(0.6, QColor("#1a0638"))
        sky.setColorAt(1.0, QColor("#4a0d4f"))
        p.fillRect(QRectF(0, 0, w, horizon), sky)

        # striped sun sitting on the horizon
        r = min(w, h) * 0.22
        cx, cy = w * 0.5, horizon - r * 0.35
        sun = QLinearGradient(0, cy - r, 0, cy + r)
        sun.setColorAt(0.0, QColor("#ffe46b"))
        sun.setColorAt(0.5, QColor("#ff7a3d"))
        sun.setColorAt(1.0, QColor("#ff2bd6"))
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), r, r)
        cut = QPainterPath()
        y, gap = cy, 2.0
        while y < cy + r:
            cut.addRect(QRectF(cx - r, y, 2 * r, gap))
            y += gap * 3.2
            gap *= 1.35
        p.setOpacity(0.55)
        p.fillPath(path.subtracted(cut), sun)
        halo = QRadialGradient(QPointF(cx, cy), r * 2.2)
        halo.setColorAt(0.0, QColor(255, 60, 200, 70))
        halo.setColorAt(1.0, QColor(255, 60, 200, 0))
        p.fillRect(QRectF(0, 0, w, horizon), halo)
        p.setOpacity(1.0)

        ground = QLinearGradient(0, horizon, 0, h)
        ground.setColorAt(0.0, QColor("#16052e"))
        ground.setColorAt(1.0, QColor("#05010f"))
        p.fillRect(QRectF(0, horizon, w, h - horizon), ground)

        # perspective grid
        pen = QPen(QColor(255, 43, 214, 120), 1.2)
        p.setPen(pen)
        vx = w / 2
        for i in range(-24, 25):
            p.drawLine(QPointF(vx + i * 6, horizon), QPointF(vx + i * w / 9, h))
        y, step = horizon, 3.0
        while y < h:
            p.drawLine(QPointF(0, y), QPointF(w, y))
            y += step
            step *= 1.28
        p.setPen(QPen(QColor(0, 240, 255, 200), 2))
        p.drawLine(QPointF(0, horizon), QPointF(w, horizon))

        # scanlines
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        for yy in range(0, h, 3):
            p.drawLine(0, yy, w, yy)
        p.end()


class NeonLabel(QLabel):
    """A label rendered with the TV-mode neon tube effect."""

    def __init__(self, text: str, kind: str = "text", px: int = 36, color: str = PINK, parent=None):
        super().__init__(parent)
        self._args = (kind, px, QColor(color))
        self.setText_(text)

    def setText_(self, text: str) -> None:
        from PySide6.QtGui import QPixmap

        kind, px, color = self._args
        img = overlays.neon_image(text, kind, px, color)
        self.setPixmap(QPixmap.fromImage(img))


class ChromeLabel(QLabel):
    """Chrome-gradient heading text (80s logo style)."""

    def __init__(self, text: str, px: int = 22, parent=None):
        super().__init__(parent)
        self._text, self._px = text, px
        f = heading_font(px)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        self._font = f
        from PySide6.QtGui import QFontMetrics

        m = QFontMetrics(f)
        self.setMinimumSize(m.horizontalAdvance(text) + 8, m.height() + 6)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        from PySide6.QtGui import QFontMetrics

        m = QFontMetrics(self._font)
        path.addText(2, m.ascent() + 2, self._font, self._text)
        g = QLinearGradient(0, 0, 0, m.height())
        g.setColorAt(0.0, QColor("#ffffff"))
        g.setColorAt(0.45, QColor("#9fd8ff"))
        g.setColorAt(0.5, QColor("#2a1a5e"))
        g.setColorAt(0.75, QColor("#ff9be8"))
        g.setColorAt(1.0, QColor("#ffffff"))
        p.setPen(QPen(QColor(255, 43, 214, 160), 1.2))
        p.setBrush(g)
        p.drawPath(path)
        p.end()


def card_button(title: str, body: str):
    """A checkable button with a bold title and a word-wrapped description."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout

    b = QPushButton()
    b.setObjectName("card")
    b.setCheckable(True)
    b.setCursor(Qt.PointingHandCursor)
    lay = QVBoxLayout(b)
    lay.setContentsMargins(12, 8, 12, 8)
    for text, name in ((title, "cardTitle"), (body, "cardBody")):
        lab = QLabel(text)
        lab.setObjectName(name)
        lab.setWordWrap(True)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(lab)
    b.setMinimumHeight(86)
    return b
