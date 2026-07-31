id: WAR-01
title: Multi-lane paths and multiple spawn points, and the one path-data migration
labels: rules, engine, content, phase-2
blocks: WAR-02, WAR-03, WAR-06, WAR-07, TER-09
milestone: E5 War
---
## Problem

The rules assume exactly one lane, in both implementations, at ten call sites each.
`scripts/anchor_sim.gd:108-112` holds the path as four flat float64 arrays plus a single
`path_length`; `:134-147` builds them from `anchor["path"]`; `:150-171` exposes
`point_at_xy(dist)` / `point_at(dist)` with no notion of which path; `:445-465` moves every
unit against that one polyline and `:457` decides a leak with `dist >= path_length`; `:647`
spawns every unit at `dist: 0.0` with no lane tag; `:656-666` builds a wave queue of
`[time, enemy_id]` pairs. `sim/engine.py` mirrors all of it — `:89`, `:215`, `:225-240`,
`:312-316`, `:376-395` — through `sim/content.py:84-128` and `:169`. The schema forbids
smuggling anything in: `data/schema/anchor.schema.json:65-77` types `path` as pairs of
integers and `:106-134` sets `additionalProperties: false` on a spawn entry, so a `lane` key
is a hard schema error today.

PRD §3 E5 also requires that the path migration happen **once**: elevation (TER-01) wants a
`z` on each waypoint and multi-lane rewrites `path` entirely, and doing those as two schema
breaks means two migrations, two validator passes and two 864-run parity re-runs for one
change of shape. This issue therefore owns the whole path data migration including the `z`
that elevation will later read.

## Tasks

- [ ] Write `docs/DECISIONS.md` entry for the path data shape: `path` → `paths`, a lane is an
      object `{"id": <kebab-case>, "waypoints": [[x, y] | [x, y, z], ...]}`, lanes are
      addressed in the rules by their **integer index** into `paths` (stable, orderable,
      identical in both languages), `id` is for authoring, dialog and the HUD only. Record
      the rejected alternative: lane-by-string-id in the rules, rejected because a string
      comparison as a parity tie-break is a locale and encoding question the project does not
      want in the hot loop.
- [ ] Schema: replace `path` with `paths` in `data/schema/anchor.schema.json` — array,
      `minItems: 1`, each item an object with required `id` (pattern `^[a-z][a-z0-9-]*$`) and
      `waypoints` (`minItems: 2`, each waypoint an array of `minItems: 2, maxItems: 3` of
      integers `minimum: 0`). Third element is the elevation **level**, integer, default 0 —
      never pixels, never a world height (PRD §2.3: `LEVEL_PX = 32`, world height per level
      `0.401872`, both derived downstream). `additionalProperties: false` throughout.
- [ ] Schema: add `lane` to a spawn entry in `anchor.schema.json` — `type: integer`,
      `minimum: 0`, default 0, description naming it as an index into `paths`.
- [ ] Write `tools/migrate_paths.py`: idempotent, rewrites all 24 `data/anchors/anchor-NN.json`
      from `path` to a single-lane `paths` with `id: "main"` and `z` omitted, and adds no
      `lane` key anywhere (0 is the default). Re-running it on migrated data must be a no-op
      and must say so. It is a one-shot tool but it is committed, because the next person to
      hand-author an anchor from an old template will need it.
- [ ] `sim/content.py`: `Anchor.waypoints` → `Anchor.lanes: tuple[Lane, ...]` with a frozen
      `Lane` dataclass carrying `id`, `waypoints`, `seg_len`, `cum_len`, `path_length`.
      `__post_init__` builds per-lane; keep `abs(dx) + abs(dy)` (see risks). `point_at` becomes
      `point_at(lane: int, dist: float)`. Keep an `Anchor.path_length(lane)` accessor; delete
      the bare property rather than leaving it to silently mean lane 0.
- [ ] `sim/content.py:169`: parse `paths` and each spawn's `lane`; `Spawn` gains
      `lane: int = 0`.
