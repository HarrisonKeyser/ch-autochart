#!/usr/bin/env python3
"""
ch-autochart studio: a local web app for turning a song into a Clone Hero chart.

    python app.py            # opens http://127.0.0.1:8765 in your browser

Upload a song, watch it get split into instruments, listen to each one,
see (and repaint) which instrument the chart follows bar by bar, preview the
chart on a scrolling highway, then add album art and details and download the
finished song folder as a .zip.

Everything runs on this computer; nothing is uploaded anywhere.
"""

import argparse
import json
import os
import queue
import shutil
import tempfile
import threading
import traceback
import uuid
import webbrowser
import zipfile

import numpy as np

try:
    from flask import Flask, abort, jsonify, request, send_file, send_from_directory
except ImportError:
    raise SystemExit("The studio needs Flask:  pip install -r requirements.txt")

import autochart as ac
import librosa
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
JOBS_ROOT = os.path.join(tempfile.gettempdir(), "ch-autochart-studio")

STAGES = ["Reading the song", "Separating instruments", "Finding the beat",
          "Listening to each instrument", "Planning the chart", "Drawing the waveforms"]
# first words of autochart's log lines -> stage index
STAGE_OF = {"Reading": 0, "Separating instruments": 1, "Finding the beat": 2,
            "Listening to each": 3, "Listening to the mix": 3, "Planning the chart": 4}
TRACK_ORDER = ["guitar", "bass", "piano", "other", "vocals", "drums"]
TRACK_NAMES = {"guitar": "Guitar", "bass": "Bass", "piano": "Piano / keys",
               "other": "Other (synth, organ, harmonica…)", "vocals": "Vocals", "drums": "Drums",
               "mix": "Full mix"}
PREVIEW_SR = 22050
PEAKS_PER_SEC = 100

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
JOBS = {}
WORK = queue.Queue()


class Job:
    def __init__(self, folder, audio, instrument):
        self.id = os.path.basename(folder)
        self.dir = folder
        self.audio = audio
        self.instrument = instrument
        self.status = "queued"          # queued, working, ready, error
        self.stage = -1
        self.message = "Waiting to start"
        self.progress = None            # 0..1 during separation
        self.notice = None              # e.g. "Demucs isn't installed"
        self.error = None
        self.proj = None
        self.meta = None
        self.payload = None             # what the studio page draws
        self.lock = threading.Lock()

    def on_log(self, line):
        text = line.strip()
        for key, idx in STAGE_OF.items():
            if text.startswith(key):
                self.stage = max(self.stage, idx)
        if text:
            self.message = text
        if "charting the full mix instead" in text:
            self.notice = ("Demucs isn't installed, so the song couldn't be split into instruments; "
                           "the chart follows the full mix. Install it with: pip install demucs")
        elif text.startswith("Separation failed") or "couldn't load the" in text:
            self.notice = text

    def state(self):
        return dict(id=self.id, status=self.status, stage=self.stage, stages=STAGES,
                    message=self.message, progress=self.progress, notice=self.notice,
                    error=self.error)


# --------------------------------------------------------------------------
# Work
# --------------------------------------------------------------------------

def worker():
    while True:
        job = WORK.get()
        job.status = "working"
        ac.LOG_HOOK = job.on_log
        ac.PROGRESS_HOOK = lambda f: setattr(job, "progress", float(f))
        try:
            analyze_job(job)
            job.status = "ready"
        except SystemExit as err:            # autochart reports user errors this way
            job.status, job.error = "error", str(err)
        except Exception as err:
            traceback.print_exc()
            job.status, job.error = "error", f"{type(err).__name__}: {err}"
        finally:
            ac.LOG_HOOK = ac.PROGRESS_HOOK = None


def analyze_job(job):
    argv = [job.audio, "--instrument", job.instrument]
    stems_dir = os.path.join(job.dir, "stems")
    if os.path.isdir(stems_dir) and os.listdir(stems_dir):
        argv += ["--stems", stems_dir]
    args = ac.parse_args(argv)
    job.meta = ac.song_meta(args)
    proj = ac.prepare(args, candidates=ac.CHARTABLE)
    job.proj = proj
    job.stage = 5
    job.message = "Drawing the waveforms"
    job.payload = build_payload(job)
    job.progress = None


