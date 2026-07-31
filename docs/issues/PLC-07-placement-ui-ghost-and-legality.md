id: PLC-07
title: Placement UI — a continuous ghost, three legality reasons, and retiring the slot tile
labels: phase-2, ui, art
depends: PLC-02
milestone: E3 Placement
---
## Problem

The whole visual language of "where can I build" is the slot set. `anchor_view.gd:1042-1075`
`_draw_board()` paints a distinct `tile_slot` texture on every buildable tile; `_draw_hover()`
(`:1076-1090`) rings the hovered tile only when `sim.free_slots.has(hovered_slot)`; and
`_draw_reach()` (`:1091`ff) previews the armed emplacement's range on that tile in amber. Remove
slots and all three go blank: the player is handed a continuous board with no indication of where
a build will be accepted, and the refusal has become **three different reasons** — out of bounds,
too close to the lane, overlapping something already there — that a single "no" cannot express.

This is also the last consumer of the `tile_slot` sprite, which is one of the 22 ids drawn from
26 sprites that the gate's `sprite coverage` check tracks.

## Tasks

- [ ] Draw a **buildable-area wash**: the region satisfying bounds and lane standoff for the
      currently armed tower's footprint. Compute it once per (anchor, tower) and cache it; it
      feeds directly into {{CAM-06}}'s static tile list rather than being a second per-frame pass.
- [ ] Draw a **continuous ghost** at the cursor: the armed tower's real albedo from the atlas at
      its facing yaw, at reduced alpha, plus its footprint. The footprint is a **projected
      ellipse, not a circle** — 2:1, exactly as `_draw_contact_shadow()` at `:1134-1146` already
      does with `radius * 0.5` on y, and for the same reason (decision 017).
- [ ] Give the refusal a reason. {{PLC-02}}'s predicate returns which of the three tests failed;
      surface it as one short line in the HUD and as a distinct treatment on the ghost —
      out-of-bounds fades at the edge, lane-standoff highlights the lane band, overlap outlines
      the emplacement being collided with. Three failures that look identical are one failure the
      player cannot learn from.
- [ ] Keep `_draw_reach()` working at a float position: the amber "what the armed emplacement
      would cover" ring and the bone "what the selected emplacement covers" ring both take
      continuous centres. `_draw_range()` builds its polyline with `cos`/`sin` — that is
      presentation and stays (decision 049 already draws that line).
- [ ] Retire the `tile_slot` kind from `_draw_board()` and the `slot_set` build above it. Decide
      what happens to the `tile_slot` sprite: either drop it from the manifest and let
      `sprite coverage` fall to 21 of 26, or repurpose it as the buildable wash tile. Say which
      in the PR — the gate hashes every render and will notice.
- [ ] Rebuild the hover treatment for a continuous cursor: the diamond ring at `:1086-1089` is
      tile-locked (`Iso.diamond`) and no longer describes what is being pointed at.
- [ ] Every colour and alpha from `Ui`, never a literal. The illegal state must be
      distinguishable without colour (dashed outline, hatch, or a distinct alpha), because red/
      green is the single most common failure mode in a build interface and the palette is solved
      contrast policy, not decoration (decisions 045, 046).
- [ ] Show the emplacement count against the cap from {{PLC-05}} at the ghost, when near it —
      "the reason you cannot build" includes "you are at the cap", and that is a fourth refusal
      reason to route through the same path.
- [ ] Screenshot every state: legal ghost, each of the four refusals, the wash at two different
      footprints, and a dense board where overlap is the live constraint. anchor-24 at 100% and
      200%.
- [ ] Run the a11y audit on each and record the new text-item count.

## Acceptance criteria

- With a tower armed, the buildable region is visible without moving the cursor.
- Moving the cursor from legal to illegal changes the ghost within one frame and names the
  reason.
- The position the ghost shows is the position that gets built, to the pixel, at every zoom
  between `ZOOM_MIN` and 1.0.
- A greyscale copy of the frame still distinguishes legal from illegal.
- No `tile_slot` texture is drawn anywhere, and `sprite coverage` passes with the recorded count.
- `accessibility` reports 0 WCAG failures on anchor-24 at 100 / 150 / 200%.
- Frame time with the wash and the ghost is within 0.5 ms of without, at 400 units (measure with
  {{CAM-06}}'s `--profile`).

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/ghost-ok.png \
  --extra --pick mortar-emplacement --cursor 6 --a11y /tmp/ghost-ok.json
.venv/bin/python tools/shot.py anchor-24 --out /tmp/ghost-lane.png \
  --extra --pick mortar-emplacement --cursor 1
.venv/bin/python tools/validate/a11y.py /tmp/ghost-ok.json --shot /tmp/ghost-ok.png --all
.venv/bin/python tools/check.py            # sprite coverage, sprite atlas, accessibility
.venv/bin/python tools/reap.py
```

Proof is the set of state screenshots side by side, plus `accessibility` at 0 failures and
`sprite coverage` green at the recorded count.

## Risks / gotchas

- **An art change is invisible in two ways.** If `tile_slot` is repurposed or a new wash tile is
  rendered: render → `mask_glow` → `pack_atlas` → `--import` → screenshot, in that order. A
  skipped `--import` serves the cached `.ctex`; a stale atlas serves the old pixels. The gate's
  `sprite atlas` check hashes every render, so the mistake is red rather than mysterious.
- **The pack is a fixed 256 px grid and never trims** — one measured pivot serves every sprite
  only because every cell is identical (LF-027).
- **Do not re-derive legality in the view.** Ask the sim. A view-side copy will disagree on the
  boundary, which is where the player aims, and the disagreement will read as a bug in the rules.
- **Colours are authored in sRGB here, not linearised** — `backdrop.gd`'s palette comment is
  explicit that in-engine immediate geometry is ordinary sRGB draw-call colour and none of the
  Blender linearisation machinery applies. Do not "fix" a wash colour by linearising it.
- **`Label.clip_text` clips horizontally only.** A zero-height reason label still draws a full
  line over the board.
- **Look at it in the engine, never in the source.**
- `--extra` needs {{PRC-09}}'s `argparse.REMAINDER` fix before any command above runs (LF-073).

## Files likely touched

- `scripts/anchor_view.gd` (`_draw_board`, `_draw_hover`, `_draw_reach`, `_draw_range`, new ghost)
- `scripts/hud.gd` (refusal reason line)
- `scripts/ui.gd` (any new colour, solved for contrast)
- `assets/renders/`, `assets/atlas/`, `tools/blender/` (only if `tile_slot` is repurposed)
- `docs/STATE.md`
