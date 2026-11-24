#!/usr/bin/env bash
# Build the AppImages: packaging/build_appimage.sh <neon|xcable|both> [out dir]
#
# Runs on Ubuntu 24.04 (GitHub Actions: .github/workflows/appimage.yml). Needs: apt packages ffmpeg libmpv2
# patchelf squashfs-tools file; a self-contained Python (python-build-standalone, PYTHON_TARBALL) or, for
# a local test build, PYTHON_DIR (a relocatable Python install to copy); and appimagetool (APPIMAGETOOL)
# for the final step (without it, only the AppDir is built: test it with  <AppDir>/AppRun list).
#
# Environment: VERSION (release tag, default: date), PYTHON_TARBALL or PYTHON_DIR, APPIMAGETOOL.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
WHICH="${1:-both}"
OUT="$(realpath -m "${2:-$REPO/dist}")"
BUILD="$REPO/build"
VERSION="${VERSION:-v$(date +%Y.%m.%d)}"
mkdir -p "$OUT" "$BUILD"

log() { printf '\n==> %s\n' "$*"; }

# ------------------------------------------------------------------ shared payload (built once)
PAYLOAD="$BUILD/payload"
if [ ! -x "$PAYLOAD/usr/python/bin/python3" ]; then
  rm -rf "$PAYLOAD"; mkdir -p "$PAYLOAD/usr/bin" "$PAYLOAD/usr/lib" "$PAYLOAD/usr/app"

  log "Python"
  if [ -n "${PYTHON_TARBALL:-}" ]; then
    mkdir -p "$BUILD/py"; tar -xzf "$PYTHON_TARBALL" -C "$BUILD/py"
    mv "$BUILD/py/python" "$PAYLOAD/usr/python"
  elif [ -n "${PYTHON_DIR:-}" ]; then
    cp -a "$PYTHON_DIR" "$PAYLOAD/usr/python"
  else
    echo "set PYTHON_TARBALL (python-build-standalone install_only) or PYTHON_DIR" >&2; exit 1
  fi
  PY="$PAYLOAD/usr/python/bin/python3"
  # ignore packages in the build machine's home folder (~/.local): everything must go into the bundle
  export PYTHONNOUSERSITE=1 PIP_USER=0 PIP_REQUIRE_VIRTUALENV=0 PIP_DISABLE_PIP_VERSION_CHECK=1
  "$PY" -m pip install --quiet --no-cache-dir --upgrade pip
  # PySide6-Essentials has every Qt part the app uses (Widgets, OpenGL); the full PySide6 adds ~400 MB
  grep -v -i '^pyside6' "$REPO/requirements.txt" | sed 's/#.*//' | grep . > "$BUILD/req.txt"
  echo "PySide6-Essentials>=6.8" >> "$BUILD/req.txt"
  "$PY" -m pip install --quiet --no-cache-dir -r "$BUILD/req.txt"
  # trim what a player never needs
  rm -rf "$PAYLOAD/usr/python/lib/python3."*/test "$PAYLOAD/usr/python/lib/python3."*/idlelib \
         "$PAYLOAD/usr/python/lib/python3."*/tkinter "$PAYLOAD/usr/python/lib/python3."*/site-packages/PySide6/Qt/translations \
         "$PAYLOAD/usr/python/lib/python3."*/site-packages/PySide6/{designer,assistant,linguist,lupdate,lrelease,uic,rcc,qmlls,qmllint,qmlformat} 2>/dev/null || true
  "$PY" -m pip uninstall --quiet -y pip >/dev/null 2>&1 || true      # never used at run time
  rm -f "$PAYLOAD/usr/python/bin/pip"*
  find "$PAYLOAD/usr/python" -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

  log "app + FieldStation42"
  cp -a "$REPO/getsome" "$PAYLOAD/usr/app/getsome"
  mkdir -p "$PAYLOAD/usr/app/vendor"
  cp -a "$REPO/vendor/FieldStation42" "$PAYLOAD/usr/app/vendor/"
  # FS42's docs (136 MB of screenshots), tests, Docker and installer files are not needed to run it
  (cd "$PAYLOAD/usr/app/vendor/FieldStation42" && rm -rf .git .github docs test docker install && find . -name '*.mp4' -delete)
  find "$PAYLOAD/usr/app" -name "__pycache__" -prune -exec rm -rf {} +
  "$PY" -m compileall -q "$PAYLOAD/usr/app" "$PAYLOAD/usr/python/lib" >/dev/null || true

  log "ffmpeg, ffprobe, libmpv and the libraries they need"
  cp "$(command -v ffmpeg)" "$(command -v ffprobe)" "$PAYLOAD/usr/bin/"
  cp -L /usr/lib/x86_64-linux-gnu/libmpv.so.2 "$PAYLOAD/usr/lib/"
  # never bundle: glibc, graphics drivers and their loaders, X11/Wayland, sound servers (always on the
  # host, and bundling them breaks things): the AppImage project's list plus a few of our own
  EXCL="$(grep -v '^#' "$HERE/excludelist" | sed 's/#.*//' | awk 'NF{print $1}')
