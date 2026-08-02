#!/usr/bin/env python3
"""A local control panel for the unattended loop: watch it, stop it, start it.

WHY THIS EXISTS. `tools/autoloop.py --status` and `--stop` answer the right questions, but
only if you are sitting at the terminal the loop was started from. The owner was not: they
started a night run, saw something they did not like, and had no way to intervene except
Ctrl-C — which (before the process-group fix) killed the spawned session mid-tool-call and
left a branch checked out that made every later run refuse at preflight. The controls existed
and were unreachable. That is the whole problem this file solves.

WHAT IT IS. One stdlib HTTP server, one self-contained page, three actions. No dependencies,
no build step, no framework. The page polls `/api/state` and renders what the loop already
writes to disk — `.cache/autoloop.lock`, `.cache/autoloop-status.json`, the live session log.
It invents no state of its own, so it cannot disagree with `--status`; both read the same
files. Every action goes through the same mechanism the CLI uses:

    Stop    writes the stopfile          — identical to `autoloop.py --stop`
    Kill    two SIGINTs to the loop pid  — identical to pressing Ctrl-C twice
    Start   spawns `autoloop.py`         — after the same preflight the loop runs itself

IT MUST NOT BECOME A SURVIVOR, and that is not a hypothetical here. `tools/audio/serve.py` is
named in `tools/reap.py`'s own docstring as a known leak because `serve_forever()` has no exit
condition at all, and on this project a forgotten background process is a MONEY bug — the
agent harness re-invokes the model when a tracked child finally exits, which has already
spilled the owner's subscription into paid credits once. So this server:

  * takes a `tools/lease.py` lease, so a sibling agent's `reap.py --kill` spares it while it
    is legitimately serving and finds it once it is not;
  * shuts itself down after `--idle` seconds with no request. The page polls continuously, so
    "no request" means "no browser has this open", which is exactly when it should stop. A
    server left running overnight for a page nobody is looking at is the failure mode;
  * refuses to bind anything but loopback without a token, because these endpoints kill
    processes and start billable agent sessions.

The loop and this server are independent processes on purpose. Closing the page does not stop
the loop, and stopping the server does not either — the loop's own boundary rules still apply.

DELIBERATELY NOT IN `reap.py`'s `OUR_TOOLS`, and neither is `tools/autoloop.py`. The obvious
move is to make both visible to the reaper, and it is wrong: `reap.py --kill` runs from the
`SessionEnd` hook in `.claude/settings.json`, so an overnight loop would be killed the moment
any unrelated agent session ended, and `autoloop.py`'s own preflight — which refuses to start
unless `reap.py` prints "clean" — would see the loop and then the panel and refuse to start
either. An unattended loop is not a stray; it is the point. The lease is still taken, because
everything spawned underneath walks its ppid chain looking for one, and a correct owner beats
"unleased". The leak this file could have become is bounded by `--idle` instead.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import signal
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import autoloop                                     # noqa: E402 — the single source of truth
import lease                                        # noqa: E402 — PRC-07, see module docstring

PORT = 8732                                         # 8731 is tools/audio/serve.py
LEASE_TTL_S = 12 * 3600.0                           # crash backstop only; --idle is the real end
TAIL_LINES = 200


# ─────────────────────────────────────────────────────────────────── state ──

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def loop_pid() -> int | None:
    """The pid in the lockfile, if that process is still alive.

    A lockfile whose pid is gone is a *stale* lock, not a running loop — it is what a `kill -9`
    leaves behind — and reporting it as "running" would be the one lie this panel could tell
    that matters, because the owner would then wait for a loop that will never move again.
    """
    try:
        pid = int(autoloop.LOCK.read_text().splitlines()[0])
    except Exception:                                          # noqa: BLE001
        return None
    return pid if _pid_alive(pid) else None


def newest_log() -> Path | None:
    if not autoloop.LOGDIR.exists():
        return None
    logs = sorted(autoloop.LOGDIR.glob("*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def state() -> dict:
    pid = loop_pid()
    stale = autoloop.LOCK.exists() and pid is None
    status: dict = {}
    try:
        status = json.loads(autoloop.STATUS.read_text())
    except Exception:                                          # noqa: BLE001
        pass

    log_path, tail, log_age = None, [], None
    lp = newest_log()
    if lp:
        log_path = str(lp)
        log_age = int(time.time() - lp.stat().st_mtime)
        try:
            lines = [ln.rstrip() for ln in lp.read_text(errors="replace").splitlines()]
            tail = [ln for ln in lines if ln.strip()][-TAIL_LINES:]
        except Exception:                                      # noqa: BLE001
            pass

    _, branch = autoloop.sh("git", "rev-parse", "--abbrev-ref", "HEAD", timeout=20)
    _, dirty = autoloop.sh("git", "status", "--porcelain", timeout=20)
    _, head = autoloop.sh("git", "log", "--oneline", "-1", timeout=20)

    session_pid = status.get("session_pid")
    return {
        "running": pid is not None,
        "stale_lock": stale,
        "pid": pid,
        "session_pid": session_pid,
        "session_alive": bool(session_pid and _pid_alive(int(session_pid))),
        "stop_requested": autoloop.STOPFILE.exists(),
        "status": status,
        "log": {"path": log_path, "age_s": log_age, "tail": tail},
        "tree": {"branch": branch, "dirty": len(dirty.splitlines()) if dirty else 0,
                 "head": head},
        "now": time.strftime("%H:%M:%S", time.localtime()),
    }


# ───────────────────────────────────────────────────────────────── actions ──

def act_stop() -> dict:
    """Graceful: the loop finishes the issue it is on, then exits with the tree on main."""
    autoloop.STOPFILE.parent.mkdir(parents=True, exist_ok=True)
    autoloop.STOPFILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ\n", time.gmtime()))
    pid = loop_pid()
    if pid:
        # The stopfile alone is enough — the loop checks it at the next issue boundary. The
        # signal is a courtesy that makes the "will stop after this issue" line appear in the
        # log immediately, so the owner sees the request was received rather than wondering.
        try:
            os.kill(pid, signal.SIGINT)
        except Exception:                                      # noqa: BLE001
            pass
        return {"ok": True, "msg": "Stop requested. It will finish the current issue, "
                                   "then exit with the tree on main."}
    return {"ok": True, "msg": "No loop is running. The stopfile is set; clear it before "
                               "starting one, or Start will clear it for you."}


def act_kill() -> dict:
    """Two SIGINTs — exactly what pressing Ctrl-C twice does. The second one is what the
    loop's handler escalates to `kill_tree`, taking the spawned session and everything under
    it. Destructive by design: the tree may be left mid-work, which is why the page confirms."""
    pid = loop_pid()
    if not pid:
        return {"ok": False, "msg": "No loop is running."}
    try:
        os.kill(pid, signal.SIGINT)
        time.sleep(0.7)
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        return {"ok": True, "msg": "It exited before the second signal landed."}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "msg": f"{exc.__class__.__name__}: {exc}"}
    return {"ok": True, "msg": "Killed. CHECK THE TREE — the session was interrupted "
                               "mid-work and may have left a branch checked out."}


def act_start(body: dict) -> dict:
    """Spawn a loop, after the same preflight it would run itself.

    Preflight runs HERE rather than being left to the child so the refusal reaches the page.
    A loop that starts and dies four seconds later, with the reason buried in a log file the
    owner has not opened, is the failure this panel exists to prevent.
    """
    if loop_pid():
        return {"ok": False, "msg": "A loop is already running."}
    autoloop.LOCK.unlink(missing_ok=True)      # stale lock from a kill -9; the pid is dead
    autoloop.STOPFILE.unlink(missing_ok=True)

    err = autoloop.preflight()
    if err:
        return {"ok": False, "msg": f"Preflight refused: {err}"}

    # No --model unless the page asks for one: the default belongs to autoloop.py, and a copy
    # of it here would be a second place to forget when it changes (decision 077 changed it).
    argv = [str(autoloop.PY), "tools/autoloop.py",
            "--max-iterations", str(int(body.get("max_iterations") or 8))]
    if body.get("model"):
        argv += ["--model", str(body["model"])]
    if body.get("start_with"):
        argv += ["--start-with", str(body["start_with"])]

    autoloop.LOGDIR.mkdir(parents=True, exist_ok=True)
    out = autoloop.LOGDIR / f"loop-{int(time.time())}.out"
    with out.open("w", encoding="utf-8") as fh:
        # Detached, for the same reason the loop detaches its own child: this server is not
        # the loop's parent in any meaningful sense, and shutting the panel must not take the
        # night's work with it.
        subprocess.Popen(argv, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
    return {"ok": True, "msg": f"Started. Loop output: {out.name}"}


# ────────────────────────────────────────────────────────────────── server ──

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Latticefall autoloop</title>
<style>
  :root{--bg:#0e1116;--panel:#161b22;--line:#2a313c;--fg:#e6edf3;--dim:#8b98a5;
        --ok:#3fb950;--warn:#d29922;--bad:#f85149;--acc:#58a6ff}
  @media (prefers-color-scheme: light){
    :root{--bg:#f6f8fa;--panel:#fff;--line:#d8dee4;--fg:#1f2328;--dim:#636c76;
          --ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--acc:#0969da}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 ui-monospace,
       SFMono-Regular,Menlo,Consolas,monospace}
  .wrap{max-width:1000px;margin:0 auto;padding:16px}
  h1{font-size:15px;margin:0;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
  .bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
  .pill{padding:3px 10px;border-radius:999px;font-weight:600;font-size:12px;
        border:1px solid var(--line)}
  .pill.run{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok);
            border-color:var(--ok)}
  .pill.idle{color:var(--dim)}
  .pill.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn);
             border-color:var(--warn)}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
        padding:14px;margin-bottom:14px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .k{color:var(--dim);min-width:104px;display:inline-block}
  button{font:inherit;font-weight:600;padding:9px 16px;border-radius:6px;cursor:pointer;
         border:1px solid var(--line);background:var(--panel);color:var(--fg)}
  button:hover:not(:disabled){border-color:var(--acc);color:var(--acc)}
  button:disabled{opacity:.4;cursor:not-allowed}
  button.stop{border-color:var(--warn);color:var(--warn)}
  button.kill{border-color:var(--bad);color:var(--bad)}
  button.go{border-color:var(--ok);color:var(--ok)}
  input{font:inherit;padding:8px 10px;border-radius:6px;border:1px solid var(--line);
        background:var(--bg);color:var(--fg)}
  input[type=text]{width:150px}
  input[type=number]{width:64px}
  pre{margin:0;padding:12px;background:var(--bg);border:1px solid var(--line);
      border-radius:6px;max-height:46vh;overflow:auto;white-space:pre-wrap;
      word-break:break-word;font-size:12.5px;line-height:1.45}
  .ev{margin:2px 0 2px 104px;color:var(--acc);font-size:13px}
  .msg{padding:10px 12px;border-radius:6px;border:1px solid var(--acc);margin-bottom:14px;
       color:var(--acc);background:color-mix(in srgb,var(--acc) 10%,transparent)}
  .msg.bad{border-color:var(--bad);color:var(--bad);
           background:color-mix(in srgb,var(--bad) 10%,transparent)}
  .banner{border-color:var(--warn);color:var(--warn);
          background:color-mix(in srgb,var(--warn) 12%,transparent)}
  .dim{color:var(--dim)}
  h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);
     margin:0 0 8px}
</style></head><body><div class="wrap">

<div class="bar">
  <h1>Latticefall autoloop</h1>
  <span id="pill" class="pill idle">…</span>
  <span class="dim" id="clock"></span>
</div>

<div id="msg" style="display:none"></div>
<div id="stopbanner" class="msg banner" style="display:none"></div>

<div class="card">
  <div class="row" style="margin-bottom:12px">
    <button class="stop" id="bstop">Stop after this issue</button>
    <button class="kill" id="bkill">Kill now</button>
    <span style="flex:1"></span>
    <input type="text" id="startwith" placeholder="PLC-07,LF-185" title="--start-with">
    <input type="number" id="iters" value="8" min="1" max="50" title="--max-iterations">
    <button class="go" id="bstart">Start</button>
  </div>
  <div><span class="k">state</span><span id="state">—</span></div>
  <div><span class="k">issue</span><span id="issue">—</span></div>
  <div><span class="k">elapsed</span><span id="mins">—</span></div>
  <div id="evidence"></div>
  <div><span class="k">loop pid</span><span id="pid">—</span></div>
  <div><span class="k">session pid</span><span id="spid">—</span></div>
  <div><span class="k">tree</span><span id="tree">—</span></div>
  <div><span class="k">head</span><span id="head">—</span></div>
</div>

<div class="card">
  <h2>live session log <span class="dim" id="logpath"></span></h2>
  <pre id="log">no session log yet</pre>
</div>

<p class="dim" style="font-size:12px">
  This page polls every 2s and shuts its server down after <span id="idle">?</span> idle.
  Closing it never stops the loop.
</p>

<script>
const T = new URLSearchParams(location.search).get('t') || '';
const $ = id => document.getElementById(id);
let busy = false, pinned = true;

function api(path, body){
  return fetch(path, {method: body ? 'POST' : 'GET',
    headers: {'X-Token': T, 'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : undefined}).then(r => r.json());
}
function say(text, bad){
  const m = $('msg'); m.textContent = text;
  m.className = 'msg' + (bad ? ' bad' : ''); m.style.display = text ? '' : 'none';
}
function render(s){
  $('clock').textContent = s.now;
  const p = $('pill');
  if (s.running){ p.className = 'pill run'; p.textContent = 'RUNNING'; }
  else if (s.stale_lock){ p.className = 'pill warn'; p.textContent = 'STALE LOCK'; }
  else { p.className = 'pill idle'; p.textContent = 'not running'; }

  const st = s.status || {};
  $('state').textContent = st.state || '—';
  $('issue').textContent = st.issue ? (st.issue + (st.title ? ' — ' + st.title : '')) : '—';
  $('mins').textContent = (st.minutes === undefined || st.minutes === null)
      ? '—' : st.minutes + ' min';
  $('pid').textContent = s.pid || '—';
  $('spid').textContent = s.session_pid
      ? (s.session_pid + (s.session_alive ? ' (alive)' : ' (gone)')) : '—';
  $('tree').textContent = s.tree.branch + ', ' + s.tree.dirty + ' uncommitted';
  $('head').textContent = s.tree.head || '—';

  $('evidence').innerHTML = '';
  (st.evidence || []).forEach(e => {
    const d = document.createElement('div'); d.className = 'ev'; d.textContent = e;
    $('evidence').appendChild(d);
  });

  const sb = $('stopbanner');
  if (s.stop_requested){
    sb.style.display = '';
    sb.textContent = s.running
      ? 'Stop requested — it will exit at the next issue boundary.'
      : 'A stopfile is set but no loop is running. Start clears it.';
  } else sb.style.display = 'none';

  $('logpath').textContent = s.log.path
      ? s.log.path.split('/').pop() + ' · ' + s.log.age_s + 's since last write' : '';
  const pre = $('log');
  const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  const text = (s.log.tail || []).join('\n') || 'no session log yet';
  if (pre.textContent !== text){
    pre.textContent = text;
    if (pinned || atBottom) pre.scrollTop = pre.scrollHeight;
  }
  $('bstop').disabled = !s.running;
  $('bkill').disabled = !s.running;
  $('bstart').disabled = !!s.running;
}
$('log').addEventListener('scroll', e => {
  const pre = e.target;
  pinned = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
});
async function tick(){
  if (busy || document.hidden) return;          // a hidden tab polls nothing
  try { render(await api('/api/state')); } catch(e){ /* server gone; keep the last frame */ }
}
async function act(path, body){
  busy = true;
  try { const r = await api(path, body || {}); say(r.msg, !r.ok); }
  finally { busy = false; tick(); }
}
$('bstop').onclick  = () => act('/api/stop');
$('bkill').onclick  = () => confirm(
  'Kill the running session NOW?\n\nThis interrupts it mid-work and may leave a branch ' +
  'checked out. "Stop after this issue" is almost always what you want.') && act('/api/kill');
$('bstart').onclick = () => act('/api/start',
  {start_with: $('startwith').value.trim(), max_iterations: +$('iters').value});
tick(); setInterval(tick, 2000);
</script></div></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "LatticefallAutoloopPanel/1"
    token = ""
    idle_label = "?"
    last_seen = time.time()

    def log_message(self, *a):                                 # noqa: D102, ANN002
        pass                                                   # the panel is not a web log

    # ── plumbing ──
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _authed(self) -> bool:
        if not Handler.token:
            return True
        given = self.headers.get("X-Token", "")
        if not given:
            from urllib.parse import parse_qs, urlparse
            given = (parse_qs(urlparse(self.path).query).get("t") or [""])[0]
        return secrets.compare_digest(given, Handler.token)

    # ── routes ──
    def do_GET(self) -> None:                                  # noqa: N802
        Handler.last_seen = time.time()
        path = self.path.split("?", 1)[0]
        if not self._authed():
            return self._send(403, b"forbidden - append ?t=<token>", "text/plain")
        if path == "/":
            page = PAGE.replace("<span id=\"idle\">?</span>",
                                f"<span id=\"idle\">{Handler.idle_label}</span>")
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._json(state())
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:                                 # noqa: N802
        Handler.last_seen = time.time()
        path = self.path.split("?", 1)[0]
        if not self._authed():
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception:                                      # noqa: BLE001
            body = {}
        if path == "/api/stop":
            return self._json(act_stop())
        if path == "/api/kill":
            return self._json(act_kill())
        if path == "/api/start":
            return self._json(act_start(body))
        self._json({"ok": False, "msg": "no such action"}, 404)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 by default. Any other bind REQUIRES a token, which is "
                         "generated and printed in the URL.")
    ap.add_argument("--idle", type=int, default=1800,
                    help="seconds with no request before the server exits (0 disables — "
                         "do not use it unattended)")
    ap.add_argument("--token", default="", help="override the generated token")
    args = ap.parse_args()

    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    token = args.token or ("" if loopback else secrets.token_urlsafe(9))
    Handler.token = token
    Handler.idle_label = f"{args.idle // 60} min" if args.idle else "never (unbounded)"

    url = f"http://{'localhost' if loopback else args.host}:{args.port}/"
    if token:
        url += f"?t={token}"

    socketserver.TCPServer.allow_reuse_address = True
    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)

    def watchdog() -> None:
        """Exit when nobody is looking. The page polls every 2s, so idleness here means no
        browser has it open — and an unwatched server is the leak `reap.py` was written for."""
        while True:
            time.sleep(5)
            if args.idle and time.time() - Handler.last_seen > args.idle:
                print(f"\n[panel] no request in {args.idle}s — shutting down. "
                      f"The loop, if running, is unaffected.", flush=True)
                threading.Thread(target=httpd.shutdown, daemon=True).start()
                return

    threading.Thread(target=watchdog, daemon=True).start()
    print(f"autoloop panel\n\n  {url}\n\n"
          f"idle shutdown: {Handler.idle_label} · ctrl-c to stop\n", flush=True)
    with lease.acquire("autoloop-web", [__file__, args.host, str(args.port)],
                       ttl_s=LEASE_TTL_S):
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[panel] stopped. The loop, if running, is unaffected.")
        finally:
            httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
