id: PLC-06
title: A free board cursor — keyboard and gamepad placement without a slot graph
labels: phase-2, ui, engine
depends: PLC-02, CAM-01
milestone: E3 Placement
---
## Problem

`scripts/anchor_view.gd:780-812` `_step_cursor()` is a discrete graph walk over the slot array.
It reads `sim.anchor["slots"]` directly (`:783`), returns immediately when that array is empty
(`:784-785`), lands on `all[0]` when the cursor is nowhere real (`:790-792`), and scores
candidates in *screen* space so that "up" means up on the screen rather than −y in tile space —
which is correct and must survive. Decision 042 records the design: *"the cursor moves between
slots rather than sweeping pixels: a virtual pointer on a stick is slow and imprecise, and the
only tiles that can be acted on are the slots anyway."*

Free placement removes both halves of that argument. With no slots there is nothing to walk, and
**full keyboard, mouse and gamepad control is a shipped property** (`docs/STATE.md`; PRD
invariant 6). This is PRD risk #7.

The mouse path is broken in the same way but more subtly: `anchor_view.gd:723` does
`hovered_slot = Vector2i(roundi(t.x), roundi(t.y))` — one line that throws away the continuity
free placement exists to provide — and `hovered_slot` / `selected_slot` are `Vector2i` throughout
(`:51`, `:56`, and the `NO_SLOT` sentinel).

## Tasks

- [ ] Change `hovered_slot` / `selected_slot` to continuous float64 board positions and rename
      them (`cursor_at`, `selected_at`) so no site keeps working by accident. Replace the
      `NO_SLOT` sentinel with an explicit `has_selection: bool` — a magic `Vector2i` sentinel does
      not survive the type change.
- [ ] Remove the rounding at `:723`: the mouse cursor is the raw projected position. Keep the
      `to_local()` fix {{CAM-01}} introduces, or picking is wrong at any zoom other than 1.0.
- [ ] Rewrite `_step_cursor(dir)` as a **sub-tile lattice step**: ½ tile per press by default,
      ¼ with a modifier, in screen-space directions exactly as today. Keep the screen-space
      reasoning and the comment explaining it.
- [ ] **Snap-to-legal on build**, not on move. Moving should be free and predictable; pressing
      `lf_build` snaps to the nearest legal position within a small radius (using {{PLC-02}}'s
      predicate) and builds there, or refuses with the reason. Snapping during movement makes the
      cursor feel like it is fighting the player.
- [ ] **Lane magnet**: holding a direction roughly along the lane biases the step toward tracking
      the path, so reaching "the far end of the lane" is a held direction rather than forty
      presses. Derive the bias from the path polyline; it is presentation, so it may use
      `Vector2`.
- [ ] Throttle the movement cue. `:811` plays `ui_hover` at −12 dB on every step; at ½-tile
      granularity across a 64-tile board that is a machine-gun. Rate-limit, or play it only on a
      legality transition.
- [ ] Update `placed_index_at()` (`:816-820`), which compares `p["slot"] == slot`. It becomes a
      nearest-hit test: the emplacement whose footprint contains the cursor, ties broken
      deterministically by `(x, y)`.
- [ ] Update `main.gd`'s `--cursor N` (`:212-217`, presses `lf_right` N times) — still
      meaningful, but N now means half-tiles; say so in the hook's comment. And `--build`
      (`:218-223` and `_place_requested()`), which builds "on the next free slot" and has no such
      thing anymore: give it an explicit position argument or a deterministic scan of the
      candidate lattice.
- [ ] Update `autobuild()` / `_autobuild_step()` (`:207-231`), the `--autoplay` smoke policy. It
      fills every free slot with the highest-priority affordable tower (LF-072); it now needs the
      same candidate ordering {{PLC-04}} gives the grader. Reuse that ordering rather than
      inventing a second one.
- [ ] Update `hud.gd:892`'s `"EMPLACEMENT · SLOT %d,%d"` kicker. It is **a11y-audited text** —
      `accessibility` counts 182 text items — so the replacement wording changes the baseline.
      Choose the wording once, deliberately, and re-record.
- [ ] Screenshot the keyboard path: cursor at three positions on anchor-24, one legal build and
      one refused, at 100% and 200% interface scale.
- [ ] Update `docs/DECISIONS.md` with an entry superseding decision 042's cursor half (the action
      map half stands unchanged), and refresh `docs/STATE.md`.

## Acceptance criteria

- With a gamepad only, on anchor-24, a player can reach any legal position on the board, build
  there, select the result, toggle its power, upgrade it and sell it.
- With a keyboard only, the same.
- The cursor never lands somewhere unreachable, and never becomes `NaN` or leaves the board.
- `lf_build` on an illegal position refuses with a reason and does not spend funds.
- Snapping moves the build by at most the snap radius, and the position built is the position
  shown by the ghost ({{PLC-07}}).
- `--cursor 12` reaches the same position on two consecutive runs (deterministic).
- `accessibility` reports 0 WCAG failures at 100 / 150 / 200% with the new kicker text.
- The `ui_hover` cue does not fire more than ~6 times per second under a held direction.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/cur.png --extra --cursor 12 --a11y /tmp/cur.json
.venv/bin/python tools/shot.py anchor-24 --out /tmp/cur2.png --extra --cursor 12
sha256sum /tmp/cur.png /tmp/cur2.png                     # identical — deterministic
.venv/bin/python tools/validate/a11y.py /tmp/cur.json --shot /tmp/cur.png --all
.venv/bin/python tools/check.py
.venv/bin/python tools/reap.py
```

Proof is the identical hashes, the a11y report with 0 failures, and a written record of a
gamepad-only and keyboard-only pass through build / select / power / upgrade / sell.

## Risks / gotchas

- **`--extra` cannot pass flag-shaped tokens today** (LF-073) — every command above needs
  {{PRC-09}} first.
- **Look at UI in the engine, never in the source.** Every UI defect this project has had was
  invisible in the code and obvious in a screenshot (`docs/STATE.md`).
- **The cursor is presentation; legality is a rule.** The view must ask `sim._is_placeable()` and
  never re-derive the test, or the ghost will say yes where the sim says no — and it will differ
  only on the boundary, which is exactly where the player aims.
- **A theme override under a name the theme does not know is accepted in silence**, and
  `Label.clip_text` clips horizontally only — both relevant to the new kicker text.
- **`hovered_slot` is read from more places than `_step_cursor`.** `_click`, `toggle_at`,
  `_draw_hover`, `_draw_reach` and the HUD all touch it; rename rather than retype so the
  compiler finds them all.
- {{PRC-12}}'s scenario harness will want to drive the cursor to an absolute position rather than
  by N presses — expose a setter.

## Files likely touched

- `scripts/anchor_view.gd` (`:51`, `:56`, `:207-231`, `:723`, `:780-812`, `:816-820`, draw sites)
- `scripts/hud.gd` (`:892` and the inspector's position readout)
- `scripts/main.gd` (`--cursor`, `--build`, `_place_requested`)
- `tools/godot/setup_input.gd` (a fine-step modifier action, if added)
- `docs/DECISIONS.md`, `docs/STATE.md`, `docs/BACKLOG.md` (LF-072)
