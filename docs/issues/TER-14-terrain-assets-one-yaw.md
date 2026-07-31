id: TER-14
title: Rendered terrain assets at one yaw — a per-asset yaw list through the whole pipeline
labels: art, tooling, phase-3
depends: TER-03, TER-07, TER-10
milestone: E4 Terrain
---
## Problem

The procedural cliffs of {{TER-07}} and the procedural decks of {{TER-10}} are placeholders
that were deliberately good enough to play and tune with. Replacing them with rendered
geometry runs into a pipeline assumption: `tools/blender/render.py:38` declares
`YAWS = (45, 135, 225, 315)` as a module constant and every asset is rendered at all four,
because an emplacement or a unit can face any of them.

**Terrain cannot rotate.** `scripts/anchor_view.gd:1065` passes yaw `45` unconditionally
when fetching a tile texture, and board rotation is explicitly out of scope (PRD §8: "the
whole projection assumes a fixed camera"). Rendering ~17 new terrain asset types at four
yaws would produce 136 renders, 68 of them per pass, for 102 cells that are never sampled
— the same waste decision 049 found when 156 of 208 atlas cells were unreachable.

At **one** yaw it is 17 types → **34 renders** (albedo + glow), and the atlas stays at
**2 pages**. `pack_atlas.py` lays out 12 columns of 256 px cells
(`tools/blender/pack_atlas.py:51-53`); adding 34 cells to the current 192 keeps both pages
comfortably inside any GL texture limit, and matters because at 16 yaws VRAM is the real
constraint (PRD §3 E6: 226 MB uncompressed RGBA8).

A per-asset yaw list is also what {{ART-01}} needs for the opposite reason — heads at 16
yaws, bases at 4 — so build it as a general mechanism, not a terrain special case.

## Tasks

- [ ] Add a per-asset yaw declaration to `render.py`: a `YAWS_FOR` mapping (or a `yaws`
      key beside each entry in `ASSETS`) defaulting to the existing four. Terrain assets
      declare `(45,)`.
- [ ] Write the per-asset yaw list into the manifest at `render.py:974` — the manifest
      already carries `"yaws"` as a global list; it becomes per sprite. Keep the global
      key for backward compatibility or remove it deliberately, not by omission.
- [ ] Make `pack_atlas.py` read the per-asset yaws rather than assuming four. It already
      iterates `sorted(by_yaw)` from the manifest (`:83`), so this may be free — **verify
      it, do not assume**, and add an assertion that the packed cell count equals the sum
      of declared yaws × passes.
- [ ] Make `sprites.gd` tolerate an asset with a single yaw: `get_tex(name, yaw, pass)`
      currently returns null for an unpacked yaw (`:104-119`). Terrain requests yaw 45 and
      will always hit, but a future caller asking for 135 must get a defined answer —
      fall back to the asset's only yaw, and warn once, rather than drawing nothing.
- [ ] Model the terrain assets in `tools/blender/render.py`'s asset-function style
      (`a_tile_ground()` at `:219` is the pattern): cliff face, cliff corner (inner and
      outer), ramp, deck, pier, abutment, rail — one function each, built around world
      origin, materials via `mat()` so colours are authored in sRGB and linearised.
- [ ] **Every cliff and pier asset is exactly one level tall and stacked at draw time.**
      The measured pivot sits at y=171.5 in a 256 px cell, leaving only **84.5 px** below
      it; a 2-level face needs 96 px and clips. {{TER-07}} already made the procedural
      version stack, so this is a like-for-like swap.
- [ ] **Do not lower `HEIGHT_BIAS`** (`render.py:53`) to buy room. It is shared by all 26
      existing assets and `calibrate()` re-derives the pivot from it — changing it
      re-renders the entire library and moves every sprite.
- [ ] Run the full pipeline in order: `render.py` → `mask_glow.py` → `pack_atlas.py` →
      `--import`. Skipping either of the middle two makes a correct art fix look like it
      did nothing, and skipping the import leaves Godot serving the cached `.ctex`.
- [ ] Swap the procedural draw for texture draws behind a flag or a clean commit, keeping
      the procedural path available for one release as the fallback — it costs nothing and
      it is the only way to A/B the art.
- [ ] Update `tools/check.py`'s `sprite coverage` check so it counts against the declared
      per-asset yaws rather than a hardcoded four; otherwise every terrain asset reads as
      75% missing.

## Acceptance criteria

- `render.py --list` shows the terrain assets; a full render produces exactly **34** new
  PNGs (17 types × 1 yaw × 2 passes), not 136.
- `pack_atlas.py` reports **2** pages after the addition and the gate's `sprite atlas`
  digest matches the renders on disk.
- `sprites.json` carries a per-asset yaw list, and `level_px` from {{TER-03}} still
  validates against `Iso`.
- The rendered board is visually equivalent to the procedural one at the same anchor and
  frame — capture both and diff them by eye; large differences mean the placeholder was
  tuned to something the art does not reproduce.
- No existing asset is re-rendered. The 192 existing renders keep their hashes.
- Glow is masked: no bright rectangles anywhere on the board.

## Verification

```bash
Blender -b --python tools/blender/render.py -- --only cliff_face
.venv/bin/python tools/blender/mask_glow.py
.venv/bin/python tools/blender/pack_atlas.py
Godot --headless --path . --import
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX --shot /tmp/terrain-art.png 300
.venv/bin/python tools/check.py --no-window     # sprite coverage + sprite atlas green
```

Proof: the render log's `RENDERED cliff_face (1 yaws x 2 passes)` line, `pack_atlas`
reporting 2 pages, and the screenshot next to the procedural capture from {{TER-07}}.

## Risks / gotchas

- **A re-render is invisible to the game until you re-import.** Godot's game mode never
  reimports; only the editor does. This has already cost a full round of misdiagnosis.
- **The board draws from the atlas, not the loose PNGs.** Skipping `pack_atlas.py` is a
  second way to make a correct art fix look like it did nothing. The gate's `sprite atlas`
  check hashes every render and fails if the page no longer matches, so the mistake is red
  rather than mysterious.
- **Glow renders opaque and must be masked.** `mask_glow.py` rewrites alpha from luminance
  and must run after every render, or the board fills with bright rectangles.
- **Colours are authored in sRGB and linearised by `mat()`.** Writing a palette as though
  it were a display value renders roughly three times too light — LF-023/020/022. Terrain
  is the largest area on screen, so this error would be the most visible version of it yet.
- The pack is a **fixed 256 px grid and never trims**. One measured pivot serves every
  sprite only because every cell is identical; trimming reintroduces LF-027.
- Set `view_settings.view_transform = 'Standard'` so sprite colour matches in-engine colour.
- Terrain must sit **under** the emplacements and units in the value hierarchy — the same
  rule that turned the anchor ring's structural tones dark (`scripts/board_props.gd:45-50`).
  A bright cliff face is a board that fights the fight for attention.
- Three places independently encode the yaw count today (PRD §3 E6). Find all of them
  while adding the per-asset list, or {{ART-01}} inherits the same hunt.

## Files likely touched

- `tools/blender/render.py`
- `tools/blender/pack_atlas.py`
- `scripts/sprites.gd`, `scripts/anchor_view.gd`
- `assets/renders/` (34 new PNGs), `assets/renders/sprites.json`, `assets/renders/atlas/`
- `tools/check.py`
