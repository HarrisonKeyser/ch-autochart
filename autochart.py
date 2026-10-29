#!/usr/bin/env python3
"""
autochart.py - turn a song into a playable Clone Hero song folder.

    python autochart.py "song.mp3" --artist "Band" --title "Song"

Writes <out>/<Artist - Title>/ containing:
    notes.chart   guitar (or bass) chart: Easy/Medium/Hard/Expert, tempo map,
                  section names and star power
    song.ogg      the audio, with a short silent lead-in added so the tempo
                  map can start cleanly on a downbeat
    guitar.ogg    with --split-audio: the charted part on its own track
    song.ini      metadata (name, artist, length, preview start, difficulty)
    album.png     only when --album-art is given

How it works, in short:
  1. The song is split into instruments with Demucs (when installed), and
     each one is transcribed on its own.
  2. Bar by bar, the chart follows the guitar wherever it really plays, and
     hands the part to another instrument (keys, synth, harmonica...) where
     the guitar sits out, sticking with a choice for whole passages.
  3. Beat tracking -> a tempo map (constant, or following the band's drift).
  4. Onsets -> note timings on a 1/16 grid; pitch movement -> frets that
     follow the melody's contour; heard polyphony -> chords; ring time ->
     sustains. Lower difficulties are thinned-out subsets of Expert.
  5. Repetition in the harmony -> section names; star power every few bars.

app.py runs the same engine as a local web app with a visual editor.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace

import numpy as np

try:
    import librosa
    import soundfile as sf
    from scipy.ndimage import maximum_filter1d
except ImportError as err:  # pragma: no cover
    sys.exit(f"Missing dependency ({err.name}). Install with:  pip install -r requirements.txt")

__version__ = "2.0.0"

RES = 192            # chart ticks per beat (the standard .chart resolution)
SR = 22050           # analysis sample rate
HOP = 256            # analysis hop -> ~11.6 ms frames
FRAME_SEC = HOP / SR
FINE_HOP = 64        # fine onset envelope (~2.9 ms) used to pin down exact attack times
FINE_LATENCY = 0.004 # the fine envelope peaks ~4 ms after a real attack

# Per-difficulty shaping. gap_* is the closest two notes may be;
# keep is the share of Expert's notes a difficulty may keep at most.
# chord/triple: share of notes made into chords when chords are guessed from
# accents (full mix); max_chord / chord_gap: limits when real chords are heard
# in an isolated instrument (largest chord, and room needed around it in ticks).
DIFFICULTIES = {
    "expert": dict(lanes=5, gap_beats=0.25, gap_sec=0.075, keep=1.00, chord=0.12, triple=0.03,
                   max_chord=3, chord_gap=96),
    "hard":   dict(lanes=5, gap_beats=0.50, gap_sec=0.150, keep=0.70, chord=0.07, triple=0.00,
                   max_chord=2, chord_gap=96),
    "medium": dict(lanes=4, gap_beats=0.50, gap_sec=0.240, keep=0.45, chord=0.03, triple=0.00,
                   max_chord=2, chord_gap=192),
    "easy":   dict(lanes=3, gap_beats=1.00, gap_sec=0.420, keep=0.28, chord=0.00, triple=0.00,
                   max_chord=1, chord_gap=192),
}
DIFF_ORDER = ["expert", "hard", "medium", "easy"]
TRACK_SUFFIX = {"guitar": "Single", "bass": "DoubleBass"}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

# Hooks the web app uses to follow progress (None when run from the command line).
LOG_HOOK = None        # called with every log line
PROGRESS_HOOK = None   # called with 0..1 while instruments are being separated


def log(msg=""):
    if LOG_HOOK:
        LOG_HOOK(msg)
    print(msg, flush=True)


def have(cmd):
    return shutil.which(cmd) is not None


def ffmpeg_path():
    """ffmpeg on the PATH, or the copy bundled with the imageio-ffmpeg package
    (the Windows installer ships that one). None when neither exists."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def safe_name(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip().rstrip(".")
    return text or "Untitled"


@dataclass
class Note:
    time: float          # seconds, in the ORIGINAL audio's timeline
    strength: float      # 0..1, accent relative to the surrounding music
    pitch: float         # pitch estimate in semitones (CQT bin)
    end_time: float      # when the note stops ringing (original timeline)
    poly: int = 1        # how many simultaneous pitches started here (chords)
    src: str = "mix"     # which instrument this note was heard in
    tick: int = 0
    lane: int = 0
    frets: tuple = ()
    sustain: int = 0


# --------------------------------------------------------------------------
# Audio in / out
# --------------------------------------------------------------------------

def decode_audio(path):
    """Return (samples[channels, n] float32, sample_rate) at the file's native rate."""
    try:
        y, sr = librosa.load(path, sr=None, mono=False)
    except Exception as first_err:
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            sys.exit(f"Could not read '{path}' ({first_err}).\n"
                     "Install ffmpeg (and put it on your PATH) to open this format.")
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "decoded.wav")
            try:
                run([ffmpeg, "-v", "error", "-y", "-i", path, "-vn", "-c:a", "pcm_f32le", wav])
            except subprocess.CalledProcessError:
                sys.exit(f"Could not read '{path}': it doesn't look like an audio file ffmpeg can open.")
            y, sr = librosa.load(wav, sr=None, mono=False)
    if y.ndim == 1:
        y = y[np.newaxis, :]
    return y.astype(np.float32), int(sr)


def write_ogg(y, sr, pad_samples, out_path):
    """Write y (channels x n) with pad_samples of leading silence as Ogg Vorbis."""
    data = np.concatenate([np.zeros((y.shape[0], pad_samples), np.float32), y], axis=1).T
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        data = data / peak
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "padded.wav")
            sf.write(wav, data, sr, subtype="FLOAT")
            try:
                run([ffmpeg, "-v", "error", "-y", "-i", wav, "-c:a", "libvorbis", "-q:a", "6", out_path])
                return
            except subprocess.CalledProcessError:
                pass  # fall back to libsndfile's encoder below
    sf.write(out_path, data, sr, format="OGG", subtype="VORBIS")


def read_tags(path):
    """Title/artist/album/... from the file's tags (mutagen, else ffprobe)."""
    try:
        import mutagen
        f = mutagen.File(path, easy=True)
        if f is not None and f.tags:
            return {k.lower(): (v[0] if isinstance(v, list) and v else str(v))
                    for k, v in f.tags.items() if v}
    except Exception:
        pass
    if not have("ffprobe"):
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", path],
            check=True, capture_output=True, text=True).stdout
        tags = json.loads(out).get("format", {}).get("tags", {})
        return {k.lower(): v for k, v in tags.items()}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

@dataclass
class Analysis:
    duration: float
    times: np.ndarray        # frame times
    env: np.ndarray          # onset strength, full band
    note_env: np.ndarray     # onset strength used for picking notes (full + harmonic)
    fine_times: np.ndarray
    fine_env: np.ndarray     # high-resolution onset strength for timing refinement
    env_low: np.ndarray      # onset strength, low band (kick/bass) for downbeats
    Cdb: np.ndarray          # CQT in dB (bins x frames), harmonic part
    Cw: np.ndarray           # same, with each pitch's median level subtracted
    chroma: np.ndarray
    mfcc: np.ndarray
    rms: np.ndarray
    beats: np.ndarray        # tracked beat times
    tempo_guess: float


def analyze(y, instrument, bpm_hint, quiet=False):
    say = (lambda m: None) if quiet else log
    say("  separating harmonic / percussive parts...")
    y_h, _ = librosa.effects.hpss(y)

    say("  onset envelope and beat tracking...")
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    env_low = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, fmax=200.0, n_mels=24)
    # Notes = drum-like attacks OR new pitches; the harmonic part's envelope
    # catches soft picked notes that drums would otherwise drown out.
    env_h = librosa.onset.onset_strength(y=y_h, sr=SR, hop_length=HOP)
    m = min(len(env), len(env_h))
    note_env = (env[:m] / (np.percentile(env, 99) + 1e-9) +
                env_h[:m] / (np.percentile(env_h, 99) + 1e-9))
    tempo, beats = librosa.beat.beat_track(
        onset_envelope=env, sr=SR, hop_length=HOP,
        start_bpm=bpm_hint or 120.0, units="time", trim=False)
    tempo = float(np.atleast_1d(tempo)[0])
    fine_env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=FINE_HOP, n_fft=512, n_mels=64)
    fine_times = librosa.frames_to_time(np.arange(len(fine_env)), sr=SR, hop_length=FINE_HOP)

    say("  pitch analysis...")
    fmin = librosa.note_to_hz("E1" if instrument == "bass" else "E2")
    n_bins = 48 if instrument == "bass" else 60
    C = np.abs(librosa.cqt(y_h, sr=SR, hop_length=HOP, fmin=fmin, n_bins=n_bins, bins_per_octave=12))
    Cdb = librosa.amplitude_to_db(C, ref=np.max)
    # Subtract each pitch's typical level so constant drones/bass don't dominate:
    # what stands out is what is being played *now*.
    Cw = Cdb - np.median(Cdb, axis=1, keepdims=True)
    chroma = librosa.feature.chroma_cqt(C=C, sr=SR, hop_length=HOP, bins_per_octave=12)
    mfcc = librosa.feature.mfcc(y=y, sr=SR, hop_length=HOP, n_mfcc=13)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]

    n = min(len(note_env), len(env_low), Cw.shape[1], chroma.shape[1], mfcc.shape[1], len(rms))
    times = librosa.frames_to_time(np.arange(n), sr=SR, hop_length=HOP)
    an = Analysis(duration=len(y) / SR, times=times, env=env[:n], note_env=note_env[:n], env_low=env_low[:n],
                    fine_times=fine_times, fine_env=fine_env, Cdb=Cdb[:, :n], Cw=Cw[:, :n], chroma=chroma[:, :n], mfcc=mfcc[:, :n], rms=rms[:n],
                    beats=np.asarray(beats, float), tempo_guess=tempo)
    # The coarse envelope (and so the beat tracker) runs 15-25 ms late; re-time
    # each beat on the fine envelope. Beats with no clear attack get the typical shift.
    if len(an.beats):
        refined, found = refine_times(an, an.beats, before=0.06, after=0.02)
        shift = np.median(refined[found] - an.beats[found]) if found.any() else 0.0
        an.beats = np.where(found, refined, an.beats + shift)
    return an


