#!/usr/bin/env python3
"""
Save/load round trip, and the recovery draft, in one tool (PRC-18).

Two coverage holes closed together because they are the same shape: `progress.gd` (save/
load) had zero automated references, and `draft.gd`/`recoveries.gd` (the between-anchor
recovery draft) had zero automated references despite being flagged fragile twice already
(LF-065, LF-070 — accessors nothing wired to the rules for several sessions, found only by a
human reading code). `tools/scenario.py`'s scenario system cannot reach either: its timeline
drives `AnchorView` inside `main.tscn` (build/select/press/... — see
`data/schema/scenario.schema.json`), and the recovery draft is a different scene
(`scenes/draft.tscn`) with its own CLI flags (`--draft`, `--seed`, `--auto-take`,
`--focus-card`) that predate PRC-12 and were never folded in. A save/load round trip is a
question about a SEPARATE Godot process reading back what an earlier one wrote to disk — the
scenario harness (and `--scenario` generally) is one live process end to end, so it cannot
ask that question at all, no matter what timeline it is given.

Mechanism: `draft.gd` already does everything this needs, unmodified —

    -- --draft --seed <n> --auto-take --shot <path>

boots the recovery draft fresh, computes `Recoveries.offer(seed)` (deterministic — draft_size
distinct pool ids the player does not already own), auto-presses the first card's TAKE button
(`Recoveries.grant(id)` -> `Progress.grant_recovery(id)` -> `Progress.save_state()`), and
because `--shot` is present, prints `DRAFT-BOOT`/`AUTO-TAKE`/`RECOVERY-TAKEN`/`DRAFT`/
`PROGRESS` before quitting — see that file's `_ready()`/`_do_auto_take()`/`_process()`.

The round trip is TWO separate OS processes launched back to back against the SAME isolated
save location: the first takes a recovery and quits; the second boots from nothing but the
file the first one left behind and reports what it loaded. No state is shared between them
except that file on disk — that IS a save/load round trip, not a simulation of one.

## Isolation: XDG_DATA_HOME, not --user-data-dir

`--user-data-dir` was the first thing tried, because it is a real Godot CLI flag in general —
and it is exactly wrong for this specific binary: `--help` on the Linux build this project
launches (`tools/toolpaths.godot()`) lists no such flag at all (checked directly; PRC-18's
own verification bar asks for the actual result, not the assumption). It was *silently*
ignored rather than rejected — Godot does not error on an argument after `--` meant for the
engine but shaped like a flag it does not have; it is just never read. That is how the first
version of this tool actually ran: twice, against
`~/.local/share/godot/app_userdata/Latticefall/progress.json` — the REAL shared save this
Linux dev/test build accumulates across sessions on this machine (separate from the owner's
actually-played save, which lives on the Windows build under
`%APPDATA%/Godot/app_userdata/Latticefall/`, untouched by anything in this file) — and it was
restored by hand before this comment was written. That mistake is the reason this paragraph
exists: a flag that silently does nothing is worse than one that errors, and CLAUDE.md's own
`.godot/`-cache warning is the same class of hazard for a different shared file.

What actually redirects `user://` on this platform: Godot's Linux backend resolves it via the
XDG Base Directory spec, `$XDG_DATA_HOME/godot/app_userdata/<project name>/` (falling back to
`~/.local/share/godot/...` when `XDG_DATA_HOME` is unset, which is exactly the path above).
Verified directly: launching with `env={"XDG_DATA_HOME": <scratch>, ...os.environ}` produced
a save under `<scratch>/godot/app_userdata/Latticefall/progress.json`, a FRESH state (0
cleared, 0 owned), and left the real default location's mtime unchanged. That is the
mechanism this file actually uses. It is Linux-specific by construction (matches
`_is_linux_native()` in `tools/toolpaths.py`), which is fine here: every invisible-capture
launch this project makes already prefers the native Linux build over a Windows/macOS one
(`toolpaths.godot()`), and this tool refuses to run (SKIP, not a silent no-op) against
anything else rather than repeat the exact mistake this paragraph documents.

    .venv/bin/python tools/save_roundtrip.py
    .venv/bin/python tools/save_roundtrip.py --keep     # leave the scratch save dir on disk
                                                          # and print its path

Exit 0 if both launches ran, the draft offered `draft_size` choices, the taken id shows up
owned in the SAME process, and the SECOND process (fresh boot, same on-disk save) both
excludes that id from its own offer and reports the identical owned state. Exit 1 naming
which of those failed. Exit 2 if the resolved Godot is not the native Linux build this
isolation mechanism requires (see above) — a fact about the machine, not a failure. Exit 124
on a timeout (reaped the same way `tools/scenario.py` does — the Xvfb-wrapped child reparents
on a hard kill, see that file's own note).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import lease        # noqa: E402
import reap         # noqa: E402
import toolpaths    # noqa: E402

DEFAULT_TIMEOUT = 90.0
TIMED_OUT = 124

# project.godot's config/name — the path segment Godot's Linux backend uses under
# $XDG_DATA_HOME/godot/app_userdata/<this>/. Read once from the actual project file rather
# than hardcoded a second time, so a rename (like the one progress.gd's own
# LEGACY_DIR_NAMES comment describes happening once already) cannot silently desync this
# tool from where saves actually land.
def _project_name() -> str:
    text = (ROOT / "project.godot").read_text()
    m = re.search(r'config/name\s*=\s*"([^"]*)"', text)
    return m.group(1) if m else "Latticefall"


# Every marker draft.gd's CLI path can print, mirroring tools/scenario.py's own
# RELAY_PREFIXES discipline: a hook that fires and gets filtered out by an unlisted prefix
# is a hook that looks broken when it is not (LF-153's own lesson, named in this PR's brief).
MARKERS = ("DRAFT-BOOT ", "AUTO-TAKE ", "RECOVERY-TAKEN ", "DRAFT ", "DRAFTSHOT ", "PROGRESS ",
           "CLI-WARN ")

SEED = "prc18-save-roundtrip"


def _out(line: str) -> None:
    print(line, flush=True)


def _err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _relay(blob: str) -> list[str]:
    lines = [l for l in blob.splitlines() if l.startswith(MARKERS)]
    for l in lines:
        _out(l)
    return lines


def _launch(xdg_data_home: Path, shot: Path, extra_game_flags: list[str],
            timeout: float) -> tuple[int, list[str]]:
    argv = toolpaths.godot_argv(
        ROOT,
        ["--fixed-fps", "60", "--",
         "--draft", "--seed", SEED, *extra_game_flags, "--shot", str(shot)],
        want_window=False,
    )
    env = {**os.environ, "XDG_DATA_HOME": str(xdg_data_home)}
    try:
        with lease.acquire_capture("save-roundtrip", argv, ttl_s=timeout + 60.0):
            try:
                r = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT),
                                   timeout=timeout, env=env)
            except subprocess.TimeoutExpired as exc:
                procs = reap.find()
                killed = reap._kill(procs, quiet=False) if procs else 0
                _err(f"save_roundtrip: timed out after {timeout:.0f}s; reaped {killed} of "
                     f"{len(procs)} stray process(es)")
                out = exc.stdout or ""
                _relay(out.decode(errors="replace") if isinstance(out, bytes) else out)
                return TIMED_OUT, []
    except TimeoutError as exc:
        _err(f"save_roundtrip: {exc}")
        return TIMED_OUT, []
    blob = r.stdout + r.stderr
    relayed = _relay(blob)
    if r.returncode not in (0, None):
        _err(f"save_roundtrip: godot exited {r.returncode}")
        _err(blob.strip()[-1500:])
    return (r.returncode or 0), relayed


def _field(lines: list[str], prefix: str) -> str:
    return next((l for l in lines if l.startswith(prefix)), "")


def _offer_ids(draft_boot_line: str) -> list[str]:
    m = re.search(r"offer=(\S*)", draft_boot_line)
    if not m or m.group(1) == "":
        return []
    return m.group(1).split(",")


def _owned_count(progress_line: str) -> int:
    m = re.search(r"(\d+) recoveries owned", progress_line)
    return int(m.group(1)) if m else -1


def run(timeout: float, keep: bool) -> int:
    exe = toolpaths.godot()
    if exe is None:
        _err("save_roundtrip: godot not installed")
        return 2
    # See module docstring: XDG_DATA_HOME only redirects user:// on Godot's Linux backend.
    # A Windows .exe (WSL interop) or the macOS app bundle would silently ignore it exactly
    # the way --user-data-dir was silently ignored — refusing outright is the point.
    is_windows = toolpaths.is_windows_exe(exe)
    is_mac = exe.startswith("/Applications/")
    if is_windows or is_mac:
        _err(f"save_roundtrip: resolved Godot ({exe}) is not the native Linux build — "
             f"XDG_DATA_HOME isolation only works there (see module docstring). Skipping "
             f"rather than risk writing to a real save on an untested platform.")
        return 2

    project = _project_name()
    scratch = Path(tempfile.mkdtemp(prefix="lf-save-roundtrip-"))
    problems: list[str] = []
    try:
        _out(f"save_roundtrip: isolated XDG_DATA_HOME={scratch} (never the owner's real save; "
             f"save lands under {scratch}/godot/app_userdata/{project}/)")

        # ── launch 1: fresh draft, take the first offered recovery ──────────────
        shot1 = scratch / "draft1.png"
        rc1, lines1 = _launch(scratch, shot1, ["--auto-take"], timeout)
        if rc1 == TIMED_OUT:
            return TIMED_OUT
        if rc1 != 0:
            return 1

        boot1 = _field(lines1, "DRAFT-BOOT ")
        auto1 = _field(lines1, "AUTO-TAKE ")
        taken1 = _field(lines1, "RECOVERY-TAKEN ")
        prog1 = _field(lines1, "PROGRESS ")
        if not (boot1 and auto1 and taken1 and prog1):
            return _fail("launch 1 did not print every expected marker "
                         "(DRAFT-BOOT/AUTO-TAKE/RECOVERY-TAKEN/PROGRESS) — see relayed output above")

        offer1 = _offer_ids(boot1)
        auto_id = auto1.split("id=", 1)[1].strip() if "id=" in auto1 else ""
        taken_id = taken1.split("id=", 1)[1].split()[0] if "id=" in taken1 else ""
        owned1 = _owned_count(prog1)

        if len(offer1) != 3:
            problems.append(f"launch 1 offered {len(offer1)} recoveries, want 3 "
                            f"(draft_size, data/tuning.json): {offer1}")
        if auto_id not in offer1:
            problems.append(f"--auto-take took {auto_id!r}, not one of the offered ids {offer1}")
        if taken_id != auto_id:
            problems.append(f"RECOVERY-TAKEN fired for {taken_id!r}, expected the auto-taken "
                            f"{auto_id!r} — the take did not reach recoveries.gd correctly")
        if owned1 != 1:
            problems.append(f"after taking one recovery on a FRESH save, PROGRESS reports "
                            f"{owned1} owned, want 1: {prog1!r}")

        # ── launch 2: separate process, same on-disk save, no --auto-take ───────
        shot2 = scratch / "draft2.png"
        rc2, lines2 = _launch(scratch, shot2, [], timeout)
        if rc2 == TIMED_OUT:
            return TIMED_OUT
        if rc2 != 0:
            return _fail("launch 2 (fresh process, same save) exited non-zero", problems)

        boot2 = _field(lines2, "DRAFT-BOOT ")
        prog2 = _field(lines2, "PROGRESS ")
        if not (boot2 and prog2):
            return _fail("launch 2 did not print DRAFT-BOOT/PROGRESS", problems)

        offer2 = _offer_ids(boot2)
        owned2 = _owned_count(prog2)

        if taken_id and taken_id in offer2:
            problems.append(f"launch 2 (a brand new process reading the save launch 1 wrote) "
                            f"still offered {taken_id!r} — the grant did not persist to disk, "
                            f"or was not read back")
        if owned2 != owned1:
            problems.append(f"launch 2 reports {owned2} recoveries owned, launch 1 left "
                            f"{owned1} — round trip mismatch. launch1={prog1!r} "
                            f"launch2={prog2!r}")

        # Direct check of the file on disk too, not only the engine's own report of it —
        # this is the ACTUAL round-trip artefact, and reading it independently is what makes
        # this a check of persistence rather than a check of two processes agreeing with
        # each other about a bug they might share.
        save_path = scratch / "godot" / "app_userdata" / project / "progress.json"
        if not save_path.exists():
            problems.append(f"{save_path} does not exist — the isolated save never landed "
                            f"where XDG_DATA_HOME says it should have")
        else:
            import json
            doc = json.loads(save_path.read_text())
            owned_on_disk = doc.get("owned_recoveries", [])
            if taken_id not in owned_on_disk:
                problems.append(f"{save_path}'s own owned_recoveries {owned_on_disk} does not "
                                f"contain {taken_id!r}")

        if problems:
            return _fail("save/load round trip FAILED", problems)

        _out(f"save_roundtrip: OK — took {taken_id!r} in process 1, process 2 (fresh boot, "
             f"same on-disk save) read it back: excluded from its own offer, "
             f"{owned2} recoveries owned in both, {save_path.name} agrees on disk")
        return 0
    finally:
        if keep:
            _out(f"save_roundtrip: --keep set, leaving {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def _fail(headline: str, problems: list[str] | None = None) -> int:
    _err(f"save_roundtrip: {headline}")
    for p in (problems or []):
        _err(f"  - {p}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="tools/save_roundtrip.py",
        description="Save/load round trip + recovery-draft smoke test, via two isolated "
                    "Godot launches sharing a scratch XDG_DATA_HOME. See module docstring.")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"seconds per launch before killing Godot and reaping stragglers "
                         f"(default: {DEFAULT_TIMEOUT:.0f})")
    ap.add_argument("--keep", action="store_true",
                    help="leave the scratch save directory on disk (for inspecting the "
                         "written progress.json / draft screenshots) instead of deleting it")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    return run(args.timeout, args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
