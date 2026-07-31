id: TER-04
title: Deterministic draw order — an explicit tie-break epsilon, because sort_custom is not stable
labels: engine, phase-2
depends: TER-01
blocks: TER-05, TER-10
milestone: E4 Terrain
---
## Problem

`scripts/anchor_view.gd:1024` sorts the drawable list with
`out.sort_custom(func(a, b): return a["depth"] < b["depth"])`. **Godot's `sort_custom` is
not a stable sort**, so two drawables with equal `depth` may swap between frames for no
reason the player can perceive — a flicker. Today the flicker is rare because `depth()`
(`scripts/iso.gd:25`) already breaks ties on `tx`, and a unit and a turret rarely occupy
the same float position. Elevation makes ties **routine**: three levels is exactly one tile
of screen height (`3 × 32 = 96 px`, versus a 64 px tile), so a raised tile and a farther
flat tile now land on top of each other constantly, and once a terrain surface joins the
same list ({{TER-05}}) the surface and the entity standing on it have identical `tx + ty`
by construction.

The precedent for what an ordering wobble costs is LF-055 and PRD risk #3: intermittent
tie-break ordering is the hardest class of bug in this codebase because it does not
reproduce on demand.

## Tasks

- [ ] Define an explicit **entity class epsilon** in `scripts/iso.gd`: terrain surface
      `0.0`, prop `0.1`, tower `0.2`, unit `0.3`, added to `depth()`'s result. A surface
      always precedes what stands on it; a unit always draws over the turret on the same
      tile. The gap must be smaller than the `tx` tie-break increment (1.0) and larger
      than any float error in the depth computation.
- [ ] Add a final deterministic tie-break so **no two drawables can ever compare equal**:
      after depth and class, compare a stable per-entity ordinal (placement index for
      towers, spawn index for units, `ty*w+tx` for terrain). Sorting becomes a total order
      and stability stops mattering.
- [ ] Change the comparator to use that composite key rather than a single float. Prefer
      packing into one `float` only if every component is provably representable; otherwise
      compare fields in order — a comparator with three branches is cheaper than a
      debugging session.
- [ ] Assert the total order in a headless test: build a synthetic list with deliberate
      depth collisions, sort it 1,000 times from shuffled inputs, and require an identical
      result every time.
- [ ] Add a gate check or extend the existing `game renders` check so a re-run of the same
      frame produces a byte-identical PNG. Two `--shot` captures of the same `--fixed-fps`
      frame must hash the same.
- [ ] Document in `drawables()`'s docstring that the list is a **total order** and why —
      naming `sort_custom`'s instability explicitly, so the next person does not remove
      the epsilon as redundant.
- [ ] Confirm `glow_layer.gd:36` and `fx_additive.gd:209` consume the same ordered list
      (they call `view.drawables()`), so the albedo and additive passes cannot disagree.

## Acceptance criteria

- Two drawables in the same frame never compare equal under the comparator — assert it in
  the test by checking the sorted list has strictly increasing keys.
- 1,000 sorts of the same shuffled collision-heavy list produce 1,000 identical outputs.
- Two `--shot` captures of the same frame of the same anchor are byte-identical PNGs.
- A unit standing on a raised surface draws over that surface in every frame of a 600-frame
  capture — no single frame shows the surface on top.
- No change to `sim/engine.py` or `scripts/anchor_sim.gd`; parity unchanged.

## Verification

```bash
Godot --headless --path . -- --test-draw-order        # 1000 shuffles, identical output
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX --shot /tmp/a.png 300
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX --shot /tmp/b.png 300
sha256sum /tmp/a.png /tmp/b.png        # must match
```

## Risks / gotchas

- **The epsilon must not swamp the `tx` tie-break.** `depth()` returns
  `(tx+ty)*1000 + tx`; a class epsilon of 0.1 sits well under 1.0. Do not use 1.0 or a
  unit at tx=3 will sort past a tower at tx=4.
- Float accumulation: at a 64×64 board `(tx+ty)*1000` reaches 126,000, where a float64 has
  ample precision but a **float32** does not. `depth()` returns GDScript `float`, which is
  float64 — do not route it through a `Vector2` or a `PackedFloat32Array` on the way to
  the comparator.
- The class epsilon is presentation-only. It must never appear in the rules; ordering in
  the rules is a parity concern with its own precedent (LF-055).
- Godot's `sort_custom` comparator must be a strict weak ordering. A comparator that
  returns `true` for equal elements is undefined behaviour and can crash or loop — with a
  total order this is moot, which is part of the point.

## Files likely touched

- `scripts/iso.gd`
- `scripts/anchor_view.gd`
- `scripts/main.gd` (test hook)
- `tools/check.py`
