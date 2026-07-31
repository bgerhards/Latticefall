#!/usr/bin/env python3
"""
The gate. Run before every commit.

One command, one exit code. Each check is mechanical and fast — this catches breakage,
not cheapness. For "does it feel finished", use the `verify` skill and the
`build-verifier` agent.

Checks that cannot run yet (because the subsystem does not exist) report SKIP rather than
passing silently. A green run that quietly skipped half the suite is worse than a red one.

    .venv/bin/python tools/check.py                    # tier 4 — everything, ~9 min
    .venv/bin/python tools/check.py --tier 1            # pre-commit, ~6 s
    .venv/bin/python tools/check.py --tier 2            # pre-push, ~14 s
    .venv/bin/python tools/check.py --tier 3            # PR, ~66 s
    .venv/bin/python tools/check.py --list
    .venv/bin/python tools/check.py --json /tmp/gate.json   # machine-readable, human table too
    .venv/bin/python tools/check.py --json                  # machine-readable to stdout only

## Tiers

A tier is a minimum: a check assigned tier 1 also runs at `--tier 2`, `--tier 3` and the
untiered default (which is tier 4, unchanged from before tiering existed — the default must
never become weaker than what CLAUDE.md promises). A check the selected tier excludes does
**not** run at all; it is reported `skip`, `skipped_reason: "tier"`, so the JSON record still
names it rather than omitting it, which would read as a pass to anything consuming the file.
`--no-window` is orthogonal to `--tier`: it always skips the three rendered checks regardless
of tier, so `--tier 3 --no-window` runs exactly tier 2 plus nothing.

Measured on this machine, after {{PRC-01}} (`git ls-files`, no `SKIP_DIRS`) and {{PRC-02}}
(enumeration fix) both landed. Tier 1 and tier 2 were re-measured live for this change; tier
3 and 4's per-check figures are carried forward from `docs/STATE.md`'s last full gate record,
taken before {{PRC-01}}/{{PRC-02}} landed — none of `game renders`, `menu renders`,
`accessibility` or `rules parity` touch file enumeration, so the figures should not have
moved, but they were not re-run for this change (see PRC-04's report for which number is
which and why).

- **tier 1 (~6 s, pre-commit), 14 checks:** `python syntax`, `json parses`, `gdscript parses`,
  `game data`, `wave density`, `dialog capacity`, `backlog rendered`, `agent models`,
  `leases wired`, `banned terms`, `safe operations`, `rules autoloads`, `yaw hysteresis`,
  `asset coverage`. No Godot window opens.
- **tier 2 (~21-23 s, pre-push, re-measured live for this change — this machine had other
  Godot processes contending for cycles at the time, hence the range), 23 checks:** tier 1 + `sim determinism`, `sprite atlas`,
  `sprite coverage`, `music manifest`, `sfx determinism`, `godot boots`,
  `terrain parsers agree`, `hooks configured`, `facing harness`. The last two (PRC-06,
  LF-142) were measured at tier 1 first and moved here: each spawns at least one real
  Godot process (`hooks configured` runs `guard.py --selftest`, itself two Godot parse
  checks plus a `reap.py` probe; `facing harness` runs `yaw_band.py`, one Godot launch of
  `scripts/test/facing.gd`), and together they pushed tier 1 from 5.7 s to 10.8 s — BLOWN
  against its own 10 s `--budget` contract. Tier 2's 25 s budget has the headroom tier 1
  does not.
- **tier 3 (~66 s, PR), 26 checks:** tier 2 + `game renders` (6.5 s), `menu renders` (4.8 s),
  `accessibility` (41.2 s).
- **tier 4 (~9 min or more, nightly/release), 28 checks — the default:** tier 3 +
  `rules parity` (measured ~594 s at 1152 runs, growing with every policy/anchor) +
  `rules parity (windows)` (BAL-06 — the same 1152 runs again, against the Windows binary
  the owner actually plays rather than the Linux build `rules parity` uses; skips loudly
  with `skipped_reason: "subsystem"` on a machine with no Windows Godot, e.g. a Linux CI
  box, rather than silently passing). The one thing making a balance claim in this project
  falsifiable; never move either below tier 4 because it is "usually fine". PRC-05: both
  are gated behind their OWN content-hash digest over exactly what can move a parity
  outcome (`tools/test_parity.py`'s `parity_inputs_digest()`), in SEPARATE cache files
  (`.cache/parity.json`, `.cache/parity-windows.json`) so a clean run of one can never
  suppress the other — an unchanged tree reports `skip, skipped_reason: "cached"` in under
  a second instead of re-running, and `--force`/`--no-cache` bypass it. A cached skip is
  still not a pass; see the loud-skip note a few paragraphs down.

`--budget` asserts the tier-1 and tier-2 contracts: with it set, exceeding 10 s at `--tier 1`
or 25 s at `--tier 2` fails the run even if every check passed, because a tier whose budget
silently doubles is a tier nobody will keep running.

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

`checks` always lists all 28 — a check a lower tier excluded is present with
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
import hashlib
import json
import py_compile
import re
import subprocess
import sys
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


## No act may run at less than this fraction of the busiest act's screen presence. It is a
## judgement, not a measurement — but it is the judgement LF-044 was about, and the ratio it
## guards was 0.38 for most of the project's life without anyone being able to see it.
DENSITY_FLOOR = 0.55


def check_wave_density() -> Result:
    """Every act keeps a comparable number of units on screen.

    Act III fielded 7.7 units a wave against Act I's 20.2 and nothing said so, because from
    Act II every unit drains the bus — unit count and bus theft were the same number, so the
    act could only get busier by getting poorer. Fixing that took two new units and a
    re-authored wave table; keeping it fixed is one ratio, and it can be undone silently,
    since `sweep.py --weight` scales every spawn count in a level at once.

    Measured as peak units in flight rather than units per wave: a Column at 0.5 tiles/sec
    holds the board four times as long as a Shard, so the per-wave count understates a slow
    act. See tools/density.py, which owns the calculation.
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
    return Result(OK, " · ".join(f"act {a} {means[a]:.0f} on screen ({means[a]/busiest:.0%})"
                                for a in sorted(means)))


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
    if not cited:
        return Result(OK, f"{len(entries)} entries (none cite a commit)")
    r = subprocess.run(["git", "log", "--format=%h", "-60"],
                       capture_output=True, text=True, cwd=str(ROOT))
    log = [h for h in r.stdout.split() if h]
    behind = 0
    for h in log:
        if any(c.startswith(h) or h.startswith(c) for c in cited):
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


