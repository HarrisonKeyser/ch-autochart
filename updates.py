"""
Checking GitHub for a newer ch-autochart, and fetching its installer.

The installed program asks GitHub's public releases API once per launch.
A release only counts once its installer file is attached (the build
finishes about 40 minutes after a release is published). Nothing about
the user or their songs is sent; it's an anonymous request for the
latest release's details.
"""

import json
import os
import re
import threading
import urllib.request

RELEASES_API = "https://api.github.com/repos/HarrisonKeyser/ch-autochart/releases/latest"
INSTALLER_NAME = "ch-autochart-setup.exe"

_cached = None
_lock = threading.Lock()


def parse_version(text):
    """'v2.0.1' -> (2, 0, 1); None for anything that isn't a plain version."""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", (text or "").strip())
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def fetch_latest(timeout=6):
    req = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ch-autochart-update-check",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def check(current, fetch=fetch_latest, force=False):
    """What GitHub's newest release offers compared with `current`.
    Never raises: problems come back as {"available": False, "error": ...}."""
    global _cached
    with _lock:
        if _cached is not None and not force:
            return _cached
        try:
            data = fetch()
            tag = data.get("tag_name", "")
            asset = next((a for a in data.get("assets", [])
                          if a.get("name") == INSTALLER_NAME and a.get("state", "uploaded") == "uploaded"), None)
            result = {
                "available": bool(asset) and is_newer(tag, current),
                "current": current,
                "latest": tag.lstrip("v"),
                "page": data.get("html_url"),
                "download": asset and asset.get("browser_download_url"),
                "size": asset and asset.get("size"),
                "notes": (data.get("body") or "")[:3000],
            }
        except Exception as err:  # offline, rate-limited, GitHub down...
            result = {"available": False, "current": current, "error": f"{type(err).__name__}: {err}"}
        _cached = result
        return result


class Download:
    """Downloads a file in the background, reporting progress."""

    def __init__(self, url, dest, expected_size=None):
        self.url, self.dest, self.expected = url, dest, expected_size
        self.state, self.done, self.total, self.error = "starting", 0, expected_size or 0, None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        part = self.dest + ".part"
        try:
            os.makedirs(os.path.dirname(self.dest), exist_ok=True)
            req = urllib.request.Request(self.url, headers={"User-Agent": "ch-autochart-updater"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(part, "wb") as out:
                self.total = int(resp.headers.get("Content-Length") or self.total or 0)
                self.state = "downloading"
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    self.done += len(chunk)
            if self.expected and self.done != self.expected:
                raise IOError(f"the download was cut short ({self.done} of {self.expected} bytes)")
            os.replace(part, self.dest)
            self.state = "ready"
        except Exception as err:
            self.state, self.error = "error", str(err)
            try:
                os.remove(part)
            except OSError:
                pass

    def status(self):
        frac = self.done / self.total if self.total else None
        return {"state": self.state, "fraction": frac, "error": self.error}
