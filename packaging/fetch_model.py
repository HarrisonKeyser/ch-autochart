#!/usr/bin/env python3
"""Download the models into models/ so they can be shipped inside the
Windows program and loaded offline.

    python packaging/fetch_model.py [models_folder] [model_name]

1. Demucs (separation): the folder gets the model's recipe (<name>.yaml) and
   its weights (<signature>-<checksum>.th), the layout Demucs reads with
   get_model(name, repo=folder).
2. Basic Pitch (note transcription, Spotify, Apache License 2.0): the 230 KB
   ONNX model plus its LICENSE and NOTICE, in <folder>/basic_pitch/.
Checksums are verified for both.
"""
import hashlib
import os
import shutil
import sys
import urllib.request
from pathlib import Path

import yaml
import demucs.pretrained as dp


def main():
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    name = sys.argv[2] if len(sys.argv) > 2 else "htdemucs_6s"
    folder.mkdir(parents=True, exist_ok=True)

    recipe = dp.REMOTE_ROOT / f"{name}.yaml"
    shutil.copyfile(recipe, folder / recipe.name)
    urls = dp._parse_remote_files(dp.REMOTE_ROOT / "files.txt")
    for sig in yaml.safe_load(recipe.read_text())["models"]:
        url = urls[sig]
        target = folder / url.rsplit("/", 1)[1]
        if not target.exists():
            print(f"downloading {url}")
            tmp = target.with_suffix(".part")
            with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out)
            tmp.replace(target)
        checksum = target.stem.rsplit("-", 1)[1]
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if not digest.startswith(checksum):
            target.unlink()
            sys.exit(f"checksum mismatch for {target.name}")
        print(f"ok  {target.name}  {target.stat().st_size / 1e6:.0f} MB")

    model = dp.get_model(name, repo=folder)   # proves it loads from the folder alone
    print("loads offline:", type(model).__name__, model.sources)
    fetch_basic_pitch(folder / "basic_pitch")


BASIC_PITCH = "https://raw.githubusercontent.com/spotify/basic-pitch/v0.4.0/"
BASIC_PITCH_SHA256 = "2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec"


def fetch_basic_pitch(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for src, name in [("basic_pitch/saved_models/icassp_2022/nmp.onnx", "nmp.onnx"),
                      ("LICENSE", "LICENSE"), ("NOTICE", "NOTICE")]:
        target = folder / name
        if not target.exists():
            print(f"downloading {BASIC_PITCH + src}")
            with urllib.request.urlopen(BASIC_PITCH + src) as resp:
                target.write_bytes(resp.read())
    digest = hashlib.sha256((folder / "nmp.onnx").read_bytes()).hexdigest()
    if digest != BASIC_PITCH_SHA256:
        (folder / "nmp.onnx").unlink()
        sys.exit("checksum mismatch for Basic Pitch's nmp.onnx")
    import onnxruntime as ort
    sess = ort.InferenceSession(str(folder / "nmp.onnx"), providers=["CPUExecutionProvider"])
    print("ok  basic_pitch/nmp.onnx, inputs:", [i.name for i in sess.get_inputs()])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
