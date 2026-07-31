id: CAM-05
title: Sprite legibility at zoom-out — needs the owner's decision
labels: phase-1, design, risk, art
blocks: CAM-01
milestone: E2 Camera
---
## Problem

**This is open decision #1 in the PRD (§6) and it blocks {{CAM-01}} being called done.** It is
not an engineering call and the programme must not guess at it.

Measured (LF-104): fitting a 64x64 board into the strip between the instrument panels needs zoom
**0.234x at 100% interface scale** and **0.117x at 200%**. The sprite library is a fixed 256 px
atlas cell at one orthographic scale, so that renders a sprite at **30-60 px on screen** —
likely below the threshold at which one enemy type can be told from another. A big board plus a
fixed 256 px atlas is a legibility wall, and the camera cannot fix what the art cannot show.

The camera needs one number out of this — `ZOOM_MIN` — and it cannot be invented. Pick it wrong
low and the game is unreadable at the zoom the board requires; pick it wrong high and a 64²
board is never visible as a whole, which is a design statement that ought to be made on purpose.

## The three options, with their real costs

**1. Raise `Iso.TILE_W` / `TILE_H`** (128 / 64 today, `scripts/iso.gd:8-9`). A whole-game layout
change. It touches: every screen-space calculation (`_centre()`, `_draw_board()`,
`Iso.diamond()`, the 27 / 15 px contact-shadow radii at `anchor_view.gd:1152`, the HUD's
measured line-height cursor in `hud.gd`); the **measured** sprite pivot, which `calibrate()`
solves and writes to the manifest; `pack_atlas.py`'s hardcoded `CELL = 256`, which is *not* read
from the manifest; `render.py`'s `ortho_scale = W * sqrt(2) / 128`; and the a11y baselines,
because the board's contrast samples come out of the composited frame. And it is partly blocked:
**`calibrate()` cannot converge at 384 or 1024 px cells** — it corrects `ORTHO_SCALE` from the
width ratio only, so a one-pixel height shortfall never resolves; 256 and 512 work by luck
(LF-102). Tile size and the 30.0° camera elevation are *one* decision (decision 002 / 017);
changing either invalidates the sprite library.

**2. A zoom floor, with the minimap doing the wide read.** Cheapest by a wide margin. Set
`ZOOM_MIN` so a sprite never renders below a measured legibility threshold, and make {{CAM-04}}
the surface on which the whole board is read at once. Consequence to accept explicitly: a 64²
board is *never* fully visible in the playfield.

**3. Silhouette-first art.** Authored to survive 30 px: distinct outlines, no reliance on
interior detail, faction read carried by shape and value rather than by hue. Applies to the
whole library at once, and the Hollow set is the one most likely to need re-authoring — its
lit and dark cones already collapse at yaw 315 (LF-049).

## Tasks

- [ ] Build the evidence, do not argue from it. Render a contact sheet of every enemy and every
      emplacement at **30 / 45 / 60 / 90 px** on the real board background, one page, from the
      committed atlas. Idempotent script, output to `docs/shots/`.
- [ ] Measure pairwise silhouette distinguishability at each size: downsample each albedo to the
      target size, threshold on alpha, and report a pairwise difference matrix. Name the pairs
      that collapse. This turns "likely below the threshold" into a number.
- [ ] Screenshot anchor-24 at zoom 1.0 / 0.5 / 0.35 / 0.234 / 0.117 at 100% and 200% interface
      scale, with a full wave on the board. Six to ten images, side by side.
- [ ] Cost option 1 concretely: grep and list every site that reads `Iso.TILE_W` or `TILE_H`, and
      every constant derived from 128/64. Include the `calibrate()` blocker (LF-102) and say
      whether 512 px cells are reachable without fixing it.
- [ ] Cost option 3 concretely: which assets fail the silhouette matrix at 30 px, and what
      re-authoring each needs.
- [ ] **Put the three options and the evidence in front of the owner and get an answer.** Do not
      pick one.
- [ ] Record the answer as a `docs/DECISIONS.md` entry with the rejected alternatives, per the
      append-only convention.
- [ ] Set `ZOOM_MIN` in {{CAM-01}} from the decision, with a comment citing the decision number.
- [ ] Feed the answer into PRD open decision #2 (board size target — 32², 48², 64²), which sets
      the culling, atlas and balance budgets and is partly downstream of this one.

## Acceptance criteria

- A single artefact exists that a non-engineer can look at and answer from: the contact sheet,
  the silhouette matrix, and the anchor-24 zoom ladder.
- The silhouette matrix names, for each candidate size, which enemy pairs are indistinguishable.
- A `docs/DECISIONS.md` entry records the choice, the rejected options and their measured costs.
- `ZOOM_MIN` is a named constant citing that decision, not a literal.
- {{CAM-01}} cannot be closed while this issue is open.

## Verification

```bash
.venv/bin/python tools/legibility.py --sizes 30,45,60,90 --out docs/shots/legibility.png
.venv/bin/python tools/legibility.py --matrix          # pairwise silhouette distances
for z in 1.0 0.5 0.35 0.234 0.117; do
  .venv/bin/python tools/shot.py anchor-24 --out /tmp/zoom-$z.png --extra --camera 9 7 $z
done
.venv/bin/python tools/reap.py
```

Proof is the artefacts plus the decision entry. There is no green check for this one; the
falsifiable part is the matrix, and the judgement is the owner's.

## Risks / gotchas

- **Do not derive the tile-size cost from memory.** The camera elevation in `CLAUDE.md` was wrong
  for six sessions because it was remembered rather than measured (decision 017). Grep and probe.
- **A re-render is invisible until re-import, and the atlas is what the board draws from.** If
  any option is prototyped with new art: render → `mask_glow` → `pack_atlas` → `--import` →
  screenshot, in that order, or a correct fix looks like it did nothing (twice over).
- **The pack is a fixed 256 px grid and never trims.** One measured pivot serves every sprite
  only because every cell is identical; trimming would reintroduce LF-027.
- Do not "improve" the answer by scaling the HUD with the board. The ladder and palette are
  solved for WCAG AA at a known size (decisions 045, 046, 050).
- This issue is allowed to end without code. Blocking is its job.

## Files likely touched

- `tools/legibility.py` (new, throwaway-quality is fine but it must be idempotent)
- `docs/shots/` (committed evidence)
- `docs/DECISIONS.md` (the answer)
- `scripts/anchor_view.gd` (`ZOOM_MIN` only)
- `docs/BACKLOG.md` (close LF-104)
