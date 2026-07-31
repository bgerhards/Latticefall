#!/usr/bin/env python3
"""Find and kill processes this repo's tooling leaves behind.

This file exists because a Latticefall verification run does not reliably die with the
thing that started it, and a survivor is not free. Four of them are known:

  * `check.py`'s rules-parity check spawns a headless Godot that **survives
    `pkill -f check.py`** — it reparents to init and holds a core at 100% for as long as
    the machine is up. CLAUDE.md has recorded this trap for several sessions; recording it
    did not stop it happening, because it relies on a human remembering to run `ps`.
  * `tools/audio/serve.py` is `httpd.serve_forever()`. It has no exit condition at all.
  * `sim/run.py --jobs N` and `tools/sweep.py --jobs N` fan out to one worker per core, and
    a killed parent leaves the pool orphaned.
  * `Xvfb`, the virtual framebuffer `tools/toolpaths.xvfb_prefix()` spins up so Godot can be
    captured with no window on the owner's desktop (decision 052), can outlive the
    `xvfb-run` wrapper that started it. `xvfb-run`'s own cleanup is a shell trap, and it does
    not reliably fire under this project's own concurrency — measured directly on this
    machine under several simultaneous invisible captures: three `Xvfb` servers were still
    running, each still spinning a CPU, after the Godot process each of them existed to
    serve had already exited cleanly. Not a timeout case; the wrapped command finished, the
    wrapper just never tore down what it started.

A survivor costs real money, not just a hot fan: a background process the agent harness is
still tracking re-invokes the model when it finally exits or emits, so a forgotten loop
bills tokens against a session that everyone believed was over.

Scoped deliberately narrowly — it will not touch a process it cannot prove belongs to this
repository:

  * Godot only when the command line carries `--headless` or `--fixed-fps`. A Godot *editor*
    the owner opened by hand matches neither, so it is never a candidate.
  * Python only when the command line names this repo's path *and* one of its own tools.
  * Blender only with `-b` (background) and this repo's path. `blender-mcp`, the editor
    bridge, is excluded by name — it is harness-managed and killing it breaks the session.
  * `Xvfb`/`xvfb-run` only with the exact screen spec `tools/toolpaths.xvfb_prefix()` uses
    (`-screen 0 1600x900x24`). `Xvfb`'s own command line never carries this repo's path —
    there is nothing else to scope it by — so the screen spec is what stops this from
    matching an unrelated `Xvfb` instance on a machine that runs more than one.

Reports by default and kills nothing. `--kill` is the verb, so that reading the situation is
never the thing that changes it.

**Command-line shape only says "ours"; it cannot say "not in use right now" (PRC-07).**
That used to mean `--kill` could not distinguish a leaked process from one genuinely
mid-capture for another agent — measured live: seven agents sharing this tree produced
6-8 processes belonging to OTHER agents mid-capture, every one of which `--kill` would
have ended. `tools/lease.py` is the fix: every launch site wraps its subprocess tree in a
lease (`.cache/leases/<pid>-<rand>.json`, one JSON file per launch — see that module's
docstring for the exact schema and why filenames are not simply `<pid>.json`). A stray
process found here is classified into exactly one of:

  * **orphan** — `ppid == 1`. Killed by `--kill` from any session, always: an orphan by
    definition has no session left to bill, and CLAUDE.md is explicit that in doubt
    between killing an orphan and sparing it, kill it.
  * **expired** — a lease was found (by walking the process's ppid chain up to the first
    pid that holds one; see `lease.find_owner()`) but its `expires_at` has passed. Killed
    by `--kill` from any session. This is what keeps a hard-killed CLI session (LF-062:
    `SessionEnd` does not fire then) from leaving a permanent "sibling" ghost that would
    make `--kill` useless forever after — the TTL is the backstop clean exit cannot be.
  * **own-session** — a live lease was found and its `session_id` matches this process's
    own (`lease.session_id()`). Killed by `--kill`.
  * **sibling** — a live lease was found belonging to a *different* session. **Never**
    killed by plain `--kill` — only `--all` reaches these, and it prints a warning naming
    how many it is about to end. Always printed in the report regardless, labelled "not
    killed — belongs to another session", so the information is never silently lost.
  * **unleased** — no lease anywhere in the ancestor chain, and the process has been
    running longer than `UNLEASED_GRACE_S`. This is exactly the pre-lease-era survivor
    this file was written for (a tool that forgot to wrap itself, or predates the lease
    system) — kept killable by `--kill` from any session, same direction CLAUDE.md
    prescribes for an orphan, but reported as "unleased" so a tool that forgot to acquire
    one stays visible rather than looking like every other kill. A process younger than
    the grace floor is not reported at all yet: `acquire()` writes its file before the
    subprocess it covers is even spawned, so a genuinely leased process is essentially
    never seen in this state — only a real gap would be, and the floor exists so a lease
    mid-write is never mistaken for one that was never coming.

    .venv/bin/python tools/reap.py               # what is running that should not be
    .venv/bin/python tools/reap.py --kill        # SIGTERM, then SIGKILL — spares siblings
    .venv/bin/python tools/reap.py --kill --all  # the nuclear option — ends siblings too
    .venv/bin/python tools/reap.py --json        # machine-readable, for PRC-06/PRC-15
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lease  # noqa: E402 — same tool suite; this is the module that owns the lease format

REPO = Path(__file__).resolve().parents[1]

## Must match `tools/toolpaths._XVFB_SCREEN_ARGS` joined — this is the one identifying mark
## an `Xvfb`/`xvfb-run` process carries, since its command line never has this repo's path
## on it. Duplicated as a literal rather than imported so this file stays dependency-free
## (it already runs with nothing but the stdlib); keep the two in sync by hand.
XVFB_SCREEN_SPEC = "-screen 0 1600x900x24"

## Tool names that are ours, for the python matcher. A bare `.venv/bin/python` running an
## interactive shell is not a stray; `.venv/bin/python tools/sweep.py` is.
OUR_TOOLS = (
    "tools/check.py",
    "tools/sweep.py",
    "tools/density.py",
    "tools/densify.py",
    "tools/session.py",
    "tools/reap.py",
    "tools/audio/serve.py",
    "tools/audio/synth_sfx.py",
    "tools/audio/ingest_music.py",
    "tools/blender/",
    "tools/validate/",
    "sim/run.py",
    "sim.run",
)

## Never a candidate, whatever else matches. The MCP bridges are started and owned by the
## agent harness, and killing one silently removes a capability mid-session.
NEVER = ("blender-mcp", "godot-ai", "reap.py --kill")

GRACE_SECONDS = 3.0

## How long a process with NO lease anywhere in its ancestor chain has to exist before it
## is reported as "unleased" and made killable. Generous on purpose: `lease.acquire()`
## writes its file before the subprocess it covers is even spawned, so a correctly leased
## process should never be seen here at all — this floor exists only to avoid a false
## positive on a lease file that is mid-write at the exact moment `ps` was snapshotted, not
## to give a forgetful tool room to breathe.
UNLEASED_GRACE_S = 20.0


def _ps() -> list[dict]:
    """Every process, as dicts. `ps` rather than psutil: stdlib only (CLAUDE.md)."""
    out = subprocess.run(
        ["ps", "-ax", "-o", "pid=,ppid=,etime=,pcpu=,command="],
        capture_output=True, text=True, check=False,
    ).stdout
    rows: list[dict] = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(.*)$", line)
        if not m:
            continue
        rows.append({
            "pid": int(m.group(1)),
            "ppid": int(m.group(2)),
            "etime": m.group(3),
            "pcpu": float(m.group(4)),
            "cmd": m.group(5),
        })
    return rows


def _classify(cmd: str) -> str | None:
    """What kind of stray this is, or None if it is not one of ours.

    Order matters only for the report label; the matchers are disjoint in practice.
    """
    if any(n in cmd for n in NEVER):
        return None

    low = cmd.lower()
    repo = str(REPO)

    # Godot: only ever a verification run. An editor session carries neither flag.
    if "godot" in low and ("--headless" in cmd or "--fixed-fps" in cmd):
        return "godot (verification run)"

    # Xvfb / xvfb-run: the virtual framebuffer for invisible Godot capture (decision 052).
    # Scoped on the screen spec, not the repo path — see module docstring for why, and for
    # why this is measured to leak (`xvfb-run`'s cleanup trap does not reliably fire here)
    # rather than a theoretical concern.
    if "xvfb" in low and XVFB_SCREEN_SPEC in cmd:
        return "xvfb (virtual framebuffer, invisible Godot capture)"

    # Blender: background renders only, and only for this repo.
    if "blender" in low and re.search(r"(^|\s)-b(\s|$)", cmd) and repo in cmd:
        return "blender (background render)"

    # Our python tooling. Requires both the repo path and one of our own entry points, so a
    # stray `python` belonging to something else on the machine is never a candidate.
    if "python" in low and repo in cmd and any(t in cmd for t in OUR_TOOLS):
        if "serve.py" in cmd:
            return "serve.py (never exits on its own)"
        return "python tooling"

    return None


def _etime_seconds(etime: str) -> float:
    """Parse `ps`'s `etime` column (`[[DD-]HH:]MM:SS`) to seconds. Best-effort: an
    unparseable value returns a large number rather than zero — the safe direction here is
    toward treating an unreadable age as "old enough to look at", not toward hiding a
    possible survivor behind a parse failure."""
    try:
        rest, days = etime, 0
        if "-" in rest:
            d, rest = rest.split("-", 1)
            days = int(d)
        parts = [int(x) for x in rest.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts[-3], parts[-2], parts[-1]
        return days * 86400 + h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return 1e9


def find(exclude_self: bool = True) -> list[dict]:
    """Every stray process, classified by command-line shape (`_classify()`) and then
    scoped by lease into orphan / expired / own-session / sibling / unleased — see the
    module docstring for what each means and why `--kill` treats them differently.
    """
    me = os.getpid()
    mine = {me, os.getppid()}
    all_rows = _ps()
    ppid_of = {p["pid"]: p["ppid"] for p in all_rows}

    lease.gc_stale()
    by_pid = lease.leases_by_pid()
    my_session = lease.session_id()
    now = time.time()

    found = []
    for p in all_rows:
        if exclude_self and p["pid"] in mine:
            continue
        kind = _classify(p["cmd"])
        if kind is None:
            continue
        p["kind"] = kind
        # A process whose parent is gone (reparented to launchd/init) is the specific
        # failure this file is about, so it is worth calling out in the report.
        p["orphan"] = p["ppid"] == 1

        owner = lease.find_owner(p["pid"], ppid_of, by_pid)
        if p["orphan"]:
            p["status"] = "orphan"
        elif owner is None:
            if _etime_seconds(p["etime"]) < UNLEASED_GRACE_S:
                # Too new to judge either way — see UNLEASED_GRACE_S and the module
                # docstring's "unleased" entry. Not reported this run; a still-alive,
                # still-unleased process will clear the floor and be seen next time.
                continue
            p["status"] = "unleased"
        elif owner["expires_at"] < now:
            p["status"] = "expired"
        elif owner["session_id"] == my_session:
            p["status"] = "own-session"
        else:
            p["status"] = "sibling"
        p["lease"] = owner
        found.append(p)
    return found


def _kill(procs: list[dict], quiet: bool) -> int:
    """SIGTERM, wait out the grace period, SIGKILL the remainder. Returns count killed."""
    for p in procs:
        try:
            os.kill(p["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            if not quiet:
                print(f"  ! {p['pid']} not ours to signal", file=sys.stderr)

    deadline = time.monotonic() + GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _alive(procs):
            break
        time.sleep(0.2)

    stubborn = _alive(procs)
    for pid in stubborn:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if stubborn:
        time.sleep(0.3)

    return len(procs) - len(_alive(procs))


def _alive(procs: list[dict]) -> list[int]:
    live = []
    for p in procs:
        try:
            os.kill(p["pid"], 0)
            live.append(p["pid"])
        except (ProcessLookupError, PermissionError):
            continue
    return live


def _to_json_record(p: dict) -> dict:
    ld = p.get("lease")
    return {
        "pid": p["pid"], "ppid": p["ppid"], "etime": p["etime"], "pcpu": p["pcpu"],
        "cmd": p["cmd"], "kind": p["kind"], "orphan": p["orphan"], "status": p["status"],
        "lease": ({"session_id": ld["session_id"], "agent": ld["agent"], "tool": ld["tool"],
                  "expires_at": ld["expires_at"]} if ld else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kill", action="store_true",
                    help="SIGTERM then SIGKILL every KILLABLE stray found — orphan, "
                         "expired, own-session and unleased. Never a sibling; see --all.")
    ap.add_argument("--all", action="store_true",
                    help="the deliberate nuclear option: with --kill, also end sibling "
                         "processes (live leases belonging to another session). Prints a "
                         "warning naming how many. No effect without --kill.")
    ap.add_argument("--quiet", action="store_true",
                    help="print only when something was found (for hook use)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable report to stdout instead of the human table — "
                         "for PRC-06's SubagentStop hook and PRC-15's ownership tooling")
    args = ap.parse_args()

    procs = find()
    siblings = [p for p in procs if p["status"] == "sibling"]
    killable = [p for p in procs if p["status"] != "sibling" or args.all]

    killed_pids: list[int] = []
    leftover: list[int] = []
    if args.kill and procs:
        _kill(killable, args.quiet)
        leftover = _alive(killable)
        killed_pids = [p["pid"] for p in killable if p["pid"] not in leftover]

    if args.json:
        doc = {
            "schema": "latticefall-reap", "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "session_id": lease.session_id(),
            "found": [_to_json_record(p) for p in procs],
            "killed": killed_pids if args.kill else None,
            "leftover": leftover if args.kill else None,
            "spared_siblings": [] if args.all else [p["pid"] for p in siblings],
        }
        print(json.dumps(doc, indent=2))
        if args.kill:
            return 1 if leftover else 0
        return 1 if procs else 0

    if not procs:
        if not args.quiet:
            print("reap: clean — no stray Latticefall processes")
        return 0

    label = "killing" if args.kill else "found (use --kill)"
    print(f"reap: {len(procs)} stray process(es), {label}:")
    for p in procs:
        flag = " ORPHAN" if p["orphan"] else ""
        who = f"  session={p['lease']['session_id']}" if p.get("lease") else ""
        print(f"  pid {p['pid']:>6}  up {p['etime']:>9}  cpu {p['pcpu']:>5.1f}%"
              f"{flag}  [{p['status'].upper()}]{who}  {p['kind']}")
        print(f"         {p['cmd'][:150]}")
        if p["status"] == "sibling" and not args.all:
            print(f"         not killed — belongs to another session "
                  f"({p['lease']['session_id']})")

    if not args.kill:
        return 1

    if siblings:
        if args.all:
            print(f"reap: --all — also ending {len(siblings)} sibling process(es) "
                  f"belonging to other sessions", file=sys.stderr)
        else:
            print(f"reap: spared {len(siblings)} sibling process(es) — use "
                  f"--kill --all to include them")

    print(f"reap: killed {len(killed_pids)}")
    if leftover:
        print(f"reap: STILL RUNNING {leftover} — kill by hand", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
