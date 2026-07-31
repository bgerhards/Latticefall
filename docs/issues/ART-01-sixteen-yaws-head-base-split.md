id: ART-01
title: 16-yaw facing with heads rendered separately from bases
labels: art, engine, tooling, phase-3
depends: ART-02, CAM-05, PRC-13, PRC-14
blocks: ART-06, ART-08
milestone: E6 Fidelity
---
## Problem

Every drawable is rendered at four yaws (`tools/blender/render.py:38` `YAWS = (45, 135, 225,
315)`, `:995`), so a turret tracking a target snaps through 90° steps. Decision 049 already
made facing *correct* — heading-driven, measured, hysteresis-damped — and the remaining
coarseness is purely a library-size problem. The naive fix, a flat 16-yaw library, is
**832 renders**. The right fix is cheaper *and* better: **bases do not track**, so they stay at
four yaws and only the head is rendered at sixteen.

Measured counts: 10 emplacement heads × 16 yaws × 2 passes = 320, 10 bases × 4 × 2 = 80,
16 units × 8 × 2 = 256, props ~24 → **~680 renders**, against **832** flat and **208** today.
`render.py`'s `ASSETS` table (`:701-728`) builds every model in script, so separating a head
from a base is a modelling change in a file that already exists rather than a new pipeline.
The pivot is shared by every cell (`pack_atlas.py:11-17`), depth sorting is per entity
(`scripts/iso.gd:25-26`), and the cost at draw time is **two `draw_texture` calls per
emplacement** instead of one (`scripts/anchor_view.gd:1160`).

The real cost is the art: every model has to be separated by hand, which is why {{ART-08}}
exists.

## Tasks

- [ ] Land {{ART-02}} first. `Iso.YAW_HYSTERESIS_DEG = 12.0` exceeds half a 16-yaw bucket
      (11.25°) and would lock every facing permanently (LF-108). Do not raise the yaw count
      before that is fixed and re-measured.
- [ ] **Resolve the yaw-slot naming, which does not survive 16 buckets.** `scripts/sprites.gd:95`
      formats a slot as `"y%03d" % yaw` and `Iso.yaw_for_heading()` returns an `int`
      (`iso.gd:64-78`). Sixteen yaws are 22.5° apart, so half of them are not integers.
      Convert the slot key and the facing API to a **bucket index** (`b00`…`b15`), with the
      degrees derived from the index where Blender needs them. Doing this as "round to the
      nearest degree" would collide `y067`/`y068` on the next resolution change.
- [ ] `tools/blender/render.py`: split each emplacement's model function into a base function
      and a head function; add a per-asset descriptor to `ASSETS` naming which parts render at
      which yaw count (`base: 4`, `head: 16`, unit: `8`, prop: `4` or `1`). Do not hardcode a
      second yaw list — read the count from the descriptor.
- [ ] `tools/blender/render.py`: the head must render **in the same cell geometry** as the base
      — same camera, same `HEIGHT_BIAS`, same `ORTHO_SCALE`, same 256 px cell — so the two
      layers composite by drawing at the same pivot with no offset. Verify with a rendered
      overlay, not by eye.
- [ ] `assets/renders/sprites.json`: the manifest gains a per-asset part list. Keep the existing
      per-file paths intact so `sprites.gd`'s loose-file fallback still works
      (`pack_atlas.py:23-28`).
- [ ] `tools/blender/mask_glow.py`: confirm it is still idempotent over the larger library and
      that a head's glow pass masks correctly against a mostly-empty cell.
- [ ] `tools/blender/pack_atlas.py`: `COLS = 12` is hardcoded at `:52`. A flat 16-yaw library is
      416 cells per pass → 35 rows → **3072 × 8960 px**; `COLS = 21` gives 5376 × 5120. The
      split library is ~340 cells per pass → 29 rows → 3072 × 7424 at `COLS = 12`. **Query
      `GL_MAX_TEXTURE_SIZE` on the owner's GPU (an RTX 5070 Ti is present) before committing to
      a page geometry** and paste the number. Then choose `COLS` to keep both dimensions
      comfortably inside it, and read it from a single place.
- [ ] **Measure atlas VRAM before and after.** The atlas imports uncompressed RGBA8
      (`compress/mode=0`, `"vram_texture": false`): 4 yaws = 56.6 MB, 8 = 113 MB, 16 = **226 MB**
      on GL Compatibility. The split library sits between; measure it, do not interpolate.
      Record the figure in `docs/STATE.md` — this is the constraint, not render time (the whole
      library re-renders in under two minutes at any of these counts).
- [ ] `scripts/sprites.gd`: fetch a part by `(name, part, bucket, pass)`; cache as today.
- [ ] `scripts/anchor_view.gd:997-1024` `drawables()`: an emplacement emits a base entry at its
      4-yaw bucket and a head entry at its 16-yaw bucket, **at the same depth**, with the head
      after the base in the sorted list. `out.sort_custom` compares `depth` only
      (`:1023`), and Godot's sort is not stable — add an explicit tiebreaker so the head can
      never draw under its own base.
- [ ] `scripts/anchor_view.gd`: the additive glow pass (`:1160`) walks the same list, so both
      parts must appear in it. Confirm the glow layer still modulates by bus load (decision 007)
      for both parts.
