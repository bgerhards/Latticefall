#!/usr/bin/env python3
"""Work the issue backlog unattended, one issue per session, until there is nothing left.

WHY THIS SHAPE, because the obvious alternative was tried first and is worse.

The problem this solves is not "the context window fills up". It is "the agent is sometimes
mid-task when the context window fills up". Those are different problems and only the second
one actually hurts. Every high-severity failure a compaction can cause — an uncommitted tree
nobody can explain, a subagent whose results land in a context that has forgotten why it was
dispatched, a pushed branch with no PR — is a *mid-task* failure. Losing prose is survivable;
this project's durable trail is 171 commits with real why-bodies, 41 append-only journal
entries, 76 decisions carrying their rejected alternatives, and 50 closed issues with evidence
in the close note. An entire investigation (LF-172) was reconstructed from `git log` alone this
week, and the reconstruction was more reliable than the backlog item describing it.

So: make the unit of work small enough that a session never fills, and the boundary is always
*between* tasks by construction. One issue, one session, ship, exit. Compaction then never
fires, and the wrap collapses into the ship it was always duplicating — PR, journal, issue
close. The `PreCompact`/`PostCompact` hooks stay wired as insurance for the rare long issue;
they are no longer the plan.

WHAT AN ITERATION MUST PRODUCE. A merged pull request, or an explicit reason it could not.
Anything else is a failure, including "ran to completion and did nothing" — silence is the
failure mode a loop is most likely to hide, so it is checked for directly (`merged_since`).

WHAT STOPS THE LOOP, in priority order. A dirty tree after an iteration stops it immediately
rather than compounding into the next one — a shared working tree is the single most expensive
thing to get wrong here. Then: an owner-gated decision, an empty queue, the iteration cap, the
wall-clock cap, or too many consecutive failures.

NOT A SCHEDULER. This runs in the foreground and holds a lockfile. Run it under `systemd --user`
or `nohup` if you want to walk away; do not run two.

HOW TO WATCH IT AND HOW TO STOP IT, because the first owner to run it could do neither and
killed it mid-issue, which left a branch checked out and every later run refusing at preflight:

    tools/autoloop.py --status     what it is doing right now, and the tail of the live session
    tools/autoloop.py --stop       finish the current issue, then exit with the tree on main
    Ctrl-C once                    the same; twice kills the running session immediately

The only unsafe stop is the second Ctrl-C, and it warns before it does it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"
ISSUES = ROOT / "docs" / "issues"
MAP = ISSUES / ".map.json"
LOCK = ROOT / ".cache" / "autoloop.lock"
STATEFILE = ROOT / ".cache" / "autoloop-state.json"
STATUS = ROOT / ".cache" / "autoloop-status.json"   # live, human-readable, tail-friendly
STOPFILE = ROOT / ".cache" / "autoloop-stop"        # touch it to stop at the next boundary
LOGDIR = ROOT / ".cache" / "autoloop-logs"

# ntfy. A self-hosted instance is usually behind auth and often behind a self-signed cert, so
# both are supported and neither is guessed. All of it is opt-in via the environment; nothing
# is stored in the repository.
#
#   LF_NTFY_URL       full topic URL, e.g. https://ntfy.home.lan/latticefall   (required)
#   LF_NTFY_TOKEN     ntfy access token -> Authorization: Bearer tk_...        (preferred)
#   LF_NTFY_USER      username, with LF_NTFY_PASS -> HTTP Basic
#   LF_NTFY_PASS      password
#   LF_NTFY_CA        path to a CA bundle, for a private/self-signed cert      (preferred)
#   LF_NTFY_INSECURE  "1" to skip TLS verification entirely                    (last resort)
#
# Token beats user/pass when both are set, because that is ntfy's own preference and a token
# can be scoped to one topic. LF_NTFY_CA beats LF_NTFY_INSECURE for the obvious reason: one
# verifies a certificate you chose to trust, the other verifies nothing at all.
NTFY = os.environ.get("LF_NTFY_URL", "").strip()
NTFY_TOKEN = os.environ.get("LF_NTFY_TOKEN", "").strip()
NTFY_USER = os.environ.get("LF_NTFY_USER", "").strip()
NTFY_PASS = os.environ.get("LF_NTFY_PASS", "")
NTFY_CA = os.environ.get("LF_NTFY_CA", "").strip()
NTFY_INSECURE = os.environ.get("LF_NTFY_INSECURE", "").strip() == "1"

# GAME WORK FIRST, PROCESS LAST — and this ordering is deliberate rather than the PRD's.
#
# PRD §5's dependency order is E1 -> E2 -> E3/E4 -> E5 -> E6 -> E7, and ranking by it put
# `PRC-14` (asset<->data coupling, pure tooling) at the head of the queue. That is precisely
# the failure the owner stopped a session to name: most of a day's pull requests changed the
# tooling and only one changed the game. E1 and E2 are effectively finished, so what survives
# in them is chores; sequencing chores ahead of the thing the owner is waiting to play is how
# an autonomous loop quietly spends a night on infrastructure.
#
# So: E3 first — it is in flight, and it is what makes the owner's own complaint (LF-176,
# "most turrets can barely reach anything") fixable in play. Then the rest of the game, then
# balance, then process. Dependencies still gate everything: an issue whose `depends:` are
# open is skipped regardless of milestone, so this only decides order among READY work.
MILESTONE_RANK = {
    "E3 Placement": 0,   # in flight; free placement is the owner's complaint made fixable
    "E4 Terrain": 1,     # visible ground
    "E5 War": 2,         # the army
    "E6 Fidelity": 3,    # how it looks
    "E7 Balance": 4,     # needs the verbs to exist first
    "E2 Camera": 5,      # complete; anything left is a straggler
    "E1 Process": 6,     # tooling — last, on purpose
}


# ───────────────────────────────────────────────────────────────── plumbing ──

def sh(*args: str, timeout: int = 120, cwd: Path = ROOT) -> tuple[int, str]:
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except Exception as exc:                                   # noqa: BLE001
        return 1, f"{exc.__class__.__name__}: {exc}"


def _ntfy_auth() -> dict[str, str]:
    """Authorization header for a protected instance, or nothing."""
    if NTFY_TOKEN:
        return {"Authorization": f"Bearer {NTFY_TOKEN}"}
    if NTFY_USER:
        raw = base64.b64encode(f"{NTFY_USER}:{NTFY_PASS}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}
    return {}


def _ntfy_ssl():
    """TLS context for a self-hosted host, or None to use the default.

    A private CA is the right answer and is tried first. `LF_NTFY_INSECURE` exists because a
    LAN box with a self-signed cert is common and a notification is not a secret — but it
    disables verification entirely, so it announces itself once rather than failing quietly
    into a false sense of encryption.
    """
    if NTFY_CA:
        return ssl.create_default_context(cafile=NTFY_CA)
    if NTFY_INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


_ntfy_warned = False

# HTTP HEADER VALUES ARE LATIN-1 ON THE WIRE, and Python enforces it. `http.client` encodes
# every header with `latin-1`, so one em dash in a Title raises
# `'latin-1' codec can't encode character '—'` *before* the request leaves the machine —
# which this module then reported as "ntfy unreachable", pointing at the network for a bug that
# was entirely local. Its own notification titles are written in this project's prose style, so
# they are full of em dashes; every one of those pushes had been failing silently-ish since the
# feature landed.
#
# The fix is to fold header values to ASCII rather than to RFC 2047 encoded-words: ntfy does
# decode `=?UTF-8?B?...?=`, but only on recent versions, and a self-hosted box that does not
# would show the raw gibberish as the title. A hyphen where an em dash was is a loss nobody
# notices on a phone. The BODY is unaffected — it is UTF-8 request data, not a header.
_ASCII_FOLD = str.maketrans({
    "—": "-", "–": "-", "−": "-",       # em / en dash, minus
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", "·": "-", "→": "->", "×": "x", " ": " ",
})


def _header_safe(value: str) -> str:
    """An HTTP-header-safe rendering of `value`: ASCII only, no control characters."""
    folded = value.translate(_ASCII_FOLD).encode("ascii", "replace").decode("ascii")
    return "".join(ch for ch in folded if 32 <= ord(ch) < 127).strip()


def notify(title: str, body: str, priority: str = "default", tags: str = "") -> None:
    """Best-effort push. A failed notification must never stop the loop — the whole point is
    that nobody is watching, so the work continuing matters more than the message landing.

    It does, however, say so ONCE. A silently-401ing notifier is indistinguishable from a
    silent loop, which is the exact anxiety this feature exists to remove.
    """
    global _ntfy_warned
    line = f"[autoloop] {title}: {body.splitlines()[0] if body else ''}"
    print(line, flush=True)
    if not NTFY:
        return
    headers = {"Title": _header_safe(title), "Priority": priority, **_ntfy_auth()}
    if tags:
        headers["Tags"] = _header_safe(tags)
    req = urllib.request.Request(NTFY, data=body.encode("utf-8"), method="POST",
                                 headers=headers)
    try:
        ctx = _ntfy_ssl()
        urllib.request.urlopen(req, timeout=10, **({"context": ctx} if ctx else {})).read()
    except urllib.error.HTTPError as exc:
        if not _ntfy_warned:
            _ntfy_warned = True
            hint = (" — set LF_NTFY_TOKEN, or LF_NTFY_USER/LF_NTFY_PASS"
                    if exc.code in (401, 403) else "")
            print(f"[autoloop] ntfy rejected the push: HTTP {exc.code}{hint}. "
                  f"Continuing WITHOUT notifications.", flush=True)
    except Exception as exc:                                   # noqa: BLE001
        if not _ntfy_warned:
            _ntfy_warned = True
            extra = ""
            if isinstance(exc, urllib.error.URLError) and "CERTIFICATE" in str(exc).upper():
                extra = " — set LF_NTFY_CA to your CA bundle, or LF_NTFY_INSECURE=1 on a LAN host"
            elif isinstance(exc, UnicodeEncodeError):
                extra = " — this is a LOCAL header-encoding bug, not the network; see _header_safe"
            print(f"[autoloop] ntfy unreachable ({exc}){extra}. "
                  f"Continuing WITHOUT notifications.", flush=True)


# ──────────────────────────────────────────────────────────── stopping it ──
#
# THERE WAS NO WAY TO STOP THIS THING SAFELY, and that is why the owner killed it mid-flight.
# Ctrl-C into a foreground loop killed the parent and left the spawned session, the branch it
# had checked out, and whatever it had staged; the next run's preflight then refused with
# "on branch 'lf/...', expected main" and the owner had no idea whether stopping had been safe.
#
# A stop must therefore have a *boundary*: the loop finishes the issue it is on, ships or
# reports it, and exits with the tree back on main. Two ways to ask for one, because the owner
# may not have the terminal the loop is running in:
#
#   .venv/bin/python tools/autoloop.py --stop     from anywhere; drops a stopfile
#   Ctrl-C / SIGTERM                              once = graceful, twice = kill the child now
#
# Only the second signal is destructive, and it says so before it does it.
_STOP = {"requested": False, "hard": False}


def _stop_reason() -> str:
    if _STOP["hard"]:
        return "second signal — child killed"
    if _STOP["requested"]:
        return "signal"
    if STOPFILE.exists():
        return "--stop requested"
    return ""


def _on_signal(signum, _frame) -> None:                        # noqa: ANN001
    name = signal.Signals(signum).name
    if _STOP["requested"]:
        _STOP["hard"] = True
        print(f"[autoloop] {name} again — killing the running session NOW. "
              f"The tree may be left mid-work.", flush=True)
    else:
        _STOP["requested"] = True
        print(f"[autoloop] {name} — will stop after the current issue finishes. "
              f"Signal again to kill it immediately.", flush=True)


def load_state() -> dict:
    try:
        return json.loads(STATEFILE.read_text())
    except Exception:                                          # noqa: BLE001
        return {"failures": {}}


def save_state(state: dict) -> None:
    STATEFILE.parent.mkdir(parents=True, exist_ok=True)
    STATEFILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# ───────────────────────────────────────────────────────────── issue picking ──

@dataclass
class Issue:
    number: int
    spec_id: str
    title: str
    milestone: str
    depends: list[str]

    @property
    def rank(self) -> tuple[int, int]:
        return (MILESTONE_RANK.get(self.milestone, 99), self.number)


def spec_depends(spec_id: str) -> list[str]:
    """`depends:` from the issue spec's header. The specs are the source of truth; GitHub is a
    projection of them (`tools/issues.py`), so dependency order is read from disk."""
    for path in ISSUES.glob(f"{spec_id}-*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("depends:"):
                return [s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()]
            if line.strip() == "---":
                break
    return []


def open_issues() -> list[Issue]:
    code, out = sh("gh", "issue", "list", "--state", "open", "--limit", "200",
                   "--json", "number,title,milestone", timeout=90)
    if code != 0:
        return []
    try:
        raw = json.loads(out)
    except Exception:                                          # noqa: BLE001
        return []
    out_list: list[Issue] = []
    for it in raw:
        m = re.match(r"\[([A-Z]+-\d+)\]\s*(.*)", it.get("title", ""))
        if not m:
            continue
        sid = m.group(1)
        out_list.append(Issue(
            number=it["number"], spec_id=sid, title=m.group(2),
            milestone=((it.get("milestone") or {}) or {}).get("title", "") or "",
            depends=spec_depends(sid)))
    return out_list


def pick(issues: list[Issue], state: dict, max_failures: int,
         prefer: list[str] | None = None) -> Issue | None:
    """Lowest-ranked ready issue: dependencies closed, not failed too often.

    Skipping an issue whose `depends:` are still open is the difference between a loop that
    makes progress and one that repeatedly picks the hardest blocked thing and fails on it.

    `prefer` seeds the front of the queue by spec id, for the case where a human knows the
    next thing that matters and the milestone heuristic does not. It still respects
    dependencies and the failure count — it reorders ready work, it does not force anything.
    """
    open_ids = {i.spec_id for i in issues}
    ready = [i for i in issues
             if not (set(i.depends) & open_ids)
             and state["failures"].get(i.spec_id, 0) < max_failures]
    if not ready:
        return None
    if prefer:
        for sid in prefer:
            for i in ready:
                if i.spec_id == sid:
                    return i
    return min(ready, key=lambda i: i.rank)


# ─────────────────────────────────────────────────────────────── the session ──

PROMPT = """\
Run /session-start first.

