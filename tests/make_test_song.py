#!/usr/bin/env python3
"""Synthesize a test song whose notes are known exactly, plus an answer key.

    python tests/make_test_song.py out.wav [--bpm 128] [--drift 0] [--start 0.37]

Writes out.wav and out.json (beat times, downbeats, lead-melody notes with
pitches). tests/check_chart.py compares a generated chart against the key.
"""
import argparse
import json

import numpy as np
import soundfile as sf

SR = 44100


def env(n, attack=0.004, decay=0.25):
    t = np.arange(n) / SR
    return np.minimum(t / attack, 1.0) * np.exp(-t / decay)


def tone(freq, dur, kind="pluck", decay=0.3):
    sig = _tone(freq, dur, kind, decay)
    fade = min(len(sig), int(0.03 * SR))   # gentle release, no clicks
    sig[-fade:] *= np.linspace(1, 0, fade)
    return sig


def _tone(freq, dur, kind, decay):
    n = int(dur * SR)
    t = np.arange(n) / SR
    if kind == "pluck":   # bright, guitar-ish
        sig = sum(np.sin(2 * np.pi * freq * h * t) / h ** 1.2 for h in range(1, 7))
        return sig * env(n, decay=decay) * 0.18
    if kind == "bass":
        sig = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
        return sig * env(n, decay=0.35) * 0.30
    if kind == "pad":
        sig = sum(np.sin(2 * np.pi * freq * h * t) / h ** 2 for h in range(1, 4))
        a = np.minimum(t / 0.05, 1.0) * np.minimum((dur - t) / 0.05, 1.0)
        return sig * a * 0.035
    raise ValueError(kind)


def drum(kind, rng):
    if kind == "kick":
        n = int(0.25 * SR); t = np.arange(n) / SR
        f = 50 + 90 * np.exp(-t / 0.03)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12) * 0.9
    if kind == "snare":
        n = int(0.2 * SR); t = np.arange(n) / SR
        return (rng.standard_normal(n) * 0.5 + np.sin(2 * np.pi * 190 * t)) * np.exp(-t / 0.06) * 0.35
    n = int(0.05 * SR); t = np.arange(n) / SR
    return rng.standard_normal(n) * np.exp(-t / 0.012) * 0.10


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--bpm", type=float, default=128.0)
    ap.add_argument("--drift", type=float, default=0.0, help="BPM change over the song (live feel)")
    ap.add_argument("--start", type=float, default=0.37, help="seconds of silence before beat 1")
    ap.add_argument("--bars", type=int, default=32)
    args = ap.parse_args()
    rng = np.random.default_rng(7)

    n_beats = args.bars * 4
    bpm = args.bpm + args.drift * np.linspace(0, 1, n_beats)
    beats = args.start + np.concatenate([[0], np.cumsum(60.0 / bpm[:-1])])
    total = beats[-1] + 60.0 / bpm[-1] + 2.0
    mix = np.zeros(int(total * SR) + SR)

    def add(sig, t):
        i = int(round(t * SR)); mix[i:i + len(sig)] += sig[: len(mix) - i]

    def at(beat_pos):  # fractional beat index -> seconds
        i = int(beat_pos); frac = beat_pos - i
        nxt = beats[i + 1] if i + 1 < len(beats) else beats[i] + 60.0 / bpm[-1]
        return beats[i] + frac * (nxt - beats[i])

    # Structure: intro 4 | verse 8 | chorus 8 | verse 8 | chorus 4 (bars)
    parts = ["intro"] * 4 + ["verse"] * 8 + ["chorus"] * 8 + ["verse"] * 8 + ["chorus"] * 4
    while len(parts) < args.bars:   # longer songs: more verse/chorus pairs
        parts += ["verse"] * 8 + ["chorus"] * 8
    parts = parts[: args.bars]
    verse_chords = [45, 41, 48, 43]   # Am F C G (roots)
    chorus_chords = [41, 43, 45, 45]  # F G Am Am
    verse_riff = [(0, 69, .5), (.5, 72, .5), (1, 74, .5), (1.5, 72, .5), (2, 69, 1), (3, 67, .5), (3.5, 64, .5)]
    chorus_riff = [(0, 76, 1.5), (1.5, 74, .5), (2, 72, .5), (2.5, 74, .5), (3, 76, .25),
                   (3.25, 77, .25), (3.5, 79, .5)]
    melody = []
    for bar, part in enumerate(parts):
        b0 = bar * 4
        if part != "intro":
            add(drum("kick", rng), at(b0)); add(drum("kick", rng), at(b0 + 2))
            add(drum("snare", rng), at(b0 + 1)); add(drum("snare", rng), at(b0 + 3))
        for e in range(8):
            add(drum("hat", rng), at(b0 + e / 2))
        chords = chorus_chords if part == "chorus" else verse_chords
        root = chords[bar % 4]
        for e in range(0, 4, 1 if part == "chorus" else 2):
            add(tone(midi_hz(root - 12), 60 / bpm[0] * 0.9, "bass"), at(b0 + e))
        for iv in (0, 4 if root in (41, 48) else 3, 7):
            add(tone(midi_hz(root + 12 + iv), 4 * 60 / bpm[0], "pad"), at(b0))
        if part == "intro":
            continue
        riff = chorus_riff if part == "chorus" else verse_riff
        for off, pitch, dur in riff:
            t = at(b0 + off)
            d = dur * 60 / bpm[0]
            add(tone(midi_hz(pitch), d * 1.05, "pluck", decay=0.15 + 0.45 * dur), t)
            melody.append(dict(time=float(t), pitch=int(pitch), beats=float(dur), part=part))

    mix /= np.max(np.abs(mix)) * 1.1
    sf.write(args.out, np.stack([mix, mix], axis=1), SR)
    key = dict(bpm=args.bpm, drift=args.drift, beats=beats.tolist(),
               downbeats=beats[::4].tolist(), melody=melody, parts=parts)
    with open(args.out.rsplit(".", 1)[0] + ".json", "w") as fh:
        json.dump(key, fh)
    print(f"wrote {args.out} ({total:.1f}s, {len(melody)} melody notes)")


if __name__ == "__main__":
    main()
