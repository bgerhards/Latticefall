#!/usr/bin/env python3
"""
The gate. Run before every commit.

One command, one exit code. Each check is mechanical and fast — this catches breakage,
not cheapness. For "does it feel finished", use the `verify` skill and the
`build-verifier` agent.

Checks that cannot run yet (because the subsystem does not exist) report SKIP rather than
passing silently. A green run that quietly skipped half the suite is worse than a red one.

    .venv/bin/python tools/check.py
    .venv/bin/python tools/check.py --list
    .venv/bin/python tools/check.py --json /tmp/gate.json   # machine-readable, human table too
    .venv/bin/python tools/check.py --json                  # machine-readable to stdout only

`--json`'s shape, for `tools/session.py` and `tools/gate_report.py` (and eventually CI —
there is no `.github/` directory yet):

    {
      "schema": "latticefall-gate", "version": 1,
      "started_at": "<UTC ISO 8601>", "duration_ms": <float>,
      "root_commit": "<sha or null>", "dirty": <bool>,
      "tier": null,                        # reserved; no tiering exists yet
      "checks": [
        {"name": str, "status": "ok"|"FAIL"|"skip", "ms": float, "detail": str,
         "skipped_reason": null|"subsystem"|"flag"|"cached"}, ...
      ],
      "summary": {"passed": int, "failed": int, "skipped": int, "skipped_by_flag": int,
                  "total": int, "duration_ms": float, "text": "<the printed tally line>"}
    }

`detail` carries the check's *full* multi-line detail, not just the first line the human
table prints — a PR comment needs all of it. `skipped_reason` exists because a skip because
a subsystem does not exist yet is not the same claim as a skip because `--no-window` was
passed, and the plain exit code cannot tell them apart; `--json` with no path still runs the
three rendered checks (only `--no-window` skips them) — a JSON run that quietly skipped half
the suite would be exactly the failure mode this whole file exists to prevent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

sys.path.insert(0, str(ROOT / "tools"))
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
    """
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
    return Result(OK, f"{len(warns)} warning(s)" if warns else "no warnings")


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


def check_rules_parity() -> Result:
    """The rules exist twice, in Python and GDScript. Prove they agree.

    Without this the game could silently stop playing the level that was balanced,
    and nothing would announce it.
    """
    script = ROOT / "tools" / "test_parity.py"
    if not script.exists():
        return Result(SKIP, "parity harness missing")
    if toolpaths.godot() is None:
        return Result(SKIP, "godot not installed")
    r = run(PY, str(script), timeout=PARITY_TIMEOUT)
    if r.returncode == TIMED_OUT:
        return Result(FAIL, r.stderr)
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip()[-1200:])
    return Result(OK, r.stdout.strip().replace("parity ok — ", ""))


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

CHECKS = [
    ("python syntax",     check_python_syntax),
    ("json parses",       check_json_parses),
    ("game data",         check_game_data),
    ("wave density",      check_wave_density),
    ("dialog capacity",   check_dialog_capacity),
    ("banned terms",      check_banned_terms),
    ("sfx determinism",   check_sfx_reproducible),
    ("music manifest",    check_music_manifest),
    ("sprite atlas",      check_sprite_atlas),
    ("sprite coverage",   check_sprite_coverage),
    ("backlog rendered",  check_backlog_rendered),
    ("agent models",      check_agent_models),
    ("sim determinism",   check_sim),
    ("gdscript parses",   check_gdscript_parses),
    ("godot boots",       check_godot_boots),
    ("game renders",      check_game_renders),
    ("menu renders",      check_menu_renders),
    ("accessibility",     check_accessibility),
    ("rules parity",      check_rules_parity),
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
    ap.add_argument("--no-window", action="store_true",
                    help="skip the three checks that render a frame (%s). This is now a "
                         "SPEED option only, not a courtesy — those checks capture invisibly "
                         "(no window ever reaches the owner's desktop, LF-061 closed) but "
                         "still cost five extra Godot launches, the slowest stretch of the "
                         "gate after rules parity. Reach for this when you want the fast "
                         "gate, not because anything about the full run disturbs anyone."
                         % ", ".join(sorted(RENDERED)))
    ap.add_argument("--json", nargs="?", const=_JSON_STDOUT, default=None, metavar="PATH",
                    help="emit a machine-readable gate result (see module docstring for the "
                         "schema). With no PATH, JSON goes to stdout and the human table is "
                         "suppressed. With PATH, JSON is written there AND the human table "
                         "still prints to stdout — CI wants the file, a human running this "
                         "by hand still wants the table. Never implies --no-window.")
    args = ap.parse_args()

    if args.list:
        for name, _ in CHECKS:
            print(name + ("  [renders a frame]" if name in RENDERED else ""))
        return 0

    emit_json = args.json is not None
    json_to_stdout = args.json == _JSON_STDOUT
    json_path = Path(args.json) if (emit_json and not json_to_stdout) else None
    # Suppress the human table only when JSON is the *sole* output (no path given). A run
    # that also wrote a file keeps the table — that is what "write both" means.
    quiet = json_to_stdout

    started_at = datetime.now(timezone.utc).isoformat()
    failed = skipped = by_flag = 0
    t0 = time.time()
    records: list[dict] = []
    for name, fn in CHECKS:
        start = time.time()
        try:
            if args.no_window and name in RENDERED:
                res = Result(SKIP, "skipped by --no-window (speed option; capture is "
                                   "invisible either way)", skipped_reason="flag")
                by_flag += 1
            else:
                res = fn()
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
            print(f"[{mark}] {name:<20s} {ms:6.0f}ms  "
                  f"{res.detail.splitlines()[0] if res.detail else ''}")
        if res.status == FAIL:
            failed += 1
            if not quiet:
                for line in res.detail.splitlines()[1:]:
                    print(f"           {line}")
        elif res.status == SKIP:
            skipped += 1
        records.append({"name": name, "status": res.status, "ms": round(ms, 1),
                        "detail": res.detail, "skipped_reason": res.skipped_reason})

    total = (time.time() - t0) * 1000
    summary_line = (f"{len(CHECKS) - failed - skipped} passed · {failed} failed · "
                    f"{skipped} skipped · {total:.0f}ms")
    if not quiet:
        print(f"\n{summary_line}")
        # The two reasons a check can skip are not the same claim and must not share a
        # line: a missing subsystem is a fact about the project, --no-window is a choice
        # about this run.
        if skipped > by_flag:
            print("skipped checks are not passes — the subsystem does not exist yet")
        if by_flag:
            print(f"--no-window skipped {by_flag} rendered check(s): they did NOT run and "
                  f"are NOT passes. Run the full gate before committing anything that "
                  f"touches the interface, the board or a sprite.")

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
            "tier": None,          # reserved for {{PRC-04}}'s tier budgets; none exist yet
            "checks": records,
            "summary": {
                "passed": len(CHECKS) - failed - skipped, "failed": failed,
                "skipped": skipped, "skipped_by_flag": by_flag, "total": len(CHECKS),
                "duration_ms": round(total, 1), "text": summary_line,
            },
        }
        text = json.dumps(doc, indent=2)
        if json_path is not None:
            json_path.write_text(text + "\n")
        if json_to_stdout:
            print(text)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
