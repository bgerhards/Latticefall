#!/usr/bin/env python3
"""
Look at the game, without a window ever touching the owner's desktop.

Every other verification path that needs a frame — `tools/check.py`'s three rendered
checks, `tools/test_parity.py`'s reference run, an ad hoc "does this look right" question —
used to mean a real, focus-stealing Godot window (`docs/STATE.md`, LF-061), because GL
Compatibility reads back nothing under `--headless`. This is the fix: launch the *native
Linux* Godot build through `xvfb-run`, which gives it a real GPU-backed (Mesa llvmpipe
software GL) window on a virtual framebuffer nothing ever presents to a screen. There is no
compositor and no window to occlude, so the 36-minute occlusion stall this project measured
once cannot happen here. `tools/toolpaths.godot_argv(..., want_window=False)` is what wires
the launch up; this file is the everyday front door to it.

    .venv/bin/python tools/shot.py anchor-06 --out /tmp/shot.png
    .venv/bin/python tools/shot.py anchor-24 --out /tmp/shot.png --ui-scale 2.0 \\
        --difficulty brutal --a11y /tmp/shot.json
    .venv/bin/python tools/shot.py anchor-01 --out /tmp/shot.png --no-autoplay \\
        --extra --paused

`--extra` must be the LAST flag on this tool's own command line. It is declared with
`argparse.REMAINDER`, so every token after it — including ones that look like `shot.py`'s
own flags, e.g. `--ui-scale` — is taken verbatim as a raw flag for the game's own CLI
(`scripts/main.gd`'s `_setup_cli()`) rather than parsed by this tool. Put `--ui-scale`,
`--a11y`, `--difficulty`, etc. before `--extra`, never after.

Exits non-zero (and prints why) if Godot never reached the shot, if it reported a nonzero
PNG write error, or if the frame is effectively blank (coverage below 0.02) — a blank frame
is a failed look at the game, not a successful one that happened to show nothing. Bounds the
subprocess with a timeout and reaps whatever a timeout leaves behind, because the direct
child (`xvfb-run`, when invisible capture is in play) surviving a `.kill()` is not the same
process as the Godot grandchild it wraps — exactly the reparenting problem `tools/reap.py`
exists for.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import lease        # noqa: E402  — scopes this capture so tools/reap.py never kills it out
                    # from under a sibling agent's session (PRC-07)
import reap        # noqa: E402  — same tool suite; reused directly rather than shelled out to
import toolpaths    # noqa: E402

DEFAULT_TIMEOUT = 300.0
TIMED_OUT = 124                       # conventional shell exit code for a timeout

# A blank frame reports coverage near 0.00-0.03 (measured for `check_game_renders`); this
# tool's job is to hand back a frame worth looking at, not merely a PNG, so it uses the same
# kind of floor rather than trusting a zero exit code alone.
MIN_COVERAGE = 0.02

# Lines Godot's `--shot`/`--a11y` path prints that are worth relaying — see `main.gd`'s
# `_process()` and `menu.gd`. `PARITY_JSON` and other machinery are deliberately not here;
# this tool is for looking at a frame, not for parity data.
## Every marker `scripts/main.gd` prints for a verification hook has to appear here or the
## hook is silently useless — the flag reaches Godot, Godot prints, and this filter drops it.
## That happened to `LANE` the day it was added: `--lanes` ran, produced nothing visible, and
## looked like a broken hook rather than a missing prefix. Add the prefix in the same change
## that adds the hook.
RELAY_PREFIXES = ("SHOT ", "MENUSHOT ", "DRAFTSHOT ", "FRAME ", "STATE ", "AUDIO ", "FACE ",
                  "LANE ", "MENUFRAME ", "CLEARED ", "CAMERA ", "DRAG ", "WHEEL ",
                  "DIALOG-TRIGGER ", "DEBRIEF-PRESS ", "DEBRIEF-PRESSED ", "DRAFT-BOOT ",
                  "AUTO-TAKE ", "RECOVERY-TAKEN ", "PLACEHOLDER ")

# LF-153: the three screens that can end a run with a saved PNG, each printing its own
# "<X> <path> err=<n> <w>x<h>" confirmation line — `main.gd`'s `SHOT`, `menu.gd`'s
# `MENUSHOT`, `draft.gd`'s `DRAFTSHOT`. `run_shot()` used to look only for `SHOT `, so a
# capture that reached the menu or the draft screen (structurally unreachable through this
# tool today per LF-109, but reachable by anything driving Godot with the same argv shape
# this module builds, and by a future caller) reported failure despite the PNG having been
# written correctly. Each screen's own per-frame stats line differs too — `main.gd`'s
# `FRAME coverage=.. distinct=..`, `menu.gd`'s `MENUFRAME coverage=.. buttons=..`,
# `draft.gd` prints no stats line at all — so the blank-frame floor below is only enforced
# where a coverage figure actually exists, the same "lower floor" precedent
# `tools/check.py`'s `check_menu_renders` already sets for the menu case.
CAPTURE_PREFIXES = ("SHOT ", "MENUSHOT ", "DRAFTSHOT ")
STATS_PREFIX_FOR = {"SHOT ": "FRAME ", "MENUSHOT ": "MENUFRAME ", "DRAFTSHOT ": None}


def _out(line: str) -> None:
    print(line, flush=True)


def _err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def build_extra_args(args: argparse.Namespace) -> list[str]:
    """Everything after `--` on Godot's own command line, in the order `main.gd`'s
    `_setup_cli()` expects it (see `scripts/main.gd`). `args.extra` (captured by
    `argparse.REMAINDER`, so it is always the tail of this tool's own argv) is appended
    before `--shot` so any positional value it carries (e.g. `--select 1`) cannot be
    mistaken for the frame count that must immediately follow `--shot <path>`."""
    extra: list[str] = []
    if args.autoplay:
        extra.append("--autoplay")
    extra += ["--anchor", args.anchor]
    if args.difficulty:
        extra += ["--difficulty", args.difficulty]
    if args.ui_scale is not None:
        extra += ["--ui-scale", str(args.ui_scale)]
    if args.facings:
        extra.append("--facings")
    if args.a11y:
        extra += ["--a11y", str(args.a11y)]
    extra += args.extra
    extra += ["--shot", str(args.out), str(args.frames)]
    return extra


def run_shot(args: argparse.Namespace) -> int:
    extra = build_extra_args(args)
    try:
        argv = toolpaths.godot_argv(ROOT, ["--fixed-fps", "60", "--", *extra],
                                    want_window=False)
    except RuntimeError as exc:
        _err(f"shot: {exc}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.a11y:
        args.a11y.parent.mkdir(parents=True, exist_ok=True)

    # Leased and bounded: a Godot capture under Xvfb is Mesa llvmpipe software GL, and
    # measured at ~8 of this machine's 16 cores per capture (LF-116) — three concurrent
    # captures drove load average to 18.6 and turned this into minutes. `acquire_capture()`
    # both scopes this process tree for tools/reap.py (so a sibling agent's `--kill` spares
    # it) and waits for one of a small number of concurrent capture slots before Godot ever
    # launches. See tools/lease.py.
    try:
        with lease.acquire_capture("shot", argv, ttl_s=args.timeout + 60.0):
            try:
                r = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT),
                                   timeout=args.timeout)
            except subprocess.TimeoutExpired as exc:
                # subprocess.run(timeout=...) kills only the direct child. When capture is
                # invisible that child is `xvfb-run`, not Godot — the wrapper is a shell
                # script whose cleanup trap does not run under a hard kill, so both `Xvfb`
                # and Godot can reparent to init and survive. tools/reap.py already knows
                # how to find and kill exactly that.
                procs = reap.find()
                killed = reap._kill(procs, quiet=False) if procs else 0
                _err(f"shot: timed out after {args.timeout:.0f}s; reaped {killed} of "
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
        # No capture slot freed in time — tools/lease.py's MAX_CONCURRENT_CAPTURES was
        # saturated for the whole wait. Distinct from the Godot-side timeout above: this
        # means Godot never even got launched.
        _err(f"shot: {exc}")
        return TIMED_OUT

    blob = r.stdout + r.stderr
    relayed = [line for line in blob.splitlines() if line.startswith(RELAY_PREFIXES)]
    for line in relayed:
        _out(line)

    # LF-153: recognise a capture success under whichever of the three markers actually
    # fired, not only "SHOT " — see CAPTURE_PREFIXES' own doc.
    capture_prefix = next((pfx for pfx in CAPTURE_PREFIXES
                            if any(l.startswith(pfx) for l in relayed)), None)
    if capture_prefix is None:
        _err("shot: Godot never reached a capture — no SHOT/MENUSHOT/DRAFTSHOT line in "
             "its output")
        _err(blob.strip()[-1500:])
        return 1
    shot_line = next(l for l in relayed if l.startswith(capture_prefix))

    try:
        err_code = int(shot_line.split("err=")[1].split()[0])
    except (IndexError, ValueError):
        _err(f"shot: could not parse the {capture_prefix.strip()} line: {shot_line!r}")
        return 1
    if err_code != 0:
        _err(f"shot: Godot reported a PNG write error (err={err_code})")
        return 1

    stats_prefix = STATS_PREFIX_FOR[capture_prefix]
    frame_line = next((l for l in relayed if stats_prefix and l.startswith(stats_prefix)), "")
    if frame_line:
        try:
            coverage = float(frame_line.split("coverage=")[1].split()[0])
        except (IndexError, ValueError):
            _err(f"shot: could not parse the {stats_prefix.strip()} line: {frame_line!r}")
            return 1
        if coverage < MIN_COVERAGE:
            _err(f"shot: frame is effectively blank (coverage={coverage:.4f}, "
                 f"min {MIN_COVERAGE}) — that is a failed look at the game, not a "
                 f"successful one")
            return 1
    # `--extra --shot-menu ...` still does NOT reach the menu through this tool — that part
    # of LF-109 is unchanged and still open, verified not assumed: `build_extra_args()`
    # unconditionally forwards `--anchor <id>` and `--shot <path> <frames>`, and `menu.gd`'s
    # `_boot_from_cli()` checks `argv.has("--anchor") or argv.has("--shot")` *before* it
    # ever looks at `--shot-menu`, so a forwarded `--shot-menu` is silently dropped and the
    # run screenshots the game as usual (a `SHOT`/`FRAME` pair, never `MENUSHOT`/
    # `MENUFRAME`). What LF-153 fixes is narrower and orthogonal: the recognition logic
    # above no longer *assumes* "SHOT " is the only marker a successful capture can print,
    # so the day something other than this tool drives Godot into the menu or draft screen
    # with this same argv shape (`tools/scenario.py`, or a future `--menu` mode here),
    # `MENUSHOT`/`DRAFTSHOT` are already recognised rather than silently read as failure.
    # `check_menu_renders` in tools/check.py reaches the real menu shot today by calling
    # `toolpaths.godot_argv()` directly with only `--shot-menu` on the line — never through
    # this tool.

    if r.returncode not in (0, None):
        _err(f"shot: godot exited {r.returncode}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="tools/shot.py",
        description="Render a frame of Latticefall to a PNG with no visible window — the "
                     "supported way to look at the game from this session. Launches the "
                     "native Linux Godot build under an Xvfb virtual framebuffer via "
                     "tools/toolpaths.godot_argv(); see that module for why the window "
                     "never reaches the owner's desktop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1] if "\n\n" in __doc__ else "",
    )
    ap.add_argument("anchor", help="anchor id to load, e.g. anchor-06")
    ap.add_argument("--out", required=True, type=Path,
                    help="where to write the PNG (parent dirs are created)")
    ap.add_argument("--frames", type=int, default=300,
                    help="frame number to capture on, i.e. how long to let the sim run "
                         "before the shot is taken (default: 300)")
    ap.add_argument("--difficulty", default=None,
                    help="standard/hard/brutal (default: whatever the game defaults to)")
    ap.add_argument("--autoplay", dest="autoplay", action="store_true", default=True,
                    help="auto-build a policy so the board is not empty (default: on)")
    ap.add_argument("--no-autoplay", dest="autoplay", action="store_false",
                    help="leave the board exactly as the anchor starts it")
    ap.add_argument("--a11y", type=Path, default=None,
                    help="also write a text-inventory report to this path, for the SAME "
                         "frame the PNG captures (tools/validate/a11y.py samples the PNG "
                         "for background colour, so the two must come from one run)")
    ap.add_argument("--facings", action="store_true",
                    help="print a FACE line per drawable — sprite, chosen yaw, board "
                         "position — for the captured frame (see decision 049)")
    ap.add_argument("--ui-scale", type=float, default=None,
                    help="force an interface scale (e.g. 2.0 for 200%%) without touching "
                         "the player's saved progress")
    ap.add_argument("--extra", nargs=argparse.REMAINDER, default=[],
                    help="additional raw flags forwarded to the game's own CLI, e.g. "
                         "--extra --select 1 --pick pulse-turret. MUST COME LAST: it is "
                         "argparse.REMAINDER, so it swallows every token after it verbatim, "
                         "even ones shaped like this tool's own flags (--ui-scale, --a11y, "
                         "...) — put those before --extra, not after. See "
                         "scripts/main.gd's _setup_cli() for the full forwarded-flag list "
                         "(--paused, --select, --pick, --scroll, --cursor, --build, "
                         "--speed, --ability, --ability-at, --press-at, --chain, ...)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"seconds to wait before killing Godot and reaping stragglers "
                         f"(default: {DEFAULT_TIMEOUT:.0f})")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass  # stdout/stderr already unbuffered (e.g. under `python -u`)

    return run_shot(args)


if __name__ == "__main__":
    raise SystemExit(main())
