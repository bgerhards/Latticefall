id: ART-08
title: Asset production workstream — the fidelity brief, owned by one agent
labels: epic, art, process, design, phase-3
depends: ART-01, CAM-05
milestone: E6 Fidelity
---
## Problem

E6 is the only epic in the programme whose cost is **hand work rather than code**. `ART-01`
establishes that a base/head split at 16 yaws is ~680 renders against 832 flat and 208 today —
cheaper *and* better-looking — but it also establishes where the real cost lands: every model
in `tools/blender/render.py`'s `ASSETS` table (`:701-728`) has to be separated into a base and
a head by hand, and the three environment sets ({{ART-07}}) are a further ~11 assets each. That
is a workstream, not a ticket, and per the owner's instruction **a single dedicated agent owns
it end to end**. This issue is that agent's brief: scope, standards, conventions, and the order
things get made in. It is a tracking issue and it closes when the library is complete.

## Scope

**In:** every asset in `ASSETS` (`render.py:701-728`) separated into base and head where it has
a tracking head; unit sprites at 8 yaws; environment tiles and props for three sets; hazard
markers; the turret animation parts {{ART-06}} needs. **Out:** UI iconography (it comes from
`Ui` and the theme), audio, and anything that changes `Iso.TILE_W` / `TILE_H` — that is a
whole-game layout change and belongs to CAM-05, not here.

## Standards — non-negotiable

- **Every asset is reproducible from a script.** `.blend` files and `render.py` are the source;
  the PNGs in `assets/renders/` are build output that happens to be committed. No hand-painted
  pixels.
- **True 2:1 isometric, elevation 30.0° (`arcsin 0.5`), orthographic.** Not `atan(1/2)` =
  26.5651°, which was wrong in `CLAUDE.md` for six sessions until it was measured
  (decision 017). Orthographic scale for a `W` px render is `W * sqrt(2) / 128`.
- **Colours are authored in sRGB** and linearised by `mat()`. An emission of 0.5 stores as
  188/255. Linear values in the palette render roughly three times too light — LF-023/020/022.
- **Glow is never baked.** Two passes per yaw: albedo with compositing off, glow with
  compositing on through Glare on the Emission pass (decision 007). Godot draws the glow
  additively and modulates it by bus load, so a brownout dims every emissive element. A baked
  glow cannot dim and bleeds past the alpha.
- **`mask_glow.py` after every render.** The compositor writes alpha 1 across the whole frame;
  an unmasked glow fills the board with bright rectangles.
- **The pivot is measured, never assumed.** `calibrate()` writes it to the manifest; a hardcoded
  `CELL//2` drew every sprite above its own tile (LF-027).
- **The pack is a fixed grid and never trims.** One measured pivot serves every sprite only
  because every cell is identical.
- **Order, every time: render → `mask_glow` → `pack_atlas` → `--import` → screenshot.** Skipping
  either of the middle two makes a correct art fix look like it did nothing.
- **Check `docs/NOMENCLATURE.md`'s banned list before naming anything.** It is legally
  load-bearing.

## The base/head split convention

- A **base** is everything that does not track: plinth, legs, cabling, rank pip, footprint. It
  renders at **4 yaws**.
- A **head** is everything that points at a target: barrel, dish, muzzle, sight. It renders at
  **16 yaws**.
- Both render in the **same cell geometry** — same camera, same `HEIGHT_BIAS`, same
  `ORTHO_SCALE` — so they composite at the shared pivot with **zero offset**. Verify with an
  overlay against the un-split render, not by eye.
- The head draws **after** the base at the same depth. Godot's sort is not stable; the
  tiebreaker is explicit ({{ART-01}}).
- Assets with no tracking part (tiles, props, the ring, most units) stay single-part.
- Units render at **8 yaws**, single-part. They turn along a lane; they do not track.

## The 30 px silhouette requirement

LF-104, measured: fitting a 64×64 board needs zoom **0.234×** at 100% interface scale and
**0.117×** at 200%, which puts a 256 px sprite on screen at **30–60 px**. Every asset must be
distinguishable from every other asset in its category **at 30 px, in silhouette, in
greyscale**. That is a modelling constraint, not a texturing one: distinguish by outline and
proportion, never by detail or by colour alone. CAM-05 may set a zoom floor that relaxes this;
until it does, 30 px is the bar.

