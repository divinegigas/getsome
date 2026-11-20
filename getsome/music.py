"""The guide channel's soundtrack: ten original tracks in the spirit of 90s / early-2000s
RPG soundtracks, part synthwave, part metal.

Nothing here is borrowed from any real game. Each track is a short recipe (key and mode,
time signature, tempo, chord progression, lead voice, drum style, how much metal), and
the composer below writes the melody from short motifs that repeat and develop over
the chords, then arranges it:

    A  intro      pads + arpeggios, drums creep in
    B  theme      the melody
    C  reprise    melody an octave up, doubled; guitars / double kick by the metal amount
"""

from __future__ import annotations

import functools
import random
from dataclasses import dataclass

import numpy as np

from . import synth as S
from .synth import RATE, _env, _hall, _kick, _lowpass, _place, _saw, _snare, _square, _t

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MODES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "harmonic": [0, 2, 3, 5, 7, 8, 11],
    "phrygian_dom": [0, 1, 4, 5, 7, 8, 10],
}
# meter -> (beats per bar, grid units per beat); the grid unit is an eighth note
METERS = {"4/4": (4, 2), "3/4": (3, 2), "6/8": (2, 3), "12/8": (4, 3)}
RHYTHMS = {
    "4/4": [[(0, 2), (2, 2), (4, 3), (7, 1)], [(0, 3), (3, 1), (4, 2), (6, 2)], [(0, 1), (1, 1), (2, 2), (4, 4)],
            [(0, 2), (2, 1), (3, 1), (4, 2), (6, 2)], [(0, 4), (4, 2), (6, 2)], [(0, 1), (1, 1), (2, 1), (3, 1), (4, 4)]],
    "3/4": [[(0, 2), (2, 2), (4, 2)], [(0, 3), (3, 1), (4, 2)], [(0, 4), (4, 2)], [(0, 1), (1, 1), (2, 2), (4, 2)]],
    "6/8": [[(0, 3), (3, 2), (5, 1)], [(0, 2), (2, 1), (3, 3)], [(0, 1), (1, 1), (2, 1), (3, 3)], [(0, 3), (3, 3)]],
    "12/8": [[(0, 3), (3, 2), (5, 1), (6, 3), (9, 3)], [(0, 2), (2, 1), (3, 3), (6, 3), (9, 2), (11, 1)],
             [(0, 3), (3, 3), (6, 4), (10, 1), (11, 1)], [(0, 6), (6, 3), (9, 3)]],
}


@dataclass(frozen=True)
class Track:
    key: str
    name: str
    tonic: str            # e.g. "D"
    mode: str
    meter: str
    bpm: float            # beats per minute (a beat is a dotted quarter in 6/8 and 12/8)
    progression: tuple    # 8 scale degrees (1-7); "5M" forces a major chord (e.g. a harmonic cadence)
    lead: str             # flute | square | saw | bell | guitar | pluck
    arp: str              # harp | square | none
    drums: str            # none | ballad | synth | march | metal
    metal: float          # 0..1 - guitars and double kick in the reprise (and earlier when high)
    seed: int


TRACKS: list[Track] = [
    Track("crystal", "Crystal Overture", "D", "dorian", "12/8", 76, (1, 7, 4, 5, 1, 6, 7, "5M"), "flute", "harp", "ballad", 0.35, 1),
    Track("skyship", "Skyship Chase", "E", "aeolian", "4/4", 152, (1, 6, 7, 1, 4, 6, "5M", "5M"), "saw", "square", "metal", 0.7, 2),
    Track("dusk", "Village at Dusk", "G", "mixolydian", "3/4", 92, (1, 7, 4, 1, 1, 4, 7, 1), "flute", "harp", "ballad", 0.0, 3),
    Track("clockwork", "Clockwork Tower", "A", "harmonic", "4/4", 112, (1, 6, 4, "5M", 1, 6, 2, "5M"), "bell", "square", "synth", 0.25, 4),
    Track("caravan", "Desert Caravan", "E", "phrygian_dom", "6/8", 100, (1, 2, 1, 7, 1, 2, 4, 1), "pluck", "harp", "march", 0.15, 5),
    Track("boss", "The Tyrant Awakens", "C", "harmonic", "4/4", 168, (1, 6, 1, 6, 4, 2, "5M", "5M"), "guitar", "square", "metal", 1.0, 6),
    Track("moonlit", "Moonlit Sea", "F", "lydian", "12/8", 62, (1, 2, 1, 2, 6, 5, 4, 5), "flute", "harp", "ballad", 0.0, 7),
    Track("neon", "Neon Kingdom", "B", "dorian", "4/4", 116, (1, 4, 7, 4, 1, 4, 6, 7), "square", "square", "synth", 0.4, 8),
    Track("ruins", "Ancient Ruins", "D", "aeolian", "4/4", 84, (1, 7, 6, 7, 1, 4, 6, "5M"), "flute", "harp", "ballad", 0.15, 9),
    Track("airship", "Airship Over the Clouds", "D", "major", "3/4", 138, (1, 5, 6, 4, 1, 4, 2, 5), "saw", "harp", "march", 0.45, 10),
]
TRACK_BY_KEY = {t.key: t for t in TRACKS}
SHUFFLE = "shuffle"
CLASSIC = "classic"


