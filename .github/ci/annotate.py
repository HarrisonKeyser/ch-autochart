#!/usr/bin/env python3
"""Show the end of a failed step's output as an error on the GitHub run page.

    python .github/ci/annotate.py LOGFILE "What failed" [error|notice]
"""
import sys

path, title = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "Step failed"
level = sys.argv[3] if len(sys.argv) > 3 else "error"
try:
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
except OSError:
    lines = [f"(no log at {path})"]
# Progress bars are noise here; GitHub also cuts long annotations short.
lines = [line for line in lines if "%|" not in line and line.strip()]
tail = "\n".join(lines[-40:])[-3500:]
esc = tail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
print(f"::{level} title={title}::{esc}", flush=True)
