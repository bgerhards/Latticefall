id: PLC-01
title: Free placement core — a continuous float64 position replaces the slot
labels: phase-2, rules, engine
depends: CAM-01, PLC-05
blocks: PLC-02, PLC-03, PLC-04, PLC-06, PLC-07
milestone: E3 Placement
---
## Problem

Pillar 2 of the PRD: *"Build where you want. No slots. Fixed slots are the single most limiting
thing in the current design."* An anchor today ships a fixed list of 8-12 integer positions, and
the entire build path is built on that list being the universe of legal places:

- `scripts/anchor_sim.gd:67-68` — `placed: Array[Dictionary]  # {tower, slot:Vector2i, ...}` and
  `free_slots: Array[Vector2i]`
- `:130-132` — `free_slots` seeded from `anchor["slots"]` in `setup()`
- `:294-306` — `build_at(tower_id, slot: Vector2i)`, gated on `free_slots.has(slot)`
- `:327` — `sell()` returns the slot to `free_slots`
- `:383-384` — `_covered_by()` reads `p["slot"].x/.y`
- `:481-482` — the targeting loop reads `sx/sy` from `p["slot"]`
- `sim/engine.py:77-82` — `Placed.slot: tuple[int, int]`
- `:177` — `self.free_slots = list(anchor.slots)`
- `:225-240` / `:242-264` — `_slot_priority()` and `_try_build()`
- `:285`, `:337`, `:422` — coverage, targeting, and the parity outcome signature
- `sim/content.py:89`, `:170` — the `Anchor.slots` field and its loader
- `data/schema/anchor.schema.json:14`, `:78-90` — `slots` is required, integers, `minimum: 0`

The blocker that used to sit in front of this is gone. Decision 030 said the rules avoid square
roots because *"Godot's `Vector2` is float32 and `distance_to` is not correctly rounded"* — that
bundled two causes and only one survives a probe (PRD §2.1, LF-106). `sqrt` is correctly rounded
by IEEE-754 §5.4.1, both runtimes issue `SQRTSD`, and it matched **100,000 / 100,000** on
CPython, Linux Godot and Windows Godot. The culprit was `distance_to`, a float32 helper: on an
exact integer radius, float32 and float64 disagree on `<= r` **10.2%** of the time. An
end-to-end off-grid loop — continuous float64 positions, `sqrt`-normalised directions,
remainder-carrying advance, 4,000 ticks x 64 units — came out **bit-identical on all three
runtimes**. Off-grid geometry is safe.

## Scope of this issue

Representation only, in one shippable step. `build_at()` takes a continuous position and
`placed` carries `x`/`y` floats, but the legality test is a **stub that accepts only the anchor's
existing slot positions**, so parity is byte-identical and all 24 grades are unchanged.
{{PLC-02}} replaces the stub with the real predicate. Doing it in one commit means changing the
representation and the rules simultaneously and having no way to tell which one broke parity.

## Tasks

- [ ] Add `footprint_radius` (number, tiles) to `data/schema/towers.schema.json` and to all nine
      rows of `data/towers.json`. `additionalProperties: false` is set, so the schema must move
      first or every row fails validation. Author the values from the sprite footprints, not by
      eye — an emplacement occupies roughly one tile today, so 0.45-0.5 is the starting band.
- [ ] Change `placed` records from `{"slot": Vector2i}` to `{"x": float, "y": float}` in
      `anchor_sim.gd`, and `Placed.slot: tuple[int,int]` to `x: float, y: float` in
      `sim/engine.py:77-82`. **Never a `Vector2`** — it is float32 and banned in the rules
      (PRD §2.1, invariant 2).
- [ ] Change `build_at(tower_id: String, slot: Vector2i)` to
      `build_at(tower_id: String, x: float, y: float)` and the `built` signal at
      `anchor_sim.gd:45` with it. Update every caller in `anchor_view.gd` and `main.gd`.
- [ ] Replace `free_slots` with the placed list plus a legality predicate. `sell()`
      (`anchor_sim.gd:327`) simply removes the record; there is no slot to hand back. `setup()`
      (`:130-132`) stops seeding a free list.
- [ ] Stub the predicate for this issue: `_is_placeable()` returns true only for positions in the
      anchor's `slots` array, so behaviour is unchanged. Mark it with a `TODO(PLC-02)` and a
      docstring saying it is deliberately temporary.
- [ ] Update the two geometry sites in each engine to read the float position:
      `_covered_by()` (`anchor_sim.gd:383-384`, `sim/engine.py:285`) and the targeting loop
      (`anchor_sim.gd:481-482`, `sim/engine.py:337`). They already compare squared distances in
      float64; only the source of `sx`/`sy` changes.
- [ ] **Fix the parity outcome signature.** `sim/engine.py:422` emits
      `f"{p.tower.id}@{p.slot[0]},{p.slot[1]}"` and `scripts/test/parity.gd:155` emits
      `"%s@%d,%d"`. With floats, `%d` truncates and the two formatters may round halfway cases
      differently. Emit a fixed-precision form that is exact on both sides — the positions this
      issue produces are integers and {{PLC-04}}'s lattice is a binary fraction, so `%.4f` /
      `f"{x:.4f}"` round-trips exactly. Assert that choice in a comment; a formatting difference
      here is an 864-run failure that looks like a rules divergence.
