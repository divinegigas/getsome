"""Static hiss, generated in code and played with QtMultimedia (separate from mpv,
so it can start instantly while mpv is still opening the next file)."""

from __future__ import annotations

import io
import logging
import wave
from pathlib import Path

import numpy as np

from .. import paths

_log = logging.getLogger("getsome.hiss")


def _make_wav(path: Path, seconds: float = 2.0, rate: int = 44100) -> None:
    rng = np.random.default_rng(7)
    n = int(seconds * rate)
    white = rng.normal(0, 1, n)
    # Soften the very top end and the rumble: a TV's tiny speaker, not a pure white noise
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, 1 / rate)
    shape = np.clip((freqs - 150) / 400, 0, 1) * np.clip((11000 - freqs) / 3000, 0, 1)
    noise = np.fft.irfft(spec * shape, n)
    # slight amplitude crackle
    crackle = 1 + 0.25 * rng.standard_normal(n // 400).repeat(400)[:n]
    noise = noise[: len(crackle)] * crackle
    noise = noise / np.max(np.abs(noise)) * 0.5
    # crossfade the ends so the loop is seamless
    fade = int(0.05 * rate)
    noise[:fade] = noise[:fade] * np.linspace(0, 1, fade) + noise[-fade:] * np.linspace(1, 0, fade)
    noise = noise[:-fade]
    pcm = (noise * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    path.write_bytes(buf.getvalue())


class Hiss:
    """A second, audio-only mpv holding the hiss loop paused, so start() is instant.

    (QtMultimedia's QSoundEffect was tried first but crashes on systems without
    a sound server; mpv copes with PipeWire, Pulse or ALSA alike.)
    """

    def __init__(self, volume: float = 35):
        self._mpv = None
        try:
            import mpv

            override = paths.overrides_dir() / "hiss.wav"
            wav = override if override.exists() else paths.fs42_work_dir() / "runtime" / "getsome_hiss.wav"
            if not wav.exists():
                _make_wav(wav)
            self._mpv = mpv.MPV(video=False, config=False, idle=True, loop_file="inf", pause=True,
                                volume=volume, audio_client_name="Neon Cable 42 static", loglevel="error")
            self._mpv.command("loadfile", str(wav), "replace")
        except Exception as e:  # no audio: run silently
            _log.warning("static hiss disabled: %s", e)
            self._mpv = None

    def start(self) -> None:
        if self._mpv is not None:
            self._mpv.pause = False

    def stop(self) -> None:
        if self._mpv is not None:
            self._mpv.pause = True

    def shutdown(self) -> None:
        if self._mpv is not None:
            self._mpv.terminate()
            self._mpv = None
