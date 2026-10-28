#!/usr/bin/env python3
"""Synthesize a full-band test song (guitar, bass, drums, keys, vocal line)
with every instrument's notes known, plus each instrument as its own stem.

    python tests/make_band_song.py band.wav [--bpm 120] [--start 0.6]

Writes:
  band.wav                 the full mix
  band.json                answer key: beats, every instrument's note onsets
                           (the guitar's with pitches and chord sizes), and
                           the bars where the guitar is silent
  band_stems/<name>.wav    guitar, bass, drums, keys, vocals

The keys and vocals deliberately play in the guitar's register on other
rhythms, so a charter that charts "everything" is easy to tell apart from one
that charts only the guitar. tests/check_guitar.py does the scoring.
"""
import argparse
import json
import os

import numpy as np
import soundfile as sf
from scipy.signal import lfilter

SR = 44100


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def fade_out(sig, sec=0.03):
    n = min(len(sig), int(sec * SR))
    sig[-n:] *= np.linspace(1, 0, n)
    return sig


def string(freq, dur, rng, bright=0.5, sustain=0.996):
    """Karplus-Strong plucked string (sounds like a real guitar pluck)."""
    n = int(dur * SR)
    period = SR / freq
    N = int(period)
    frac = period - N
    burst = rng.uniform(-1, 1, N + 2)
    burst = lfilter([bright, 1 - bright], [1], burst)        # soften the pick noise
    x = np.zeros(n)
    x[: len(burst)] = burst[: n]
    a = np.zeros(N + 2)
    a[0] = 1.0
    a[N] = -sustain * (1 - frac)                               # fractional delay
    a[N + 1] = -sustain * frac
    y = lfilter([1.0], a, x)
    y = lfilter([0.5, 0.5], [1], y)
    return fade_out(y / (np.max(np.abs(y)) + 1e-9))


def guitar_note(midis, dur, rng, drive=0.0):
    sig = np.zeros(int(dur * SR))
    for i, m in enumerate(midis):                              # strum: 8 ms per string
        s = string(hz(m), dur - i * 0.008, rng, sustain=0.997 if drive else 0.995)
        sig[int(i * 0.008 * SR):][: len(s)] += s
    if drive:
        sig = np.tanh(drive * sig) / np.tanh(drive)
    return sig * 0.30


