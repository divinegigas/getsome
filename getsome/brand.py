"""The look of each edition (paths.XCABLE).

Neon Cable 42 keeps its synthwave colours untouched. XCable ("premium cable": black, gold and
oxblood, like a gentleman's club) reuses all the same drawing code: every colour the drawing code
makes goes through `QColor` below, which maps the neon palette onto the club palette by hue:

    magenta / pink  -> gold            cyan / teal   -> champagne
    violet / navy   -> oxblood, nearly black (darks stay dark)
    yellow / orange -> amber gold      whites        -> ivory          reds stay deep red

Modules import `QColor` from here instead of PySide6 (it *is* a QColor, so it goes anywhere one
does). Stylesheets go through `restyle()`. Fonts: Limelight (Art Deco) and Playfair Display, both
SIL OFL, bundled.
"""

from __future__ import annotations

import colorsys
import re

from PySide6 import QtGui

from . import paths

XCABLE = paths.XCABLE

# font files per edition: "numbers" (big numbers, headings) and "text"
FONTS = ({"numbers": "Limelight-Regular.ttf", "text": "PlayfairDisplay.ttf"} if XCABLE
         else {"numbers": "Monoton-Regular.ttf", "text": "TiltNeon.ttf"})


def map_rgb(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Neon palette -> club palette (identity for Neon Cable 42)."""
    if not XCABLE:
        return r, g, b
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    deg = h * 360
    if s < 0.16:                                   # greys, black, white: warm them slightly
        h2, s2, v2 = 42 / 360, min(0.14, s + 0.06) if v > 0.55 else s, v
    elif deg >= 285 or deg < 5 and s > 0.6 and g < 90 and b > 120:   # magenta / pink -> gold
        h2, s2, v2 = 43 / 360, min(1.0, 0.55 + s * 0.35), max(v, 0.55)
    elif 160 <= deg < 215:                         # cyan / teal -> champagne
        h2, s2, v2 = 38 / 360, s * 0.32, min(1.0, v * 0.97)
    elif 215 <= deg < 285:                         # blue / violet / navy -> oxblood, darker
        h2 = 4 / 360
        s2 = s * 0.75
        v2 = v * (0.42 if v < 0.5 else 0.55)
    elif 70 <= deg < 160:                          # greens (grids, a few ads) -> olive gold
        h2, s2, v2 = 46 / 360, s * 0.55, v * 0.85
    elif 18 <= deg < 70:                           # yellow / orange -> amber gold (pulled towards 40°)
        h2, s2, v2 = (40 + (deg - 40) * 0.3) / 360, s, v
    else:                                          # reds stay deep red
        h2, s2, v2 = h, s, v * 0.85
    rr, gg, bb = colorsys.hsv_to_rgb(h2 % 1.0, max(0.0, min(1.0, s2)), max(0.0, min(1.0, v2)))
    return round(rr * 255), round(gg * 255), round(bb * 255)


class QColor(QtGui.QColor):
    """A QColor whose colour is mapped to this edition's palette when it is created from a value
    (copies of an already mapped colour are not mapped again)."""

    def __init__(self, *args):
        if len(args) == 1 and isinstance(args[0], QtGui.QColor):
            src = args[0]                     # copy by value (PySide can't copy a Python subclass directly)
            super().__init__(src.red(), src.green(), src.blue(), src.alpha())
        else:
            super().__init__(*args)
        if XCABLE and args and not isinstance(args[0], QColor) and self.isValid():
            a = self.alpha()
            r, g, b = map_rgb(self.red(), self.green(), self.blue())
            self.setRgb(r, g, b, a)


_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")
_RGBA = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,")


def hexcolor(h: str) -> str:
    """Map one '#rrggbb' string."""
    r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % map_rgb(r, g, b)


def restyle(css: str) -> str:
    """Map every colour in a Qt stylesheet."""
    if not XCABLE:
        return css
    css = _HEX.sub(lambda m: hexcolor("#" + m.group(1)), css)
    return _RGBA.sub(lambda m: "rgba(%d, %d, %d," % map_rgb(*(int(m.group(i)) for i in (1, 2, 3))), css)
