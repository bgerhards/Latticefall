#!/usr/bin/env python3
"""Audition the staged CC0 candidates and record keep/drop against each one.

Why this exists
---------------
`fetch_cc0.py` proves a file is *safe to ship* — CC0, verified on the item's own page,
logged with a SHA-256. It cannot prove a file is the *right sound*, because judging that
means hearing it. Decision 011 is the standing reminder of what happens when this project
substitutes a metric for an ear: a loop scorer was tuned against a seam number and made
the loops measurably worse.

So candidates stay outside `assets/` until a human plays them. This serves the staging
directory next to the music masters (decision 012 keeps large audio out of git) together
with a page that puts each candidate next to the brief it is meant to answer, and writes
`keep` or `drop` straight back into `candidates.json`.

Verdicts persist, so auditioning is resumable — close the tab, come back, keep going.
Only files marked `keep` are eligible for promotion into the bank.

    .venv/bin/python tools/audio/audition_cc0.py
"""
from __future__ import annotations

import http.server
import json
import socketserver
import urllib.parse
import webbrowser
from pathlib import Path

STAGE = Path.home() / "Latticefall-masters" / "cc0-candidates"
MANIFEST = STAGE / "candidates.json"
PORT = 8732
URL = f"http://localhost:{PORT}/"

PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8">
<title>Latticefall — CC0 candidate audition</title>
<style>
:root { color-scheme: dark; }
body { background:#0e1417; color:#dbe3e1; font:15px/1.5 -apple-system,"Segoe UI",sans-serif;
       margin:0; padding:32px 28px 80px; }
