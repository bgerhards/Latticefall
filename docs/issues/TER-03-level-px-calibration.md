id: TER-03
title: Measure level_px in calibrate(), write it to the manifest, assert it in sprites.gd
labels: tooling, art, phase-2
depends: TER-01
blocks: TER-14
milestone: E4 Terrain
---
## Problem

`LEVEL_PX = 32` is currently a number in a document. The project's standing rule is
"verify against the installed tool, never from memory", and the reason it exists is
decision 017: the iso camera angle in `CLAUDE.md` was wrong for six sessions because it
was derived from memory rather than measured. `tools/blender/render.py:921` (`calibrate()`)
already refuses to render if a 1×1 plane does not measure exactly `TILE_W × TILE_W/2`, and
`scripts/sprites.gd:60` already refuses to load the library when the manifest's `tile_px`
disagrees with `Iso`. Height gets neither guard, so a future change to `ortho_scale`,
`HEIGHT_BIAS` or `CELL` would silently desynchronise the blit offset from the projection
the art was rendered in — and the symptom is "everything is slightly wrong", the least
diagnosable failure mode this project has.

One extra 256 px render costs nothing next to re-rendering the library, which is the
argument `calibrate()`'s own docstring already makes (`render.py:921-930`).

## Tasks

- [ ] Extend `_measure_tile()` (or add `_measure_level()` beside it, sharing the
      one-sample / `filter_size = 0.0` setup) to render a second plane raised to
      `z = LEVEL_H = 0.401872` with the *production* camera, and measure the screen
      displacement of its silhouette centre from the ground plane's.
- [ ] Assert the measured displacement is `LEVEL_PX = 32` px within 0.5 px and **abort the
      render** on failure, with a message naming `ortho_scale`, `HEIGHT_BIAS`, `CELL` and
      the measured value — the same shape as the existing `CALIBRATION FAIL` lines
      (`render.py:911`, `:946`).
- [ ] Print a `CALIBRATION level_px=…` line alongside the existing `CALIBRATION ok` and
      `CALIBRATION pivot=` lines so a render log records it.
- [ ] Write `"level_px"` and `"level_h"` into the manifest dict at `render.py:974`,
      next to `tile_px`, `pivot` and `ortho_scale`.
- [ ] Add the reciprocal assertion to `scripts/sprites.gd:60`: if
      `int(doc["level_px"]) != int(Iso.LEVEL_PX)`, `push_error` and refuse to load, with
      the same wording pattern as the existing tile_px error ("art and game disagree").
- [ ] Treat a manifest with **no** `level_px` as ok-with-a-warning, not an error — the
      manifest is rewritten by `render.py` without the atlas section already
      (`sprites.gd:69`), and a between-steps state must not brick the game.
- [ ] Add `level_px` to whatever `tools/check.py`'s `sprite atlas` / `sprite coverage`
      checks assert about the manifest, so a manifest missing it after a full render is a
      red gate.
- [ ] Record in `CLAUDE.md`'s art-pipeline section that `level_px` is measured, not
      chosen, and that `LEVEL_H` derives from the **solved** `ortho_scale = 2.784233` and
      never from the nominal 128 (the rendered tile is geometrically 130.03 px).

## Acceptance criteria

- A full `render.py` run prints `CALIBRATION level_px=32.0` (±0.5) and writes
  `"level_px": 32` and `"level_h": 0.401872` into `assets/renders/sprites.json`.
- Perturbing `LEVEL_H` in `render.py` by 5% makes the render **abort** with a message
  naming the measured value — demonstrate this, then revert.
- Editing `level_px` in the manifest to 33 makes the game refuse to load the library with
  a `push_error`, not draw a subtly wrong board.
- Deleting `level_px` from the manifest produces a warning and a working game.
- The measured value is consistent with the independent Blender probe already recorded in
  PRD §2.3 (`k/s = 0.865994` vs `cos 30° = 0.866025`).

## Verification

```bash
Blender -b --python tools/blender/render.py -- --calibrate
# expect: CALIBRATION ok tile=128x64 …  /  CALIBRATION pivot=(127.5,171.5) …
#         CALIBRATION level_px=32.0 level_h=0.401872
.venv/bin/python -c "import json;d=json.load(open('assets/renders/sprites.json'));print(d['level_px'], d['level_h'])"
```

Then the negative case, which is the part that proves the guard exists:

```bash
# temporarily set LEVEL_H *= 1.05 in render.py
Blender -b --python tools/blender/render.py -- --calibrate   # expect non-zero exit + FAIL line
```

## Risks / gotchas

- `--calibrate` must remain able to run **without** re-rendering the library
  (`render.py:970`). Do not put the level probe behind the asset loop.
- The pixel buffer is bottom-up in Blender and top-left-origin in Godot
  (`render.py:914-917`). The raised plane's centre moves to a *smaller* y in Godot
  coordinates; get the sign right or the assertion passes on the absolute value while the
  offset is upside down.
- Use `filter_size = 0.0` and `taa_render_samples = 1` for the level probe exactly as
  `_measure_tile()` does, or the reconstruction filter blurs the silhouette and the
  measured centre drifts.
- The raised plane must not clip the frame. `HEIGHT_BIAS = 0.55` leaves 84.5 px above the
  pivot's lower half; a plane at one level is fine, several are not. Probe one level and
  multiply — do not probe at `z = 3·LEVEL_H`.
- **Do not "fix" a failing assertion by changing `HEIGHT_BIAS`.** It is shared by all 26
  assets and `calibrate()` re-derives the pivot from it; changing it re-renders the world.
- `calibrate()` cannot converge at 384 or 1024 px cells (PRD §6/E6) — it corrects
  `ORTHO_SCALE` from the width ratio only. Do not entangle this issue with a cell-size
  change.

## Files likely touched

- `tools/blender/render.py`
- `assets/renders/sprites.json` (regenerated)
- `scripts/sprites.gd`
- `tools/check.py`
- `CLAUDE.md`
