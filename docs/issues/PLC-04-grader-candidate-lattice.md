id: PLC-04
title: Grader placement strategy — a deterministic candidate lattice replaces _slot_priority()
labels: phase-2, tooling, rules
depends: PLC-02
milestone: E3 Placement
---
## Problem

`sim/engine.py:225-240` `_slot_priority()` ranks `self.free_slots` by squared distance to the
path and `_try_build()` (`:242-264`) loops `while self.free_slots:`, taking `slot_order[0]` each
time. With no slots there is no candidate set and the grader cannot build anything at all — so
every one of the 24 anchor grades in `docs/STATE.md` stops existing the moment {{PLC-01}} lands.
The same loop is duplicated in GDScript at `scripts/test/parity.gd:172-217`, which is what the
864-run parity gate drives, so it is two implementations that must agree candidate-for-candidate.

The cheapest honest replacement is a **deterministic candidate lattice**: every ½ tile across the
board, minus the lane standoff, sorted by `(d2_to_path, x, y)`. That leaves `_try_build`
structurally unchanged — it still pops the best remaining candidate — and keeps the grader's
build order a total order with no RNG, which is what makes the sim deterministic *structurally*
rather than by seed (`sim/engine.py:1-21`).

Two things must be said out loud, because both are easy to get wrong:

**It costs 80x more candidates.** `_slot_priority()` is recomputed on **every iteration** of
`_try_build`'s while loop (`:245`), and its `d2()` samples the path at `max(2, int(path_length))`
points **per candidate**. At 12 slots that is invisible. A ½-tile lattice on 18x15 is ~1,015
candidates; on 64x64 it is ~16,000. Multiplied by the rebuild-per-iteration, by 24 anchors, by 3
difficulties, by every policy, by 864 parity runs, it is the difference between a 9-minute gate
and PRD risk #6 ("parity wall-clock at 10x units: 9 minutes → potentially hours").

**It does not fix LF-053, and it makes it worse.** Every `Policy` is a total preference order and
`_try_build` fills greedily from the top, so a graded build is all-of-one-thing. With 12 slots
that produced "twelve arc nodes"; with 1,015 candidates it produces "as many arc nodes as money
and the cap allow". {{BAL-02}}'s capped-core policies are the fix and this issue must not be read
as one. {{PLC-05}}'s `max_emplacements` is what actually stops the fill.

## Tasks

- [ ] Build the candidate lattice **once per anchor**, cached on the `Anchor`: every position at
      ½-tile spacing inside the board, filtered by {{PLC-02}}'s bounds and lane tests using the
      *largest* footprint in `towers.json` (a candidate legal for the biggest gun is legal for
      all of them; a per-tower filter happens at build time).
- [ ] Sort once by `(d2_to_path, x, y)`. Compute `d2_to_path` with the same segment-exact
      point-to-segment distance {{PLC-02}} introduces, not the `steps`-sampled approximation at
      `:232-239` — one distance function, used by the rule and the grader, is one fewer thing to
      keep in step.
- [ ] Change `_try_build()` to consume from an index into the sorted lattice and to skip
      candidates that fail the *overlap* test against what is already placed. **Do not re-sort
      per iteration.** Keep the loop's structure otherwise identical, including the `for tower in
      self.buildable` / `else: return` shape, so the diff is readable against the old behaviour.
- [ ] Mirror the lattice, the sort and the consumption exactly into
      `scripts/test/parity.gd:172-217`. The two must produce the same candidate at the same step
      or parity fails on the *first* wave of the *first* anchor.
- [ ] Pin the lattice spacing to a **binary fraction** (½ or ¼ tile). This is not cosmetic:
      {{PLC-01}} formats positions into the parity signature (`sim/engine.py:422`,
      `parity.gd:155`) and a binary fraction round-trips exactly through `%.4f` on both sides,
      where a decimal spacing like 0.3 does not.
- [ ] Measure the parity wall clock before and after on a single anchor, then on the full 864.
      If it regresses more than ~2x, cache `d2_to_path` per candidate (it is anchor-static) and
      re-measure before considering anything cleverer.
- [ ] Re-grade all 24 anchors and record the new table. Expect real movement: the grader can now
      stand a gun where a level author never put a slot, which is the point.
- [ ] Regenerate `docs/STATE.md`'s anchor-grade block with `tools/session.py` and note in the PR
      which anchors changed verdict, if any.
- [ ] Check `tools/sweep.py` and `sim/run.py` for anything that assumes `free_slots` exists —
      `PRESSURE_FLOOR` (LF-054) and the sweep's capacity bound both read board saturation.
- [ ] Add a note to `docs/BACKLOG.md` on LF-053/LF-095 recording that the lattice enlarges the
      problem rather than solving it.

## Acceptance criteria

- `tools/test_parity.py` reports **864 runs identical** — the Python and GDScript lattices agree
  on every candidate, in order.
- The lattice is built once per anchor: instrument and assert one build per `Sim` construction,
  not one per `_try_build` iteration.
- Parity wall clock stays within 2x of the current 542 s on the same machine, and the number is
  in the PR.
- Every anchor still grades `ok` at all three difficulties, and the new table is recorded.
- Two runs of `sim.run --jobs 8` and `--jobs 1` produce identical output — the lattice introduces
  no order dependence.
- `_try_build` contains no sort.

## Verification

```bash
.venv/bin/python -m sim.run --jobs 8  > /tmp/grades-after.txt
.venv/bin/python -m sim.run --jobs 1  > /tmp/grades-1job.txt
diff /tmp/grades-after.txt /tmp/grades-1job.txt        # must be empty
time .venv/bin/python tools/test_parity.py
.venv/bin/python tools/check.py
.venv/bin/python tools/session.py                       # refresh STATE's grade table
.venv/bin/python tools/reap.py
```

Proof is `864 runs identical`, the empty `--jobs` diff, and the timing line next to the old 542 s.

## Risks / gotchas

- **Grid-hash and lattice tie-break ordering breaking parity intermittently is PRD risk #3, and
  LF-055 is the precedent** — `==` on a Dictionary is a value comparison in Godot 4.7, so two
  identical-looking candidates compared equal and the two engines disagreed only when a wave
  happened to produce a tie. Sort on `(d2, x, y)` with the full tuple, always; never rely on a
  stable sort matching across languages, because Python's `sorted` is stable and GDScript's
  `sort_custom` is not guaranteed to be.
- **`_slot_priority()`'s `d2()` is O(path_length) per candidate and is called per iteration.**
  Leaving that shape in place with 1,000 candidates is the single most likely way to turn the
  parity gate into an hours-long run.
- **This is grader policy, not a rule.** The lattice lives in the policy layer; `anchor_sim.gd`
  has no equivalent, because the player places by hand. But `parity.gd` *replays* the policy, so
  the lattice must be mirrored there and only there on the GDScript side.
- **A sweep proves nothing outside its grid** and a sweep tunes one anchor blind to the others
  (`docs/STATE.md`). If capacities move after re-grading, reconcile the ladder to a monotone
  envelope by hand and re-run `tools/say_capacity.py`.
- Kill the worker pools: `sim/run.py` and `tools/sweep.py` `--jobs` pools survive their parent
  and re-invoke the model when they exit. `tools/reap.py` after every run.

## Files likely touched

- `sim/engine.py` (`_slot_priority` → lattice, `_try_build`)
- `sim/content.py` (cached lattice on `Anchor`)
- `scripts/test/parity.gd` (`:172-217`)
- `tools/sweep.py`, `sim/run.py`
- `docs/STATE.md` (grade table), `docs/BACKLOG.md`