def refine_times(an, times, before, after):
    """Move each time to the strongest fine-envelope attack in [t-before, t+after].
    Returns (new_times, found_mask); found is False where no attack stands out
    (judged against the typical level and against the attacks around it, so a
    soft passage after a loud one still counts)."""
    ft, fe = an.fine_times, an.fine_env
    if getattr(an, "_fine_local", None) is None:
        an._fine_local = maximum_filter1d(fe, size=int(3.0 / (FINE_HOP / SR)) | 1)
    floor = np.median(fe) + 1e-9
    out = np.array(times, float)
    found = np.zeros(len(out), bool)
    lo = np.searchsorted(ft, out - before)
    hi = np.searchsorted(ft, out + after)
    for i, (a, b) in enumerate(zip(lo, hi)):
        if b - a < 2:
            continue
        j = a + int(np.argmax(fe[a:b]))
        if fe[j] > 3 * floor and fe[j] > 0.12 * an._fine_local[j]:
            out[i] = ft[j] - FINE_LATENCY
            found[i] = True
    return out, found


# --------------------------------------------------------------------------
# Tempo map
# --------------------------------------------------------------------------

class TempoMap:
    """Converts between seconds and chart ticks using the exact BPM events
    that get written to [SyncTrack], so the chart and the audio agree."""

    def __init__(self, events):
        self.events = events                                  # [(tick, milli_bpm)]
        self.ticks = np.array([e[0] for e in events], float)
        mbpm = np.array([e[1] for e in events], float)
        self.spt = 60.0 / (mbpm / 1000.0) / RES               # seconds per tick
        seg = np.diff(self.ticks) * self.spt[:-1]
        self.times = np.concatenate([[0.0], np.cumsum(seg)])

    def tick_to_time(self, tick):
        tick = np.asarray(tick, float)
        i = np.clip(np.searchsorted(self.ticks, tick, side="right") - 1, 0, len(self.ticks) - 1)
        return self.times[i] + (tick - self.ticks[i]) * self.spt[i]

    def time_to_tick(self, t):
        t = np.asarray(t, float)
        i = np.clip(np.searchsorted(self.times, t, side="right") - 1, 0, len(self.times) - 1)
        return self.ticks[i] + (t - self.times[i]) / self.spt[i]


def clean_beats(beats):
    """Fill beats the tracker skipped and drop doubled ones."""
    if len(beats) < 4:
        return beats
    med = float(np.median(np.diff(beats)))
    out = [beats[0]]
    for b in beats[1:]:
        gap = b - out[-1]
        if gap < 0.6 * med:
            continue
        if gap > 1.6 * med:
            k = int(round(gap / med))
            out.extend(out[-1] + gap * np.arange(1, k) / k)
        out.append(b)
    return np.array(out)


def smooth_beats(beats, half=4):
    """Robust local straight-line fit (Theil-Sen over +-half beats) at every beat,
    used to catch beats where the tracker slipped toward the off-beat. Real
    tempo changes and expressive timing are kept."""
    n = len(beats)
    if n < 2 * half + 2:
        return beats
    med = float(np.median(np.diff(beats)))
    out = np.empty(n)
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        k = np.arange(a, b)
        x = beats[a:b]
        kk, xx = np.meshgrid(k, k), np.meshgrid(x, x)
        mask = kk[0] < kk[1]
        slope = float(np.median((xx[1] - xx[0])[mask] / (kk[1] - kk[0])[mask]))
        slope = float(np.clip(slope, 0.7 * med, 1.4 * med))
        out[i] = float(np.median(x - (k - i) * slope))
    # Trust the tracked beat unless it strays far from the local trend
    # (a slip toward the off-beat); then use the trend instead.
    out = np.where(np.abs(beats - out) > 0.2 * med, out, beats)
    for i in range(1, n):  # keep beats strictly increasing
        out[i] = max(out[i], out[i - 1] + 0.5 * med)
    return out


def fit_constant(beats, period=None, anchor=None):
    """Least-squares fit of beat_k = t0 + k * period, ignoring stray beats."""
    fixed = period is not None
    if period is None:
        period = float(np.median(np.diff(beats)))
    # Number the beats from their gaps (robust to skipped/extra beats and to a
    # slightly-off starting period), then fit a line, dropping outliers.
    k = np.concatenate([[0], np.cumsum(np.maximum(1, np.round(np.diff(beats) / period)))])
    ok = np.ones(len(beats), bool)
    for _ in range(4):
        if fixed:
            t0 = float(np.median(beats[ok] - k[ok] * period))
        else:
            period, t0 = (float(v) for v in np.polyfit(k[ok], beats[ok], 1))
        resid = beats - (t0 + k * period)
        new_ok = np.abs(resid) < max(0.25 * period * 0.5, 3 * np.median(np.abs(resid)) + 0.005)
        if new_ok.sum() < 3 or np.array_equal(new_ok, ok):
            break
        ok = new_ok
    if anchor is not None:  # user-given downbeat: keep the period, shift the phase
        t0 = anchor
    k = np.round((beats - t0) / period)
    resid = np.abs(beats - (t0 + k * period))
    return t0, period, resid


def downbeat_phase(beats, an, per_measure):
    """Which beat (mod per_measure) is beat 1? Kick drums and bass hits land on it most."""
    inside = (beats >= 0) & (beats <= an.duration)
    b = beats[inside]
    if len(b) < per_measure * 2:
        return int(np.argmax(inside)) % per_measure if inside.any() else 0
    # The coarse envelopes peak a little after the beat: take the max just after it.
    fr0 = np.clip(librosa.time_to_frames(b, sr=SR, hop_length=HOP), 0, len(an.times) - 1)
    span = int(0.07 / FRAME_SEC) + 1
    lo = np.array([an.env_low[f:f + span].max() for f in fr0])
    full = np.array([an.env[f:f + span].max() for f in fr0])
    # Chords usually change on beat 1: harmony difference across each beat.
    fr = np.clip(librosa.time_to_frames(b, sr=SR, hop_length=HOP), 0, an.chroma.shape[1] - 1)
    w = max(2, int(0.9 * np.median(np.diff(b)) / FRAME_SEC))   # one beat each side
    change = np.zeros(len(b))
    for i, f in enumerate(fr):
        x = an.chroma[:, max(0, f - w):f].mean(axis=1) if f > 0 else an.chroma[:, 0]
        y = an.chroma[:, f:f + w].mean(axis=1)
        change[i] = 1 - (x @ y) / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9)
    cues = [c / (c.mean() + 1e-9) for c in (lo, full, change)]
    first = int(np.argmax(inside))
    scores = [np.mean(cues[0][r::per_measure]) + 0.5 * np.mean(cues[1][r::per_measure])
              + np.mean(cues[2][r::per_measure]) for r in range(per_measure)]
    return (first + int(np.argmax(scores))) % per_measure


def extend_grid(beats, t_lo, t_hi):
    """Extend a beat list backward to t_lo and forward past t_hi."""
    p0 = float(np.median(np.diff(beats[:5])))
    p1 = float(np.median(np.diff(beats[-5:])))
    n_pre = int(math.ceil((beats[0] - t_lo) / p0)) + 1
    n_post = int(math.ceil((t_hi - beats[-1]) / p1)) + 1
    pre = beats[0] - p0 * np.arange(n_pre, 0, -1)
    post = beats[-1] + p1 * np.arange(1, n_post + 1)
    return np.concatenate([pre, beats, post]), n_pre


