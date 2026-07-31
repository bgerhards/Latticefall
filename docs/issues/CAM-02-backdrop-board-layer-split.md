id: CAM-02
title: Split the backdrop off the board transform so the board can scale
labels: phase-1, engine, ui
blocks: CAM-01
milestone: E2 Camera
---
## Problem

`anchor_view.gd:289` `_centre()` is translation-only and its own docstring says why: *"No
`scale` here, however tempting a shrink-to-fit looks. This node's scale is inherited by every
child, including Backdrop, which sizes itself to `get_viewport_rect().size` independently of
the board."* `backdrop.gd` reads the raw viewport in three places — `_process()` at
`backdrop.gd:91`, `_advance_motes()`'s wrap region at `:227`, and `_draw()`'s cache check at
`:245` — and rebuilds against `_cached_vp`. Scale `AnchorView` and the sky covers a fraction
of the screen while the motes wrap around a rectangle that is no longer the window. That is
the single reason there is no zoom today, so it is the first thing {{CAM-01}} needs.

The other half of the same knot: `AnchorView.position` is now owned by the screen-shake
trauma system (`anchor_view.gd:103-111`, written at `:485`), and every draw call in the file
adds `_origin` by hand rather than using the node transform — deliberately, per the comment at
`:103`. A camera therefore cannot take `position`, and the zoom it *can* take (`scale`) is
exactly the one Backdrop cannot survive.

## Tasks

- [ ] Re-parent `Backdrop` in `scenes/main.tscn` from a child of `AnchorView` to a sibling
      drawn beneath it (same parent, `z_index` kept at −30 relative to the board, or its own
      `CanvasLayer` below the board's). Confirm draw order in a screenshot, not by reasoning
      about `z_index` inheritance.
- [ ] Rewrite `backdrop.gd`'s "reads its subject from its parent" contract (`backdrop.gd:1-13`
      and `var view: Node2D` / `_ready()`): it must be handed the `AnchorView` explicitly by
      `main.gd` or by an exported `NodePath`, because `get_parent()` is no longer the board.
      Leave a docstring saying why the parent-lookup idiom was dropped here and kept in
      `glow_layer.gd:16`.
- [ ] Give `Backdrop` a parallax hook rather than nothing: a `set_camera(offset: Vector2,
      zoom: float)` that translates the sky by `offset * PARALLAX` and **never** scales it.
      Default `PARALLAX = 0.0` in this issue so the frame is provably unchanged; {{CAM-01}}
      turns it up and re-shoots.
- [ ] Decide and record whether the backdrop still shakes. It does today by inheritance; the
      `PAD = 60.0` overhang at `backdrop.gd:61` exists so shake never bares an edge. Recommend
      it stops shaking (a static sky under a shaking board reads as a camera, not a wobble) and
      that `PAD` stays, since parallax will use it.
- [ ] Introduce the board-layer wrapper the camera will scale. `AnchorView` draws board tiles
      and entities in its *own* `_draw()`, so it cannot be the unscaled node — it is the thing
      that scales. Confirm `BoardProps`, `CombatFx`, `GlowLayer` and `FxAdditive` are all still
      children of `AnchorView` and inherit that scale; list them in the commit body.
- [ ] Rewrite `_centre()`'s docstring: it currently documents this blocker as permanent and
      points at "LF-052's deferred pan/zoom camera". It must instead say that `_origin` is the
      camera's pan term and `scale` is its zoom term, and that `position` belongs to shake.
- [ ] Re-run the render checks and diff coverage against the recorded baseline (`game renders`
      reports coverage 0.95, `menu renders` 0.144).

## Acceptance criteria

- `AnchorView.scale = Vector2(0.5, 0.5)` set by hand in the remote inspector shrinks the board,
  the props, the FX and the glow together, and leaves the sky filling the window.
- With `scale = 1.0` the frame is pixel-identical to the pre-change frame at anchor-01,
  anchor-13 and anchor-24 (compare PNG SHA-256, not by eye).
- `add_trauma(1.0)` still visibly shakes the board layer; nothing about shake changed.
- Motes still wrap without popping at any viewport size, checked at 100% and 200% interface
  scale.
- `check.py`'s `game renders` coverage stays within 0.01 of 0.95.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/cam02-before.png     # on main, first
# after the change
.venv/bin/python tools/shot.py anchor-24 --out /tmp/cam02-after.png
sha256sum /tmp/cam02-before.png /tmp/cam02-after.png                     # must match
.venv/bin/python tools/check.py --no-window
.venv/bin/python tools/reap.py
```

Proof is the matching SHA-256 at `scale = 1.0` plus a second pair of shots taken with a
hand-set `scale = 0.5` showing the sky still full-bleed.

## Risks / gotchas

- **Re-parenting in `main.tscn` changes node paths.** Anything using `$Backdrop` or a
  `get_node("Backdrop")` relative to `AnchorView` breaks silently at runtime, and in this
  project a script that fails to *parse* is a hang or a blank frame, never an error at the
  failure site (`docs/STATE.md`, decision 055). Grep before editing the scene.
- **`Display.changed.connect(queue_redraw)`** exists in `glow_layer.gd:22` and has an analogue
  in the backdrop path; a layer that only redraws when asked will appear to ignore an option.
- **Do not rebuild `.godot/`.** The owner plays out of this same tree; a cold import blanks
  their running level (LF-075).
- The `check.py` render checks read `user://progress.json` for `ui_scale` — every rendered
  check must lead with `--display-defaults`, and it must come first.

## Files likely touched

- `scenes/main.tscn`
- `scripts/backdrop.gd`
- `scripts/anchor_view.gd` (`_centre()` docstring only)
- `scripts/main.gd` (wiring the backdrop's view reference, if the `NodePath` route is taken)
