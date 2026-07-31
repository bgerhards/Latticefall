id: ART-07
title: Three environment sets, each with weather and one hazard
labels: art, content, design, phase-4
depends: ART-01
milestone: E6 Fidelity
---
## Problem

Every anchor draws from the same three tiles — `tile_ground`, `tile_path`, `tile_slot`
(`tools/blender/render.py:701-704`) — plus one ring, so twenty-four levels across three acts
are visually one place. CLAUDE.md's five-line pitch says each act introduces one biome, and the
game currently has none. On a board large enough to be a theatre ({{WAR-06}}, CAM-01), a single
repeated tile is also a legibility problem: with nothing varying, the player has no landmarks
to navigate by when zoomed out.

Weather and a per-set hazard are what make a biome a *place* rather than a palette: something
that moves, and something the player has to account for.

## Tasks

- [ ] Design pass in `docs/DECISIONS.md`, one entry: three sets, one per act, each with a
      palette, a tile family, a prop family, a weather effect and **exactly one hazard**.
      Record the rejected alternative — a hazard per anchor — rejected because it is 24 rules
      to balance and parity-test rather than three.
- [ ] Check `docs/NOMENCLATURE.md`'s banned list before naming any set, prop or hazard.
- [ ] Palettes are authored in **sRGB** and linearised by `mat()`. Writing display values as
      linear renders roughly three times too light — LF-023/020/022, the bug that made the
      board a light grey slab. Add the new palette constants next to the existing ones in
      `render.py:62-70` and never put a linear value there.
- [ ] Tiles render at **one yaw** — the board never rotates (PRD §8) — so a set of ~6 tile
      variants plus ~5 props is ~11 assets × 2 passes × 1 yaw = 22 renders per set, 66 total.
      Confirm that against the atlas budget from {{ART-01}} before modelling.
- [ ] Build the props procedurally first where possible. `scripts/board_props.gd` already has
      the idiom, it costs zero renders and zero atlas growth, and it lets the whole look be
      played and tuned before an asset is committed (the same argument PRD §3 E4 makes for
      cliffs).
- [ ] Schema: an anchor gains an optional `environment` string naming the set, validated against
      a manifest of known sets in `tools/validate/validate_data.py` so a typo is an error rather
      than a silently default board.
- [ ] Weather is **presentation-only**: a parallax particle layer plus a colour grade, driven by
      the set, drawn under or over the board as appropriate, and modulated so it never reduces
      contrast below the WCAG AA floor the interface is measured against (decision 045). Run
      `tools/validate/a11y.py` on a frame of each set — weather over a HUD is the most likely
      way to break contrast in this project.
- [ ] Each hazard **is a rule** and therefore must exist in both engines. Keep them cheap and
      keep them shaped like things that already exist: a zone that slows (reuse the `slow`
      coverage path), a zone that drains the bus (reuse `drains_mw` arithmetic), a zone that
      taxes damage (reuse the shield-leak scale). Authored as anchor data, evaluated in
      `_step()`, mirrored line for line, parity-tested.
- [ ] `tools/validate/validate_data.py`: hazards must be validated for position, must not cover
      the whole board, and must be counted in the saturation reasoning if they touch power.
- [ ] Re-grade every anchor that gains a hazard and sweep it; a hazard is a balance change.
- [ ] Screenshot all three sets at 100% and 200% interface scale, plus one frame with weather at
      maximum intensity, and run the a11y audit on each.
- [ ] Full pipeline in order for every render: render → `mask_glow` → `pack_atlas` → `--import`
      → screenshot.

## Acceptance criteria

- Three environment sets exist, each selectable from anchor data, each visually distinct in a
  side-by-side screenshot.
- Palettes are sRGB in `render.py` and the rendered board matches the intended colour in-engine
  (spot-check one swatch's pixel value against the authored value).
- Each set has exactly one hazard, present in **both** rule files, and parity passes 864/864
  with at least one hazard-carrying anchor in the set.
- An anchor with no `environment` key renders exactly as today and grades byte-identically.
- `tools/validate/a11y.py` reports no new contrast or text-size failures on any set, including
  at maximum weather intensity.
- A typo in `environment` is a validator error, not a default board.

## Verification

```bash
<blender> -b --python tools/blender/render.py
.venv/bin/python tools/blender/mask_glow.py
.venv/bin/python tools/blender/pack_atlas.py
<godot> --headless --path . --import
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
for a in anchor-04 anchor-14 anchor-22; do
  .venv/bin/python tools/shot.py $a --out /tmp/$a.png --ui-scale 2.0 --a11y /tmp/$a.json
  .venv/bin/python tools/validate/a11y.py /tmp/$a.json --shot /tmp/$a.png --all
done
```

Proof to paste: the three screenshots, the three a11y summaries, the empty grade diff for
environment-free anchors, and parity's 864/864.

## Risks / gotchas

- **Weather over the HUD is a contrast risk.** The interface colours were solved against the
  real composited panel (decisions 045/046); a particle layer changes what they are composited
  against.
- **Colours are authored in sRGB and linearised by `mat()`.** An emission of 0.5 is stored as
  188/255. Putting linear values in the palette renders everything roughly three times too
  light — this has happened three times in this project.
- A hazard is a rule. It cannot be GDScript-only for the same reason the pulse cannot
  ({{WAR-10}}): it runs whether or not the player acts, so the anchor would be graded against
  weaker rules than it is played against.
- Terrain and environment both want to merge into the sorted drawable list, which is rebuilt
  4× per frame — PRD §3 E4 measures that naively at 52 ms/frame at 64×64. Static scenery must
  be sorted once at boot and merged in O(n). Coordinate with TER-01 rather than each building
  its own list.
- `mask_glow.py` after every render, `pack_atlas.py` after that, `--import` after that. Skipping
  any of them makes a correct art change look like it did nothing.

## Files likely touched

- `tools/blender/render.py`, `assets/renders/**`, `assets/renders/sprites.json`
- `scripts/board_props.gd`, `scripts/anchor_view.gd`
- `data/schema/anchor.schema.json`, `data/anchors/*.json`
- `sim/content.py`, `sim/engine.py`, `scripts/anchor_sim.gd` (hazards)
- `tools/validate/validate_data.py`
- `docs/DECISIONS.md`, `docs/NOMENCLATURE.md`
