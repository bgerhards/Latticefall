id: PLC-02
title: Placement validity as a rule — bounds, lane standoff, and overlap, in both engines
labels: phase-2, rules, engine
depends: PLC-01
blocks: PLC-04, PLC-06, PLC-07
milestone: E3 Placement
---
## Problem

{{PLC-01}} leaves a stub that accepts only the anchor's old slot positions. This issue replaces
it with the real thing. *Where you are allowed to build* is a rules question — it decides which
builds a grade describes — so it must exist in **both** implementations and be diffed by the
864-run parity gate, exactly like every other rule (PRD §1, "the one rule that outranks the
pillars").

All three tests are expressible with the safe operation set and none needs a square root if the
comparison is made squared (PRD §2.1, §E3):

- **In bounds** — the footprint is fully inside the board.
- **Off the lane** — squared point-to-segment distance from the path polyline is at least
  `(lane_half_width + footprint_radius)²`. Point-to-segment needs only `+ − × ÷ min max`: the
  parameter is `t = clamp(dot(p−a, b−a) / dot(b−a, b−a), 0, 1)`, and the distance is compared
  squared.
- **No overlap** — `dx*dx + dy*dy >= (r_i + r_j) * (r_i + r_j)`.

## Tasks

- [ ] Pin the coordinate convention in one sentence, in both files' docstrings, and never
      re-derive it: positions are **tile-centre coordinates**, the board interior is
      `[-0.5, w-0.5] x [-0.5, h-0.5]`, and a footprint of radius `r` is in bounds when
      `x - r >= -0.5` and `x + r <= w - 0.5` (likewise y). Getting this off by half a tile is
      invisible until a level's edge column stops being buildable.
- [ ] Add `lane_half_width` (number, default 0.5) to `data/schema/anchor.schema.json` and to the
      anchor loader (`sim/content.py`). Author it per anchor so a level can have a wide road.
- [ ] Write `_is_placeable(tower_id, x, y) -> bool` in `scripts/anchor_sim.gd` and the identical
      function in `sim/engine.py`, called from `build_at()` and `_try_build()` respectively.
      One function, three tests, in a fixed order: bounds, lane, overlap. Return the *reason* as
      well as the boolean — {{PLC-07}} needs to tell the player which of the three failed.
- [ ] **Write the arithmetic in the same term order in both files.** Floating-point addition is
      not associative: `dx*dx + dy*dy` and `dy*dy + dx*dx` can differ in the last bit, and on an
      exact boundary that is the difference between legal and illegal. This is the whole reason
      the parity gate exists; do not let the two files drift into "equivalent" expressions.
- [ ] Use `>=` for the overlap test and `>=` for the lane test consistently, and say in the
      docstring that a *touching* footprint is legal. An exact-boundary decision that differs
      between the engines is a parity failure that reproduces one run in a thousand.
- [ ] Sample the path as **segments**, not as the `steps`-resolution point sampling
      `sim/engine.py:232-239` uses for `_slot_priority()`. That sampling is a grader heuristic
      and is allowed to be approximate; a rule must not be. Iterate `waypoints` pairwise.
- [ ] Mirror into `scripts/test/parity.gd` — it carries its own copy of the build loop at
      `:172-217` and will otherwise place emplacements the engine would refuse.
- [ ] Replace `validate_data.py`'s slot checks with area checks: the "fewer than 3 build slots"
      warning becomes a minimum buildable-area warning, and the "slot further from the path than
      any weapon's range is dead" check becomes "no buildable position covers waypoint N",
      which is a stronger statement and catches a lane the player cannot defend at all.
- [ ] Now retire `slots`: drop it from `data/schema/anchor.schema.json` (`:14`, `:78-90`), from
      all 24 anchor files, from `sim/content.py:89` and `:170`. One commit, one migration script,
      idempotent.
- [ ] Add a unit test in `sim/` covering: a footprint exactly on the board edge; a position
      exactly `lane_half_width + r` from a segment; a position exactly `r_i + r_j` from an
      existing emplacement; a position past the end of the polyline (the point-to-segment clamp);
      and a degenerate zero-length segment (duplicate waypoints — check whether any anchor has
      one before assuming not).
- [ ] Run the 864-run parity, and re-grade all 24 anchors. Expect grades to **change** here for
      the first time, because the candidate set changes — record the before/after table.
- [ ] `docs/DECISIONS.md` entry: placement validity is a rule, the three tests, the coordinate
      convention, and why the sampling is exact rather than heuristic.

## Acceptance criteria

- `_is_placeable` in both engines returns identical booleans for a 10,000-position sweep across
  every anchor, compared as raw values — write a throwaway harness that dumps both and diffs.
- `tools/test_parity.py` reports 864 runs identical.
- No `Vector2` and none of `atan2 sin cos tan pow log exp` appears in either rule file.
- No `sqrt` is called in the validity path (everything compared squared) — assert by grep.
- A tower placed exactly `lane_half_width + footprint_radius` from the lane is **legal** in both
  engines; one epsilon closer is illegal in both.
- All 24 anchors validate with `slots` removed, and `grep -rn '"slots"' data/` returns nothing.
- The re-graded table is recorded, with every anchor still `ok`.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8                       # re-grade; expect movement
.venv/bin/python tools/placement_probe.py --dump /tmp/py.txt        # python side
/path/to/godot --headless --path . --script res://scripts/test/placement_probe.gd \
  -- --dump /tmp/gd.txt
diff /tmp/py.txt /tmp/gd.txt                               # must be empty
.venv/bin/python tools/check.py
.venv/bin/python tools/reap.py
```

Proof is the empty `diff` over the 10,000-position sweep plus `864 runs identical`.

## Risks / gotchas

- **The exact boundary is where float32 and float64 disagree 10.2% of the time** (PRD §2.1).
  Both engines are float64 here, so the exposure is the *expression*, not the type — but the
  moment anyone writes `Vector2(x,y).distance_to(...)` in `anchor_view.gd` and reuses it as
  truth, the boundary moves. The rules own legality; the view asks the rules.
- **Term order matters.** Write the same expression, character for character where the languages
  allow it.
- **`sqrt` is now permitted** (decision 030 superseded by {{PLC-01}}) but is not needed here.
  Not needing it is better than being allowed it.
- **A degenerate segment divides by zero** in the point-to-segment parameter. Guard it, and check
  whether any of the 24 anchors actually has a repeated waypoint before deciding what the guard
  does.
- **Grades will move.** That is expected and is not a regression, but it must be recorded, and
  `tools/say_capacity.py` must be re-run if any capacity is retuned afterwards — sixteen briefs
  drifted once already because prose quotes its own numbers.
- **Parity is ~9 minutes and 83.6% of gate time.** Get all three files right first.

## Files likely touched

- `scripts/anchor_sim.gd`, `sim/engine.py`, `scripts/test/parity.gd`
- `sim/content.py`, `data/schema/anchor.schema.json`, `data/anchors/*.json`
- `tools/validate/validate_data.py`
- `tools/placement_probe.py`, `scripts/test/placement_probe.gd` (new, differential harness)
- `docs/DECISIONS.md`, `docs/STATE.md`
