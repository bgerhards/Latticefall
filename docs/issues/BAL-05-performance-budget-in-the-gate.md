id: BAL-05
title: Performance budget in the gate
labels: phase-3, tooling, perf
depends: PRC-04, PRC-12
blocks: WAR-03
milestone: E7 Balance
---
## Problem

A frame-time regression is currently a **feeling**, not a red run (LF-097). Theatre Scale is
explicitly a scaling programme with measured targets — the whole game costs **1.5 ms of a
16.7 ms frame** today, and the PRD projects 13.4 ms/frame of draw at 64×64 and 25.1 ms/tick of
sim at 512 units before the fixes land (PRD §2.2). **The sim budget is not 16.7 ms — it is
5.6 ms**, because the speed control goes to 3× so the rules run 90 ticks per second of wall
clock. Every one of the E2–E6 issues claims a speedup (2.47× for the draw path, 15–18× for the
tick), and nothing in the repository can currently falsify any of them.

The prerequisite is honest and blocking: **there is no frame-time output from a `--fixed-fps`
run to assert on.** `main.gd` prints `FRAME coverage=… distinct=…` and `STATE`/`FACE` lines,
and nothing else. Instrumentation has to exist before a budget can. That instrumentation
belongs with {{PRC-12}}'s scenario harness, which is already the thing that drives a run to a
known state and emits a machine-readable summary.

## Tasks

- [ ] Land the instrumentation as part of {{PRC-12}}: per-frame `_process` and
      `_physics_process` duration, plus a separate sim-tick timer around `AnchorSim.tick()`,
      accumulated into min / mean / p95 / max and emitted in the `SCENARIO` summary line.
- [ ] Separate **draw** from **rules**. They have different budgets (16.7 ms vs 5.6 ms) and
      different fixes; one combined number cannot tell you which regressed.
- [ ] Use a monotonic clock and report in microseconds. Do not derive frame time from
      `Engine.get_frames_per_second()` — under `--fixed-fps` it reports the fixed rate, which is
      exactly the number that cannot regress.
- [ ] Write budget scenario files under `data/scenarios/perf/`: one per representative load —
      `small` (anchor-01), `current-worst` (anchor-24), and, as they exist, `wide`
      (large board), `mass` (250–400 units), `multilane`. Each declares its own budget in the
      file, so a budget change is a reviewable data diff.
- [ ] Add a `performance budget` gate check at **tier 4** ({{PRC-04}}): run each perf scenario,
      assert p95 frame time and p95 tick time against the declared budget, fail with the
      measured numbers in the detail line.
- [ ] Use **p95, not mean and not max.** Mean hides a stall; max is one GC pause or one
      shader compile and will make the check flap. Record the reasoning where the constant
      lives.
- [ ] Establish the baseline by measuring the current tree and committing the numbers as the
      first budget — 1.5 ms/frame total is the PRD figure; measure it here rather than
      inheriting it.
- [ ] Set budgets with headroom (suggest 1.5× the measured baseline) and require a **deliberate
      data edit** to raise one, so "the budget went up" appears in a diff and in a PR.
- [ ] Handle the machine-variance problem head on: this runs on Mesa llvmpipe software GL under
      Xvfb (decision 052), which is not the owner's Windows GPU path. Either pin the check to a
      known runner ({{PRC-08}}'s self-hosted runner) or assert on **relative** change against a
      committed baseline rather than an absolute wall-clock number. Choose one, state it, and
      say what the check does *not* prove.
- [ ] Add a headless sim-only budget too: `sim/run.py` timing per anchor, asserted in the same
      check. That number is machine-variant but not GPU-variant, so it is the more stable half.
- [ ] Wire the measurement into {{PRC-03}}'s JSON so a PR comment can show the frame-time delta.
- [ ] Record the baselines and the method in `docs/STATE.md` and add a `docs/DECISIONS.md`
      entry (p95, split draw/rules, relative-vs-absolute).
- [ ] Close LF-097.

## Acceptance criteria

- A `--scenario` run prints a summary containing p50/p95/max for draw and for tick, in
  microseconds.
- `tools/check.py --tier 4` includes `performance budget` and it is green on `main`.
- Adding a deliberate `for i in range(2_000_000)` busy loop into `anchor_view.gd`'s `_draw()`
  makes the check **red**, and the failure detail names the scenario, the measured p95 and the
  budget.
- Removing it makes the check green again.
- Raising a budget requires editing a `data/scenarios/perf/*.json` file — no budget constant
  lives in Python or GDScript.
- The check reports the sim-only figure separately from the frame figure.
- The check's docstring states explicitly what it does not prove (software GL, not the owner's
  GPU).

## Verification

```bash
.venv/bin/python tools/scenario.py data/scenarios/perf/current-worst.json
.venv/bin/python tools/check.py --tier 4 2>&1 | grep 'performance budget'
# deliberate regression
python - <<'EOF'
import pathlib
p = pathlib.Path('scripts/anchor_view.gd'); s = p.read_text()
# insert a busy loop at the top of _draw(), then revert with git checkout
EOF
.venv/bin/python tools/check.py --tier 4 2>&1 | grep 'performance budget'   # expect FAIL
git checkout -- scripts/anchor_view.gd
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **The sim budget is 5.6 ms, not 16.7 ms** (PRD §2.2) — the speed control goes to 3× so the
  rules run 90 ticks per wall second. A budget written against 16.7 ms passes a build that
  stutters at 3×, which is the speed a player uses to get through a lead-in.
- **A benchmark that flaps gets disabled.** Software GL under Xvfb on a shared machine will
  vary; tier 4 (nightly) placement plus p95 plus generous headroom is the mitigation, and
  relative-to-baseline is the fallback.
- **An occluded window stalls a rendered check and the check then passes** — 36 minutes,
  reported `ok` (LF-061). Closed on this machine by decision 052, but a machine without a native
  Linux Godot or `xvfb-run` falls back to a visible window, and a *stalled* frame loop would
  produce a garbage frame-time figure rather than a failure. The check must sanity-bound its own
  wall clock.
- **`--fixed-fps` makes reported FPS a constant.** Measure durations, not rates. LF-028 already
  recorded that frame pacing is not reproducible under `--fixed-fps` and was dropped for it.
- **A new `class_name` is invisible until the editor imports, and the symptom is a hang**
  (`CLAUDE.md`). Import in place (LF-075) if the instrumentation introduces one.
- Do not let the perf scenarios become the only scenarios that are maintained. They are
  load fixtures; correctness assertions belong in {{PRC-12}}'s functional scenarios.
- This check launches Godot repeatedly. Reap afterwards, and never background it.

## Files likely touched

- `scripts/scenario.gd`, `scripts/anchor_view.gd`, `scripts/anchor_sim.gd` (timers only)
- `data/scenarios/perf/*.json` (new), `data/schema/scenario.schema.json`
- `tools/scenario.py`, `tools/check.py`, `sim/run.py`
- `docs/STATE.md`, `docs/DECISIONS.md`, `backlog.json`
