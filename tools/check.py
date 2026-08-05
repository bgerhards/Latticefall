#!/usr/bin/env python3
"""
The gate. Run before every commit.

One command, one exit code. Each check is mechanical and fast — this catches breakage,
not cheapness. For "does it feel finished", use the `verify` skill and the
`build-verifier` agent.

Checks that cannot run yet (because the subsystem does not exist) report SKIP rather than
passing silently. A green run that quietly skipped half the suite is worse than a red one.

    .venv/bin/python tools/check.py                    # tier 4 (default) — everything
    .venv/bin/python tools/check.py --tier 1            # pre-commit, the fastest tier
    .venv/bin/python tools/check.py --tier 2            # pre-push
    .venv/bin/python tools/check.py --tier 3            # PR
    .venv/bin/python tools/check.py --list              # every check, its tier, RENDERED tag
    .venv/bin/python tools/check.py --json /tmp/gate.json   # machine-readable, human table too
    .venv/bin/python tools/check.py --json                  # machine-readable to stdout only

## Tiers

A tier is a minimum: a check assigned tier 1 also runs at `--tier 2`, `--tier 3` and the
untiered default (which is tier 4, unchanged from before tiering existed — the default must
never become weaker than what CLAUDE.md promises). A check the selected tier excludes does
**not** run at all; it is reported `skip`, `skipped_reason: "tier"`, so the JSON record still
names it rather than omitting it, which would read as a pass to anything consuming the file.
`--no-window` is orthogonal to `--tier`: it always skips the rendered checks (`RENDERED`
below — every one of them a real Godot launch, tier 3) regardless of tier, so `--tier 3
--no-window` runs exactly tier 2 plus nothing.

**Wall-clock costs are deliberately not stated anywhere in this docstring.** PRC-16: a
hand-written "~66 s" for tier 3 here drifted to a measured ~189 s within a single session,
while `CLAUDE.md` carried a second, differently-wrong copy of the same figure at the same
time — two hand-maintained numbers is worse than zero, and unlike a check *count* there is no
registry a runtime cost could be asserted against without adding a whole caching subsystem
just to keep a docstring honest. Run `--tier N --json` (or read `TIER_BUDGET_MS` for the
tier-1/tier-2 budgets specifically) for the real, current figure. Check *counts* are the
opposite case — they live in `CHECKS`, so they cost nothing to assert — and the `tier counts`
check below does exactly that on every tier-1 run: a stale count here is a red run, not a
silent drift. See that check's own docstring for what it caught before it existed.

- **tier 1 (pre-commit), 21 checks:** `python syntax`, `json parses`, `gdscript parses`,
  `game data`, `wave density`, `dialog capacity`, `dialog figures`, `backlog rendered`,
  `chronicle current`, `agent models`, `leases wired`, `issue traceability`, `banned terms`,
  `tier counts`, `safe operations`, `rules autoloads`, `yaw hysteresis`, `asset coverage`,
  `playfield width`, `grade verdict`, `grade criteria`. No Godot window opens.

  **`grade criteria` (LF-270/LF-278) is the newest, and it is `grade verdict`'s argument
  applied to a second instrument.** `tools/criteria.py` prints BAL-04's acceptance-criteria
  table against `tools/bal04_baseline.json`, and on the shipped campaign it prints six `ok`s
  — the state in which a comparator that can only say `ok` looks exactly like one that
  works. This drives all six red as well as green over synthetic grade tables in ~0.2 s. It
  is also the *only* caller of `tools/density.py`'s `build_tower_ids()` and
  `weapon_ids_in_build()`, which is the other half of why it exists: a `built` entry is
  `"<tower-id>@<x>,<y>`", so a membership test against bare ids matches nothing and answers
  a confident **zero** without raising — the helpers exist to hold that down and had no
  caller to notice if they stopped.

  **`dialog figures` is the newest, and what it says about mechanical checks over prose is
  worth more than the check.** `dialog capacity` already covers the bus figure a brief reads
  aloud; every *other* number the briefs speak had no cover, and one was wrong — anchor-04
  told the player the shield wall costs "forty of those ninety-four" megawatts when it draws
  **26**, stale since `LF-032` lowered it from 40. Two mechanical designs were tried against
  the corpus first and **both fail**: a regex over "<number> megawatts" *misses this exact
  bug*, because "forty" carries no unit word; and "every spoken number must be a live figure"
  false-positives on "reaches across four lanes" and on the *deliberately* unexplained
  figures in anchor-08 and anchor-17 ("about nine megawatts that isn't going anywhere I can
  find"), which are narrative and must stay unexplained. So the dependency is **declared, not
  inferred** — a line carries `quotes`, dotted paths into `towers.json`/`enemies.json`, and
  the check asserts the live value is among the numbers the line says. It is opt-in by
  construction and therefore cannot prove a line *should* have been annotated; what it
  guarantees is that a declared figure can never be silently invalidated by a tuning change.

  **`grade verdict` (LF-243) is the newest, and it is the red half of a tier-3 check.**
  `anchor grades` grades the shipped 24 and asserts they pass, so every rule in
  `sim/run.py`'s `verdict()` is only ever seen succeeding; three of the four cannot fire on
  any shipped anchor at all. This drives all of them on both paths, over synthetic grade
  tables, in ~180 ms with no Godot and no content load. Same argument as `firing arcs agree`
  and `verbs agree`, one level up — see `check_grade_verdict`'s own docstring.

  **`playfield width` (LF-247) is the newest, and it is here rather than at tier 2 or 3
  because it costs nothing to run and everything to have skipped.** It asserts that the board
  is at least `Ui.PLAYFIELD_MIN_W` wide at every scale in `Display.UI_SCALES`, entirely by
  parsing constants — no Godot, no frame. The bug it closes was a playfield **4 px wide at
  200% interface scale, on every anchor**, with `accessibility` and `scenario a11y-worst`
  (which runs at exactly that scale, on exactly that anchor, *because* it is the worst case)
  both green throughout, because both audit **text** and a playfield is not a text item. Its
  mirror of the four geometry expressions is pinned verbatim against `scripts/ui_theme.gd`,
  so the mirror can only go stale loudly.
- **tier 2 (pre-push), 30 checks:** tier 1 + `sim determinism`, `sprite atlas`,
  `sprite coverage`, `music manifest`, `sfx determinism`, `sfx loudness` (PRC-18 — see
  `_run_loudness_check`'s own comment for why it is `sfx loudness` and not `music loudness`
  that landed here), `godot boots`, `terrain parsers agree`, `hooks configured`.
  `hooks configured` (PRC-06) was measured at tier 1 first and moved here: it spawns real
  Godot processes (`guard.py --selftest`, itself two Godot parse checks plus a `reap.py`
  probe) and pushed tier 1 over its own `--budget` contract while it briefly lived there.

  **`facing harness` was moved OUT of tier 2 to tier 3, closing LF-178.** Tier 2 had been
  over its 28,000 ms budget on four separate idle-ish measurements — 28,598, 28,751 and
  29,831 ms here, plus 45,555 ms on a contended run — and `TIER_BUDGET_MS` had already been
  raised once (25,000 → 28,000, for `sfx loudness`). A second raise would have made the
  number decorative, which is the move decision 067 rejected when it deleted `PRESSURE_FLOOR`
  rather than raising it. So the budget did not move and a check did. `facing harness`
  (LF-142) was chosen over `terrain parsers agree`, the other candidate LF-178 names, because
  it is a narrow regression harness for one mapping, whereas `terrain parsers agree` is the
  cheap fast-tier sibling of `rules parity` — the same two-implementations-drifting risk at a
  fraction of the cost — and decision 057 put it in the fast tier deliberately.

  **What 28,000 ms means, since it was previously a round number with no stated derivation.**
  It is an *idle-machine* ceiling on this 16-core WSL2 box: tier 2 measured **27,225 ms** and
  **27,790 ms** on two runs after the move, both with one core busy, so the real headroom is
  between **0.8% and 2.8%** and the run-to-run spread (~570 ms) is itself larger than the
  smaller of those margins. Treat the budget as barely satisfied, not comfortably. It is
  explicitly **not** survivable under contention — the 45,555 ms figure above was a real
  tier-2 run while other agents held cores — and `--budget` is not passed by `check.py`'s
  default invocation or by the wrap, so it is advisory in every path that currently runs it.
  Two consequences worth knowing before touching this: a 3% margin means **the next check
  added to tier 2 blows the budget again**, and the structural answer at that point is
  LF-178's option (2), splitting into a ~10 s pre-commit tier and a pre-push tier, not
  another raise. Every duration here is timed with **`time.monotonic()`**, closing LF-170 —
  it was `time.time()`, which is subject to wall-clock adjustment and was observed once
  reporting a **negative 796 ms** elapsed under heavy concurrent load. That matters more
  than a cosmetic wrong number: the whole tiering argument rests on per-check costs being
  trustworthy, and `--budget` turns the figures above into assertions, so an instrument that
  can run backwards is the wrong one to be judging a 0.8% margin with. Only `started_at`
  stays on the wall clock, because a timestamp is not a duration.
- **tier 3 (PR), 44 checks:** tier 2 + `facing harness` (moved here from tier 2, above) +
  `anchor grades` (LF-224 — the deliberate replacement for the all-anchors-clean assertion
  `sim determinism` was serving by accident until PLC-04; ~62 s, and tier 3 rather than
  tier 2 because tier 2 is over budget and rather than tier 4 because the regression it
  catches arrives as a data-only PR, see `check_anchor_grades`) +
  `game renders`, `menu renders`, `accessibility`,
  `scenario smoke`, `scenario abilities`, `scenario a11y-worst`, `scenario lf161-scroll`,
  `scenario gamepad`, `scenario lf226-fallback` (PRC-18 split what used to be one
  `scenarios pass` check hardcoding `smoke.json` into one check per `data/scenarios/*.json`
  file — see `SCENARIO_FILES`/`_run_scenario_check`'s own doc for why a failure should name
  the exact file rather than share one check across all six; LF-226 added the sixth, the only
  check anywhere in this file that reaches `anchor_view.gd`'s `_lattice_fallback_candidate()`
  — a branch no shipped anchor could enter until anchor-11's `max_emplacements` was authored
  above its slot count, and the same shape of hole `firing arcs agree` exists to plug),
  and `save roundtrip` (`tools/save_roundtrip.py` — a
  genuine two-process save/load round trip and the recovery draft, neither reachable through
  a scenario file at all). PR tier is meant to be where a coverage regression is caught
  before merge, and every one of these ten launches Godot exactly like the three that were
  here before PRC-18. PLC-03 added `firing arcs agree` here too — a headless, windowless
  Godot like `terrain parsers agree`, put at this tier rather than at tier 2 because tier 2
  is already over its own budget (LF-178) and the branch it covers is inert in shipped data;
  see `check_firing_arcs`'s own docstring. **LF-244 added `verbs agree` here on the same
  reasoning and for a larger hole** — six of the eight verbs `Sim._dispatch_one()` accepts
  are never scheduled by any shipped policy, and unlike the firing arc the *player* uses
  every one of them; see `check_verb_parity`'s own docstring.
- **tier 4 (nightly/release), 47 checks — the default:** tier 3 + `music loudness` (see
  `_run_loudness_check`'s own comment for why it did not join `sfx loudness` at tier 3) +
  `rules parity` (grows with every policy/anchor) + `rules parity (windows)` (BAL-06 — the
  same runs again, against the Windows binary the owner actually plays rather than the Linux
  build `rules parity` uses; skips loudly with `skipped_reason: "subsystem"` on a machine
  with no Windows Godot, e.g. a Linux CI box, rather than silently passing). The one thing
  making a balance claim in this project falsifiable; never move either below tier 4 because
  it is "usually fine". PRC-05: both are gated behind their OWN content-hash digest over
  exactly what can move a parity outcome (`tools/test_parity.py`'s `parity_inputs_digest()`),
  in SEPARATE cache files (`.cache/parity.json`, `.cache/parity-windows.json`) so a clean run
  of one can never suppress the other — an unchanged tree reports `skip, skipped_reason:
  "cached"` instead of re-running, and `--force`/`--no-cache` bypass it. A cached skip is
  still not a pass; see the loud-skip note a few paragraphs down.

`--budget` asserts the tier-1 and tier-2 contracts declared in `TIER_BUDGET_MS`: with it set,
exceeding a tier's budget fails the run even if every check passed, because a tier whose
budget silently doubles is a tier nobody will keep running. Read `TIER_BUDGET_MS` itself for
the current numbers rather than this sentence — see the note above on why costs are not
duplicated into prose.

`--json`'s shape, for `tools/session.py` and `tools/gate_report.py` (and eventually CI —
`.github/workflows/gate.yml` consumes it at tier 1):

    {
      "schema": "latticefall-gate", "version": 1,
      "started_at": "<UTC ISO 8601>", "duration_ms": <float>,
      "root_commit": "<sha or null>", "dirty": <bool>,
      "tier": <int>,                       # the --tier this run was asked for (1-4)
      "checks": [
        {"name": str, "status": "ok"|"FAIL"|"skip", "ms": float, "detail": str, "tier": int,
         "skipped_reason": null|"subsystem"|"flag"|"tier"|"cached"}, ...
      ],
      "summary": {"passed": int, "failed": int, "skipped": int, "skipped_by_flag": int,
                  "skipped_by_tier": int, "skipped_by_cache": int, "total": int,
                  "duration_ms": float, "text": "<the printed tally line>"}
    }

`checks` always lists every check `CHECKS` defines, at whatever count that is today — a check
a lower tier excluded is present with
`status: "skip"` rather than dropped from the array, for the same reason `--no-window`'s
skips are present: an absent entry reads as a pass to anything consuming the file. `detail`
carries the check's *full* multi-line detail, not just the first line the human table prints
— a PR comment needs all of it. `skipped_reason` distinguishes four unrelated claims the
plain exit code cannot tell apart: a subsystem that does not exist yet, `--no-window`'s
choice not to open a window, `--tier`'s choice not to run a check this tier excludes, and
(PRC-05) `rules parity`'s own content-hash cache reporting that none of its inputs moved
since the last clean run. That last one is a skip like every other — `tools/test_parity.py`
did not compare anything this run, so it is never treated as a pass — but it is also, like
`"flag"` and `"tier"`, a fact about a *choice* this run made rather than about a broken
environment, so `tools/gate_report.py --fail-on-subsystem-skip` (CI's escalation for a
missing subsystem) leaves it alone.
`--json` with no path still runs everything the selected tier includes (only `--no-window`
and `--tier` skip checks) — a JSON run that quietly skipped work its own flags didn't ask
for would be exactly the failure mode this whole file exists to prevent. `--force`/
`--no-cache` are the escape hatch for `rules parity`'s own cache specifically (see below);
neither flag changes what any other check does.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

sys.path.insert(0, str(ROOT / "tools"))
import lease                                                   # noqa: E402
import toolpaths                                              # noqa: E402

OK, FAIL, SKIP = "ok", "FAIL", "skip"


class Result:
    def __init__(self, status: str, detail: str = "",
                 skipped_reason: str | None = None) -> None:
        self.status, self.detail, self.skipped_reason = status, detail, skipped_reason


## Every subprocess the gate starts is bounded, because an unbounded one is not a slow
## check — it is a hang that reports success afterwards. Measured on 2026-07-29: the
## `game renders` check, which normally takes 2.5 s, took **36 minutes** on one run and then
## passed, taking the whole gate to 47 minutes. With no timeout there was nothing to
## distinguish that from ordinary slowness, and a wedged Godot holding a core is exactly the
## survivor `tools/reap.py` exists for. The generous default is deliberate — this is a
## backstop against a wedge, not a performance budget.
DEFAULT_TIMEOUT = 300.0
## The parity check runs 864 simulations through both implementations and legitimately takes
## about eleven minutes, so it gets its own ceiling rather than dragging the default up.
PARITY_TIMEOUT = 1800.0
TIMED_OUT = 124                       # conventional shell exit code for a timeout


def tracked(*globs: str) -> list[Path]:
    """Every file this repo tracks matching `globs`, as absolute paths under ROOT.

    Shells `git ls-files -z -- <globs>` and splits on `\\0`: this repo's paths are not
    guaranteed free of spaces (docs/ filenames especially, even though today's aren't), and
    newline-splitting is the classic way this kind of helper breaks silently.

    `git ls-files` is "in this repository, right now" — untracked scratch files, build
    output, and agent worktrees under `.claude/` never show up. That is what closes LF-051: a
    worktree carrying its own copy of `docs/NOMENCLATURE.md` scored six false hits against a
    hand-maintained `SKIP_DIRS` denylist, because `rglob` cannot tell "a second checkout of
    this repo" from "this repo". `git ls-files` needs no such list — a file has to be tracked
    to be seen at all, so the false positive is structurally impossible rather than denied.

    Falls back to the old `rglob` walk, filtered the same way `SKIP_DIRS` used to filter it,
    when this is not a git checkout at all (an export tarball) — so the gate still runs
    rather than crashing on `git`'s nonzero exit.

    A path `git ls-files` names but that is missing from the working tree (a staged deletion,
    or an index that briefly disagrees with disk) is dropped rather than handed to a caller
    that will `read_text()` it and turn "one file was deleted" into "check itself raised".
    """
    if not (ROOT / ".git").exists():
        FALLBACK_SKIP_DIRS = {".venv", "addons", ".godot", ".claude"}
        out: list[Path] = []
        for g in globs:
            out += [p for p in ROOT.rglob(g) if not FALLBACK_SKIP_DIRS.intersection(p.parts)]
        return sorted(set(out))
    r = subprocess.run(["git", "ls-files", "-z", "--", *globs],
                       capture_output=True, text=True, cwd=str(ROOT))
    paths = (ROOT / p for p in r.stdout.split("\0") if p)
    return sorted(p for p in paths if p.exists())


## Where a check's throwaway evidence goes. NOT `.godot/` — PRC-15 task 2.
##
## Every rendering check used to write a FIXED path under the engine's import cache:
## `.godot/gate-frame.png`, `.godot/gate-menu.png`, `.godot/gate-a11y-<case>.{png,json}`.
## Two consequences, both real.
##
## Two concurrent gate runs deleted each other's evidence — the accessibility analyser
## samples background colours out of those PNGs, so it could read a frame another process
## had already unlinked, and "two agents ran the gate at once" had no defence at all.
##
## And it put the gate's scratch files inside the directory the owner's running game reads
## from, which `guard.py:rule_godot_write` forbids every other writer from touching for
## exactly the reason LF-075 records. The gate was the one writer exempt from the rule it
## enforces, by being the thing that wrote the rule.
##
## `.cache/` is already gitignored and already holds the lease directory and the parity
## digests, so it is the established home for state that is real but not content.
ARTIFACT_ROOT = ROOT / ".cache" / "gate"


def with_artifacts(name: str):
    """Give a check its own per-run artefact directory, and clean it up ONLY on success.

    The pid is in the directory name so a hard-killed run leaves an inspectable trail
    rather than a mystery (PRC-15 task 1).

    **Cleaning up only on success is deliberate and preserves the existing behaviour.**
    The old code called `shot.unlink()` *after* its assertions passed and returned early on
    failure, which leaves the failing frame on disk to be looked at — that is the whole
    value of a rendering check that failed. A blanket `finally: rmtree` would have been
    tidier and would have destroyed the evidence, so the failure path keeps the directory
    and the failure message says where it is.
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
            out = Path(tempfile.mkdtemp(prefix=f"{name}-{os.getpid()}-", dir=ARTIFACT_ROOT))
            result = fn(out, *a, **kw)
            if result.status == OK:
                shutil.rmtree(out, ignore_errors=True)
            elif result.status == FAIL:
                result.detail = f"{result.detail}\n    artefacts kept: {out}"
            return result
        return wrapper
    return decorate


