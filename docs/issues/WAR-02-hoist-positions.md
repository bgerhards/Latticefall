id: WAR-02
title: Resolve every unit position once per tick instead of once per test
labels: perf, rules, engine, phase-2
depends: WAR-01
blocks: WAR-03
milestone: E5 War
---
## Problem

`point_at_xy()` is called once per unit in the movement loop, once per unit **per online
emplacement** in the targeting scan, and once per unit again in every splash loop —
`scripts/anchor_sim.gd:448`, `:504`, `:562`, mirrored at `sim/engine.py:312`, `:336`, `:352`.
It is a linear scan over the segment table (`sim/content.py:121-128`) and it returns the same
answer every time within a tick, because `u.dist` only changes in the movement phase. PRD §2.2
measured the consequence at 512 units / 60 emplacements / 24 segments: **25.8 ms per tick
today, 3.1 ms with positions hoisted to one pass per tick** — an 8.3× reduction before any
indexing structure exists at all. At 1000 units / 80 emplacements / 32 segments it is
76.2 ms → 8.3 ms. **The budget is 5.6 ms, not 16.7**, because the speed control goes to 3×
(`data/tuning.json` `pacing.speeds`) so the rules run 90 ticks per second of wall clock.

This is the cheapest of the three scale fixes and it is a prerequisite for {{WAR-03}}: a
spatial index has to be built from resolved positions, so the pass has to exist first.

## Tasks

- [ ] `sim/engine.py`: at the top of `_step()`, after the movement loop, build
      `pos: list[tuple[float, float]]` indexed by the same index as `self.units`, resolving
      `self.a.point_at(u.lane, u.dist)` exactly once per live unit. Dead units get a resolved
      position too (or a sentinel) so the list stays index-parallel with `self.units` — a
      compacted list would make the index used by the splash loop mean something different in
      each language.
- [ ] `sim/engine.py`: replace the three call sites (`:336`, `:349`, `:352`) with reads from
      `pos`. `bus_load()` (`:215`) runs **before** `_step()` in `_tick_once()` and therefore
      cannot share the cache; leave it alone and say so in a comment, or the next reader will
      "fix" it into a stale read.
- [ ] `scripts/anchor_sim.gd`: same hoist in `_step()`, into a `PackedFloat64Array` of
      `2 * units.size()` (`x` at `2*i`, `y` at `2*i+1`) rather than an `Array` of
      `PackedFloat64Array` — one allocation per tick instead of N.
- [ ] Confirm the movement loop still resolves positions *before* it advances `dist`: the slow
      field is sampled at the position the unit is leaving, which is today's behaviour and is
      a rule. Build the cache **after** the movement loop, from the advanced `dist`, because
      that is the position the targeting scan uses today.
- [ ] Write down in both files, next to the cache, that the cache is invalidated by exactly one
      thing (a write to `u.dist`) and that only the movement loop and `fire_surge()` do that.
      `fire_surge()` is GDScript-only and runs outside `_step()`; assert that in the comment.
- [ ] Re-measure: extend or add a micro-benchmark under `tools/` that reproduces the PRD's
      512/60/24 and 1000/80/32 cells against the Python engine and prints ms/tick before and
      after. Commit it — the 5.6 ms budget needs a repeatable number, not a remembered one.
- [ ] Re-run the 864-run parity set.
- [ ] Re-grade all 24 anchors; the diff must be empty.
- [ ] Record the measured ms/tick in `docs/STATE.md`.

## Acceptance criteria

- `point_at` / `point_at_xy` is called at most `units.size()` times per tick in the fire phase
  in both languages (assert it in the benchmark by counting calls, not by reading the code).
- `.venv/bin/python -m sim.run --jobs 8` output is **byte-identical** to before the change.
  This is a pure hoist; any number that moves is a bug.
- Measured ms/tick at 512 units / 60 emplacements / 24 segments is ≤ 4.0 (PRD measured 3.1).
- Parity passes 864/864.

## Verification

```bash
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/bench_tick.py --units 512 --towers 60 --segments 24
.venv/bin/python tools/bench_tick.py --units 1000 --towers 80 --segments 32
.venv/bin/python tools/test_parity.py
```

Proof to paste: the empty `diff`, both benchmark lines with the before/after ms/tick, and
parity's 864/864.

## Risks / gotchas

- **The cache must be index-parallel with `units`, in both languages.** The splash loop
  excludes the target by index (`anchor_sim.gd:557`) precisely because LF-055 showed that
  value-comparing a unit dictionary diverges from Python's `is`. A compacted position array
  would reintroduce exactly that class of bug in a new place.
- Do not cache across ticks. `dist` advances every tick and a one-tick-stale position is a
  sub-ulp difference that the parity gate will find at run 700 of 864 and nowhere else.
- `bus_load()` runs before `_step()` and samples damper coverage at the *pre-movement*
  position. That ordering is a rule (decision 027 pricing). Sharing a cache between them
  changes it.
- `prune_dead()` (GDScript) and the wave-end filter (`sim/engine.py:408`) both compact
  `units`; the cache must not outlive them.

## Files likely touched

- `sim/engine.py`
- `scripts/anchor_sim.gd`
- `tools/bench_tick.py` (new)
- `docs/STATE.md`