Then work exactly ONE issue to completion and ship it: **#{number} — {spec_id}: {title}**.

Read `docs/issues/{spec_id}-*.md` — it is the source of truth and it is detailed.

Ship it with /ship: branch `lf/<epic>-<slug>`, gate it, invoke the `chronicler` agent for the
journal entry (every PR updates the journal — copy any images into `docs/chronicle/assets/`,
never link a scratch path), push, wait for CI, squash-merge when green, and close the issue
with `tools/issues.py close {spec_id} --note "<what landed and how it was proved>"`.

Rules for this run:

- **Do not start any other issue.** Anything you discover goes to the backlog with
  `tools/backlog.py add` and, if it deserves one, a new spec in `docs/issues/` — not into this
  change.
- **A measured "no" is a successful outcome.** If the issue's premise turns out to be wrong,
  say so with the numbers and do not ship something you do not believe in.
- **If you hit a decision only the owner can make**, stop, and say `BLOCKED:` followed by the
  question. Do not guess. `docs/DECISIONS.md` and PRD §6 are where those live.
- **Leave the tree clean.** Everything committed and merged, or explicitly explained.
- Delegate with /dispatch; at most one or two Godot-launching workstreams at once.

When you are done, end your final message with exactly one of:
  `RESULT: SHIPPED #<pr>` · `RESULT: BLOCKED <why>` · `RESULT: REFUSED <why>`