def segment_tempo(T, tol):
    """Greedy piecewise-constant tempo: one BPM marker per stretch whose beats
    all sit within `tol` seconds of a straight line."""
    anchors, s, e, n = [0], 0, 1, len(T)
    while e < n - 1:
        c = e + 1
        line = T[s] + (np.arange(s, c + 1) - s) * (T[c] - T[s]) / (c - s)
        if np.max(np.abs(T[s:c + 1] - line)) <= tol:
            e = c
        else:
            anchors.append(e)
            s, e = e, e + 1
    anchors.append(n - 1)
    events = []
    for a, b in zip(anchors[:-1], anchors[1:]):
        mbpm = int(round(60.0 * (b - a) / (T[b] - T[a]) * 1000))
        if not events or events[-1][1] != mbpm:
            events.append((a * RES, mbpm))
    return events


def build_tempo(an, args):
    """Returns (TempoMap, pad_seconds, mode, bpm_summary).

    The audio gets `pad_seconds` of silence in front so that tick 0 is a
    downbeat and there is a lead-in before the music starts."""
    per_measure = args.time_signature
    beats = an.beats
    if len(beats) < 8:
        # Too little rhythm to track (very short or ambient); fall back to a guess.
        bpm = args.bpm or args.bpm_hint or an.tempo_guess or 120.0
        p = 60.0 / bpm
        beats = np.arange(0, an.duration, p) + (args.first_downbeat or 0.0)
    period = 60.0 / args.bpm if args.bpm else None

    t0, p, resid = fit_constant(beats, period, args.first_downbeat)
    # Steady if most beats sit on one constant grid (a few tracker slips are fine).
    steady = np.mean(resid < min(0.035, 0.08 * p)) >= 0.75
    mode = args.tempo_map
    if args.bpm:
        mode = "constant"
    elif mode == "auto":
        mode = "constant" if steady else "variable"

    lead = max(args.lead_in, 0.0)
    t_lo = -(lead + 3 * per_measure * p + 2.0)
    t_hi = an.duration + 4 * p
    if mode == "constant":
        k = np.arange(math.floor((t_lo - t0) / p), math.ceil((t_hi - t0) / p) + 1)
        grid = t0 + k * p
    else:
        clean = smooth_beats(clean_beats(beats))
        grid, _ = extend_grid(clean, t_lo, t_hi)

    if args.first_downbeat is not None:
        phase = int(np.argmin(np.abs(grid - args.first_downbeat))) % per_measure
    else:
        phase = downbeat_phase(grid, an, per_measure)

    # Latest downbeat that leaves at least `lead` seconds before the audio starts.
    idx = np.arange(len(grid))
    cands = idx[(idx % per_measure == phase) & (grid <= -lead + 1e-9)]
    i0 = int(cands[-1])
    pad = -float(grid[i0])
    T = grid[i0:] + pad

    if mode == "constant":
        events = [(0, int(round(60.0 / p * 1000)))]
    else:
        events = segment_tempo(T, tol=args.tempo_tolerance / 1000.0)
    tempo = TempoMap(events)

    bpms = np.array([e[1] for e in events]) / 1000.0
    summary = (f"{bpms[0]:.2f} BPM" if len(bpms) == 1 else
               f"{len(bpms)} tempo markers, {np.min(bpms):.1f}-{np.max(bpms):.1f} BPM "
               f"(median {np.median(bpms):.1f})")
    return tempo, pad, mode, summary


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

# Harmonic template: a note at CQT bin c also lights up c+12, c+19, c+24, c+28.
HARMONICS = [(0, 1.0), (12, 0.8), (19, 0.6), (24, 0.5), (28, 0.4)]
CHORD_RATIO = 0.55   # a second pitch this strong (vs the first) makes a chord


def salience(spec):
    nb = len(spec)
    sal = np.zeros(nb)
    for off, w in HARMONICS:
        sal[:nb - off] += w * spec[off:]
    return sal


def best_pitch(sal, level=None):
    """Strongest pitch, corrected for octave errors: if a pitch an octave (or
    octave+fifth, two octaves) lower explains the peak almost as well, the
    peak is that note's harmonic, which happens when a still-ringing note is
    re-struck and its upper harmonics jump more than its fundamental."""
    c = int(np.argmax(sal))
    if level is None:
        return c
    for sub in (24, 19, 12):
        # ...and that lower note must really be sounding (level = dB spectrum)
        if c - sub >= 0 and sal[c - sub] >= 0.75 * sal[c] and level[c - sub] >= level[c] - 6.0:
            return c - sub
    return c


def find_pitches(rise, level=None, max_voices=3):
    """Iterative multi-pitch estimate: take the strongest pitch, remove its
    harmonics, and look for another one that is nearly as strong.
    Returns the pitches (CQT bins), strongest first."""
    nb = len(rise)
    r = rise.copy()
    sal = salience(r)
    f0s = [best_pitch(sal, level)]
    top = sal[f0s[0]]
    if top <= 0:
        return f0s
    for _ in range(max_voices - 1):
        for c in f0s:
            for off, _ in HARMONICS:
                r[max(0, c + off - 1):min(nb, c + off + 2)] = 0
        sal = salience(r)
        c2 = best_pitch(sal, level)
        if sal[c2] < CHORD_RATIO * top or any(abs(c2 - c) <= 1 for c in f0s):
            break
        f0s.append(c2)
    return f0s


def detect_notes(an, density, keep_unpitched=False, mix=None, gate_db=30.0, src="mix"):
    """Find the notes in one audio track.

    When `an` is an isolated instrument (a stem) pass the full mix's analysis
    as `mix`: notes are then only kept where that instrument is actually
    audible, not where only faint bleed from other instruments remains."""
    env = an.note_env
    if mix is not None:
        # An isolated instrument: judge each attack against the playing around
        # it (3 s), not the loudest moment of the song. Distorted guitar is so
        # compressed that chord changes barely stand out globally.
        ref = maximum_filter1d(env, size=int(3.0 / FRAME_SEC))
        env = env / np.maximum(ref, 0.1 * np.percentile(env, 99) + 1e-9)
    wait = max(1, int(round(0.06 / FRAME_SEC)))
    frames = librosa.onset.onset_detect(
        onset_envelope=env, sr=SR, hop_length=HOP, units="frames",
        delta=0.07 / max(density, 0.05), wait=wait)
    frames = frames[frames < len(an.times)]
    if len(frames) == 0:
        return []

    # Accent = this onset relative to the loudest onsets around it (+-3 s).
    local_max = maximum_filter1d(env, size=max(3, int(6.0 / FRAME_SEC)))
    strength = env[frames] / np.maximum(local_max[frames], 1e-9)

    times, found = refine_times(an, an.times[frames], before=0.05, after=0.035)
    # No sharp attack nearby = a smeared echo of an earlier note, not a new one.
    frames, times, strength = frames[found], times[found], strength[found]

    audible = None
    if mix is not None:
        audible = presence(an, mix, gate_db)

    Cdb, Cw = an.Cdb, an.Cw
    nb, nf = Cw.shape
    max_ring = int(8.0 / FRAME_SEC)
    notes = []
    for i, f in enumerate(frames):
        if audible is not None and not audible[min(f + 2, len(audible) - 1)]:
            continue                    # only bleed from other instruments here
        nxt = frames[i + 1] if i + 1 < len(frames) else nf
        a = min(f + 1, nf - 1)
        b = max(a + 1, min(f + 5, nxt, nf))
        after = Cdb[:, a:b].mean(axis=1)
        before = Cdb[:, max(0, f - 4):max(1, f - 1)].mean(axis=1)
        # What got louder at this onset = the note(s) just played.
        rise = np.clip(after - before, 0, None)
        pitched = rise.max() >= 4.0     # something tonal started here (not just a hi-hat)
        if not pitched:                 # e.g. a re-struck sustained note: use what stands out
            rise = np.clip(Cw[:, a:b].mean(axis=1), 0, None)
        sal = salience(rise)
        # Octave correction needs a clean spectrum: only for isolated instruments.
        level = after if mix is not None else None
        f0s = find_pitches(rise, level) if pitched else [best_pitch(sal, level)]
        pk = f0s[0]
        # Sub-bin refinement for a smoother contour.
        if 0 < pk < nb - 1:
            l, c, r = sal[pk - 1], sal[pk], sal[pk + 1]
            den = l - 2 * c + r
            pitch = pk + (0.5 * (l - r) / den if den < 0 else 0.0)
        else:
            pitch = float(pk)
        if len(f0s) > 1 and mix is not None:
            pitch = float(min(f0s))     # chords follow their lowest note (the root)

        # Ring time: follow that pitch until it drops 8 dB below its attack.
        track = Cdb[max(0, pk - 1):pk + 2, f:min(nf, f + max_ring)].max(axis=0)
        attack = track[:6].max()
        gone = np.where(track < attack - 8.0)[0]
        gone = gone[gone >= 3]
        end_f = f + (int(gone[0]) if len(gone) else len(track))
        # Unpitched attacks: drum hits in a mix, but in an isolated instrument
        # they're re-struck chords and muted strums, so keep the clear ones.
        if not (pitched or keep_unpitched or strength[i] >= (0.35 if mix is not None else 0.7)):
            continue                    # drums only: not something a guitarist plays
        notes.append(Note(time=float(times[i]), strength=float(strength[i]),
                          pitch=float(pitch), end_time=float(end_f * FRAME_SEC),
                          poly=len(f0s), src=src))
    return notes


