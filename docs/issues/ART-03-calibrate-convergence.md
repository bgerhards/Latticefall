id: ART-03
title: LF-102 — calibrate() cannot converge at 384 or 1024 px cells, and pack_atlas has its own CELL
labels: art, tooling, phase-3
blocks: ART-04
milestone: E6 Fidelity
---
## Problem

`tools/blender/render.py:921-948` `calibrate()` solves `ORTHO_SCALE` by measuring a rendered
1×1 tile and correcting from the **width ratio only** (`:944`
`ORTHO_SCALE = ORTHO_SCALE * (float(w) / float(TILE_W))`), while its success test requires both
width **and** height to be exact (`:937` `if w == TILE_W and h == TILE_W // 2`). A one-pixel
height shortfall therefore never resolves: the width is already right, so the correction is a
no-op, and it burns all six iterations and fails. Reproduced deterministically at 384 px cells
(tile 192×95 against an expected 192×96) and at 1024 px (512×255 against 512×256). 256 and 512
work by luck.

This blocks every resolution raise, so it is a prerequisite for LF-090 ({{ART-04}}). There is a
second, independent constant problem: `tools/blender/pack_atlas.py:51` hardcodes `CELL = 256`
and does not read it from the manifest, so the two must move in lockstep or the packer raises
on the first render it opens (`:106-108`).

## Tasks

- [ ] Reproduce both failures first and paste the output — `--calibrate` at `CELL = 384` and at
      `CELL = 1024`. A fix with no reproduction is a guess.
- [ ] Correct the solver so it converges on both axes. The honest fix is to correct from
      whichever axis is further out, or to solve on the *diagonal* the projection actually
      constrains: a 1×1 tile is `sqrt(2)` world units across and `ORTHO_SCALE` maps that to
      `TILE_W` px, with the height following from the 30° elevation. Derive the correction
      from the measured ratio in both axes and iterate on the larger error.
- [ ] Decide what "exact" means at odd cell sizes. `TILE_W // 2` assumes an even tile height;
      at 384 px cells the nominal tile is 192×96 and the render measures 95, which may be a
      half-pixel boundary rather than a scale error. **Measure the sub-pixel coverage before
      changing the tolerance** — loosening the assertion to `abs(h - TILE_W // 2) <= 1` would
      make the calibration stop being a calibration, which is the thing decision 017 exists to
      prevent.
- [ ] Keep `calibrate()` a hard gate: it must still refuse to render when the projection is off,
      and it must still print the solved `ORTHO_SCALE` and the measured `PIVOT`.
- [ ] `tools/blender/pack_atlas.py:51`: read `CELL` from `assets/renders/sprites.json` rather
      than hardcoding it, keeping the size assertion at `:105-108` (that assertion is the
      pivot's safety argument — see the module docstring at `:11-17`).
- [ ] `assets/renders/sprites.json`: ensure `render.py` writes the cell size and the solved
      pivot into the manifest at every run, so the packer and the engine read one number.
- [ ] `scripts/sprites.gd`: confirm the pivot is read from the manifest, not derived from a
      texture size, and fail loudly if the manifest lacks it (LF-027 was a hardcoded `CELL//2`
      drawing every sprite above its own tile).
- [ ] Prove convergence at 256, 384, 512, 768 and 1024 px cells with `--calibrate`, and paste
      the table of solved `ORTHO_SCALE` and measured pivot for each.
- [ ] Render one asset at a non-256 cell end to end (`render.py --only pulse_turret`,
      `mask_glow`, `pack_atlas`, `--import`) and screenshot it on the board to confirm it lands
      on its tile.
- [ ] Close LF-102 in `docs/BACKLOG.md` with the convergence table.

## Acceptance criteria

- `--calibrate` converges and prints `CALIBRATION ok` at **all five** cell sizes above.
- The solved `ORTHO_SCALE` at 256 px is unchanged to at least six decimal places, so the
  existing library does not need re-rendering to remain correct.
- `pack_atlas.py` contains no `CELL` literal and packs correctly at a non-256 cell size.
- A sprite rendered at a non-256 cell draws on its own tile in-game, verified by screenshot.
- `calibrate()` still fails hard on a genuinely wrong projection (prove it by perturbing
  `ELEVATION_DEG` and pasting the failure).

## Verification

```bash
<blender> -b --python tools/blender/render.py -- --calibrate --cell 256
<blender> -b --python tools/blender/render.py -- --calibrate --cell 384
<blender> -b --python tools/blender/render.py -- --calibrate --cell 512
<blender> -b --python tools/blender/render.py -- --calibrate --cell 768
<blender> -b --python tools/blender/render.py -- --calibrate --cell 1024
<blender> -b --python tools/blender/render.py -- --only pulse_turret --cell 512
.venv/bin/python tools/blender/mask_glow.py
.venv/bin/python tools/blender/pack_atlas.py
<godot> --headless --path . --import
.venv/bin/python tools/shot.py anchor-01 --out /tmp/cell512.png
```

Proof to paste: five `CALIBRATION ok` lines with their solved scales and pivots, the deliberate
failure, and the screenshot.

## Risks / gotchas

- **Do not "fix" this by loosening the tolerance.** The whole reason `calibrate()` exists is
  that a constant written from memory was wrong for six sessions (decision 017). A calibration
  that accepts a pixel of error is a constant with extra steps.
- The pivot is measured, not assumed: `HEIGHT_BIAS` puts world (0,0,0) about 43 px below the
  canvas centre, measured at (127.5, 171.5) in a 256 px cell. Changing the cell changes the
  pivot, and every consumer must take it from the manifest.
- `ORTHO_SCALE_NOMINAL = CELL * sqrt(2) / TILE_W` and `TILE_W` scales with `CELL`; make sure
  `--cell` moves both, or the calibration is solving for a projection nothing else uses.
- Blender 5.2 facts still apply: only `BLENDER_EEVEE` is registered, `scene.node_tree` does not
  exist, Glare settings are input sockets. Do not re-derive them; verify against the installed
  tool if anything looks different.
- `pack_atlas.py` raising on a mismatched cell size is a **feature** (`:106-108`). Keep it.

## Files likely touched

- `tools/blender/render.py`, `tools/blender/pack_atlas.py`
- `assets/renders/sprites.json`
- `scripts/sprites.gd`
- `docs/BACKLOG.md`
