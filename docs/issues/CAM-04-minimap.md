id: CAM-04
title: Minimap with threat and power overlay
labels: phase-1, ui, design
depends: CAM-01
milestone: E2 Camera
---
## Problem

Once the board exceeds the screen the player cannot see where the trouble is (LF-079). The
threat panel answers *what is coming and when* (decision 037); it cannot answer *where*, and at
32²–64² with 2–5 simultaneous lanes ({{WAR-01}}) "where" is the whole decision — pillar 1 of the
PRD is *"choosing where to make a stand is the first decision of every level"*. Today the board
is small enough that the question does not exist: anchor-24 is 18x15 with one lane, one
entrance and one exit.

There is a second reason this is not optional. {{CAM-05}} measures that fitting 64x64 needs zoom
0.234x (0.117x at 200% interface scale), rendering a 256 px sprite at 30-60 px. If the answer to
that is a zoom floor, **the minimap becomes the wide read** — the only surface on which the whole
board is legible at once — and its fidelity requirements go up accordingly.

## Tasks

- [ ] Build it as a HUD element on the existing `CanvasLayer`, never as a board child. Board
      transform must not reach it (decisions 045, 046, 050).
- [ ] Place it inside the threat panel's scroll region rather than over the board. At 200%
      interface scale the two panels already cover 420 + 528 of a 960 px design space (LF-057);
      a floating minimap over the board makes that worse. The panels scroll (decision 050), so
      there is room there and only there.
- [ ] One affine board→minimap map, derived from the anchor's `grid` and the projected tile
      bounding box, computed once per anchor. Reuse `Iso.tile_to_screen` so the minimap is the
      same projection at a different scale rather than a second, top-down, contradictory picture
      of the same board.
- [ ] Draw, back to front: board extent; the lane polyline(s) from `anchor["path"]`; a threat
      heat term; emplacement marks; unit marks; the camera viewport rectangle.
- [ ] **Threat overlay**: bucket live units into minimap cells and weight by `leak_cost`
      (`max(1, round(hp/130))`, decision 047), not by count — a Column and a Shard are not the
      same amount of trouble, exactly as `tools/density.py` reports peak units in flight rather
      than per-wave count. Render as a low-alpha wash, not as numbers.
- [ ] **Power overlay**: tint the minimap frame by `bus_load / capacity_now()` and draw offline
      emplacements differently from online ones. Capacity is global today; leave a single seam
      where a regional grid would plug in (PRD §5 recommends against building it now).
- [ ] Do not encode anything by hue alone. Online/offline must differ in shape or fill as well
      as colour, and every colour comes from `Ui` — they are accessibility policy solved against
      the real composited panel, not picked by eye (decisions 045, 046).
- [ ] Click and drag on the minimap moves the camera; a `lf_*` action focuses the minimap for
      keyboard/gamepad and steps the camera by region. Full keyboard, mouse and gamepad control
      is a shipped property (`docs/STATE.md`, invariant 6).
- [ ] Keep the per-frame cost bounded: one pass over `sim.units` and one over `sim.placed`,
      no sort, no allocation per entity. It must not become a fifth `drawables()` rebuild —
      see {{CAM-07}}.
- [ ] Screenshot it at 100 / 150 / 200% interface scale on anchor-01 (small board, one lane) and
      anchor-24 (largest board), and run the a11y audit on each.
- [ ] Update `docs/STATE.md` and close LF-079.

## Acceptance criteria

- The minimap shows every unit alive on the board at anchor-24 during wave 8, and the camera
  rectangle on it corresponds to what is actually on screen to within one minimap cell.
- Clicking a point on the minimap centres the camera on the corresponding board point.
- `tools/validate/a11y.py` reports **0 WCAG failures** on the anchor-24 frame at 100, 150 and
  200% with the minimap present, and the worst contrast does not fall below the current 5.08:1.
- No colour-only encoding: a greyscale copy of the shot still distinguishes online from offline
  emplacements and lane from ground.
- Frame time with the minimap present is within 0.3 ms of frame time without it at 400 units
  (measure with the `--profile` hook from {{CAM-06}}).
- Full keyboard-only operation: the camera can be moved to any region of anchor-24 through the
  minimap without a mouse.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/mm-100.png    # + --a11y /tmp/mm-100.json
.venv/bin/python tools/shot.py anchor-24 --out /tmp/mm-200.png    # + --ui-scale 2.0 --scroll 3
.venv/bin/python tools/validate/a11y.py /tmp/mm-100.json --shot /tmp/mm-100.png --all
.venv/bin/python tools/check.py            # accessibility walks five cases
.venv/bin/python tools/reap.py
```

Proof is the two annotated screenshots plus `accessibility` reporting 0 failures and naming a
worst contrast no lower than today's.

## Risks / gotchas

- **The a11y check counts text items** (182 today). Any label on the minimap changes that count;
  expect the baseline to move and re-record it deliberately rather than being surprised.
- **`--a11y` must be paired with the `--shot` on the same frame** (`CLAUDE.md`); use
  {{CAM-03}}'s `--camera` so the shot is reproducible.
- **A theme override under a name the theme does not know is accepted in silence** — verify any
  theme key against `ThemeDB.get_default_theme().get_color_list(...)`.
- **`Label.clip_text` clips horizontally only.** A zero-height label still draws a full line over
  whatever is beneath it — relevant if the minimap gets a legend.
- The panels scroll; a minimap that scrolls out of view during a wave is worse than no minimap.
  Consider pinning it outside the scroll region as SELL / UPGRADE / power already are
  (decision 050), and re-measure the vertical budget if so.
- Do not draw the minimap from `drawables()` — it needs raw sim state and would otherwise inherit
  the yaw/hysteresis mutation described in {{CAM-07}}.

## Files likely touched

- `scripts/minimap.gd` (new)
- `scripts/hud.gd` (placement, panel budget)
- `scripts/ui.gd` (any new colour, solved for contrast — never a literal)
- `tools/godot/setup_input.gd` (focus action)
- `docs/STATE.md`, `docs/BACKLOG.md`
