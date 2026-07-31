id: WAR-11
title: Regional power grid — RECOMMENDED AGAINST, owner-gated
labels: risk, rules, design, ui, phase-4
depends: PLC-01, WAR-08
milestone: E5 War
---
## Problem

The idea is that capacity stops being one board-wide scalar and becomes per-zone, so a
brownout is local and routing power is a spatial decision. It is written up here because it
was asked for, and it is **recommended against for now** — PRD §6 open decision 4, PRD §3 E5.
This issue must not be started without the owner explicitly saying so.

The cost is not the concept, it is the blast radius. `capacity_mw` is read by
`scripts/anchor_sim.gd:265-285`, `sim/engine.py:188-203`, `Outcome.capacity_mw`,
`sim/content.py:84`, `scripts/hud.gd:669-676`, `tools/sweep.py:126,138-139,208`,
`tools/validate/validate_data.py:139-165`, `tools/say_capacity.py:48` and
`data/schema/anchor.schema.json:34-38`. Today `_step()` computes **one** scalar
`rate = 1.0 - penalty` and applies it to every gun in one pass (`anchor_sim.gd:436-443`,
`sim/engine.py:306`). Per-region means a per-zone penalty looked up per tower **inside the
hottest parity-sensitive loop in the project** — the loop {{WAR-03}} exists to make fast
enough — plus zone-aware grading policies, per-zone saturation validation, a `sweep.py
--apply` that writes a structure rather than a number, N gauges in an instrument column
already fighting for vertical space at 200% (decision 050), and 24 mission briefs that speak
one capacity number aloud.

## Tasks

- [ ] **Gate.** Do not start until the owner has answered PRD §6 decision 4 in writing and the
      answer is recorded in `docs/DECISIONS.md`. If the answer is no, close this issue with
      that decision as the reason and stop.
- [ ] Prototype the *presentation* first, on paper: sketch N capacity gauges into the
      instrument column at `--ui-scale 2.0` on `anchor-24` and confirm they fit. Decision 050
      already had to make both panels scroll to reach 200%; if three gauges do not fit, the
      feature is not shippable and nothing else here matters.
- [ ] Schema: zones as **regions**, not a per-tile map — `{"id", "capacity_mw", "rect": [x, y,
      w, h]}` with an implicit default zone for anything uncovered. A dense per-tile map is not
      something a human can read or a generator can emit cleanly (the same argument PRD §3 E4
      makes for terrain).
- [ ] Define zone lookup as a **single shared algorithm** and implement it identically in both
      engines. Two implementations of a spatial lookup is PRD risk 10 in a hotter loop.
- [ ] `sim/engine.py` / `scripts/anchor_sim.gd`: per-zone `bus_load()` and `capacity_now()`,
      and a per-zone `penalty` array computed **once per tick** — never looked up per tower
      inside the fire loop. The fire loop indexes a precomputed array by a zone index cached on
      the placed record at build time.
- [ ] Units that drain (`drains_mw`) drain the zone they are standing in. That is a position
      lookup per drain-carrying unit per tick; fold it into {{WAR-02}}'s position pass.
- [ ] `tools/validate/validate_data.py`: saturation must be checked **per zone**. A board that
      passes globally and saturates one zone has no power decision in that zone, which is
      decision 048's failure localised.
- [ ] `tools/sweep.py`: `--cap` becomes a per-zone axis; `--apply` writes a structure. Cap the
      grid size or the sweep becomes combinatorial across zones.
- [ ] `sim/engine.py` policies: build order must become zone-aware, or every policy fills one
      zone and browns it out while the rest of the board idles. This is BAL-02's capped-core
      work applied per zone.
- [ ] `scripts/hud.gd`: N gauges, from `Ui` sizes and colours only.
- [ ] `tools/say_capacity.py` and all 24 briefs: decide what a brief says when there are three
      numbers.
- [ ] `Outcome`: `capacity_mw` becomes ambiguous. Decide what it reports and update the parity
      comparison.
- [ ] Re-run parity and re-grade all 24 anchors. Every anchor without zones must be
      byte-identical; every anchor with zones is a new balance problem.

## Acceptance criteria

- The owner's decision is recorded in `docs/DECISIONS.md` before any code lands.
- Three capacity gauges fit in the instrument column on `anchor-24` at `--ui-scale 2.0`,
  proven by a screenshot and an a11y report on the same frame.
- Zone penalty is computed once per tick, not per tower — assertable by a call count in
  `tools/bench_tick.py`.
- ms/tick at 400 units / 60 emplacements / 3 zones is still inside the 5.6 ms budget.
- Zone-free anchors grade byte-identically; parity 864/864.
- `validate_data.py` fires the saturation error on a fixture where one zone alone is saturated.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python tools/bench_tick.py --units 400 --towers 60 --zones 3
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/shot.py anchor-24 --out /tmp/zones.png --ui-scale 2.0 --a11y /tmp/zones.json
.venv/bin/python tools/validate/a11y.py /tmp/zones.json --shot /tmp/zones.png --all
```

## Risks / gotchas

- **This is the expensive one and it is the reason this issue carries `risk`.** It rewrites the
  hottest parity-sensitive loop, touches eight tools, the briefs, and an instrument column
  already at its limit.
- A zone index cached on a placed record is fine (records are compared by `slot`); a zone index
  cached on a *unit* is LF-055 again.
- Free placement (PLC-01) means a tower's zone is a float position lookup rather than a slot
  lookup, and a tower can sit on a zone boundary. Define the boundary rule (half-open rects,
  `floor`) once and mirror it.
- The gauges compete with the threat panel, which decision 037 already fought for space with,
  and with the scroll regions decision 050 introduced.
- Every mission brief that says a capacity number becomes wrong. 24 dialog files.

## Files likely touched

- `data/schema/anchor.schema.json`, `data/anchors/*.json`, `data/dialog/*.json`
- `sim/content.py`, `sim/engine.py`, `scripts/anchor_sim.gd`
- `scripts/hud.gd`
- `tools/validate/validate_data.py`, `tools/sweep.py`, `tools/say_capacity.py`,
  `tools/test_parity.py`, `tools/bench_tick.py`
- `docs/DECISIONS.md`
