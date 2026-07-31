#!/usr/bin/env python3
"""Serve the repo over HTTP so the loop audition page can fetch audio.

Web Audio needs fetch + decodeAudioData for sample-accurate looping, and fetch
is blocked on file:// URLs. This is the smallest thing that makes the page work.

    .venv/bin/python tools/audio/serve.py
"""
import http.server, socketserver, sys, webbrowser
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import lease  # noqa: E402  — scopes this server for tools/reap.py (PRC-07): a sibling
              # agent's --kill now spares a listen session started by a different
              # session instead of ending it out from under whoever is auditioning a loop

PORT = 8731
URL = f"http://localhost:{PORT}/docs/latticefall-loops.html"
## Long, not "forever": this server has no natural end (serve_forever() only stops on
## Ctrl-C), so the TTL is purely the crash backstop tools/lease.py exists for, not an
## expected session length. Generous enough that a real audition session never trips it.
LEASE_TTL_S = 6 * 3600.0

class Handler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler serves .html as bare text/html, so browsers fall
    back to windows-1252 and mangle every em dash. Say utf-8 explicitly."""

    def guess_type(self, path):
        base = super().guess_type(path)
        if base.startswith("text/") and "charset=" not in base:
            return base + "; charset=utf-8"
        return base


if __name__ == "__main__":
    handler = partial(Handler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), handler) as httpd:
        print(f"serving {ROOT}\n\n  {URL}\n\nctrl-c to stop")
        try:
            webbrowser.open(URL)
        except Exception:
            pass
        with lease.acquire("audio-serve", [__file__], ttl_s=LEASE_TTL_S):
            httpd.serve_forever()
