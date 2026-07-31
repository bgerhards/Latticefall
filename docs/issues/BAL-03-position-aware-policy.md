id: BAL-03
title: Position-aware policy — rank a position by the path length visible from it
labels: phase-4, tooling, design
depends: PLC-01, TER-01
blocks: BAL-04
milestone: E7 Balance
---
## Problem

`_slot_priority()` (`sim/engine.py:225-240`) ranks build positions by **squared distance to
the nearest point on the path**, sampling `max(2, int(path_length))` points and taking the
minimum. On today's boards — one lane, 8–12 fixed slots, no terrain — that is a reasonable
proxy for "covers something". It stops being one the moment either of two things lands:

- **Free placement** ({{PLC-01}}) turns position into a continuous float64 value with a
  footprint radius, so "the nearest free slot" is no longer a choice from a short list. A
  grading policy that always builds at the point nearest the lane will produce one shape of
  board on every anchor and grade the game against it.
- **Line of sight** ({{TER-01}}) makes "near the path" and "can shoot the path" different
  questions. `sim/engine.py`'s ranking would happily build into a **blind slot** — adjacent to
  the lane, behind a ridge, covering nothing — and then grade the anchor as unwinnable for a
  reason that is the harness rather than the level. That is the failure mode `docs/STATE.md`
  warns about seven times over: *"Check the harness before the level."*

The right metric is already named in the PRD (§3 E7): the ranking must become **"path length
visible from here"** — how much of the lane this position can actually deliver damage to,
weighted by the weapon that will stand there.

## Tasks

- [ ] Replace `_slot_priority()`'s distance metric with a coverage metric:
      for a candidate position and a weapon range, walk the path at a fixed sample step and sum
      the length of path within range **and** (once {{TER-01}} lands) visible.
- [ ] Fix the sample step as a **constant in path units**, not `max(2, int(path_length))`
      samples. The current formula makes the ranking's resolution depend on the anchor's path
      length, so two boards are ranked at different fidelities — invisible today, and wrong the
      moment lane lengths vary by 4×.
- [ ] Make range weapon-specific. Today the ranking is weapon-agnostic and the tower is chosen
      afterwards; a mortar and a pulse turret want different ground. Rank
      (position, tower) pairs, or rank per candidate tower inside `_try_build`.
- [ ] Keep the whole computation inside the **safe operation set** — `+ − × ÷ sqrt fmod floor
      min max` and comparisons, never `atan2/sin/cos/tan/pow/log/exp`, never `Vector2`
      (PRD §2.1, §4.2). A visibility test is a segment/segment or segment/AABB intersection and
      is expressible with multiplies and comparisons; do not reach for an angle.
- [ ] Compare squared distances where possible and only take `sqrt` where a real length is
      summed. `sqrt` is **safe** — IEEE-754 §5.4.1 requires correct rounding, both runtimes
      issue `SQRTSD`, and it matched 100,000/100,000 across CPython, Linux Godot and Windows
      Godot (PRD §2.1). Decision 030's ban was about `Vector2.distance_to`, a float32 helper.
- [ ] Consume {{TER-01}}'s **precomputed visibility intervals**. The PRD's line-of-sight design
      is "visibility as path intervals at build time, O(1) per tick"; the ranking should use the
      same intervals rather than raycasting, or a policy evaluation becomes O(candidates ×
      samples × occluders).
- [ ] Add a total tie-break. `_slot_priority` currently sorts by `(d2(s), s)` — the trailing
      `s` is what makes it deterministic across runtimes, and any replacement needs an
      equivalent. Two positions with identical coverage must order identically in Python and
      GDScript.
- [ ] Mirror the whole ranking into `scripts/test/parity.gd` and re-run the full parity set.
      A ranking difference changes `built`, which is one of the `EXACT` compared fields
      (`tools/test_parity.py:37`).
- [ ] Bound the cost. With free placement the candidate set is continuous — define the
      candidate generator explicitly (a grid at some spacing? the tiles adjacent to buildable
      area?) and record its size, because the grader runs it once per build per policy per
      difficulty per anchor.
- [ ] Add a degenerate-case guard: if no candidate has non-zero visible path length, say so in
      the outcome rather than building at the first position. "This anchor has no ground worth
      holding" is a level defect the grader should surface, not absorb.
- [ ] Write a unit-level check with a hand-computed board — a straight lane, one ridge, three
      candidate positions with known visible lengths — so the metric is verifiable without a
      full grade.
- [ ] Update `docs/STATE.md` and record the change in `docs/DECISIONS.md` (position ranking is
      coverage, not proximity; rejected alternative: keep proximity and add a blind-slot
      exclusion list, which is content maintenance for a computable property).

## Acceptance criteria

- On a board with a blind position adjacent to the lane and a covering position further away,
  the policy chooses the covering position. Verified with the hand-computed test board, with
  the expected visible lengths written into the test.
- The ranking is weapon-aware: a long-range weapon and a short-range weapon choose different
  positions on at least one authored anchor.
- `rules parity` is identical — the same `built` list from Python and GDScript on all runs.
- Two positions with equal coverage order identically on both runtimes (assert with a
  deliberately symmetric board).
- No `atan2`, `sin`, `cos`, `tan`, `pow`, `log`, `exp` or `Vector2` appears in the new code —
  enforced by {{BAL-07}}'s gate check, not by inspection.
- Grading wall clock does not regress by more than 2× (measure and record; the candidate set is
  the knob).
- An anchor with no visible ground reports that explicitly instead of grading unwinnable.

## Verification

```bash
.venv/bin/python -m pytest sim/tests/test_slot_priority.py -q     # or the project's chosen form
.venv/bin/python -m sim.run --jobs 8 --json > /tmp/grades.json
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/check.py --tier 1 2>&1 | grep 'safe operations'
time .venv/bin/python -m sim.run --jobs 8 > /dev/null
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **Range stays 2-D in v1** (PRD §3 E4). True 3-D distance is physically right and plays wrong —
  a turret on a hill would cover less plan area than one in the valley. The coverage metric must
  use plan distance, with a high-ground bonus as the tuning knob if one is wanted.
- **Line of sight invalidates all 24 grades** (PRD §6, open decision 3) and is the largest
  single work item in E4. If {{TER-01}} ships without it, this issue ships the coverage half
  and leaves visibility as a no-op stub — say so in the docstring rather than pretending.
- **`built` is compared exactly by the parity harness** (`tools/test_parity.py:37`). A ranking
  that differs by one position in the last bit is a hard parity failure, not a rounding
  tolerance. This is the highest-parity-risk item in E7.
- **LF-055 is the precedent for a latent tie-break bug**: Godot compares `Dictionary` by value,
  Python by identity, and it stayed hidden until two identical things collided. A coverage
  metric will produce ties constantly — symmetric boards are common.
- **Free placement removes the board-saturation denominator** (LF-107, PRD §7 risk 1) and
  {{PLC-05}} replaces it. Do not let a position-aware policy paper over a missing invariant by
  simply building fewer things.
- **A sweep tunes one anchor blind to the others** (`docs/STATE.md`). Any retune that follows
  belongs in {{BAL-04}}.

## Files likely touched

- `sim/engine.py` (`_slot_priority`, `_try_build`)
- `scripts/test/parity.gd`, `scripts/anchor_sim.gd` (only if visibility is a rule)
- `sim/content.py` (terrain/visibility intervals from {{TER-01}})
- `sim/tests/` (new)
- `docs/STATE.md`, `docs/DECISIONS.md`
