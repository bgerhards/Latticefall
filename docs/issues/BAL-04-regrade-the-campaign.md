id: BAL-04
title: Re-grade the whole campaign at theatre scale
labels: phase-4, design, content
depends: BAL-01, BAL-02, BAL-03, PLC-05, WAR-01
milestone: E7 Balance
---
## Problem

Every balance figure in this project is a function of board size, lane count, unit budget and
what the grading policy can do — and Theatre Scale moves all four (LF-096). The 24-row grade
table in `docs/STATE.md` was produced by a harness that never presses a button, on 18×15
boards with one lane, 8–12 fixed slots and a peak of 32 units on screen. After E3, E4, E5 and
{{BAL-01}}–{{BAL-03}} it describes a game that no longer exists.

Two specific measurements go stale in ways that are easy to miss:

- **The wave-density gate check compares acts against the busiest act's peak concurrency**
  with a `DENSITY_FLOOR = 0.55` (`tools/check.py:112-147`). The current figures are Act I 32
  units, Act II 27 (85%), Act III 21 (65%). Multi-lane changes what "on screen" means — units
  spread across lanes are less dense per lane at the same total — so the ratio needs rebasing
  or the check either fails on healthy content or passes on thin content.
- **`PRESSURE_FLOOR` in `sim/run.py:36` is already a dead check** (LF-054): peak load ratio is
  ≥100% of capacity on all 24 anchors × 3 difficulties and ≥75% even excluding the
  greedy-overdraw policy, so "the player is pressed against capacity" can never fail.

And the trap this project has already fallen into once is waiting: **density must never be paid
for with reactor capacity.** anchor-24 reached **103% of what would run every slot at maximum
draw**; every anchor still graded clean, and *the power decision the whole game is about had
stopped existing on five levels* (`docs/STATE.md`). The grader cannot see it — a level with no
decision in it is still winnable.

## Tasks

- [ ] Do not start until {{PLC-05}}'s replacement saturation invariant exists. Without a
      denominator there is nothing bounding capacity, and the sweep will buy clean grades with
      reactor megawatts exactly as it did before (LF-107, PRD §7 risk 1).
- [ ] Re-derive the density targets for the new board and lane counts, and write the derivation
      into `tools/density.py`'s docstring. Peak units *in flight* stays the metric — a Column at
      0.5 tiles/sec holds the board four times as long as a Shard (`tools/check.py:115-127`).
- [ ] Decide what "screen presence" means with a camera ({{CAM-01}}) and multiple lanes
      ({{WAR-01}}): total units alive, or units within the current view? Record the choice; the
      gate check's comparison depends on it.
- [ ] Rebase `DENSITY_FLOOR` and the act comparison in `tools/check.py`, with the new measured
      per-act figures in the commit body.
- [ ] Fix or retire `PRESSURE_FLOOR` (LF-054): measure pressure on **winning builds only**, or
      raise the floor until it discriminates. A check that cannot fail is worse than no check.
- [ ] Re-derive the leak budget per act. Current: Act I 8.7%, Act II 11.4%, Act III 20.4%
      (`docs/STATE.md`). A leak costs the unit's `leak_cost = max(1, round(hp/130))`
      (decision 047), and lives must be compared against the wave's **total `leak_cost`**, never
      raw — `sweep.py --lives %12` takes it in that form for that reason.
- [ ] Re-sweep every anchor with `tools/sweep.py`, passing `--weight` and `--lives` explicitly.
      A sweep proves nothing outside its grid and prints its box next to the verdict; the box
      must be widened deliberately, not defaulted.
- [ ] Reconcile the capacity ladder by hand afterwards to a **monotone envelope** across the
      campaign. A sweep tunes one anchor blind to the others and will happily leave capacity
      dipping mid-act (`docs/STATE.md`).
- [ ] Re-run `tools/say_capacity.py` — Control reads the bus figure aloud in every brief, and
      `sweep.py --apply` moves `capacity_mw` without touching a word of prose. Sixteen briefs
      had drifted last time this happened, and `dialog capacity` is a gate check.
