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
never the thing that changes it. Note that this still cannot distinguish "leaked" from
"legitimately mid-capture right now": exactly like the Godot rule above it, a matching
process is reported (and, with `--kill`, killed) purely by command-line shape — so running
`--kill` while another agent or process has a capture genuinely in flight will end that
capture too. That is a pre-existing property of this scoping, not new to `Xvfb`.

    .venv/bin/python tools/reap.py             # what is running that should not be
    .venv/bin/python tools/reap.py --kill      # SIGTERM, then SIGKILL what ignores it
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

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


def find(exclude_self: bool = True) -> list[dict]:
    me = os.getpid()
    mine = {me, os.getppid()}
    found = []
    for p in _ps():
        if exclude_self and p["pid"] in mine:
            continue
        kind = _classify(p["cmd"])
        if kind is None:
            continue
        p["kind"] = kind
        # A process whose parent is gone (reparented to launchd/init) is the specific
        # failure this file is about, so it is worth calling out in the report.
        p["orphan"] = p["ppid"] == 1
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kill", action="store_true",
                    help="SIGTERM then SIGKILL every stray found")
    ap.add_argument("--quiet", action="store_true",
                    help="print only when something was found (for hook use)")
    args = ap.parse_args()

    procs = find()
    if not procs:
        if not args.quiet:
            print("reap: clean — no stray Latticefall processes")
        return 0

    label = "killing" if args.kill else "found (use --kill)"
    print(f"reap: {len(procs)} stray process(es), {label}:")
    for p in procs:
        flag = " ORPHAN" if p["orphan"] else ""
        print(f"  pid {p['pid']:>6}  up {p['etime']:>9}  cpu {p['pcpu']:>5.1f}%"
              f"{flag}  {p['kind']}")
        print(f"         {p['cmd'][:150]}")

    if not args.kill:
        return 1

    n = _kill(procs, args.quiet)
    leftover = _alive(procs)
    print(f"reap: killed {n}")
    if leftover:
        print(f"reap: STILL RUNNING {leftover} — kill by hand", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