def keys_note(midi, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = hz(midi)
    partials = [(1, 1.0), (2, 0.45), (3, 0.25), (4.02, 0.12), (5.03, 0.06)]
    sig = sum(a * np.sin(2 * np.pi * f * h * t) * np.exp(-t * (1.5 + h)) for h, a in partials)
    sig *= np.minimum(t / 0.003, 1)
    return fade_out(sig * 0.16)


def vocal_note(midi, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = hz(midi) * (1 + 0.006 * np.sin(2 * np.pi * 5.5 * t) * np.minimum(t / 0.3, 1))
    phase = 2 * np.pi * np.cumsum(f) / SR
    # a few harmonics shaped like an "ah" vowel (formants near 700 / 1200 Hz)
    sig = 0
    for h in range(1, 12):
        fh = hz(midi) * h
        amp = np.exp(-((fh - 700) / 350) ** 2) + 0.6 * np.exp(-((fh - 1200) / 400) ** 2) + 0.3 / h
        sig = sig + amp * np.sin(h * phase)
    env = np.minimum(t / 0.06, 1) * np.minimum((dur - t) / 0.08, 1)  # soft, sung attack
    return np.clip(env, 0, 1) * sig * 0.05


def bass_note(midi, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * hz(midi) * t) + 0.35 * np.sin(4 * np.pi * hz(midi) * t)
    return fade_out(sig * np.exp(-t / 0.5) * np.minimum(t / 0.004, 1) * 0.35)


def drum(kind, rng):
    if kind == "kick":
        n = int(0.25 * SR); t = np.arange(n) / SR
        f = 50 + 90 * np.exp(-t / 0.03)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12) * 0.9
    if kind == "snare":
        n = int(0.2 * SR); t = np.arange(n) / SR
        return (rng.standard_normal(n) * 0.5 + np.sin(2 * np.pi * 190 * t)) * np.exp(-t / 0.06) * 0.35
    n = int(0.05 * SR); t = np.arange(n) / SR
    return rng.standard_normal(n) * np.exp(-t / 0.012) * 0.08


# ---- the song --------------------------------------------------------------
# (bar offset in beats, midi pitches, length in beats)
VERSE_RIFF = [(0, [52], .5), (.5, [55], .5), (1, [57], .25), (1.25, [59], .25), (1.5, [57], .5),
              (2, [55], .5), (2.5, [52], .5), (3, [50], .5), (3.5, [52], .5)]
CHORUS_CHORDS = [  # power chords: root, fifth, octave
    [(0, 45, 1.5), (1.5, 45, .5), (2, 48, 1), (3, 50, 1)],
    [(0, 43, 1.5), (1.5, 43, .5), (2, 41, 2)],
]
KEYS_VERSE = [(0.5, 67, .5), (1.5, 69, .5), (2.5, 71, .5), (3.75, 72, .25)]   # off-beat stabs
KEYS_BREAK = [(0, 64, 1), (1, 67, 1), (2, 71, 1), (3, 69, 1)]
VOCAL_LINE = [(0.75, 64, 1.25), (2.25, 62, .75), (3.25, 60, .75)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--start", type=float, default=0.6)
    args = ap.parse_args()
    rng = np.random.default_rng(3)

    parts = (["intro"] * 4 + ["verse"] * 8 + ["chorus"] * 8 + ["break"] * 4 +
             ["verse"] * 8 + ["chorus"] * 4)
    spb = 60.0 / args.bpm
    n_beats = len(parts) * 4
    total = args.start + n_beats * spb + 3.0
    names = ["guitar", "bass", "drums", "keys", "vocals"]
    stems = {k: np.zeros(int(total * SR)) for k in names}
    key = {k: [] for k in names}

    def at(beat):
        return args.start + beat * spb

    def add(name, sig, t, **info):
        i = int(round(t * SR))
        buf = stems[name]
        buf[i:i + len(sig)] += sig[: len(buf) - i]
        key[name].append(dict(time=float(t), **info))

    no_guitar_bars = []
    for bar, part in enumerate(parts):
        b0 = bar * 4
        # drums
        if part != "intro":
            for b in (0, 2):
                add("drums", drum("kick", rng), at(b0 + b))
            for b in (1, 3):
                add("drums", drum("snare", rng), at(b0 + b))
        for e in range(8):
            add("drums", drum("hat", rng), at(b0 + e / 2))
        # bass
        if part in ("verse", "chorus"):
            root = 40 if part == "verse" else (33 if bar % 2 == 0 else 31)
            for b in range(4):
                add("bass", bass_note(root + (12 if b == 3 else 0), spb * 0.9), at(b0 + b), pitch=root)
        # guitar
        if part == "verse":
            for off, mids, dur in VERSE_RIFF:
                add("guitar", guitar_note(mids, dur * spb * 1.1, rng), at(b0 + off),
                    pitches=mids, beats=dur)
        elif part == "chorus":
            for off, root, dur in CHORUS_CHORDS[bar % 2]:
                mids = [root, root + 7, root + 12]
                add("guitar", guitar_note(mids, dur * spb * 1.05, rng, drive=3.0), at(b0 + off),
                    pitches=mids, beats=dur)
        else:
            no_guitar_bars.append(bar)
        # keys
        for off, m, dur in (KEYS_BREAK if part in ("break", "intro") else KEYS_VERSE):
            add("keys", keys_note(m, dur * spb * 1.6), at(b0 + off), pitch=m)
        # vocals
        if part in ("verse", "break", "chorus"):
            for off, m, dur in VOCAL_LINE:
                add("vocals", vocal_note(m + (5 if part == "chorus" else 0), dur * spb), at(b0 + off),
                    pitch=m)

    mix = sum(stems.values())
    scale = 0.9 / np.max(np.abs(mix))
    base = os.path.splitext(args.out)[0]
    os.makedirs(base + "_stems", exist_ok=True)
    sf.write(args.out, np.stack([mix * scale] * 2, axis=1), SR)
    for k, v in stems.items():
        sf.write(f"{base}_stems/{k}.wav", np.stack([v * scale] * 2, axis=1), SR)
    beats = [at(b) for b in range(n_beats)]
    with open(base + ".json", "w") as fh:
        json.dump(dict(bpm=args.bpm, drift=0, beats=beats, downbeats=beats[::4], parts=parts,
                       no_guitar_bars=no_guitar_bars, notes=key,
                       melody=[dict(time=n["time"], pitch=min(n["pitches"]), beats=n["beats"])
                               for n in key["guitar"]]), fh)
    print(f"wrote {args.out} ({total:.1f}s): " +
          ", ".join(f"{k} {len(v)}" for k, v in key.items()) + " notes")


if __name__ == "__main__":
    main()
