#!/usr/bin/env python3
"""Show the end of a failed step's output as an error on the GitHub run page.

    python .github/ci/annotate.py LOGFILE "What failed"
"""
import sys

path, title = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "Step failed"
try:
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
except OSError:
    lines = [f"(no log at {path})"]
tail = "\n".join(lines[-60:])[-6000:]
esc = tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
print(f"::error title={title}::{esc}", flush=True)
