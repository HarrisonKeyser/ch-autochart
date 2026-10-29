#!/usr/bin/env python3
"""Download the Demucs separation model into models/ so it can be shipped
inside the Windows program and loaded offline.

    python packaging/fetch_model.py [models_folder] [model_name]

The folder ends up with the model's recipe (<name>.yaml) and its weights
(<signature>-<checksum>.th), which is the layout Demucs reads with
get_model(name, repo=folder). The checksum is verified.
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


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