def run(*args: str, timeout: float = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a child, bounded. On timeout, reap whatever it left behind and report failure.

    `subprocess.run(timeout=...)` kills the direct child only. That is not sufficient here:
    the parity check's Godot reparents to init and survives its parent (see CLAUDE.md), so
    the timeout path calls the reaper rather than trusting the kill.

    Leased for tools/reap.py (PRC-07), so a sibling agent's `tools/reap.py --kill` spares
    this gate's own Godot/Blender/etc. children instead of ending them mid-check. Whether
    the lease also serialises against LF-116's measured capture cost is auto-detected from
    `args[0]`: `toolpaths.godot_argv(..., want_window=False)` prefixes the whole command
    with `xvfb-run` exactly when a window would otherwise land on the owner's desktop, and
    that prefix is the one reliable signal that this particular launch is a Mesa llvmpipe
    capture rather than a plain headless run (`godot boots`, `rules parity`) or a bare
    Python subprocess (`game data`, `sfx determinism`, ...) — neither of those was part of
    what LF-116 measured, so neither waits for a capture slot.
    """
    is_capture = bool(args) and Path(args[0]).name == "xvfb-run"
    acquire = lease.acquire_capture if is_capture else lease.acquire
    tool = "check-capture" if is_capture else "check"
    try:
        with acquire(tool, list(args), ttl_s=timeout + 60.0):
            try:
                return subprocess.run(args, capture_output=True, text=True,
                                      cwd=str(ROOT), timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                reaper = ROOT / "tools" / "reap.py"
                swept = ""
                if reaper.exists():
                    got = subprocess.run([PY, str(reaper), "--kill"], capture_output=True,
                                         text=True, cwd=str(ROOT), timeout=60)
                    swept = got.stdout.strip().replace("\n", " · ")
                out = exc.stdout or b""
                return subprocess.CompletedProcess(
                    args, TIMED_OUT,
                    stdout=out.decode(errors="replace") if isinstance(out, bytes) else out,
                    stderr=f"timed out after {timeout:.0f}s; reaped: {swept or 'nothing'}",
                )
    except TimeoutError as exc:
        # No capture slot freed within tools/lease.py's wait window — the launch never
        # even started. Reported the same shape check.py's own TIMED_OUT path uses, so
        # every caller of run() handles it identically without a new branch.
        return subprocess.CompletedProcess(args, TIMED_OUT, stdout="", stderr=str(exc))


# ─────────────────────────────────────────────────────────────── checks ──

def check_python_syntax() -> Result:
    files = tracked("*.py")
    bad = []
    for p in files:
        try:
            py_compile.compile(str(p), doraise=True, cfile=str(p.with_suffix(".pyc-check")))
        except py_compile.PyCompileError as e:
            bad.append(f"{p.relative_to(ROOT)}: {e.msg.splitlines()[-1]}")
        finally:
            p.with_suffix(".pyc-check").unlink(missing_ok=True)
    if bad:
        return Result(FAIL, "\n".join(bad))
    return Result(OK, f"{len(files)} files")


def check_game_data() -> Result:
    script = ROOT / "tools" / "validate" / "validate_data.py"
    if not script.exists():
        return Result(SKIP, "validator missing")
    r = run(PY, str(script), "--quiet")
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip())
    warns = [l for l in r.stdout.splitlines() if l.strip().startswith("warn")]
    # The validator dispatches generically now — every tracked data/**/*.json is checked
    # against the schema its own "schema" key names, and a schema no document exercises is an
    # error. It prints that tally unconditionally, even under --quiet, precisely so this line
    # can say what was validated rather than only how many complaints came back. A gate line
    # reading "no warnings" is compatible with having validated nothing at all, which is
    # exactly how data/tuning.json went unchecked for a session (LF-064).
    counts = next((l.strip() for l in r.stdout.splitlines()
                   if "documents against" in l), "")
    warned = f"{len(warns)} warning(s)" if warns else "no warnings"
    return Result(OK, f"{counts} · {warned}" if counts else warned)


## No act may run at less than this fraction of the busiest act's screen presence.
##
## BAL-04 asked whether 0.55 still means anything now that decision 091 has moved the busiest
## act from 1 to 2. It does, and **the number does not move** — the strongest defence against
## "fitted to today's data" is a threshold that was placed against a measured *defect* and has
## not been touched since. Decision 067 deleted PRESSURE_FLOOR for being a constant with no
## argument behind it; this one has one, and it is below rather than in a session transcript.
##
## The rule, stated before the measurement: the floor must be red on every state of this
## campaign that LF-044 named a defect, and green on every state since the defect was fixed.
## Peak concurrency per act, recomputed at all fourteen commits that have touched
## data/anchors/ and had more than one act on disk (min/max in the last column):
##
##     7378dee   32.4  16.0     -    0.494   Act II opens
##     915d5da   32.4  15.6     -    0.483   Act II complete
##     c048141   32.4  15.6  11.1    0.344   Act III complete — the LF-044 defect
##     5475dfa   32.4  15.6  11.1    0.344
##     1cfbeaa   32.4  16.0  11.1    0.344
##     b7e38d3   32.4  16.0  11.1    0.344   leak_cost stops being flat (decision 047)
##     4aef1cc   32.4  27.4  21.1    0.653   escort units: the fix, and this constant
##     668c6b4 … 5021a9e                     four more commits at 0.653
##     8c56b5c   32.0  27.1  21.1    0.660
##     cd21e6f   32.0  27.1  21.1    0.660   decision 090, arc-node repricing
##     901053f   26.2  27.1  21.1    0.779   decision 091, warden-hauler — today
##
## Six observed states below the floor, eight above, and **no observation anywhere in
## (0.494, 0.653)**. Every threshold in that open interval returns the same verdict on all
## fourteen; 0.55 sits inside it with 0.056 of margin below and 0.103 above. That is a
## plateau 0.159 wide, not a cell — LF-264's bar, met by a constant nobody had to move.
##
## Reachable today, not only in history: `sweep.py --weight 0.70` over Act III alone takes the
## ratio to 0.535 and this check to red. 0.75 leaves it green at 0.604.
##
## No ABSOLUTE floor is added alongside it, and both reasons are measurements rather than
## preferences. The only absolute floor that can be *derived* is dead on arrival: one target
## inside a median turret's envelope needs a peak of 1 / 0.174 = 5.75 units (decision 082's
## own-lane coverage), against a campaign minimum of 18 on anchor-18 — a 3.1x margin, which is
## exactly PRESSURE_FLOOR's disease. And the hole an absolute floor would cover is already
## covered elsewhere: a *uniform* thinning leaves this ratio flat (0.779 -> 0.716 at
## `--weight 0.30` campaign-wide, still green) but fails `anchor grades` on 9 of 24 anchors by
## weight 0.70, with campaign win share going 44.8/31.2/25.1 -> 61.1/52.9/51.6. Uniform
## thinning is a difficulty regression before it is a presence one, and the grader owns
## difficulty. What this check owns is the case where the two come apart, which is the one
## LF-044 actually was — see check_wave_density()'s docstring for those numbers.
DENSITY_FLOOR = 0.55


def check_wave_density() -> Result:
    """Every act keeps a comparable number of units on screen.

    Act III fielded 7.7 units a wave against Act I's 20.2 and nothing said so, because from
    Act II every unit drains the bus — unit count and bus theft were the same number, so the
    act could only get busier by getting poorer. Fixing that took two new units and a
    re-authored wave table; keeping it fixed is one ratio, and it can be undone silently,
    since `sweep.py --weight` scales every spawn count in a level at once.

    **Why the grader cannot stand in for this, measured rather than argued.** At `c048141`,
    the commit where the defect was worst, Act III showed 11.1 units on screen against Act I's
    32.4 — a third — while carrying **1562 hit points per wave against Act I's 950**, 64% more
    work for the board. Presence and difficulty did not merely vary independently there, they
    moved in opposite directions, so every difficulty instrument in the project reported Act
    III as the hardest act in the game, which it was. Presence is the axis nothing else sees.

    Measured as peak units in flight rather than units per wave: a Column at 0.5 tiles/sec
    holds the board four times as long as a Shard, so the per-wave count understates a slow
    act. Summed across lanes, not per lane, and not "within the camera" — BAL-04 asked and
    tools/density.py's docstring carries the answer with the numbers behind it. In one line:
    a camera-relative count is a function of pan, zoom and the interface scale (940 px of
    board strip at 100%, 508 px at 200%), so it describes a setting rather than the content.

    The verdict is `min(act) / max(act) >= DENSITY_FLOOR`, which is what the per-act
    comparison below has always computed; naming the spread in the message as well is BAL-04's
    only change here, because decision 091 moved *which* act is the reference and a message
    that only ever named a percentage of an unnamed busiest act made that read as a content
    event. The inequality, the constant and every historical verdict are unchanged.
    """
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    from density import peak_concurrent                       # noqa: PLC0415
    from sim.content import all_anchor_ids, load_anchor, load_enemies  # noqa: PLC0415

    enemies = load_enemies()
    per_act: dict[int, list[int]] = {}
    for aid in all_anchor_ids():
        a = load_anchor(aid)
        per_act.setdefault(a.act, []).append(peak_concurrent(a, enemies))

    means = {act: sum(v) / len(v) for act, v in sorted(per_act.items())}
    busiest = max(means.values())
    thin = [f"act {act} holds {m:.1f} units on screen against the busiest act's {busiest:.1f} "
            f"({m / busiest:.0%}, floor {DENSITY_FLOOR:.0%})"
            for act, m in means.items() if m < busiest * DENSITY_FLOOR]
    if thin:
        return Result(FAIL, "; ".join(thin))
    spread = busiest / min(means.values())
    return Result(OK, " · ".join(f"act {a} {means[a]:.0f} on screen ({means[a]/busiest:.0%})"
                                 for a in sorted(means))
                  + f" · spread {spread:.2f}:1 (cap {1 / DENSITY_FLOOR:.2f}:1)")


## Spoken numbers, for the dialog-vs-data check. Control reads the bus figure aloud in the
## brief of every anchor — "A hundred and ninety megawatts." — so the reactor tier is content
## as well as a tuning knob, and `sweep.py --apply` moves it without touching the line.
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}


def _spoken_numbers(text: str) -> set[int]:
    """Every "<words> megawatts" figure in a line of dialog, as integers."""
    import re                                                  # noqa: PLC0415

    found = set()
    for phrase in re.findall(r"([A-Za-z][A-Za-z \-]*?)\s+megawatts", text, re.I):
        words = phrase.lower().replace("-", " ").split()
        total = current = 0
        seen = False
        for w in words:
            if w in ("a", "and"):
                continue
            if w == "hundred":
                current = max(current, 1) * 100
                seen = True
            elif w in _WORD_NUM:
                current += _WORD_NUM[w]
                seen = True
            else:                       # a word that is not part of a number resets the run
                if seen:
                    total += current
                current, seen = 0, False
        if seen:
            total += current
        if total:
            found.add(total)
    return found


def _spell(n: int) -> str:
    """The number as Control would read it: "a hundred and ninety". The check reports this
    so a failure names the line to write, rather than only the number that is wrong."""
    tens = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty",
            70: "seventy", 80: "eighty", 90: "ninety"}
    ones = {v: k for k, v in _WORD_NUM.items() if v <= 19}

    def under_100(v: int) -> str:
        if v in ones:
            return ones[v]
        t, r = divmod(v, 10)
        return tens[t * 10] + (f"-{ones[r]}" if r else "")

    h, r = divmod(n, 100)
    if not h:
        return under_100(r)
    lead = "a hundred" if h == 1 else f"{ones[h]} hundred"
    return lead + (f" and {under_100(r)}" if r else "")


def check_dialog_capacity() -> Result:
    """The capacity an anchor speaks is the capacity it has.

    Every brief states the bus figure aloud, and `sweep.py --apply` writes `capacity_mw`
    without touching prose — so a re-tune silently leaves Control reading out a number that
    was true two sessions ago. Nothing else in the project compares the two, and a player
    hears this line before every level.
    """
    sys.path.insert(0, str(ROOT))
    from sim.content import DATA, all_anchor_ids, load_anchor   # noqa: PLC0415

    wrong = []
    for aid in all_anchor_ids():
        a = load_anchor(aid)
        p = DATA / "dialog" / f"{aid}.json"
        if not p.exists():
            continue
        spoken: set[int] = set()
        for line in json.loads(p.read_text())["lines"]:
            spoken |= _spoken_numbers(line["text"])
        if int(a.capacity_mw) not in spoken:
            heard = ", ".join(str(s) for s in sorted(spoken)) or "no figure at all"
            wrong.append(f"{aid} runs at {a.capacity_mw:.0f} MW but says {heard} — "
                         f'the brief should read "{_spell(int(a.capacity_mw)).capitalize()} '
                         f'megawatts"')
    if wrong:
        return Result(FAIL, "\n".join(wrong))
    return Result(OK, f"{len(all_anchor_ids())} briefs quote their own capacity")


def _all_spoken_numbers(text: str) -> set[int]:
    """Every integer a line says, spelled or in digits — not only the ones next to
    "megawatts".

    `_spoken_numbers()` deliberately anchors on the unit word, because `dialog capacity`
    is asking "does this brief state its own bus figure". That anchor is exactly what made
    anchor-04's error invisible for the whole life of the project: the line read "forty of
    those ninety-four if you raise it", and only "ninety-four" sits next to "megawatts", so
    the wrong number was never in the set being compared. Here the caller already knows
    which figure it is looking for, so the widest possible extraction is the safe one.
    """
    import re                                                  # noqa: PLC0415

    found = {int(d) for d in re.findall(r"\b\d+\b", text)}
    # Chunk on anything that is not a letter, a space or an ASCII hyphen, so a full stop or
    # an em dash ENDS a number and a hyphen does not. Without this, "Anchor fourteen.
    # Ninety-four megawatts" reads as one run and accumulates to 108 — measured, not
    # hypothetical; it was the first output of this function.
    for chunk in re.split(r"[^A-Za-z \-]+", text.lower()):
        current = 0
        seen = False
        for w in re.split(r"[ \-]+", chunk):
            if w in ("a", "and") and seen:
                continue
            if w == "hundred":
                current = max(current, 1) * 100
                seen = True
            elif w in _WORD_NUM:
                current += _WORD_NUM[w]
                seen = True
            else:                   # any other word ends the run and banks it
                if seen:
                    found.add(current)
                current, seen = 0, False
        if seen:
            found.add(current)
    return found


def check_dialog_figures() -> Result:
    """A line that reads a data figure aloud declares which one, and this checks it.

    `dialog capacity` already covers the bus figure, and that check exists because
    `sweep.py --apply` moves `capacity_mw` without touching prose. Every *other* number a
    brief speaks had no such cover, and one was wrong: anchor-04 told the player the shield
    wall costs "forty of those ninety-four" megawatts. It draws **26**. It drew 40 until
    `LF-032` lowered it, and the brief was never updated — a figure the player is read
    before the level, wrong by 14 MW, for the whole life of the project.

    Two obvious mechanical designs were tried against the corpus first and BOTH fail:

    - A regex over "<number> megawatts" **misses this exact bug**, because "forty" carries
      no unit word — the unit is implied by the "ninety-four" it is compared against.
    - "Every spoken number must be a live figure" false-positives immediately: anchor-06
      says the ion lance "reaches across four lanes", and anchor-08 and anchor-17 both
      speak deliberately *unexplained* figures ("about nine megawatts that isn't going
      anywhere I can find") which are narrative, not data, and must stay unexplained.

    So the dependency is **declared, not inferred**. A line carries `quotes`, a list of
    dotted paths into `towers.json` or `enemies.json`, and the check asserts the live value
    is among the numbers the line actually says. That survives any rewording — the writer
    can move the figure anywhere in the sentence — and it says nothing at all about lines
    that quote nothing, which is most of them.

    This is opt-in by construction, so it cannot prove a line *should* have been annotated.
    What it does guarantee is that once a figure is declared, a tuning change can never
    silently invalidate the prose — which is the failure that actually happened.
    """
    sys.path.insert(0, str(ROOT))
    from sim.content import DATA, all_anchor_ids               # noqa: PLC0415

    rows: dict[str, dict] = {}
    for fname, key in (("towers.json", "towers"), ("enemies.json", "enemies")):
        for row in json.loads((DATA / fname).read_text())[key]:
            rows[row["id"]] = row

    wrong, checked = [], 0
    for aid in all_anchor_ids():
        p = DATA / "dialog" / f"{aid}.json"
        if not p.exists():
            continue
        for line in json.loads(p.read_text())["lines"]:
            said = None
            for ref in line.get("quotes", []):
                ident, _, path = ref.partition(".")
                node = rows.get(ident)
                if node is None:
                    wrong.append(f"{aid}: `{ref}` names no emplacement or unit")
                    continue
                for part in path.split("."):
                    if not isinstance(node, dict) or part not in node:
                        node = None
                        break
                    node = node[part]
                if not isinstance(node, (int, float)):
                    wrong.append(f"{aid}: `{ref}` does not resolve to a number")
                    continue
                if said is None:
                    said = _all_spoken_numbers(line["text"])
                checked += 1
                if int(node) not in said:
                    heard = ", ".join(str(s) for s in sorted(said)) or "no figure at all"
                    wrong.append(
                        f"{aid}: `{ref}` is {int(node)} but the line says {heard} — "
                        f'"{line["text"][:70]}"')
    if wrong:
        return Result(FAIL, "\n".join(wrong))
    return Result(OK, f"{checked} quoted figure(s) match the data they name")


def check_sprite_coverage() -> Result:
    """Every enemy and emplacement id has a sprite under the name the game derives.

    `anchor_view.gd` does not look a sprite up, it *derives* one: the id with hyphens
    turned into underscores. A miss returns null, falls through to `_draw_unit`, and the
    game quietly draws a coloured circle instead — no warning, no error, and the unit still
    walks and fights. So a new unit shipped without art looks like a rendering bug rather
    than a missing asset, and only on the anchor that fields it.

    The manifest is hand-kept in step with `render.py`'s ASSETS dict and with
    `enemies.json`, by three separate people-shaped processes. This is the comparison.
    """
    sys.path.insert(0, str(ROOT))
    from sim.content import load_enemies, load_towers            # noqa: PLC0415

    man = ROOT / "assets" / "renders" / "sprites.json"
    if not man.exists():
        return Result(SKIP, "no sprite manifest")
    have = set(json.loads(man.read_text()).get("sprites", {}))
    ids = list(load_enemies()) + list(load_towers())
    missing = sorted(i for i in ids if i.replace("-", "_") not in have)
    if missing:
        return Result(FAIL, "no sprite for: " + ", ".join(missing)
                            + " — the board will draw a coloured circle and say nothing")
    return Result(OK, f"{len(ids)} ids drawn from {len(have)} sprites")


def check_json_parses() -> Result:
    files = tracked("*.json")
    bad = []
    for p in files:
        try:
            json.loads(p.read_text())
        except Exception as e:
            bad.append(f"{p.relative_to(ROOT)}: {e}")
    return Result(FAIL, "\n".join(bad)) if bad else Result(OK, f"{len(files)} files")


def check_sfx_reproducible() -> Result:
    """The bank claims to be a pure function of sound names. Verify one sound."""
    script = ROOT / "tools" / "audio" / "synth_sfx.py"
    target = ROOT / "assets" / "audio" / "sfx" / "ui_confirm.wav"
    if not (script.exists() and target.exists()):
        return Result(SKIP, "sfx bank not built")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    r = run(PY, str(script), "ui_confirm")
    if r.returncode != 0:
        return Result(FAIL, r.stderr.strip()[-400:])
    after = hashlib.sha256(target.read_bytes()).hexdigest()
    if before != after:
        return Result(FAIL, "ui_confirm.wav changed on regeneration — synthesis is not "
                            "deterministic, so the bank cannot be trusted to rebuild")
    return Result(OK, "ui_confirm byte-identical")


## PRC-18. CLAUDE.md's fifth non-negotiable — "loudness-match audio, never peak-normalize" —
## had no check at all: `check_sfx_reproducible` above verifies byte-identity on regeneration,
## a determinism check, not a loudness one. `tools/audio/loudness.py` (merged separately,
## standalone, `--only sfx`/`--only music`) is a real BS.1770 integrated-LUFS implementation
## with a band anchored to the committed bank's OWN measured spread — see that module's
## docstring for how the band was derived, what it validates against, and what it can and
## cannot catch. This just calls it and reports the verdict: "call it, do not edit it" is
## this session's own contract for everything under tools/audio/.
##
## Split exactly like `sfx determinism`/`music manifest` above, for the same reason: the cost
## is almost entirely in music. Measured on this machine: sfx alone ~2.0 s (86 files), music
## alone ~85.1 s (14 tracks, full BS.1770 block/gate procedure over several-minute stereo
## files). `sfx loudness` joins tier 2 next to its cheap sibling checks; `music loudness`
## goes to tier 4 — at 85 s it would blow tier 2's entire 25 s budget on its own, and even
## tier 3's ~66 s claim (itself already understated — see the `SCENARIO_FILES` comment above)
## would nearly double from this one check alone. Nightly/release is where an 85 s check
## belongs when nothing about it needs PR-turnaround urgency.
def _run_loudness_check(category: str, timeout: float) -> Result:
    script = ROOT / "tools" / "audio" / "loudness.py"
    if not script.exists():
        return Result(SKIP, "tools/audio/loudness.py missing")
    r = run(PY, str(script), "--only", category, timeout=timeout)
    if r.returncode not in (0, 1):
        return Result(FAIL, f"loudness.py exited {r.returncode}: "
                            + (r.stdout + r.stderr).strip()[-1500:])
    if r.returncode == 1:
        return Result(FAIL, r.stdout.strip()[-1500:])
    tail = next((l for l in r.stdout.splitlines() if "file(s) measured" in l), "")
    return Result(OK, tail.strip() or f"{category} within band")


def check_sfx_loudness() -> Result:
    """`tools/audio/loudness.py --only sfx` — measured ~2.0 s, 86 files. See the module
    comment above `_run_loudness_check` for the full split/tier reasoning."""
    return _run_loudness_check("sfx", timeout=60.0)


def check_music_loudness() -> Result:
    """`tools/audio/loudness.py --only music` — measured ~85.1 s, 14 tracks. See the module
    comment above `_run_loudness_check` for the full split/tier reasoning."""
    return _run_loudness_check("music", timeout=180.0)


def check_music_manifest() -> Result:
    man = ROOT / "assets" / "audio" / "music_manifest.json"
    if not man.exists():
        return Result(SKIP, "no music manifest")
    doc = json.loads(man.read_text())
    missing = [t["id"] for t in doc["tracks"]
               if not (ROOT / "assets" / "audio" / t["file"]).exists()]
    if missing:
        return Result(FAIL, f"manifest references missing ogg: {', '.join(missing)}")
    no_hash = [t["id"] for t in doc["tracks"] if not t.get("source_sha256")]
    if no_hash:
        return Result(FAIL, f"no master sha256 recorded for: {', '.join(no_hash)} — "
                            f"masters are not versioned, so the hash is the only proof")
    return Result(OK, f"{len(doc['tracks'])} tracks")


## How many commits may land on main before the build journal is considered stale. The
## journal is meant to be written DURING the work, one entry per pull request — it was
## batched into three bulk "editions" at the end of a session instead, which produces a
## story written ABOUT the work rather than during it, and loses exactly the pivots and
## wrong turns that are the most interesting part. This is the mechanical version of that
## rule, because the remembered version failed on its first outing.
CHRONICLE_STALE_AFTER = 12


def check_chronicle_current() -> Result:
    """The build journal has an entry covering roughly the work that has landed.

    Warns rather than fails, and deliberately: a chronicle entry is a judgement about what
    was worth recording, and a gate that hard-fails on it would get satisfied with an empty
    entry, which is worse than a late one. What it cannot do is stay silent.
    """
    rec = ROOT / "docs" / "chronicle" / "chronicle.json"
    if not rec.exists():
        return Result(SKIP, "no chronicle")
    try:
        entries = json.loads(rec.read_text()).get("entries", [])
    except Exception as exc:
        return Result(FAIL, f"chronicle.json does not parse: {exc}")
    if not entries:
        return Result(FAIL, "chronicle.json has no entries")
    ## A commit record is {"hash": ..., "subject": ...}, not a bare string — read the
    ## shape rather than assuming it; assuming it is what made this check raise on its
    ## first run.
    cited = {c["hash"] if isinstance(c, dict) else c
             for e in entries for c in (e.get("commits") or [])}
    ## Subjects, not just hashes — LF-184. Matching on hash alone was broken for the entire
    ## life of this check and nobody noticed, because it can only ever succeed by accident:
    ##
    ##   1. The chronicler is dispatched BEFORE the coordinator commits, so it can read the
    ##      working-tree diff it is describing. Every entry is therefore authored with no
    ##      commit hash to cite at all.
    ##   2. Even when the hash is backfilled afterwards, `gh pr merge --squash` mints a NEW
    ##      commit on `main`. The branch hash the entry cites is never in `git log` of the
    ##      merged history, so the check walks straight past it.
    ##
    ## Measured: with hash-only matching this reported "15 commits since the newest
    ## journalled one" on a `main` where the newest four commits each had their own entry.
    ## The one check meant to stop the journal going quiet was blind to every entry written
    ## the way the ship skill tells you to write them.
    ##
    ## A subject survives the squash — `gh` appends " (#106)" and keeps the rest — so the
    ## subject is the stable identity across a merge and the hash is not. Hashes stay in the
    ## match because a non-squashed commit is still legitimate and cheap to check.
    cited_subjects = {(c.get("subject") or "").strip()
                      for e in entries for c in (e.get("commits") or [])
                      if isinstance(c, dict) and (c.get("subject") or "").strip()}
    if not cited and not cited_subjects:
        return Result(OK, f"{len(entries)} entries (none cite a commit)")
    r = subprocess.run(["git", "log", "--format=%h%x00%s", "-60"],
                       capture_output=True, text=True, cwd=str(ROOT))
    log = [ln.split("\x00", 1) for ln in r.stdout.splitlines() if "\x00" in ln]
    behind = 0
    for h, subj in log:
        if any(c.startswith(h) or h.startswith(c) for c in cited):
            break
        ## `git log` gives "<subject> (#106)" after a squash; the entry cites the bare
        ## subject, so the log line starts with it rather than equalling it.
        if any(subj.startswith(s) for s in cited_subjects):
            break
        behind += 1
    else:
        behind = len(log)
    if behind > CHRONICLE_STALE_AFTER:
        return Result(FAIL, f"{behind} commits since the newest journalled one — the "
                            f"journal is meant to be written per pull request, not batched "
                            f"at the end ({len(entries)} entries)")
    return Result(OK, f"{len(entries)} entries, {behind} commit(s) behind")


def check_backlog_rendered() -> Result:
    store, doc = ROOT / "backlog.json", ROOT / "docs" / "BACKLOG.md"
    if not store.exists():
        return Result(SKIP, "no backlog")
    if not doc.exists():
        return Result(FAIL, "docs/BACKLOG.md missing — run tools/backlog.py render")
    if store.stat().st_mtime > doc.stat().st_mtime + 1:
        return Result(FAIL, "docs/BACKLOG.md is stale — run tools/backlog.py render")
    n = len([i for i in json.loads(store.read_text())["items"]
             if i["status"] in ("open", "wip")])
    return Result(OK, f"{n} open")


## The model every subagent definition must name. THE PIN IS THE INVARIANT, NOT THE VALUE:
## an agent with no `model:` key silently inherits the parent, so the failure this check
## exists to prevent is an *unstated* model, not an expensive one. Decision 077 moved the
## value from `sonnet` to `opus` at the owner's instruction; decision 051's reasoning for
## having a pin at all is untouched by that. One constant, so the value is changed here.
AGENT_MODEL = "opus"


def check_agent_models() -> Result:
    """Every subagent definition pins its model, because an unpinned one is a silent default.

    An agent file with no `model:` key in its frontmatter inherits the parent model — so a
    five-way fan-out silently costs five parent-model contexts. That is not a theoretical: it
    spilled the owner's subscription usage into paid credits, which is why this is a gate
    check and not a note. The pin is `AGENT_MODEL` for all of them, and it is now `opus`
    (decision 077) — which makes the pin *more* load-bearing, not less: with the expensive
    model chosen deliberately, an agent that drifts off the pin is a cost surprise in either
    direction, and the gate is the only thing that would notice.

    Parsed rather than grepped: awk's record counter persists across files, so the obvious
    one-liner inspects only the first agent and passes no matter what the rest say.
    """
    agents = sorted((ROOT / ".claude" / "agents").glob("*.md"))
    if not agents:
        return Result(SKIP, "no agent definitions")
    unpinned, wrong = [], []
    for path in agents:
        lines = path.read_text().splitlines()
        if not lines or lines[0].strip() != "---":
            unpinned.append(path.stem)
            continue
        try:
            close = next(i for i, l in enumerate(lines[1:], 1) if l.strip() == "---")
        except StopIteration:
            unpinned.append(path.stem)
            continue
        model = None
        for line in lines[1:close]:
            if line.startswith("model:"):
                model = line.split(":", 1)[1].strip()
                break
        if model is None:
            unpinned.append(path.stem)
        elif model != AGENT_MODEL:
            wrong.append(f"{path.stem}={model}")
    if unpinned:
        return Result(FAIL, f"no model pinned (silently inherits the parent): "
                            f"{', '.join(unpinned)}")
    if wrong:
        return Result(FAIL, f"model is not {AGENT_MODEL}: {', '.join(wrong)}")
    return Result(OK, f"{len(agents)} agents pinned to {AGENT_MODEL}")


HOOK_EVENTS: tuple[str, ...] = ("PreToolUse", "PostToolUse", "SubagentStop", "SessionEnd")


def check_hooks_configured() -> Result:
    """PRC-06. `.claude/settings.json`'s hook wiring and `tools/hooks/guard.py` encode six
    working-agreement rules that each cost a real diagnosis pass this session (LF-075's
    blanked level, LF-133's session-scoped `--kill`, a `git stash` that swept eleven files
    across five workstreams, a bypassed `toolpaths.py` that silently processed nothing).
    A silently dropped hook or a typo in `settings.json` would take all six away with no
    signal at all — the same shape `agent models` (above) already guards against for a
    different silent-Opus-fallback failure, and the same "everyone remembers to check a
    text file is not a control" reasoning applies here.

    Checked mechanically rather than by provoking a live hook: `guard.py`'s own module
    docstring records that live firing is UNPROVEN — LF-112 found a hook added mid-session
    was never observed firing, most likely because `settings.json` is read once at session
    start, so "did this fire just now" cannot be asked from inside this process at all.
    What CAN be asserted here, and is: the file parses as JSON, every one of the four
    events `guard.py` actually handles (`evaluate()`'s own dispatch) is wired to *something*,
    the target script exists, and its rule table is internally self-consistent
    (`--selftest`, which drives `evaluate()` directly against a fixed case table covering
    every rule in both directions — no live hook firing required, see that flag's own
    docstring for why).
    """
    settings = ROOT / ".claude" / "settings.json"
    guard = ROOT / "tools" / "hooks" / "guard.py"
    if not settings.exists():
        return Result(SKIP, "no .claude/settings.json")
    try:
        doc = json.loads(settings.read_text())
    except Exception as exc:  # noqa: BLE001
        return Result(FAIL, f".claude/settings.json does not parse: {exc}")
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        return Result(FAIL, '.claude/settings.json has no top-level "hooks" object')
    missing_events = [e for e in HOOK_EVENTS if not hooks.get(e)]
    if missing_events:
        return Result(FAIL, f".claude/settings.json is missing hook wiring for: "
                            f"{', '.join(missing_events)}")
    if not guard.exists():
        return Result(FAIL, "tools/hooks/guard.py missing, but settings.json wires it")
    r = run(PY, str(guard), "--selftest", timeout=30.0)
    if r.returncode != 0:
        return Result(FAIL, (r.stdout + r.stderr).strip()[-1500:])
    m = re.search(r"(\d+) passed, (\d+) failed, (\d+) total", r.stdout)
    tally = f"guard.py --selftest {m.group(1)}/{m.group(3)} ok" if m \
        else "guard.py --selftest exit 0"

    # Every OTHER wired hook is checked too — that its target script exists and compiles.
    # `HOOK_EVENTS` above is deliberately scoped to the four events `guard.py` dispatches,
    # so a hook pointing at any other script was previously invisible here: it could be
    # deleted, renamed or left with a syntax error and this check would still report a
    # cheerful "4 events wired". That is precisely the silent-drop failure the whole check
    # exists to prevent, one script over. A missing file or a SyntaxError is caught by
    # `py_compile` without running anything — these hooks fire at session start and before
    # compaction, neither of which can be provoked from inside this process (LF-112).
    extra: list[str] = []
    for event, groups in sorted(hooks.items()):
        if event in HOOK_EVENTS or not isinstance(groups, list):
            continue
        for group in groups:
            for hook in (group or {}).get("hooks", []):
                cmd = hook.get("command", "")
                for token in cmd.split():
                    if not token.endswith(".py"):
                        continue
                    target = ROOT / token
                    if not target.exists():
                        return Result(FAIL, f"{event} hook points at a missing script: {token}")
                    c = run(PY, "-m", "py_compile", str(target), timeout=30.0)
                    if c.returncode != 0:
                        return Result(FAIL, f"{event} hook script does not compile ({token}): "
                                            f"{(c.stdout + c.stderr).strip()[-400:]}")
                    extra.append(event)
    suffix = f" · +{len(extra)} more wired ({', '.join(sorted(set(extra)))}), scripts compile" \
        if extra else ""
    return Result(OK,
                  f"{len(HOOK_EVENTS)} events wired ({', '.join(HOOK_EVENTS)}) · {tally}{suffix}")


def check_asset_coverage() -> Result:
    """PRC-14. `sprite coverage` (above) already catches one direction of the asset<->data
    coupling — a data id with no sprite. It cannot catch the other: a rendered asset no
    data id references, which quietly grows the atlas with a dead cell and nothing says so.
    `tools/blender/gen_assets.py` was written this session specifically to be the
    bidirectional check (see its own module docstring), so this calls its `check()`
    directly rather than re-deriving the id<->asset naming convention a second time here.

    A sibling of `sprite coverage`, not a merge into it: the two checks compare against
    different sources of truth at different pipeline stages. `sprite coverage` reads
    `assets/renders/sprites.json`, the *rendered* manifest — "has this actually been
    rendered". This reads `render.py`'s `ASSETS` dict via `ast` — no Blender, no manifest
    needed at all — "is this *going* to render to something no data id will ever ask for".
    Conflating them would make a missing manifest (nothing rendered yet) silently mask the
    orphan direction, which is exactly the failure mode this exists to prevent. Tier 1
    because, like `gen_assets.py`'s own docstring stresses, this needs no Blender process
    and no manifest on disk — it is pure `ast`/`json`, comparably cheap to `sprite
    coverage`'s own JSON read.
    """
    script = ROOT / "tools" / "blender" / "gen_assets.py"
    if not script.exists():
        return Result(SKIP, "tools/blender/gen_assets.py missing")
    sys.path.insert(0, str(ROOT / "tools" / "blender"))
    try:
        import gen_assets                                          # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return Result(FAIL, f"cannot import gen_assets: {exc}")
    missing, orphaned = gen_assets.check()
    if missing or orphaned:
        parts = []
        if missing:
            parts.append("no render asset for data id(s): " + ", ".join(missing))
        if orphaned:
            parts.append("render asset(s) claimed by no data id, growing the atlas for "
                         "nothing: " + ", ".join(orphaned))
        return Result(FAIL, "; ".join(parts))
    tower_ids, enemy_ids = gen_assets.data_ids()
    n_ids = len(tower_ids) + len(enemy_ids)
    n_assets = len(gen_assets.render_asset_names())
    return Result(OK, f"{n_ids} data ids <-> {n_assets} render assets, both directions clean")


def check_facing_harness() -> Result:
    """LF-142. `tools/yaw_band.py` and `scripts/test/facing.gd` read `anchor_sim.gd`'s
    `wave_queue()`/`spawn()` tuple shapes directly, with no shared accessor between them —
    and broke silently once already this session, when the multi-lane migration changed
    `wave_queue()`'s tuple to `[time, lane, enemy_id]`. Neither file runs in any gate tier,
    so the next drift is discovered by whoever next needs a facing measurement, mid-task,
    exactly like this time.

    A smoke run, not a measurement: the narrowest possible sweep (one yaw count, one
    hysteresis fraction) against anchor-07 — decision 049's own anchor — asserting only
    that the harness still runs end to end and reports both an `EMPLACEMENTS` row and a
    `UNITS` row, never that any number in them is right (that is `tools/yaw_band.py`'s own
    job, run by hand). A tuple-shape mismatch surfaces as `facing.gd` erroring out (the
    wrong argument count/type into `spawn()`/`wave_queue()`) rather than as a wrong number,
    so "exited 0 and printed what it always prints" is exactly the right level of assertion
    here — anything sharper would be re-deriving `yaw_band.py`'s own analysis.
    """
    script = ROOT / "tools" / "yaw_band.py"
    if not script.exists():
        return Result(SKIP, "tools/yaw_band.py missing")
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    r = run(PY, str(script), "--anchor", "anchor-07", "--yaws", "4", "--frac", "0",
            timeout=60.0)
    if r.returncode != 0:
        return Result(FAIL, (r.stdout + r.stderr).strip()[-1200:])
    if "EMPLACEMENTS" not in r.stdout or "UNITS" not in r.stdout:
        return Result(FAIL, "yaw_band.py exited 0 but did not report both an EMPLACEMENTS "
                            "and a UNITS row — the harness ran but did not produce its "
                            "usual shape:\n" + r.stdout.strip()[-800:])
    return Result(OK, "yaw_band.py + facing.gd smoke ok (anchor-07, yaws=4, frac=0)")


## Every launch site docs/issues/PRC-07-reaper-leases.md names, mapped to the tool name its
## `lease.acquire()`/`acquire_capture()` call is expected to carry — the tool name is not
## load-bearing (nothing keys off it), it just makes a false positive impossible to get
## from an unrelated `lease.acquire(` string elsewhere in the file matching by accident.
LEASE_SITES: dict[str, str] = {
    "tools/shot.py": "shot",
    "tools/check.py": "check",
    "tools/test_parity.py": "test-parity",
    "tools/blender/build.py": "blender-build",
    "tools/audio/serve.py": "audio-serve",
    # The autoloop control panel: a second long-lived HTTP server, and the reason its lease
    # matters is that everything the loop spawns underneath it walks the ppid chain looking
    # for one. It is bounded by --idle rather than by the reaper — see its module docstring
    # for why it is deliberately absent from reap.py's OUR_TOOLS.
    "tools/autoloop_web.py": "autoloop-web",
    "sim/run.py": "sim-run",
    "tools/sweep.py": "sweep",
}


def check_leases_wired() -> Result:
    """Every subprocess/pool launch site PRC-07 named goes through `tools/lease.py`'s
    `acquire()` or `acquire_capture()`, so `tools/reap.py` can tell a leaked process from
    one legitimately mid-capture for another session instead of killing both alike.

    Grepped rather than executed — same argument as `agent models`: a launch site that
    forgot to wrap itself is a fact about the source right now, not something that needs a
    live capture to notice, and by the time a live capture would have caught it, a sibling
    agent may already have lost a process to it.
    """
    missing = []
    for rel, tool in LEASE_SITES.items():
        p = ROOT / rel
        if not p.exists():
            missing.append(f"{rel} (file missing)")
            continue
        text = p.read_text()
        if "lease.acquire(" not in text and "lease.acquire_capture(" not in text:
            missing.append(rel)
    if missing:
        return Result(FAIL, f"no lease.acquire()/acquire_capture() found: "
                            f"{', '.join(missing)}")
    return Result(OK, f"{len(LEASE_SITES)} launch sites leased")


def check_issue_traceability() -> Result:
    """Every `Closes:` trailer on this branch is backed by the repository (LF-212).

    Proven live rather than argued: pull request #130 merged with the body line
    ``Closes `PLC-03` `` and issue #31 was still OPEN thirty-seven minutes later, because
    GitHub acts only on `Closes #<number>` and reads a backticked spec id as prose. Across
    the 51 merged pull requests available at the time, *zero* carried a closing keyword
    GitHub could resolve.

    This is the half of the fix that can be checked offline, and it covers the half that
    lives in this repository. A `Closes: LF-nnn` trailer asserts that `backlog.json` marks
    that item done **in this branch**, so merging the pull request is what lands the close
    and there is no second step for anyone to forget. A `Closes: PLC-03` trailer asserts
    only that the spec has a GitHub issue to close; whether the pull request *body* carries
    the resolved `Closes #31` is a fact about GitHub, not about the working tree, so it is
    enforced by `tools/traceability.py check-pr` in `gate.yml` where the body is readable.

    Tier 1: it is a `git log` and two JSON reads. A branch with no trailer passes — plenty
    of pull requests close nothing — and a shallow clone with no reachable merge base
    reports what it could not determine rather than a pass.
    """
    tool = ROOT / "tools" / "traceability.py"
    if not tool.exists():
        return Result(SKIP, "tools/traceability.py missing")
    r = run(sys.executable, str(tool), "check", timeout=60)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        return Result(FAIL, out or "traceability check failed")
    ## The tool prints its own one-line verdict; the gate shows it verbatim rather than
    ## re-summarising, so a "skip: no merge base" cannot be mistaken for a pass.
    return Result(OK, out.splitlines()[-1] if out else "no close declared")


def _strip_gd_comment(line: str) -> str:
    """A GDScript source line with its trailing `#` comment removed, string contents left
    alone. A plain substring/regex scan over raw source cannot tell a real reference from
    one inside a comment or a string literal — and `scripts/anchor_sim.gd` is full of prose
    *about* the exact identifiers this file's checks ban, discussing why they must not
    appear (see `check_autoload_in_rules`). This is a small hand-rolled quote-tracking
    scanner rather than a regex, because a regex `#.*$` would itself be fooled by a `#`
    inside a string.
    """
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ('"', "'"):
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "#":
            break
        out.append(c)
        i += 1
    return "".join(out)


## BAL-07 / LF-106. PRD §2.1 measured 100,000 float64 samples across five value regimes, 24
## operations, raw IEEE-754 bytes compared on CPython, Linux Godot 4.7.1 and Windows Godot
## 4.7.1: `+ - * / sqrt fmod floor min max` and comparisons are bit-identical on all three —
## decision 030 is partly superseded here, because it banned sqrt and the measurement clears
## it (IEEE-754 requires sqrt to be correctly rounded; 100,000/100,000 matched). These seven
## are not bit-identical, so they are banned in the rules:
SAFE_OPS_DIVERGENCE: dict[str, str] = {
    "atan2": "0.084%", "sin": "0.133%", "cos": "0.120%", "pow": "0.130%",
    "log": "0.031%", "exp": "0.069%", "tan": "4.32%",
}
## The actual LF-106 culprit was never a bare `sqrt` — it was `Vector2.distance_to`, one of
## Vector2's own geometry methods, which run in Godot's `real_t` (float32): for 2,000,000
## points placed exactly on an integer radius, float32 and float64 disagreed on the `<= r`
## test 10.2% of the time. Bare `Vector2`/`Vector2i` *values* are not banned by this check —
## `scripts/anchor_sim.gd`'s `point_at()` returns one for drawing, and its `built`/
## `shot_fired`/`unit_damaged`/`splash_landed` signals carry them to presentation code, all
## shipped and correct (decision 030) — banning the type outright would redden that on sight,
## which is the exact "over-scope and get disabled" failure BAL-07's own risk note warns
## about. What is banned is the specific float32-lossy methods a rule could route a real
## comparison through.
SAFE_OPS_VECTOR_METHODS: tuple[str, ...] = (
    "distance_to", "length", "normalized", "angle", "rotated",
)
SAFE_OPS_VECTOR_DIVERGENCE = "10.2%"

## The rules exist twice — see decision 030 and `check_rules_parity` — and this is the file
## list both engines and their harness are built from. `anchor_view.gd`, `iso.gd` and
## `fx_additive.gd` are deliberately excluded: facing and yaw are presentation-only
## (decision 049) and legitimately use trigonometry.
## PLC-03 added `scripts/test/arc_parity.gd` for the same reason `parity.gd` is already
## here and `iso.gd` is not: it runs the rules inside Godot, so a divergent operation
## introduced there would move a result the gate then reports as agreement. The issue's
## own task list says this check must scan "only anchor_sim.gd and engine.py"; what that
## bullet is protecting against is `iso.gd`'s legitimate trigonometry being flagged, and
## a rules HARNESS is on the rules side of that line.
## LF-244 added `scripts/test/verb_parity.gd` on the identical reasoning as
## `arc_parity.gd`: it drives the rules inside Godot, so a divergent operation introduced
## there would move a result this gate then reports as agreement.
SAFE_OPS_SCOPE: list[str] = ["scripts/anchor_sim.gd", "sim/engine.py",
                             "scripts/test/parity.gd", "scripts/test/arc_parity.gd",
                             "scripts/test/verb_parity.gd"]

SAFE_OPS_EXEMPT_MARKER = "safe-ops-exempt"


def _safe_ops_gd_violations(rel: str) -> tuple[list[str], int]:
    """Banned-operation hits in one GDScript rules file, plus its exemption count."""
    path = ROOT / rel
    fn_re = re.compile(r"(?<![.\w])(" + "|".join(SAFE_OPS_DIVERGENCE) + r")\s*\(")
    vec_re = re.compile(r"\.(" + "|".join(SAFE_OPS_VECTOR_METHODS) + r")\s*\(")
    hits: list[str] = []
    exempt = 0
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        if SAFE_OPS_EXEMPT_MARKER in raw:
            exempt += 1
            continue
        line = _strip_gd_comment(raw)
        for m in fn_re.finditer(line):
            name = m.group(1)
            hits.append(f"{rel}:{lineno}: `{name}(` is banned in the rules — measured "
                        f"{SAFE_OPS_DIVERGENCE[name]} divergence between Windows Godot and "
                        f"CPython over 100,000 samples (BAL-07): {raw.strip()}")
        for m in vec_re.finditer(line):
            name = m.group(1)
            hits.append(f"{rel}:{lineno}: `.{name}(` runs through Vector2's float32 math — "
                        f"measured {SAFE_OPS_VECTOR_DIVERGENCE} divergence against float64 "
                        f"on an exact-boundary test (BAL-07): {raw.strip()}")
        if "**" in line:
            hits.append(f"{rel}:{lineno}: `**` (pow) is banned in the rules — measured "
                        f"{SAFE_OPS_DIVERGENCE['pow']} divergence (BAL-07): {raw.strip()}")
    return hits, exempt


def _safe_ops_py_violations(rel: str) -> tuple[list[str], int]:
    """Banned-operation hits in the Python rules file, via `ast` rather than a regex — a
    regex would miss `from math import sin as s` and false-positive on the word "cos" in a
    comment (both called out in BAL-07's own risk notes)."""
    import ast                                                     # noqa: PLC0415

    path = ROOT / rel
    text = path.read_text()
    exempt_lines = {i for i, line in enumerate(text.splitlines(), 1)
                    if SAFE_OPS_EXEMPT_MARKER in line}
    tree = ast.parse(text, filename=rel)

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "math":
            for alias in node.names:
                if alias.name in SAFE_OPS_DIVERGENCE:
                    aliases[alias.asname or alias.name] = alias.name

    hits: list[str] = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            name = "pow"
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in SAFE_OPS_DIVERGENCE:
                name = fn.attr
            elif isinstance(fn, ast.Name):
                if fn.id in SAFE_OPS_DIVERGENCE:
                    name = fn.id
                elif fn.id in aliases:
                    name = aliases[fn.id]
        if name and getattr(node, "lineno", None) not in exempt_lines:
            hits.append(f"{rel}:{node.lineno}: `{name}` is banned in the rules — measured "
                        f"{SAFE_OPS_DIVERGENCE[name]} divergence between Windows Godot and "
                        f"CPython over 100,000 samples (BAL-07)")
    return hits, len(exempt_lines)


def check_safe_ops() -> Result:
    """The rules use none of `atan2 sin cos tan pow log exp` today, so cross-platform parity
    holds by accident rather than by design (BAL-07, LF-106) — this makes it hold by design.

    `+ - * / sqrt fmod floor min max` and comparisons are bit-identical across CPython,
    Linux Godot and Windows Godot over 100,000 samples; the seven above are not, up to
    `tan`'s 4.32%. Decision 030 is partly superseded: it banned `sqrt`, but `sqrt` matched
    100,000/100,000 — the real culprit was `Vector2.distance_to`, a float32 helper, which is
    why this checks the *specific* lossy Vector2 methods rather than the type itself. See
    `SAFE_OPS_VECTOR_METHODS` above for why bare `Vector2`/`Vector2i` values are in scope but
    not banned outright.

    Scope is exactly `SAFE_OPS_SCOPE` — the rules and their parity harness — never
    `anchor_view.gd`/`iso.gd`/`fx_additive.gd`, which are presentation and legitimately use
    trigonometry (decision 049). A `# safe-ops-exempt: <reason>` marker on the line is the
    escape hatch, matching `nomenclature-exempt`'s idiom.
    """
    missing = [rel for rel in SAFE_OPS_SCOPE if not (ROOT / rel).exists()]
    if missing:
        return Result(SKIP, f"scope file(s) missing: {', '.join(missing)}")

    hits: list[str] = []
    exempt = 0
    for rel in SAFE_OPS_SCOPE:
        if rel.endswith(".py"):
            h, e = _safe_ops_py_violations(rel)
        else:
            h, e = _safe_ops_gd_violations(rel)
        hits += h
        exempt += e

    if hits:
        return Result(FAIL, "\n".join(hits))
    exempt_note = f", {exempt} exemption(s)" if exempt else ""
    return Result(OK, f"{len(SAFE_OPS_SCOPE)} files clean ({', '.join(SAFE_OPS_SCOPE)})"
                      f"{exempt_note}")


def check_autoload_in_rules() -> Result:
    """LF-148. `scripts/test/parity.gd` preloads `scripts/anchor_sim.gd` as a `--script`
    MainLoop, where autoloads do not exist — so a reference to one makes the whole script
    fail to LOAD: `AnchorSimScript.new()` returns a bare `GDScript` with no `new()`, all
    1152 parity rows come back as empty dictionaries, and `test_parity.py` dies on
    `KeyError: 'anchor'`, naming itself rather than the offending line. `gdscript parses`
    (elsewhere in this file) stays green throughout, because `--check-only` resolves
    autoloads from `project.godot` and this runtime path never does.

    Autoload names are read out of `project.godot`'s `[autoload]` block (via
    `tools/validate/gdscript.py`'s existing parser — the same one `gdscript parses` already
    trusts) rather than hardcoded, so a new autoload does not need a matching change here.

    Comments are stripped before matching: `anchor_sim.gd` carries its own docstring
    *about* this exact trap — several lines discussing why `Recoveries` must not appear —
    and a plain substring scan would flag its own warning.
    """
    target = ROOT / "scripts" / "anchor_sim.gd"
    if not target.exists():
        return Result(SKIP, "scripts/anchor_sim.gd missing")

    sys.path.insert(0, str(ROOT / "tools" / "validate"))
    import gdscript                                                # noqa: PLC0415

    autoloads = gdscript.parse_project_autoloads(ROOT / "project.godot")
    if not autoloads:
        return Result(SKIP, "no autoloads declared in project.godot")

    pat = re.compile(r"\b(" + "|".join(re.escape(a) for a in sorted(autoloads)) + r")\b")
    hits = []
    for lineno, raw in enumerate(target.read_text().splitlines(), 1):
        line = _strip_gd_comment(raw)
        m = pat.search(line)
        if m:
            hits.append(f"scripts/anchor_sim.gd:{lineno}: references autoload "
                        f"`{m.group(1)}` — parity.gd preloads this file as a --script "
                        f"MainLoop where autoloads do not exist, so it fails to LOAD "
                        f"(LF-148): {raw.strip()}")
    if hits:
        return Result(FAIL, "\n".join(hits))
    return Result(OK, f"clean against {len(autoloads)} autoload(s): "
                      f"{', '.join(sorted(autoloads))}")


## LF-247. The strip-geometry expressions this check mirrors, pinned verbatim from
## `scripts/ui_theme.gd`. Whitespace-normalised before comparing, so reindenting is free and
## changing the arithmetic is not: a rewrite makes this check FAIL by name instead of quietly
## continuing to evaluate a formula the game no longer uses. That is the whole reason a
## text-only check is safe to trust here — the mirror cannot silently go stale, only loudly.
STRIP_FORMULA = {
    "threat_docked": "return vp.x - 2.0 * COL_X - COL_W - THREAT_W >= PLAYFIELD_MIN_W",
    "reserved_w":    "return COL_W + (THREAT_W if threat_docked(vp) else 0.0)",
    "gutter":        "return clampf((vp.x - reserved_w(vp)) / 3.0, 4.0, COL_X)",
    "strip_w":       "return maxf(vp.x - 2.0 * gutter(vp) - reserved_w(vp), 1.0)",
}


def _gd_float_consts(text: str, names: list[str]) -> dict[str, float]:
    """`const NAME := <float literal | already-known identifier>` out of a GDScript file.

    The identifier case is not incidental: `PLAYFIELD_MIN_W := COL_W` is the *derivation*
    LF-247 rests on — the board is at least as wide as the narrowest instrument panel — and
    resolving it to a literal here would turn a stated relationship back into a magic number,
    which is the mistake the whole check exists to catch.
    """
    out: dict[str, float] = {}
    for name in names:
        m = re.search(rf"^const {name}\s*:=\s*([A-Za-z_][A-Za-z_0-9]*|[0-9]*\.?[0-9]+)",
                      text, re.M)
        if not m:
            continue
        tok = m.group(1)
        try:
            out[name] = float(tok)
        except ValueError:
            if tok in out:
                out[name] = out[tok]
    return out


def check_playfield_width() -> Result:
    """LF-247. The playfield must be at least `Ui.PLAYFIELD_MIN_W` wide at every interface
    scale the options screen offers. Nothing measured it, and that is the entire reason a
    4 px board shipped.

    The failure it exists for, in full, because it is instructive. `COL_W` (420) and
    `THREAT_W` (528) are 948 px of instrument panel that do **not** shrink when the interface
    scale shrinks the logical viewport, so the strip between them ran 940 px at 100%, 556 at
    125%, 300 at 150%, 117 at 175% and **4 px at 200%** — the two panels covered the board
    completely on every anchor and the board was drawn behind them. Every accessibility check
    passed throughout: decision 050 measured the panels' *vertical* fit exhaustively and made
    both scroll, which is correct for WCAG SC 1.4.4/1.4.10 and is about **text**;
    `tools/validate/a11y.py` audits text items, and a playfield is not a text item. So
    `accessibility` reported 192 items clean and `scenario a11y-worst` — which runs at
    `ui_scale: 2.0` on anchor-24 *precisely because it is the worst case* — asserted
    `hud.selected` plus a text inventory and passed. The general lesson is the same one
    `firing arcs agree` records: a check that runs the whole game proves nothing about a
    quantity nothing in it ever asserts on.

    Tier 1 and text-only. It parses the constants out of `scripts/ui_theme.gd` and the scale
    ladder out of `scripts/display_settings.gd`, mirrors the four geometry expressions in
    `STRIP_FORMULA` above, and pins those expressions verbatim so the mirror cannot drift
    into vacuous agreement. No Godot, no frame, ~0 ms — which matters, because tier 2 is
    already over its own budget (LF-178) and the cheapest place for a bound like this is the
    tier that runs before every commit.

    The viewport width used is `viewport_width / scale`, and that is the worst case rather
    than an assumption: the project stretches `canvas_items` with aspect `expand`, so an
    aspect narrower than 16:9 keeps the base width and grows height, and a wider one grows
    width. Neither can make the strip narrower than this.
    """
    ui = ROOT / "scripts" / "ui_theme.gd"
    disp = ROOT / "scripts" / "display_settings.gd"
    proj = ROOT / "project.godot"
    for p in (ui, disp, proj):
        if not p.exists():
            return Result(SKIP, f"{p.relative_to(ROOT)} missing")
    ui_text = ui.read_text()

    consts = _gd_float_consts(ui_text, ["COL_X", "COL_W", "THREAT_W", "PLAYFIELD_MIN_W"])
    missing = [n for n in ("COL_X", "COL_W", "THREAT_W", "PLAYFIELD_MIN_W")
               if n not in consts]
    if missing:
        return Result(FAIL, f"scripts/ui_theme.gd: could not read {', '.join(missing)} — "
                            f"LF-247's bound has nothing to check")

    # Pin the formulas before evaluating the mirror of them.
    drifted = []
    for fn, expected in STRIP_FORMULA.items():
        m = re.search(rf"^func {fn}\(.*?^\t(return .*?)$", ui_text, re.M | re.S)
        got = " ".join(m.group(1).split()) if m else None
        if got != expected:
            drifted.append(f"{fn}(): expected `{expected}`, found "
                           f"{'`' + got + '`' if got else 'no return statement'}")
    if drifted:
        return Result(FAIL, "scripts/ui_theme.gd's strip geometry no longer matches the "
                            "mirror in check.py's STRIP_FORMULA, so this check would be "
                            "measuring a formula the game does not use — update both "
                            "together: " + "; ".join(drifted))

    m = re.search(r"const UI_SCALES\s*:\s*Array\[float\]\s*=\s*\[([^\]]*)\]",
                  disp.read_text())
    if not m:
        return Result(FAIL, "scripts/display_settings.gd: no UI_SCALES declaration — the "
                            "set of scales this check is supposed to cover is unknown")
    scales = [float(s) for s in m.group(1).replace(" ", "").split(",") if s]

    pm = re.search(r"^window/size/viewport_width=(\d+)", proj.read_text(), re.M)
    if not pm:
        return Result(FAIL, "project.godot: no window/size/viewport_width")
    base_w = float(pm.group(1))

    col_x, col_w = consts["COL_X"], consts["COL_W"]
    threat_w, floor_w = consts["THREAT_W"], consts["PLAYFIELD_MIN_W"]
    rows, bad = [], []
    for s in scales:
        vp_x = base_w / s
        docked = vp_x - 2.0 * col_x - col_w - threat_w >= floor_w
        reserved = col_w + (threat_w if docked else 0.0)
        gutter = min(max((vp_x - reserved) / 3.0, 4.0), col_x)
        strip = max(vp_x - 2.0 * gutter - reserved, 1.0)
        rows.append(f"{s:g}x {strip:.0f}px{'' if docked else ' (undocked)'}")
        if strip < floor_w:
            bad.append(f"{s:g}x interface scale: viewport {vp_x:.0f}px wide leaves a "
                       f"{strip:.0f}px playfield, under Ui.PLAYFIELD_MIN_W={floor_w:.0f}")
        # The recursion-avoidance argument in `Ui.threat_docked()` — it uses COL_X where
        # `gutter()` would, and claims the two agree wherever the answer is `true`. Asserted
        # rather than believed, because if it ever stopped holding the panel would dock at a
        # width at which docking does not fit, and the symptom would be the original bug.
        if docked and gutter != col_x:
            bad.append(f"{s:g}x: threat_docked() assumed a {col_x:.0f}px gutter but "
                       f"gutter() returns {gutter:.0f} — Ui.threat_docked()'s own "
                       f"self-consistency note no longer holds")
    if bad:
        return Result(FAIL, "; ".join(bad))
    return Result(OK, f"playfield >= {floor_w:.0f}px at all {len(scales)} scales: "
                      + ", ".join(rows))


def check_yaw_hysteresis() -> Result:
    """LF-141. `YAW_HYSTERESIS_FRAC` in `scripts/iso.gd` is a fraction of a bucket
    (decision 060) rather than a bare degree count, so it survives a `YAW_COUNT` change —
    but at or above 0.5 the band exceeds half a bucket and every emplacement's facing
    freezes permanently, the exact `LF-108` failure the fraction was introduced to prevent.
    `sprites.gd` already asserts this at boot; this is the gate-time equivalent so the
    failure shows up before the game ever runs.
    """
    path = ROOT / "scripts" / "iso.gd"
    if not path.exists():
        return Result(SKIP, "scripts/iso.gd missing")
    text = path.read_text()
    m = re.search(r"YAW_HYSTERESIS_FRAC\s*:\s*float\s*=\s*([0-9]*\.?[0-9]+)", text)
    if not m:
        return Result(FAIL, "scripts/iso.gd: could not find a YAW_HYSTERESIS_FRAC "
                            "declaration — LF-141's guard has nothing to check")
    value = float(m.group(1))
    lineno = text[:m.start()].count("\n") + 1
    if value >= 0.5:
        return Result(FAIL, f"scripts/iso.gd:{lineno}: YAW_HYSTERESIS_FRAC={value!r} is >= "
                            f"0.5 — at or above half a bucket every emplacement's facing "
                            f"freezes permanently (LF-108, LF-141)")
    return Result(OK, f"YAW_HYSTERESIS_FRAC={value!r} (< 0.5)")


def check_banned_terms() -> Result:
    """Nomenclature is a legal risk, so it gets a mechanical check, not a promise.

    nomenclature-exempt
    """
    nom = ROOT / "docs" / "NOMENCLATURE.md"
    if not nom.exists():
        return Result(SKIP, "no nomenclature bible")
    # Files that legitimately name these terms in order to forbid them carry the
    # marker below. An allowlist of paths would rot; a marker travels with the file.
    EXEMPT = "nomenclature-exempt"
    # Terms specific enough that any occurrence outside the bible is a real hit.
    banned = ["stargate", "goa'uld", "jaffa", "naquadah", "tok'ra", "ha'tak",
              "asgard", "chevron", "dial-home", "zero point module", "kawoosh"]
    hits = []
    # `.claude` used to hold agent worktrees — a second checkout of this repo, nomenclature
    # bible included, which the exact-path exemption below does not recognise and which
    # therefore failed the gate with six false hits against a file that is the authority on
    # those terms (LF-051). That was fixed with a hand-maintained SKIP_DIRS denylist; `git
    # ls-files` makes the false positive structurally impossible instead — an untracked
    # worktree is never "in this repository" to begin with, so there is nothing left to deny.
    # `addons/` is excluded as a pathspec rather than as a denylist entry, and the distinction
    # matters: this is not "a directory we forgot to skip", it is third-party code this project
    # did not author and could not fix if it ever did hit. The check exists to police OUR
    # naming — a vendored plugin turning the gate red for a word in someone else's source is a
    # red run nobody can act on. 121 tracked files, 0 hits today; the risk is the next upgrade.
    scan = [p for p in tracked("*.py", "*.json", "*.gd", "*.md", ":(exclude)addons/**")
            if p != nom]
    for p in scan:
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        if EXEMPT in text:
            continue
        low = text.lower()
        for term in banned:
            if term in low:
                hits.append(f"{p.relative_to(ROOT)}: '{term}'")
    return Result(FAIL, "\n".join(hits)) if hits else Result(OK, f"{len(scan)} files clean")


def check_gate_docs_consistent() -> Result:
    """This module's own docstring states a check count per tier in its `## Tiers` section
    ("tier N (...), C checks") — assert each against the live `CHECKS` registry rather than
    trust it. PRC-16: that prose drifted from `CHECKS` at least twice before this check
    existed — once when `scenarios pass` was added with no matching sentence (tier 3 stayed
    "26 checks" while `CHECKS` had 27), once when `chronicle current` landed at tier 1 without
    the bullet's list or count following it. Neither was a Godot problem or a data problem, so
    nothing else in this gate would ever have caught it.

    Deliberately narrow, per the issue's own risk note: this reads only the tier bullets'
    literal "tier N (...), C checks" phrasing, nothing else in the docstring, and it asserts
    *counts* only — a re-tier that moves a check between tiers without changing a total would
    not be caught by this alone, which is an accepted, smaller gap, not scope this check
    claims to close. A clever general doc-parser would just be a second thing to get wrong;
    this is a tripwire, not a documentation engine. Wall-clock costs are deliberately absent
    from the docstring entirely (see its own note) specifically so there is nothing numeric
    left for this check to have to assert and get wrong.
    """
    doc = __doc__ or ""
    pattern = re.compile(r"tier (\d)\s*\([^)]*\),\s*(\d+)\s*checks")
    found = pattern.findall(doc)
    if not found:
        return Result(FAIL, "no 'tier N (...), C checks' bullet found in the module "
                             "docstring's '## Tiers' section — did the phrasing change? "
                             "update check_gate_docs_consistent's regex if so")
    mismatches = []
    seen = set()
    for tier_s, stated_s in found:
        tier, stated = int(tier_s), int(stated_s)
        seen.add(tier)
        actual = len([c for c in CHECKS if c.tier <= tier])
        if actual != stated:
            mismatches.append(f"tier {tier}: docstring says {stated} checks, "
                               f"CHECKS has {actual} at tier <= {tier}")
    missing = {1, 2, 3, 4} - seen
    if missing:
        mismatches.append(f"docstring has no bullet for tier(s) {sorted(missing)}")
    if mismatches:
        return Result(FAIL, "; ".join(mismatches))
    return Result(OK, f"{len(CHECKS)} checks, tiers 1-4 all match the docstring")


def check_sim() -> Result:
    """Two runs at one seed must produce byte-identical output. That is the entire claim, and
    it is deliberately *not* "anchor-01 grades ok".

    `sim/run.py --json` exits 1 whenever an anchor is not clean, and this check used to return
    FAIL on that exit code **before** comparing the two runs — so a balance regression reported
    as "the sim is not deterministic", naming the wrong subsystem, with `a.stderr` as its
    message. That message is *empty*, because `run.py` writes its verdict to stdout. `PLC-04`
    found it the expensive way: the lattice grader made anchor-01 fail at brutal, and the gate
    said the sim was non-deterministic with nothing after the check name.

    A non-zero exit is still fatal when there is nothing to compare — that is a crash, and it
    is caught first. Once both runs agree byte for byte the sim *is* deterministic, so a
    non-zero exit is reported rather than raised.

    What this deliberately does not cover is asserted by `anchor grades` (tier 3) directly
    below, which closes `LF-224`. Keep the two separate: this one answers "is the sim
    reproducible", that one answers "is the content winnable", and conflating them is the
    exact mistake `PLC-04` had to undo.
    """
    sim = ROOT / "sim" / "run.py"
    if not sim.exists():
        return Result(SKIP, "headless sim not written (LF-002)")
    a = run(PY, str(sim), "--anchor", "anchor-01", "--seed", "1", "--json")
    b = run(PY, str(sim), "--anchor", "anchor-01", "--seed", "1", "--json")
    if not a.stdout.strip():
        detail = a.stderr.strip()[-400:] or "and no stderr either"
        return Result(FAIL, f"sim/run.py produced no output (exit {a.returncode}) — {detail}")
    if a.stdout != b.stdout:
        return Result(FAIL, "same seed produced different output — sim is not deterministic")
    if a.returncode != 0:
        return Result(OK, f"deterministic — anchor-01 does not grade clean (exit "
                          f"{a.returncode}), which is a balance state, not a determinism one")
    return Result(OK, "deterministic")


## `anchor grades` gets its own ceiling rather than riding DEFAULT_TIMEOUT. Measured on this
## 16-core WSL2 box, idle: **61.6 s wall, 6m15s CPU** for all 24 anchors at `--jobs 8`. The
## default 300 s is only 4.9x that, and LF-116 measured contention on this machine turning a
## 9 s capture into minutes — a pool that is merely slow because three agents are grading at
## once must not be reported as a wedge. 600 s is ~10x the idle figure and still a backstop,
## not a performance budget.
GRADE_TIMEOUT = 600.0
## Matches the figure CLAUDE.md documents for this exact command. Capped rather than
## `--jobs 0` so a 4-core CI box is not 6x oversubscribed, and floored at 1 so the check
## still runs where `os.cpu_count()` returns None.
GRADE_JOBS = max(1, min(8, os.cpu_count() or 1))


def check_anchor_grades() -> Result:
    """Every anchor must grade `ok`. `LF-224`.

    **Nothing in the gate asserted this until now, and for a while nothing had to.**
    `check_sim` above used to return FAIL on `sim/run.py`'s *exit code* before comparing its
    two runs, and `run.py --json` exits 1 whenever any anchor is not clean — so a balance
    regression was caught, under the name "sim determinism", with an empty message (`run.py`
    writes its verdict to stdout; the check reported stderr). `PLC-04` made that check do what
    its name says and the accidental net went with it. This is the deliberate replacement, and
    the whole point of it is the message: the failing anchors and their own problem strings,
    not an exit code.

    What `sim/run.py` calls a problem is its business, not this check's — unwinnable at some
    difficulty, exactly one distinct winning build (a single-solution level), or every distinct
    build clearing above standard (difficulty not biting). This check restates none of that; it
    reads `problems` and prints them. A threshold copied here would be the second copy of a
    rule, which is the drift this project keeps paying for.

    **Tier 3, not 2 and not 4.** Tier 2 is already *over* its own 28,000 ms budget (`LF-178`)
    and decision 067 refused to raise a threshold to make data fit once already, so 61.6 s
    there is not available — the fix for tier 2 is to move a check out, not to move the number.
    Tier 4 was rejected because the thing this guards against arrives almost exclusively as a
    **data-only pull request** — `LF-223` moved four anchors' capacities and slot layouts and
    nothing else — and tier 3 is the PR tier, described in the module docstring as "where a
    coverage regression is caught before merge". A check that only runs nightly lets an
    unwinnable anchor sit on `main` until the next scheduled run, which is the same shape of
    hole `LF-224` opened. 61.6 s against a tier that already spends ~190 s launching Godot nine
    times is a cost worth naming and paying; tier 3 has no asserted budget precisely because it
    is the tier where completeness beats speed.

    **A note on what this cannot see, since that is half of why it exists.** `sim/run.py`
    grades `data/anchors/*.json` through `all_anchor_ids()`, which is `git ls-files` — a
    generated or scratch anchor is invisible here, deliberately (see `sim/coverage.py`'s
    `verdict()` and `LF-229` for what that class of blindness cost in a neighbouring
    instrument). This asserts the shipped 24 are winnable, nothing more.
    """
    sim = ROOT / "sim" / "run.py"
    if not sim.exists():
        return Result(SKIP, "headless sim not written (LF-002)")
    r = run(PY, str(sim), "--jobs", str(GRADE_JOBS), "--json", timeout=GRADE_TIMEOUT)
    if not r.stdout.strip():
        detail = r.stderr.strip()[-400:] or "and no stderr either"
        return Result(FAIL, f"sim/run.py produced no output (exit {r.returncode}) — {detail}")
    try:
        reports = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        # A non-zero exit with unparseable stdout is a crash, not a grade — say which.
        detail = r.stderr.strip()[-400:] or r.stdout.strip()[:200]
        return Result(FAIL, f"sim/run.py --json emitted unparseable output "
                            f"(exit {r.returncode}, {exc}) — {detail}")
    bad = [rep for rep in reports if not rep.get("ok", False)]
    if bad:
        lines = [f"{len(bad)} of {len(reports)} anchors do not grade ok "
                 f"(sim/run.py --jobs {GRADE_JOBS}):"]
        for rep in bad:
            for p in rep.get("problems") or ["ok=false with no problem string"]:
                lines.append(f"  {rep.get('anchor', '?')}  {p}")
        return Result(FAIL, "\n".join(lines))
    return Result(OK, f"{len(reports)} anchors grade ok at every difficulty "
                      f"(--jobs {GRADE_JOBS})")


def check_grade_verdict() -> Result:
    """`sim/run.py --selftest`: drive every branch of the grading verdict, red and green.

    The sibling of `anchor grades` above, and the half that check structurally cannot do.
    `anchor grades` grades the shipped 24 and asserts they are all `ok` — so every rule in
    `verdict()` is exercised on its **passing** path only, and three of the four cannot fire
    on any anchor in `data/anchors/` at all. A rule that has been quietly inverted, or a
    tutorial exemption widened until it swallows the campaign, would leave `anchor grades`
    green. This is the same argument `firing arcs agree` and `verbs agree` are here for,
    stated one level up: **a check that runs the whole game proves nothing about a branch
    the shipped data never enters** (CLAUDE.md).

    It is what makes decision 086's rule falsifiable. That rule — the top difficulty's win
    share must fall below the bottom one's — is green on all 24 shipped anchors today, which
    is exactly the state in which a new check is worth nothing unless its red path has been
    driven. `sim/run.py`'s `selftest()` drives it six ways, including the two real tables
    (anchor-02 and anchor-23 at the derived ranges) that decision 082 measured and the old
    knife-edge rule read as clean.

    **Tier 1**: no Godot, no content load, no Sim — `verdict()` is a pure function of a grade
    table and the whole selftest is ~180 ms of arithmetic, which is the same argument that
    put `playfield width` and `safe operations` at this tier. Note tier 2 is over its budget
    (`LF-240`) and this adds to that; ~0.2 s against a ~12 s overrun does not change the fix,
    which is still to move a check out.
    """
    sim = ROOT / "sim" / "run.py"
    if not sim.exists():
        return Result(SKIP, "headless sim not written (LF-002)")
    r = run(PY, str(sim), "--selftest", timeout=60)
    if r.returncode != 0:
        detail = (r.stderr.strip() or r.stdout.strip())[-600:]
        return Result(FAIL, f"sim/run.py --selftest failed (exit {r.returncode}):\n{detail}")
    return Result(OK, r.stdout.strip().splitlines()[-1] if r.stdout.strip()
                  else "selftest ok (no output)")


def check_criteria_selftest() -> Result:
    """`tools/criteria.py --selftest`: drive every BAL-04 acceptance comparator, red and green.

    The third member of the family above, and here for the same reason as `grade verdict`.
    `tools/criteria.py` prints BAL-04's acceptance-criteria table against the baselines in
    `tools/bal04_baseline.json`, and run against the shipped campaign it prints six `ok`s —
    which is exactly the state in which a comparator that can only ever say `ok` is
    indistinguishable from one that works (`LF-229`, decision 078). The selftest drives all
    six red as well as green over synthetic grade tables.

    It also gives `tools/density.py`'s `build_tower_ids()` and `weapon_ids_in_build()` a
    *tested* caller, which is `LF-278`: a `built` entry is `"<tower-id>@<x>,<y>"`, so a
    membership test against bare tower ids matches nothing and returns a confident **zero**
    rather than raising. Those helpers were written with that trap documented and had no
    caller at all, so nothing would have noticed them regressing — and every criterion in
    `criteria.py` would have stayed green while the multi-weapon count silently became 0 of
    22. The selftest pins the helper directly for that reason.

    **Tier 1**: ~0.2 s, no Godot, no Sim, no content load — the comparators are pure
    functions of a grade table and a baseline dict, which is the same argument that put
    `grade verdict` and `playfield width` at this tier. Note the check deliberately does
    *not* grade the campaign: `tools/criteria.py` with no `--selftest` needs a full
    `sim/run.py` pass (~65 s at `--jobs 8`) and that is `anchor grades`' territory at tier 3.
    """
    tool = ROOT / "tools" / "criteria.py"
    if not tool.exists():
        return Result(SKIP, "tools/criteria.py not written (LF-270)")
    r = run(PY, str(tool), "--selftest", timeout=120)
    if r.returncode != 0:
        # The human table prints only the FIRST line of a detail, so the failing assertion
        # goes there and the traceback below it — a detail that opens with a newline reads
        # as a failure with no message at all, which is how this was first written and how
        # it was caught while proving the check red.
        blob = r.stderr.strip() or r.stdout.strip()
        last = blob.splitlines()[-1] if blob else "(no output)"
        return Result(FAIL, f"tools/criteria.py --selftest failed "
                             f"(exit {r.returncode}): {last}\n{blob[-600:]}")
    return Result(OK, r.stdout.strip().splitlines()[-1] if r.stdout.strip()
                  else "selftest ok (no output)")


def check_gdscript_parses() -> Result:
    """Parse-check every tracked GDScript file in isolation. See `tools/validate/gdscript.py`
    for the why: a parse error here is a hang or a blank frame with no error at the failure
    site, never an obvious crash, and `godot boots` below only greps stdout for whatever the
    *main scene* happens to load — this catches `draft.gd`, `options_menu.gd`,
    `scripts/test/*.gd`, everything `godot boots` cannot see. Placed before `godot boots` so
    the specific failure is reported before the vague one.

    Not a lint, and not a `class_name`-visibility check — see that module's docstring for
    what a clean run here does and does not prove.
    """
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")

    sys.path.insert(0, str(ROOT / "tools" / "validate"))
    import gdscript                                            # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor          # noqa: PLC0415

    files = gdscript.tracked_gd_files()
    autoloads = gdscript.parse_project_autoloads(ROOT / "project.godot")

    def runner(*argv: str) -> subprocess.CompletedProcess:
        # Routed through this file's own bounded run(), not gdscript.py's plain
        # subprocess.run default — a wedged Godot here is a red run, not a silent wait
        # (LF-061 precedent), and reap.py only fires on an actual timeout.
        return run(*argv, timeout=30.0)

    bad: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(gdscript.check_file, p, autoloads, runner): p for p in files}
        for fut in futs:
            p = futs[fut]
            diags = fut.result()
            if diags:
                bad[str(p.relative_to(ROOT))] = diags

    if bad:
        lines = [d for _, diags in sorted(bad.items()) for d in diags]
        return Result(FAIL, "\n".join(lines))
    return Result(OK, f"{len(files)} scripts parse clean")


## PRC-18. `check_scenarios_pass` used to hardcode `smoke.json` alone — confirmed by reading
## the function body, which is exactly the audit finding this maps to. `data/scenarios/`
## carried three more files nothing ever ran: `abilities.json` (the ability-falloff numbers
## pinned to four decimal places — "not a claim about anchor-01's balance" is smoke.json's
## OWN note, not this one's), `a11y-worst.json` (the 200%-scale worst case, contrast sampled
## off its own screenshot), and `lf161_edge_scroll_contained.json`. One check per file, not
## one check that loops over all of them: a loop's failure would name whichever file happened
## to run first that iteration and bury the others' status in `detail` text, exactly the
## "a red run should name one thing" reasoning `rules parity`/`terrain parsers agree` already
## follow for a different pair of checks. `gamepad_build.json` (new, same PR) joins them —
## see its own `note` field and `_dispatch_gamepad_event()` in `scripts/main.gd` for why.
##
## All five stayed at tier 3, not split to a cheaper/pricier tier — measured, not assumed:
## smoke 32.3 s, abilities 15.3 s, a11y-worst 10.1 s, lf161-scroll 11.8 s, gamepad 19.5 s
## (this machine, sequential, one capture at a time per LF-116). Summed, that takes tier 3
## from its previous ~66 s to roughly ~125 s — nearly double, and worth saying plainly rather
## than absorbing quietly, per this file's own module docstring on what a tier means. It was
## kept at tier 3 anyway rather than invented a tier in between: every one of these five
## launches Godot exactly like `game renders`/`menu renders`/`accessibility` already do, tier
## 3 is the PR gate specifically because it is where a coverage regression has to be caught
## before merge, and the alternative — tier 4 — already runs 9+ minutes on `rules parity`
## alone and would hide this class of break from every PR until nightly, which is the exact
## failure this whole issue (PRC-18) exists to close for something else (gamepad/save/draft).
## No `--budget` assertion exists at tier 3 (`TIER_BUDGET_MS` only covers 1 and 2) so this
## does not turn the gate red on its own; it is a real, accepted cost, not a hidden one.
SCENARIO_FILES: tuple[str, ...] = (
    "smoke.json", "abilities.json", "a11y-worst.json",
    "lf161_edge_scroll_contained.json", "gamepad_build.json",
)

## Raised 120 -> 300 s by PRC-17, from a measurement rather than a guess. `scenario smoke`
## takes ~32 s on this 16-core box and was the ONLY check still red on the container job's
## second real CI run (30662293364, `tier 3 — 33 passed · 1 failed`): it reached the scenario
## and emitted `DIALOG-TRIGGER brief frame=0`, then hit the 120 s bound. Not a failed
## assertion — a bound calibrated on this machine crossing a hosted runner roughly 4x slower.
##
## 300 s matches what `game renders`, `menu renders` and `accessibility` already get, and all
## three pass comfortably in the same container, so the scenario bound was simply the odd one
## out rather than 300 being a new indulgence.
##
## The cost of a looser bound is that a genuinely wedged scenario burns 5 minutes instead of
## 2. That is accepted: the rendered checks already make the same trade, and a timeout is a
## liveness bound, not a quality threshold — unlike `TIER_BUDGET_MS`, which LF-178 says must
## NOT be raised again to accommodate a measurement.
SCENARIO_TIMEOUT_S = 300


def _run_scenario_check(filename: str) -> Result:
    """One `data/scenarios/<filename>` through `tools/scenario.py` itself.

    Driven through the real tool rather than a hand-rolled subprocess, so this check fails
    exactly the way running it by hand fails — a gate that reimplements the thing it is
    checking can pass while the thing is broken.
    """
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    scenario = ROOT / "data" / "scenarios" / filename
    if not scenario.exists():
        return Result(SKIP, f"no {filename} authored")
    r = run(PY, str(ROOT / "tools" / "scenario.py"), str(scenario), "--timeout", str(SCENARIO_TIMEOUT_S))
    if r.returncode != 0:
        return Result(FAIL, (r.stdout + r.stderr).strip()[-1500:])
    line = next((l for l in r.stdout.splitlines() if l.startswith("SCENARIO ")), "")
    n = len(json.loads(line[len("SCENARIO "):]).get("assertions", [])) if line else 0
    return Result(OK, f"{filename}: {n} assertion(s) passed")


def check_save_roundtrip() -> Result:
    """PRC-18. `progress.gd` (save/load) had zero automated references before this — see
    `tools/save_roundtrip.py`'s own module docstring for the full mechanism (two isolated
    Godot launches sharing a scratch `XDG_DATA_HOME`, never the owner's real save) and for a
    documented mistake worth reading once: the first version of this tool used
    `--user-data-dir`, a flag that does not exist on this project's Godot build and was
    *silently* ignored rather than rejected, so it ran twice against the real shared Linux
    dev save before the isolation was fixed. Also exercises the recovery draft (`draft.gd`/
    `recoveries.gd`, LF-065/LF-070) as a side effect of driving the round trip through it —
    see that tool's docstring for why the two are one check rather than two.

    Measured ~10.1 s for both launches together (this machine); tier 3, next to the other
    checks that launch Godot for real rather than parsing source.
    """
    script = ROOT / "tools" / "save_roundtrip.py"
    if not script.exists():
        return Result(SKIP, "tools/save_roundtrip.py missing")
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    r = run(PY, str(script), timeout=150.0)
    if r.returncode == 2:
        return Result(SKIP, (r.stdout + r.stderr).strip()[-800:])
    if r.returncode != 0:
        return Result(FAIL, (r.stdout + r.stderr).strip()[-1500:])
    tail = next((l for l in r.stdout.splitlines() if l.startswith("save_roundtrip: OK")), "")
    return Result(OK, tail or "save/load round trip ok")


def check_godot_boots() -> Result:
    """Load the main scene headlessly and assert no script errors.

    A GDScript parse error does not stop the process — Godot logs it and carries on
    with the scene missing. Exit code alone would call that a pass, so the output is
    what has to be asserted on.

    `--headless` never opens a window on any build, so this goes through
    `toolpaths.godot_argv(..., want_window=True)` purely to pick up whichever binary is
    installed — there is nothing here for `Xvfb` to hide.
    """
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    argv = toolpaths.godot_argv(ROOT, ["--headless", "--quit-after", "120"],
                                want_window=True)
    r = run(*argv)
    blob = r.stdout + r.stderr
    bad = [l for l in blob.splitlines()
           if "SCRIPT ERROR" in l or "Parse Error" in l or "Compile Error" in l]
    if bad:
        return Result(FAIL, "\n".join(bad[:8]))
    return Result(OK, "main scene loads clean")


## The anchor `game renders` photographs, pinned rather than inherited. Without it the run
## takes whatever `Progress.selected_anchor` happens to be (`scripts/main.gd` line 194),
## which is only "anchor-01" because nothing has written it yet — a save format that ever
## persisted it would silently change what the gate is looking at. The board assertion below
## reads this anchor's authored slot list, so "which anchor" has to be a declared input.
GAME_RENDERS_ANCHOR = "anchor-01"

## How many distinct emplacements the frame is asked to draw. Three, because one proves
## nothing about the id→sprite mapping (any single id would draw *something*) and the
## anchors with the fewest authored slots still have room for three. The *ids* are never
## written here — see `_board_builds()`.
GAME_RENDERS_BUILDS = 3


def _board_builds() -> list[str]:
    """The emplacement ids `game renders` asks the board to draw, taken from the content.

    Deliberately data-derived and not a literal: the check must not carry a tower id, and
    the assertion it feeds must not encode which towers today's autobuild policy happens to
    like. `--build` goes through `main.gd`'s `_build_one()`, which grants funds and bypasses
    the palette's unlock gate, so any id in `data/towers.json` is placeable at any anchor —
    that is what makes the requested set a free choice rather than an anchor-dependent one.
    Capped by the anchor's authored slot count because `_build_one()` places on the next
    free authored slot and warns instead of building when they run out.
    """
    ids = [t["id"] for t in json.loads((ROOT / "data" / "towers.json").read_text())["towers"]]
    anchor = json.loads(
        (ROOT / "data" / "anchors" / f"{GAME_RENDERS_ANCHOR}.json").read_text())
    return ids[:min(GAME_RENDERS_BUILDS, len(anchor.get("slots", [])))]


def _emplacements_drawn(blob: str) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """Split a `--facings` dump into (base sprites, head sprites, sprites of neither shape).

    **Field 3 is the sprite; field 2 is the drawable class.** `FACE tower pulse_turret_head
    bucket=10/16 at=(714,260)` — an `awk '{print $2}'` over this buckets the entire board
    into `tower`/`unit` and reports one distinct value no matter what is standing on it,
    which has already cost this project time. The keys returned are the *ids* (`pulse_turret`),
    with the `_base`/`_head` suffix stripped, so the two halves of one emplacement can be
    matched against each other.

    Only `kind == "tower"` lines are read. `anchor_view.gd`'s `_build_drawables()` emits
    exactly two per placed emplacement (ART-01/LF-157: a 4-bucket base and a 16-bucket head,
    separate sprites at the same screen point), so a name ending in neither suffix means the
    renderer changed shape under a check that would otherwise silently count zero.
    """
    base: dict[str, int] = {}
    head: dict[str, int] = {}
    odd: list[str] = []
    for raw in blob.splitlines():
        if not raw.startswith("FACE "):
            continue
        fields = raw.split()
        if len(fields) < 3 or fields[1] != "tower":
            continue
        sprite = fields[2]
        if sprite.endswith("_base"):
            base[sprite[:-5]] = base.get(sprite[:-5], 0) + 1
        elif sprite.endswith("_head"):
            head[sprite[:-5]] = head.get(sprite[:-5], 0) + 1
        else:
            odd.append(sprite)
    return base, head, odd


def _board_verdict(blob: str, want: list[str], known: set[str],
                   frame_note: str) -> tuple[str | None, dict[str, int], int]:
    """Everything `game renders` asserts about the board, as one predicate over one dump.

    Returns `(failure message or None, ids drawn with their base counts, tower drawables)`.
    Split out from the check itself so a red proof can drive *this* code against a dump
    shaped like the failure, rather than a re-implementation of it that could agree with a
    bug — the same reason `firing arcs agree` exists (decision 078): a check the shipped data
    never pushes into its failing branch has not been shown to have one.
    """
    base, head, odd = _emplacements_drawn(blob)
    faces = sum(base.values()) + sum(head.values()) + len(odd)
    if odd:
        return ("drawable is neither a base nor a head — the emplacement sprite pair "
                f"changed shape: {sorted(set(odd))}", base, faces)
    if faces == 0:
        seen = "no FACE lines at all" if "FACE " not in blob else "no tower FACE lines"
        return (f"no emplacement was drawn on the board — {frame_note} is terrain and "
                f"panels only. asked for {want or '(nothing — anchor has no slots)'}; "
                f"{seen}", base, faces)
    unknown = sorted((set(base) | set(head)) - known)
    if unknown:
        return ("board drew emplacement sprites data/towers.json does not define: "
                f"{unknown}", base, faces)
    if base != head:
        halves = sorted(set(base) | set(head))
        return ("emplacement drawn without its matching half (base/head): "
                + ", ".join(f"{k} base={base.get(k, 0)} head={head.get(k, 0)}"
                            for k in halves if base.get(k, 0) != head.get(k, 0)),
                base, faces)
    missing = [t for t in want if base.get(t.replace("-", "_"), 0) < 1]
    if missing:
        return (f"asked the board for {want}, and {missing} never reached it — "
                f"drawn: {sorted(base)}", base, faces)
    return (None, base, faces)


@with_artifacts("game-renders")
def check_game_renders(out: Path) -> Result:
    """Run the real renderer and assert the board has the right emplacements on it.

    `check_godot_boots` runs headless and only greps for script errors, so it passes
    happily on a scene that draws nothing at all — which is how scenes/main.tscn stayed
    a childless Node2D for several sessions while the gate stayed green. The build
    reports `FRAME coverage=… distinct=…` alongside its self-screenshot, and the floor
    below sits between a healthy frame and a board that failed to load.

    **The coverage figure is about the frame, not about the board, and it must not be read
    as though it were** (LF-251). It is the fraction of non-background pixels over the whole
    1440x810 image — terrain, the two instrument panels and every label included — so it is
    dominated by furniture that is there whether or not a single emplacement rendered.
    Measured across the LF-236 fix in one session: the board went from twelve `anchor_damper`
    emplacements to twelve `pulse_turret` ones, a complete change of what was standing on it,
    and this check reported `coverage 0.95, 114 tones` on both sides — the same two numbers
    to the digit. It was a real check and it caught real regressions (blank frames, the
    flat-grey atlas), but on its own it is evidence about the *frame* only.

    So the run also passes `--build` and `--facings`, and asserts against the facing dump.
    `--facings` costs nothing extra — the capture is already running — and it is the one
    output that names what was actually drawn. What is asserted:

      * every requested emplacement id appears, as a **base and a head** (the pair
        `_build_drawables()` emits per placement — a head that stopped being drawn is
        LF-157's regression and is invisible in coverage);
      * bases and heads balance across the whole board, so no emplacement is half-drawn;
      * every drawn id exists in `data/towers.json` — a sprite name the content does not
        know is a wrong board, not a stylistic difference.

    What is deliberately **not** asserted is an emplacement count fitted to one capture, or
    the identity of what the autobuild policy chooses. The board here is *requested*, not
    observed: `_board_builds()` names the ids from the content file and the assertion is that
    those ids came back, which is causal rather than fitted and stays true when the policy,
    the anchor's economy or the wave table move. A "subset of what this anchor unlocked"
    assertion was considered and rejected twice over — `--build` legitimately bypasses the
    unlock gate, so it would be asserting something about the hook rather than the board, and
    at anchor-01 the unlocked set is a single id, which makes it nearly vacuous anyway.
    Extra emplacements beyond the requested ones are tolerated (a future anchor may
    pre-place one); everything drawn still has to be a real tower, drawn whole.

    GL Compatibility renders nothing readable under `--headless`, so this needs a real,
    GPU-backed window — but not a *visible* one. `toolpaths.godot_argv(..., want_window=
    False)` launches the native Linux build under an `Xvfb` virtual framebuffer instead,
    which supersedes the old occlusion workaround (LF-061, decision 051): there is no
    compositor and no window on any real desktop, so nothing can occlude it and nothing
    steals focus.
    """
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")

    MIN_COVERAGE, MIN_DISTINCT = 0.15, 12
    want = _board_builds()
    known = {t["id"].replace("-", "_")
             for t in json.loads((ROOT / "data" / "towers.json").read_text())["towers"]}
    shot = out / "gate-frame.png"
    extra: list[str] = ["--display-defaults", "--anchor", GAME_RENDERS_ANCHOR]
    for tid in want:
        extra += ["--build", tid]
    extra += ["--facings", "--shot", str(shot), "120"]
    argv = toolpaths.godot_argv(ROOT, ["--fixed-fps", "60", "--", *extra], want_window=False)
    r = run(*argv)
    blob = r.stdout + r.stderr

    line = next((l for l in blob.splitlines() if l.startswith("FRAME ")), "")
    if not line:
        return Result(FAIL, "build never reported a frame — it did not reach the shot:\n"
                            + blob.strip()[-800:])
    try:
        coverage = float(line.split("coverage=")[1].split()[0])
        distinct = int(line.split("distinct=")[1].split()[0])
    except (IndexError, ValueError):
        return Result(FAIL, f"could not parse frame stats: {line!r}")

    if coverage < MIN_COVERAGE or distinct < MIN_DISTINCT:
        return Result(FAIL,
                      f"frame is effectively blank: coverage={coverage:.4f} "
                      f"(min {MIN_COVERAGE}), distinct={distinct} (min {MIN_DISTINCT})")

    bad, base, faces = _board_verdict(
        blob, want, known, f"coverage {coverage:.4f} over {distinct} tones")
    if bad is not None:
        return Result(FAIL, bad)

    shot.unlink(missing_ok=True)
    return Result(OK, f"{len(base)} emplacement ids on the board ({', '.join(sorted(base))}), "
                      f"{faces} base+head drawables, all in towers.json; "
                      f"frame coverage {coverage:.2f} over {distinct} tones "
                      f"(whole image — terrain and panels dominate it)")


@with_artifacts("menu-renders")
def check_menu_renders(out: Path) -> Result:
    """The boot scene is the menu now, and it is built entirely in code.

    `game renders` passes `--shot`, which the menu treats as "go straight to the game" —
    so without this check nothing looks at the screen the player actually sees first, and
    a menu that drew nothing (or listed no anchors) would ship green.

    Same invisible path as `game renders` — see that check's docstring.
    """
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")

    MIN_COVERAGE, WANT_BUTTONS = 0.015, 8
    shot = out / "gate-menu.png"
    argv = toolpaths.godot_argv(ROOT, ["--fixed-fps", "60", "--", "--display-defaults",
                                      "--shot-menu", str(shot), "40"], want_window=False)
    r = run(*argv)
    blob = r.stdout + r.stderr

    line = next((l for l in blob.splitlines() if l.startswith("MENUFRAME ")), "")
    if not line:
        return Result(FAIL, "menu never reported a frame:\n" + blob.strip()[-800:])
    try:
        coverage = float(line.split("coverage=")[1].split()[0])
        buttons = int(line.split("buttons=")[1].split()[0])
    except (IndexError, ValueError):
        return Result(FAIL, f"could not parse menu stats: {line!r}")

    # The menu is mostly dark by design, so the coverage bar is low; the anchor count is
    # the real assertion. Act I is eight anchors and the grid is per-act.
    if coverage < MIN_COVERAGE:
        return Result(FAIL, f"menu is blank: coverage={coverage:.4f} (min {MIN_COVERAGE})")
    if buttons != WANT_BUTTONS:
        return Result(FAIL, f"menu listed {buttons} act-I anchors, expected {WANT_BUTTONS}")
    shot.unlink(missing_ok=True)
    return Result(OK, f"coverage {coverage:.3f}, {buttons} anchors listed")


@with_artifacts("accessibility")
def check_accessibility(out: Path) -> Result:
    """Every piece of text on screen must clear WCAG 2.1 AA and the size floor.

    Runs the game and the menu with `--a11y`, which dumps the live UI tree, and pairs each
    dump with the screenshot taken on the same frame so `tools/validate/a11y.py` can sample
    the real composited background under every label. See that module for what is measured
    and why the thresholds are what they are.

    The game is checked at anchor-24 and at **200%** interface scale — the worst case on
    both axes. Anchor-24 unlocks nine emplacements and fields the widest threat rows, and
    200% is the smallest logical viewport the interface is offered in: 960x540, into which
    the instrument column wants 893 px of readout plus 98 px of pinned controls and the
    threat panel another 455 beside it. It is also checked at
    100%, because the reflow is conditional and a rule that only fires at the top of the
    range can break the bottom of it.

    The menu and the options panel are checked at 200% as well. The options panel carries
    the interface-scale control itself, so it is the one screen that absolutely must survive
    the top of its own range — it did not, before decision 048: MUSIC, EFFECTS and BACK were
    off the bottom of the screen.

    This exists because every one of these defects was invisible in the source and obvious
    in a measurement: an 11 px ladder that read as 8 px in the default window, an alert
    colour at 4.00:1, a locked-anchor override under a theme key that does not exist, and a
    note label that drew over the SELL button once its height went to zero.

    Same invisible path as `game renders` — see that check's docstring. Five cases means
    five separate Godot launches; wrapped invisibly, none of them ever reach a real screen.
    """
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")

    sys.path.insert(0, str(ROOT / "tools" / "validate"))
    import a11y                                          # noqa: E402

    cases = [
        ("game-100", ["--autoplay", "--anchor", "anchor-24", "--select", "1",
                      "--ui-scale", "1.0"], "--shot", "300"),
        ("game-200", ["--autoplay", "--anchor", "anchor-24", "--select", "1",
                      "--ui-scale", "2.0"], "--shot", "300"),
        # Brutal, because difficulty scales the widest field on the widest row. The threat
        # panel's trait line is the longest string the interface can be asked to draw, and
        # THREAT_W is a hand-derived constant sized from it (LF-048); since the panel shows
        # difficulty-scaled hp (LF-047), a 1.55x multiplier is what would push it over. The
        # panel scrolls vertically only, so the clipping check treats an over-wide row as a
        # failure — but only on a screen the gate actually renders.
        ("game-brutal", ["--autoplay", "--anchor", "anchor-24", "--difficulty", "brutal",
                         "--select", "1", "--ui-scale", "1.0"], "--shot", "300"),
        ("menu", ["--ui-scale", "2.0"], "--shot-menu", "40"),
        ("options", ["--options", "--ui-scale", "2.0"], "--shot-menu", "40"),
    ]

    totals, worst = [], []
    for name, extra, shot_flag, frame in cases:
        png, js = out / f"gate-a11y-{name}.png", out / f"gate-a11y-{name}.json"
        # `--display-defaults` leads, because it resets ui_scale and the per-case
        # `--ui-scale` after it must win. Without it the gate measures whatever window
        # mode and resolution happen to be saved in the player's progress.json — a file
        # outside the repo. The same tree reported coverage 0.56 on one machine and 0.34
        # on another for exactly that reason, and the a11y analyser samples its background
        # colours out of these frames.
        argv = toolpaths.godot_argv(ROOT, ["--fixed-fps", "60", "--", "--display-defaults",
                                          *extra, shot_flag, str(png), frame,
                                          "--a11y", str(js)], want_window=False)
        r = run(*argv)
        if not js.exists():
            return Result(FAIL, f"{name}: probe wrote no report — the run did not reach "
                                f"the shot:\n" + (r.stdout + r.stderr).strip()[-800:])
        findings, summary = a11y.audit(js, png)
        fails = [f for f in findings if f.severity == "fail"]
        if fails:
            detail = "\n".join(f"    {f.check}: {f.text!r} — {f.detail}" for f in fails[:6])
            return Result(FAIL, f"{name}: {len(fails)} of {summary['items']} text items "
                                f"fail WCAG AA or the size floor\n{detail}")
        totals.append(summary["items"])
        if summary["min_contrast"] is not None:
            worst.append(summary["min_contrast"])
        png.unlink(missing_ok=True)
        js.unlink(missing_ok=True)

    return Result(OK, f"{sum(totals)} text items clean, worst contrast "
                      f"{min(worst):.2f}:1" if worst else f"{sum(totals)} text items clean")


def check_terrain_parity() -> Result:
    """`sim/content.py`'s `resolve_terrain()` against `scripts/content.gd`'s, on one fixture.

    PRD risk #10 is the two independent anchor parsers disagreeing on region-to-height
    resolution, invisibly. This is the cheap, targeted proof — one small board, both
    resolvers, diffed tile for tile — and it exists because the expensive proof is a bad
    place to learn it: `rules parity` would surface a one-tile drift nine minutes in, as an
    unexplained leak with no pointer to terrain at all.

    The fixture is diffed against the expected grid *and* the two resolvers against each
    other, so a typo in the fixture cannot hide a real disagreement behind two matching
    wrong answers. Decision 057.
    """
    script = ROOT / "tools" / "terrain_parity.py"
    if not script.exists():
        return Result(SKIP, "terrain parity harness missing")
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    r = run(PY, str(script))
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip()[-1200:])
    return Result(OK, r.stdout.strip())


def check_firing_arcs() -> Result:
    """PLC-03. `sim/engine.py`'s firing-arc test against `scripts/anchor_sim.gd`'s, on a
    fixture weapon that carries an arc — because no shipped weapon does.

    This is the check that covers the hole `rules parity` structurally cannot. Parity
    runs the whole game through both engines, but the arc branch is gated on a
    `cos_half_angle` key that **no row in `data/towers.json` carries**, deliberately: the
    acceptance bar for PLC-03 is that all 1,440 parity runs stay byte-identical, which is
    only true because the arc path is inert. So parity proves the *absent* branch and
    says nothing about the present one, and would keep saying nothing right up until the
    Siege Battery, Cutting Lance or Spine Driver (PRD §E6) shipped an arc — at which
    point a divergence would surface as an unexplained leak nine minutes into a run.

    `tools/arc_parity.py` drives both engines over
    `data/schema/fixtures/firing-arc.json` — four emplacements, one walker, 280 ticks —
    and makes three claims: the fire patterns are byte-identical between engines, every
    firing window falls inside bounds derived from the *geometry* rather than from either
    implementation (so two engines wrong the same way still fail), and the arc'd rows
    fire strictly less than an un-arc'd control (so an arc test that never engaged cannot
    pass by looking omnidirectional). Both deliberate breaks were proved red before this
    check was trusted: dropping the `dot >= 0` sign guard, and assuming `facing` is a
    unit vector.

    Tier 3, not tier 2, and that is a deliberate cost decision rather than an oversight.
    It is the same shape and roughly the same cost as `terrain parsers agree` (one
    headless Godot, no window), which sits at tier 2 — but tier 2 is already *over* its
    own 28,000 ms `--budget` contract on a clean run (LF-178), and this file's own note
    on that says the answer is to move a check out, not to move the number again. Adding
    one here would have made it worse for a check whose subject is provably inert in
    shipped data. It joins the PR tier, where CI runs it on every pull request.
    """
    script = ROOT / "tools" / "arc_parity.py"
    if not script.exists():
        return Result(SKIP, "firing-arc parity harness missing")
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    r = run(PY, str(script))
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip()[-1200:])
    return Result(OK, r.stdout.strip())


def check_verb_parity() -> Result:
    """The scheduled verbs exist twice too, and nothing has ever compared them (LF-244).

    `Sim._dispatch_one()` accepts eight verbs. Across all twenty distinct policies
    `standard_policies()` returns, exactly two are ever scheduled — `call_wave` (one
    policy) and `ability` (surge and overcharge, two policies). So every one of the
    1,440 `rules parity` runs executes the **absent** branch for `target_mode`, `sell`,
    `upgrade`, `set_online`, `build`, the shutter ability and `speed`.
    `scripts/test/parity.gd:317` has mirrored the upgrade dispatch since BAL-01 and
    nothing has ever driven it.

    **This is a worse hole than the firing arc's, and the difference is who uses it.** An
    unauthored arc is inert in the shipped game by construction; these six verbs are the
    player's entire interface — the build, sell, upgrade, online-toggle, target-mode and
    speed controls. An `upgrade()` that merged its stats differently in the two engines
    would mean every balance conclusion about an upgraded board describes a game nobody
    plays, and it would surface as an unexplained divergence between the sweep and the
    build the owner is actually running on Windows.

    `tools/verb_parity.py` drives both engines over
    `data/schema/fixtures/scheduled-verbs.json` — four emplacements, three walkers, ten
    actions, 320 ticks — and makes five claims: the two engines agree byte for byte on
    fire pattern, funds, spend, bus load, emplacement count and every unit's distance and
    hit points; the money trajectory matches arithmetic done in the harness from the
    fixture's own costs; `upgrade` is proved *geometrically*, by an emplacement standing
    4.0 tiles off the lane with range 3.0 going from structurally-unable-to-fire to firing
    inside the window range 8.0 admits; `set_online` and the shutter are proved on the bus,
    to the exact megawatt; and `speed` is proved to be the no-op the engine's own comment
    claims, by re-running with the speed actions stripped and requiring identical output.

    All five were proved red before this check was trusted — `--corrupt upgrade`,
    `refund`, `bus`, `target` and `engine`, the last two breaking behaviour rather than an
    expectation. Tier 3, for the same two reasons `firing arcs agree` is: it is one
    headless windowless Godot of the same class, and tier 2 is already over its own
    28,000 ms budget (LF-178), where the answer is to move a check out rather than move
    the number again.
    """
    script = ROOT / "tools" / "verb_parity.py"
    if not script.exists():
        return Result(SKIP, "scheduled-verb parity harness missing")
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    r = run(PY, str(script))
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip()[-1200:])
    return Result(OK, r.stdout.strip())