- [ ] `tools/check.py`'s `sprite atlas` check hashes every render; confirm it still passes and
      that the digest covers the new parts.
- [ ] Extend the `--facings` verification hook (decision 049) to print the base bucket and the
      head bucket separately on the captured frame. Four yaws of one turret already differ by
      which side the muzzle is on and 40 px of height; sixteen makes that unreadable from a
      screenshot alone.
- [ ] Re-measure the hysteresis band at 16 buckets using {{ART-02}}'s harness and record the
      changes/reversals figures the way decision 049 did (116 changes / 40 reversals → 59 / 3
      at four yaws).
- [ ] Render → `mask_glow` → `pack_atlas` → `--import` → screenshot. Never skip a step.
- [ ] Screenshot a board with several emplacements tracking different targets, at 100% and 200%
      interface scale, and diff against the 4-yaw baseline.

## Acceptance criteria

- `GL_MAX_TEXTURE_SIZE` is queried on the owner's GPU and the chosen page dimensions are
  strictly inside it, with both numbers pasted.
- Atlas VRAM after the change is measured and recorded, and is under 226 MB (the flat-16
  figure) — the split is supposed to be cheaper, and if it is not the split has been done
  wrong.
- A turret tracking a unit across the board changes facing at 22.5° granularity with **no
  facing that never changes** — the LF-108 failure mode is a *frozen* sprite, which looks like
  the feature simply not working.
- `--facings` prints a base bucket in `{0..3}` and a head bucket in `{0..15}` for every
  emplacement, and they are consistent with the drawn frame.
- Base and head composite with zero pixel offset: an overlay of the two layers reproduces the
  single-model render of the same asset at a shared yaw.
- The head never draws beneath its own base at any depth.
- `tools/check.py`'s `sprite atlas` check passes.

## Verification

```bash
# probe the GPU limit first and paste the number
<godot> --headless --path . --script tools/godot/gl_limits.gd     # or equivalent probe
<blender> -b --python tools/blender/render.py -- --calibrate
<blender> -b --python tools/blender/render.py
.venv/bin/python tools/blender/mask_glow.py
.venv/bin/python tools/blender/pack_atlas.py
<godot> --headless --path . --import
.venv/bin/python tools/shot.py anchor-12 --out /tmp/yaw16.png --extra --facings
.venv/bin/python tools/shot.py anchor-12 --out /tmp/yaw16_2x.png --ui-scale 2.0
.venv/bin/python tools/check.py --no-window
```

Proof to paste: the `GL_MAX_TEXTURE_SIZE` value, the `pack_atlas` page dimensions line, the
measured VRAM figure, the `--facings` block, and the two screenshots.

## Risks / gotchas

- **Rotating sprites in-engine is not an alternative, and this is measured rather than
  asserted.** The camera is orthographic at 30°, so world verticals project to *exact* screen
  verticals — that invariant is the isometric look. A 22.5° 2D rotation swings the top of a
  96 px barrel **36.7 px** sideways (67.9 px at 45°). Even a flat 1×1 footprint deforms:
  best-fit screen rotation leaves **11.7 px RMS = 36.5%** of footprint radius at 22.5° world
  yaw and **22.0 px RMS = 68.7%** at 45°. The only legitimate hybrid is rotating genuinely
  flat ground-plane elements — a reticle, a decal — never a sprite with height.
- **The integer yaw slot is a silent collision waiting to happen.** `"y%03d" % 67.5` does not
  round-trip. Move to bucket indices before rendering anything.
- **`YAW_HYSTERESIS_DEG` freezes every facing at 16 yaws** (LF-108, PRD risk 8). It is
  {{ART-02}} and it is a hard prerequisite.
- Three places independently encode the yaw count today: `render.py:38`, `iso.gd:78`
  (`90 * roundi(deg / 90.0)`, with `YAW_FOR_PLUS_X` at `:50`), and `iso.gd:75`
  (`45.0 + hysteresis_deg`). {{ART-02}} collapses them; if it has not, do not proceed.
- **A re-render is invisible until `--import`, and the board draws from the atlas, not the
  loose PNGs.** Skipping either makes a correct art fix look like it did nothing. The gate's
  `sprite atlas` check turns the second mistake red rather than mysterious.
- Sprite legibility at zoom-out (CAM-05, LF-104) sets the art bar: at 30–60 px on screen, a
  16-yaw head may be indistinguishable from an 8-yaw one. Get that decision before spending
  the modelling time.
- 226 MB of uncompressed RGBA8 on GL Compatibility is PRD risk 9. Combining 16 yaws with 512 px
  cells ({{ART-04}}) is 4× that and is not viable — the two issues must not both land
  unmeasured.

## Files likely touched

- `tools/blender/render.py`, `tools/blender/mask_glow.py`, `tools/blender/pack_atlas.py`
- `assets/renders/**` (the library), `assets/renders/sprites.json`
- `scripts/iso.gd`, `scripts/sprites.gd`, `scripts/anchor_view.gd`, `scripts/main.gd`
- `tools/check.py`
- `docs/DECISIONS.md`, `docs/STATE.md`, `CLAUDE.md`
