id: TER-12
title: Line of sight — OPTIONAL and owner-gated; precomputed as path intervals
labels: rules, risk, design, phase-3
depends: TER-01, TER-02
milestone: E4 Terrain
---
## Problem

**This issue is optional and must not be started without the owner's explicit yes.** It is
open decision #3 in PRD §6, it is the largest single work item in E4, and **it invalidates
all 24 anchor grades**. Nothing else in E4 depends on it.

Terrain that blocks sight is what makes high ground and dead ground mean something rather
than just look like something. Without it, a ridge is decoration: a turret in a hollow
shoots straight through a 3-level plateau because the rules never learn the plateau exists
({{TER-01}} deliberately keeps terrain out of `sim/engine.py`).

**Decision 030 does not forbid this.** Its ban on `Vector2` stands and its ban on
transcendentals stands, but the probe in PRD §2.1 — 100,000 float64 pairs, 24 operations,
raw IEEE-754 bytes, on CPython, Linux Godot 4.7.1 and Windows Godot 4.7.1 — found
`+ − × ÷ sqrt fmod floor min max` and comparisons all at **0 mismatches out of 100,000**.
A visibility test needs no square root and no angle at all.

## Design — precompute, do not raycast

**A naive per-tick raycast is unaffordable and a parity minefield.** The target-acquisition
loop is already the hottest thing in the game (PRD §2.2: today's shape is 25.8 ms per tick
at 512 units / 60 emplacements against a **5.6 ms** budget, because the speed control goes
to 3×). Multiplying the innermost loop by ~16 sample points is not a tuning problem.

Instead: **precompute visibility as path intervals at build time.** The lane is fixed. For
each emplacement position, walk the lane once at fixed resolution and record the
`[d_lo, d_hi]` distance-along-path intervals over which the emplacement can see the lane.
Per tick the test becomes two comparisons against the unit's `dist` — **cheaper than the
range test that already exists**, which computes a squared distance.

**Use a fixed step count.** Sixteen samples per segment, `t = i / 16.0`. Deriving the step
count from `int(distance)` puts a rounding boundary inside the innermost rules loop, which
is exactly the shape of the anchor-14 six-leak bug quantified in PRD §2.1 (float32 vs
float64 disagreeing on `<= r` **10.2%** of the time on an exact integer radius).

## Tasks

- [ ] **Get the owner's decision first.** Do not open a branch before it.
- [ ] Specify the occlusion test in prose, in one place, before writing either
      implementation: sample the segment from emplacement to lane point at 16 fixed steps;
      at each step compare the interpolated sight-line height against the terrain height
      at that tile; blocked if terrain is above the line at any step.
- [ ] Implement the interval precomputation in `sim/engine.py` (or a new `sim/los.py`
      called from it) and in `scripts/anchor_sim.gd`, from the same prose, using only the
      safe operation set. No `Vector2` anywhere near it.
- [ ] Recompute intervals when a build changes the emplacement set — build time, not tick
      time. With {{PLC-01}}'s free placement this is per placement, so it must be fast
      enough to run on a click; measure it.
- [ ] Replace the range test's acceptance with `range AND interval`, in both engines, in
      the same commit.
- [ ] Add a fixture with a deliberately awkward sightline — an emplacement just behind a
      ridge crest, a lane grazing the crest — and assert both engines produce identical
      intervals to the bit.
- [ ] Re-grade all 24 anchors and record the deltas. Expect them to move; the point is
      that they move *and are re-tuned*, not that they happen not to.
- [ ] Make {{TER-08}}'s dead-slot check LOS-aware: a slot in range of the lane but with no
      sightline is dead in a way `dist_to_path()` (`tools/validate/validate_data.py:180`)
      cannot see. This is the check that caught anchor-06's two dead slots by measurement
      after several sweeps had blamed wave balance.
- [ ] Make the **position-aware policy** work (PRD §3 E7): `_slot_priority()` ranks by
      distance to the path and would happily build into blind slots. Coordinate with
      {{BAL-02}} — capped-core policies land there and a blind-slot-aware policy belongs in
      the same family.
- [ ] Draw it. A player cannot be asked to reason about sightlines they cannot see: the
      reach overlay (`_draw_reach()`, `scripts/anchor_view.gd:1091`) must show covered lane
      *segments*, not a circle, once LOS exists. A circle that lies is worse than no circle.
- [ ] Add a decision entry with the rejected alternative (per-tick raycast) and the
      measured reason.

## Acceptance criteria

- Owner's yes is recorded in `docs/DECISIONS.md` before any code lands.
- The two engines produce **bit-identical** intervals on the fixture and across the full
  864-run parity sweep.
- The per-tick cost of the visibility test is **lower** than the existing range test —
  measured, both before and after, at 512 units / 60 emplacements.
- Step count is a literal 16 in both implementations, with `t = i / 16.0`. Grep proves
  there is no `int(distance)`-derived step count anywhere.
- All 24 anchors are re-graded and re-tuned, and `tools/density.py` and the gate's
  `wave density` check are green afterwards.
- The reach overlay shows lane coverage, not a circle, on a board with terrain.
- The dead-slot validator check fires on a deliberately blind slot.

## Verification

```bash
.venv/bin/python tools/test_parity.py                # 864/864 with LOS active
.venv/bin/python -m sim.run --jobs 8                 # all 24 re-graded
.venv/bin/python tools/sweep.py anchor-20 --jobs 8   # re-tune whatever moved
.venv/bin/python tools/validate/validate_data.py     # blind-slot fixture errors
.venv/bin/python tools/density.py
```

Paste the parity summary, the before/after grade table for all 24, and the two per-tick
cost measurements.

## Risks / gotchas

- **PRD risk #4: line of sight in two languages, gated at 864 runs.** Any divergence
  surfaces as "a unit leaked in one engine" nine minutes after the commit, with no pointer
  to sightlines. Write the prose spec first and implement from it twice; do not port one
  implementation to the other language.
- Sampling at 16 steps means the test is an approximation. **Both engines must make the
  same approximation**, which is why the count is fixed and literal.
- Ties at the crest: a sight line exactly grazing terrain must resolve identically in both
  engines. Pick `>` or `>=` once, write it in the prose spec, and test the exact-grazing
  fixture.
- The precomputation is per emplacement × per lane sample. At 60 emplacements and a long
  multi-lane board ({{WAR-01}}) that is not free; measure it at build time, and if it is
  slow, cache per tile rather than per position.
- This invalidates every `capacity_mw` too, indirectly: fewer effective firing positions
  changes what a viable build costs, and the validator's saturation guard
  (`validate_data.py:150-165`) plus the briefs that read capacity aloud
  (`check_dialog_capacity`) both key off it.
- Do not let LOS creep into {{TER-13}}'s territory. Range stays 2-D; visibility is a
  separate predicate.

## Files likely touched

- `sim/engine.py`, `sim/los.py` (new)
- `scripts/anchor_sim.gd`
- `scripts/anchor_view.gd`
- `tools/validate/validate_data.py`
- `data/anchors/*.json` (re-tuned), `data/tuning.json`
- `docs/DECISIONS.md`