def presence(part, mix, gate_db):
    """Per frame: is this isolated instrument really playing? It must be within
    `gate_db` of its own loud passages and not buried far below the mix
    (separated stems always carry some quiet bleed from everything else)."""
    n = min(len(part.rms), len(mix.rms))
    pr, mr = part.rms[:n], mix.rms[:n]
    ref = np.percentile(pr, 95) + 1e-9
    level = 20 * np.log10(pr / ref + 1e-9)
    share = 20 * np.log10((pr + 1e-9) / (mr + 1e-9))
    on = (level > -gate_db) & (share > -gate_db * 0.6)
    # smooth over ~120 ms so a note's own attack/decay doesn't flicker
    return maximum_filter1d(on.astype(np.uint8), size=int(0.12 / FRAME_SEC) | 1).astype(bool)


def quantize(notes, tempo, pad, triplets):
    """Snap notes to 1/16 (optionally 1/12 triplet) ticks; merge collisions."""
    t = np.array([n.time for n in notes]) + pad
    tf = tempo.time_to_tick(t)
    q = np.round(tf / (RES / 4)) * (RES / 4)
    if triplets:
        q3 = np.round(tf / (RES / 3)) * (RES / 3)
        better = np.abs(tf - q3) < 0.5 * np.abs(tf - q)
        q[better] = q3[better]
    err = np.abs(tempo.tick_to_time(q) - t)
    by_tick = {}
    for n, tick in zip(notes, q.astype(int)):
        n.tick = int(tick)
        if tick not in by_tick or n.strength > by_tick[tick].strength:
            by_tick[tick] = n
    out = sorted(by_tick.values(), key=lambda n: n.tick)
    return out, float(np.median(err) * 1000) if len(err) else 0.0


