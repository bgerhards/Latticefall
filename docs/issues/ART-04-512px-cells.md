id: ART-04
title: LF-090 — raise the sprite atlas to 512 px cells, with VRAM as the budget
labels: art, tooling, perf, phase-3
depends: ART-03
milestone: E6 Fidelity
---
## Problem

The library renders at 256 px cells (`tools/blender/render.py:41`), which is four times fewer
pixels per sprite than 512. Render time is not the constraint — the whole library re-renders in
under two minutes at any yaw count — and neither is git-LFS (~56 MB at 16 yaws against a 1 GB
quota). **VRAM is the constraint.** The atlas imports uncompressed RGBA8 (`compress/mode=0`,
`"vram_texture": false`), which on GL Compatibility gives 56.6 MB at 4 yaws, 113 MB at 8 and
**226 MB at 16** — at 256 px cells. Quadrupling the cell quadruples all of it.

LF-102 ({{ART-03}}) blocks this outright: `calibrate()` cannot converge at 384 or 1024 px, and
256 and 512 work by luck. And `pack_atlas.py` carries its own hardcoded `CELL = 256`, so two
constants must move in lockstep.

## Tasks

- [ ] Confirm {{ART-03}} has landed and `--calibrate` converges at 512 with the pivot written
      to the manifest.
- [ ] **Query `GL_MAX_TEXTURE_SIZE` on the owner's GPU and paste it.** At 512 px cells the page
      is four times the area; with the current `COLS = 12` the 4-yaw library alone becomes
      6144 px wide. Choose `COLS` against the measured limit, not against "comfortably inside
      any GL texture limit" (`pack_atlas.py:52`), which was written for 3072.
- [ ] Decide the yaw count this lands with, explicitly. **512 px cells and 16 yaws together is
      roughly 900 MB of uncompressed RGBA8 and is not viable.** Either land 512 px at the
      current yaw count, or land {{ART-01}}'s split library at 256 px, or change the import to
      a compressed VRAM format — but that last one is its own decision, because it changes how
      every sprite looks and the project's whole colour argument (`view_transform='Standard'`,
      sRGB palettes linearised by `mat()`) assumes lossless.
- [ ] Measure the actual VRAM after import rather than computing it. Godot's import settings,
      mipmap generation and page padding all move the number.
- [ ] Re-render the full library at 512 and run the pipeline in order: render → `mask_glow` →
      `pack_atlas` → `--import`. Confirm `mask_glow` is still idempotent at the larger cell.
- [ ] Confirm the pivot moves as expected: at 512 px the measured pivot should scale from
      (127.5, 171.5); check it against the manifest rather than assuming, and confirm
      `scripts/sprites.gd` reads it.
- [ ] Check the draw path. `scripts/anchor_view.gd:1067` and `:1160` draw at
      `position - pivot` with no scaling; a 512 px sprite for a 128 px tile is drawn at 1:1 and
      will be twice the size on screen unless the draw scales it. **Decide whether 512 px cells
      mean bigger sprites or higher-resolution sprites** — those are completely different
      changes, and the second one interacts with CAM-05 and LF-104's legibility question.
- [ ] Measure frame time before and after at a realistic board; four times the texture bandwidth
      is not free even when it fits.
- [ ] Screenshot the same anchor at 256 and 512 and put them side by side. If the difference is
      not visible at the zoom levels the game actually plays at, close the issue as not worth
      the VRAM.
- [ ] Update `CLAUDE.md`'s art pipeline facts with the new cell size, the new pivot and the
      measured VRAM, and record the `GL_MAX_TEXTURE_SIZE` value.

## Acceptance criteria

- `GL_MAX_TEXTURE_SIZE` is pasted and both atlas page dimensions are strictly inside it.
- Measured post-import VRAM is recorded in `docs/STATE.md` and is a number, not an estimate.
- `calibrate()` prints `CALIBRATION ok` at 512 and the manifest carries the measured pivot.
- Every sprite draws on its own tile — the LF-027 failure — verified by screenshot at two
  anchors.
- `tools/check.py`'s `sprite atlas` check passes.
- The 256-vs-512 side-by-side exists and the decision to keep or revert is recorded.
- Frame time at a realistic board has not regressed by more than a stated budget.

## Verification

```bash
<blender> -b --python tools/blender/render.py -- --calibrate --cell 512
<blender> -b --python tools/blender/render.py -- --cell 512
.venv/bin/python tools/blender/mask_glow.py
.venv/bin/python tools/blender/pack_atlas.py
<godot> --headless --path . --import
.venv/bin/python tools/shot.py anchor-06 --out /tmp/c512_a.png
.venv/bin/python tools/shot.py anchor-24 --out /tmp/c512_b.png --ui-scale 2.0
.venv/bin/python tools/check.py --no-window
```

Proof to paste: the calibration line, the packer's page dimensions, the measured VRAM, both
screenshots, and the gate's `sprite atlas` line.

## Risks / gotchas

- **226 MB at 16 yaws and 256 px is already PRD risk 9.** Do not stack this on {{ART-01}}
  without measuring; ~900 MB of uncompressed RGBA8 on GL Compatibility is not a budget, it is a
  crash on a modest GPU.
- The pack is a **fixed grid and never trims** (`pack_atlas.py:11-17`). Trimming to save page
  area would give every sprite its own origin and reintroduce LF-027. Do not reach for it as a
  VRAM saving.
- Bigger cells versus higher-resolution sprites is the real design question hiding in this
  issue. Answer it explicitly, with CAM-05 in hand.
- `mask_glow.py` must run after every render; the compositor writes alpha 1 across the frame
  and an unmasked glow fills the board with bright rectangles.
- A re-render is invisible until `--import`, and the board draws from the atlas. Full order,
  every time.

## Files likely touched

- `tools/blender/render.py`, `tools/blender/pack_atlas.py`, `tools/blender/mask_glow.py`
- `assets/renders/**`, `assets/renders/sprites.json`
- `scripts/sprites.gd`, `scripts/anchor_view.gd`
- `docs/BACKLOG.md`, `docs/STATE.md`, `CLAUDE.md`
