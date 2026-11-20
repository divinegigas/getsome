"""A tiny synthesizer for generated music: the guide channel loop and the
"up next" jingles. Detuned saw pads, an arpeggiator, a bass line and a drum
machine. Nothing is sampled; everything is computed here.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np

RATE = 44100


def _note(n: str) -> float:
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    pitch, octave = n[:-1], int(n[-1])
    semis = names[pitch] + 12 * (octave + 1) - 69
    return 440.0 * 2 ** (semis / 12)


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * RATE)) / RATE


def _saw(freq: float, seconds: float, detune: float = 0.0) -> np.ndarray:
    t = _t(seconds)
    out = np.zeros_like(t)
    for d in (-detune, 0.0, detune) if detune else (0.0,):
        f = freq * (1 + d)
        out += 2 * ((t * f) % 1.0) - 1
    return out / (3 if detune else 1)


def _square(freq: float, seconds: float, duty: float = 0.5) -> np.ndarray:
    t = _t(seconds)
    return np.where((t * freq) % 1.0 < duty, 1.0, -1.0)


def _sine(freq: float, seconds: float) -> np.ndarray:
    return np.sin(2 * np.pi * freq * _t(seconds))


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    # one-pole low-pass, vectorised in blocks via scipy-free recurrence
    a = np.exp(-2 * np.pi * cutoff / RATE)
    y = np.empty_like(x)
    acc = 0.0
    b = 1 - a
    for i in range(0, len(x), 512):
        seg = x[i:i + 512]
        # closed form for a block: y[n] = a^n*acc + b*sum(a^(n-k) x[k])
        n = np.arange(len(seg))
        powers = a ** n
        conv = np.convolve(seg, b * powers)[: len(seg)]
        out = conv + acc * a * powers
        y[i:i + len(seg)] = out
        acc = out[-1]
    return y


def _env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n)
    a, r = int(attack * RATE), int(release * RATE)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def _place(buf: np.ndarray, sound: np.ndarray, at: float) -> None:
    i = int(at * RATE)
    j = min(len(buf), i + len(sound))
    if i < len(buf):
        buf[i:j] += sound[: j - i]


def _kick() -> np.ndarray:
    t = _t(0.35)
    f = 120 * np.exp(-t * 18) + 45
    return np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t * 9)


def _snare(rng) -> np.ndarray:
    t = _t(0.45)
    noise = _lowpass(rng.normal(0, 1, len(t)), 3800)
    noise = noise / (np.max(np.abs(noise)) or 1)
    # gated "80s" snare: long-ish noise tail that cuts off
    return (noise * 0.6 + np.sin(2 * np.pi * 190 * t) * 0.5) * np.exp(-t * 7) * (t < 0.32)


def _hat(rng) -> np.ndarray:
    t = _t(0.06)
    n = rng.normal(0, 1, len(t))
    n = n - _lowpass(n, 6000)
    n = _lowpass(n, 11000)
    return n / (np.max(np.abs(n)) or 1) * np.exp(-t * 60)


def _echo(x: np.ndarray, delay: float, fb: float, mix: float) -> np.ndarray:
    d = int(delay * RATE)
    y = x.copy()
    for k in range(1, 5):
        if d * k >= len(x):
            break
        y[d * k:] += x[: len(x) - d * k] * (fb ** k) * mix
    return y


def render_track(chords: list[list[str]], bpm: float, bars_per_chord: int, seconds: float,
                 seed: int = 1, arp: bool = True, drums: bool = True, bright: float = 2400,
                 fade_out: float = 0.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar = beat * 4
    out_pad = np.zeros(int(seconds * RATE) + RATE)
    out_arp = np.zeros_like(out_pad)
    out_bass = np.zeros_like(out_pad)
    out_drum = np.zeros_like(out_pad)
    t, ci = 0.0, 0
    while t < seconds:
        chord = chords[ci % len(chords)]
        dur = bar * bars_per_chord
        # pad: detuned saws, filtered
        pad = sum(_saw(_note(n), dur, 0.004) for n in chord) / len(chord)
        pad *= _env(len(pad), 0.25, 0.3)
        _place(out_pad, pad, t)
        # bass: root an octave down, eighth notes
        root = _note(chord[0]) / 2
        for k in range(int(bars_per_chord * 8)):
            s = _square(root, beat / 2 * 0.9, 0.35) * _env(int(beat / 2 * 0.9 * RATE), 0.005, 0.04)
            _place(out_bass, s * (1.0 if k % 2 == 0 else 0.7), t + k * beat / 2)
        # arpeggio: chord tones up an octave, sixteenths
        if arp:
            tones = [_note(n) * 2 for n in chord] + [_note(chord[0]) * 4]
            for k in range(int(bars_per_chord * 16)):
                f = tones[[0, 1, 2, 3, 2, 1][k % 6] % len(tones)]
                s = _square(f, beat / 4 * 0.8, 0.25) * _env(int(beat / 4 * 0.8 * RATE), 0.002, 0.05)
                _place(out_arp, s, t + k * beat / 4)
        if drums:
            for b in range(bars_per_chord * 4):
                at = t + b * beat
                if b % 2 == 0:
                    _place(out_drum, _kick() * 0.9, at)
                else:
                    _place(out_drum, _snare(rng) * 0.35, at)
                for hh in range(2):
                    _place(out_drum, _hat(rng) * 0.12, at + hh * beat / 2)
        t += dur
        ci += 1
    pad = _lowpass(out_pad, bright) * 0.35
    arp_ = _echo(_lowpass(out_arp, bright * 1.6), beat * 0.75, 0.45, 0.6) * 0.14
    bass = _lowpass(out_bass, 700) * 0.30
    mix = pad + arp_ + bass + out_drum * 0.22
    mix = mix[: int(seconds * RATE)]
    if fade_out:
        n = int(fade_out * RATE)
        mix[-n:] *= np.linspace(1, 0, n)
    mix[: int(0.01 * RATE)] *= np.linspace(0, 1, int(0.01 * RATE))
    peak = np.max(np.abs(mix)) or 1.0
    return np.tanh(mix / peak * 1.2) * 0.6


def write_wav(samples: np.ndarray, path: Path) -> None:
    stereo = np.repeat(samples[:, None], 2, axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    path.write_bytes(buf.getvalue())


# --------------------------------------------------------------------------- pieces

def guide_music_classic(seconds: float = 64.0) -> np.ndarray:
    """The original mellow synth bed (kept for reference / overrides)."""
    chords = [["F3", "A3", "C4", "E4"], ["E3", "G3", "B3", "D4"], ["D3", "F3", "A3", "C4"], ["C3", "E3", "G3", "B3"]]
    return render_track(chords, bpm=96, bars_per_chord=2, seconds=seconds, seed=3, drums=True, bright=1800)


# --------------------------------------------------------------------------- fantasy synthwave
# An original piece for the guide channel: a lilting 12/8 fantasy-RPG feel (modal harmony,
# harp arpeggios, a breathy flute lead, crystal bells) dressed in synthwave pads and drums,
# with a short palm-muted metal section before it loops. No melodies are borrowed.

def _pluck(freq: float, seconds: float, rng, brightness: float = 0.5, decay: float = 0.996) -> np.ndarray:
    """Karplus-Strong plucked string (harp / harpsichord-ish), vectorised per period."""
    n = int(seconds * RATE)
    period = max(2, int(RATE / freq))
    buf = rng.uniform(-1, 1, period)
    buf = _lowpass(buf, 2000 + 9000 * brightness) if period > 8 else buf
    # y[i] = decay * (y[i-N] + y[i-N-1]) / 2, computed one period at a time
    out = np.zeros(n + period + 1)
    out[1:period + 1] = buf
    i = period + 1
    while i < n + 1:
        k = min(period, n + 1 - i)
        out[i:i + k] = decay * 0.5 * (out[i - period:i - period + k] + out[i - period - 1:i - period - 1 + k])
        i += k
    out = out[1:]
    out = out[:n]
    return out * _env(n, 0.002, min(0.25, seconds / 3))


def _flute(freq: float, seconds: float, rng) -> np.ndarray:
    t = _t(seconds)
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.3 * t) * np.clip((t - 0.25) / 0.3, 0, 1)
    phase = 2 * np.pi * freq * np.cumsum(vib) / RATE
    tone = np.sin(phase) + 0.18 * np.sin(2 * phase) + 0.06 * np.sin(3 * phase)
    breath = _lowpass(rng.normal(0, 1, len(t)), 3500) * 0.05
    return (tone + breath) * _env(len(t), 0.07, 0.18)


def _bell(freq: float, seconds: float) -> np.ndarray:
    t = _t(seconds)
    mod = np.sin(2 * np.pi * freq * 3.5 * t) * 2.2 * np.exp(-t * 3)
    return np.sin(2 * np.pi * freq * t + mod) * np.exp(-t * 2.4)


def _power_chord(freq: float, seconds: float, mute: bool) -> np.ndarray:
    """Distorted root+fifth+octave 'guitar'; palm-muted chugs are short and dark."""
    raw = _saw(freq, seconds, 0.003) + 0.8 * _saw(freq * 1.4983, seconds, 0.003) + 0.5 * _saw(freq * 2, seconds)
    env = _env(len(raw), 0.004, 0.03) * (np.exp(-_t(seconds) * (14 if mute else 1.2)))
    x = np.tanh(raw * env * 7.0)
    x = _lowpass(x, 1700 if mute else 3200)
    return x - _lowpass(x, 110)          # trim the mud


def _hall(x: np.ndarray, mix: float = 0.35) -> np.ndarray:
    """Cheap room: a handful of decaying, slightly filtered reflections."""
    y = x.copy()
    for delay, gain in ((0.031, 0.5), (0.047, 0.42), (0.071, 0.36), (0.113, 0.3), (0.173, 0.24), (0.263, 0.18)):
        d = int(delay * RATE)
        y[d:] += _lowpass(x[:-d], 5000) * gain * mix
    return y


# chords per bar (D Dorian with a major V for the cadence) and an original flute melody,
# written as (start, length, note) in eighth notes of a 12/8 bar
FANTASY_CHORDS = [
    ["D3", "F3", "A3"], ["C3", "E3", "G3"], ["G2", "B2", "D3"], ["A2", "C3", "E3"],
    ["D3", "F3", "A3"], ["A#2", "D3", "F3"], ["C3", "E3", "G3"], ["A2", "C#3", "E3"],
]
FANTASY_MELODY = [
    [(0, 3, "A4"), (3, 2, "F5"), (5, 1, "E5"), (6, 3, "D5"), (9, 3, "A4")],
    [(0, 2, "G4"), (2, 1, "A4"), (3, 3, "C5"), (6, 3, "E5"), (9, 2, "D5"), (11, 1, "C5")],
    [(0, 3, "B4"), (3, 3, "D5"), (6, 4, "G5"), (10, 1, "F5"), (11, 1, "E5")],
    [(0, 6, "E5"), (6, 3, "C5"), (9, 3, "A4")],
    [(0, 2, "D5"), (2, 1, "E5"), (3, 3, "F5"), (6, 3, "A5"), (9, 2, "G5"), (11, 1, "F5")],
    [(0, 3, "F5"), (3, 3, "D5"), (6, 3, "A#4"), (9, 3, "C5")],
    [(0, 2, "E5"), (2, 1, "F5"), (3, 3, "G5"), (6, 3, "E5"), (9, 3, "C5")],
    [(0, 3, "C#5"), (3, 3, "E5"), (6, 6, "A5")],
]


def guide_music(seconds: float | None = None) -> np.ndarray:
    """~76 s loop: A) harp + pads, B) flute melody + drums, C) metal-tinged reprise."""
    rng = np.random.default_rng(9)
    eighth = 60.0 / 76 / 3            # dotted quarter = 76 bpm, 12/8
    bar = eighth * 12
    bars = 24
    total = bars * bar
    n = int(total * RATE) + RATE
    harp, flute, lead, pad, bass, bells, gtr, drums = (np.zeros(n) for _ in range(8))

    def at(b, e=0.0):
        return b * bar + e * eighth

    up, down = [0, 1, 2, 3, 2, 1], [3, 2, 1, 0, 1, 2]
    for b in range(bars):
        section = b // 8                      # 0 = A, 1 = B, 2 = C (metal)
        chord = FANTASY_CHORDS[b % 8]
        root = _note(chord[0])
        tones = [_note(x) * 2 for x in chord] + [_note(chord[0]) * 4]

        # pad (whole bar, detuned saws, darker in A)
        p_ = sum(_saw(_note(x), bar, 0.004) for x in chord) / 3 * _env(int(bar * RATE), 0.4, 0.5)
        _place(pad, p_, at(b))

        # harp arpeggio: rolling eighths, up then down
        if section != 2 or b % 2 == 0:
            for e in range(12):
                f = tones[(up + down)[e]] * (2 if section == 1 and e % 6 == 0 else 1)
                _place(harp, _pluck(f, 1.1, rng, 0.55) * (0.9 if e % 3 == 0 else 0.6), at(b, e))

        # bass: dotted quarters, octave jump on the last one
        for k in range(4):
            f = root / 2 * (2 if k == 3 else 1)
            s_ = _saw(f, eighth * 2.6) * _env(int(eighth * 2.6 * RATE), 0.005, 0.08)
            _place(bass, s_, at(b, k * 3))

        # crystal bells on the first beat of every other bar
        if b % 2 == 0:
            for j, x in enumerate(chord):
                _place(bells, _bell(_note(x) * 4, 2.5) * 0.5, at(b, j * 0.5))

        # melody: flute in B, square lead (an octave up, doubled by flute) in C
        if section >= 1:
            for start, length, note in FANTASY_MELODY[b % 8]:
                dur = length * eighth * 0.95
                f = _note(note)
                _place(flute, _flute(f, dur + 0.15, rng), at(b, start))
                if section == 2:
                    sq = _square(f * 2, dur, 0.3) * _env(int(dur * RATE), 0.01, 0.06)
                    t_ = _t(dur)
                    sq *= 1 + 0.15 * np.sin(2 * np.pi * 6 * t_) * np.clip((t_ - 0.2) / 0.2, 0, 1)
                    _place(lead, sq, at(b, start))

        # drums: none for the first 4 bars, half-time in the rest, double kick in C
        if b >= 4:
            for beat in range(4):
                e0 = beat * 3
                if section == 2 and b % 2 == 1:
                    for sub in range(6):           # sixteenth-triplet double kick, every other bar
                        _place(drums, _kick() * 0.55, at(b, e0 + sub * 0.5))
                elif beat in (0, 2) or (beat == 3 and b % 2) or section == 2:
                    _place(drums, _kick(), at(b, e0))
                if beat in (1, 3):
                    _place(drums, _snare(rng) * 0.9, at(b, e0))
                for sub in range(3):
                    _place(drums, _hat(rng) * (0.5 if sub == 0 else 0.3), at(b, e0 + sub))

        # metal: chugging palm-mutes with an open power chord on each half bar
        if section == 2:
            for e in range(12):
                open_ = e in (0, 6)
                dur = eighth * (2.8 if open_ else 0.9)
                if open_ or e % 3 != 2:
                    _place(gtr, _power_chord(root / 2 if root > 140 else root, dur, not open_), at(b, e))

    mix = (_hall(harp, 0.5) * 0.30 + _hall(flute, 0.45) * 0.30 + _hall(lead, 0.3) * 0.06
           + _lowpass(pad, 1600) * 0.16 + _lowpass(bass, 600) * 0.30 + _hall(bells, 0.5) * 0.08
           + gtr * 0.11 + drums * 0.22)
    mix = mix[: int(total * RATE)]
    # seamless loop: blend the very end into silence over 40 ms and ease in
    f = int(0.04 * RATE)
    mix[-f:] *= np.linspace(1, 0, f)
    mix[:f] *= np.linspace(0, 1, f)
    peak = np.max(np.abs(mix)) or 1.0
    return np.tanh(mix / peak * 1.3) * 0.62


JINGLES = [
    # (chords, bpm, seed, brightness)
    ([["A2", "C3", "E3", "A3"], ["F2", "A2", "C3", "F3"], ["G2", "B2", "D3", "G3"], ["E2", "G#2", "B2", "E3"]], 118, 11, 2600),
    ([["D3", "F3", "A3", "D4"], ["A#2", "D3", "F3", "A#3"], ["C3", "E3", "G3", "C4"], ["A2", "C#3", "E3", "A3"]], 126, 12, 3000),
    ([["C3", "E3", "G3", "B3"], ["A2", "C3", "E3", "G3"], ["F2", "A2", "C3", "E3"], ["G2", "B2", "D3", "F3"]], 110, 13, 2200),
]


def jingle(i: int, seconds: float = 10.0) -> np.ndarray:
    chords, bpm, seed, bright = JINGLES[i % len(JINGLES)]
    beat = 60 / bpm
    bpc = 1 if seconds / (beat * 4) < 6 else 2
    return render_track(chords, bpm=bpm, bars_per_chord=bpc, seconds=seconds, seed=seed, bright=bright,
                        fade_out=1.2)


# Original, generic pop progressions for commercial jingles (no melodies are borrowed).
AD_PROGRESSIONS = [
    [["C3", "E3", "G3", "C4"], ["G2", "B2", "D3", "G3"], ["A2", "C3", "E3", "A3"], ["F2", "A2", "C3", "F3"]],
    [["D3", "F#3", "A3", "D4"], ["B2", "D3", "F#3", "B3"], ["G2", "B2", "D3", "G3"], ["A2", "C#3", "E3", "A3"]],
    [["E3", "G#3", "B3", "E4"], ["C#3", "E3", "G#3", "C#4"], ["A2", "C#3", "E3", "A3"], ["B2", "D#3", "F#3", "B3"]],
    [["F2", "A2", "C3", "F3"], ["D3", "F3", "A3", "D4"], ["A#2", "D3", "F3", "A#3"], ["C3", "E3", "G3", "C4"]],
    [["G2", "B2", "D3", "G3"], ["E2", "G2", "B2", "E3"], ["C3", "E3", "G3", "C4"], ["D3", "F#3", "A3", "D4"]],
    [["A2", "C3", "E3", "A3"], ["G2", "B2", "D3", "G3"], ["F2", "A2", "C3", "F3"], ["E2", "G#2", "B2", "E3"]],
]


def ad_jingle(seed: int, seconds: float) -> np.ndarray:
    """A bright, bouncy jingle for a fake commercial; seed picks progression, tempo and tone."""
    rng = np.random.default_rng(seed)
    chords = AD_PROGRESSIONS[seed % len(AD_PROGRESSIONS)]
    bpm = float(rng.choice([112, 120, 126, 132, 140]))
    bright = float(rng.choice([2400, 2800, 3200, 3600]))
    beat = 60 / bpm
    bpc = 1 if seconds / (beat * 4) < 8 else 2
    return render_track(chords, bpm=bpm, bars_per_chord=bpc, seconds=seconds, seed=seed, bright=bright,
                        fade_out=min(1.5, seconds / 6))
