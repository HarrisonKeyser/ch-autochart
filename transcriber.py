"""
Which notes are sounding, using Spotify's Basic Pitch model.

Basic Pitch (https://github.com/spotify/basic-pitch, Apache License 2.0) is a
small neural network trained to transcribe real instruments, guitar included.
Its 230 KB ONNX model runs here directly with ONNX Runtime, so neither
TensorFlow nor the basic-pitch package's other dependencies are needed.

posteriorgram(y) returns, for every analysis frame (22050 Hz, hop 256, the
same frames autochart uses), how strongly each of the 88 piano keys is
sounding ("note") and starting ("onset"). Returns None when the model or
ONNX Runtime isn't available; autochart then falls back to its own pitch
analysis.
"""

import os
import sys

import numpy as np

SR = 22050
HOP = 256
WINDOW = 43844                 # the model's input length (2 s minus one hop)
OVERLAP_FRAMES = 30
LOWEST_MIDI = 21               # key 0 = A0

_session = None
_failed = False


def model_path():
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    candidates = [os.path.join(d, "basic_pitch", "nmp.onnx")
                  for d in (os.environ.get("CH_AUTOCHART_MODELS"), os.path.join(here, "models")) if d]
    try:
        import importlib.util
        spec = importlib.util.find_spec("basic_pitch")
        if spec and spec.origin:
            candidates.append(os.path.join(os.path.dirname(spec.origin),
                                           "saved_models", "icassp_2022", "nmp.onnx"))
    except Exception:
        pass
    return next((p for p in candidates if os.path.isfile(p)), None)


def _get_session():
    global _session, _failed
    if _session is None and not _failed:
        try:
            import onnxruntime as ort
            path = model_path()
            if path is None:
                raise FileNotFoundError("nmp.onnx not found")
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            _session = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
        except Exception:
            _failed = True
    return _session


def available():
    return _get_session() is not None


def posteriorgram(y, batch=16):
    """y: mono float audio at 22050 Hz. Returns {"note", "onset"} arrays of
    shape (frames, 88) with values 0..1, or None if the model can't run.
    Frame k is sample k * 256, like autochart's own analysis frames."""
    sess = _get_session()
    if sess is None or len(y) == 0:
        return None
    overlap = OVERLAP_FRAMES * HOP
    hop = WINDOW - overlap                 # 36164 samples = 141.27 frames
    half = OVERLAP_FRAMES // 2
    audio = np.concatenate([np.zeros(overlap // 2, np.float32), np.asarray(y, np.float32)])
    starts = list(range(0, len(audio), hop))
    parts = {"note": [], "onset": []}
    win = []

    def flush():
        x = np.stack(win)[..., None]
        note, onset = sess.run(["StatefulPartitionedCall:1", "StatefulPartitionedCall:2"],
                               {"serving_default_input_2:0": x})
        parts["note"].extend(note[:, half:-half])
        parts["onset"].extend(onset[:, half:-half])
        win.clear()

    for i in starts:
        w = audio[i:i + WINDOW]
        win.append(np.pad(w, (0, WINDOW - len(w))) if len(w) < WINDOW else w)
        if len(win) == batch:
            flush()
    if win:
        flush()
    # Window w's kept frame j is original sample w * hop + j * HOP. A window
    # advances 141.27 frames but yields 142, so frames are placed by time
    # (simply joining them would drift ~0.7 frame per window, a third of a
    # second by the end of a song) and read off at whole frames.
    n = len(y) // HOP
    k = np.arange(n, dtype=float)
    per = hop / HOP
    w = np.minimum((k // per).astype(int), len(starts) - 1)
    pos = k - w * per                      # fractional frame within window w
    j0 = np.clip(np.floor(pos).astype(int), 0, None)
    frac = (pos - j0)[:, None]
    out = {}
    for name, wins in parts.items():
        arr = np.stack(wins)               # windows x kept frames x 88
        last = arr.shape[1] - 1
        a = arr[w, np.minimum(j0, last)]
        b = arr[w, np.minimum(j0 + 1, last)]
        out[name] = (a * (1 - frac) + b * frac).astype(np.float32)
    return out