def check_agent_models() -> Result:
    """Every subagent definition pins its model, because the default is the expensive one.

    An agent file with no `model:` key in its frontmatter inherits the parent model, which is
    Opus — so a five-way fan-out silently costs five Opus contexts. That is not a
    theoretical: it spilled the owner's subscription usage into paid credits, which is why
    this is a gate check and not a note. The pin is `sonnet` for all of them.

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
        elif model != "sonnet":
            wrong.append(f"{path.stem}={model}")
    if unpinned:
        return Result(FAIL, f"no model pinned (inherits Opus): {', '.join(unpinned)}")
    if wrong:
        return Result(FAIL, f"model is not sonnet: {', '.join(wrong)}")
    return Result(OK, f"{len(agents)} agents pinned to sonnet")


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
    return Result(OK, f"{len(HOOK_EVENTS)} events wired ({', '.join(HOOK_EVENTS)}) · {tally}")


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
SAFE_OPS_SCOPE: list[str] = ["scripts/anchor_sim.gd", "sim/engine.py", "scripts/test/parity.gd"]

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


def check_sim() -> Result:
    sim = ROOT / "sim" / "run.py"
    if not sim.exists():
        return Result(SKIP, "headless sim not written (LF-002)")
    a = run(PY, str(sim), "--anchor", "anchor-01", "--seed", "1", "--json")
    b = run(PY, str(sim), "--anchor", "anchor-01", "--seed", "1", "--json")
    if a.returncode != 0:
        return Result(FAIL, a.stderr.strip()[-400:])
    if a.stdout != b.stdout:
        return Result(FAIL, "same seed produced different output — sim is not deterministic")
    return Result(OK, "deterministic")


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


def check_scenarios_pass() -> Result:
    """`data/scenarios/smoke.json` through `tools/scenario.py` itself.

    Driven through the real tool rather than a hand-rolled subprocess, so this check fails
    exactly the way running it by hand fails — a gate that reimplements the thing it is
    checking can pass while the thing is broken.

    Tier 3, not tier 2, and the reason is measured rather than aesthetic: the scenario takes
    about 30 s and tier 2's whole asserted budget is 25 s. Putting it there would have
    doubled that tier silently, which is precisely what `TIER_BUDGET_MS` exists to prevent —
    so it goes where a 30 s check belongs, beside the other checks that render frames.
    """
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    scenario = ROOT / "data" / "scenarios" / "smoke.json"
    if not scenario.exists():
        return Result(SKIP, "no scenarios authored")
    r = run(PY, str(ROOT / "tools" / "scenario.py"), str(scenario), "--timeout", "120")
    if r.returncode != 0:
        return Result(FAIL, (r.stdout + r.stderr).strip()[-1500:])
    line = next((l for l in r.stdout.splitlines() if l.startswith("SCENARIO ")), "")
    n = len(json.loads(line[len("SCENARIO "):]).get("assertions", [])) if line else 0
    return Result(OK, f"smoke.json: {n} assertion(s) passed")


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


def check_game_renders() -> Result:
    """Run the real renderer and assert the frame is not blank.

    `check_godot_boots` runs headless and only greps for script errors, so it passes
    happily on a scene that draws nothing at all — which is how scenes/main.tscn stayed
    a childless Node2D for several sessions while the gate stayed green. The build
    reports `FRAME coverage=… distinct=…` alongside its self-screenshot; measured here,
    a healthy anchor-01 frame is ~0.39 coverage and a board that failed to load is
    ~0.03, so the bar sits between them with room on both sides.

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
    shot = ROOT / ".godot" / "gate-frame.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    argv = toolpaths.godot_argv(ROOT, ["--fixed-fps", "60", "--", "--display-defaults",
                                      "--shot", str(shot), "120"], want_window=False)
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
    shot.unlink(missing_ok=True)
    return Result(OK, f"coverage {coverage:.2f}, {distinct} tones")


