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

- [x] Do not start until {{PLC-05}}'s replacement saturation invariant exists. Without a
      denominator there is nothing bounding capacity, and the sweep will buy clean grades with
      reactor megawatts exactly as it did before (LF-107, PRD §7 risk 1). **Done** — E3 is 7/7
      and `validate_data.py` reports `ok — 0 warning(s)` on the shipped campaign.
- [x] Establish a grade-quality selector that can see a difficulty dissolve. **Done, decision
      086** (LF-243): `win_share` per difficulty, and the top tier's share must fall strictly
      below the bottom tier's on every anchor. The old knife-edge rule let decision 082's
      derived ranges take brutal from 24% to 43% campaign-wide without moving one verdict, so
      re-grading against the old table would have produced a green result that meant nothing.
- [ ] Re-derive the density targets for the new board and lane counts, and write the derivation
      into `tools/density.py`'s docstring. Peak units *in flight* stays the metric — a Column at
      0.5 tiles/sec holds the board four times as long as a Shard (`tools/check.py:115-127`).
- [ ] Decide what "screen presence" means with a camera ({{CAM-01}}) and multiple lanes
      ({{WAR-01}}): total units alive, or units within the current view? Record the choice; the
      gate check's comparison depends on it.
- [ ] Rebase `DENSITY_FLOOR` and the act comparison in `tools/check.py`, with the new measured
      per-act figures in the commit body.
- [x] Fix or retire `PRESSURE_FLOOR` (LF-054). **Done, decision 067 — it was deleted, not
      raised**, because a threshold picked to make today's data fail is fitted to that data.
      Nothing in `sim/` or `tools/` computes it any more; only a tombstone comment in
      `sim/run.py` remains, and that comment names the replacement worth building (time spent
      within a band of capacity across a *winning* run) as design work with its own evidence.
      This task and its acceptance bullet were both stale for three sessions.
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

**Every bullet below carries the measured baseline it is judged against.** The previous
version of this section did not: **three of its nine criteria were already failed by the
campaign they were written to describe, and a fourth had been satisfied three sessions
earlier** — see decision 088 and `LF-255`. (Nine criteria in eight bullets; one was
compound.) A criterion invented before the measurement is a criterion this workstream would
have had to argue its way out of at the end, which is the worst possible time to discover it.

> **The live bar is `tools/bal04_baseline.json`, not the prose below.** `LF-270`/`LF-278`:
> five slices hand-rolled this table from `sim/run.py`'s JSON before it got a tool, and each
> rediscovered the same traps. Run it:
>
> ```bash
> .venv/bin/python tools/criteria.py --jobs 8 --verbose    # all six criteria vs the artefact
> ```
>
> **Three numbers written below have since been superseded and are kept for the record only** —
> multi-weapon is **22/22** achievable, not 17/22 (decision **090**); act 1's `count: 1` share
> is **7.9%**, not 10.1% (decision **091**); and the per-anchor `standard + brutal` list is
> decision 088's at `54c666b`, five of whose eight anchors have since improved (03 6→7, 04
> 6→10, 05 7→8, 07 7→8, 08 5→6). Reading the prose as the live bar under-measures by up to four
> builds on anchor-04. The artefact carries the commit it was taken on; re-baseline with
> `--rebaseline`, which refuses on a regression unless you say so explicitly.

Baselines below are the shipped 18×15 campaign as of `54c666b`, reproducible with
`.venv/bin/python -m sim.run --jobs 8` and the one-liners in each bullet.

- **All 24 anchors grade `ok`, at all three difficulties.** Baseline **24/24**. Note this is
  now a stronger statement than when it was written: decision 086 added the win-share rule, so
  `ok` also asserts the top difficulty is harder than the bottom one on every anchor.
- **No anchor's `standard + brutal` distinct winning builds falls below its own baseline.**
  Baseline is **not** a flat 8. Decision 044's `ROBUST_ENOUGH = 8` caps the *benefit* of extra
  builds inside `tools/sweep.py`'s scorer — a saturation point for a search, never a floor —
  and read as a floor it fails on **8 of 24 anchors today**: anchor-01 (3), 02 (7), 03 (6),
  04 (6), 05 (7), 07 (7), 08 (5), 23 (7). So the bar is *no anchor gets worse*, per anchor,
  against those numbers. `LF-255`, decision 088.
- **Every anchor with two or more weapon ids unlocked keeps at least one winning build
  containing more than one weapon id.** Baseline **17 of the 22 anchors where it is
  achievable** — 17 of 24 unconditioned; keep the two denominators apart. The condition is
  load-bearing: anchor-01 and anchor-02 unlock exactly **one** weapon, so the criterion is
  impossible there by construction and the old unconditional wording made them permanent
  failures. The five
  achievable misses are anchor-03, 04, 05 (two weapons unlocked) and anchor-07, 08 (three, while
  anchor-06 with the same three does meet it) — all Act I, and all real content findings for this
  workstream rather than defects in the criterion.
- **No anchor's `capacity_mw` exceeds the {{PLC-05}} saturation bound.** Baseline
  `validate_data.py` → `ok — 0 warning(s)`, so the bar is that it stays there.
- **`wave density` passes with rebased per-act figures**, and those figures are recorded in
  `docs/STATE.md`. Baseline: act 1 32 on screen (100%), act 2 27 (85%), act 3 21 (66%).
- **`dialog capacity` passes:** 24 briefs quote their own capacity. Baseline **24/24**.
- **No act's share of spawn entries at `count: 1` rises above its baseline.** Baselines are act 1
  **10.1%** (15/148), act 2 **29.4%** (106/361), act 3 **33.8%** (108/320) — so the old
  "no act above 10%" bullet was failed by all three acts on the day it was written. The intent
  behind it is sound and unchanged (251 of 252 Act III spawn entries once came out at `count: 1`
  and every wave collapsed to "N shards and one of each"); it is a **regression bound**, and it
  is stated as one now rather than as an absolute nobody measured.
- **`rules parity` identical**, both platforms.

**Deleted rather than rewritten:** *"`PRESSURE_FLOOR` either fails on a deliberately slack
anchor or has been removed with a written reason."* Decision **067** removed it, with a written
reason, three sessions before this was read. The task list above is corrected to match.

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