- [ ] Re-grade with `sim/run.py --jobs 8` and regenerate `docs/STATE.md`'s grade table with
      `tools/session.py`.
- [ ] Check composition, not just the verdict: **a transform free to cut the wave table will
      destroy it** — 251 of 252 Act III spawn entries once came out at `count: 1`, every wave
      reduced to "N shards and one of each". Flattening composition is a content loss, not a
      tuning miss.
- [ ] Re-run the full gate at tier 4, including the 864-run (or larger) parity set.
- [ ] Update `docs/STATE.md`'s act table, grade table and "None of the new gameplay is balanced
      — only verified" paragraph, which this issue is what finally retires.
- [ ] Add a `docs/DECISIONS.md` entry for the rebased density and leak targets, superseding
      decisions 044, 047 and 048 by reference rather than by edit.

## Acceptance criteria

- All 24 anchors grade `ok` against the new policy set, at all three difficulties.
- Every anchor has at least the project's existing robustness threshold of distinct winning
  builds (`ROBUST_ENOUGH = 8` per decision 044), and at least one winning build on each anchor
  contains **more than one weapon id** ({{BAL-02}}'s output — a mixed board is now gradeable and
  must be shown to be reachable).
- No anchor's `capacity_mw` exceeds the {{PLC-05}} saturation bound; `validate_data.py` reports
  zero saturation errors and zero warnings above 80%.
- `wave density` passes with rebased per-act figures, and those figures are recorded in
  `docs/STATE.md`.
- `PRESSURE_FLOOR` either fails on a deliberately slack anchor or has been removed with a
  written reason.
- `dialog capacity` passes: 24 briefs quote their own capacity.
- Composition is preserved: no act has more than 10% of its spawn entries at `count: 1`.
- `rules parity` identical.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python tools/density.py
.venv/bin/python -m sim.run --jobs 8
.venv/bin/python tools/say_capacity.py
.venv/bin/python -u tools/check.py            # tier 4, full
.venv/bin/python tools/session.py             # regenerate STATE's AUTO block
.venv/bin/python tools/reap.py
```

Proof: 24 `ok` verdicts, a green tier-4 gate, and the regenerated grade table in
`docs/STATE.md`.

## Risks / gotchas

- **Density must never be paid for with reactor capacity.** The sweep will do exactly that if
  allowed; capacity is bounded at 70% of board saturation in the sweep grid and
  `validate_data.py` errors at saturation and warns from 80%. Keep both, and make sure the new
  saturation denominator from {{PLC-05}} is what they read.
- **A derived quantity that another term compensates for does not announce itself.**
  `solve_scale`'s denominator was algebraically wrong for six revisions; the escort residual
  absorbed the error, so the tables looked plausible while the target was missed by a fifth on
  every Act III anchor. **Re-derive from the docstring, do not read the code.**
- **Preview a rule before you spend cores on it** — `tools/densify.py --preview` prints the wave
  table a rule change *would* write. Four of six rule versions on the last pass were wrong and
  each cost a thirty-minute sweep to disprove.
- **A raw life count means nothing on its own.** Compare against the wave's total `leak_cost`.
- **Act III is a measured ceiling, not a stopping point** (`docs/STATE.md`): the finale already
  carried 2,700 hp and ran at 89% of capacity before escorts, and capacity cannot buy the
  difference. Act III escorts must be nearly free in **both** hp and drain.
- Parity wall clock grows with unit count — PRD §7 risk 6 is 9 minutes → potentially hours at
  10× units. {{PRC-05}}'s hash gating and sharding are what make this issue's verification loop
  affordable at all.
- This is a long, machine-heavy job. Use `--jobs`, run in the **foreground**, and reap. A
  forgotten sweep pool bills the owner (`CLAUDE.md`).

## Files likely touched

- `data/anchors/anchor-*.json` (all 24), `data/dialog/anchor-*.json` (capacity lines)
- `tools/check.py` (`DENSITY_FLOOR`, `check_wave_density`), `tools/density.py`
- `sim/run.py` (`PRESSURE_FLOOR`), `tools/sweep.py` (grid bounds)
- `docs/STATE.md`, `docs/DECISIONS.md`, `backlog.json`
