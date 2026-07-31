id: CAM-01
title: Board camera — pan, zoom, edge-scroll, cursor-follow
labels: phase-1, engine, ui
depends: CAM-02, CAM-05
blocks: CAM-03, CAM-04, CAM-06, PLC-01, PLC-06
milestone: E2 Camera
---
## Problem

anchor-24's tile bounding box is `(18 + 15) * 64 = 2112` px wide — **wider than the 1920
logical viewport at 100% interface scale**, and the strip between the two instrument panels is
about 940 px of that (`docs/STATE.md`, LF-076). At 200% the logical viewport is 960x540 and the
panels take 420 + 528 of it (LF-057). The compensation shipped today is
`anchor_view.gd:289` `_centre()`, which centres the board on the strip and then *nudges* the
whole placement so the ring specifically never runs off the window edge — a workaround for
having no camera, and one that already cannot fit anchor-13's 1920 px box. The theatre-scale
target is 32²–64², where the board exceeds the viewport several times over and the camera stops
being a convenience and becomes how the game is read (PRD §2.2 rank 1: *"No camera — already"*).

Two facts correct the old scoping in `docs/STATE.md`. `AnchorView.position` is now taken by the
screen-shake trauma system (`anchor_view.gd:103-111`, written at `:485`), so a camera must
*compose* with shake rather than own the transform. And `backdrop.gd` sizes itself from
`get_viewport_rect().size` while being a child of `AnchorView`, so scaling the parent tears it —
{{CAM-02}} solves that first.

## Tasks

- [ ] Fix the transform split and write it down: **`scale` is zoom, `_origin` is pan,
      `position` stays shake.** `_update_shake()` (`anchor_view.gd:470-485`) keeps `position`
      untouched, which also keeps shake amplitude in screen pixels at every zoom — verify that
      is the behaviour wanted before locking it in.
- [ ] Add `_cam_target: Vector2` (board-space point the camera looks at, float64) and
      `_cam_zoom: float`. Rewrite `_centre()` as `_apply_camera()`: it computes `_origin` from
      `_cam_target` and the strip rect it already derives from `Ui.gutter(vp)`, `Ui.COL_W`,
      `Ui.THREAT_W` and `Ui.dialog_h()`, and sets `scale = Vector2(_cam_zoom, _cam_zoom)`.
      Keep the existing "centre on the strip, not on `vp * 0.5`" formula — it is the corrected
      one and the comment at `:315-333` explains why.
- [ ] Fix mouse picking. `anchor_view.gd:723` does
      `IsoScript.screen_to_tile(get_global_mouse_position() - _origin)`, which is wrong the
      moment `scale != 1`. It becomes `screen_to_tile(to_local(get_global_mouse_position()) -
      _origin)`, and `to_local` must be used everywhere a screen point crosses into board space.
      Grep for `get_global_mouse_position` and fix every site.
- [ ] Middle-drag pan. `MOUSE_BUTTON_MIDDLE` press captures, motion pans by
      `-event.relative / _cam_zoom` in board space, release ends it. **Never left-drag** — left
      click arms builds and selects emplacements (`_click()` at `:728`), so drag-to-pan turns
      every slipped click into a camera move (LF-052).
- [ ] Wheel zoom about the cursor: the board point under the pointer stays under the pointer.
      Clamp to `[ZOOM_MIN, 1.0]`. **1.0 is the hard maximum** — the atlas is a fixed 256 px cell
      at one orthographic scale, so past 1.0 sprites soften and the fix is re-rendering 224
      sprites for a feature whose value is inspection (LF-052).
- [ ] Take `ZOOM_MIN` from {{CAM-05}}'s recorded decision. Do not invent it: fitting 64x64
      needs 0.234x at 100% and 0.117x at 200%, which renders a 256 px sprite at 30-60 px, and
      whether that is playable is the owner's call, not this issue's.
- [ ] Edge-scroll: pointer within N px of the *strip* edge (not the window edge — the panels
      are opaque) scrolls at a rate proportional to depth into the margin, framerate-independent.
      Add a `Display.edge_scroll` toggle defaulting on, surfaced in the options menu, because an
      always-on edge scroll is hostile to anyone using a trackpad or a magnifier.
- [ ] Cursor-follow: when `hovered_slot` / the board cursor moves under keyboard or gamepad
      control, pan just enough to keep it inside an inset of the strip. That is what gives
      keyboard and gamepad panning without a second system (LF-052), and it is the only path
      that keeps full keyboard/gamepad control — a shipped property (`docs/STATE.md`,
      decision 042).