def build_payload(job):
    proj = job.proj
    audio_dir = os.path.join(job.dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    sources = [(k, proj.stems[k]) for k in TRACK_ORDER if k in proj.stems]
    sources.append(("mix", proj.y))
    monos = {}
    for name, y in sources:
        m = librosa.resample(librosa.to_mono(y), orig_sr=proj.sr, target_sr=PREVIEW_SR)
        monos[name] = m
        sf.write(os.path.join(audio_dir, f"{name}.wav"), np.clip(m, -1, 1), PREVIEW_SR, subtype="PCM_16")
    loudest = max(float(np.max(np.abs(m))) for m in monos.values()) or 1.0
    hop = PREVIEW_SR // PEAKS_PER_SEC
    tracks = []
    for name, m in monos.items():
        n = len(m) // hop
        peaks = np.abs(m[: n * hop]).reshape(n, hop).max(axis=1) / loudest
        notes = []
        if name in proj.parts:
            notes = [[round(x.time, 4), round(x.pitch, 2), x.poly] for x in proj.parts[name][0]]
        tracks.append(dict(
            id=name, name=TRACK_NAMES[name], chartable=name in proj.parts or (not proj.parts and name == "mix"),
            url=f"/api/jobs/{job.id}/audio/{name}.wav",
            peaks=(np.sqrt(np.clip(peaks, 0, 1)) * 255).astype(np.uint8).tolist(),
            notes=notes))

    bars = proj.bar_times().tolist()
    recommended = proj.recommended if proj.recommended is not None else ["mix"] * proj.n_bars
    return dict(
        meta=job.meta, duration=proj.duration, instrument=proj.primary,
        tempo=dict(text=proj.bpm_text, mode=proj.tempo_mode,
                   bpm=[[float(proj.tempo.tick_to_time(t)) - proj.pad, b / 1000] for t, b in proj.tempo.events]),
        bars=bars, first_bar=int(proj.first_bar),
        sections=[dict(time=float(proj.tempo.tick_to_time(t)) - proj.pad, name=n) for t, n in proj.sections],
        tracks=tracks, peaks_per_sec=PEAKS_PER_SEC,
        recommended=recommended, separated=bool(proj.parts), notice=job.notice,
        songs_folder=find_songs_folder(),
        preview=chart_preview(job, recommended))


def plan_for_engine(proj, plan):
    """The page uses 'mix' for full-mix charts; the engine uses None per bar there."""
    if not proj.parts:
        return None
    known = set(proj.parts)
    plan = [(p if p in known else None) for p in plan][: proj.n_bars]
    return plan + [None] * (proj.n_bars - len(plan))


def chart_preview(job, plan):
    proj = job.proj
    tracks = ac.build_charts(proj, plan_for_engine(proj, plan), diffs=ac.DIFF_ORDER)
    out = {}
    hopo_ticks = 65 * ac.RES // 192          # Clone Hero's automatic HOPO distance for .chart
    for diff, (notes, phrases) in tracks.items():
        rows, prev = [], None
        for n in notes:
            t = float(proj.tempo.tick_to_time(n.tick)) - proj.pad
            end = float(proj.tempo.tick_to_time(n.tick + n.sustain)) - proj.pad if n.sustain else t
            hopo = (prev is not None and len(n.frets) == 1 and n.tick - prev.tick <= hopo_ticks
                    and n.frets != prev.frets and not set(n.frets) <= set(prev.frets))
            rows.append([round(t, 4), list(n.frets), round(end - t, 4), int(hopo), n.src])
            prev = n
        sp = [[float(proj.tempo.tick_to_time(s)) - proj.pad, float(proj.tempo.tick_to_time(s + ln)) - proj.pad]
              for s, ln in phrases]
        out[diff] = dict(notes=rows, star_power=sp, chords=sum(len(r[1]) > 1 for r in rows))
    lines = ac.describe_plan(proj, plan_for_engine(proj, plan)) if proj.parts else []
    return dict(charts=out, summary=lines)


def find_songs_folder():
    home = os.path.expanduser("~")
    for p in (os.path.join(home, "Documents", "Clone Hero", "Songs"),
              os.path.join(home, "Clone Hero", "Songs"),
              os.path.join(home, ".clonehero", "Songs")):
        if os.path.isdir(p):
            return p
    return None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/web/<path:name>")
def web_files(name):
    return send_from_directory(WEB, name)


def safe_filename(name, fallback):
    base = os.path.basename(name or "").replace("\x00", "")
    base = "".join(c for c in base if c.isalnum() or c in " ._-()[]&,'").strip() or fallback
    return base[:120]


@app.post("/api/jobs")
def create_job():
    song = request.files.get("song")
    if not song or not song.filename:
        return jsonify(error="Choose a song file to chart."), 400
    instrument = request.form.get("instrument", "guitar")
    if instrument not in ("guitar", "bass"):
        instrument = "guitar"
    folder = os.path.join(JOBS_ROOT, uuid.uuid4().hex[:12])
    os.makedirs(os.path.join(folder, "input"), exist_ok=True)
    audio = os.path.join(folder, "input", safe_filename(song.filename, "song.mp3"))
    song.save(audio)
    roles = request.form.getlist("stem_role")
    for f, role in zip(request.files.getlist("stems"), roles):
        if role in ac.STEM_NAMES and f.filename:
            ext = os.path.splitext(f.filename)[1].lower()
            if ext in ac.AUDIO_EXTS:
                os.makedirs(os.path.join(folder, "stems"), exist_ok=True)
                f.save(os.path.join(folder, "stems", role + ext))
    job = Job(folder, audio, instrument)
    JOBS[job.id] = job
    WORK.put(job)
    return jsonify(job.state())


def get_job(job_id):
    job = JOBS.get(job_id)
    if job is None:
        abort(404)
    return job


@app.get("/api/jobs/<job_id>")
def job_state(job_id):
    return jsonify(get_job(job_id).state())


@app.get("/api/jobs/<job_id>/project")
def job_project(job_id):
    job = get_job(job_id)
    if job.status != "ready":
        return jsonify(error="Still working on this song."), 409
    return jsonify(job.payload)


@app.get("/api/jobs/<job_id>/audio/<name>.wav")
def job_audio(job_id, name):
    job = get_job(job_id)
    if name not in TRACK_NAMES:
        abort(404)
    return send_from_directory(os.path.join(job.dir, "audio"), f"{name}.wav", mimetype="audio/wav")


@app.post("/api/jobs/<job_id>/preview")
def job_preview(job_id):
    job = get_job(job_id)
    plan = (request.get_json(silent=True) or {}).get("plan")
    if job.status != "ready" or not isinstance(plan, list):
        return jsonify(error="No plan to preview."), 400
    with job.lock:
        return jsonify(chart_preview(job, plan))


@app.post("/api/jobs/<job_id>/build")
def job_build(job_id):
    job = get_job(job_id)
    if job.status != "ready":
        return jsonify(error="The song isn't ready yet."), 409
    form = request.form
    plan = json.loads(form.get("plan") or "null")
    meta = dict(job.meta)
    for key in ("title", "artist", "album", "genre", "year", "charter"):
        if key in form:
            meta[key] = form[key].strip()
    meta["title"] = meta["title"] or "Untitled"
    meta["artist"] = meta["artist"] or "Unknown Artist"
    meta["charter"] = meta["charter"] or "ch-autochart"
    diffs = [d for d in ac.DIFF_ORDER if form.get(f"diff_{d}", "on") == "on"] or ["expert"]

    with job.lock:
        proj = job.proj
        art = None
        if request.files.get("art") and request.files["art"].filename:
            art = os.path.join(job.dir, "input", "art" + os.path.splitext(request.files["art"].filename)[1].lower())
            request.files["art"].save(art)
        engine_plan = plan_for_engine(proj, plan) if plan else proj.recommended
        tracks = ac.build_charts(proj, engine_plan, diffs=diffs)
        if not tracks:
            return jsonify(error="That plan has no notes in it. Highlight at least one passage to chart."), 400
        name = ac.safe_name(f"{meta['artist']} - {meta['title']}")
        out_root = os.path.join(job.dir, "build")
        shutil.rmtree(out_root, ignore_errors=True)
        folder = os.path.join(out_root, name)
        try:
            ac.write_song(proj, tracks, engine_plan, folder, meta, art,
                          split_audio=form.get("split_audio") == "on" and bool(proj.parts))
        except Exception as err:
            traceback.print_exc()
            return jsonify(error=f"Couldn't write the song: {err}"), 500

        zip_path = os.path.join(job.dir, f"{name}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(os.listdir(folder)):
                zf.write(os.path.join(folder, f), f"{name}/{f}")

        installed = None
        if form.get("install") == "on":
            songs = find_songs_folder()
            if songs:
                dest = os.path.join(songs, name)
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(folder, dest)
                installed = dest

    counts = {d: len(tracks[d][0]) for d in tracks}
    return jsonify(download=f"/api/jobs/{job.id}/download", filename=f"{name}.zip",
                   files=sorted(os.listdir(folder)), counts=counts, installed=installed,
                   size=os.path.getsize(zip_path))


@app.get("/api/jobs/<job_id>/download")
def job_download(job_id):
    job = get_job(job_id)
    zips = [f for f in os.listdir(job.dir) if f.endswith(".zip")]
    if not zips:
        abort(404)
    return send_file(os.path.join(job.dir, zips[0]), as_attachment=True, download_name=zips[0])


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="ch-autochart studio (local web app)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default: this computer only)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    args = ap.parse_args()
    os.makedirs(JOBS_ROOT, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"
    print(f"ch-autochart studio running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