def check_rules_parity(force: bool = False, no_cache: bool = False) -> Result:
    """The rules exist twice, in Python and GDScript. Prove they agree.

    Without this the game could silently stop playing the level that was balanced,
    and nothing would announce it.

    PRC-05: `tools/test_parity.py` gates its own 1152-run comparison behind a content-
    hash digest over exactly the files that can move a parity outcome (see that module's
    `parity_inputs_digest()`) — on an unchanged tree it prints a `parity cached — ...`
    line and exits in well under a second instead of re-running anything. That line is
    recognised here and reported as `skip, skipped_reason: "cached"`, never as `ok`: a
    cached skip proves the inputs did not move, not that anything was re-verified this
    run, and this project's own gate contract (a tier-excluded check, a `--no-window`
    skip) already treats "did not run" as a claim that must be visible, not silent.
    `force`/`no_cache` are threaded straight through to `test_parity.py`'s own flags —
    see its `--force`/`--no-cache` help text for what each means.
    """
    script = ROOT / "tools" / "test_parity.py"
    if not script.exists():
        return Result(SKIP, "parity harness missing")
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    cmd = [PY, str(script)]
    if no_cache:
        cmd.append("--no-cache")
    elif force:
        cmd.append("--force")
    r = run(*cmd, timeout=PARITY_TIMEOUT)
    if r.returncode == TIMED_OUT:
        return Result(FAIL, r.stderr)
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip()[-1200:])
    out = r.stdout.strip()
    if out.startswith("parity cached"):
        return Result(SKIP, out, skipped_reason="cached")
    return Result(OK, out.replace("parity ok — ", ""))