def thin(notes, cfg, n_expert, tempo, pad):
    """Keep the strongest notes that respect this difficulty's spacing,
    preferring notes that land on the beat."""
    target = max(1, int(round(cfg["keep"] * n_expert)))
    gap_t = int(cfg["gap_beats"] * RES)

    def score(n):
        on = 1.0 if n.tick % RES == 0 else 0.85 if n.tick % (RES // 2) == 0 else 0.6
        return n.strength * on

    kept_ticks, kept_times, kept = [], [], []
    for n in sorted(notes, key=score, reverse=True):
        if len(kept) >= target:
            break
        t = n.time + pad
        i = np.searchsorted(kept_ticks, n.tick)
        ok = True
        for j in (i - 1, i):
            if 0 <= j < len(kept_ticks):
                if abs(kept_ticks[j] - n.tick) < gap_t or abs(kept_times[j] - t) < cfg["gap_sec"]:
                    ok = False
        if ok:
            kept_ticks.insert(i, n.tick)
            kept_times.insert(i, t)
            kept.insert(i, n)
    return kept


def assign_frets(notes, cfg, allow_chords, real_chords=False):
    """Pick frets that follow the melody's contour, like a human charter:
    higher pitch -> further right, repeated pitch -> same fret, bigger leaps ->
    bigger jumps, while drifting back toward the middle of the local range.
    Solved with dynamic programming (Viterbi) so a rising run starts low
    enough to keep rising instead of piling up on the top fret."""
    L = cfg["lanes"]
    if not notes:
        return
    p = np.array([n.pitch for n in notes])
    t = np.array([n.time for n in notes])
    n = len(notes)
    lanes_arr = np.arange(L)

    # Where each note sits within the pitch range of the surrounding ~8 seconds.
    base = np.empty(n)
    for i in range(n):
        lo_i, hi_i = np.searchsorted(t, t[i] - 4.0), np.searchsorted(t, t[i] + 4.0)
        win = p[lo_i:hi_i]
        lo, hi = np.percentile(win, 10), np.percentile(win, 90)
        span = max(hi - lo, 5.0)
        base[i] = np.clip((p[i] - ((lo + hi) / 2 - span / 2)) / span, 0, 1) * (L - 1)

    def unary(i):
        return 0.35 * (lanes_arr - base[i]) ** 2

    # trans[a, b]: cost of moving from fret a to fret b given the pitch change.
    def transition(i):
        dp = p[i] - p[i - 1]
        dl = lanes_arr[np.newaxis, :] - lanes_arr[:, np.newaxis]
        if t[i] - t[i - 1] > 1.5 or notes[i].src != notes[i - 1].src:
            return np.zeros((L, L))                       # a rest or new instrument: start fresh
        if abs(dp) < 0.6:
            return 6.0 * np.abs(dl)                        # same note, same fret
        step = np.clip(round(abs(dp) / 3.0), 1, L - 1)
        wrong_way = (np.sign(dl) != np.sign(dp))
        return 12.0 * wrong_way + 0.6 * (np.abs(dl) - step) ** 2

    cost = unary(0)
    back = np.zeros((n, L), int)
    for i in range(1, n):
        total = cost[:, np.newaxis] + transition(i)
        back[i] = np.argmin(total, axis=0)
        cost = total[back[i], lanes_arr] + unary(i)
    lanes = [int(np.argmin(cost))]
    for i in range(n - 1, 0, -1):
        lanes.append(int(back[i, lanes[-1]]))
    lanes = lanes[::-1]

    if real_chords:
        place_real_chords(notes, lanes, cfg, allow_chords)
        return
    strengths = np.array([n.strength for n in notes])
    chord_cut = np.quantile(strengths, 1 - cfg["chord"]) if cfg["chord"] > 0 else np.inf
    triple_cut = np.quantile(strengths, 1 - cfg["triple"]) if cfg["triple"] > 0 else np.inf
    for i, n in enumerate(notes):
        lane = lanes[i]
        n.lane = lane
        roomy = ((i == 0 or n.tick - notes[i - 1].tick >= RES // 2) and
                 (i == len(notes) - 1 or notes[i + 1].tick - n.tick >= RES // 2))
        if allow_chords and roomy and L >= 3 and n.strength >= triple_cut:
            s = min(lane, L - 3)
            n.frets = (s, s + 1, s + 2)
        elif allow_chords and roomy and L >= 2 and n.strength >= chord_cut:
            n.frets = (lane, lane + 1) if lane <= L - 2 else (lane - 1, lane)
        else:
            n.frets = (lane,)


def place_real_chords(notes, lanes, cfg, allow_chords):
    """Chords where the instrument really played several pitches at once
    (power chords, strums, piano chords), sized for the difficulty."""
    L, gap = cfg["lanes"], cfg["chord_gap"]
    for i, n in enumerate(notes):
        lane = lanes[i]
        n.lane = lane
        roomy = ((i == 0 or n.tick - notes[i - 1].tick >= gap) and
                 (i == len(notes) - 1 or notes[i + 1].tick - n.tick >= gap))
        size = min(n.poly, cfg["max_chord"], L) if allow_chords and roomy else 1
        if size <= 1:
            n.frets = (lane,)
        else:
            start = min(lane, L - size)       # the root on the left, like a fretting hand
            n.frets = tuple(range(start, start + size))


def assign_sustains(notes, tempo, pad, min_beats):
    min_ticks = int(min_beats * RES)
    for i, n in enumerate(notes):
        nxt = notes[i + 1].tick if i + 1 < len(notes) else 10 ** 9
        end = int(tempo.time_to_tick(n.end_time + pad) // (RES // 8)) * (RES // 8)
        end = min(end, nxt - RES // 4)        # let go a 1/16 before the next note
        sus = end - n.tick
        n.sustain = sus if sus >= min_ticks else 0


# --------------------------------------------------------------------------
# Instruments: separating the mix and choosing what to chart
# --------------------------------------------------------------------------

# File names recognised in a --stems folder, by role.
STEM_NAMES = {
    "guitar": ("guitar", "guitars", "gtr", "lead_guitar", "rhythm_guitar"),
    "bass": ("bass",),
    "drums": ("drums", "drum", "percussion"),
    "vocals": ("vocals", "vocal", "vox", "voice"),
    "piano": ("piano", "keys", "keyboard", "keyboards"),
    "other": ("other", "synth", "synths", "harmonica", "strings", "organ", "horns"),
}
LABEL = {"guitar": "guitar", "bass": "bass", "piano": "piano/keys", "other": "other (synth, organ, "
         "harmonica...)", "vocals": "vocals", "drums": "drums"}
# How happily a part is handed to each instrument when the guitar isn't playing.
FILL_WEIGHT = {"piano": 0.9, "other": 0.9, "guitar": 1.0, "bass": 0.6, "vocals": 0.5}
AUDIO_EXTS = (".wav", ".flac", ".ogg", ".mp3", ".m4a", ".opus", ".aif", ".aiff")


class SeparationUnavailable(Exception):
    pass


def fit_audio(y, from_sr, to_sr, n):
    """Resample a (channels x samples) track and pad/trim it to n samples."""
    if y.ndim == 1:
        y = y[np.newaxis, :]
    if from_sr != to_sr:
        y = librosa.resample(y, orig_sr=from_sr, target_sr=to_sr)
    if y.shape[1] < n:
        y = np.pad(y, ((0, 0), (0, n - y.shape[1])))
    return y[:, :n].astype(np.float32)


def load_stem_folder(folder, sr, n):
    stems = {}
    for fname in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(fname)
        if ext.lower() not in AUDIO_EXTS:
            continue
        key = stem.lower().replace(" ", "_").replace("-", "_")
        role = next((r for r, names in STEM_NAMES.items() if key in names), None)
        if role is None or role in stems:
            log(f"  (ignoring {fname}: not a recognised stem name)")
            continue
        y, ssr = decode_audio(os.path.join(folder, fname))
        stems[role] = fit_audio(y, ssr, sr, n)
    if not stems:
        sys.exit(f"No stems found in {folder} (expected files like guitar.wav, bass.wav, piano.wav...)")
    log(f"  stems: {', '.join(stems)}")
    return stems


def cache_root():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "ch-autochart", "stems")


def file_digest(path):
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:20]


def bundled_models_folder(model_name):
    """A folder holding the model offline (<name>.yaml + weights): the one the
    Windows program ships with, or models/ next to this file."""
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for folder in (os.environ.get("CH_AUTOCHART_MODELS"), os.path.join(here, "models")):
        if folder and os.path.isfile(os.path.join(folder, f"{model_name}.yaml")):
            return folder
    return None


def separate_demucs(path, y, sr, model_name, device, use_cache):
    """Split the mix into instruments with Demucs (htdemucs_6s: drums, bass,
    other, vocals, guitar, piano). Results are cached per song."""
    folder = os.path.join(cache_root(), f"{file_digest(path)}-{model_name}")
    n = y.shape[1]
    if use_cache and os.path.isfile(os.path.join(folder, "done")):
        log("  using the cached separation from an earlier run")
        stems = {}
        for fname in os.listdir(folder):
            if fname.endswith(".flac"):
                a, ssr = sf.read(os.path.join(folder, fname), always_2d=True, dtype="float32")
                stems[fname[:-5]] = fit_audio(a.T, ssr, sr, n)
        return stems

    try:
        import torch
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
    except ImportError:
        raise SeparationUnavailable("Demucs isn't installed (pip install demucs)")

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    offline = bundled_models_folder(model_name)
    log(f"  loading the {model_name} separation model" +
        ("..." if offline else " (downloaded once on first use)..."))
    try:
        if offline:
            from pathlib import Path
            model = get_model(model_name, repo=Path(offline))
        else:
            model = get_model(model_name)
    except Exception as err:
        raise SeparationUnavailable(f"couldn't load the {model_name} model: {err}")
    model.eval()
    msr = int(model.samplerate)
    wav = y[:2] if y.shape[0] >= 2 else np.repeat(y[:1], 2, axis=0)
    if sr != msr:
        wav = librosa.resample(wav, orig_sr=sr, target_sr=msr)
    t = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32))
    ref = t.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    log(f"  separating instruments on {device.upper()} "
        f"({'usually under a minute' if device != 'cpu' else 'this can take a few minutes'})...")
    import demucs.apply as dapply
    real_tqdm = dapply.tqdm
    if PROGRESS_HOOK:
        class _Progress:  # forwards Demucs's progress bar to the app
            @staticmethod
            def tqdm(iterable, total=None, **kw):
                items = list(iterable)
                for i, item in enumerate(items):
                    PROGRESS_HOOK(i / max(len(items), 1))
                    yield item
                PROGRESS_HOOK(1.0)
        dapply.tqdm = _Progress
    try:
        with torch.no_grad():
            out = apply_model(model, ((t - mean) / std)[None], device=device, shifts=1,
                              split=True, overlap=0.25, progress=True)[0]
    finally:
        dapply.tqdm = real_tqdm
    out = (out * std + mean).cpu().numpy()
    stems = {name: out[i] for i, name in enumerate(model.sources)}

    if use_cache:
        try:
            os.makedirs(folder, exist_ok=True)
            for name, a in stems.items():
                sf.write(os.path.join(folder, name + ".flac"), np.clip(a.T, -1, 1), msr, subtype="PCM_24")
            open(os.path.join(folder, "done"), "w").close()
        except OSError as err:
            log(f"  (couldn't cache the stems: {err})")
    return {name: fit_audio(a, msr, sr, n) for name, a in stems.items()}


def get_stems(args, y, sr):
    """The isolated instruments available for charting ({} = only the mix)."""
    n = y.shape[1]
    if args.stem:
        s, ssr = decode_audio(args.stem)
        return {args.instrument: fit_audio(s, ssr, sr, n)}
    if args.stems:
        return load_stem_folder(args.stems, sr, n)
    if args.separate == "off":
        return {}
    try:
        return separate_demucs(args.audio, y, sr, args.demucs_model, args.device, not args.no_cache)
    except SeparationUnavailable as err:
        if args.separate == "on":
            sys.exit(f"Separation failed: {err}")
        log(f"  {err}: charting the full mix instead.")
        log("  For a chart of just the guitar:  pip install demucs")
        return {}


def bar_index(times, tempo, pad, bar_ticks):
    return (tempo.time_to_tick(np.asarray(times) + pad) // bar_ticks).astype(int)


def choose_sources(parts, primary, mode, tempo, pad, bar_ticks, n_bars, section_bars, frame_times,
                   weights=None):
    """Decide, bar by bar, which instrument the chart follows.

    The primary instrument (guitar) wins wherever it is really playing. Where
    it is silent, the busiest other instrument (keys, synth/organ/harmonica,
    then bass or vocals) can take over, the way Guitar Hero charts the piano
    intro or the organ break. In "smart" mode another instrument can also
    take over when the guitar is only holding a chord while something else
    carries the song. Switching costs something, so the chart sticks with an
    instrument for whole passages instead of flickering between them; switches
    are cheaper where a new section starts."""
    names = list(parts)
    act = {s: np.zeros(n_bars) for s in names}
    pres = {s: np.zeros(n_bars) for s in names}
    variety = {s: np.zeros(n_bars) for s in names}
    fb = np.clip(bar_index(frame_times, tempo, pad, bar_ticks), 0, n_bars - 1)
    for s in names:
        notes, on = parts[s]
        for n in notes:
            b = n.tick // bar_ticks
            if 0 <= b < n_bars:
                act[s][b] += 1
        m = min(len(on), len(fb))
        pres[s] = np.bincount(fb[:m], weights=on[:m].astype(float), minlength=n_bars)[:n_bars] / \
            np.maximum(np.bincount(fb[:m], minlength=n_bars)[:n_bars], 1)
        by_bar = {}
        for n in notes:
            by_bar.setdefault(n.tick // bar_ticks, set()).add(int(round(n.pitch)))
        for b, ps in by_bar.items():
            if 0 <= b < n_bars:
                variety[s][b] = len(ps)

    score = np.zeros((len(names), n_bars))
    for i, s in enumerate(names):
        for b in range(n_bars):
            if s == primary:
                playing = act[s][b] > 0 or pres[s][b] > 0.25
                score[i, b] = 2.0 + 0.5 * min(act[s][b], 8) if playing else 0.0
            else:
                p_playing = act[primary][b] > 0 or pres[primary][b] > 0.25 if primary in act else False
                p_idle = act[primary][b] <= 1 if primary in act else True
                allowed = (mode == "smart" and p_idle) or not p_playing
                busy = act[s][b] >= 2
                fun = 0.5 * min(act[s][b], 8) + 0.15 * min(variety[s][b], 6)
                score[i, b] = (weights or FILL_WEIGHT).get(s, 0.5) * fun if (allowed and busy) else -1.0

    # Viterbi over bars: maximise score minus switching costs.
    k = len(names)
    switch = np.where(np.isin(np.arange(n_bars), section_bars), 1.0, 2.0)
    total = score[:, 0].copy()
    back = np.zeros((k, n_bars), int)
    for b in range(1, n_bars):
        trans = total[:, None] - switch[b] * (1 - np.eye(k))
        back[:, b] = np.argmax(trans, axis=0)
        total = trans[back[:, b], np.arange(k)] + score[:, b]
    path = [int(np.argmax(total))]
    for b in range(n_bars - 1, 0, -1):
        path.append(int(back[path[-1], b]))
    return [names[i] for i in path[::-1]]


def charted_audio(stems, chosen, tempo, pad, bar_ticks, sr, n, fade=0.02):
    """The audio of whatever the chart follows (guitar, or keys where the
    keys were charted), to be muted when the player misses notes."""
    edges = tempo.tick_to_time(np.arange(len(chosen) + 1) * bar_ticks) - pad
    out = np.zeros_like(next(iter(stems.values())))
    for s in set(chosen):
        gain = np.zeros(n, np.float32)
        for b, c in enumerate(chosen):
            if c == s:
                a, e = (int(np.clip(x * sr, 0, n)) for x in (edges[b], edges[b + 1]))
                gain[a:e] = 1.0
        # short ramps instead of hard cuts at the bar lines
        w = max(1, int(fade * sr))
        gain = np.convolve(gain, np.ones(w, np.float32) / w, mode="same")
        out += stems[s] * gain
    return out


# --------------------------------------------------------------------------
# Song structure: sections, star power, preview
# --------------------------------------------------------------------------

def find_sections(an, tempo, pad, per_measure):
    """Split the song at changes in harmony/timbre and name repeated parts."""
    bar_ticks = per_measure * RES
    end_tick = tempo.time_to_tick(an.duration + pad)
    # First bar that is mostly music (not the silent lead-in).
    first_bar = int(round(tempo.time_to_tick(pad) / bar_ticks))
    bar_starts = np.arange(first_bar, int(end_tick // bar_ticks) + 1) * bar_ticks
    bar_times = tempo.tick_to_time(bar_starts) - pad
    frames = np.unique(np.clip(librosa.time_to_frames(bar_times, sr=SR, hop_length=HOP),
                               0, len(an.times) - 1))
    if len(frames) < 6:
        return [(int(first_bar * bar_ticks), "Song")], []

    # Column j = bar (first_bar + j); pad=False keeps that mapping exact.
    chroma = librosa.util.sync(an.chroma, frames, aggregate=np.median, pad=False)
    timbre = librosa.util.sync(an.mfcc, frames, aggregate=np.mean, pad=False)
    loud = librosa.util.sync(an.rms[np.newaxis, :], frames, aggregate=np.mean, pad=False)[0]
    feats = np.vstack([chroma, timbre / 20.0])
    feats = (feats - feats.mean(axis=1, keepdims=True)) / (feats.std(axis=1, keepdims=True) + 1e-9)
    n_bars = feats.shape[1]
    k = int(np.clip(round(n_bars / 8), 2, 30))
    bounds = list(librosa.segment.agglomerative(feats, k))
    bounds = sorted(set([0] + [int(b) for b in bounds if 0 < b < n_bars]))
    segs = list(zip(bounds, bounds[1:] + [n_bars]))

    # Group segments that sound alike (similar average harmony).
    cen = chroma - chroma.mean(axis=1, keepdims=True)
    labels, protos = [], []
    for a, b in segs:
        v = cen[:, a:b].mean(axis=1)
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = [float(v @ q) for q in protos]
        if sims and max(sims) > 0.6:
            labels.append(int(np.argmax(sims)))
        else:
            protos.append(v)
            labels.append(len(protos) - 1)

    seg_loud = np.array([loud[a:b].mean() for a, b in segs])
    counts = {lab: labels.count(lab) for lab in set(labels)}
    repeated = [lab for lab in counts if counts[lab] > 1]
    names = {}
    if repeated:
        chorus = max(repeated, key=lambda lab: np.mean(seg_loud[[i for i, x in enumerate(labels) if x == lab]]))
        names[chorus] = "Chorus"
        others = [lab for lab in repeated if lab != chorus]
        if others:
            verse = max(others, key=lambda lab: (counts[lab], -labels.index(lab)))
            names[verse] = "Verse"
    letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    for lab in sorted(set(labels), key=labels.index):
        names.setdefault(lab, None)

    out, seen = [], {}
    for i, ((a, _), lab) in enumerate(zip(segs, labels)):
        name = names[lab]
        if name is None:
            if i == 0 and seg_loud[i] <= np.median(seg_loud):
                name = "Intro"
            elif i == len(segs) - 1 and counts[lab] == 1:
                name = "Outro"
            elif counts[lab] == 1 and i > len(segs) / 2:
                name = "Bridge"
            else:
                names[lab] = name = f"Part {next(letters)}"
        if counts[lab] > 1 or name in seen:
            seen[name] = seen.get(name, 0) + 1
            name = f"{name} {seen[name]}"
        else:
            seen[name] = 1
        out.append((int((first_bar + a) * bar_ticks), name))
    return out, list(zip([s for s, _ in out], seg_loud))


def star_power(expert_notes, per_measure, every):
    """2-bar star power phrases, roughly every `every` bars where there are notes."""
    if every <= 0 or not expert_notes:
        return []
    bar = per_measure * RES
    ticks = np.array([n.tick for n in expert_notes])
    bar_idx = int(ticks[0] // bar) + 2
    last = int(ticks[-1] // bar)
    phrases = []
    while bar_idx + 2 <= last:
        start, end = bar_idx * bar, (bar_idx + 2) * bar
        if np.sum((ticks >= start) & (ticks < end)) >= 4:
            phrases.append((start, end - start))
            bar_idx += every
        else:
            bar_idx += 1
    return phrases


def preview_start(sections, an, pad):
    for tick_time, name in sections:
        if name.startswith("Chorus"):
            return tick_time
    # Fallback: loudest 30 seconds.
    win = max(1, int(30.0 / FRAME_SEC))
    if len(an.rms) <= win:
        return pad
    csum = np.convolve(an.rms, np.ones(win), mode="valid")
    return float(an.times[int(np.argmax(csum))]) + pad


# --------------------------------------------------------------------------
# Writing the song folder
# --------------------------------------------------------------------------

def q(text):
    return '"' + str(text).replace('"', "'") + '"'


def write_chart(path, meta, tempo, per_measure, sections, tracks, instrument):
    lines = ["[Song]", "{",
             f"  Name = {q(meta['title'])}",
             f"  Artist = {q(meta['artist'])}",
             f"  Charter = {q(meta['charter'])}",
             f"  Album = {q(meta['album'])}",
             f"  Year = {q(', ' + meta['year'] if meta['year'] else '')}",
             "  Offset = 0",
             f"  Resolution = {RES}",
             "  Player2 = bass",
             "  Difficulty = 0",
             "  PreviewStart = 0",
             "  PreviewEnd = 0",
             f"  Genre = {q(meta['genre'])}",
             '  MediaType = "cd"',
             '  MusicStream = "song.ogg"',
             "}", "[SyncTrack]", "{",
             f"  0 = TS {per_measure}"]
    lines += [f"  {tick} = B {mbpm}" for tick, mbpm in tempo.events]
    lines += ["}", "[Events]", "{"]
    lines += [f'  {tick} = E "section {name}"' for tick, name in sections]
    lines += ["}"]
    for diff in DIFF_ORDER:
        if diff not in tracks:
            continue
        notes, phrases = tracks[diff]
        rows = []
        for n in notes:
            for fret in n.frets:
                rows.append((n.tick, 0, f"N {fret} {n.sustain}"))
        for start, length in phrases:
            rows.append((start, 1, f"S 2 {length}"))
        rows.sort()
        lines += [f"[{diff.capitalize()}{TRACK_SUFFIX[instrument]}]", "{"]
        lines += [f"  {tick} = {body}" for tick, _, body in rows]
        lines += ["}"]
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines) + "\n")


def difficulty_rating(notes, seconds):
    nps = len(notes) / max(seconds, 1.0)
    return int(np.searchsorted([1.5, 2.5, 3.5, 4.5, 5.5, 7.0], nps))


def write_ini(path, meta, song_ms, preview_ms, rating, instrument):
    diff_key = "diff_bass" if instrument == "bass" else "diff_guitar"
    rows = [("name", meta["title"]), ("artist", meta["artist"]), ("album", meta["album"]),
            ("genre", meta["genre"]), ("year", meta["year"]), ("charter", meta["charter"]),
            ("song_length", str(song_ms)), ("preview_start_time", str(preview_ms)),
            (diff_key, str(rating)), ("delay", "0"),
            ("loading_phrase", f"Auto-generated by ch-autochart {__version__}.")]
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("[song]\n" + "".join(f"{k} = {v}\n" for k, v in rows))


def write_album_art(src, dst):
    try:
        from PIL import Image
        img = Image.open(src).convert("RGB")
        side = min(img.size)
        left, top = (img.width - side) // 2, (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize((512, 512), Image.LANCZOS)
        img.save(dst)
    except ImportError:
        if src.lower().endswith(".png"):
            shutil.copyfile(src, dst)
        else:
            log("  (skipping album art: install Pillow to convert it to PNG)")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate a Clone Hero song folder (notes.chart, song.ogg, song.ini) from an audio file.")
    ap.add_argument("audio", help="input audio file (mp3, ogg, wav, flac; m4a/opus etc. need ffmpeg)")
    meta = ap.add_argument_group("metadata (defaults come from the file's tags or name)")
    meta.add_argument("--title")
    meta.add_argument("--artist")
    meta.add_argument("--album")
    meta.add_argument("--genre")
    meta.add_argument("--year")
    meta.add_argument("--charter", default="ch-autochart")
    meta.add_argument("--album-art", help="image to use as album.png")

    out = ap.add_argument_group("output")
    out.add_argument("-o", "--out", default=".", help="folder to create the song folder in (default: here)")
    out.add_argument("--force", action="store_true", help="overwrite an existing song folder")

    chart = ap.add_argument_group("chart")
    chart.add_argument("--instrument", choices=["guitar", "bass"], default="guitar")
    chart.add_argument("--difficulties", default="expert,hard,medium,easy",
                       help="comma-separated subset of expert,hard,medium,easy")
    chart.add_argument("--density", type=float, default=1.0,
                       help="note detection sensitivity; >1 = more notes, <1 = fewer (default 1.0)")
    chart.add_argument("--all-onsets", action="store_true",
                       help="also chart onsets with no pitched content (drum hits); off by default")
    chart.add_argument("--triplets", action="store_true", help="also allow triplet (1/12) timing")
    chart.add_argument("--no-chords", action="store_true")
    chart.add_argument("--no-sustains", action="store_true")
    chart.add_argument("--min-sustain", type=float, default=0.5,
                       help="shortest sustain to keep, in beats (default 0.5)")
    chart.add_argument("--star-power-every", type=int, default=8,
                       help="bars between star power phrases; 0 disables (default 8)")

    inst = ap.add_argument_group("instruments (what gets charted)")
    inst.add_argument("--separate", choices=["auto", "on", "off"], default="auto",
                      help="split the song into instruments with Demucs and chart only the guitar "
                           "(auto = when Demucs is installed; off = chart the full mix)")
    inst.add_argument("--fill", choices=["smart", "gaps", "off"], default="smart",
                      help="where the guitar isn't playing, chart another instrument: "
                           "smart = in gaps and where the guitar only holds a chord while something "
                           "else carries the song (default); gaps = only where the guitar is silent; "
                           "off = guitar only, rests stay rests")
    inst.add_argument("--fill-from", default=None,
                      help="instruments allowed to fill in, in any of piano,other,bass,vocals,guitar "
                           "(default: piano,other for guitar charts; none for bass charts)")
    inst.add_argument("--split-audio", action="store_true",
                      help="write the charted part to its own track (guitar.ogg / bass.ogg) so "
                           "Clone Hero mutes it when you miss, like the real games")
    inst.add_argument("--stems", metavar="FOLDER",
                      help="use stems you already separated (guitar.wav, bass.wav, piano.wav, "
                           "vocals.wav, drums.wav, other.wav...) instead of running Demucs")
    inst.add_argument("--stem", help="a single isolated guitar (or bass) track to chart")
    inst.add_argument("--gate-db", type=float, default=30.0,
                      help="ignore an instrument when it's this many dB below its loud parts; lower "
                           "if separation bleed gets charted, raise if quiet playing is missed (30)")
    inst.add_argument("--demucs-model", default="htdemucs_6s", help="Demucs model (default htdemucs_6s)")
    inst.add_argument("--device", default="auto", help="auto, cpu, cuda or mps (default auto)")
    inst.add_argument("--no-cache", action="store_true", help="don't reuse/save separated stems")

    tempo = ap.add_argument_group("tempo")
    tempo.add_argument("--bpm", type=float, help="force a constant tempo (BPM)")
    tempo.add_argument("--bpm-hint", type=float, help="rough tempo to steer the beat tracker")
    tempo.add_argument("--tempo-map", choices=["auto", "constant", "variable"], default="auto",
                       help="constant = one BPM; variable = follow tempo drift (default: auto-detect)")
    tempo.add_argument("--tempo-tolerance", type=float, default=10.0,
                       help="variable maps: max beat error in ms before adding a BPM marker (default 10)")
    tempo.add_argument("--first-downbeat", type=float,
                       help="time in seconds of a beat 1 in the audio, if the auto-detected one is wrong")
    tempo.add_argument("--time-signature", type=int, default=4, help="beats per bar (default 4)")
    tempo.add_argument("--lead-in", type=float, default=1.5,
                       help="minimum seconds of silence added before the music (default 1.5)")
    args = ap.parse_args(argv)

    args.diffs = [d.strip().lower() for d in args.difficulties.split(",") if d.strip()]
    bad = [d for d in args.diffs if d not in DIFFICULTIES]
    if bad or not args.diffs:
        ap.error(f"unknown difficulty: {', '.join(bad) or '(none)'}")
    if not os.path.isfile(args.audio):
        ap.error(f"no such file: {args.audio}")
    if args.stem and not os.path.isfile(args.stem):
        ap.error(f"no such file: {args.stem}")
    if args.stems and not os.path.isdir(args.stems):
        ap.error(f"no such folder: {args.stems}")
    if args.fill_from is None:
        args.fill_from = "piano,other" if args.instrument == "guitar" else ""
    args.fillers = [f.strip().lower() for f in args.fill_from.split(",") if f.strip()]
    bad = [f for f in args.fillers if f not in FILL_WEIGHT]
    if bad:
        ap.error(f"unknown --fill-from instrument: {', '.join(bad)}")
    return args


# --------------------------------------------------------------------------
# Pipeline (shared by the command line and the web app)
# --------------------------------------------------------------------------

CHARTABLE = ["guitar", "bass", "piano", "other", "vocals"]


@dataclass
class Project:
    """Everything learned about one song, before a chart plan is fixed."""
    args: argparse.Namespace
    y: np.ndarray            # full mix, channels x samples
    sr: int
    stems: dict              # instrument -> audio, same shape as y
    an: Analysis
    tempo: TempoMap
    pad: float               # seconds of silence added in front
    pad_samples: int
    tempo_mode: str
    bpm_text: str
    bar_ticks: int
    n_bars: int
    sections: list           # [(tick, name)]
    first_bar: int
    parts: dict              # instrument -> (notes, per-frame "is playing")
    recommended: list        # instrument per bar (None = rest); None without stems
    mix_notes: list          # full-mix notes, used when there are no stems

    @property
    def primary(self):
        return self.args.instrument

    @property
    def duration(self):
        return self.y.shape[1] / self.sr

    def bar_times(self):
        """Start time of each bar (and the end of the last) in the song's own timeline."""
        return self.tempo.tick_to_time(np.arange(self.n_bars + 1) * self.bar_ticks) - self.pad


def song_meta(args):
    tags = read_tags(args.audio)
    return dict(
        title=args.title or tags.get("title") or os.path.splitext(os.path.basename(args.audio))[0],
        artist=args.artist or tags.get("artist") or tags.get("album_artist") or "Unknown Artist",
        album=args.album or tags.get("album") or "",
        genre=args.genre or tags.get("genre") or "",
        year=args.year or (tags.get("date") or tags.get("year") or "")[:4],
        charter=args.charter)


def prepare(args, candidates=None):
    """Read, separate and analyze a song and recommend a chart plan.
    `candidates` = instruments to transcribe (default: the charted one plus
    the allowed fill-ins); the web app asks for all of them so any can be picked."""
    log(f"Reading {os.path.basename(args.audio)}")
    y, sr = decode_audio(args.audio)
    n = y.shape[1]
    log("Separating instruments")
    stems = get_stems(args, y, sr)

    def mono22(a):
        return librosa.resample(librosa.to_mono(a), orig_sr=sr, target_sr=SR)

    log("Finding the beat")
    an = analyze(mono22(y), args.instrument, args.bpm_hint)
    an.duration = n / sr
    tempo, pad, mode, bpm_text = build_tempo(an, args)
    pad_samples = int(round(pad * sr))
    pad = pad_samples / sr
    log(f"  tempo: {bpm_text} ({mode} map), lead-in {pad:.2f}s")
    bar_ticks = args.time_signature * RES
    n_bars = int(tempo.time_to_tick(an.duration + pad) // bar_ticks) + 1
    sections, _ = find_sections(an, tempo, pad, args.time_signature)
    first_bar = sections[0][0] // bar_ticks if sections else 0

    primary = args.instrument
    parts, recommended, mix_notes = {}, None, []
    if primary in stems:
        fillers = [f for f in args.fillers if f != primary] if args.fill != "off" else []
        names = candidates or [primary] + fillers
        names = [primary] + [x for x in names if x != primary and x in stems]
        log("Listening to each instrument")

        def listen(name):
            mono = mono22(stems[name])
            if np.max(np.abs(mono)) < 1e-4:
                return
            log(f"  listening to the {LABEL[name]}...")
            pa = analyze(mono, "bass" if name == "bass" else "guitar", None, quiet=True)
            found = detect_notes(pa, args.density, args.all_onsets, mix=an, gate_db=args.gate_db, src=name)
            qn = quantize(found, tempo, pad, args.triplets)[0] if found else []
            on = presence(pa, an, args.gate_db)
            parts[name] = (qn, on)
            log(f"    {len(qn)} notes, playing in {100 * on.mean():.0f}% of the song")

        for name in names:
            listen(name)
        mode_fill, weights = args.fill, None
        n_primary = len(parts.get(primary, ([],))[0])
        if n_primary < max(8, 0.002 * an.duration * 60):
            # Separation found (almost) nothing for the main instrument: the
            # song may have none, or its tone landed in another track (synth
            # leads, unusual guitar sounds). Chart whatever carries the song.
            log(f"  The {LABEL[primary]} track came out nearly empty after separation, so the chart "
                f"follows the other instruments instead.")
            for name in [x for x in CHARTABLE if x in stems and x not in parts]:
                listen(name)
            fillers = [x for x in parts if x != primary]
            mode_fill = "smart"
            # A guitar Demucs didn't recognise most often lands in "other".
            weights = dict(FILL_WEIGHT, other=1.0)
        if primary not in parts:
            parts[primary] = ([], np.zeros(len(an.times), bool))
        log("Planning the chart")
        pool = {k: v for k, v in parts.items() if k == primary or k in fillers}
        recommended = choose_sources(pool, primary, mode_fill if len(pool) > 1 else "off", tempo,
                                     pad, bar_ticks, n_bars, [t // bar_ticks for t, _ in sections],
                                     an.times, weights)
        # bars where the chosen instrument plays nothing are simply rests
        busy = {k: {n.tick // bar_ticks for n in v[0]} for k, v in parts.items()}
        recommended = [c if b in busy.get(c, ()) else None for b, c in enumerate(recommended)]
        if not any(recommended):
            log("  None of the separated instruments had clear notes; charting the full mix.")
            parts, recommended = {}, None
            found = detect_notes(an, args.density, args.all_onsets)
            mix_notes = quantize(found, tempo, pad, args.triplets)[0] if found else []
    else:
        log("Listening to the mix")
        found = detect_notes(an, args.density, args.all_onsets)
        mix_notes = quantize(found, tempo, pad, args.triplets)[0] if found else []
    return Project(args=args, y=y, sr=sr, stems=stems, an=an, tempo=tempo, pad=pad,
                   pad_samples=pad_samples, tempo_mode=mode, bpm_text=bpm_text, bar_ticks=bar_ticks,
                   n_bars=n_bars, sections=sections, first_bar=first_bar, parts=parts,
                   recommended=recommended, mix_notes=mix_notes)


def plan_notes(proj, plan):
    """The notes a plan selects: per bar, the chosen instrument's notes."""
    if not proj.parts:
        return sorted(proj.mix_notes, key=lambda n: n.tick)
    last = proj.n_bars - 1
    raw = [n for name, (notes, _) in proj.parts.items() for n in notes
           if plan[min(n.tick // proj.bar_ticks, last)] == name]
    return sorted(raw, key=lambda n: n.tick)


def build_charts(proj, plan=None, diffs=None):
    """All difficulties for a plan (instrument per bar, None = rest).
    Returns {difficulty: (notes, star_power_phrases)}; empty if nothing to chart."""
    args = proj.args
    plan = proj.recommended if plan is None else plan
    raw = plan_notes(proj, plan)
    if not raw:
        return {}
    expert = thin(raw, DIFFICULTIES["expert"], len(raw), proj.tempo, proj.pad)
    phrases_all = star_power(expert, args.time_signature, args.star_power_every)
    tracks = {}
    for diff in diffs or args.diffs:
        cfg = DIFFICULTIES[diff]
        base = expert if diff == "expert" else thin(expert, cfg, len(expert), proj.tempo, proj.pad)
        notes = [replace(n) for n in base]
        allow_chords = not args.no_chords and not (args.instrument == "bass" and diff != "expert")
        assign_frets(notes, cfg, allow_chords, real_chords=bool(proj.parts))
        if not args.no_sustains:
            assign_sustains(notes, proj.tempo, proj.pad, args.min_sustain)
        ticks = np.array([n.tick for n in notes])
        phrases = [(s0, ln) for s0, ln in phrases_all if np.any((ticks >= s0) & (ticks < s0 + ln))]
        tracks[diff] = (notes, phrases)
    return tracks


def write_song(proj, tracks, plan, folder, meta, album_art=None, split_audio=False):
    """Write notes.chart, song.ogg (+ the charted part's own track), song.ini, album.png."""
    args, tempo, pad = proj.args, proj.tempo, proj.pad
    plan = proj.recommended if plan is None else plan
    os.makedirs(folder, exist_ok=True)
    for old in ("song.ogg", "guitar.ogg", "bass.ogg"):
        if os.path.exists(os.path.join(folder, old)):
            os.remove(os.path.join(folder, old))
    if split_audio and proj.parts:
        chosen = [c or proj.primary for c in plan]
        part_audio = charted_audio({k: proj.stems[k] for k in set(chosen)}, chosen, tempo, pad,
                                   proj.bar_ticks, proj.sr, proj.y.shape[1])
        write_ogg(part_audio, proj.sr, proj.pad_samples, os.path.join(folder, f"{proj.primary}.ogg"))
        write_ogg(proj.y - part_audio, proj.sr, proj.pad_samples, os.path.join(folder, "song.ogg"))
    else:
        if split_audio:
            log("  (--split-audio needs separated instruments; writing a single song.ogg)")
        write_ogg(proj.y, proj.sr, proj.pad_samples, os.path.join(folder, "song.ogg"))
    write_chart(os.path.join(folder, "notes.chart"), meta, tempo, args.time_signature,
                proj.sections, tracks, args.instrument)
    preview_sec = preview_start([(float(tempo.tick_to_time(t)), nm) for t, nm in proj.sections],
                                proj.an, pad)
    song_ms = int(round((proj.duration + pad) * 1000))
    rating_notes = tracks.get("expert", next(iter(tracks.values())))[0]
    write_ini(os.path.join(folder, "song.ini"), meta, song_ms, int(round(preview_sec * 1000)),
              difficulty_rating(rating_notes, proj.duration), args.instrument)
    if album_art:
        write_album_art(album_art, os.path.join(folder, "album.png"))


def describe_plan(proj, plan):
    """Lines like 'bars 1-8: piano/keys' for passages not charted from the main instrument."""
    if not proj.parts:
        return []
    out, b = [], 0
    while b < len(plan):
        e = b
        while e + 1 < len(plan) and plan[e + 1] == plan[b]:
            e += 1
        if plan[b] not in (None, proj.primary):
            lo, hi = b - proj.first_bar + 1, e - proj.first_bar + 1
            out.append(f"{'bar' if lo == hi else 'bars'} {lo}{'' if lo == hi else f'-{hi}'}: {LABEL[plan[b]]}")
        b = e + 1
    return out


def main(argv=None):
    args = parse_args(argv)
    meta = song_meta(args)
    folder = os.path.join(args.out, safe_name(f"{meta['artist']} - {meta['title']}"))
    if os.path.exists(folder) and not args.force:
        sys.exit(f"'{folder}' already exists (use --force to overwrite).")

    proj = prepare(args)
    log("Charting")
    tracks = build_charts(proj)
    if not tracks:
        sys.exit(f"No {args.instrument} notes found in this song." if proj.parts
                 else "No notes detected - is the file silent?")
    expert = tracks.get("expert", next(iter(tracks.values())))[0]
    snap_ms = float(np.median(np.abs(proj.tempo.tick_to_time([n.tick for n in expert]) -
                                     (np.array([n.time for n in expert]) + proj.pad)))) * 1000

    log("Writing song folder")
    write_song(proj, tracks, None, folder, meta, args.album_art, args.split_audio)

    log("")
    log(f"Done: {folder}")
    for diff in DIFF_ORDER:
        if diff in tracks:
            notes, phr = tracks[diff]
            chords = sum(len(n.frets) > 1 for n in notes)
            sus = sum(n.sustain > 0 for n in notes)
            log(f"  {diff.capitalize():7s} {len(notes):5d} notes  {chords:4d} chords  "
                f"{sus:4d} sustains  {len(phr):2d} star power")
    log(f"  sections: {', '.join(n for _, n in proj.sections)}")
    if proj.parts:
        fills = describe_plan(proj, proj.recommended)
        if proj.primary not in proj.recommended:
            log("  charted from other instruments (the separation found no clear "
                f"{LABEL[proj.primary]}):")
        else:
            log(f"  charted from the {LABEL[proj.primary]}" +
                (", with other instruments filling in where it rests:" if fills else " only"))
        for line in fills:
            log(f"    {line}")
    else:
        log("  charted from the full mix")
    log(f"  median timing snap: {snap_ms:.1f} ms")
    log("Copy the folder into Clone Hero's Songs folder and choose Settings > General > Scan Songs.")
    return folder


if __name__ == "__main__":
    main()