- [ ] `sim/engine.py`: `Unit` gains `lane: int = 0` (`:89`). Update `bus_load` (`:215`),
      `_slot_priority` (`:225-240`, sample **every** lane and keep the minimum squared
      distance), the movement loop (`:312-316`, per-unit `self.a.point_at(u.lane, u.dist)` and
      `u.dist >= self.a.path_length(u.lane)`), the targeting scan and the splash loop
      (`:336`, `:349-355`), and the wave queue in `run()` (`:385-395`).
- [ ] **Make the spawn queue tie-break fully deterministic in Python:**
      `queue.sort(key=lambda q: (q[0], q[1], q[2]))` over `(time, lane, enemy_id)`. Two lanes
      spawning at the same instant is the normal case for this feature, and a two-element key
      leaves that order to Python's stable sort against GDScript's `sort_custom` — an
      intermittent parity failure with no reproducer.
- [ ] `scripts/anchor_sim.gd:108-147`: `_wx`/`_wy`/`_seg_len`/`_cum_len`/`path_length` each
      become an `Array` of `PackedFloat64Array` (and `PackedFloat64Array` for the lengths),
      one entry per lane, built in the same loop order as Python. Add `_wz` for the elevation
      level so the migration is complete even though nothing reads it yet.
- [ ] `scripts/anchor_sim.gd:150-171`: `point_at_xy(lane: int, dist: float)` and
      `point_at(lane: int, dist: float)`. Do not add a defaulted `lane = 0` overload — a
      defaulted lane is exactly how one of the ten call sites gets missed and silently reads
      lane 0 forever.
- [ ] `scripts/anchor_sim.gd:445-465`: movement, slow coverage and the leak test per lane.
      `:457` becomes `>= _path_length[lane]`.
- [ ] `scripts/anchor_sim.gd:467-568`: targeting scan and splash loop resolve each unit's
      position through its own lane. **Targeting itself needs no change** — `_can_target`,
      `_covered_by` and the range test all consume a resolved `(x, y)`; the only edit is where
      that pair comes from.
- [ ] `scripts/anchor_sim.gd:645-647`: `spawn(enemy_id: String, lane: int = 0)` writes
      `"lane": lane` into the unit dictionary **at construction**, never afterwards.
- [ ] `scripts/anchor_sim.gd:656-666`: `wave_queue()` emits `[time, lane, enemy_id]` and sorts
      `a[0] != b[0] ? a[0] < b[0] : (a[1] != b[1] ? a[1] < b[1] : a[2] < b[2])`. Byte-for-byte
      the same total order as the Python key above.
- [ ] Update every caller of `wave_queue()` and `spawn()` in `scripts/anchor_view.gd` — the
      wave clock unpacks a 2-tuple today.
- [ ] `scripts/anchor_view.gd`: `drawables()` resolves unit positions per lane
      (`:1012`), `_unit_heading()` likewise, `_draw_board()`'s `_path_tiles()` unions all lanes,
      and `_draw_reach()` / the editor overlay draw every lane.
- [ ] `tools/validate/validate_data.py:89-124`: expand every lane into occupied tiles for the
      slot-on-path check; keep the axis-aligned rejection (`:98-100`) per lane; new errors for
      a spawn whose `lane` is out of range, a duplicate lane `id`, a lane with a zero-length
      segment, and a lane no wave ever spawns into (dead data). Update the dead-slot range
      check (`:174-202`) to take the **minimum** distance over all lanes.
- [ ] `tools/density.py`: report peak units in flight **per lane** as well as per anchor, so
      "four lanes of eight" and "one lane of thirty-two" stop reading identically.
- [ ] `tools/sweep.py` and `sim/run.py`: confirm nothing indexes `anchor.waypoints`; fix what
      does.