h1 { font-size:20px; letter-spacing:.02em; margin:0 0 4px; }
.sub { color:#7d8a8c; font-size:13px; margin-bottom:28px; }
.cue { border:1px solid #1d282c; border-radius:6px; margin:0 0 20px; background:#111a1d; }
.cue > header { padding:14px 16px; border-bottom:1px solid #1d282c; }
.cue h2 { font-size:15px; margin:0 0 4px; color:#e8efed; letter-spacing:.04em; }
.brief { color:#8d9a9b; font-size:13px; max-width:70ch; }
.tag { font:11px/1 ui-monospace,monospace; color:#5d6b6d; border:1px solid #26343a;
       border-radius:3px; padding:3px 6px; margin-left:8px; vertical-align:2px; }
.row { display:flex; align-items:center; gap:14px; padding:10px 16px;
       border-top:1px solid #162024; }
.row.keep { background:#0f1f18; }
.row.drop { opacity:.42; }
.meta { flex:1 1 auto; min-width:0; }
.name { font:12px/1.4 ui-monospace,monospace; color:#c8d3d1; overflow-wrap:anywhere; }
.by { font-size:12px; color:#71807f; }
.by a { color:#5a8f80; text-decoration:none; }
audio { height:32px; flex:0 0 260px; }
button { font:12px/1 inherit; padding:7px 13px; border-radius:4px; cursor:pointer;
         border:1px solid #2b3a3e; background:#162125; color:#c2cecd; }
button.on-keep { background:#1c4034; border-color:#2f6b56; color:#d7f0e5; }
button.on-drop { background:#3d1f1c; border-color:#6b3630; color:#f0d9d5; }
.empty { padding:12px 16px; color:#6d7a7c; font-size:13px; }
footer { position:fixed; left:0; right:0; bottom:0; background:#0a1013;
         border-top:1px solid #1d282c; padding:10px 28px; font-size:13px; color:#8d9a9b; }
b { color:#d8e3e0; }
</style></head><body>
<h1>CC0 candidate audition</h1>
<div class="sub">Every file below is verified CC0 and logged with a SHA-256 &mdash; that is
the licence question, already answered. The open question is whether it is the right sound.
Nothing reaches <code>assets/</code> until it is marked keep.</div>
"""

PAGE_TAIL = """
<footer><span id="tally"></span> &middot; verdicts save as you click &middot;
keep only what you would ship</footer>
<script>
function mark(file, verdict, el) {
  fetch('/verdict', {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify({file:file, verdict:verdict})})
    .then(r => r.json()).then(d => {
      const row = el.closest('.row');
      row.className = 'row ' + (d.verdict === 'unheard' ? '' : d.verdict);
      row.querySelectorAll('button').forEach(b => {
        b.className = (b.dataset.v === d.verdict) ? 'on-' + d.verdict : '';
      });
      tally();
    });
}
function tally() {
  const k = document.querySelectorAll('.row.keep').length;
  const d = document.querySelectorAll('.row.drop').length;
  const n = document.querySelectorAll('.row').length;
  document.getElementById('tally').innerHTML =
    '<b>' + k + '</b> keep, <b>' + d + '</b> drop, <b>' + (n-k-d) + '</b> unheard';
}
tally();
</script></body></html>
"""


def build_page(manifest: dict) -> str:
    out = [PAGE_HEAD]
    if not manifest:
        out.append('<div class="empty">Nothing staged. Run '
                   '<code>tools/audio/fetch_cc0.py</code> first.</div>')
    for cue, spec in manifest.items():
        kind = "bed &middot; must loop" if spec.get("loop") else "one-shot"
        out.append('<section class="cue"><header>')
        out.append(f'<h2>{cue}<span class="tag">{kind}</span></h2>')
        out.append(f'<div class="brief">{spec.get("note", "")}</div></header>')
        cands = spec.get("candidates", [])
        if not cands:
            out.append('<div class="empty">No CC0 candidate cleared the relevance filter '
                       'for this cue. It needs a different search, or a different source.'
                       '</div>')
        for c in cands:
            verdict = c.get("verdict", "unheard")
            row_cls = "" if verdict == "unheard" else verdict
            src = "/files/" + urllib.parse.quote(c["file"])
            page = c.get("page", "")
            mb = c.get("bytes", 0) / 1e6
            out.append(f'<div class="row {row_cls}">')
            out.append(f'<audio controls preload="none" src="{src}"></audio>')
            out.append('<div class="meta">')
            out.append(f'<div class="name">{c["file"]}</div>')
            out.append(f'<div class="by">{c.get("author","?")} &middot; '
                       f'{c.get("licence","?")} &middot; {mb:.2f} MB &middot; '
                       f'<a href="{page}" target="_blank">source</a></div>')
            out.append('</div>')
            f = c["file"].replace("'", "\\'")
            for v in ("keep", "drop"):
                on = f' class="on-{v}"' if verdict == v else ""
                out.append(f'<button data-v="{v}"{on} '
                           f"onclick=\"mark('{f}','{v}',this)\">{v}</button>")
            out.append('</div>')
        out.append('</section>')
    out.append(PAGE_TAIL)
    return "".join(out)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STAGE), **kw)

    def log_message(self, *a):        # the page makes one request per click; stay quiet
        pass

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
            self._send(build_page(manifest).encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path.startswith("/files/"):
            self.path = urllib.parse.unquote(self.path[len("/files"):])
            return super().do_GET()
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/verdict":
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        manifest = json.loads(MANIFEST.read_text())
        current = "unheard"
        for spec in manifest.values():
            for c in spec.get("candidates", []):
                if c["file"] == payload["file"]:
                    # Clicking the verdict a file already has clears it, so a misclick is
                    # undoable without hunting for a third button.
                    c["verdict"] = "unheard" if c.get("verdict") == payload["verdict"] \
                        else payload["verdict"]
                    current = c["verdict"]
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        self._send(json.dumps({"verdict": current}).encode(), "application/json")


def main() -> int:
    if not MANIFEST.exists():
        print(f"no manifest at {MANIFEST}\nrun: .venv/bin/python tools/audio/fetch_cc0.py")
        return 1
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"auditioning {STAGE}\n\n  {URL}\n\nctrl-c to stop")
        try:
            webbrowser.open(URL)
        except Exception:  # noqa: BLE001 — a headless box just gets the printed URL
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nverdicts saved to", MANIFEST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
