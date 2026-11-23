#!/usr/bin/env bash
# GetSome launcher for running from a source checkout (phase 2 adds an AppImage).
# First run creates a private Python environment in .venv/ and installs dependencies.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
PY="${GETSOME_PYTHON:-python3}"

NAME="${GETSOME_APP_NAME:-Neon Cable 42}"
need() { command -v "$1" >/dev/null 2>&1; }
# started from the app menu there's no terminal to read errors in: show a notification too
fail() {
  echo "$1" >&2
  if [ ! -t 2 ] && need notify-send; then notify-send -a "$NAME" "$NAME can't start" "$1"; fi
  exit 1
}
# Bazzite / Fedora Atomic (Silverblue, Kinoite …) install packages with rpm-ostree, then a restart
atomic() { [ -e /run/ostree-booted ]; }
how() {
  if atomic; then
    echo "On Bazzite / Fedora Atomic: rpm-ostree install $1   then restart the computer (if you already ran that, just restart)"
  else
    echo "On Fedora: sudo dnf install $1"
  fi
}
if ! need ffmpeg || ! need ffprobe; then
  fail "$NAME needs ffmpeg. $(how ffmpeg-free)"
fi
if ! "$PY" -c "import ctypes.util, sys; sys.exit(0 if ctypes.util.find_library('mpv') else 1)"; then
  fail "$NAME needs libmpv. $(how mpv-libs)"
fi
if [ ! -f "$HERE/vendor/FieldStation42/station_42.py" ]; then
  git -C "$HERE" submodule update --init --recursive
fi
if [ ! -x "$VENV/bin/python" ] || [ "$HERE/requirements.txt" -nt "$VENV/.installed" ]; then
  echo "Setting up Python environment (first run only)..."
  if [ ! -t 1 ] && need notify-send; then notify-send -a "$NAME" "$NAME" "Setting up (first run only, about a minute)…"; fi
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"
  touch "$VENV/.installed"
fi
# Started from Steam (a non-Steam game shortcut, Big Picture, Game Mode)?
if [ -n "${SteamGameId:-}${SteamAppId:-}${SteamOverlayGameId:-}${SteamClientLaunch:-}" ] || [ "${SteamEnv:-}" = 1 ]; then
  export GETSOME_FROM_STEAM=1
  # Steam injects its overlay with LD_PRELOAD; it hooks OpenGL and can leave the video black
  if [ -n "${LD_PRELOAD:-}" ]; then
    LD_PRELOAD="$(printf '%s' "$LD_PRELOAD" | tr ': ' '\n\n' | grep -v gameoverlayrenderer | paste -sd: - || true)"
    export LD_PRELOAD
  fi
  # Steam's runtime libraries can shadow the system's graphics libraries: use the system's
  if [ -n "${SYSTEM_LD_LIBRARY_PATH+x}" ]; then export LD_LIBRARY_PATH="$SYSTEM_LD_LIBRARY_PATH"; fi
fi
cd "$HERE"
exec "$VENV/bin/python" -m getsome "$@"