def check_rules_parity_windows(force: bool = False, no_cache: bool = False) -> Result:
    """BAL-06 / LF-105 (PRD risk 2, blocker). `rules parity` above proves the rules agree
    against whichever Godot `toolpaths.godot()` prefers — the native LINUX build on this
    machine. The owner plays the WINDOWS build, and that check has never once run against
    it. Measured across 100,000 float64 samples on CPython, Linux Godot and Windows Godot
    (`docs/DECISIONS.md`): `+ - * /`, `sqrt`, `fmod`, `floor`, comparisons are byte-identical
    on all three, but Windows Godot's MSVC UCRT diverges from CPython and Linux Godot's
    glibc on `atan2` (0.084%), `sin`/`cos` (0.133%/0.120%), `pow`/`log`/`exp`
    (0.130%/0.031%/0.069%), and `tan` (4.32%). The rules use none of those ops today
    (decision 061's `safe operations` check above is what keeps that true by design rather
    than by accident) — this check is the other half: actually run parity against the
    Windows binary, at least once per release.

    Skips loudly — never falls back to Linux and calls that a pass — when no Windows build
    resolves on this machine (`toolpaths.resolve_for_platform("windows")`), which is the
    ordinary case on a Linux-only CI box. A skip here must read as "not verified", never as
    "verified clean", so it is reported with `skipped_reason="subsystem"` like any other
    missing-subsystem skip in this file — not `"cached"`, which would misstate why nothing
    ran, and not silently folded into `rules parity`'s own result.

    Uses `tools/test_parity.py --platform windows`, which keeps its own cache file
    (`.cache/parity-windows.json`) so a clean Linux `rules parity` run can never suppress
    this one, or vice versa — PRC-05's digest cache is per-platform, not global.
    """
    script = ROOT / "tools" / "test_parity.py"
    if not script.exists():
        return Result(SKIP, "parity harness missing")
    if toolpaths.resolve_for_platform("windows") is None:
        return Result(SKIP, "no Windows Godot resolvable")
    cmd = [PY, str(script), "--platform", "windows"]
    if no_cache:
        cmd.append("--no-cache")
    elif force:
        cmd.append("--force")
    r = run(*cmd, timeout=PARITY_TIMEOUT)
    if r.returncode == TIMED_OUT:
        return Result(FAIL, r.stderr)
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip()[-1200:])
    out = r.stdout.strip()
    if out.startswith("parity cached"):
        return Result(SKIP, out, skipped_reason="cached")
    return Result(OK, out.replace("parity ok — ", ""))


