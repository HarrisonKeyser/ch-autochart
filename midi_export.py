"""
What ch-autochart heard, as a MIDI file: one track per instrument.

The notes are the ones the chart is built from (after snapping to the beat
grid, so the rhythm reads like sheet music), with every note of each chord.
Open it in a notation program such as MuseScore to see the transcription as
a score, or in any DAW to play it against the song. It uses the chart's own
timeline and tempo map, so it lines up with the chart's song.ogg (which
starts with a short lead-in of silence).
"""

import struct

PPQ = 192                       # same resolution as the .chart (RES)
PROGRAMS = {"guitar": 27, "bass": 33, "piano": 0, "other": 81, "vocals": 53, "mix": 0}
NAMES = {"guitar": "Guitar", "bass": "Bass", "piano": "Piano / keys", "other": "Other (synth, organ...)",
         "vocals": "Vocals", "mix": "Full mix"}
ORDER = ["guitar", "bass", "piano", "other", "vocals"]


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _track(events):
    """events: (tick, order, bytes) -> an MTrk chunk with delta times."""
    data, last = bytearray(), 0
    for tick, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
        data += _vlq(tick - last) + msg
        last = tick
    data += _vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(data)) + bytes(data)


def _text(kind, s):
    raw = s.encode("utf-8")
    return bytes([0xFF, kind]) + _vlq(len(raw)) + raw


def note_keys(n):
    """MIDI note numbers of one detected note (all of a chord's notes)."""
    if getattr(n, "keys", ()):
        return sorted(set(int(k) for k in n.keys))
    return [int(round(n.pitch + (28 if n.src == "bass" else 40)))]


def transcription(parts, tempo, pad, time_signature=(4, 4), title=""):
    """parts: {instrument: [Note with .tick]}; tempo: the chart's TempoMap.
    Returns the bytes of a format-1 MIDI file."""
    head = [(0, 0, _text(0x03, title or "ch-autochart transcription")),
            (0, 1, bytes([0xFF, 0x58, 0x04, time_signature[0], {1: 0, 2: 1, 4: 2, 8: 3}.get(time_signature[1], 2),
                          24, 8]))]
    for tick, millibpm in tempo.events:
        usec = int(round(60_000_000 / (millibpm / 1000)))
        head.append((int(tick), 2, bytes([0xFF, 0x51, 0x03]) + usec.to_bytes(3, "big")))
    chunks = [_track(head)]
    names = [k for k in ORDER if k in parts] + [k for k in parts if k not in ORDER]
    for ch, name in enumerate(n for n in names if parts[n]):
        channel = ch if ch < 9 else ch + 1          # skip channel 10 (drums)
        notes = sorted(parts[name], key=lambda n: n.tick)
        ev = [(0, 0, _text(0x03, NAMES.get(name, name))),
              (0, 1, bytes([0xC0 | channel, PROGRAMS.get(name, 0)]))]
        for i, n in enumerate(notes):
            start = int(n.tick)
            end = int(tempo.time_to_tick(n.end_time + pad))
            end = max(start + PPQ // 8, int(round(end / (PPQ // 8))) * (PPQ // 8))
            nxt = next((m.tick for m in notes[i + 1:] if m.tick > start), None)
            if nxt is not None:
                end = min(end, int(nxt))        # one strum ends where the next begins
            vel = max(30, min(127, int(40 + 87 * float(n.strength))))
            for k in note_keys(n):
                if 0 <= k <= 127:
                    ev.append((start, 3, bytes([0x90 | channel, k, vel])))
                    ev.append((end, 2, bytes([0x80 | channel, k, 0])))
        chunks.append(_track(ev))
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), PPQ) + b"".join(chunks)
