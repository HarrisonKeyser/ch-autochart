#!/usr/bin/env python3
"""Build a test song from recorded instrument samples, with every note known.

    python tests/make_sampled_song.py song.wav --samples <FluidR3_GM folder>

The samples are the FluidR3 General MIDI soundfont rendered note by note
(https://github.com/gleitz/midi-js-soundfonts, folder FluidR3_GM, CC BY 3.0);
it needs electric_guitar_clean, electric_bass_finger, acoustic_grand_piano,
trumpet and alto_sax. Unlike tests/make_band_song.py's synthesized plucks,
these are recorded instruments, so pitch and chord detection get a fair
test. Strummed chords are built string by string (6-11 ms apart, down and
up strums), and the overdriven parts go through a soft-clipping "amp".

The song (118 BPM, about 2 minutes) is written to be hard in the ways real
songs are hard:
  intro     piano only
  verse     clean guitar off-beat chops on high strings (syncopated 16ths),
            piano comping in the guitar's register, vocal line
  chorus    overdriven guitar strumming 8ths (down/up strums, full chords and
            power chords), piano, horn stabs
  break     no guitar: a piano riff in the guitar's register
  solo      overdriven single-note lead
  verse 2   the same chops, but the guitar is 14 dB quieter, under loud horns
  chorus 2  the guitar 10 dB quieter
The last two are the "guitar drowned out" case: a human charter would chart
them like the first verse and chorus.

Writes song.wav, song.json (answer key in tests/make_band_song.py's format,
plus "other" for the horns and per-note "section" and "level"), and
song_stems/ (perfect stems) and song_stems_bleed/ (stems with some of the
other instruments leaking in, the way real separation leaves them).
"""
import argparse
import json
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_band_song import drum, vocal_note  # noqa: E402

SR = 44100
NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# chord voicings as played on a guitar in standard tuning
FULL = {"G": [43, 47, 50, 55, 59, 67], "Em": [40, 47, 52, 55, 59, 64],
        "C": [48, 52, 55, 60, 64], "D": [50, 57, 62, 66]}
HIGH = {"G": [59, 62, 67], "Em": [59, 64, 67], "C": [60, 64, 67], "D": [62, 66, 69]}
POWER = {"G": [43, 50, 55], "Em": [40, 47, 52], "C": [48, 55, 60], "D": [50, 57, 62]}
BASS_ROOT = {"G": 31, "Em": 28, "C": 36, "D": 38}
PIANO = {"G": [55, 59, 62], "Em": [55, 59, 64], "C": [55, 60, 64], "D": [57, 62, 66]}
PROGRESSION = ["G", "Em", "C", "D"]

CHOP = [0.75, 1.5, 2.25, 2.75, 3.5]                      # off-beat chops, in beats
STRUM = [(0, "down", .5), (.5, "up", .5), (1, "down", .5), (1.5, "up", .5),
         (2, "down", .75), (2.75, "up", .25), (3, "down", .5), (3.5, "up", .5)]
SOLO = [  # (beat in a 2-bar phrase, midi, length in beats)
    (0, 67, .5), (.5, 69, .5), (1, 71, .25), (1.25, 74, .25), (1.5, 71, .5), (2, 69, 1),
    (3, 67, .5), (3.5, 64, .5), (4, 62, .5), (4.5, 64, .25), (4.75, 67, .25), (5, 69, .5),
    (5.5, 71, .5), (6, 74, 1.5), (7.5, 76, .5)]
PIANO_RIFF = [(i * .5, m, .5) for i, m in enumerate([64, 67, 71, 67, 72, 71, 67, 64])]
VOCALS = [(0.5, 67, 1), (1.5, 69, .5), (2, 71, 1.5), (3.5, 69, .5)]
HORN_STABS = [(1.5, .25), (3.5, .25), (3.75, .25)]
HORN_LINE = [(0, 72, .75), (.75, 74, .75), (1.5, 76, 1), (2.5, 74, .5), (3, 71, 1)]


