"""Keep the computer awake while TV mode is on.

libmpv drawing into our own window can't stop the screensaver itself, so we ask
the desktop directly, over D-Bus (jeepney, pure Python):

  * org.gnome.SessionManager.Inhibit      GNOME: no screen blanking, no idle suspend
  * org.freedesktop.ScreenSaver.Inhibit   KDE and most others: no blanking/locking
  * org.freedesktop.PowerManagement.Inhibit  KDE: no suspend
  * org.freedesktop.login1.Inhibit        systemd: block idle + sleep (any desktop)

Every one of these is tied to our D-Bus connection (or, for logind, a file
descriptor we hold), so if GetSome crashes the desktop drops the block by itself.
Each call that isn't supported is simply skipped.

"Like a real TV's auto-off": with `keep_awake_hours` set, the block is released
after that many hours without a remote/keyboard press, and taken again on the next one.
"""

from __future__ import annotations

import logging
import os
from .. import paths

_log = logging.getLogger("getsome.awake")
APP, WHY = f"{paths.APP_NAME}", "Watching TV"

# (bus name, object path, interface, inhibit method, signature, args, release method)
SESSION_CALLS = [
    ("org.gnome.SessionManager", "/org/gnome/SessionManager", "org.gnome.SessionManager",
     "Inhibit", "susu", (APP, 0, WHY, 4 | 8), "Uninhibit"),          # 4 = suspend, 8 = idle
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver",
     "Inhibit", "ss", (APP, WHY), "UnInhibit"),
    ("org.freedesktop.PowerManagement", "/org/freedesktop/PowerManagement/Inhibit",
     "org.freedesktop.PowerManagement.Inhibit", "Inhibit", "ss", (APP, WHY), "UnInhibit"),
]


class KeepAwake:
    def __init__(self):
        self.active = False
        self._session = None
        self._cookies: list[tuple] = []      # (address, release method, cookie)
        self._logind_fd: int | None = None

    def start(self) -> None:
        if self.active:
            return
        self.active = True
        got = self._start_session() + self._start_logind()
        _log.info("keeping the computer awake (%s)", ", ".join(got) if got else "no inhibitor available")

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        if self._session is not None:
            from jeepney import new_method_call

            for addr, release, cookie in self._cookies:
                try:
                    self._session.send(new_method_call(addr, release, "u", (cookie,)))
                except Exception as e:
                    _log.debug("release failed: %s", e)
            try:
                self._session.close()
            except Exception:
                pass
        self._session, self._cookies = None, []
        if self._logind_fd is not None:
            try:
                os.close(self._logind_fd)
            except OSError:
                pass
            self._logind_fd = None
        _log.info("computer may sleep again")

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _call(conn, addr, method, sig, args):
        from jeepney import MessageType, new_method_call

        reply = conn.send_and_get_reply(new_method_call(addr, method, sig, args), timeout=3)
        if reply.header.message_type == MessageType.error:
            raise RuntimeError(reply.body[0] if reply.body else "D-Bus error")
        return reply.body

    def _start_session(self) -> list[str]:
        try:
            from jeepney import DBusAddress
            from jeepney.io.blocking import open_dbus_connection

            conn = open_dbus_connection(bus="SESSION")
        except Exception as e:
            _log.info("no desktop session bus: %s", e)
            return []
        got = []
        for name, path, iface, method, sig, args, release in SESSION_CALLS:
            addr = DBusAddress(path, bus_name=name, interface=iface)
            try:
                body = self._call(conn, addr, method, sig, args)
                self._cookies.append((addr, release, int(body[0])))
                got.append(name.rsplit(".", 1)[-1])
            except Exception as e:
                _log.debug("%s not available: %s", name, e)
        if got:
            self._session = conn          # the blocks live as long as this connection
        else:
            conn.close()
        return got

    def _start_logind(self) -> list[str]:
        try:
            from jeepney import DBusAddress
            from jeepney.io.blocking import open_dbus_connection

            conn = open_dbus_connection(bus="SYSTEM", enable_fds=True)
        except Exception as e:
            _log.debug("no system bus: %s", e)
            return []
        try:
            addr = DBusAddress("/org/freedesktop/login1", bus_name="org.freedesktop.login1",
                               interface="org.freedesktop.login1.Manager")
            body = self._call(conn, addr, "Inhibit", "ssss", ("idle:sleep", APP, WHY, "block"))
            fd = body[0]
            self._logind_fd = fd.to_raw_fd() if hasattr(fd, "to_raw_fd") else int(fd)
            return ["logind"]
        except Exception as e:
            _log.debug("logind inhibit not available: %s", e)
            return []
        finally:
            conn.close()
