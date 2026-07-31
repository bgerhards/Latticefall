#!/usr/bin/env python3
"""
Run one `data/scenarios/*.json` file through Godot and report pass/fail.

PRC-12: a scenario file describes a sequence of actions and the assertions to check, so a
verification is a data file rather than a new `main.gd` CLI flag. This is the everyday front
door to it — the same shape as `tools/shot.py` (LF-153's own fix for `shot.py` generalises
the "did the capture succeed" question this tool never had, since a scenario always ends by
printing its own `SCENARIO {json}` summary rather than relying on a `SHOT`/`MENUFRAME`/
`DRAFTSHOT` marker at all).

    .venv/bin/python tools/scenario.py data/scenarios/smoke.json
    .venv/bin/python tools/scenario.py data/scenarios/abilities.json --timeout 120

Exit code is the scenario's own: 0 if every assertion passed, 1 on the first assertion
that failed (`ASSERT FAIL` is printed, naming the frame, the expression, what was seen and
what was wanted), 124 on a timeout, 1 if Godot never reached a `SCENARIO` line at all (a
crash, a hang, a load-time schema error — `SCENARIO-LOAD-FAIL` is relayed and printed
either way).

Reuses `tools/toolpaths.godot_argv()` (the invisible-capture launch PRC-09/decision 052
built) and `tools/lease.py` (PRC-07's capture-slot bound — at most two concurrent captures
machine-wide, LF-116) exactly as `tools/shot.py` does, rather than assembling a second Godot
command line by hand.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import lease        # noqa: E402
import reap         # noqa: E402
import toolpaths    # noqa: E402

DEFAULT_TIMEOUT = 300.0
TIMED_OUT = 124

# Every marker scripts/main.gd's scenario path can print, relayed for the caller — the same
# "add the prefix in the same change as the hook" discipline tools/shot.py's own
# RELAY_PREFIXES doc names (LANE was silently dropped once for lacking exactly this).
RELAY_PREFIXES = (
    "SCENARIO ", "SCENARIO-LOAD-FAIL ", "ASSERT FAIL ",
    "SHOT ", "FRAME ", "STATE ", "BUS ", "AUDIO ", "CAMERA ", "FACE ", "LANE ",
    "BUILD ", "BUILD-GRANT ", "PRESS-AT ", "ABILITY-AT ", "ABILITY-FIRED ", "ABILITY-POST ",
    "ABILITY-LIVE ", "SURGE-BEFORE ", "SURGE-AFTER ", "TARGET-BEFORE ", "TARGET-AFTER ",
    "CALL-WAVE-BEFORE ", "CALL-WAVE-AFTER ", "VET ", "PROFILE ", "DRAWABLES ",
    "CLI-WARN ", "DIALOG-TRIGGER ",
)


def _out(line: str) -> None:
    print(line, flush=True)


def _err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def run_scenario(args: argparse.Namespace) -> int:
    extra = ["--scenario", str(args.path), *args.extra]
    try:
        argv = toolpaths.godot_argv(ROOT, ["--fixed-fps", "60", "--", *extra],
                                    want_window=False)
    except RuntimeError as exc:
        _err(f"scenario: {exc}")
        return 1

    try:
        with lease.acquire_capture("scenario", argv, ttl_s=args.timeout + 60.0):
            try:
                r = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT),
                                   timeout=args.timeout)
            except subprocess.TimeoutExpired as exc:
                # Same reparenting problem tools/shot.py's own doc names: the direct child
                # under Xvfb is xvfb-run, not Godot, so a plain kill() on timeout leaves both
                # processes behind. tools/reap.py already knows how to find and kill them.
                procs = reap.find()
                killed = reap._kill(procs, quiet=False) if procs else 0
                _err(f"scenario: timed out after {args.timeout:.0f}s; reaped {killed} of "
                     f"{len(procs)} stray process(es)")
                for p in procs:
                    _err(f"  pid {p['pid']}  {p['kind']}  {p['cmd'][:120]}")
                out = exc.stdout or ""
                for line in (out.decode(errors="replace") if isinstance(out, bytes)
                             else out).splitlines():
                    if line.startswith(RELAY_PREFIXES):
                        _out(line)
                return TIMED_OUT
    except TimeoutError as exc:
        _err(f"scenario: {exc}")
        return TIMED_OUT

    blob = r.stdout + r.stderr
    relayed = [line for line in blob.splitlines() if line.startswith(RELAY_PREFIXES)]
    for line in relayed:
        _out(line)

    load_fail = next((l for l in relayed if l.startswith("SCENARIO-LOAD-FAIL ")), "")
    if load_fail:
        _err(f"scenario: {load_fail}")
        return 1

    assert_fail = next((l for l in relayed if l.startswith("ASSERT FAIL ")), "")

    summary_line = next((l for l in relayed if l.startswith("SCENARIO ")), "")
    if not summary_line:
        _err("scenario: Godot never printed a SCENARIO summary — it crashed, hung, or "
             "quit before reaching one")
        _err(blob.strip()[-1500:])
        return 1

    try:
        import json
        summary = json.loads(summary_line[len("SCENARIO "):])
    except (ValueError, json.JSONDecodeError):
        _err(f"scenario: could not parse the SCENARIO line: {summary_line!r}")
        return 1

    if not summary.get("pass", False):
        if assert_fail:
            _err(f"scenario: {assert_fail}")
        _err(f"scenario: {args.path} failed "
             f"({sum(1 for a in summary.get('assertions', []) if not a.get('pass'))} of "
             f"{len(summary.get('assertions', []))} assertions failed)")
        return 1

    if r.returncode not in (0, None):
        _err(f"scenario: godot exited {r.returncode}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="tools/scenario.py",
        description="Run one Latticefall scenario file through Godot and report pass/fail. "
                    "See data/schema/scenario.schema.json for the file shape.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1] if "\n\n" in __doc__ else "",
    )
    ap.add_argument("path", type=Path, help="scenario JSON file, e.g. data/scenarios/smoke.json")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"seconds to wait before killing Godot and reaping stragglers "
                         f"(default: {DEFAULT_TIMEOUT:.0f})")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[],
                    help="additional raw flags forwarded to the game's own CLI, after "
                         "--scenario <path>. MUST COME LAST (argparse.REMAINDER) — same "
                         "contract as tools/shot.py's own --extra (PRC-09).")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    return run_scenario(args)


if __name__ == "__main__":
    raise SystemExit(main())
