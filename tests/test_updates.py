#!/usr/bin/env python3
"""Tests for the update check and download (no internet needed).

    python tests/test_updates.py
"""
import http.server
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import updates  # noqa: E402


def release(tag, with_installer=True, state="uploaded"):
    assets = [{"name": "ch-autochart-setup.exe", "state": state, "size": 1234,
               "browser_download_url": f"https://example.invalid/{tag}/ch-autochart-setup.exe"}]
    return {"tag_name": tag, "html_url": f"https://github.com/x/releases/{tag}", "body": "notes",
            "assets": assets if with_installer else []}


def check(current, data=None, error=None):
    def fetch():
        if error:
            raise error
        return data
    return updates.check(current, fetch=fetch, force=True)


def test_versions():
    assert updates.parse_version("v2.0.1") == (2, 0, 1)
    assert updates.parse_version("2.1") == (2, 1, 0)
    assert updates.parse_version("0.0.0-dev") is None
    assert updates.is_newer("v2.0.10", "2.0.9")
    assert not updates.is_newer("v2.0.0", "2.0.0")
    assert not updates.is_newer("v1.9.9", "2.0.0")
    assert not updates.is_newer("v2.0.1", "0.0.0-dev")   # dev builds never nag


def test_check():
    r = check("2.0.0", release("v2.0.1"))
    assert r["available"] and r["latest"] == "2.0.1" and r["size"] == 1234 and r["download"]
    assert not check("2.0.1", release("v2.0.1"))["available"]
    assert not check("2.0.0", release("v2.0.1", with_installer=False))["available"]   # still building
    assert not check("2.0.0", release("v2.0.1", state="starter"))["available"]       # still uploading
    r = check("2.0.0", error=OSError("offline"))
    assert not r["available"] and "offline" in r["error"]
    # results are remembered for the rest of the run
    assert updates.check("2.0.0", fetch=lambda: 1 / 0)["available"] is False


def serve(payload, lie_about_length=False):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload) + (1000 if lie_about_length else 0)))
            self.end_headers()
            for i in range(0, len(payload), 65536):
                self.wfile.write(payload[i:i + 65536])
                time.sleep(0.01)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/setup.exe"


def wait(dl, timeout=20):
    seen = []
    t0 = time.time()
    while dl.state in ("starting", "downloading") and time.time() - t0 < timeout:
        seen.append(dl.status()["fraction"])
        time.sleep(0.02)
    return seen


def test_download():
    payload = os.urandom(3 * 1024 * 1024 + 17)
    srv, url = serve(payload)
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "updates", "setup.exe")
        dl = updates.Download(url, dest, expected_size=len(payload))
        seen = wait(dl)
        assert dl.state == "ready", dl.error
        assert open(dest, "rb").read() == payload
        assert any(f is not None and 0 < f < 1 for f in seen), "no progress was reported"
        assert dl.status()["fraction"] == 1.0
    srv.shutdown()


def test_cut_short():
    payload = os.urandom(200_000)
    srv, url = serve(payload, lie_about_length=True)
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "setup.exe")
        dl = updates.Download(url, dest, expected_size=len(payload) + 1000)
        wait(dl)
        assert dl.state == "error" and "cut short" in dl.error, dl.status()
        assert not os.path.exists(dest) and not os.path.exists(dest + ".part")
    srv.shutdown()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print("all update tests passed")