def check_sprite_atlas() -> Result:
    """The packed atlas must still match the renders it was packed from.

    An atlas is derived output with no visible link to its inputs. Re-render one sprite,
    forget to re-pack, and the board keeps drawing the old pixels out of the stale page —
    a correct art fix that looks like it did nothing, which is precisely the misdiagnosis
    that skipping `--import` already cost this project once.
    """
    manifest = ROOT / "assets" / "renders" / "sprites.json"
    if not manifest.exists():
        return Result(SKIP, "no sprite manifest")
    doc = json.loads(manifest.read_text())
    atlas = doc.get("atlas")
    if not atlas:
        return Result(SKIP, "no atlas packed")

    sys.path.insert(0, str(ROOT / "tools" / "blender"))
    try:
        import pack_atlas
    except Exception as exc:  # noqa: BLE001
        return Result(FAIL, f"cannot import pack_atlas: {exc}")

    groups = pack_atlas.collect(doc)
    missing = [p for g in groups.values() for (_, _, p) in g if not p.exists()]
    if missing:
        return Result(FAIL, f"{len(missing)} renders referenced by the manifest are gone, "
                            f"first {missing[0].name}")
    for pass_name, rel in atlas.get("pages", {}).items():
        if not (ROOT / rel).exists():
            return Result(FAIL, f"atlas page missing: {rel}")
    if pack_atlas.source_digest(groups) != atlas.get("source_digest"):
        return Result(FAIL, "atlas is stale — renders have changed since it was packed. "
                            "Run tools/blender/pack_atlas.py, then --import")
    cells = sum(len(v) for v in atlas.get("index", {}).values())
    return Result(OK, f"{cells} cells in {len(atlas.get('pages', {}))} pages, in sync")