## Production order

1. **Emplacement heads and bases**, in unlock order (`unlocked_at` in `data/towers.json`), so
   the earliest anchors look finished first and the split convention is proved on
   `pulse_turret` before it is applied ten times.
2. **Units at 8 yaws**, in first-appearance order, screen units before line units — a screen
   unit appears in the largest numbers and is the one the 30 px test bites on.
3. **Environment set for Act I**, procedurally first ({{ART-07}}), committed as renders only
   once the look is settled in play.
4. **Turret animation parts** ({{ART-06}}) — only after the split has shipped and been seen at
   16 yaws.
5. **Act II and Act III environment sets.**
6. **Hazard markers and props.**

## Tasks

- [ ] Produce a per-asset checklist file listing every asset, its part split, its yaw count and
      its status, and keep it current. This is the workstream's ledger.
- [ ] Prove the convention on `pulse_turret` end to end before touching a second asset:
      split, render, mask, pack, import, screenshot, overlay against the un-split render.
- [ ] Add a **silhouette contact sheet** tool: render every asset in the library at 30 px, in
      greyscale, on one page. This is how the 30 px requirement gets checked rather than
      assumed, and it is the single most useful thing this workstream can build for itself.
- [ ] Work the production order above, screenshotting on the board after each asset.
- [ ] Keep the atlas budget in view at every step: {{ART-01}} measures VRAM, {{ART-04}} raises
      the cell. Re-measure after each batch rather than at the end.
- [ ] Update `CLAUDE.md`'s art pipeline facts with anything the installed Blender contradicts.
      Verify against the tool, never from memory.

## Acceptance criteria

- Every asset in `ASSETS` has a recorded part split and a status in the ledger.
- The silhouette contact sheet exists, and every pair of assets within a category is
  distinguishable on it at 30 px in greyscale — reviewed and signed off, not merely generated.
- Base and head composite with zero offset for every split asset, proven by overlay.
- The full library renders, masks, packs and imports clean, and `tools/check.py`'s
  `sprite atlas` check passes.
- Atlas VRAM is measured and recorded after the final batch.
- No asset name appears on `docs/NOMENCLATURE.md`'s banned list.

## Verification

```bash
<blender> -b --python tools/blender/render.py -- --calibrate
<blender> -b --python tools/blender/render.py
.venv/bin/python tools/blender/mask_glow.py
.venv/bin/python tools/blender/pack_atlas.py
<godot> --headless --path . --import
.venv/bin/python tools/blender/contact_sheet.py --px 30 --grey --out /tmp/silhouettes.png
.venv/bin/python tools/shot.py anchor-12 --out /tmp/board.png --extra --facings
.venv/bin/python tools/check.py --no-window
```

Proof to paste: the calibration line, the packer's cell counts and page size, the contact sheet,
a board screenshot, and the gate's `sprite atlas` line.

## Risks / gotchas

- **The art is the cost and it is serial.** Ten heads and ten bases separated by hand is the
  bulk of E6; nothing in the tooling shortens it. Plan it as a workstream with a ledger, not as
  a sprint.
- **Verify against the installed Blender, every time.** 5.2 registers only `BLENDER_EEVEE`,
  `scene.node_tree` does not exist (it is `scene.compositing_node_group` ending in
  `NodeGroupOutput`), Glare settings are input sockets taking title-case strings, and
  `CompositorNodeOutputFile` uses `directory`/`file_name`/`file_output_items`. Do not
  re-derive; do not trust memory.
- **CAM-05 can invalidate the 30 px bar in either direction.** If the owner picks bigger tiles,
  every screen-space calculation, the measured pivot and the a11y baselines move with them.
  Get that decision before the bulk of the modelling.
- PRC-13's incremental asset build is what keeps the iteration loop short; without it, every
  single-asset change costs a full library render.
- A background Blender or Godot left running is a **money bug**, not hygiene — a tracked
  background process re-invokes the model when it exits. Run `tools/reap.py` at every wrap and
  paste what it printed.

## Files likely touched

- `tools/blender/render.py`, `tools/blender/contact_sheet.py` (new)
- `assets/blend/**`, `assets/renders/**`, `assets/renders/sprites.json`
- `docs/` (the asset ledger), `CLAUDE.md`, `docs/NOMENCLATURE.md`