- [ ] Add `lf_zoom_in`, `lf_zoom_out` and `lf_camera_reset` to `tools/godot/setup_input.gd` and
      regenerate. **Do not hand-edit `[input]` in `project.godot`** — a typo in a serialized
      `InputEvent` produces an action that silently never fires (decision 042). Bind gamepad
      shoulder buttons for zoom and a stick for pan if a free axis exists; say which in the
      commit body.
- [ ] Clamp the camera so the board's tile bounding box can never leave the strip entirely:
      when the board is smaller than the strip, centre it; when larger, clamp the target inside
      the board's bounds plus a margin.
- [ ] Leave the HUD alone. It is a `CanvasLayer` and must stay one — the type ladder and palette
      were solved for WCAG AA at a known size (decisions 045, 046, 050) and scaling them with the
      board silently undoes it. Assert this in the acceptance run by diffing `--a11y` inventories
      across zoom levels.
- [ ] Turn {{CAM-02}}'s backdrop parallax up from 0.0 and re-shoot; pick the factor by looking,
      not by formula.
- [ ] Update `docs/STATE.md`'s LF-052 scoping block to describe what shipped, and close LF-076.
- [ ] Add a `docs/DECISIONS.md` entry: the transform split (scale/origin/position), the 1.0
      zoom ceiling and why, and the no-left-drag rule.

## Acceptance criteria

- At anchor-24, `--camera` set to the board centre with zoom fitted to the strip puts every
  one of the board's four projected corners inside the strip rect, verified from the shot.
- Zoom is clamped to `[ZOOM_MIN, 1.0]`; scrolling past either end is a no-op, not a creep.
- Zooming with the pointer over a specific tile leaves that tile under the pointer to within
  1 px at every step from 1.0 down to `ZOOM_MIN`.
- Left-drag across the board never moves the camera; it produces exactly the click behaviour it
  produces today.
- Middle-drag pans; releasing outside the window does not leave the drag latched.
- The `--a11y` text inventory at zoom 1.0 and at `ZOOM_MIN` is identical in every item's size
  and colour — only the board moved.
- `add_trauma(1.0)` shakes by the same screen-space amplitude at zoom 1.0 and 0.4.
- Keyboard-only: stepping the board cursor to the far corner of anchor-24 brings that corner
  into view without touching the mouse.
- All 24 anchors boot with the camera framed at least as well as `_centre()` frames them today
  (the ring on screen, the board centred on the strip).

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/cam-1x.png
# camera hook lands in CAM-03; until then drive it with --cursor and a hand-set default
.venv/bin/python tools/shot.py anchor-24 --out /tmp/cam-cursor.png     # + --cursor 12
.venv/bin/python tools/check.py            # full gate, incl. game renders / accessibility
.venv/bin/python tools/reap.py
```

Proof is: the fitted-zoom shot of anchor-24 with all four corners inside the strip; two
`--a11y` JSONs at different zooms whose text inventories diff clean; and `accessibility`
reporting 0 failures at 100 / 150 / 200%.

## Risks / gotchas

- **`Backdrop` tears under scale.** {{CAM-02}} is a hard prerequisite, not a nicety.
- **`position` is shake's.** Writing pan into `position` will fight `_update_shake()` every
  frame and read as jitter, and the bug will look like a shake bug.
- **`Vector2` is float32.** The camera is presentation, so `Vector2` is fine *here* — but do
  not let a camera-space quantity leak into `anchor_sim.gd` or `sim/engine.py`, where it is
  banned (PRD §2.1: float32 vs float64 disagree on `<= r` 10.2% of the time).
- **A rendered check reads the player's save.** `window_mode`, `resolution` and `ui_scale` live
  in `user://progress.json`, outside the repo; every rendered check must lead with
  `--display-defaults`, first (`docs/STATE.md`).
- **A parse error is a hang, not an error at the failure site.** Annotate anything reached
  through an untyped reference; `--headless --check-only --script` a changed file before running.
- **`Label.clip_text` clips horizontally only** — irrelevant to the board, relevant the moment
  a camera-state readout is added to the HUD.
- Kill what you start: `.venv/bin/python tools/reap.py` after any run.

## Files likely touched

- `scripts/anchor_view.gd` (`_centre()` → `_apply_camera()`, `_unhandled_input`,
  `_action_input`, `_process`, `_slot_screen`, every `_origin` site)
- `scripts/backdrop.gd` (parallax factor)
- `scripts/display.gd` / options menu (`edge_scroll` toggle)
- `tools/godot/setup_input.gd`, `project.godot` (regenerated, never hand-edited)
- `docs/STATE.md`, `docs/DECISIONS.md`, `docs/BACKLOG.md`
