#!/usr/bin/env python3
"""
ch-autochart as a desktop program: the studio in its own window.

This is what the Windows installer runs. It starts the studio's local server
on a free port and shows it in a native window (Microsoft Edge WebView2,
built into Windows 10 and 11). If no such window can be opened, it falls
back to your web browser.

    python desktop.py                 # run it from source
    ch-autochart.exe --selftest DIR   # (packaged) check the install works, then exit
"""

import json
import multiprocessing
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser

FROZEN = getattr(sys, "frozen", False)
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.path.expanduser("~/.local/share")
    path = os.path.join(base or os.path.expanduser("~"), "ch-autochart")
    os.makedirs(path, exist_ok=True)
    return path


def prepare_environment():
    """Point Demucs at the model shipped with the installer, and send output to
    a log file (a windowed program has no console to print to)."""
    models = os.path.join(BASE, "models")
    if os.path.isdir(models):
        os.environ["TORCH_HOME"] = models
    if FROZEN or sys.stdout is None:
        log = open(os.path.join(data_dir(), "log.txt"), "a", encoding="utf-8", buffering=1)
        log.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        sys.stdout = sys.stderr = log


def free_port(preferred=8765):
    for port in [preferred] + list(range(preferred + 1, preferred + 50)) + [0]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def start_server(port):
    from werkzeug.serving import make_server
    import app as studio
    studio.start_worker()
    server = make_server("127.0.0.1", port, studio.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def open_path(path):
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 (opens Explorer)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class DesktopApi:
    """Things the page can ask the desktop program to do (window.pywebview.api)."""

    def __init__(self):
        self.window = None

    def save_zip(self, job_id):
        import webview
        import app as studio
        job = studio.JOBS.get(job_id)
        zips = [f for f in os.listdir(job.dir) if f.endswith(".zip")] if job else []
        if not zips:
            return {"error": "Build the chart first."}
        result = self.window.create_file_dialog(
            webview.FileDialog.SAVE, directory=os.path.expanduser("~/Downloads"),
            save_filename=zips[0], file_types=("Zip files (*.zip)",))
        if not result:
            return {"cancelled": True}
        dest = result if isinstance(result, str) else result[0]
        if not dest.lower().endswith(".zip"):
            dest += ".zip"
        shutil.copyfile(os.path.join(job.dir, zips[0]), dest)
        return {"path": dest}

    def choose_songs_folder(self):
        import webview
        import app as studio
        result = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"cancelled": True}
        folder = result if isinstance(result, str) else result[0]
        studio.save_settings(songs_folder=folder)
        return {"path": folder}

    def show_in_folder(self, path):
        if path and os.path.exists(path):
            open_path(path if os.path.isdir(path) else os.path.dirname(path))
        return True

    def open_link(self, url):
        if url.startswith("https://"):
            webbrowser.open(url)
        return True


def run_window(url):
    try:
        import webview
    except ImportError:
        return False
    api = DesktopApi()
    try:
        window = webview.create_window(
            "ch-autochart", url, js_api=api, width=1440, height=940, min_size=(900, 640),
            background_color="#241A1A", text_select=True)
        api.window = window
        icon = os.path.join(BASE, "packaging", "icon.ico")
        webview.start(private_mode=False, storage_path=os.path.join(data_dir(), "webview"),
                      icon=icon if os.path.exists(icon) else None)
        return True
    except Exception:
        traceback.print_exc()
        return False


# --------------------------------------------------------------------------
# Self-test: proves a fresh install can separate and chart a song offline.
# --------------------------------------------------------------------------

