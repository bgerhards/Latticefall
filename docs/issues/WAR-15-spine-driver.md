id: WAR-15
title: Spine Driver — charges off spare capacity, so it rewards staying under budget
labels: rules, content, design, phase-3
depends: WAR-13
milestone: E5 War
---
## Problem

Every pressure in the game today points the same way: more emplacements, more draw, closer to
the ceiling, and the brownout curve prices how far over you went
(`scripts/anchor_sim.gd:408-412`, `sim/engine.py:58-63`, decision 022). Nothing rewards
*headroom*. That makes the discipline of building under capacity a purely defensive virtue —
you avoid a penalty, you never gain anything. The Spine Driver inverts it: it accumulates
charge in proportion to `capacity() - bus_load()` and discharges for heavy damage, so leaving
the bus slack is an investment rather than caution. It is the third new shape of draw and it is
the one that argues *with* the other two.

## Tasks

- [ ] `docs/DECISIONS.md`: headroom becomes a resource. Record the rejected alternative — a
      flat damage bonus while under capacity — rejected because it is a threshold, and this
      project already learned from decision 022 that a threshold makes one side of the line
      unconditionally correct.
- [ ] Schema: optional `charges_on_headroom` block on a tower —
      `{"per_mw_second": float, "charge_max": float, "discharge_damage": float}`,
      `additionalProperties: false`, all positive. Reuse {{WAR-13}}'s state-varying-draw
      mechanism for the emplacement's own draw while charging.
- [ ] `sim/content.py`: flat fields on the frozen `Tower`.
- [ ] `sim/engine.py`: `Placed` gains `charge: float = 0.0`. In `_step()`,
      `headroom = max(0.0, capacity_now() - load)` where `load` is **the load already computed
      for this tick** in `_tick_once()` — do not call `bus_load()` a second time; it is
      O(units) and it would be computed at a different point in the tick than the penalty was.
      Pass the load down.
- [ ] `scripts/anchor_sim.gd`: the identical structure. `tick()` already computes `load` at
      `:421` and passes only `penalty` into `_step()`; pass `load` too, in both files, in the
      same argument position.
- [ ] Charge accrues `min(charge_max, charge + headroom * per_mw_second * DT)`. Fix the operand
      order and mirror it exactly — `headroom * per_mw_second * DT` and
      `headroom * (per_mw_second * DT)` are different doubles.
- [ ] Fire when `charge >= charge_max`, dealing `discharge_damage` through the existing
      `_damage()` path, then reset `charge` to 0.0. Target selection is unchanged.
- [ ] Add the Spine Driver row to `data/towers.json`. Check `docs/NOMENCLATURE.md`'s banned
      list before naming it.
- [ ] `scripts/hud.gd`: a charge readout on the selected emplacement, and — because this is the
      first mechanic that makes headroom *positive* — the load gauge should indicate headroom
      as a quantity rather than only as the absence of a fault. From `Ui` only.
- [ ] `sim/engine.py:441-489`: add a policy with a deliberately large `reserve` (the field
      already exists, `:139`) so "build less, hit harder" is gradeable against "build more".
- [ ] `tools/sweep.py`: confirm the reserve axis exists or add it; a driver-led board is a
      reserve question.
- [ ] Sweep, grade, re-run parity.

## Acceptance criteria

- A tower without `charges_on_headroom` grades byte-identically on all 24 anchors.
- Both engines agree on the exact tick each discharge lands, across 864 parity runs on an
  anchor carrying the driver.
- At zero headroom the driver never charges; at capacity/2 load it charges at the rate the data
  says, verified against a hand-computed figure.
- A high-reserve policy beats a spend-it-all policy on at least one anchor where the driver is
  unlocked, and the reverse holds somewhere else. If one dominates everywhere the numbers are
  wrong.
- `bus_load()` is called exactly once per tick in both engines (assert by call count in
  `tools/bench_tick.py`).

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/sweep.py anchor-20 --jobs 8
.venv/bin/python tools/bench_tick.py --units 400 --towers 60 --count-bus-load
.venv/bin/python tools/shot.py anchor-20 --out /tmp/driver.png --extra --pick <driver-slot>
```

Proof to paste: the empty diff, parity 864/864, the two policies' grades, the `bus_load()`
call count of 1/tick, and the inspector screenshot.

## Risks / gotchas

- **Do not recompute `bus_load()` inside `_step()`.** It is O(units) with a `_covered_by()` call
  per drain-carrying unit ({{WAR-05}}), and a second call at a different point in the tick
  reads a different world than the one the penalty was priced from — a parity divergence that
  only appears when a damper is on the board.
- Headroom is `capacity() - load`, and `capacity()` moves with Act III decay (decision 031),
  restorers ({{WAR-08}}), suppression ({{WAR-10}}) and captures ({{WAR-09}}). Every one of
  those changes the driver's charge rate. That is a feature, and it means each of those issues
  must re-sweep the driver's anchors.
- The driver rewards *not building*, which fights the free-placement pillar and the mass
  pillar. That tension is the design intent; if a sweep shows it dominating, cut the rate, do
  not cut the mechanic.
- Operand order and `min` clamping must be character-identical across the two files.

## Files likely touched

- `data/schema/towers.schema.json`, `data/towers.json`
- `sim/content.py`, `sim/engine.py`, `scripts/anchor_sim.gd`
- `scripts/hud.gd`
- `tools/sweep.py`, `tools/bench_tick.py`
- `docs/DECISIONS.md`, `docs/NOMENCLATURE.md`
