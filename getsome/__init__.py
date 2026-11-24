"""Neon Cable 42 / XCable.

In the AppImage, libmpv ships inside the bundle: GETSOME_LIBMPV (set by AppRun) is where it is, and
python-mpv looks it up with ctypes.util.find_library("mpv"), so answer that one name with our copy."""

import os as _os

_libmpv = _os.environ.get("GETSOME_LIBMPV")
if _libmpv:
    import ctypes.util as _cu

    _find = _cu.find_library
    _cu.find_library = lambda name: _libmpv if name == "mpv" else _find(name)
