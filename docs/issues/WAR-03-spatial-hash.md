id: WAR-03
title: Spatial hash for target acquisition
labels: perf, rules, engine, phase-2
depends: WAR-01, WAR-02
blocks: WAR-06
milestone: E5 War
---
## Problem

Target acquisition is O(emplacements × units) every tick in both engines —
`scripts/anchor_sim.gd:500-525` and `sim/engine.py:333-344` — and it is multiplied by the
speed control, which reaches 3× (`data/tuning.json` `pacing.speeds`), so **the budget is
5.6 ms per tick, not 16.7**. PRD §2.2 measured, on Linux Godot headless: at 512 units /
60 emplacements / 24 segments the tick costs **25.8 ms today, 3.1 ms** once positions are
hoisted ({{WAR-02}}), and **1.0 ms** with a uniform grid hash. At 1000 / 80 / 32 it is
76.2 → 8.3 → **2.3 ms**. Without the index, a 250–400 unit board ({{WAR-06}}) does not fit
inside the budget at 3× speed.

**This is the highest-risk parity change in E5.** The targeting loop keeps the *first*
candidate on a tie (`anchor_sim.gd:523` `keep = target.is_empty() or ...`, `sim/engine.py:343`
`if target is None or u.dist > target.dist`), so which unit a gun shoots depends on the order
candidates are visited. LF-055 is the precedent and it is the same defect in a different
disguise: `u == target` versus `u is target` diverged for exactly this reason and stayed
latent until two identical units shared a tick. An index that iterates buckets in insertion
order will diverge intermittently, on some anchors, at some unit counts, with no reproducer.

## Tasks

- [ ] Write a `docs/DECISIONS.md` entry fixing the **candidate ordering contract**: the index
      yields candidate unit indices in **ascending `units` index order**, in both engines,
      always. Record the rejected alternative — iterating buckets in insertion order, rejected
      because it makes the tie-break depend on spawn history and container internals, which is
      LF-055 with a new face.
- [ ] Choose and document the cell size. A uniform grid keyed on
      `(floor(x / CELL_W), floor(y / CELL_W))` with `CELL_W` a **constant in both files**, not
      per-anchor data — a per-anchor cell size is a per-anchor bucket order and therefore a
      per-anchor tie-break. `floor` is in the safe operation set (PRD §4.2).
- [ ] `sim/engine.py`: build the index once per tick from {{WAR-02}}'s position pass, storing
      **unit indices**, appended in ascending index order so each bucket is already sorted by
      construction.
- [ ] `sim/engine.py`: replace the targeting scan with a walk over the cells overlapping the
      emplacement's range square, collecting candidate indices, **sorting the collected list
      ascending** before the preference test. Sorting the merged candidate list is not
      optional even when each bucket is sorted — the merge across cells is not.
- [ ] `scripts/anchor_sim.gd`: the same structure, same constant, same ascending sort. Use a
      `Dictionary` keyed by a packed `int` cell key (`cy * STRIDE + cx` with a stride wider
      than any board) rather than a `Vector2i` key — `Vector2i` hashing order is an engine
      internal and the whole point is that ordering is ours.
- [ ] Keep the *linear* scan available behind a constant in both files and add a
      cross-check mode to the benchmark that runs both and asserts the selected target index
      is identical on every tick of a full anchor. Delete neither until that has run green
      across all 24 anchors at all three difficulties.
- [ ] Apply the same index to the splash loop (`anchor_sim.gd:556-567`, `sim/engine.py:349-356`)
      — it is the same shape and the same tie-break exposure.
- [ ] Leave `_covered_by()` alone here; it is {{WAR-05}}.
- [ ] Extend `tools/bench_tick.py` with the index enabled and reproduce the PRD's four cells
      (512/60/24 and 1000/80/32, before and after).
- [ ] Re-run the 864-run parity set. Then run it again with a wave table that deliberately
      spawns two identical units on the same tick into the same lane, which is the case
      LF-055 says is latent.
- [ ] Re-grade all 24 anchors; the diff must be empty.
- [ ] Record the measured ms/tick and the cell-size choice in `docs/STATE.md`.

## Acceptance criteria

- The cross-check mode reports **zero** differing target selections over a full run of all
  24 anchors × 3 difficulties, linear versus indexed.
- `.venv/bin/python -m sim.run --jobs 8` is byte-identical to the pre-change run.
- Parity passes 864/864, including on a purpose-built anchor with simultaneous identical
  spawns.
- Measured ≤ 1.5 ms/tick at 512 units / 60 emplacements / 24 segments (PRD measured 1.0), and
  ≤ 3.0 ms at 1000 / 80 / 32 (PRD measured 2.3).
- Neither implementation contains a code path where candidate order depends on a container's
  iteration order.

## Verification

```bash
.venv/bin/python tools/bench_tick.py --units 512 --towers 60 --segments 24 --crosscheck
.venv/bin/python tools/bench_tick.py --units 1000 --towers 80 --segments 32
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/test_parity.py --anchor anchor-XX --verbose   # the simultaneous-spawn anchor
```

Proof to paste: the cross-check line reading `0 differing selections`, both benchmark lines,
the empty `diff`, and parity's 864/864 twice.

## Risks / gotchas

- **Ties are the bug.** Two units at the same `dist` in range of the same gun is not
  hypothetical once lanes exist — {{WAR-01}} makes simultaneous spawns routine.
- A range test against a *square* of cells is a superset of the circular range; the exact
  squared-distance test at `anchor_sim.gd:507` must still run on every candidate. Do not
  "optimise" it away — decision 030's whole argument is that both runtimes do the same double
  arithmetic.
- `floor` on a negative coordinate: free placement (PLC-01) and boards with negative-adjacent
  geometry can produce `x < 0`. `floor` is safe, C-style truncation is not; use `floor` in
  both languages and never `int()` on a negative float.
- Rebuilding the index every tick is the correct choice — an incrementally maintained index
  would carry state across ticks and is a second place for the two implementations to drift.
  The measurement above already includes the rebuild.
- The parity wall clock is already 83.6% of gate time (PRD §3 E1) and risk 6 in the register
  is parity time at 10× units. Do not raise unit counts in the parity fixtures in this issue.

## Files likely touched

- `sim/engine.py`
- `scripts/anchor_sim.gd`
- `tools/bench_tick.py`
- `docs/DECISIONS.md`, `docs/STATE.md`
