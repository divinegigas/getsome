#!/usr/bin/env bash
# GetSome launcher for running from a source checkout (phase 2 adds an AppImage).
# First run creates a private Python environment in .venv/ and installs dependencies.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
PY="${GETSOME_PYTHON:-python3}"

need() { command -v "$1" >/dev/null 2>&1; }
# started from the app menu there's no terminal to read errors in: show a notification too
fail() {
  echo "$1" >&2
  if [ ! -t 2 ] && need notify-send; then notify-send -a "Neon Cable 42" "Neon Cable 42 can't start" "$1"; fi
  exit 1
}
if ! need ffmpeg || ! need ffprobe; then
  fail "Neon Cable 42 needs ffmpeg. On Fedora: sudo dnf install ffmpeg-free  (or ffmpeg from RPM Fusion)"
fi
if ! "$PY" -c "import ctypes.util, sys; sys.exit(0 if ctypes.util.find_library('mpv') else 1)"; then
  fail "Neon Cable 42 needs libmpv. On Fedora: sudo dnf install mpv-libs"
fi
if [ ! -f "$HERE/vendor/FieldStation42/station_42.py" ]; then
  git -C "$HERE" submodule update --init --recursive
fi
if [ ! -x "$VENV/bin/python" ] || [ "$HERE/requirements.txt" -nt "$VENV/.installed" ]; then
  echo "Setting up Python environment (first run only)..."
  if [ ! -t 1 ] && need notify-send; then notify-send -a "Neon Cable 42" "Neon Cable 42" "Setting up (first run only, about a minute)…"; fi
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
