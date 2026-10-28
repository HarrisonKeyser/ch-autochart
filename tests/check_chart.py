#!/usr/bin/env python3
"""Check a generated song folder against a test song's answer key.

    python tests/check_chart.py "<song folder>" test.wav

Parses notes.chart independently of autochart.py, rebuilds the tempo map
from [SyncTrack], and reports beat sync, downbeat placement, melody recall,
contour agreement and file-format problems.
"""
import json
import re
import sys

import numpy as np
import soundfile as sf

LANES = {"Expert": 5, "Hard": 5, "Medium": 4, "Easy": 3}


def parse_chart(path):
    text = open(path, encoding="utf-8-sig").read()
    sections = dict(re.findall(r"\[(\w+)\]\s*\{(.*?)\}", text, re.S))
    res = int(re.search(r"Resolution\s*=\s*(\d+)", sections["Song"]).group(1))
    sync = [(int(t), int(v)) for t, v in re.findall(r"(\d+)\s*=\s*B\s+(\d+)", sections["SyncTrack"])]
    events = re.findall(r'(\d+)\s*=\s*E\s+"section (.*?)"', sections.get("Events", ""))
    tracks = {}
    for name, body in sections.items():
        m = re.match(r"(Expert|Hard|Medium|Easy)(Single|DoubleBass)$", name)
        if not m:
            continue
        rows = [l.strip() for l in body.strip().splitlines() if l.strip()]
        notes, problems, last = {}, [], -1
        for r in rows:
            mm = re.match(r"(\d+) = (N|S) (\d+) (\d+)$", r)
            if not mm:
                problems.append(f"bad line: {r}")
                continue
            tick, kind, a, b = int(mm[1]), mm[2], int(mm[3]), int(mm[4])
            if tick < last:
                problems.append(f"out of order at {tick}")
            last = tick
            if kind == "N":
                if a >= LANES[m[1]]:
                    problems.append(f"fret {a} too high for {m[1]} at {tick}")
                notes.setdefault(tick, []).append((a, b))
        tracks[m[1]] = (notes, problems)
    return res, sync, events, tracks


def tick_times(ticks, sync, res):
    ticks = np.asarray(ticks, float)
    st = np.array([s[0] for s in sync], float)
    spt = 60.0 / (np.array([s[1] for s in sync]) / 1000.0) / res
    seg_t = np.concatenate([[0], np.cumsum(np.diff(st) * spt[:-1])])
    i = np.clip(np.searchsorted(st, ticks, side="right") - 1, 0, len(st) - 1)
    return seg_t[i] + (ticks - st[i]) * spt[i]


def find_pad(ogg, src):
    """Lead-in added by autochart = where sound starts in song.ogg minus where
    it starts in the source (robust to encoder padding at the end)."""
    def start(path):
        x, sr = sf.read(path, always_2d=True)
        x = np.abs(x).max(axis=1)
        return int(np.argmax(x > 0.02 * x.max())) / sr
    return start(ogg) - start(src)


def main():
    folder, wav = sys.argv[1], sys.argv[2]
    key = json.load(open(wav.rsplit(".", 1)[0] + ".json"))
    res, sync, events, tracks = parse_chart(f"{folder}/notes.chart")
    pad = find_pad(f"{folder}/song.ogg", wav)
    print(f"pad {pad*1000:.1f} ms | {len(sync)} tempo marker(s), first {sync[0][1]/1000:.3f} BPM "
          f"(true {key['bpm']}{' +drift ' + str(key['drift']) if key['drift'] else ''})")

    beats = np.array(key["beats"])
    last_tick = max(max(n[0].keys(), default=0) for n in tracks.values()) + 4 * res
    grid = tick_times(np.arange(0, last_tick, res), sync, res) - pad
    err = np.array([np.min(np.abs(grid - b)) for b in beats]) * 1000
    print(f"beat sync: median {np.median(err):.1f} ms, worst {err.max():.1f} ms")

    bars = tick_times(np.arange(0, last_tick, 4 * res), sync, res) - pad
    db_ok = np.mean([np.min(np.abs(bars - d)) < 0.04 for d in key["downbeats"]])
    print(f"downbeats on bar lines: {db_ok*100:.0f}%")

    mel = key["melody"]
    for diff in ["Expert", "Hard", "Medium", "Easy"]:
        if diff not in tracks:
            continue
        notes, problems = tracks[diff]
        ticks = np.array(sorted(notes))
        times = tick_times(ticks, sync, res) - pad
        matched = []
        for m in mel:
            j = int(np.argmin(np.abs(times - m["time"])))
            if abs(times[j] - m["time"]) < 0.035:
                matched.append((m, notes[ticks[j]]))
        line = f"{diff:6s} {len(ticks):4d} notes, melody recall {len(matched)}/{len(mel)}"
        if len(matched) > 2:
            agree = total = 0
            for (m1, f1), (m2, f2) in zip(matched, matched[1:]):
                dp = np.sign(m2["pitch"] - m1["pitch"])
                dl = np.sign(min(f[0] for f in f2) - min(f[0] for f in f1))
                total += 1
                agree += dp == dl
            line += f", contour agreement {agree}/{total}"
        sus = sum(1 for v in notes.values() if v[0][1] > 0)
        line += f", sustains {sus}"
        print(line)
        for p in problems[:5]:
            print("   PROBLEM:", p)
    print("sections:", ", ".join(f"{name}@bar{int(t)//(4*res)}" for t, name in events))


if __name__ == "__main__":
    main()
