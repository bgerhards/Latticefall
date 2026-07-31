id: TER-07
title: Cliffs and ramps drawn procedurally first — zero renders, zero atlas growth
labels: art, engine, phase-2
depends: TER-01
blocks: TER-14
milestone: E4 Terrain
---
## Problem

Elevation with no cliff faces reads as floating tiles: the surface moves up 32 px and
nothing fills the gap where the ground used to be. The obvious response is to render cliff
assets, but that commits the atlas, the manifest and a `.blend` before anyone has played
a raised board and decided whether **32 px is even the right step**. The PRD is explicit
(§3 E4): build cliffs procedurally first — the whole feature can be played and tuned
before an asset is committed.

`scripts/board_props.gd` already has the exact idiom and has already solved the two hard
parts. `_pillar_cap()` (`:201`) lifts an `Iso.diamond` by a height; `_pillar_faces()`
(`:205`) extrudes it, and its comment names precisely which two faces a 30° elevation
camera looks down onto rather than edge-on — **left→bottom and bottom→right** — which is
the same pair `_build_edge()` (`:190`) extrudes for the platform rim. That comment is the
answer to "which faces do I draw", already measured and already in the repo.

## Tasks

- [ ] Add a `TerrainDraw` section to `AnchorView` (or a small helper preloaded like
      `IsoScript`) that, for a tile at level `h` whose neighbour in a given direction is
      lower, emits the extruded face quads for the height difference.
- [ ] **Stack one level at a time.** A 2-level drop is two stacked one-level faces, not
      one 64 px quad — this matches the hard art constraint that {{TER-14}}'s rendered
      assets will have to obey (measured pivot at y=171.5 in a 256 px cell leaves only
      84.5 px below it, and a 2-level face needs 96 px and clips). Making the procedural
      version stack too means the eventual asset swap is a like-for-like replacement
      rather than a re-think.
- [ ] Draw only the two camera-facing directions per the `_pillar_faces()` comment. The
      other two are never visible and drawing them is wasted fill.
- [ ] Shade the two faces differently, as `_draw_platform_edge()` (`:370`) and
      `_draw_bindstone()` (`:393`) already do: the same colour at `×1.3` and `×1.25`
      respectively for the right-hand face. **Colours come from the existing palette
      constants** (`STONE_DARK`, `ALLOY_DARK`, `ALLOY_MID`) or new ones authored the same
      way — never picked by eye, and never brighter than the emplacements, which would
      invert the value hierarchy the brief sets (`board_props.gd:45-50`).
- [ ] Draw a 1 px rim highlight along the top edge of each face, as `_edge_rim` does
      (`:198`, `:374`), so a cliff reads as an edge and not as a flat colour block.
- [ ] Draw ramps procedurally: a quad from the low tile's surface to the high tile's
      surface in the declared `dir`, with side triangles filling the wedge.
- [ ] Make the platform edge (`_build_edge()`, `:190`) elevation-aware, or the board's
      outer rim floats where the boundary tiles are raised. This is the one place
      `board_props.gd` itself must change.
- [ ] Make `_build_ground_sigils()` (`:327`) and `_build_ring_ground_ticks()` (`:286`)
      apply the height offset, or the sigil field lies flat through a raised plateau. Both
      already project through `Iso`, so this is one added term each.
- [ ] Feed the cliff faces into the static terrain drawable list from {{TER-05}} as their
      own entries with the correct depth and class epsilon ({{TER-04}}) — a face belongs to
      its tile and must draw under anything standing on that tile.
- [ ] Play a terrain board and record, in `docs/DECISIONS.md`, whether 32 px is the right
      step. This is the deliverable that justifies doing it procedurally; if the answer is
      24 or 40, changing it here is a constant, and after {{TER-14}} it is a re-render of
      the whole terrain library.

## Acceptance criteria

- A raised region draws with visible side faces on the two camera-facing directions and
  no faces on the other two.
- A 3-level plateau shows three stacked 32 px faces, not one 96 px face.
- No new file appears under `assets/renders/`. `pack_atlas.py --check` reports the same
  cell counts and the gate's `sprite atlas` digest is unchanged.
- The platform edge and the ground sigil field follow the terrain rather than sitting at
  level 0 under it.
- Cliff faces never draw over a unit standing on the tile above them.
- Every colour used traces to a named palette constant; no literal `Color(...)` at a draw
  site.
- A written answer, in a decision entry, to "is 32 px the right step".

## Verification

```bash
.venv/bin/python tools/blender/pack_atlas.py --check   # unchanged cell counts
.venv/bin/python tools/check.py --no-window            # sprite atlas digest unchanged
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX --shot /tmp/cliff.png 300
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX --ui-scale 2.0 \
      --shot /tmp/cliff2x.png 300 --a11y /tmp/cliff2x.json
```

The a11y report must be taken on the **same frame** as its shot — the analyser samples the
background out of that PNG. Proof is the screenshot showing stacked faces and the atlas
check showing zero growth.

## Risks / gotchas

- **`board_props.gd` draws at `DRAW_Z = 1`, above the board and entities** (`:19`), for the
  reason its docstring gives at length: at −1 anything overlapping a tile's footprint was
  painted over and never appeared. Cliff faces must go in the **board's** draw list, not
  in props, or they will paint over the units standing on them.
- `_pillar_faces()` takes the cap in `Iso.diamond` order `[top, right, bottom, left]`
  (`:207`). Passing a differently-ordered polygon produces faces that look almost right
  from one angle and inside-out from another.
- Getting the visible pair wrong is the classic version of this bug and it is *subtle* at
  30° — read the comment at `:208-212` rather than re-deriving it.
- Overdraw: at 64×64 with a busy heightfield this is thousands of `draw_colored_polygon`
  calls. They belong in the static list built once ({{TER-05}}), not recomputed per frame.
- Do not add a gradient or a texture to a face "for now". A procedural placeholder that
  looks finished is how a placeholder ships.
- The brownout dim (`_draw_entities()`, `:1149`) modulates entities. Decide deliberately
  whether cliff faces dim with it — they are unlit stone and probably should not, but the
  decision should be written down rather than fall out of where the code was pasted.

## Files likely touched

- `scripts/anchor_view.gd`
- `scripts/board_props.gd`
- `docs/DECISIONS.md`