class Sampler:
    def __init__(self, folder, instrument):
        self.dir = os.path.join(folder, f"{instrument}-mp3")
        self.cache = {}

    def note(self, midi, dur, release=0.06):
        if midi not in self.cache:
            import librosa
            name = f"{NAMES[midi % 12]}{midi // 12 - 1}"
            y, _ = librosa.load(os.path.join(self.dir, name + ".mp3"), sr=SR, mono=True)
            # drop the encoder's leading silence so the attack is at sample 0
            start = int(np.argmax(np.abs(y) > 0.02 * np.abs(y).max()))
            self.cache[midi] = y[max(0, start - 32):]
        y = self.cache[midi]
        n = min(len(y), int((dur + release) * SR))
        out = y[:n].copy()
        r = min(n, int(release * SR))
        tail = n - r
        out[tail:] *= np.linspace(1, 0, n - tail)
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--samples", required=True, help="the FluidR3_GM folder")
    ap.add_argument("--bpm", type=float, default=118.0)
    ap.add_argument("--start", type=float, default=0.8)
    args = ap.parse_args()
    rng = np.random.default_rng(7)
    inst = {k: Sampler(args.samples, v) for k, v in {
        "clean": "electric_guitar_clean", "bass": "electric_bass_finger",
        "piano": "acoustic_grand_piano", "trumpet": "trumpet", "sax": "alto_sax"}.items()}

    plan = ([("intro", 1.0)] * 4 + [("verse", 1.0)] * 8 + [("chorus", 1.0)] * 8 +
            [("break", 1.0)] * 4 + [("solo", 1.0)] * 8 + [("verse 2", 0.2)] * 8 +
            [("chorus 2", 0.32)] * 8)
    spb = 60.0 / args.bpm
    n_beats = len(plan) * 4
    total = args.start + n_beats * spb + 3.0
    names = ["guitar", "bass", "drums", "piano", "vocals", "other"]
    stems = {k: np.zeros(int(total * SR)) for k in names}
    key = {k: [] for k in ["guitar", "bass", "drums", "keys", "vocals", "other"]}
    key_name = {"piano": "keys"}

    def at(beat):
        return args.start + beat * spb

    def put(stem, sig, t, gain=1.0):
        i = int(round(t * SR))
        buf = stems[stem]
        buf[i:i + len(sig)] += gain * sig[: len(buf) - i]

    def note(stem, sig, t, gain=1.0, **info):
        put(stem, sig, t, gain)
        key[key_name.get(stem, stem)].append(dict(time=float(t), **info))

    def overdrive(sig, drive):
        """A guitar amp: the whole chord through one soft-clipping stage."""
        return np.tanh(drive * sig / (np.abs(sig).max() + 1e-9)) / np.tanh(drive) * 0.5

    def strum(pitches, t, dur, direction, gain, drive=0.0, **info):
        order = pitches if direction == "down" else pitches[::-1]
        step = rng.uniform(0.006, 0.011)
        buf = np.zeros(int((dur + 0.2) * SR) + int(len(order) * step * SR))
        for i, m in enumerate(order):
            s = inst["clean"].note(m, max(0.05, dur - i * step)) * (0.9 + 0.2 * rng.random())
            j = int(i * step * SR)
            buf[j:j + len(s)] += s[: len(buf) - j]
        put("guitar", overdrive(buf, drive) if drive else buf, t, gain)
        key["guitar"].append(dict(time=float(t), pitches=sorted(pitches), **info))

    no_guitar_bars = []
    for bar, (part, level) in enumerate(plan):
        b0 = bar * 4
        chord = PROGRESSION[bar % 4]
        loud = part.startswith("chorus") or part == "solo"
        # drums
        if part != "intro":
            for b in (0, 2, 2.5) if loud else (0, 2):
                note("drums", drum("kick", rng), at(b0 + b), kind="kick")
            for b in (1, 3):
                note("drums", drum("snare", rng), at(b0 + b), kind="snare")
            for e in range(8):
                note("drums", drum("hat", rng), at(b0 + e / 2), kind="hat")
        # bass
        if part not in ("intro", "break"):
            root = BASS_ROOT[chord]
            for b in (0, 1, 1.5, 2, 3) if loud else (0, 1.5, 2, 3.5):
                m = root + (7 if b == 3 else 0)
                note("bass", inst["bass"].note(m, spb * 0.45), at(b0 + b), 0.9, pitch=m)
        # guitar
        info = dict(section=part, level=level)
        if part.startswith("verse"):
            for off in CHOP:
                strum(HIGH[chord], at(b0 + off), 0.11,
                      "up" if off % 0.5 else "down", 0.55 * level, beats=0.25, **info)
        elif part.startswith("chorus"):
            voicing = POWER[chord] if bar % 2 else FULL[chord]
            for off, direction, dur in STRUM:
                pitches = voicing if direction == "down" else voicing[-3:]
                strum(pitches, at(b0 + off), dur * spb * 0.95, direction,
                      (0.5 if direction == "down" else 0.35) * level, drive=3.0, beats=dur, **info)
        elif part == "solo":
            phrase_beat = (bar % 2) * 4
            for off, m, dur in SOLO:
                if phrase_beat <= off < phrase_beat + 4:
                    note("guitar", overdrive(inst["clean"].note(m, dur * spb * 0.95), 4.0),
                         at(b0 + off - phrase_beat), 0.45 * level, pitches=[m], beats=dur, **info)
        else:
            no_guitar_bars.append(bar)
        # piano
        if part == "break":
            for off, m, dur in PIANO_RIFF:
                note("piano", inst["piano"].note(m, dur * spb), at(b0 + off), 0.8, pitch=m)
        elif part != "solo":
            for off in (0, 2):
                for m in PIANO[chord]:
                    note("piano", inst["piano"].note(m, 1.8 * spb), at(b0 + off), 0.35, pitch=m)
        # vocals
        if part.startswith(("verse", "chorus")):
            for off, m, dur in VOCALS:
                note("vocals", vocal_note(m + (2 if part.startswith("chorus") else 0) - 12, dur * spb),
                     at(b0 + off), 1.6, pitch=m)
        # horns: stabs in the choruses, a loud line over the quiet second verse
        if part.startswith("chorus"):
            for off, dur in HORN_STABS:
                for m in PIANO[chord]:
                    note("other", inst["trumpet"].note(m + 12, dur * spb), at(b0 + off), 0.35, pitch=m + 12)
        if part == "verse 2":
            for off, m, dur in HORN_LINE:
                note("other", inst["sax"].note(m - 5, dur * spb), at(b0 + off), 0.7, pitch=m - 5)

    mix = sum(stems.values())
    scale = 0.9 / np.max(np.abs(mix))
    base = os.path.splitext(args.out)[0]
    sf.write(args.out, np.stack([mix * scale] * 2, axis=1), SR)
    leak = {"guitar": {"piano": 0.12, "other": 0.10, "drums": 0.04, "vocals": 0.05},
            "piano": {"guitar": 0.15, "other": 0.08}, "other": {"guitar": 0.15, "piano": 0.1},
            "bass": {"guitar": 0.03}, "vocals": {"other": 0.05}, "drums": {}}
    for folder, bleed in (("_stems", False), ("_stems_bleed", True)):
        os.makedirs(base + folder, exist_ok=True)
        for k, v in stems.items():
            sig = v + sum(g * stems[o] for o, g in leak[k].items()) if bleed else v
            sf.write(f"{base}{folder}/{k}.wav", np.stack([sig * scale] * 2, axis=1), SR)
    beats = [at(b) for b in range(n_beats)]
    with open(base + ".json", "w") as fh:
        json.dump(dict(bpm=args.bpm, drift=0, beats=beats, downbeats=beats[::4],
                       parts=[p for p, _ in plan], no_guitar_bars=no_guitar_bars, notes=key,
                       melody=[dict(time=n["time"], pitch=min(n["pitches"]), beats=n["beats"])
                               for n in key["guitar"]]), fh)


if __name__ == "__main__":
    main()