## The checks that need a real, GPU-backed Godot frame — GL Compatibility renders nothing
## readable under `--headless`. `godot boots` and `rules parity` pass `--headless` and are
## not in this set.
##
## These three used to mean a *visible* window on the owner's desktop: seven of them per
## gate run, because `accessibility` walks five cases. It stole focus and popped up over
## whatever the owner was doing, and — worse — **macOS throttled a window it considered
## occluded**: cover or background it and the frame loop stalled, so
## `await RenderingServer.frame_post_draw` in main.gd never resolved. That is the measured
## 36-minute `game renders` run in LF-061 — it did not fail, it sat there until the window
## came back into view and then passed.
##
## That is fixed, not mitigated: `toolpaths.godot_argv(..., want_window=False)` now launches
## the native Linux Godot build under an `Xvfb` virtual framebuffer, which is a real
## GPU-backed (Mesa llvmpipe software GL) window that no compositor ever presents to a
## screen. There is nothing left to occlude and nothing left to steal focus, so LF-061 is
## closed rather than merely bounded.
##
## `--no-window` still exists, but it is now a *speed* option, not a courtesy: launching
## Godot separate times (Xvfb included) is the slowest single stretch of the gate after
## `rules parity`, so skipping these is worth reaching for on an otherwise-idle box even
## though nothing about running them disturbs anyone anymore. They still report SKIP, and
## this file's contract is unchanged — a skip is never a pass — so reaching for the speed
## option cannot quietly weaken a commit.
##
## PRC-18 grew this set from 3 to 9: `scenario smoke/abilities/a11y-worst/lf161-scroll/
## gamepad` and `save roundtrip` are exactly the same class of thing as the original three —
## a real Godot process launched invisibly under Xvfb — so leaving them out of RENDERED would
## have quietly broken `--no-window`'s own contract (a tier-3 "no Godot capture at all" fast
## path) the moment this PR landed, which is the same "a docstring silently stops matching
## CHECKS" failure PRC-16 exists to audit for a different pair of numbers. `--tier 3
## --no-window` now runs tier 2 plus nothing, same as before PRC-18 — it is the SET that grew,
## not the guarantee.
RENDERED = {"game renders", "menu renders", "accessibility",
            "scenario smoke", "scenario abilities", "scenario a11y-worst",
            "scenario lf161-scroll", "scenario gamepad", "scenario lf226-fallback",
            "save roundtrip"}