# --------------------------------------------------------------------------- theory

def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _tonic_midi(tonic: str, octave: int = 4) -> int:
    return NOTE_NAMES.index(tonic) + 12 * (octave + 1)


class Harmony:
    def __init__(self, t: Track):
        self.scale = MODES[t.mode]
        self.root = _tonic_midi(t.tonic, 3)          # chord roots around octave 3

    def degree_midi(self, deg: int, base: int) -> int:
        """Scale degree (0-based, may be negative or > 6) relative to tonic `base`."""
        octave, idx = divmod(deg, 7)
        return base + 12 * octave + self.scale[idx]

    def chord(self, symbol) -> list[int]:
        s = str(symbol)
        deg = int(s[0]) - 1
        notes = [self.degree_midi(deg + k, self.root) for k in (0, 2, 4)]
        if s.endswith("M"):                          # force a major triad
            notes[1] = notes[0] + 4
            notes[2] = notes[0] + 7
        return notes

    def scale_midis(self, lo: int, hi: int) -> list[int]:
        out = []
        for octv in range(-1, 9):
            for st in self.scale:
                m = self.root - 12 + st + 12 * octv
                if lo <= m <= hi:
                    out.append(m)
        return sorted(set(out))


def compose_melody(t: Track, h: Harmony) -> list[list[tuple[int, int, int]]]:
    """8 bars of melody as (start_unit, length_units, midi). Motif-based: a two-bar idea,
    its sequence over the next chords, a contrasting idea, and a cadence on the tonic."""
    rng = random.Random(t.seed * 7919)
    beats, sub = METERS[t.meter]
    units = beats * sub
    lo, hi = 64, 84                                   # E4..C6: a comfortable lead range in any key
    scale = h.scale_midis(lo, hi)
    pats = RHYTHMS[t.meter]

    def make_motif():
        rhythm = [rng.choice(pats), rng.choice(pats)]
        # mostly steps, the odd leap, a gentle arch: up early, down late
        contour = [[rng.choice([1, 1, 2, -1, 3, 0] if j < len(r) / 2 else [-1, -1, -2, 1, -3, 0])
                    for j, _ in enumerate(r)] for r in rhythm]
        return rhythm, contour

    def stepped(i: int, step: int) -> int:
        """Index i moved by step, bouncing off the ends of the range instead of sticking."""
        j = i + step
        if j > len(scale) - 1:
            j = i - abs(step)
        if j < 0:
            j = i + abs(step)
        return max(0, min(len(scale) - 1, j))

    def realise(motif, chord_syms, start_midi):
        bars, cur, prev = [], start_midi, None
        for (rhythm, contour), sym in zip(zip(*motif), chord_syms):
            chord = h.chord(sym)
            pcs = {n % 12 for n in chord}
            tones = [m for m in scale if m % 12 in pcs]
            notes = []
            for (st, ln), step in zip(rhythm, contour):
                i = min(range(len(scale)), key=lambda k: abs(scale[k] - cur))
                if st % sub == 0:
                    # on the beat: the next chord tone in the motif's direction
                    up = [m for m in tones if m > cur]
                    dn = [m for m in tones if m < cur]
                    if step > 0:
                        cand = up[0] if up else dn[-1]
                    elif step < 0:
                        cand = dn[-1] if dn else up[0]
                    else:
                        cand = min(tones, key=lambda m: abs(m - cur))
                else:
                    cand = scale[stepped(i, step or rng.choice([-1, 1]))]
                if cand == cur and cand == prev:     # never three of the same note in a row
                    j = scale.index(cand) if cand in scale else i
                    cand = scale[stepped(j, rng.choice([-1, 1, 2]))]
                prev, cur = cur, cand
                notes.append((st, ln, cand))
            bars.append(notes)
        return bars, cur

    prog = list(t.progression)
    a = make_motif()
    b = make_motif()
    start = min((m for m in scale if m % 12 in {n % 12 for n in h.chord(prog[0])}),
                key=lambda m: abs(m - (lo + hi) // 2))
    bars12, cur = realise(a, prog[0:2], start)
    bars34, cur = realise(a, prog[2:4], cur + rng.choice([2, 3, -2]))   # the motif again, moved
    bars56, cur = realise(b, prog[4:6], cur)
    # cadence: a short pickup then a long tonic (or the chord's root) to round the phrase off
    first7, _ = realise(([rng.choice(pats)], [[1, -1, 1, -1, 1, -1][: len(pats[0])]]), prog[6:7], cur)
    last = h.chord(prog[7])
    land = min((m for m in scale if m % 12 == last[0] % 12), key=lambda m: abs(m - cur))
    bar8 = [(0, units - sub, land), (units - sub, sub, land)]
    return bars12 + bars34 + bars56 + first7 + [bar8]


# --------------------------------------------------------------------------- voices

@functools.lru_cache(maxsize=512)
def _pluck_c(midi: int, seconds: float) -> np.ndarray:
    return S._pluck(_freq(midi), seconds, np.random.default_rng(midi), 0.55)


@functools.lru_cache(maxsize=512)
def _flute_c(midi: int, seconds: float) -> np.ndarray:
    return S._flute(_freq(midi), seconds, np.random.default_rng(midi))


def _lead(kind: str, midi: int, dur: float) -> np.ndarray:
    f = _freq(midi)
    n = int(dur * RATE)
    tt = _t(dur)
    vib = 1 + 0.12 * np.sin(2 * np.pi * 5.8 * tt) * np.clip((tt - 0.18) / 0.2, 0, 1)
    if kind == "flute":
        return _flute_c(midi, round(dur, 2))
    if kind == "pluck":
        return _pluck_c(midi, round(dur + 0.4, 2)) * 1.4
    if kind == "bell":
        return S._bell(f, dur + 0.6) * 0.9
    if kind == "square":
        return _square(f, dur, 0.3) * _env(n, 0.01, 0.06) * vib * 0.5
    if kind == "saw":    # brassy synth lead: saw with a filter that opens on the attack
        x = _saw(f, dur, 0.004) * _env(n, 0.02, 0.08)
        return _lowpass(x, 2600) * 0.9
    if kind == "guitar":  # distorted lead guitar with vibrato
        ph = 2 * np.pi * f * np.cumsum(1 + 0.008 * np.sin(2 * np.pi * 6 * tt) * np.clip((tt - 0.15) / 0.2, 0, 1)) / RATE
        raw = (2 * ((ph / (2 * np.pi)) % 1.0) - 1) * _env(n, 0.01, 0.06)
        x = np.tanh(raw * 6)
        return _lowpass(x - _lowpass(x, 180), 3800) * 0.55
    return _square(f, dur) * _env(n, 0.01, 0.05) * 0.4


def _crash(rng) -> np.ndarray:
    tt = _t(1.6)
    nz = rng.normal(0, 1, len(tt))
    nz = nz - _lowpass(nz, 4000)
    return nz * np.exp(-tt * 2.5) * 0.5


# --------------------------------------------------------------------------- render

def render(t: Track) -> np.ndarray:
    rng = np.random.default_rng(t.seed)
    h = Harmony(t)
    beats, sub = METERS[t.meter]
    units = beats * sub
    beat_s = 60.0 / t.bpm
    unit_s = beat_s / sub
    bar_s = unit_s * units
    melody = compose_melody(t, h)
    # aim for roughly a minute to a minute and a half: 8-bar sections, repeated as needed
    per_section = 8
    reps = max(1, round(70 / (bar_s * per_section * 3)))
    sections = ["A"] * reps + ["B"] * reps + ["C"] * reps
    bars = len(sections) * per_section
    total = bars * bar_s
    n = int(total * RATE) + RATE
    lead, lead2, arp, pad, bass, drums, gtr = (np.zeros(n) for _ in range(7))

    def at(b, u=0.0):
        return b * bar_s + u * unit_s

    for b in range(bars):
        sec = sections[b // per_section]
        i8 = b % per_section
        chord = h.chord(t.progression[i8])

        # pad
        p_ = sum(_saw(_freq(m + 12), bar_s, 0.004) for m in chord) / 3 * _env(int(bar_s * RATE), 0.3, 0.4)
        _place(pad, p_, at(b))

        # arpeggio
        tones = [m + 12 for m in chord] + [chord[0] + 24]
        order = [0, 1, 2, 3, 2, 1]
        if t.arp != "none" and not (sec == "C" and t.metal > 0.6):
            for u in range(units):
                m = tones[order[u % len(order)]]
                if t.arp == "harp":
                    s_ = _pluck_c(m, 0.9) * (0.9 if u % sub == 0 else 0.6)
                else:
                    d = unit_s * 0.8
                    s_ = _square(_freq(m + 12), d, 0.25) * _env(int(d * RATE), 0.002, 0.04) * 0.35
                _place(arp, s_, at(b, u))

        # bass: on every beat, the root (octave bounce in faster tracks)
        for k in range(beats):
            m = chord[0] - 12 + (12 if (t.bpm > 120 and k % 2) else 0)
            d = beat_s * 0.85
            s_ = _saw(_freq(m), d) * _env(int(d * RATE), 0.005, 0.06)
            _place(bass, s_, at(b, k * sub))

        # melody in B and C
        if sec in ("B", "C"):
            for st, ln, m in melody[i8]:
                d = ln * unit_s * 0.95
                _place(lead, _lead(t.lead, m, d), at(b, st))
                if sec == "C":
                    second = "guitar" if t.metal >= 0.5 and t.lead != "guitar" else "square"
                    _place(lead2, _lead(second, m + 12, d) * 0.6, at(b, st))

        # drums
        style = t.drums
        if style != "none" and not (sec == "A" and i8 < 4 and b < per_section):
            for k in range(beats):
                u0 = k * sub
                if style == "ballad":
                    if k == 0:
                        _place(drums, _kick() * 0.8, at(b, u0))
                    if k == beats // 2 or (beats == 3 and k == 2):
                        _place(drums, _snare(rng) * 0.6, at(b, u0))
                elif style == "march":
                    _place(drums, _kick() * (0.9 if k == 0 else 0.5), at(b, u0))
                    for r in range(sub):
                        _place(drums, _snare(rng) * (0.45 if r == 0 and k else 0.18), at(b, u0 + r))
                else:   # synth / metal: backbeat
                    if k % 2 == 0:
                        _place(drums, _kick(), at(b, u0))
                    else:
                        _place(drums, _snare(rng) * 0.9, at(b, u0))
                    if style == "metal" or (sec == "C" and t.metal >= 0.4):
                        for r in range(sub * 2):            # double kick
                            if (b % 2 == 1) or style == "metal":
                                _place(drums, _kick() * 0.45, at(b, u0 + r / 2))
                for r in range(sub):
                    _place(drums, S._hat(rng) * (0.45 if r == 0 else 0.25), at(b, u0 + r))
            if i8 == 0 and sec == "C":
                _place(drums, _crash(rng), at(b))

        # guitars: in C by the metal amount; also under B for the heaviest tracks
        if t.metal > 0 and (sec == "C" or (sec == "B" and t.metal >= 0.7)):
            root = chord[0] - 12
            for u in range(units):
                open_ = u % (units // 2) == 0
                if open_ or (u % sub != sub - 1):
                    d = unit_s * (units / 2 * 0.95 if open_ else 0.9)
                    _place(gtr, S._power_chord(_freq(root), d, not open_), at(b, u))

    metal_gain = 0.06 + 0.10 * t.metal
    mix = (_hall(lead, 0.4) * 0.30 + _hall(lead2, 0.3) * 0.12 + _hall(arp, 0.45) * 0.20
           + _lowpass(pad, 1500) * 0.15 + _lowpass(bass, 650) * 0.28 + drums * 0.22 + gtr * metal_gain)
    mix = mix[: int(total * RATE)]
    f = int(0.04 * RATE)
    mix[-f:] *= np.linspace(1, 0, f)
    mix[:f] *= np.linspace(0, 1, f)
    peak = np.max(np.abs(mix)) or 1.0
    return np.tanh(mix / peak * 1.3) * 0.62
