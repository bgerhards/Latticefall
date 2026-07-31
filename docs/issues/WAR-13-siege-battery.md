id: WAR-13
title: Siege Battery — a weapon whose draw is a spike, not a level
labels: rules, content, design, phase-3
blocks: WAR-14, WAR-15
milestone: E5 War
---
## Problem

Every emplacement in `data/towers.json` draws a constant `draw_mw` while online, summed
unconditionally at `scripts/anchor_sim.gd:176-189` and `sim/engine.py:220-221`. That makes the
power economy a **static allocation problem**: the player solves it once at build time and the
bus load then only moves when a drain-carrying unit walks on. Pillar 4 of the programme says
spectacle must be *different shapes of draw*, never bigger numbers, and the Siege Battery is
the first and simplest new shape — it idles near zero and spikes hard during a salvo. The
decision it creates is temporal rather than spatial: a board that fits at idle can brown out
the moment two batteries fire together.

This issue also owns the **schema and rules mechanism for a time-varying draw**, which
{{WAR-14}} and {{WAR-15}} both build on.

## Tasks

- [ ] `docs/DECISIONS.md`: draw becomes a function of emplacement *state*, not a constant.
      Record the rejected alternative — a second `draw_mw_peak` field averaged into one number
      — rejected because the average is exactly the thing that makes the mechanic invisible.
- [ ] Schema: add an optional `salvo` block to `data/schema/towers.schema.json` —
      `{"charge_s": float, "shots": int, "shot_interval": float, "draw_idle_mw": float,
      "draw_salvo_mw": float}`, `additionalProperties: false`, all positive. A tower without
      `salvo` behaves exactly as today, and `draw_mw` remains required so nothing that reads it
      (the HUD's build tooltip, `validate_data.py`'s saturation maths) breaks.
- [ ] Decide what `draw_mw` means for a salvo tower and write it in the schema description —
      almost certainly `draw_salvo_mw`, because `validate_data.py:150-155` computes board
      saturation from `max(draw_mw)` and understating it would let a board of batteries pass
      the "no power decision exists" check while browning out in play.
- [ ] `sim/content.py`: parse the block onto the frozen `Tower` dataclass as flat fields, in
      the existing style.
- [ ] `sim/engine.py`: `Placed` gains `salvo_state` (charging / firing / idle) and a phase
      clock. Advance it in `_step()` **before** `bus_load()` is next read — note that
      `bus_load()` runs at the top of `_tick_once()`, so the phase advanced this tick is the
      one next tick's load sees. Fix that ordering explicitly and mirror it.
- [ ] `sim/engine.py:220-221` `_online_draw()` and `:207-218` `bus_load()`: a salvo tower
      contributes `draw_salvo_mw` while firing and `draw_idle_mw` otherwise.
- [ ] `scripts/anchor_sim.gd:176-189` `online_draw()`: the identical branch. **Careful** — this
      function also carries the Overcharge multiplier, which is GDScript-only and must keep
      multiplying only what it multiplies today.
- [ ] `sim/engine.py:326-357` / `anchor_sim.gd:467-568`: the fire loop drives the salvo phase.
      A salvo fires `shots` times at `shot_interval` and then re-enters `charge_s`. Reuse the
      existing `cooldown` field for the intra-salvo interval rather than adding a second clock,
      so there is one place a fire decision is made.
- [ ] Add the Siege Battery row to `data/towers.json` with `fx.class` and a `note` explaining
      the shape. Check `docs/NOMENCLATURE.md`'s banned list before naming it.
- [ ] `scripts/hud.gd`: the load gauge must be readable when it spikes. A gauge that pins to
      the right for 1.2 seconds every 8 is not a readout, it is a flicker — add a
      short-window peak marker. Colours and sizes from `Ui` (decisions 045/046).
- [ ] `scripts/hud.gd` build tooltip and the inspector must show the idle and salvo draw as two
      numbers. One averaged number is a lie about the decision being made.
- [ ] `tools/validate/validate_data.py`: saturation must use the salvo draw; add a warning when
      an anchor's capacity cannot absorb two simultaneous salvos, because that is the
      interesting case and it should be a deliberate choice.
- [ ] `sim/engine.py:441-489`: add a policy that leads with the battery, capped, so it grades.
- [ ] Sweep and grade the anchors it is unlocked at; re-run parity.

## Acceptance criteria

- A tower with no `salvo` block produces byte-identical grades on all 24 anchors.
- On a board with one battery, measured bus load over a wave shows a square wave between
  `draw_idle_mw` and `draw_salvo_mw`, and `peak_load_mw` in the `Outcome` reflects the spike.
- Both engines agree on every `Outcome` field across 864 parity runs with a battery-carrying
  anchor in the set.
- `validate_data.py` warns on an anchor that cannot absorb two simultaneous salvos.
- The HUD shows idle and salvo draw separately, and the load gauge is legible during a spike
  (screenshot at 100% and 200% interface scale).

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/sweep.py anchor-18 --jobs 8
.venv/bin/python tools/shot.py anchor-18 --out /tmp/siege.png --ui-scale 2.0 --a11y /tmp/siege.json
.venv/bin/python tools/validate/a11y.py /tmp/siege.json --shot /tmp/siege.png --all
```

Proof to paste: the empty diff for salvo-free anchors, parity 864/864, the sweep grid showing
the anchor is still winnable by more than one policy, and the two screenshots.

## Risks / gotchas

- **Phase ordering against `bus_load()`.** `bus_load()` is called at the top of `_tick_once()` /
  `tick()`, before `_step()`. Whichever tick the phase is advanced on, both files must advance
  it on the same one, or the load differs by one tick and compounds through the brownout
  penalty.
- `validate_data.py`'s saturation maths is the invariant that keeps the power decision alive
  (decision 048). Understating a battery's draw there is the same failure as anchor-24 at 103%.
- Do not add a second cooldown field. Two clocks in two languages is two chances to drift.
- The salvo state lives on the `placed` record, which is only ever compared by `slot` — safe,
  the same argument as `aim`/`kills`/`emp_until` ({{WAR-10}}).
- Overcharge (`anchor_sim.gd:180`, `:442-443`) multiplies draw and fire rate and is
  GDScript-only. Confirm it still multiplies exactly what it did, or a parity-invisible ability
  silently changes the new mechanic's economics.

## Files likely touched

- `data/schema/towers.schema.json`, `data/towers.json`
- `sim/content.py`, `sim/engine.py`, `scripts/anchor_sim.gd`
- `scripts/hud.gd`, `scripts/combat_fx.gd`
- `tools/validate/validate_data.py`
- `docs/DECISIONS.md`, `docs/NOMENCLATURE.md`