@dataclass(frozen=True)
class Check:
    """One gate check. `tier` is a minimum — see the module docstring's `## Tiers` section
    for the assignment table and the measured cost behind each number; this is the data an
    eventual re-tier edits, not an argument anyone has to win."""
    name: str
    tier: int
    fn: Callable[[], Result]


## Budget in ms for `--budget`, tier 1 and 2 only — PRC-04's acceptance criteria. Tier 3 and
## 4 have no asserted budget: a nightly 9-minute run doubling to 18 is a different problem
## than a "fast" tier nobody can trust to still be fast.
##
## Tier 2's budget moved 25_000 -> 28_000 when PRC-18 added `sfx loudness` there (measured
## ~1.9-2.3 s, 86 files) — the task asked for tier 2 specifically (cheap, next to `sfx
## determinism`), and a real tier-2 run measured 27.8 s afterward, over the OLD 25_000 budget
## by ~2.8 s even though nothing about the check itself is slow. That is marginal creep, not
## the doubling this budget exists to catch, so raising it to keep `--budget` a real assertion
## (rather than leaving it permanently, silently red for anyone who opts in) was the smaller
## lie: an unraised budget that always fails stops meaning anything within one PR of being
## set, same as a check nobody runs.
TIER_BUDGET_MS: dict[int, float] = {1: 10_000.0, 2: 28_000.0}

