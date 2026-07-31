id: CAM-08
title: Bucket the hit-flash lookup — the one quadratic term in the renderer
labels: phase-2, engine, perf
milestone: E2 Camera
---
## Problem

`fx_additive.gd:203-222` `_draw_hit_flashes()` walks every live unit and calls
`fx.hit_flash_at(tile)` for each. `combat_fx.gd:97-114` `hit_flash_at()` then scans the **whole**
live hit list looking for the nearest hit within `HIT_MATCH_RADIUS` (0.55 tiles,
`combat_fx.gd:24`). The hit list grows with the number of units being shot, so the pass is
quadratic in unit count.

Measured (LF-100) at 32x32: **0.31 ms at 150 units, 0.96 at 300, 3.67 at 600, 7.67 at 900** —
roughly U^1.9, and **29% of all draw cost at 900 units**. The theatre-scale target is 250-400
alive rising to 900 in the stress case (PRD §E5), so this is rank 5 in the break-order table and
it is the only quadratic term in the renderer.

## Tasks

- [ ] Bucket `_hits` by integer tile once per frame in `combat_fx.gd`: a `Dictionary` keyed by
      `Vector2i(floori(tile.x), floori(tile.y))` holding an array of hit indices. Rebuild when
      the hit list changes, not per lookup.
- [ ] Look up over the 3x3 neighbourhood of the query tile. Prove sufficiency rather than
      assuming it: `HIT_MATCH_RADIUS = 0.55` with a unit-tile bucket means a match can be at most
      one bucket away in each axis, so 3x3 is exact. Put that argument in the docstring, with the
      constant named — if `HIT_MATCH_RADIUS` ever exceeds 1.0 the neighbourhood must grow, and
      that is the kind of coupling that goes wrong silently.
- [ ] **Preserve which hit is chosen.** `hit_flash_at()` returns the nearest hit within the
      radius, ties going to the earlier entry in `_hits` because the comparison is `<=`. The
      bucketed version must return the same element for the same input, or a flash lands on the
      wrong sprite. Write a differential test: run both implementations over a recorded hit list
      and assert identical results.
- [ ] Keep proximity as the key. `combat_fx.gd:98-105` is explicit that `unit_damaged` carries a
      *kind* and a position and never the mutable unit dictionary, and that the sim must never
      grow a unit id to give the FX layer one — `anchor_sim.gd`'s splash loop tests `i ==
      target_i` for exactly that reason (LF-055). Do not "fix" this by adding an id.
- [ ] Reuse the same bucketing idiom for `_draw_beam_charges` and any other per-unit scan in
      `fx_additive.gd` if the profile says they matter; measure first.
- [ ] Measure with `--profile` (from {{CAM-06}}) at 150 / 300 / 600 / 900 units and reproduce the
      LF-100 numbers before changing anything — a fix measured against an unreproduced baseline
      proves nothing.
- [ ] Close LF-100 in `docs/BACKLOG.md`.

## Acceptance criteria

- The differential test passes: bucketed and linear `hit_flash_at` return the same hit for
  every unit position in a recorded 900-unit frame.
- `--profile` shows `_draw_hit_flashes` at 900 units under **1.0 ms** (from 7.67 ms), and scaling
  roughly linearly rather than as U^1.9 across 150 / 300 / 600 / 900.
- The frame is pixel-identical before and after on a captured frame with flashes on screen
  (SHA-256).
- No allocation per unit per frame in the lookup path.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/flash-after.png --extra --profile 600
sha256sum /tmp/flash-before.png /tmp/flash-after.png
# the differential test lives with the other in-engine tests
/path/to/godot --headless --path . --script res://scripts/test/hit_bucket.gd
.venv/bin/python tools/check.py --no-window && .venv/bin/python tools/reap.py
```

Proof is the per-count `--profile` table before and after, plus the differential test passing.

## Risks / gotchas

- **Presentation only, no parity exposure** — `combat_fx.gd` and `fx_additive.gd` are downstream
  of the sim's presentation signals (decision 053). Say so in the PR; do not run an 864-run for it.
- **A tie-break change is invisible for a frame and then wrong forever.** LF-055 is the precedent:
  `==` on a Dictionary is a value comparison in Godot 4.7, so two units of the same kind with
  equal hp and equal dist compared equal, and the two rule implementations disagreed the moment a
  wave put two identical units at the same distance. Same class of bug, same care.
- **`floori` on a negative coordinate is not `int()`.** Board positions are non-negative today,
  but free placement ({{PLC-01}}) allows a footprint to sit at a fractional position near 0 and
  terrain may introduce negative offsets. Use `floori`, not truncation.
- `fx_additive.gd` and `combat_fx.gd` both failed to parse once during an unrelated run
  (LF-063) — check them with `--headless --check-only --script` before running the game, because
  a parse error here is a blank playfield, not an error.
- {{WAR-03}} is the same bucketing shape applied to the *rules*, where it is parity-sensitive and
  much more dangerous. This one is cosmetic and is a cheap place to prove the idiom first.

## Files likely touched

- `scripts/combat_fx.gd` (`_hits` bucket, `hit_flash_at`)
- `scripts/fx_additive.gd` (`_draw_hit_flashes`)
- `scripts/test/hit_bucket.gd` (new differential test)
- `docs/BACKLOG.md`
