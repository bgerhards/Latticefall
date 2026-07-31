id: TER-08
title: Validator checks for terrain — ramps, steps, bridge support, and unbuildable ground
labels: tooling, content, phase-2
depends: TER-02
milestone: E4 Terrain
---
## Problem

`tools/validate/validate_data.py` earns its keep on the second layer, not the schema
layer — its own docstring says so (`:5-13`), and the dead-slot check (`:170-202`) is the
proof: anchor-06 was authored with two dead slots and read as a *wave balance* problem
through several sweeps before the layout was measured. Terrain introduces a new family of
data that is perfectly well-formed JSON and completely unplayable: a ramp that connects
nothing, a lane that steps two levels in one tile, a bridge with no abutment, an
emplacement authored on a cliff face.

None of these are visible in a diff and none of them fail a schema. They must fail the
validator, with the tile coordinate in the message.

## Tasks

- [ ] **Region bounds and level range.** Every `rect` lies inside `grid.w × grid.h`; every
      `z` satisfies `0 <= z <= terrain.levels`. Error, with the offending rect echoed.
- [ ] **Regions and heightmap are mutually exclusive.** Declaring both is an error, not a
      precedence rule ({{TER-02}}).
- [ ] **Heightmap dimensions.** `len(heightmap) == grid.h` and every row
      `len(row) == grid.w`. A transposed heightmap on a square board is otherwise
      undetectable — say so in the message.
- [ ] **Ramp connectivity.** A ramp connects exactly two heights differing by exactly 1,
      and the tiles at each end — the neighbours in `-dir` and `+dir` — are actually at
      `from` and `to` in the resolved grid. A ramp declared next to a wall is an error.
- [ ] **Ramp direction is axis-aligned** and one of `+x -x +y -y`; the ramp tile itself is
      inside the grid.
- [ ] **The lane never steps more than one level per tile.** Walk the resolved path tiles
      in order (the validator already expands the path into occupied tiles at `:104-112`)
      and require `abs(h[i+1] - h[i]) <= 1`. A 2-level step is a cliff the units walk up.
      Where the step is 1, require a ramp on that tile or on the tile being stepped onto.
- [ ] **Every bridge has support at each end.** Each deck run's two ends must sit on a
      tile whose terrain level equals the deck level, or carry a declared abutment.
      ({{TER-10}} defines the deck data; this check ships with it.)
- [ ] **Deck clearance is at least 2 levels.** A deck one level above the surface is a
      kerb, not a bridge, and nothing can pass under it. Error below 2.
- [ ] **No emplacement on a ramp or a cliff face.** Slots today are `[x, y]` pairs
      (`anchor.schema.json:78`) checked against the path at `:119`; add the terrain checks
      beside it. After {{PLC-01}} this becomes a footprint-versus-terrain test — write the
      check so the geometry is in one function that can take a radius later.
- [ ] **No emplacement under a deck** unless the deck is declared see-through. An
      emplacement the player cannot see is an emplacement the player will not maintain.
- [ ] **Extend the existing dead-slot check to be LOS-aware if and only if line of sight
      ships.** It currently measures perpendicular distance to the path (`:180-188`);
      with LOS, a slot in range but with no sightline is dead in a way distance cannot
      see. Guard it on the LOS feature being present rather than assuming it —
      see {{TER-12}}, which is optional and owner-gated.
- [ ] Every new message names the tile coordinates, in the style of the existing ones
      (`"slot (3,4) sits on the enemy path"`).
- [ ] Distinguish error from warning deliberately. Unreachable-but-legal geometry is a
      warning; geometry the engines would resolve differently is an error.
- [ ] Add a fixture anchor per check under `data/schema/fixtures/` that trips exactly that
      check, and a test asserting the validator reports it — a validator rule with no
      failing fixture is a rule nobody has seen fail.

## Acceptance criteria

- All 24 shipped anchors still validate with the same warning count as before (they carry
  no terrain, so every new check is a no-op on them).
- Each new check has a fixture that trips it and a message naming the offending tile.
- Corrupting the pilot anchor's ramp to connect two equal heights produces an error, not a
  warning, and names the ramp tile.
- Corrupting the pilot anchor's terrain so the lane steps 2 levels produces an error
  naming both tiles.
- `validate_data.py` exit code is 1 on any of the above.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
# expect: 24 anchors, unchanged warning count, ok

.venv/bin/python tools/validate/validate_data.py --quiet   # on each fixture in turn
# expect: one ERROR line per fixture, naming the tile
```

Paste the full run over the fixture set — one line per rule is the evidence that each rule
has been seen to fire.

## Risks / gotchas

- The validator loads anchors independently of `sim/content.py` (`:250-256`). It must use
  the **shared** `resolve_terrain()` from {{TER-02}}, not a third implementation — three
  parsers is worse than two.
- `check_anchor()` returns early when no emplacement is unlocked (`:136-137`). Terrain
  checks must run **before** that return or they silently never run on such an anchor.
- The dead-slot check already produces both an error and a warning tier
  (`:197-202`); keep terrain in the same tiers so the output stays skimmable.
- `STATIC_TRIGGERS` (`:46`) and the dialog schema are already two allowlists for one fact
  that have drifted once (LF-067). Do not create a third allowlist for ramp directions —
  put the enum in the schema and read it.
- A check that is expensive at 64×64 (e.g. per-tile line-of-sight over every slot) will
  make the fast gate tier slow. Keep the per-anchor cost linear in tiles.

## Files likely touched

- `tools/validate/validate_data.py`
- `data/schema/anchor.schema.json` (enums referenced by the checks)
- `data/schema/fixtures/*.json` (new)
