"""Short original music beds for cards, promos, ads and intermissions.

Six styles, each with its own groove: jrpg (arpeggios, flute, bells), metal (palm-muted
chugs, double kick), rock (open power chords), punk (fast down-strokes, punk beat),
reggae (one-drop drums, off-beat skank, melodica) and ska (off-beat upstrokes, walking
bass, horn stabs). Progressions are generic, and melodies are made up from short
motifs by a seeded random walk; nothing is borrowed from existing music.
"""

from __future__ import annotations

import math

import numpy as np

from .synth import (RATE, _bell, _env, _flute, _hall, _hat, _kick, _lowpass, _place, _pluck, _power_chord,
                    _saw, _snare, _square, _t)

GENRES = ["jrpg", "metal", "rock", "punk", "reggae", "ska"]
# XCable's music: groovy, bass-led jazz. lounge (walking upright bass, brushes, swung ride, Rhodes,
# breathy sax), groove (syncopated slap bass, tight funk drums with ghost notes, Rhodes stabs, sax),
# velvet (slow late-night: lazy upright, rimshots, Rhodes pad, vibraphone)
JAZZ_GENRES = ["lounge", "groove", "velvet"]


def edition_genres() -> list[str]:
    from . import paths

    return JAZZ_GENRES if paths.XCABLE else GENRES

MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]
DORIAN = [0, 2, 3, 5, 7, 9, 10]
PHRYGIAN = [0, 1, 3, 5, 7, 8, 10]

