#!/usr/bin/env python3
"""Score how well a chart follows ONLY the guitar of tests/make_band_song.py.

    python tests/check_guitar.py "<song folder>" band.wav

For each difficulty:
  guitar recall   share of guitar notes that have a chart note on them
  precision       share of chart notes that land on a guitar note
  from others     chart notes that land only on keys / vocals / bass notes
  in no-guitar    chart notes in bars where the guitar is silent, and how
                  many of those are on keys notes (a deliberate fill-in)
  chords          chart chords on guitar chords / chart chords on single notes
"""
import json
import sys

import numpy as np

from check_chart import find_pad, parse_chart, tick_times

TOL = 0.035


def main():
    folder, wav = sys.argv[1], sys.argv[2]
    key = json.load(open(wav.rsplit(".", 1)[0] + ".json"))
    res, sync, _, tracks = parse_chart(f"{folder}/notes.chart")
    pad = find_pad(f"{folder}/song.ogg", wav)
    beats = np.array(key["beats"])
    spb = float(np.median(np.diff(beats)))
    bar_start = beats[::4]
    silent = [(bar_start[b], bar_start[b] + 4 * spb) for b in key["no_guitar_bars"]]

    g = key["notes"]["guitar"]
    gt = np.array([n["time"] for n in g])
    gchord = np.array([len(n["pitches"]) > 1 for n in g])
    keys_t = np.array(sorted(n["time"] for n in key["notes"]["keys"]))
    others = np.array(sorted(n["time"] for k in ("keys", "vocals", "bass") for n in key["notes"][k]))

    print(f"{'':7s}{'notes':>6s} {'guitar recall':>14s} {'precision':>10s} {'from others':>12s} "
          f"{'in no-guitar (on keys)':>23s} {'chords ok/wrong':>16s}")
    for diff in ["Expert", "Hard", "Medium", "Easy"]:
        if diff not in tracks:
            continue
        notes, problems = tracks[diff]
        ticks = np.array(sorted(notes))
        t = tick_times(ticks, sync, res) - pad
        on_g = np.array([np.min(np.abs(gt - x)) < TOL for x in t])
        on_o = np.array([np.min(np.abs(others - x)) < TOL for x in t])
        on_k = np.array([np.min(np.abs(keys_t - x)) < TOL for x in t])
        in_silent = np.array([any(a - 0.05 <= x < b - 0.05 for a, b in silent) for x in t])
        rec = np.mean([np.min(np.abs(t - x)) < TOL for x in gt]) if len(t) else 0
        chords_ok = chords_bad = 0
        for x, tk in zip(t, ticks):
            if len(notes[tk]) > 1:
                j = int(np.argmin(np.abs(gt - x)))
                if abs(gt[j] - x) < TOL and gchord[j]:
                    chords_ok += 1
                else:
                    chords_bad += 1
        print(f"{diff:7s}{len(t):6d} {rec*100:13.0f}% {on_g.mean()*100:9.0f}% "
              f"{(on_o & ~on_g).sum():12d} {in_silent.sum():13d} ({(in_silent & on_k).sum():3d})     "
              f"{chords_ok:8d}/{chords_bad:<7d}")
        for p in problems[:3]:
            print("   PROBLEM:", p)


if __name__ == "__main__":
    main()
