id: TER-02
title: Terrain data shape — regions, ramps, and one shared resolution algorithm
labels: content, rules, tooling, phase-2
depends: PRC-10
blocks: TER-01, TER-08
milestone: E4 Terrain
---
## Problem

`data/schema/anchor.schema.json:17` sets `"additionalProperties": false`, so an anchor
cannot carry a single byte of terrain today — adding it is a deliberate, reviewed schema
change, not an accident of authoring. The shape must be **regions + ramps**, not a dense
heightmap: regions are what a generator emits and what a human can read in a diff, and a
64×64 dense array is 4,096 numbers nobody will ever review. The escape hatch stays,
because a generator that has already computed a heightfield should not be forced to
factor it back into rectangles.

**The parity-critical detail:** `sim/content.py:160` (`load_anchor`) and
`scripts/content.gd:43` (`anchor`) are two independent parsers of the same file, and the
PRD lists "two independent anchor parsers disagreeing on region→height resolution" as risk
#10. A one-tile disagreement between them is invisible to schema validation, invisible in
a screenshot, and findable only by the 9-minute 864-run parity gate — after which the
symptom is "some unit leaked in one engine and not the other" with no pointer to terrain
at all. So the resolution algorithm is specified here, in prose, with a paint order, and
implemented twice against the same fixture.

## The shape

```json
"terrain": {
  "levels": 3,
  "regions": [
    {"rect": [4, 2, 6, 5], "z": 1},
    {"rect": [6, 3, 2, 2], "z": 2}
  ],
  "ramps": [
    {"tile": [3, 4], "from": 0, "to": 1, "dir": "+x"}
  ],
  "heightmap": [[0, 0, 1], [0, 1, 1]]
}
```

- `levels` — the maximum level index the board uses; every `z` must satisfy
  `0 <= z <= levels`.
- `rect` is `[x, y, w, h]`, integers, half-open in `w`/`h`.
- **Paint order is later-region-wins.** The base is level 0 everywhere; regions are
  applied in array order; a later region completely overwrites the level of any tile it
  covers, including lowering it. This is the single sentence both parsers must implement
  identically and it goes in the schema `description`, both parser docstrings and
  `docs/DECISIONS.md`.
- `heightmap` is the escape hatch: a dense `h × w` array of integer levels, row-major,
  `heightmap[y][x]`. **If present it replaces `regions` entirely** — it is not composited
  on top, because "composited with what precedence" is another chance for the two parsers
  to disagree. Declaring both is a validation error ({{TER-08}}).
- `ramps` are declared, not inferred. A ramp occupies exactly one tile and connects two
  heights differing by exactly 1; `dir` is one of `+x -x +y -y` and names the direction of
  *ascent*.

## Tasks

- [ ] Add the `terrain` object to `data/schema/anchor.schema.json` with
      `"additionalProperties": false` on every nested object, matching house style.
      `terrain` itself stays optional so all 24 existing anchors validate unchanged.
- [ ] Write the resolution algorithm once as prose in the schema `description`, naming
      paint order and the heightmap-replaces-regions rule.
- [ ] Implement `resolve_terrain(doc) -> tuple[tuple[int, ...], ...]` in `sim/content.py`,
      returning a dense row-major level grid. Absent `terrain` returns an all-zero grid of
      the anchor's `grid.w × grid.h`, so every downstream consumer has one code path.
- [ ] Store it on `Anchor` as a new frozen field `levels: tuple[tuple[int, ...], ...]` and
      a `height_at(tx, ty) -> int` accessor with edge clamping.
- [ ] Implement the byte-for-byte equivalent in `scripts/content.gd` as
      `resolve_terrain(doc) -> Array` returning `PackedInt32Array` rows, with the *same*
      docstring naming the paint order, and a cross-reference comment in each file naming
      the other. Two implementations, one spec, both pointing at each other.
- [ ] Add a fixture `data/schema/fixtures/terrain-resolution.json`: a deliberately nasty
      case — overlapping regions in both orders, a region that lowers, a region clipped by
      the board edge, a ramp on the boundary — plus its expected dense grid.
- [ ] Add a gate check `terrain parsers agree` to `tools/check.py` that runs the fixture
      through the Python resolver and through a headless Godot one-shot, and diffs the
      dense grids tile by tile. This is cheap (one small board), runs in the fast tier,
      and is the *only* thing standing between a one-tile drift and the 9-minute gate.
- [ ] Extend `tools/density.py` to report terrain presence per anchor (levels used, %
      of board raised) so a "terrain that means something" claim is measurable rather than
      asserted.
- [ ] Author terrain on exactly **one** anchor as the pilot. Do not touch the other 23 in
      this issue — a 24-anchor terrain pass is a content issue, not a schema issue.
- [ ] Add a decision entry: regions+ramps over dense heightmap, paint order, and the
      rejected alternatives (dense-only: unreviewable; sparse per-tile list: same
      ambiguity with more syntax; inferred ramps: unfalsifiable).

## Acceptance criteria

- All 24 existing anchors validate against the extended schema **with no edit to any of
  them** — `terrain` is optional and absent means flat.
- `resolve_terrain` in Python and in GDScript produce identical dense grids for the
  fixture, tile for tile, asserted by a gate check that fails red on a single-tile
  difference.
- An anchor with `regions` and an anchor with the equivalent `heightmap` resolve to the
  same grid.
- Declaring both `regions` and `heightmap` is a validation **error**, not a silent
  precedence rule.
- `sim.run` grades all 24 anchors to the same numbers as before this issue: terrain data
  exists but nothing in the rules reads it yet.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8              # all 24, grades unchanged
.venv/bin/python tools/check.py --no-window       # new 'terrain parsers agree' check green
.venv/bin/python tools/density.py                 # terrain column present for the pilot
```

The proof that matters is the parser-agreement check printing `ok` and then, with one
digit of the fixture's expected grid deliberately corrupted, printing the failing tile
coordinate. Show both.

## Risks / gotchas

- **`"additionalProperties": false` at line 17.** Every nested object needs it too, or
  a typo'd `regoins` key is accepted in silence — the same class of failure as the
  `font_color_disabled` theme override in `menu.gd`.
- **Row-major vs column-major.** `heightmap[y][x]`, stated once, tested by a fixture whose
  width and height differ. A square fixture would pass either way.
- **`Array` vs `PackedInt32Array` in GDScript.** Use packed rows; a nested `Array` of
  `Array` of `int` at 64×64 is 4,096 Variants and this grid is read in the draw path.
- **Do not let terrain reach `sim/engine.py` or `scripts/anchor_sim.gd` in this issue.**
  The moment it does, the cheap slice stops being parity-free. Range stays 2-D ({{TER-13}})
  and line of sight is separate and owner-gated ({{TER-12}}).
- Terrain levels are integers everywhere. A float level would put a rounding boundary into
  a data path both engines parse — the anchor-14 six-leak shape.
- {{WAR-01}} rewrites `path` entirely and elevation wants a `z` on waypoints. **Do not
  touch `path` here.** That migration happens once, inside {{WAR-01}}; {{TER-09}} consumes it.

## Files likely touched

- `data/schema/anchor.schema.json`
- `data/schema/fixtures/terrain-resolution.json` (new)
- `sim/content.py`
- `scripts/content.gd`
- `tools/check.py`, `tools/density.py`
- `data/anchors/anchor-XX.json` (one pilot anchor only)
- `docs/DECISIONS.md`