libvulkan.so.1
libva.so.2
libva-drm.so.2
libva-x11.so.2
libva-wayland.so.2
libvdpau.so.1
libdrm.so.2
libgbm.so.1
libwayland-client.so.0
libwayland-cursor.so.0
libwayland-egl.so.1
libxkbcommon.so.0
libpulse.so.0
libpulse-simple.so.0
libpipewire-0.3.so.0
libjack.so.0
libasound.so.2
libdbus-1.so.3
libsystemd.so.0
libudev.so.1
libcap.so.2
libz.so.1"
  excluded() { local b; b="$(basename "$1")"; grep -qxF "$b" <<<"$EXCL" || [[ "$b" == libpulsecommon* ]] || [[ "$b" == ld-linux* || "$b" == libc.so* || "$b" == libm.so* || "$b" == libpthread* || "$b" == libdl.so* || "$b" == librt.so* || "$b" == libGL* || "$b" == libEGL* || "$b" == libX* || "$b" == libxcb* ]]; }
  todo=("$PAYLOAD/usr/bin/ffmpeg" "$PAYLOAD/usr/bin/ffprobe" "$PAYLOAD/usr/lib/libmpv.so.2")
  declare -A seen=()
  while [ ${#todo[@]} -gt 0 ]; do
    f="${todo[0]}"; todo=("${todo[@]:1}")
    while read -r lib path; do
      [ -n "$path" ] && [ -f "$path" ] || continue
      excluded "$lib" && continue
      [ -n "${seen[$lib]:-}" ] && continue
      seen[$lib]=1
      cp -L "$path" "$PAYLOAD/usr/lib/$lib"
      todo+=("$PAYLOAD/usr/lib/$lib")
    done < <(ldd "$f" 2>/dev/null | awk '/=> \//{print $1, $3}')
  done
  for f in "$PAYLOAD/usr/lib/"*.so*; do patchelf --set-rpath '$ORIGIN' "$f" 2>/dev/null || true; done
  for f in "$PAYLOAD/usr/bin/ffmpeg" "$PAYLOAD/usr/bin/ffprobe"; do patchelf --set-rpath '$ORIGIN/../lib' "$f"; done
  echo "bundled $(ls "$PAYLOAD/usr/lib" | wc -l) libraries"
fi

# ------------------------------------------------------------------ one AppImage per edition
build_one() {
  local edition="$1" name file
  if [ "$edition" = xcable ]; then name="XCable"; file="XCable"; else name="Neon Cable 42"; file="NeonCable42"; fi
  local APPDIR="$BUILD/AppDir-$edition"
  log "$name AppDir"
  rm -rf "$APPDIR"; cp -a "$PAYLOAD" "$APPDIR"
  sed -e "s/@EDITION@/$edition/" -e "s/@NAME@/$name/" "$HERE/AppRun" > "$APPDIR/AppRun"
  chmod 755 "$APPDIR/AppRun"
  echo "$VERSION" > "$APPDIR/VERSION"
  # icon drawn by the app itself (the TV with "42" or "X"), at build time
  GETSOME_EDITION="$edition" QT_QPA_PLATFORM=offscreen PYTHONPATH="$APPDIR/usr/app" \
    "$APPDIR/usr/python/bin/python3" -c "from pathlib import Path; from getsome import desktop; desktop.make_icon(Path('$APPDIR/$file.png'), 256)"
  cp "$APPDIR/$file.png" "$APPDIR/.DirIcon"
  cat > "$APPDIR/$file.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Comment=Cable TV from your own shows and movies
Exec=AppRun
Icon=$file
Categories=AudioVideo;Video;TV;Player;
Terminal=false
EOF
  if [ -n "${APPIMAGETOOL:-}" ]; then
    log "$name AppImage"
    ARCH=x86_64 "$APPIMAGETOOL" --no-appstream "$APPDIR" "$OUT/$file-x86_64.AppImage"
    (cd "$OUT" && sha256sum "$file-x86_64.AppImage" > "$file-x86_64.AppImage.sha256")
    ls -la "$OUT/$file-x86_64.AppImage"
  else
    echo "no APPIMAGETOOL: AppDir only ($APPDIR)"
  fi
}
case "$WHICH" in
  neon) build_one neon ;;
  xcable) build_one xcable ;;
  both) build_one neon; build_one xcable ;;
  *) echo "usage: $0 <neon|xcable|both> [out dir]" >&2; exit 2 ;;
esac