CHECKS = [
    Check("python syntax",     1, check_python_syntax),
    Check("json parses",       1, check_json_parses),
    Check("game data",         1, check_game_data),
    Check("wave density",      1, check_wave_density),
    Check("dialog capacity",   1, check_dialog_capacity),
    Check("dialog figures",    1, check_dialog_figures),
    Check("banned terms",      1, check_banned_terms),
    # PRC-16: the module docstring's own '## Tiers' table names a check count per tier —
    # assert it against this same registry rather than let it rot unchallenged. See
    # check_gate_docs_consistent's own docstring for what drifted before this existed.
    Check("tier counts",       1, check_gate_docs_consistent),
    Check("sfx determinism",   2, check_sfx_reproducible),
    Check("music manifest",    2, check_music_manifest),
    # PRC-18: split exactly like the pair above — sfx loudness is nearly free (~2s, 86
    # files), music loudness carries the whole ~85s cost (14 tracks) so it goes to tier 4
    # instead of doubling tier 3 on its own. See _run_loudness_check's own comment.
    Check("sfx loudness",      2, check_sfx_loudness),
    Check("music loudness",    4, check_music_loudness),
    Check("sprite atlas",      2, check_sprite_atlas),
    Check("sprite coverage",   2, check_sprite_coverage),
    # PRC-14 — sibling of "sprite coverage" above, the other direction. See
    # check_asset_coverage's own docstring for why it is a sibling rather than a merge.
    Check("asset coverage",    1, check_asset_coverage),
    Check("backlog rendered",  1, check_backlog_rendered),
    Check("chronicle current", 1, check_chronicle_current),
    Check("agent models",      1, check_agent_models),
    Check("leases wired",      1, check_leases_wired),
    Check("issue traceability", 1, check_issue_traceability),
    Check("sim determinism",   2, check_sim),
    # LF-224. Sits next to `sim determinism` because the two were once one check by
    # accident — see both docstrings. Tier 3, and the reason is in check_anchor_grades'.
    Check("anchor grades",     3, check_anchor_grades),
    # LF-243 / decision 086. The RED half of `anchor grades`: that check grades the shipped
    # 24 and so only ever exercises the green path of every rule in `sim/run.py`'s
    # `verdict()`. See check_grade_verdict's own docstring.
    Check("grade verdict",     1, check_grade_verdict),
    # LF-270 / LF-278. Sibling of `grade verdict` directly above: the red half of a table
    # that is six `ok`s on the shipped campaign, and the only caller of tools/density.py's
    # two build-parsing helpers. See check_criteria_selftest's own docstring.
    Check("grade criteria",    1, check_criteria_selftest),
    Check("gdscript parses",   1, check_gdscript_parses),
    Check("godot boots",       2, check_godot_boots),
    # PRC-18: one check per data/scenarios/*.json file, not one check that loops over all of
    # them — see SCENARIO_FILES/_run_scenario_check's own doc for why (a failure names the
    # exact file, not a shared "scenarios pass" that buries which one broke in its detail text).
    Check("scenario smoke",       3, lambda: _run_scenario_check("smoke.json")),
    Check("scenario abilities",   3, lambda: _run_scenario_check("abilities.json")),
    Check("scenario a11y-worst",  3, lambda: _run_scenario_check("a11y-worst.json")),
    Check("scenario lf161-scroll", 3,
          lambda: _run_scenario_check("lf161_edge_scroll_contained.json")),
    Check("scenario gamepad",     3, lambda: _run_scenario_check("gamepad_build.json")),
    # LF-226: the only check in this file that enters anchor_view.gd's
    # `_lattice_fallback_candidate()` at all. See the scenario's own `note` for why nothing
    # else could, and `_run_scenario_check`'s doc for why this is its own check rather than
    # another iteration of a loop.
    Check("scenario lf226-fallback", 3,
          lambda: _run_scenario_check("lf226_lattice_fallback.json")),
    # PRC-18: not a scenario (scenario.gd's timeline is scoped to main.tscn's AnchorView) —
    # a separate tool because a save/load round trip needs two separate Godot PROCESSES, and
    # the recovery draft is a different scene with its own CLI flags. See both docstrings.
    Check("save roundtrip",       3, check_save_roundtrip),
    Check("game renders",      3, check_game_renders),
    Check("menu renders",      3, check_menu_renders),
    Check("accessibility",     3, check_accessibility),
    # Deliberately the fast-tier sibling of `rules parity`, and placed next to it so the
    # relationship is visible: same class of risk, a fraction of the cost.
    Check("terrain parsers agree", 2, check_terrain_parity),
    # PLC-03. The arc-branch sibling of the two above: same "one rule, two
    # implementations" risk, on the one branch `rules parity` structurally cannot reach.
    # Tier 3 rather than tier 2 — see the function's own docstring for why.
    Check("firing arcs agree", 3, check_firing_arcs),
    # LF-244. The same shape again, for a much larger hole: six of the eight verbs
    # Sim._dispatch_one() accepts are never scheduled by any shipped policy, so all 1,440
    # parity runs execute their absent branch. Unlike the arc, the PLAYER uses all six.
    Check("verbs agree",       3, check_verb_parity),
    Check("rules parity",      4, check_rules_parity),
    # BAL-06 / LF-105 (PRD risk 2, blocker): the check above proves parity against
    # whichever Godot toolpaths.godot() prefers (Linux on this machine) — this is the
    # other half, against the WINDOWS build the owner actually plays. Same tier: both are
    # the ~9-minute-class check, and there is no cheaper tier this belongs at.
    Check("rules parity (windows)", 4, check_rules_parity_windows),
    # BAL-07, LF-148, LF-141 — all three cost a real diagnosis pass once already; all three
    # are pure text/AST analysis over a handful of known files, so tier 1 costs them nothing.
    Check("safe operations",   1, check_safe_ops),
    Check("rules autoloads",   1, check_autoload_in_rules),
    Check("yaw hysteresis",    1, check_yaw_hysteresis),
    ## LF-247: text-only, ~0 ms, and at tier 1 for that reason — see its own docstring for
    ## why nothing else in this gate could have caught a 4 px playfield.
    Check("playfield width",   1, check_playfield_width),
    # PRC-06 / LF-142: both measured too expensive for tier 1's 10s budget (each spawns at
    # least one real Godot process — guard.py --selftest spawns two plus a reap.py probe,
    # yaw_band.py spawns one for facing.gd). Measured pushing tier 1 from 5.7s to 10.8s,
    # BLOWN against its own --budget contract. tier 2's budget is 25s with headroom to
    # spare, and both checks are the same shape as tier 2's existing Godot-spawning checks
    # (godot boots, sim determinism, terrain parsers agree) rather than tier 1's "no Godot
    # window opens" checks — see the module docstring's ## Tiers table.
    Check("hooks configured",  2, check_hooks_configured),
    Check("facing harness",    3, check_facing_harness),
]


def _git_line(*args: str) -> str:
    """One trimmed line of `git <args>`, or "" — used only for the JSON artefact's
    provenance fields, never for anything a check's pass/fail depends on."""
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(ROOT))
    return r.stdout.strip()


# A run passed with no explicit --json still asks for it, so with-no-value ("--json" alone,
# meaning "stdout, and suppress the human table") is distinguishable from "not passed at
# all" (default None, meaning "no JSON, nothing changes"). argparse's own None default
# already means "flag absent"; this sentinel is what "flag present, no path" parses to.
_JSON_STDOUT = "-"


def main() -> int:
    ap = argparse.ArgumentParser(description="Latticefall pre-commit gate.")
    ap.add_argument("--list", action="store_true", help="list checks and exit")
    # Both help strings below are built from CHECKS/TIER_BUDGET_MS at parser-construction
    # time rather than hand-typed, the same idiom `--no-window`'s help already used for
    # RENDERED — PRC-16 found the hand-typed versions of exactly these two numbers (a tier's
    # check count, tier 2's budget) stale in the module docstring; generating them here means
    # they cannot drift the same way, because there is nothing hand-typed left to forget to
    # update. Costs are still not shown — see the docstring's own note on why those are read
    # from --json instead — only counts and the budget figures themselves, both of which come
    # straight from the registry they describe.
    _tier_counts = ", ".join(f"{t}={len([c for c in CHECKS if c.tier <= t])}"
                              for t in (1, 2, 3, 4))
    ap.add_argument("--tier", type=int, default=4, choices=(1, 2, 3, 4),
                    help="run only checks assigned this tier or lower (a tier is a minimum — "
                         "see module docstring's '## Tiers' for the assignment table). "
                         f"Check counts by tier ({_tier_counts}) are read live from CHECKS, "
                         "not hand-typed here; run --list or --json for exact names and "
                         "current wall-clock cost, which is not duplicated into this help "
                         "text because it drifts and a count does not. "
                         "Orthogonal to --no-window: --tier 3 --no-window runs exactly tier "
                         "2 plus nothing, since every rendered check is tier 3. "
                         "A check a lower tier excludes reports skip, skipped_reason=tier — "
                         "it did not run and is not a pass.")
    ap.add_argument("--budget", action="store_true",
                    help="assert the tier-1 "
                         f"({TIER_BUDGET_MS[1] / 1000:g}s) and tier-2 "
                         f"({TIER_BUDGET_MS[2] / 1000:g}s) wall-clock budgets, read live from "
                         "TIER_BUDGET_MS; fail the run if the selected tier exceeds its "
                         "budget even when every check passed. No-op at --tier 3 or 4, which "
                         "have no asserted budget. Exists so a tier whose cost silently "
                         "doubles turns red instead of quietly becoming a tier nobody runs.")
    ap.add_argument("--no-window", action="store_true",
                    help="skip the %d checks that launch a real Godot process (%s). This is "
                         "now a SPEED option only, not a courtesy — those checks capture "
                         "invisibly (no window ever reaches the owner's desktop, LF-061 "
                         "closed) but still cost that many extra Godot launches, the slowest "
                         "stretch of the gate after rules parity. Reach for this when you "
                         "want the fast gate, not because anything about the full run "
                         "disturbs anyone. Every one of these checks is tier 3, so this only "
                         "has an effect at --tier 3 or the tier-4 default — it is a no-op at "
                         "--tier 1 or 2, which never reach them."
                         % (len(RENDERED), ", ".join(sorted(RENDERED))))
    ap.add_argument("--json", nargs="?", const=_JSON_STDOUT, default=None, metavar="PATH",
                    help="emit a machine-readable gate result (see module docstring for the "
                         "schema). With no PATH, JSON goes to stdout and the human table is "
                         "suppressed. With PATH, JSON is written there AND the human table "
                         "still prints to stdout — CI wants the file, a human running this "
                         "by hand still wants the table. Never implies --no-window.")
    ap.add_argument("--force", action="store_true",
                    help="PRC-05/BAL-06: ignore rules parity's (and rules parity "
                         "(windows)'s — separate cache files, both bypassed) content-hash "
                         "cache and run the full 1152-run comparison even if nothing that "
                         "can affect it has moved. Forwarded to tools/test_parity.py's own "
                         "--force. Does not affect any other check.")
    ap.add_argument("--no-cache", action="store_true",
                    help="PRC-05/BAL-06: like --force, but neither parity check RECORDS "
                         "this run's digest afterward — for a one-off/experimental gate "
                         "run whose result the next run should not trust. Forwarded to "
                         "tools/test_parity.py's own --no-cache.")
    args = ap.parse_args()

    if args.list:
        for c in CHECKS:
            tag = "  [renders a frame]" if c.name in RENDERED else ""
            print(f"{c.name}  (tier {c.tier}){tag}")
        return 0

    emit_json = args.json is not None
    json_to_stdout = args.json == _JSON_STDOUT
    json_path = Path(args.json) if (emit_json and not json_to_stdout) else None
    # Suppress the human table only when JSON is the *sole* output (no path given). A run
    # that also wrote a file keeps the table — that is what "write both" means.
    quiet = json_to_stdout

    started_at = datetime.now(timezone.utc).isoformat()
    failed = skipped = by_flag = by_tier = by_cache = 0
    t0 = time.monotonic()
    records: list[dict] = []
    for check in CHECKS:
        start = time.monotonic()
        try:
            if check.tier > args.tier:
                res = Result(SKIP, f"excluded by --tier {args.tier} (this check is tier "
                                   f"{check.tier}) — did not run", skipped_reason="tier")
                by_tier += 1
            elif args.no_window and check.name in RENDERED:
                res = Result(SKIP, "skipped by --no-window (speed option; capture is "
                                   "invisible either way)", skipped_reason="flag")
                by_flag += 1
            elif check.name in ("rules parity", "rules parity (windows)"):
                # The two checks with their own cache (PRC-05, BAL-06) — --force/
                # --no-cache only ever mean anything to these; every other check.fn()
                # takes no args.
                fn = check_rules_parity if check.name == "rules parity" \
                    else check_rules_parity_windows
                res = fn(force=args.force, no_cache=args.no_cache)
                # Every other SKIP in this file is a fact about the project (a subsystem
                # that does not exist yet) unless the check itself already named a more
                # specific reason (here, "cached" — set inside the check function itself).
                if res.status == SKIP and res.skipped_reason is None:
                    res.skipped_reason = "subsystem"
                if res.skipped_reason == "cached":
                    by_cache += 1
            else:
                res = check.fn()
                # Every other SKIP in this file is a fact about the project (a subsystem
                # that does not exist yet), not a choice about this run — set the reason
                # here, once, rather than touching every check function that returns SKIP.
                if res.status == SKIP and res.skipped_reason is None:
                    res.skipped_reason = "subsystem"
        except Exception as e:
            res = Result(FAIL, f"check itself raised: {type(e).__name__}: {e}")
        ms = (time.monotonic() - start) * 1000
        mark = {OK: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[res.status]
        if not quiet:
            print(f"[{mark}] {check.name:<20s} {ms:6.0f}ms  "
                  f"{res.detail.splitlines()[0] if res.detail else ''}")
        if res.status == FAIL:
            failed += 1
            if not quiet:
                for line in res.detail.splitlines()[1:]:
                    print(f"           {line}")
        elif res.status == SKIP:
            skipped += 1
        records.append({"name": check.name, "status": res.status, "ms": round(ms, 1),
                        "detail": res.detail, "tier": check.tier,
                        "skipped_reason": res.skipped_reason})

    total = (time.monotonic() - t0) * 1000
    summary_line = (f"tier {args.tier} — {len(CHECKS) - failed - skipped} passed · "
                    f"{failed} failed · {skipped} skipped · {total:.0f}ms")
    budget = TIER_BUDGET_MS.get(args.tier)
    budget_blown = bool(args.budget and budget is not None and total > budget)
    if not quiet:
        print(f"\n{summary_line}")
        # Four reasons a check can skip, and they are not the same claim: a missing
        # subsystem is a fact about the project, --no-window is a choice about this run,
        # --tier is a different choice about this run, and (PRC-05) a content-hash cache
        # hit is a claim that NOTHING was re-verified this run, not that it passed. None
        # share a line.
        if skipped > by_flag + by_tier + by_cache:
            print("skipped checks are not passes — the subsystem does not exist yet")
        if by_flag:
            print(f"--no-window skipped {by_flag} rendered check(s): they did NOT run and "
                  f"are NOT passes. Run the full gate before committing anything that "
                  f"touches the interface, the board or a sprite.")
        if by_tier:
            excluded = [c.name for c in CHECKS if c.tier > args.tier]
            print(f"--tier {args.tier} excluded {by_tier} check(s) that did NOT run and are "
                  f"NOT passes: {', '.join(excluded)}. Run a higher --tier before trusting "
                  f"this as more than a fast, partial signal.")
        if by_cache:
            print(f"rules parity did NOT run and is NOT a pass — its content-hash cache "
                  f"found nothing that can affect the outcome has changed since the last "
                  f"clean run (PRC-05). Pass --force to re-verify anyway.")
        if budget is not None:
            verdict = "BLOWN" if budget_blown else "ok"
            print(f"tier {args.tier} budget: {total:.0f}ms of {budget:.0f}ms ({verdict})"
                 + ("" if args.budget else " [not asserted — pass --budget to fail on this]"))

    if emit_json:
        doc = {
            "schema": "latticefall-gate",
            "version": 1,
            "started_at": started_at,
            "duration_ms": round(total, 1),
            "root_commit": _git_line("rev-parse", "HEAD") or None,
            # A fresh CI checkout can report clean even when the workflow patched files in
            # first — see module docstring's caller, tools/gate_report.py: do not treat
            # False here as proof of anything.
            "dirty": bool(_git_line("status", "--porcelain")),
            "tier": args.tier,
            "checks": records,
            "summary": {
                "passed": len(CHECKS) - failed - skipped, "failed": failed,
                "skipped": skipped, "skipped_by_flag": by_flag, "skipped_by_tier": by_tier,
                "skipped_by_cache": by_cache,
                "total": len(CHECKS), "duration_ms": round(total, 1), "text": summary_line,
            },
        }
        text = json.dumps(doc, indent=2)
        if json_path is not None:
            json_path.write_text(text + "\n")
        if json_to_stdout:
            print(text)

    return 1 if (failed or budget_blown) else 0


if __name__ == "__main__":
    raise SystemExit(main())