def check_menu_renders() -> Result:
    """The boot scene is the menu now, and it is built entirely in code.

    `game renders` passes `--shot`, which the menu treats as "go straight to the game" —
    so without this check nothing looks at the screen the player actually sees first, and
    a menu that drew nothing (or listed no anchors) would ship green.

    Same invisible path as `game renders` — see that check's docstring.
    """
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")

    MIN_COVERAGE, WANT_BUTTONS = 0.015, 8
    shot = ROOT / ".godot" / "gate-menu.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
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


def check_accessibility() -> Result:
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

    out = ROOT / ".godot"
    out.mkdir(parents=True, exist_ok=True)
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
## Godot five separate times (Xvfb included) is the slowest single stretch of the gate after
## `rules parity`, so skipping these three is worth reaching for on an otherwise-idle box
## even though nothing about running them disturbs anyone anymore. They still report SKIP,
## and this file's contract is unchanged — a skip is never a pass — so reaching for the
## speed option cannot quietly weaken a commit.
RENDERED = {"game renders", "menu renders", "accessibility"}


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
TIER_BUDGET_MS: dict[int, float] = {1: 10_000.0, 2: 25_000.0}

CHECKS = [
    Check("python syntax",     1, check_python_syntax),
    Check("json parses",       1, check_json_parses),
    Check("game data",         1, check_game_data),
    Check("wave density",      1, check_wave_density),
    Check("dialog capacity",   1, check_dialog_capacity),
    Check("banned terms",      1, check_banned_terms),
    Check("sfx determinism",   2, check_sfx_reproducible),
    Check("music manifest",    2, check_music_manifest),
    Check("sprite atlas",      2, check_sprite_atlas),
    Check("sprite coverage",   2, check_sprite_coverage),
    # PRC-14 — sibling of "sprite coverage" above, the other direction. See
    # check_asset_coverage's own docstring for why it is a sibling rather than a merge.
    Check("asset coverage",    1, check_asset_coverage),
    Check("backlog rendered",  1, check_backlog_rendered),
    Check("chronicle current", 1, check_chronicle_current),
    Check("agent models",      1, check_agent_models),
    Check("leases wired",      1, check_leases_wired),
    Check("sim determinism",   2, check_sim),
    Check("gdscript parses",   1, check_gdscript_parses),
    Check("godot boots",       2, check_godot_boots),
    Check("scenarios pass",    3, check_scenarios_pass),
    Check("game renders",      3, check_game_renders),
    Check("menu renders",      3, check_menu_renders),
    Check("accessibility",     3, check_accessibility),
    # Deliberately the fast-tier sibling of `rules parity`, and placed next to it so the
    # relationship is visible: same class of risk, a fraction of the cost.
    Check("terrain parsers agree", 2, check_terrain_parity),
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
    # PRC-06 / LF-142: both measured too expensive for tier 1's 10s budget (each spawns at
    # least one real Godot process — guard.py --selftest spawns two plus a reap.py probe,
    # yaw_band.py spawns one for facing.gd). Measured pushing tier 1 from 5.7s to 10.8s,
    # BLOWN against its own --budget contract. tier 2's budget is 25s with headroom to
    # spare, and both checks are the same shape as tier 2's existing Godot-spawning checks
    # (godot boots, sim determinism, terrain parsers agree) rather than tier 1's "no Godot
    # window opens" checks — see the module docstring's ## Tiers table.
    Check("hooks configured",  2, check_hooks_configured),
    Check("facing harness",    2, check_facing_harness),
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
    ap.add_argument("--tier", type=int, default=4, choices=(1, 2, 3, 4),
                    help="run only checks assigned this tier or lower (a tier is a minimum — "
                         "see module docstring's '## Tiers' for the assignment table and "
                         "measured cost of each). 1=pre-commit ~6s (14 checks), 2=pre-push "
                         "~21s (23), 3=PR ~66s (26), 4=nightly/release ~9min or more (28, "
                         "the default — BAL-06 added a second, Windows-binary parity run "
                         "alongside the Linux one that was already there). "
                         "Orthogonal to --no-window: --tier 3 --no-window runs exactly tier "
                         "2 plus nothing, since the three rendered checks are all tier 3. "
                         "A check a lower tier excludes reports skip, skipped_reason=tier — "
                         "it did not run and is not a pass.")
    ap.add_argument("--budget", action="store_true",
                    help="assert the tier-1 (10s) and tier-2 (25s) wall-clock budgets; fail "
                         "the run if the selected tier exceeds its budget even when every "
                         "check passed. No-op at --tier 3 or 4, which have no asserted "
                         "budget. Exists so a tier whose cost silently doubles turns red "
                         "instead of quietly becoming a tier nobody runs.")
    ap.add_argument("--no-window", action="store_true",
                    help="skip the three checks that render a frame (%s). This is now a "
                         "SPEED option only, not a courtesy — those checks capture invisibly "
                         "(no window ever reaches the owner's desktop, LF-061 closed) but "
                         "still cost five extra Godot launches, the slowest stretch of the "
                         "gate after rules parity. Reach for this when you want the fast "
                         "gate, not because anything about the full run disturbs anyone. "
                         "All three checks are tier 3, so this only has an effect at "
                         "--tier 3 or the tier-4 default — it is a no-op at --tier 1 or 2, "
                         "which never reach them."
                         % ", ".join(sorted(RENDERED)))
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
    t0 = time.time()
    records: list[dict] = []
    for check in CHECKS:
        start = time.time()
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
        ms = (time.time() - start) * 1000
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

    total = (time.time() - t0) * 1000
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
