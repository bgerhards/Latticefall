id: TER-06
title: Picking becomes a front-to-back ray walk — screen_to_tile stops being invertible
labels: engine, ui, phase-2
depends: TER-01
blocks: TER-10
milestone: E4 Terrain
---
## Problem

`Iso.screen_to_tile()` (`scripts/iso.gd:17`) is a closed-form inverse of
`tile_to_screen()`, and `scripts/anchor_view.gd:723` uses it directly on the mouse
position to decide which tile the cursor is over. That inverse only exists because the
board is a plane. With elevation, one screen point lies on a **ray** through the
heightfield and can hit several tiles: the top of a raised tile at (5,5) and the ground
tile at (7,7) behind it project to overlapping screen regions, and the correct answer is
always the one **nearest the camera**.

Getting this wrong is not cosmetic. The hover ring, the build click, the selection, the
range preview (`_draw_reach()`, `:1091`) and the keyboard/gamepad cursor all key off
`hovered_slot`, so a wrong pick means the player builds somewhere they did not point.
Full keyboard, mouse and gamepad control is a shipped property (PRD §4 invariant 6), and
the `--cursor` and `--pick` verification hooks exist precisely because `--fixed-fps` has
nobody to press a key.

## The algorithm

Walk candidate tiles **front to back** — decreasing `tx + ty`, the reverse of the
painter's order — and return the first whose raised diamond contains the screen point.
For each candidate at height `h`, the test is the existing flat-diamond containment
against `p + Vector2(0, h * LEVEL_PX)`; i.e. un-lift the point rather than lift the
diamond. Bound the walk: the highest tile on the board can only lift a point by
`levels * LEVEL_PX` px, so the candidate set is `screen_to_tile(p)` and the diagonal band
of tiles up to `levels` steps toward the camera. With `levels <= 4` this is at most a
handful of containment tests, not a scan.

Cliff **faces** ({{TER-07}}) are pickable surfaces too, and the answer for a face is the
tile it belongs to — clicking the side of a plateau selects the plateau, not the tile
below it.

## Tasks

- [ ] Add `AnchorView._pick_tile(screen_p: Vector2) -> Vector2i` implementing the bounded
      front-to-back walk, returning `Vector2i(-1, -1)` for a miss (off-board), which is a
      state the current code cannot express and silently rounds into a real tile
      (`:724` `roundi`).
- [ ] Replace the `screen_to_tile` call at `:723` with it. Leave `Iso.screen_to_tile()`
      itself in place — it is still the correct ground-plane inverse and `board_props.gd`
      uses it at `:293` — but add a docstring warning that it is **not** the picker.
- [ ] Handle the miss case everywhere `hovered_slot` is consumed: `_draw_hover()`
      (`:1077`), `_click()` (`:855`), `toggle_at()` (`:882`), `_draw_reach()` (`:1091`).
      An off-board hover must draw nothing and click nothing.
- [ ] Make the keyboard/gamepad cursor elevation-aware. `_step_cursor()` (`:780`) judges
      directions in **screen** space (`_slot_screen()`, `:776`) because the player is
      looking at a projection — that stays true, but `_slot_screen()` must include the
      height offset or "up" walks to a tile that is visually down. Note that {{PLC-01}}
      replaces the slot graph entirely; keep this change small and additive.
- [ ] Update the `--cursor N` and `--pick <id>` hooks (`scripts/main.gd:199`, `:212`) so
      they exercise the ray walk rather than the old inverse, and add a `--pick-at X Y`
      form that feeds a raw screen coordinate through `_pick_tile()` and prints the tile
      it resolved to. A screenshot cannot settle which tile a pixel belongs to; this can.
- [ ] Add a headless test: for every tile on a terrain-bearing board, project its centre
      with the height offset and require `_pick_tile()` to return that tile. Round-trip,
      every tile, every level.
- [ ] Add the adversarial case explicitly: a point inside the screen region of both a
      raised near tile and a flat far tile must resolve to the **near** one.

## Acceptance criteria

- Round-trip holds for 100% of tiles on the pilot terrain anchor: `pick(project(t)) == t`.
- A screen point over a 3-level plateau resolves to the plateau tile, not to the ground
  tile behind it, asserted by `--pick-at`.
- Clicking the visible side face of a plateau selects the plateau tile.
- A click outside the board's silhouette selects nothing and plays no build sound — today
  it rounds to a real tile.
- Keyboard cursor movement on a terrain board never jumps to a tile that is visually in
  the opposite direction to the key pressed.
- The pick is bounded: instrument the walk and assert it tests at most `levels + 1`
  candidates.

## Verification

```bash
Godot --headless --path . -- --test-pick-roundtrip     # every tile, every level
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX \
      --pick-at 812 430 --shot /tmp/pick.png 200
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX \
      --cursor 6 --shot /tmp/cursor.png 200
```

Proof: the round-trip test printing `4096/4096`, and `--pick-at` printing the near tile
for a coordinate that the old `screen_to_tile` resolves to the far one — show both values.

## Risks / gotchas

- **Front to back is the reverse of the draw order**, and the draw order is
  `(tx+ty)*1000 + tx` ascending. Getting the direction backwards returns the *farthest*
  hit, which looks almost right on a flat board and never right on a raised one.
- Un-lift the point (`p.y + h*LEVEL_PX`), do not lift the diamond — one addition per
  candidate versus rebuilding a polygon.
- The board is drawn into a 1920×1080 logical viewport rendered to a 1440×810 window
  (0.75 scale). A pick fed raw window pixels is off by a third. Use
  `get_global_mouse_position()`, which is already in the canvas space `_origin` lives in.
- Once {{CAM-01}} exists the camera transform composes with `AnchorView.position`, which
  the screen-shake trauma system already owns (`add_trauma()`, `:464`). The picker must
  read the *composed* transform, not `_origin` alone, or picking drifts while the screen
  shakes.
- `Vector2` is float32. This is presentation code so that is fine — but do not let a
  picked position flow into the rules as a float32 value; {{PLC-01}} needs float64
  positions.
- `roundi` at `:724` silently clamps off-board points into the grid today. Some caller may
  be depending on that; check `_click()` before removing it.

## Files likely touched

- `scripts/anchor_view.gd`
- `scripts/iso.gd` (docstring only)
- `scripts/main.gd`
