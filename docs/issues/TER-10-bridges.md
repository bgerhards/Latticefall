id: TER-10
title: Bridges — the z term in Iso.depth(), decks, piers, and the three-way case
labels: engine, art, risk, phase-3
depends: TER-04, TER-05, TER-06
blocks: TER-11, TER-14
milestone: E4 Terrain
---
## Problem

Everything before this issue is a **pure heightfield**, and a pure heightfield is proven
to need no change to `Iso.depth()` (`scripts/iso.gd:25`) at all — a prototype rendered a
3-level ridge in front of a tall turret and a plateau behind a valley lane
**pixel-identically** under today's `(tx + ty) * 1000.0 + tx`, because raising a tile
always moves it toward the camera and never past a tile with a larger `tx + ty`
(PRD §2.3).

A bridge breaks that. Two surfaces share one tile: the ground under the deck and the deck
itself. `tx + ty` is identical for both, so the painter's key cannot separate them, and a
unit walking under a deck must draw *behind* it while a unit on the deck draws *in front*.
**This is the only reason the z term exists.**

The correct depth is measured, not guessed: true camera depth is
`(√3 / 2√2)(tx + ty) + ½·z`, which normalises to `z_depth_per_level = LEVEL_PX / 96` —
**exactly 1/3** (PRD §2.3). {{TER-01}} already added the parameter with a default of
`0.0`; this issue is where call sites start passing it.

## Rejected alternatives — recorded so they are not re-opened

- **Layered draw** (draw everything at deck level after everything at ground level).
  Wrong for a tall turret in front of a ridge — the prototype shows it diverging from the
  correct order, because a tall entity at ground level legitimately overlaps a deck behind
  it. It replaces one ordering bug with a subtler one.
- **Per-tile buckets** (sort within a tile, then sort tiles). Produces the same result as
  the z term with more code, more state, and a second ordering rule to keep in step with
  the first.
- **Author-declared over/under tags** on entities or tiles. Unfalsifiable — nothing checks
  that the tag matches the geometry — and unnecessary once every entity carries `z`, which
  {{TER-01}} already arranged.

## Tasks

- [ ] Pass `z` at every `Iso.depth()` call site: `drawables()` for towers (`:1003`) and
      units (`:1016`), and the static terrain list from {{TER-05}}. Keep the default `0.0`
      so nothing outside the board changes.
- [ ] Verify the coefficient is exactly `LEVEL_PX / 96` and that the `tx` tie-break
      (`+ tx`, `iso.gd:26`) still dominates float noise at the largest board size.
- [ ] Extend the terrain schema ({{TER-02}}) with `bridges`: a deck run (start tile, end
      tile, axis, deck level), piers, abutments, and a `see_through` flag consumed by
      {{TER-08}}'s "no emplacement under an opaque deck" rule.
- [ ] Implement deck/pier/abutment/rail geometry procedurally first, exactly as
      {{TER-07}} did for cliffs — the argument is the same and the payoff is the same
      (zero renders while the mechanic is being tuned). Rendered assets are {{TER-14}}.
- [ ] Handle the **three-way case** explicitly and test it: a deck, a unit under it, and a
      unit on it, all on one tile, in one frame. Correct order is ground surface, unit
      under, deck, unit over.
- [ ] Make the picker ({{TER-06}}) deck-aware: a screen point over a deck resolves to the
      deck surface, and the same point where the deck is see-through resolves to whichever
      surface is nearer the camera. Add `--pick-at` cases for both.
- [ ] Make {{TER-09}}'s lane height carry the deck: a lane crossing *under* a deck takes
      the ground height, a lane crossing *on* it takes the deck level. This is why height
      belongs to the waypoint rather than to the tile.
- [ ] Extend {{TER-08}} with the bridge rules: support at each end, deck clearance ≥ 2
      levels, no emplacement under an opaque deck.
- [ ] **Do not make bridges destructible.** Explicitly out of scope (PRD §8). Note it in
      the code so the next person does not add it as a natural extension.
- [ ] Record a decision entry with the three rejected alternatives above and the measured
      1/3 coefficient.

## Acceptance criteria

- `Iso.depth(a, b)` and `Iso.depth(a, b, 0.0)` remain identical for 10,000 random pairs —
  the default must stay free.
- The three-way frame renders in the correct order and is byte-identical across two
  captures of the same frame ({{TER-04}}).
- A unit walking a lane under a deck is occluded by the deck for the whole crossing, with
  no frame where it pops in front.
- A unit on the deck draws over the deck for the whole crossing.
- Clicking a deck selects the deck tile; clicking a see-through deck's gap selects the
  ground.
- Parity unchanged: bridges are geometry and lane data, not rules. If any part of this
  reaches `sim/engine.py`, it moves in both files in the same commit and the campaign is
  re-graded.
- The validator rejects a deck with no support and a deck with 1 level of clearance.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py         # bridge fixtures fail as designed
.venv/bin/python tools/test_parity.py                    # 864/864, digest unchanged
Godot --headless --path . -- --test-depth-default        # depth(a,b) == depth(a,b,0.0)
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX \
      --shot /tmp/bridge.png 320 --heights /tmp/bridge.txt
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX \
      --pick-at 900 470 --shot /tmp/deckpick.png 200
```

Proof: one screenshot containing all three of deck, unit-under and unit-over, with
`--heights` naming their z values; and the depth-default test printing `10000/10000`.

## Risks / gotchas

- **The z term must not change the flat case.** Every existing anchor is flat; if a single
  pixel moves on anchor-01, the coefficient or the default is wrong.
- `1/3` is exact as `LEVEL_PX / 96.0` in float64. Do not write `0.333333`.
- The tie-break in `depth()` is `+ tx`, magnitude up to the board width. The z
  contribution is at most `levels / 3`. With `levels <= 4` that is 1.33 — **larger than
  the smallest `tx` difference of 1.0**, so a deck can sort past a neighbouring tile.
  Scale the z term into the same band as the class epsilon ({{TER-04}}) or restructure the
  key into explicit components; do not stack three magnitudes into one float by hope.
- The measured pivot leaves only 84.5 px below it in a 256 px cell, so a pier is **one
  level tall and stacked**, exactly like a cliff face. Do not lower `HEIGHT_BIAS`
  (`tools/blender/render.py:53`) — it is shared by all 26 assets and re-derives the pivot.
- Projectiles and additive FX will still draw over a deck regardless of z, because
  `CombatFx` and `FxAdditive` are separate `CanvasItem`s at fixed `z_index` 8 and 14
  (`scenes/main.tscn:31`, `:39`). That is {{TER-11}} and it is **accepted in v1** — do not
  try to solve it with the z term, which cannot reach across `CanvasItem` boundaries.
- `board_props.gd` draws at `DRAW_Z = 1` above the whole board (`:19`) and does not
  depth-sort against entities by design. The anchor ring will draw over a deck. Decide
  whether that is acceptable at the exit tile — it probably is, for the reason its
  docstring already gives — and write it down.

## Files likely touched

- `scripts/iso.gd`
- `scripts/anchor_view.gd`
- `data/schema/anchor.schema.json`
- `sim/content.py`, `scripts/content.gd`
- `tools/validate/validate_data.py`
- `docs/DECISIONS.md`