def make_test_song(path, seconds=24, bpm=120):
    """A small synthesized band: plucked-string guitar riff, bass, drums, keys."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import lfilter
    sr = 44100
    rng = np.random.default_rng(1)
    mix = np.zeros(int((seconds + 2) * sr))
    spb = 60 / bpm

    def add(sig, t):
        i = int(t * sr)
        mix[i:i + len(sig)] += sig[: len(mix) - i]

    def pluck(midi, dur):
        f = 440 * 2 ** ((midi - 69) / 12)
        n, N = int(dur * sr), int(sr / f)
        x = np.zeros(n)
        x[:N] = rng.uniform(-1, 1, N)
        a = np.zeros(N + 2)
        a[0], a[N], a[N + 1] = 1, -0.498, -0.498
        y = lfilter([1], a, x)
        return y / (np.abs(y).max() + 1e-9) * 0.35

    t = np.arange(int(0.2 * sr)) / sr
    kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-t / 0.03)).cumsum() / sr) * np.exp(-t / 0.12)
    snare = rng.standard_normal(len(t)) * np.exp(-t / 0.05) * 0.3
    riff = [52, 55, 57, 55, 52, 50, 52, 59]
    for beat in range(int(seconds / spb)):
        bt = 0.5 + beat * spb
        add(kick if beat % 2 == 0 else snare, bt)
        add(np.sin(2 * np.pi * 41.2 * np.arange(int(spb * sr)) / sr) * 0.25 * np.exp(-np.arange(int(spb * sr)) / sr / 0.4), bt)
        for half in (0, 1):
            add(pluck(riff[(beat * 2 + half) % len(riff)], spb), bt + half * spb / 2)
    mix /= np.abs(mix).max() * 1.1
    sf.write(path, np.stack([mix, mix], 1), sr)


def selftest(out_dir):
    """Exit code 0 when the packaged program can do everything it needs to."""
    os.makedirs(out_dir, exist_ok=True)
    report = {"ok": False, "steps": {}}

    def step(name, fn):
        t0 = time.time()
        try:
            value = fn()
            report["steps"][name] = {"ok": True, "seconds": round(time.time() - t0, 1), "value": value}
            return value
        except BaseException as err:  # SystemExit from the engine counts as a failure too
            report["steps"][name] = {"ok": False, "error": f"{type(err).__name__}: {err}",
                                     "trace": traceback.format_exc()[-2000:]}
            raise

    try:
        import autochart as ac
        step("web files bundled", lambda: os.path.exists(os.path.join(BASE, "web", "index.html")))
        step("ffmpeg available", lambda: ac.ffmpeg_path())
        def model_files():
            found = sorted(f for _, _, fs in os.walk(os.environ.get("TORCH_HOME", "")) for f in fs
                           if f.endswith(".th"))
            # The installed program must work offline, so the model has to be inside it.
            assert found or not FROZEN, "the separation model isn't bundled"
            return found
        step("separation model bundled", model_files)
        song = os.path.join(out_dir, "selftest.wav")
        step("make test song", lambda: make_test_song(song))

        def chart():
            folder = ac.main([song, "-o", out_dir, "--force", "--separate", "on", "--no-cache",
                              "--artist", "ch-autochart", "--title", "Selftest", "--split-audio"])
            text = open(os.path.join(folder, "notes.chart"), encoding="utf-8").read()
            notes = text.count(" = N ")
            assert "[ExpertSingle]" in text and notes > 20, f"only {notes} notes"
            return {"folder": folder, "files": sorted(os.listdir(folder)), "note_lines": notes}
        step("separate and chart offline", chart)

        def serve():
            import urllib.request
            port = free_port(8799)
            start_server(port)
            html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read().decode()
            assert "ch-autochart studio" in html
            return port
        step("studio server", serve)
        report["ok"] = True
    except BaseException:
        pass
    with open(os.path.join(out_dir, "selftest.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    return 0 if report["ok"] else 1


def main():
    multiprocessing.freeze_support()
    prepare_environment()
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else os.path.join(data_dir(), "selftest")
        code = selftest(out)
        sys.stdout.flush()
        os._exit(code)

    port = free_port()
    start_server(port)
    url = f"http://127.0.0.1:{port}/"
    print(f"studio at {url}", flush=True)
    if not run_window(url):
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    os._exit(0)


if __name__ == "__main__":
    main()
