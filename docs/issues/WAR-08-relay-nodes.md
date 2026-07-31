id: WAR-08
title: Relay nodes — player-built generation, and a per-wave generation curve
labels: rules, content, design, phase-2
depends: BAL-02
blocks: WAR-09
milestone: E5 War
---
## Problem

Pillar 5 of the programme is "power as a front line" — supply you build, route and defend.
Most of it already exists and is nearly free: `effect: {"type": "restore", "value": N}` is
already summed into capacity by both engines (`scripts/anchor_sim.gd:274-279`,
`sim/engine.py:198-203`), and `data/towers.json` already ships a `restorer` row with a
`restore-first` policy graded against it (`sim/engine.py:486-487`). What does **not** exist is
a generation *curve*: capacity is a single scalar per anchor (`anchor.schema.json:34-38`) that
only ever falls, via `capacity_decay_mw` (decision 031). A player who builds supply has no way
for that supply to ramp, and the act-III decay has no positive counterpart to fight.

## Tasks

- [ ] Add a second reactor emplacement to `data/towers.json` — a **row, not a mechanic**. It
      needs its own `cost`, `draw_mw` (a restorer that draws nothing is free capacity and
      deletes the decision), `effect: {"type": "restore", "value": N}`, `unlocked_at`, an `fx`
      block with `class: "field"`, and a `note` explaining what it is for. Check
      `docs/NOMENCLATURE.md`'s banned list before naming it.
- [ ] Schema: mirror `capacity_decay_mw` with `capacity_gain_mw` in
      `data/schema/anchor.schema.json` — MW the bus **gains** per wave, for anchors where the
      fiction is that the player is bringing supply online rather than losing it. `minimum: 0`,
      default 0, `additionalProperties: false` respected.
- [ ] `sim/engine.py:188-203` `capacity_now()`: apply gain and decay in one expression, in a
      fixed order, floored at `CAPACITY_FLOOR` of rated and **ceilinged** — an uncapped gain
      curve deletes the power decision by wave twelve, which is exactly the failure decision
      048 caught at 103% saturation. Add a `capacity_ceiling_mult` constant in both files.
- [ ] `scripts/anchor_sim.gd:265-285` `capacity()`: the identical expression, same operand
      order. This is a rule and it is read every tick through `brownout_penalty()`.
- [ ] `sim/content.py`: parse `capacity_gain_mw` onto `Anchor`.
- [ ] `tools/validate/validate_data.py:131-165`: the saturation invariant must be evaluated
      against **peak** capacity (rated + gain × waves + restorers at cap), not rated. Today it
      is checked against `capacity_mw` alone, which means a gain curve can walk an anchor past
      the "no power decision exists" error without the validator ever noticing — the same
      shape as decision 048's near-miss.
- [ ] `scripts/hud.gd:665-680`: the capacity readout must show the *current* capacity and
      signal that it is moving. It already reads `sim.capacity()` and `sim.rated_capacity()`;
      make the delta visible rather than implied.
- [ ] `tools/say_capacity.py`: it reads `capacity_mw` for the briefs. A curve means the brief's
      single number is a lie; either state the wave-one number explicitly or state the range.
- [ ] `tools/sweep.py`: `--cap` sweeps the rated scalar. Add a `--gain` axis so a generation
      curve is gradeable, and confirm `--apply` writes the new key.
- [ ] Add one policy to `sim/engine.py:441-489` that builds generation before guns and one that
      builds it last, so "was buying capacity worth it" is answerable. Both must be capped
      (BAL-02) or a generation-first policy fills the board with reactors and grades the
      anchor unwinnable for a reason that is the harness.
- [ ] Author or re-tune one Act III anchor with a gain curve and sweep it.
- [ ] Re-run parity and re-grade all 24; anchors with no `capacity_gain_mw` must be
      byte-identical.
- [ ] `docs/DECISIONS.md` entry: generation is data, the curve is a rule, and the ceiling is
      what stops it deleting the game.

## Acceptance criteria

- A new reactor row exists in `data/towers.json`, validates, and is buildable in-game.
- `capacity_gain_mw` is parsed and applied identically in both engines; an anchor without it
  grades byte-identically to before.
- The saturation check in `validate_data.py` uses peak capacity and **fires** on a
  deliberately over-generous test anchor.
- `tools/sweep.py --gain` produces a grid and `--apply` writes the chosen value.
- Parity 864/864.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/sweep.py anchor-22 --gain 0,2,4,6 --jobs 8
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/say_capacity.py
.venv/bin/python tools/shot.py anchor-22 --out /tmp/cap.png
```

Proof to paste: the validator firing on the over-generous fixture, the empty grade diff for
gain-free anchors, the sweep grid, parity's 864/864, and the HUD screenshot showing a moving
capacity.

## Risks / gotchas

- **Capacity is the game.** It is read by `anchor_sim.gd:265-285`, `sim/engine.py:188-203`,
  `Outcome.capacity_mw`, `sim/content.py:84`, `hud.gd:669-676`, `sweep.py:126,138-139,208`,
  `validate_data.py:139-165`, `say_capacity.py:48` and `anchor.schema.json:34-38`. Nine call
  sites, and `Outcome.capacity_mw` reports the *rated* scalar — a curve makes that field
  ambiguous, so decide whether it reports rated or peak and say so in the dataclass.
- `capacity_decay_mw` and `capacity_gain_mw` on the same anchor must have a defined order of
  application, identical in both files. Prefer forbidding both in the validator over trusting
  the order.
- A restorer that is switched offline stops restoring (`p.online` guard at both sites). Once
  {{WAR-10}} lands, a *suppressed* restorer must also stop supplying — that is called out
  there, but it means the capacity expression gains a second condition and both files must
  gain it in the same commit.
- Uncapped generation is decision 048's failure again: every anchor grades clean, the
  validator says nothing, and the power decision has quietly stopped existing.

## Files likely touched

- `data/towers.json`, `data/schema/anchor.schema.json`
- `sim/content.py`, `sim/engine.py`, `scripts/anchor_sim.gd`
- `scripts/hud.gd`
- `tools/validate/validate_data.py`, `tools/sweep.py`, `tools/say_capacity.py`
- `data/anchors/anchor-2*.json`
- `docs/DECISIONS.md`, `docs/NOMENCLATURE.md`