- [ ] Add the `--lanes` verification hook to `scripts/main.gd` alongside `--facings`
      (decision 049's precedent): on the frame `--shot` captures, print one line per lane —
      index, id, waypoint count, path length, live unit count. A screenshot shows that units
      are on the board; it cannot show which lane the rules think they are on.
- [ ] Author one genuinely multi-lane anchor as the acceptance case (a new `anchor-25` scratch
      file under `data/anchors/` is not acceptable — use an existing Act II anchor and record
      the before/after grade), or add the lane to an existing anchor behind a sweep.
- [ ] Re-run the full 864-run parity set and record the wall clock in `docs/STATE.md`.
- [ ] Re-grade all 24 anchors and diff against the committed grades; a single-lane anchor must
      grade **byte-identically** to before the migration.
- [ ] Update `CLAUDE.md`'s data layout note and `docs/STATE.md`.

## Acceptance criteria

- Every `data/anchors/anchor-*.json` has `paths`, no file has `path`, and
  `tools/validate/validate_data.py` exits 0.
- With every anchor still single-lane, `.venv/bin/python -m sim.run --jobs 8` produces output
  **identical** to the pre-migration run (diff is empty). This is the whole safety argument
  for the migration: shape changed, numbers did not.
- `tools/test_parity.py` passes all 864 runs, including on an anchor with ≥2 lanes and two
  spawn entries whose first spawn times are equal.
- A wave that spawns into lane 1 puts units on lane 1: `--lanes` prints a non-zero live count
  for lane 1 on the captured frame.
- `point_at` / `point_at_xy` have no defaulted `lane` parameter in either language.
- Waypoints accept an optional third integer element and it round-trips through both parsers
  without being read by any rule.

## Verification

```bash
.venv/bin/python tools/migrate_paths.py            # then re-run: must report "no changes"
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt                # empty, single-lane anchors
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/density.py
.venv/bin/python tools/shot.py anchor-06 --out /tmp/lanes.png --extra --lanes
```

Proof to paste: the empty `diff`, `test_parity.py`'s "864/864" line, and the `--lanes` block
showing more than one lane with a non-zero live count.

## Risks / gotchas

- **Paths must stay axis-aligned.** Both engines compute a segment as `abs(dx) + abs(dy)`
  (`anchor_sim.gd:142-147`, `sim/content.py:104`) and `validate_data.py:98-100` hard-rejects a
  diagonal. Euclidean lengths are *safe* now (PRD §2.1 — `sqrt` matched 100,000/100,000 on
  CPython, Linux Godot and Windows Godot) but switching changes every `path_length`, every
  unit `dist` and every wave's pacing, and re-grades all 24 anchors. That is WAR-12 and it is
  explicitly optional. **Do not sneak it in here.**
- **The tie-break is the whole parity risk.** `(time, enemy_id)` was already tie-broken for a
  reason (`sim/engine.py:166-171` records the last time insertion order diverged). Two lanes
  make simultaneous spawns routine rather than rare.
- **A unit dictionary must never grow a key after construction.** LF-055: Godot 4.7 compares
  `Dictionary` by value, `sim/engine.py` used `u is target`. `lane` is safe **only** because
  both engines write it at spawn and never again, and both carry it. Do not let the view layer
  annotate a unit with a lane-derived cache.
- Ten GDScript call sites, ten Python ones, and a missed one reads lane 0 and looks almost
  right. Removing the no-arg form of `point_at` turns every miss into a parse error, which is
  the 1.59 s GDScript parse check (PRD §3 E1), not a nine-minute parity run.
- `_slot_priority()` sampling every lane multiplies its cost by lane count; it runs once per
  build, not per tick, so this is fine — but do not copy the pattern into `_step()`.
- The HUD threat panel and the briefs speak about "the lane" in the singular. Copy is out of
  scope here; open a backlog item rather than rewriting 24 dialog files in this change.

## Files likely touched

- `data/schema/anchor.schema.json`
- `data/anchors/anchor-01.json` … `data/anchors/anchor-24.json`
- `tools/migrate_paths.py` (new)
- `sim/content.py`, `sim/engine.py`
- `scripts/anchor_sim.gd`, `scripts/anchor_view.gd`, `scripts/main.gd`
- `tools/validate/validate_data.py`, `tools/density.py`, `tools/sweep.py`, `sim/run.py`
- `docs/DECISIONS.md`, `docs/STATE.md`, `CLAUDE.md`