# (scale, [progressions as scale degrees 1-7], bpm range, swing)
STYLE = {
    "jrpg":   (DORIAN,   [[1, 7, 4, 5], [1, 6, 7, 1], [4, 5, 3, 6]], (104, 128), 0.0),
    "metal":  (PHRYGIAN, [[1, 2, 1, 7], [1, 6, 7, 1], [1, 6, 4, 5]], (150, 176), 0.0),
    "rock":   (MAJOR,    [[1, 4, 5, 4], [1, 7, 4, 1], [6, 4, 1, 5]], (118, 138), 0.0),
    "punk":   (MAJOR,    [[1, 5, 6, 4], [1, 4, 1, 5], [4, 1, 5, 6]], (176, 200), 0.0),
    "reggae": (MAJOR,    [[1, 4, 1, 5], [2, 5, 1, 1], [6, 4, 1, 5]], (70, 84), 0.12),
    "ska":    (MAJOR,    [[1, 6, 2, 5], [1, 4, 5, 1], [1, 3, 4, 5]], (148, 168), 0.0),
    # jazz: generic changes (ii-V-I, turnarounds, modal vamps), seventh chords, swung eighths
    "lounge": (MAJOR,    [[2, 5, 1, 6], [1, 6, 2, 5], [3, 6, 2, 5]], (112, 136), 0.17),
    "groove": (DORIAN,   [[1, 4, 1, 4], [1, 1, 4, 5], [1, 7, 4, 1]], (94, 106), 0.05),
    "velvet": (DORIAN,   [[1, 4, 7, 3], [2, 5, 1, 1], [1, 6, 2, 5]], (66, 78), 0.15),
}
JAZZ = set(JAZZ_GENRES)
TONICS = [40, 42, 43, 45, 47]        # E2 .. B2 as the bass root (MIDI)


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _chord(scale: list[int], root_midi: int, degree: int) -> list[int]:
    """Triad on a scale degree, as MIDI notes."""
    i = degree - 1
    out = []
    for k in (0, 2, 4):
        j = i + k
        out.append(root_midi + scale[j % 7] + 12 * (j // 7))
    return out


def _chord7(scale: list[int], root_midi: int, degree: int) -> list[int]:
    """Seventh chord on a scale degree (jazz voicings), as MIDI notes."""
    i = degree - 1
    return [root_midi + scale[(i + k) % 7] + 12 * ((i + k) // 7) for k in (0, 2, 4, 6)]


def _rhodes(notes: list[int], dur: float, vel: float = 1.0) -> np.ndarray:
    """Electric-piano tone: a bell-ish FM attack that mellows, with a gentle tremolo."""
    t = _t(dur)
    x = np.zeros(len(t))
    for n in notes:
        f = _hz(n)
        mod = 1.4 * np.exp(-t * 7)
        x += np.sin(2 * np.pi * f * t + mod * np.sin(2 * np.pi * f * t)) * np.exp(-t * 1.8)
    x *= 1 + 0.12 * np.sin(2 * np.pi * 4.5 * t)
    return x * _env(len(t), 0.003, min(0.09, dur / 3)) * vel / max(1, len(notes)) * 2.2


def _upright(midi: int, dur: float, rng) -> np.ndarray:
    """Upright bass: a dark pluck with a woody thump under it."""
    f = _hz(midi)
    t = _t(dur)
    x = _pluck(f, dur, rng, 0.12, 0.994) * 1.7 + 0.8 * np.sin(2 * np.pi * f * t) * np.exp(-t * 5)
    return x * _env(len(t), 0.004, min(0.06, dur / 3))


def _slap(midi: int, dur: float, rng, pop: bool = False) -> np.ndarray:
    """Funk bass: bright thumb slap, or a snappy popped octave."""
    f = _hz(midi + (12 if pop else 0))
    t = _t(dur)
    x = _pluck(f, dur, rng, 0.75 if pop else 0.5, 0.993) + 0.9 * np.sin(2 * np.pi * _hz(midi) * t) * np.exp(-t * 6)
    return np.tanh(x * (2.2 if pop else 1.6)) * _env(len(t), 0.002, min(0.05, dur / 3))


def _sax(midi: int, dur: float, rng) -> np.ndarray:
    """Breathy saxophone-ish lead: reedy saw with delayed vibrato and a little breath noise."""
    f = _hz(midi)
    t = _t(dur)
    vib = 1 + 0.01 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.18) / 0.25, 0, 1)
    ph = np.cumsum(f * vib) / RATE
    x = 2 * (ph % 1) - 1
    x = np.tanh(_lowpass(x, 1700) * 2.2) + 0.08 * _lowpass(rng.standard_normal(len(t)), 3000)
    return x * _env(len(t), 0.04, min(0.12, dur / 3))


def _vibes(midi: int, dur: float) -> np.ndarray:
    t = _t(dur)
    return _bell(_hz(midi), dur) * (1 + 0.35 * np.sin(2 * np.pi * 5.5 * t))


def _ride(rng) -> np.ndarray:
    n = int(0.6 * RATE)
    x = rng.standard_normal(n)
    x = x - _lowpass(x, 5500)
    return x * np.exp(-np.arange(n) / RATE * 7) * 0.35


def _brush(rng) -> np.ndarray:
    n = int(0.22 * RATE)
    x = _lowpass(rng.standard_normal(n), 3500)
    return x * _env(n, 0.06, 0.12) * 0.5


def _clean_chord(notes: list[int], dur: float, rng, bright: float = 0.8) -> np.ndarray:
    x = sum(_pluck(_hz(n), dur, rng, bright, 0.985) for n in notes)
    return x * _env(len(x), 0.002, min(0.04, dur / 3))


def _organ(notes: list[int], dur: float) -> np.ndarray:
    t = _t(dur)
    x = sum(np.sin(2 * np.pi * _hz(n) * t) + 0.5 * np.sin(4 * np.pi * _hz(n) * t) + 0.25 * np.sin(6 * np.pi * _hz(n) * t)
            for n in notes)
    return x * _env(len(t), 0.01, min(0.05, dur / 3))


def _bass(midi: int, dur: float, rng, kind: str) -> np.ndarray:
    f = _hz(midi)
    if kind == "pluck":
        x = _pluck(f, dur, rng, 0.25, 0.997) * 1.6
    else:
        x = _lowpass(_saw(f, dur) * 0.6 + np.sin(2 * np.pi * f * _t(dur)), 700)
        x = x * _env(len(x), 0.005, min(0.06, dur / 3))
    return x


def _brass(midi: int, dur: float) -> np.ndarray:
    f = _hz(midi)
    x = _saw(f, dur, 0.004) + 0.7 * _saw(f * 1.003, dur)
    x = _lowpass(x, 2200) * _env(int(dur * RATE), 0.025, min(0.08, dur / 3))
    return np.tanh(x * 1.5)


def _dist_lead(midi: int, dur: float) -> np.ndarray:
    f = _hz(midi)
    t = _t(dur)
    vib = 1 + 0.008 * np.sin(2 * np.pi * 6 * t) * np.clip((t - 0.15) / 0.2, 0, 1)
    ph = 2 * np.pi * f * np.cumsum(vib) / RATE
    x = np.tanh((2 * ((ph / (2 * np.pi)) % 1) - 1) * 6)
    return _lowpass(x, 3600) * _env(len(t), 0.005, min(0.08, dur / 3))


def _melodica(midi: int, dur: float) -> np.ndarray:
    x = _lowpass(_square(_hz(midi), dur, 0.35), 2400)
    return x * _env(len(x), 0.03, min(0.1, dur / 3))


def _lead(style: str, midi: int, dur: float, rng) -> np.ndarray:
    if style == "jrpg":
        return _flute(_hz(midi), dur, rng) * 0.8 if rng.random() < 0.7 else _bell(_hz(midi), dur) * 0.6
    if style == "metal":
        return _dist_lead(midi, dur) * 0.5
    if style == "rock":
        return np.tanh(_pluck(_hz(midi), dur, rng, 0.9, 0.995) * 4) * 0.5
    if style == "reggae":
        return _melodica(midi, dur) * 0.55
    if style == "ska":
        return (_brass(midi, dur) + 0.6 * _brass(midi - 5, dur)) * 0.45     # horn section in fourths
    if style in ("lounge", "groove"):
        return _sax(midi, dur, rng) * 0.5
    if style == "velvet":
        return _vibes(midi, dur) * 0.55
    return np.tanh(_pluck(_hz(midi), dur, rng, 0.9, 0.995) * 5) * 0.4         # punk


def _motif(rng, length: int, rest: float) -> list[tuple[int, int]]:
    """(eighth-note position, step in the pentatonic) pairs for one bar."""
    out, pos, step = [], 0, int(rng.integers(0, 4))
    while pos < length:
        if rng.random() > rest:
            out.append((pos, step))
        step = max(-2, min(7, step + int(rng.choice([-2, -1, -1, 1, 1, 2, 0]))))
        pos += int(rng.choice([1, 1, 2, 2, 3]))
    return out


def compose(style: str, seed: int, seconds: float) -> np.ndarray:
    scale, progs, (lo, hi), swing = STYLE[style]
    rng = np.random.default_rng(seed)
    prog = progs[seed % len(progs)]
    bpm = float(rng.integers(lo, hi + 1))
    beat = 60.0 / bpm
    root = int(rng.choice(TONICS))
    bar = beat * 4
    bars = max(2, math.ceil(seconds / bar))
    n = int(seconds * RATE)
    drums, bass, rhythm, lead = (np.zeros(n + RATE) for _ in range(4))
    kick, hat = _kick(), _hat(rng)
    snr = _snare(rng)
    rim = _snare(rng)[: int(0.05 * RATE)] * _env(int(0.05 * RATE), 0.0, 0.03)     # rim click
    penta = [0, 2, 4, 7, 9] if scale is MAJOR else [0, 3, 5, 7, 10]
    rest = {"punk": 0.6, "metal": 0.35, "reggae": 0.55, "ska": 0.4, "lounge": 0.45, "groove": 0.5,
            "velvet": 0.58}.get(style, 0.35)
    ride, brush = _ride(rng), _brush(rng)
    # longer pieces change section every 8 bars: new chords and new motifs (same key and tempo),
    # with a drum breakdown in the third section, so a 2-3 minute track doesn't just loop
    sections: dict[int, tuple[list[int], list]] = {}

    def section(b: int):
        k = b // 8 if bars > 12 else 0
        if k not in sections:
            srng = np.random.default_rng(seed * 101 + k)
            sp = progs[(seed + k) % len(progs)]
            sections[k] = (sp, [_motif(srng, 8, rest) for _ in range(2)])
        return k, sections[k]

    def sw(k8: int) -> float:                    # swung eighth position, in seconds
        return k8 * beat / 2 + (swing * beat if k8 % 2 else 0)

    for b in range(bars):
        t0 = b * bar
        sec, (prog, motifs) = section(b)
        breakdown = bars > 12 and sec % 4 == 2
        deg = prog[b % len(prog)]
        ch = _chord(scale, root + 12, deg)       # chord voicing around E3..B3
        ch7 = _chord7(scale, root + 12, deg)
        nxt_root = _chord(scale, root, prog[(b + 1) % len(prog)])[0]
        broot = _chord(scale, root, deg)[0]
        # ---- drums
        if breakdown:                            # breakdown: just hats (and the odd kick)
            for k in range(8):
                _place(drums, hat * 0.3, t0 + sw(k))
            if b % 2 == 1:
                _place(drums, kick * 0.6, t0 + 3 * beat)
        elif style == "lounge":                  # swung ride "ding, ding-a ding", brushes on 2 and 4
            for k in (0, 2, 3, 4, 6, 7):
                _place(drums, ride * (0.55 if k in (0, 4) else 0.42), t0 + sw(k))
            for k in (1, 3):
                _place(drums, brush, t0 + k * beat)
            for k in (0, 2):
                _place(drums, kick * 0.25, t0 + k * beat)          # feathered kick
        elif style == "groove":                  # funk: syncopated kick, backbeat, ghost notes, 16th hats
            for k16 in (0, 6, 8, 11):
                _place(drums, kick * (1.0 if k16 in (0, 8) else 0.75), t0 + k16 * beat / 4)
            for k16 in (4, 12):
                _place(drums, snr * 0.85, t0 + k16 * beat / 4)
            for k16 in (7, 10, 15):
                _place(drums, snr * 0.18, t0 + k16 * beat / 4)     # ghost notes
            for k16 in range(16):
                _place(drums, hat * (0.4 if k16 % 4 == 2 else 0.22), t0 + k16 * beat / 4 + (swing * beat / 2 if k16 % 2 else 0))
        elif style == "velvet":                  # slow: kick on 1 and the and-of-3, rim on 2 and 4, soft ride
            _place(drums, kick * 0.7, t0)
            _place(drums, kick * 0.5, t0 + sw(5))
            for k in (1, 3):
                _place(drums, rim * 0.8, t0 + k * beat)
            for k in (0, 2, 3, 4, 6, 7):
                _place(drums, ride * 0.3, t0 + sw(k))
        elif style == "reggae":                    # one drop: kick + rim on 3, hats on eighths
            _place(drums, kick * 0.9, t0 + 2 * beat)
            _place(drums, rim * 0.9, t0 + 2 * beat)
            for k in range(8):
                _place(drums, hat * (0.25 if k % 2 else 0.4), t0 + sw(k))
        elif style == "punk":                    # kick on the beat, snare on every off-beat
            for k in range(8):
                _place(drums, (kick if k % 2 == 0 else snr * 0.7), t0 + k * beat / 2)
                _place(drums, hat * 0.3, t0 + k * beat / 2)
        else:
            for k in range(4):
                if k in (0, 2):
                    _place(drums, kick, t0 + k * beat)
                if k in (1, 3):
                    _place(drums, snr * 0.8, t0 + k * beat)
            if style == "metal":                 # double kick sixteenths
                for k in range(16):
                    _place(drums, kick * 0.55, t0 + k * beat / 4)
            sub = 16 if style in ("metal", "jrpg") else 8
            for k in range(sub):
                _place(drums, hat * (0.3 if k % 2 else 0.45), t0 + k * bar / sub)
        # ---- bass
        if style == "lounge":                    # walking quarters, last beat leads into the next chord
            third = ch7[1] - ch7[0]
            walk = [broot, broot + third, broot + 7, nxt_root + (1 if rng.random() < 0.5 else -1)]
            for k, note in enumerate(walk):
                _place(bass, _upright(note, beat * 0.95, rng) * 1.55, t0 + k * beat)
        elif style == "groove":                  # the star: syncopated slap line with popped octaves
            pattern = [(0, 0, 3, False), (3, 0, 1, False), (6, 12, 1, True), (8, 7, 2, False),
                       (10, 10, 1, False), (11, 12, 1, True), (14, -2, 1, False), (15, -1, 1, False)]
            if b % 2:                            # vary every other bar
                pattern = pattern[:4] + [(10, 5, 1, False), (12, 7, 2, False), (14, 10, 1, True), (15, 12, 1, False)]
            for pos, iv, ln, pop in pattern:
                _place(bass, _slap(broot + iv, ln * beat / 4 * 0.95, rng, pop) * 1.1, t0 + pos * beat / 4)
        elif style == "velvet":                  # lazy: root held, fifth, pickup into the next chord
            for pos, note, ln in ((0, broot, 2.5), (5, broot + 7, 1.0), (7, nxt_root - 1, 0.5)):
                _place(bass, _upright(note, ln * beat, rng) * 1.7, t0 + sw(pos))
        elif style == "reggae":                    # deep syncopated line: root, fifth, octave
            for pos, iv, ln in ((0, 0, 1.5), (3, 7, 0.5), (4, 12, 1.0), (6, 7, 0.5), (7, 0, 0.5)):
                _place(bass, _bass(broot + iv, ln * beat, rng, "pluck") * 1.2, t0 + pos * beat / 2)
        elif style == "ska":                     # walking quarters through the chord
            for k, iv in enumerate((0, 4 if scale is MAJOR else 3, 7, 9)):
                _place(bass, _bass(broot + iv, beat * 0.9, rng, "pluck"), t0 + k * beat)
        elif style == "jrpg":
            for k in range(4):
                _place(bass, _bass(broot + (0 if k % 2 == 0 else 7), beat * 0.9, rng, "saw") * 0.8, t0 + k * beat)
        else:                                    # rock / punk / metal: driving eighths
            for k in range(8):
                _place(bass, _bass(broot, beat / 2 * 0.9, rng, "saw") * 0.8, t0 + k * beat / 2)
        # ---- rhythm part
        if style == "jrpg":                      # sixteenth arpeggio
            notes = ch + [ch[0] + 12]
            for k in range(16):
                s = _square(_hz(notes[k % 4] + 12), beat / 4 * 0.9, 0.25)
                _place(rhythm, _lowpass(s, 3000) * _env(len(s), 0.002, 0.03) * 0.18, t0 + k * beat / 4)
        elif style == "metal":                   # palm-muted sixteenths, open hit at the bar start
            for k in range(16):
                mute = k not in (0, 8)
                _place(rhythm, _power_chord(_hz(broot + 12), beat / 4 * (0.9 if mute else 3.5), mute) * 0.35,
                       t0 + k * beat / 4)
        elif style == "rock":
            for k in range(8):
                _place(rhythm, _power_chord(_hz(broot + 12), beat / 2 * 0.95, k % 4 == 3) * 0.3, t0 + k * beat / 2)
        elif style == "punk":
            for k in range(8):
                _place(rhythm, _power_chord(_hz(broot + 12), beat / 2 * 0.8, False) * 0.3, t0 + k * beat / 2)
        elif style == "lounge":                  # Rhodes comping: Charleston rhythm on the seventh chord
            for k8, ln in ((0, 1.2), (3, 1.4)):
                _place(rhythm, _rhodes(ch7, ln * beat, 0.9), t0 + sw(k8))
        elif style == "groove":                  # Rhodes stabs on the off-sixteenths
            for k16 in (2, 7, 10):
                _place(rhythm, _rhodes([x + 12 for x in ch7[1:]], beat * 0.35, 0.8), t0 + k16 * beat / 4)
        elif style == "velvet":                  # a held Rhodes chord with a slow arpeggio on top
            _place(rhythm, _rhodes(ch7, bar * 0.98, 0.75), t0)
            for k in (2, 5):
                _place(rhythm, _rhodes([ch7[(k // 2) % 4] + 12], beat * 1.2, 0.5), t0 + sw(k))
        elif style == "reggae":                  # skank on 2 and 4, organ bubble on the off-beats
            for k in (1, 3):
                _place(rhythm, _clean_chord([x + 12 for x in ch], 0.14, rng) * 0.45, t0 + k * beat)
            for k in range(8):
                if k % 2:
                    _place(rhythm, _organ(ch, beat / 2 * 0.6) * 0.06, t0 + sw(k))
        elif style == "ska":                     # upstrokes on every off-beat
            for k in range(8):
                if k % 2:
                    _place(rhythm, _clean_chord([x + 12 for x in ch], 0.11, rng, 0.9) * 0.4, t0 + k * beat / 2)
        # ---- lead: two motifs, A A B A over four bars, moved to the chord
        if b % 4 == 3 and style in ("punk",):
            continue
        mot = motifs[1 if b % 4 == 2 else 0]
        base = ch[0] + 12
        for i, (pos, step) in enumerate(mot):
            nxt = mot[i + 1][0] if i + 1 < len(mot) else 8
            ln = max(1, nxt - pos) * beat / 2
            midi = base + penta[step % 5] + 12 * (step // 5)
            _place(lead, _lead(style, midi, ln * 0.95, rng), t0 + sw(pos))

    if style in JAZZ:                            # bass up front: it's what these are about
        mix = drums * 0.5 + bass * 0.8 + rhythm * 0.5 + _hall(lead, 0.35) * 0.45
    else:
        mix = drums * 0.55 + bass * 0.5 + rhythm * 0.55 + _hall(lead, 0.3) * 0.5
    mix = _hall(mix, 0.12)[:n]
    fade = int(min(1.2, seconds / 8) * RATE)
    mix[-fade:] *= np.linspace(1, 0, fade)
    mix[: int(0.01 * RATE)] *= np.linspace(0, 1, int(0.01 * RATE))
    peak = np.max(np.abs(mix)) or 1.0
    return np.tanh(mix / peak * 1.3) * 0.6