- [ ] Retire `slots` from `data/schema/anchor.schema.json` (`:14` required list, `:78-90`
      definition) and from all 24 anchor files, **after** {{PLC-02}} — this issue keeps them,
      because the stub reads them. Add the migration as a checklist item on {{PLC-02}} and note
      it here so it is not lost.
- [ ] Update `sim/content.py:89` and `:170` for whatever replaces `slots`, keeping the loader's
      explicit float conversion idiom (`float(x)`, as `waypoints` already does at `:169`).
- [ ] Update `validate_data.py`'s remaining slot checks — the `< 3 build slots` warning at
      `:167-168` and the "slot further from the path than any weapon's range is dead" check
      below it. Both become statements about buildable area under {{PLC-02}}.
- [ ] Run the full 864-run parity and confirm **identical**, then re-grade all 24 anchors and
      confirm every verdict and every distinct-build count is unchanged.
- [ ] `docs/DECISIONS.md`: an entry superseding decision 030 with the measured safe set
      (`+ − × ÷ sqrt fmod floor min max`, comparisons) and banned set (`atan2 sin cos tan pow log
      exp`, anything through `Vector2`), citing the probe. Then a second entry for free placement
      itself. Append-only; do not edit 030.
- [ ] Update `CLAUDE.md` (the `Vector2`/`sqrt` line under traps is currently wrong) and
      `docs/STATE.md`.

## Acceptance criteria

- `tools/test_parity.py` reports **864 runs identical** with the stub predicate in place.
- `.venv/bin/python -m sim.run --jobs 8` reproduces all 24 anchor verdicts, waves cleared,
  lives left, leaks, spend and `built` lists exactly as recorded in `docs/STATE.md`.
- `grep -n 'Vector2' scripts/anchor_sim.gd` returns nothing in the rules path; `grep -nE
  'atan2|\bsin\(|\bcos\(|\btan\(|\bpow\(|\blog\(|\bexp\(' scripts/anchor_sim.gd sim/engine.py`
  returns nothing.
- `build_at("pulse-turret", 4.5, 7.25)` is rejected by the stub and accepted by {{PLC-02}}'s
  predicate — the seam is a single function.
- `sell()` on any emplacement leaves `placed` one shorter and no free-list state anywhere.
- The game plays identically: a shot of anchor-24 mid-wave is pixel-identical to before.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8
.venv/bin/python tools/check.py                  # rules parity is the one that matters here
.venv/bin/python tools/reap.py
```

Proof is `rules parity  864 runs identical (gdscript vs python)` on the gate line, plus the
anchor-grade table diffing clean against `docs/STATE.md`.

## Risks / gotchas

- **There is no pathfinding anywhere in this repository.** Units advance a scalar `dist` along a
  fixed polyline — `sim/engine.py:19-21` says so explicitly under "what is not modelled". Free
  placement therefore introduces **zero lane-blocking risk**, and nobody should build a navmesh,
  a flow field or a blocked-lane fallback for it. Stated here so the question is closed.
- **The rules exist twice and are diffed over 864 runs on every commit.** Anything here moves in
  `anchor_sim.gd` *and* `sim/engine.py` *and* `scripts/test/parity.gd`, or it does not ship.
  Parity is 83.6% of gate time (~9 minutes) — get all three right before running it.
- **`Vector2` is float32.** On an exact radius it disagrees with float64 on `<= r` 10.2% of the
  time. It is fine in `anchor_view.gd` (presentation); it is banned in the two rule files.
- **`_face()` annotates the `placed` dictionary and `anchor_sim.gd:541-546` says that is safe
  "because they are only ever compared by `slot`".** That comment stops being true here.
  Coordinate with {{CAM-07}}, which touches the same annotation.
- **The parity gate never tests the build the owner plays** (LF-105, PRD risk #2): all 864 runs
  compare CPython against *Linux* Godot. The rules use none of the divergent operations, so
  cross-platform parity holds by accident. Keep it that way — no `atan2`, ever ({{PLC-03}}).
- **`towers.schema.json` sets `additionalProperties: false`.** Adding a field to `towers.json`
  before the schema is a red `game data` check with a message that names the row, not the schema.
- **{{PLC-05}} must land first.** Removing slots removes the board-saturation denominator, and
  the failure it guards has already happened once.

## Files likely touched

- `scripts/anchor_sim.gd`, `sim/engine.py`, `scripts/test/parity.gd`
- `sim/content.py`, `data/schema/towers.schema.json`, `data/towers.json`
- `data/schema/anchor.schema.json` (with {{PLC-02}})
- `scripts/anchor_view.gd`, `scripts/main.gd` (call sites)
- `tools/validate/validate_data.py`
- `docs/DECISIONS.md`, `CLAUDE.md`, `docs/STATE.md`