"""


def merged_prs_since(iso: str) -> list[str]:
    code, out = sh("gh", "pr", "list", "--state", "merged", "--limit", "20",
                   "--json", "number,title,mergedAt", timeout=90)
    if code != 0:
        return []
    try:
        return [f"#{p['number']} {p['title']}"
                for p in json.loads(out) if (p.get("mergedAt") or "") > iso]
    except Exception:                                          # noqa: BLE001
        return []


def preflight() -> str | None:
    """Refuse to start on a tree that is not pristine. Everything downstream assumes it."""
    code, out = sh("git", "status", "--porcelain")
    if code != 0:
        return f"git status failed: {out}"
    if out:
        return f"working tree is dirty ({len(out.splitlines())} file(s)) — refusing to start"
    code, branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        # Usually the leftover of a session that was killed mid-issue. Say how to clear it —
        # the message alone sent the owner looking for a fault that was not there.
        _, ahead = sh("git", "rev-list", "--count", f"origin/main..{branch}")
        unmerged = (f"it has {ahead} commit(s) not on origin/main — inspect before discarding"
                    if ahead.strip() not in ("0", "") else
                    "it has no commits of its own; safe to delete: "
                    f"git checkout main && git branch -D {branch}")
        return f"on branch {branch!r}, expected main. {unmerged}"
    sh("git", "fetch", "origin", timeout=120)
    _, behind = sh("git", "rev-list", "--count", "HEAD..origin/main")
    if behind.strip() not in ("0", ""):
        return f"main is {behind} commit(s) behind origin — pull first"
    if PY.exists():
        code, out = sh(str(PY), "tools/reap.py", timeout=60)
        if "clean" not in out:
            return f"stray processes present: {out.splitlines()[0] if out else '?'}"
    return None


def progress_evidence(logfile: Path, branch_before: str) -> list[str]:
    """What the session has actually DONE, not merely that it is still breathing.

    A heartbeat saying "alive" is nearly worthless — a wedged process is also alive. These are
    the three signals that distinguish working from stuck, cheapest first: the last thing the
    session said, how many commits exist that did not before, and whether a pull request is
    open yet. Together they answer "is it moving" without anyone reading a log.
    """
    bits: list[str] = []
    try:
        tail = [ln.strip() for ln in logfile.read_text(errors="replace").splitlines() if ln.strip()]
        if tail:
            bits.append(f"last: {tail[-1][:160]}")
    except Exception:                                          # noqa: BLE001
        pass
    code, branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD", timeout=20)
    if code == 0 and branch and branch != branch_before:
        bits.append(f"branch: {branch}")
        _, n = sh("git", "rev-list", "--count", f"origin/main..{branch}", timeout=20)
        if n.strip().isdigit() and int(n) > 0:
            bits.append(f"{n} commit(s)")
    code, out = sh("gh", "pr", "list", "--state", "open", "--limit", "3",
                   "--json", "number,title", timeout=45)
    if code == 0:
        try:
            prs = json.loads(out)
            if prs:
                bits.append("PR " + ", ".join(f"#{p['number']}" for p in prs))
        except Exception:                                      # noqa: BLE001
            pass
    return bits


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the spawned session AND everything it spawned.

    It is a process-group leader (`start_new_session=True`), so one `killpg` reaches its
    subagents, its Blender, its Godot. `proc.kill()` alone would leave those reparented to init
    with a core each — the survivor problem `tools/reap.py` exists for, and which costs money
    rather than fan noise because the harness re-invokes the model when a tracked child exits.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def write_status(**kw) -> None:
    """A file anyone can look at without a notification. `tail -f` friendly, and it is what a
    status page or a phone widget would read if one is ever wanted."""
    try:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps({"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                               time.gmtime()), **kw},
                                     indent=2, sort_keys=True) + "\n")
    except Exception:                                          # noqa: BLE001
        pass


def run_one(issue: Issue, model: str, timeout_s: int, heartbeat_s: int,
            remote_control: bool = False) -> tuple[str, str]:
    """Spawn one session for this issue, beating a pulse while it works.

    Output goes to a FILE rather than a pipe, deliberately. A pipe that nobody drains fills its
    kernel buffer and blocks the child — a session that produces a lot of output would deadlock
    partway through and look exactly like a hang. A file cannot do that, and it doubles as the
    source for the heartbeat's "last line", which is the cheapest real evidence of progress
    there is.

    `remote_control` is off by default: `--help` describes `--remote-control` as starting an
    *interactive* session and `-p` is non-interactive, so the combination is unverified. It is
    kept behind a flag rather than removed in case it is wanted later.
    """
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.time()
    _, branch_before = sh("git", "rev-parse", "--abbrev-ref", "HEAD", timeout=20)
    prompt = PROMPT.format(number=issue.number, spec_id=issue.spec_id, title=issue.title)
    argv = ["claude", "-p", prompt, "--dangerously-skip-permissions",
            "--model", model, "--output-format", "text"]
    if remote_control:
        argv += ["--remote-control", f"latticefall-{issue.spec_id}"]

    LOGDIR.mkdir(parents=True, exist_ok=True)
    logfile = LOGDIR / f"{issue.spec_id}-{int(t0)}.log"
    last_beat = t0
    try:
        with logfile.open("w", encoding="utf-8") as fh:
            # OWN PROCESS GROUP, or the graceful stop is a lie. Ctrl-C in a terminal sends
            # SIGINT to the whole foreground process group, so it reaches the spawned session
            # directly — the parent's handler never gets to decide anything, and the child dies
            # mid-tool-call. That is exactly what happened on the first unattended run: the
            # session had worked for three minutes and was dispatching a subagent when it took
            # `[Request interrupted by user for tool use]`, and `claude -p` printed a 15-byte
            # "Execution error" that looked like the session was broken. It was not; it was
            # interrupted. `start_new_session=True` detaches it, so a terminal Ctrl-C now hits
            # only this loop and the child runs on until the loop decides otherwise.
            proc = subprocess.Popen(argv, cwd=str(ROOT), stdout=fh,
                                    stderr=subprocess.STDOUT, text=True,
                                    start_new_session=True)
            # The child's pid goes on the record immediately. Detaching it means a `kill -9` on
            # the loop leaves it running — and an orphaned agent session bills tokens at nobody's
            # request — so whoever has to end it by hand needs the number without hunting for it.
            print(f"[autoloop] session pid {proc.pid} (own process group), log: {logfile}",
                  flush=True)
            write_status(state="working", issue=issue.spec_id, minutes=0,
                         session_pid=proc.pid, log=str(logfile))
            while True:
                if proc.poll() is not None:
                    break
                now = time.time()
                if _STOP["hard"]:
                    kill_tree(proc)
                    write_status(state="killed", issue=issue.spec_id, log=str(logfile))
                    return "STOPPED", f"killed on request after {int(now - t0)}s\n{logfile}"
                if now - t0 > timeout_s:
                    kill_tree(proc)
                    write_status(state="timeout", issue=issue.spec_id, log=str(logfile))
                    return "TIMEOUT", f"killed after {int(now - t0)}s\n{logfile}"
                if now - last_beat >= heartbeat_s:
                    mins = int((now - t0) // 60)
                    ev = progress_evidence(logfile, branch_before)
                    pending = _stop_reason()
                    if pending:
                        ev.append(f"stopping after this issue ({pending})")
                    write_status(state="working", issue=issue.spec_id, minutes=mins,
                                 evidence=ev, log=str(logfile), session_pid=proc.pid,
                                 stop_pending=pending or None)
                    notify(f"still working — {issue.spec_id} ({mins}m)",
                           "\n".join(ev) or "no output yet",
                           priority="low", tags="hourglass_flowing_sand")
                    last_beat = now
                time.sleep(5)
        code = proc.returncode
    except Exception as exc:                                   # noqa: BLE001
        return "FAILED", f"{exc.__class__.__name__}: {exc}"

    out = ""
    try:
        out = logfile.read_text(errors="replace")
    except Exception:                                          # noqa: BLE001
        pass
    tail = out[-4000:]

    m = re.search(r"RESULT:\s*(SHIPPED|BLOCKED|REFUSED)\s*(.*)", out)
    verdict = m.group(1) if m else ""
    reason = (m.group(2) or "").strip() if m else ""

    # Trust the ground truth over the self-report: a merged PR is the only proof of SHIPPED.
    merged = merged_prs_since(started)
    if verdict == "SHIPPED" and not merged:
        return "FAILED", f"claimed SHIPPED but no PR merged since {started}\n{tail}"
    if verdict in ("BLOCKED", "REFUSED"):
        return verdict, reason or tail
    if merged:
        return "SHIPPED", "; ".join(merged)
    return "FAILED", f"exit {code}, no verdict and no merged PR\n{tail}"


def show_status() -> int:
    """What is it doing RIGHT NOW — the question the owner had no way to answer.

    Three sources, all already on disk: the lockfile says whether a loop is alive at all, the
    status file says which issue and for how long, and the newest session log's tail is what
    the agent last said. Deliberately a snapshot rather than a follower — a `tail -f` left
    armed is its own problem, and the command to start one is printed instead.
    """
    alive = LOCK.exists()
    pid = ""
    if alive:
        try:
            pid = LOCK.read_text().splitlines()[0]
            os.kill(int(pid), 0)
        except ProcessLookupError:
            alive = False
            print(f"lockfile names pid {pid}, which is GONE — stale lock at {LOCK}\n"
                  f"remove it before starting another loop.")
        except Exception:                                      # noqa: BLE001
            pass
    print(f"loop     {'running, pid ' + pid if alive else 'not running'}")
    if STOPFILE.exists():
        print(f"stop     REQUESTED ({STOPFILE.read_text().strip()}) — will exit at the "
              f"next issue boundary")
    try:
        st = json.loads(STATUS.read_text())
        for k in sorted(st):
            v = st[k]
            v = "\n         ".join(v) if isinstance(v, list) else v
            print(f"{k:<9}{v}")
    except Exception:                                          # noqa: BLE001
        print("status   none written yet")
    logs = sorted(LOGDIR.glob("*.log"), key=lambda p: p.stat().st_mtime) if LOGDIR.exists() else []
    if logs:
        newest = logs[-1]
        age = int(time.time() - newest.stat().st_mtime)
        tail = [ln for ln in newest.read_text(errors="replace").splitlines() if ln.strip()][-8:]
        print(f"\nnewest log ({age}s since last write): {newest}")
        for ln in tail:
            print(f"  {ln[:200]}")
        print(f"\nfollow it with:  tail -f {newest}")
    _, dirty = sh("git", "status", "--porcelain")
    _, branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    print(f"\ntree     {branch}, "
          f"{len(dirty.splitlines()) if dirty else 0} uncommitted file(s)")
    return 0


# ───────────────────────────────────────────────────────────────────── main ──

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-iterations", type=int, default=8)
    ap.add_argument("--issue-timeout", type=int, default=5400, help="seconds per issue")
    ap.add_argument("--max-wall-clock", type=int, default=28800, help="seconds, whole loop")
    ap.add_argument("--max-failures", type=int, default=2, help="per issue, before skipping")
    # Opus by decision 077, at the owner's instruction. The main development line is the one
    # place where model quality converts directly into shipped game work, and an issue worked
    # badly costs more than the model difference — it costs a wrong PR, a review, and a redo.
    ap.add_argument("--model", default="opus")
    ap.add_argument("--heartbeat", type=int, default=600,
                    help="seconds between progress pings while an issue is being worked")
    ap.add_argument("--remote-control", action="store_true",
                    help="add --remote-control to spawned sessions (UNVERIFIED with -p)")
    ap.add_argument("--start-with", default="", help="comma-separated spec ids to try first, "
                                                     "e.g. PLC-07,PLC-04 (still respects depends)")
    ap.add_argument("--dry-run", action="store_true", help="show the queue and exit")
    ap.add_argument("--notify-test", action="store_true",
                    help="send one test push and exit — verify auth BEFORE trusting a night")
    ap.add_argument("--status", action="store_true",
                    help="print what the running loop is doing right now, and exit")
    ap.add_argument("--stop", action="store_true",
                    help="ask a running loop to stop after the current issue, and exit")
    args = ap.parse_args()

    if args.status:
        return show_status()

    if args.stop:
        STOPFILE.parent.mkdir(parents=True, exist_ok=True)
        STOPFILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ\n", time.gmtime()))
        if LOCK.exists():
            print("stop requested. The loop will finish the issue it is on, then exit.\n"
                  "Watch it with:  .venv/bin/python tools/autoloop.py --status")
        else:
            print("stop requested, but no loop is running (no lockfile). The next one to "
                  "start will stop immediately — remove .cache/autoloop-stop to clear it.")
        return 0

    if args.notify_test:
        # Verify the notifier end to end before a night depends on it. Discovering a 401 at
        # 03:00, from silence, is the worst possible time to discover it.
        if not NTFY:
            print("LF_NTFY_URL is not set — nothing to test.")
            return 1
        auth = ("token" if NTFY_TOKEN else "basic" if NTFY_USER else "none")
        tls = ("custom CA" if NTFY_CA else "UNVERIFIED" if NTFY_INSECURE else "system")
        print(f"url  {NTFY}\nauth {auth}\ntls  {tls}\n")
        notify("Latticefall autoloop", "Notification test — if you can read this on your "
               "phone, the loop can reach you.", priority="default", tags="satellite")
        print("\nIf no push arrived, the line above says why. Nothing else was run.")
        return 0

    if not MAP.exists():
        print("docs/issues/.map.json missing — run tools/issues.py first", file=sys.stderr)
        return 2

    issues = open_issues()
    state = load_state()
    prefer = [s.strip() for s in args.start_with.split(',') if s.strip()]

    if args.dry_run:
        print(f"{len(issues)} open issue(s). Queue order:")
        open_ids = {i.spec_id for i in issues}
        for i in sorted(issues, key=lambda i: i.rank)[:15]:
            blocked = sorted(set(i.depends) & open_ids)
            fails = state["failures"].get(i.spec_id, 0)
            flag = f"  BLOCKED by {', '.join(blocked)}" if blocked else ""
            flag += f"  ({fails} prior failure(s))" if fails else ""
            print(f"  {i.milestone:<14} #{i.number:<4} {i.spec_id:<8} {i.title[:56]}{flag}")
        nxt = pick(issues, state, args.max_failures, prefer)
        print(f"\nwould start: {nxt.spec_id} (#{nxt.number})" if nxt else "\nnothing ready")
        return 0

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        print(f"another autoloop holds {LOCK} — refusing to start", file=sys.stderr)
        return 2
    LOCK.write_text(f"{os.getpid()}\n{time.time()}\n")
    # A stopfile left over from a previous run would stop this one before it did anything.
    STOPFILE.unlink(missing_ok=True)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    print(f"[autoloop] pid {os.getpid()} — stop it with "
          f"'.venv/bin/python tools/autoloop.py --stop', watch it with '--status'", flush=True)

    began = time.time()
    shipped: list[str] = []
    consecutive_failures = 0
    try:
        for n in range(1, args.max_iterations + 1):
            reason = _stop_reason()
            if reason:
                write_status(state="stopped", reason=reason, shipped=shipped)
                notify("autoloop stopped on request",
                       f"{reason}\n\nShipped this run: {len(shipped)}\n" + "\n".join(shipped),
                       tags="octagonal_sign")
                return 0
            if time.time() - began > args.max_wall_clock:
                notify("autoloop stopped", f"wall-clock cap reached after {len(shipped)} shipped",
                       tags="hourglass")
                break

            err = preflight()
            if err:
                write_status(state="preflight-failed", reason=err, shipped=shipped)
                notify("autoloop STOPPED — preflight", err, priority="high", tags="warning")
                return 1

            issues = open_issues()
            issue = pick(issues, state, args.max_failures, prefer)
            if issue is None:
                notify("autoloop finished", "Nothing ready to work — the queue is empty or "
                       "everything left is blocked by an open dependency.\n\n"
                       f"Shipped this run: {len(shipped)}\n" + "\n".join(shipped),
                       tags="white_check_mark")
                break

            write_status(state="starting", issue=issue.spec_id, iteration=n,
                         of=args.max_iterations, title=issue.title, shipped=shipped)
            notify(f"autoloop {n}/{args.max_iterations} — starting",
                   f"{issue.spec_id} (#{issue.number}) {issue.title}", tags="hammer")
            verdict, detail = run_one(issue, args.model, args.issue_timeout,
                                      args.heartbeat, args.remote_control)

            if verdict == "STOPPED":
                write_status(state="stopped", reason="killed on request", issue=issue.spec_id,
                             shipped=shipped)
                notify("autoloop killed mid-issue",
                       f"{issue.spec_id} was interrupted — CHECK THE TREE.\n\n{detail}",
                       priority="high", tags="octagonal_sign")
                return 1

            if verdict == "SHIPPED":
                shipped.append(f"{issue.spec_id} — {detail}")
                state["failures"].pop(issue.spec_id, None)
                consecutive_failures = 0
                notify(f"shipped {issue.spec_id}", detail, tags="rocket")
            elif verdict in ("BLOCKED", "REFUSED"):
                state["failures"][issue.spec_id] = args.max_failures  # do not retry
                notify(f"autoloop {verdict} — needs you",
                       f"{issue.spec_id} (#{issue.number})\n\n{detail}",
                       priority="high", tags="raised_hand")
                save_state(state)
                if verdict == "BLOCKED":
                    return 0        # an owner decision halts the run; a refusal moves on
                continue
            else:
                state["failures"][issue.spec_id] = state["failures"].get(issue.spec_id, 0) + 1
                consecutive_failures += 1
                notify(f"autoloop {verdict} — {issue.spec_id}", detail,
                       priority="high", tags="x")
            save_state(state)

            # A dirty tree after an iteration is not recoverable by the next one. Stop.
            dirt = sh("git", "status", "--porcelain")[1]
            if dirt:
                notify("autoloop STOPPED — dirty tree",
                       f"After {issue.spec_id}:\n{dirt[:800]}", priority="urgent", tags="rotating_light")
                return 1
            if consecutive_failures >= 2:
                notify("autoloop STOPPED", "two consecutive failures", priority="high", tags="x")
                return 1

        notify("autoloop done", f"{len(shipped)} shipped\n" + "\n".join(shipped) if shipped
               else "autoloop done — nothing shipped", tags="checkered_flag")
        return 0
    finally:
        LOCK.unlink(missing_ok=True)
        STOPFILE.unlink(missing_ok=True)     # never let one run's stop request stop the next
        if PY.exists():
            sh(str(PY), "tools/reap.py", "--kill", timeout=60)


if __name__ == "__main__":
    raise SystemExit(main())
