id: WAR-07
title: Unit roles and formations — mass that has a shape
labels: content, rules, design, phase-3
depends: WAR-01, WAR-06
milestone: E5 War
---
## Problem

A wave today is a flat list of `{enemy, count, interval, delay}` spawn entries
(`data/schema/anchor.schema.json:106-134`), so 300 units is 300 identical arrivals down one
pipe rather than a formation. `tools/density.py` already makes the point that a wave's unit
count is not its screen presence — a Column at 0.5 tiles/sec holds the board four times as
long as a Shard — and multiplying the count without giving mass a shape produces a longer
queue, not a battle. Roles are also the only honest way to spend the unit budget from
{{WAR-06}}: 300 identical pickets is a number, 300 units in escorted blocks with a screen and
a spearhead is a thing the player reads and answers.

## Tasks

- [ ] Design pass, written into `docs/DECISIONS.md`: define the roles as **compositions of
      existing stats**, not new rules. Screen (cheap, fast, high count, low `leak_cost`),
      line (armoured, slow, high `leak_cost`), support (`drains_mw`, no damage), spearhead
      (shielded, fast, expensive). Every one of these is already expressible in
      `data/enemies.json` today. Record the rejected alternative: a `role` field that the sim
      branches on, rejected because it is a rule in two files for something the stats already
      say.
- [ ] Schema: add a `formation` block to a wave in `anchor.schema.json` — a named,
      **pure-data** expansion that the loader turns into ordinary spawn entries. Shape:
      `{"kind": "block"|"echelon"|"stream", "lane": int, "rows": int, "spacing": float,
      "of": [{"enemy": id, "count": n}, ...]}`. `additionalProperties: false`.
- [ ] Implement the expansion **once**, in a place both engines consume identically. The
      honest option here is to expand in the data layer: `sim/content.py` expands formations
      into `Spawn` tuples, and `scripts/anchor_sim.gd:656-666` expands them the same way in
      `wave_queue()`. Two expansions of one algorithm is exactly the failure mode PRD §3 E4
      warns about for region→height; the alternative is a build-time expansion committed into
      the anchor JSON, which is uglier data but has one implementation. **Pick one, and record
      which and why.**
- [ ] Whatever is chosen, the expansion must emit spawn entries in a **total order** with the
      `(time, lane, enemy_id)` tie-break from {{WAR-01}} intact.
- [ ] `tools/validate/validate_data.py`: validate a formation's lane against `paths`, reject a
      formation whose expansion produces a zero-count row, and reject an expansion that
      exceeds a per-wave alive-unit ceiling (the {{WAR-06}} budget) — that ceiling is the
      replacement for "someone will notice".
- [ ] `tools/density.py`: report formations by name and their peak alive contribution, so a
      designer can see that "one block of 40" and "40 stream" are different shapes.
- [ ] Re-author two or three Act II/III waves as formations without changing their totals, and
      grade before and after. A formation that changes the grade is a balance change and needs
      a sweep, not a "no functional change" claim.
- [ ] Re-run parity; formations must expand identically in both engines on every anchor that
      uses one.
- [ ] Update `docs/NOMENCLATURE.md` if any role gets a name that reaches the player, and check
      the banned list before naming anything.

## Acceptance criteria

- A wave authored as a formation and the equivalent wave authored as flat spawn entries
  produce **identical** `wave_queue()` output in both languages.
- `tools/validate/validate_data.py` rejects a formation whose lane does not exist and one that
  exceeds the alive-unit ceiling.
- `tools/density.py` reports peak alive per formation.
- Parity 864/864 with at least one formation-carrying anchor in the set.
- No new branch in `_step()` in either rule file.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python tools/density.py anchor-XX
.venv/bin/python -m sim.run --jobs 8
.venv/bin/python tools/test_parity.py --anchor anchor-XX --verbose
.venv/bin/python tools/sweep.py anchor-XX --jobs 8
```

Proof to paste: the identical-queue check, the density table showing the formation, and
parity's 864/864.

## Risks / gotchas

- **Two expansions of one algorithm is the risk.** PRD §7 risk 10 is two anchor parsers
  disagreeing about region→height; a formation expander is the same hazard, findable only by
  the nine-minute parity gate.
- Spacing is a float multiplied by a row index. That is `× +` — inside the safe operation set
  (PRD §4.2) — but the multiply order must match in both files, or the last bit differs and a
  spawn lands one tick apart.
- Roles must not become a new dictionary key on a unit. LF-055: a unit dictionary must never
  grow a key. If a role needs to be visible to the FX layer, read it off `u["kind"]`, which is
  content data.
- Formations interact with BAL-01's scheduled-action policies: a policy graded against a flat
  wave is not graded against a formation of the same total. Re-grade rather than assume.

## Files likely touched

- `data/schema/anchor.schema.json`
- `data/anchors/*.json` (the re-authored waves)
- `sim/content.py`, `scripts/anchor_sim.gd`
- `tools/validate/validate_data.py`, `tools/density.py`
- `docs/DECISIONS.md`, `docs/NOMENCLATURE.md`
