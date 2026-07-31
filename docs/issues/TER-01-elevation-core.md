id: TER-01
title: Elevation core — LEVEL_PX blit offset and z on every drawable
labels: engine, art, phase-2
depends: TER-02
blocks: TER-03, TER-04, TER-05, TER-06, TER-07, TER-09, TER-12, TER-13
milestone: E4 Terrain
---
## Problem

The board is flat. Every tile and every entity is placed by `Iso.tile_to_screen()` alone
(`scripts/anchor_view.gd:1059`, `:1008`, `:1021`), so there is no high ground, no dead
ground, and nowhere worth holding — pillar three of the PRD ("terrain that means
something") has no representation in the engine at all. The projection maths for height
is already solved and measured: for the orthographic camera at ε = 30° with the
calibrated `ortho_scale = 2.784233`, `s = 256 / 2.784233 = 91.9463` px per world unit and
`k = s·cos(30°) = 79.6279` px per world unit of *height*, verified against two throwaway
Blender renders at **k/s = 0.865994 vs cos 30° = 0.866025 — 0.004%** (PRD §2.3). It is
`cos`, not `sin`; `sin` would have given 46 px and a board that looked subtly wrong
forever, the same shape of error as decision 017.

**This issue is presentation-plus-data and cannot break parity.** It makes **no change to
`Iso.depth()`** (`scripts/iso.gd:25`) and **no change to `sim/engine.py` or
`scripts/anchor_sim.gd`**. That is the entire reason the cheap slice is safe to do early
and in parallel with E3 free placement — the rules never learn the board has height.

## Why no depth change is needed

Proven with a prototype, not assumed (PRD §2.3): a 3-level ridge in front of a tall
turret, and a plateau behind a valley lane, render **pixel-identically** under today's
`(tx + ty) * 1000.0 + tx`. Raising a tile always moves it toward the camera along screen
−y and never past a tile with a larger `tx + ty`, so a pure heightfield's painter order is
unchanged. **The z term exists solely for bridges**, where two surfaces occupy one tile —
that is {{TER-10}} and it is deliberately not in this slice.

The drawable dictionaries still gain a `z` field here, defaulted to the tile's height in
levels, because every consumer ({{TER-04}}, {{TER-05}}, {{TER-06}}, {{TER-10}}) needs it
and retrofitting a field into four call sites later is worse than carrying an unused one
now.

## Tasks

- [ ] Add `LEVEL_PX: float = 32.0` and `LEVEL_H: float = 0.401872` to `scripts/iso.gd`,
      with a docstring recording the derivation (`k = s·cos ε`, `s = CELL/ortho_scale`),
      the measured `k/s = 0.865994`, and the explicit warning that `LEVEL_H` is derived
      from the **solved** `ortho_scale = 2.784233`, never from the nominal 128 — the
      rendered tile is geometrically 130.03 px and the "128" is an alpha-threshold
      artefact `calibrate()` bakes in.
- [ ] Add `Iso.height_offset(z: float) -> Vector2` returning `Vector2(0.0, -z * LEVEL_PX)`.
      One function, so no call site ever writes the sign of the offset by hand.
- [ ] Add `z_depth_per_level` as a named constant `LEVEL_DEPTH := LEVEL_PX / 96.0`
      (exactly 1/3) next to `depth()`, with a comment that nothing uses it until
      {{TER-10}} and why: true camera depth is `(√3/2√2)(tx+ty) + ½z`, which normalises to
      that coefficient.
- [ ] Extend `Iso.depth()` with an optional third parameter `z: float = 0.0` that
      contributes `z * LEVEL_DEPTH`. **Default 0.0 so every existing call site keeps
      working byte-identically**, and leave every call site passing two arguments in this
      issue. Assert in the docstring that the heightfield case is proven not to need it.
- [ ] Add a terrain height accessor on `AnchorView` — `_height_at(tx: int, ty: int) -> int`
      — reading the dense level array produced by {{TER-02}}, clamped at the board edge.
- [ ] Apply the offset to ground tiles in `_draw_board()` (`scripts/anchor_view.gd:1042`):
      `c = Iso.tile_to_screen(x, y) + _origin + Iso.height_offset(h)`. Keep the existing
      back-to-front `s_` sweep untouched.
- [ ] Apply the offset to entities in `drawables()` (`scripts/anchor_view.gd:997`): add
      `"z"` to both the tower and unit dictionaries, and fold `Iso.height_offset(z)` into
      the `"at"` each already computes. Because `at` carries it, `glow_layer.gd:36` and
      `fx_additive.gd:209` inherit elevation for free and cannot disagree with the albedo
      pass.
- [ ] Apply the offset to `AnchorView.to_screen()` (`:488`) — it is the shared helper the
      FX layer uses, and an FX layer that ignores height would draw every tracer at sea
      level. Take an optional `z` argument defaulting to the terrain height at that tile.
- [ ] Apply it to `_draw_contact_shadow()` (`:1135`): the shadow belongs on the *surface*
      the entity stands on, so it takes the same offset as the sprite, not zero.
- [ ] Apply it to the ground-drawn overlays that are now on a raised surface:
      `_draw_hover()` (`:1077`), `_draw_selection()` (`:1107`), `_draw_range()` (`:1121`,
      per sampled point — a range ring crossing a ridge must climb it), `_draw_reach()`
      (`:1091`).
- [ ] Leave `board_props.gd` alone in this issue except for a `# TODO TER-07` marker: the
      platform edge (`:190`) and ground sigils (`:327`) assume a flat plate and are dealt
      with in {{TER-07}}.
- [ ] Add a `--heights` verification hook to `scripts/main.gd`, in the family of
      `--facings` (decision 049): on the frame `--shot` captures, print `tx ty level` for
      every tile whose level is non-zero plus the `z` of every drawable. A screenshot
      shows *a* silhouette; only this settles whether tile (7,4) is at level 2 or level 3.
- [ ] Re-run the parity gate and record that it is unchanged — the point of this issue is
      that it *cannot* move, so a moved parity result means something leaked into the rules.
- [ ] Update `CLAUDE.md` art-pipeline facts with the `k = s·cos ε` derivation and the
      "elevation is a blit offset, never baked into a sprite" rule.
- [ ] Add a decision entry recording the cheap slice: no `Iso.depth()` change, no rules
      change, z carried but unused.

## Acceptance criteria

- `Iso.LEVEL_PX == 32.0` and `Iso.height_offset(3)` returns `Vector2(0, -96)`.
- `Iso.depth(a, b)` and `Iso.depth(a, b, 0.0)` return the identical float for 10,000
  random `(a, b)` pairs.
- An anchor whose terrain declares a 3-level plateau screenshots with that region's tiles
  exactly 96 px higher on screen than the level-0 tiles beside them, measured from the
  PNG, not eyeballed.
- A turret placed on a level-2 tile stands on that tile's surface: its contact shadow, its
  selection ring and its hover ring are all at the same offset as its sprite.
- **Parity is byte-identical to the pre-change run.** 864/864, same digest.
- No sprite in `assets/renders/` is re-rendered by this issue. Zero atlas change.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8            # grades unchanged, all 24
.venv/bin/python tools/test_parity.py           # 864/864, unchanged digest
```

Then, with the owner's agreement because these open real windows on their desktop
(LF-061):

```bash
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX \
      --shot /tmp/elev.png 300 --heights /tmp/elev-heights.txt
```

Proof is: `--heights` lists the raised tiles; the PNG shows them 96 px up; and
`tools/test_parity.py` prints the same summary line as it did before the branch.

## Risks / gotchas

- **Deriving `LEVEL_H` from 128.** The nominal tile width is not the rendered width. Use
  `ortho_scale = 2.784233`. Same trap family as decision 017.
- **Using `sin`.** `sin(30°) = 0.5` would give ~46 px per level and a board that looks
  plausible but is not the projection the sprites were rendered in.
- **Baking height into a sprite.** The pivot is a property of the camera and is shared by
  all 26 assets. Baking is LF-027 again. The measured pivot is (127.5, 171.5) in a 256 px
  cell — **84.5 px below the top of the cell's lower half**, which is why a 2-level cliff
  face (96 px) cannot be one asset. See {{TER-07}} and {{TER-13}}.
- **Do not lower `HEIGHT_BIAS`** (`tools/blender/render.py:53`) to make terrain fit. It is
  shared by every asset and `calibrate()` re-derives the pivot from it; changing it
  invalidates the whole library.
- Elevation moves sprites up into the screen region the tile mosaic assigns to *farther*
  tiles. That is correct and is exactly why `board_props.gd` sits at `DRAW_Z = 1` rather
  than −1 (`scripts/board_props.gd:19`). Expect props to now float over raised ground
  until {{TER-07}}.
- `Iso.height_offset()` must return a `Vector2`, but nothing in the **rules** may ever
  touch it — `Vector2` is float32 and banned in the rules (PRD §2.1). This is presentation
  code; keep it there.

## Files likely touched

- `scripts/iso.gd`
- `scripts/anchor_view.gd`
- `scripts/main.gd`
- `docs/DECISIONS.md`, `CLAUDE.md`
