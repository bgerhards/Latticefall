id: WAR-14
title: Cutting Lance — damage that ramps while held, so switching targets wastes it
labels: rules, content, design, phase-3
depends: WAR-13, ART-05, BAL-01
milestone: E5 War
---
## Problem

Damage in both engines is a constant per shot (`scripts/anchor_sim.gd:571-602`,
`sim/engine.py:359-368`) and targeting re-acquires from scratch every time the cooldown
expires, keeping the furthest-along candidate (`anchor_sim.gd:500-525`, `sim/engine.py:333-344`).
Nothing in the game rewards *continuity* of fire. The Cutting Lance is the second new shape of
draw: it ramps damage the longer it stays on one target, so switching wastes the ramp — which
makes the player's targeting-mode choice (`data/tuning.json` `targeting`) a real decision
instead of a preference, and makes a wave of many small units a genuinely bad matchup for it.

It also needs something that does not exist: the current `beam` FX class is a **0.18 s snap**
(`data/schema/towers.schema.json` fx description, `scripts/combat_fx.gd:25`
`BEAM_CHARGE_TIME`), not a held state. That is {{ART-05}}.

## Tasks

- [ ] `docs/DECISIONS.md`: the ramp is state on the **placed** record (safe — placed records
      are only ever compared by `slot`), and it is keyed on a target identity that is stable
      in both languages. Record the rejected alternatives: storing a reference to the target
      unit (rejected — LF-055, a unit dictionary must never grow a key and Godot compares
      dictionaries by value), and storing the target's `dist` (rejected — `dist` advances every
      tick, so it identifies nothing).
- [ ] Solve target identity properly. The only index-stable identity available is the position
      in `units`, and **`units` is compacted** — `prune_dead()` (`anchor_sim.gd:669-674`) and
      the wave-end filter (`sim/engine.py:408`) both rebuild the list. So the ramp must reset
      at every compaction point, in both engines, at the same point. Write that down and put
      an assertion behind it.
- [ ] Schema: optional `ramp` block on a tower in `data/schema/towers.schema.json` —
      `{"per_shot": float, "max_mult": float, "reset_on_switch": true}`,
      `additionalProperties: false`. A tower without it is unchanged.
- [ ] `sim/content.py`: flat fields on the frozen `Tower`.
- [ ] `sim/engine.py`: `Placed` gains `ramp_mult: float = 1.0` and `ramp_target: int = -1`.
      After target selection, if the selected index differs from `ramp_target`, reset
      `ramp_mult` to 1.0; otherwise `ramp_mult = min(max_mult, ramp_mult + per_shot)`. Apply it
      in `_damage()` through the existing `scale` path, not by scaling `tower.damage` — the
      `Tower` dataclass is frozen and shared, and `anchor_sim.gd:571-577` already documents why
      scaling a shared definition buffs that tower type for every board in the session.
- [ ] `scripts/anchor_sim.gd`: the identical fields and the identical expressions. Note that
      `_damage()` here already takes a `dmg_mult` for veterancy — the ramp multiplies alongside
      it, and the **multiply order must match Python**, which currently has no `dmg_mult` at
      all. Add the parameter to `sim/engine.py._damage()` with a 1.0 default so both signatures
      and both orders line up, rather than leaving the two files structurally different.
- [ ] Reset the ramp when the emplacement has no target (which is also {{WAR-04}}'s branch) and
      when it is suppressed ({{WAR-10}}).
- [ ] Add the Cutting Lance row to `data/towers.json` with `fx.class` pointing at the persistent
      beam from {{ART-05}}. Check `docs/NOMENCLATURE.md`'s banned list before naming it.
- [ ] `scripts/hud.gd` inspector: show the current ramp multiplier on the selected emplacement.
      A mechanic the player cannot see is a mechanic they will not use.
- [ ] `sim/engine.py:441-489`: the lance is only gradeable against a policy that can express
      "hold the target" versus "switch" — that is BAL-01's scheduled-action policies. Add at
      least one of each and record the grade difference; if the two grade the same, the ramp
      numbers are too small to matter and should be retuned before shipping.
- [ ] Sweep, grade, re-run parity.

## Acceptance criteria

- A tower without a `ramp` block grades byte-identically on all 24 anchors.
- Holding one target for N shots deals exactly `min(max_mult, 1 + N × per_shot)` times base
  damage, in both engines, on the same tick.
- Switching targets resets the multiplier to 1.0 in both engines on the same tick, including
  across a `prune_dead()` / wave-end compaction.
- A hold policy and a switch policy produce **different** grades on the lance's anchors.
- Parity 864/864 with a lance anchor in the set.
- The inspector shows the live ramp multiplier.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/sweep.py anchor-19 --jobs 8
.venv/bin/python tools/shot.py anchor-19 --out /tmp/lance.png --extra --pick <lance-slot>
```

Proof to paste: the empty diff for ramp-free anchors, parity 864/864, the two policies' grades
side by side, and the inspector screenshot showing the ramp.

## Risks / gotchas

- **Target identity is the whole difficulty and LF-055 is the precedent.** The Python side used
  `u is target` and the GDScript side `u == target` for the splash exclusion; they diverged the
  moment two identical units shared a `dist`. Any identity scheme that survives one tick but
  not a compaction has the same shape.
- `prune_dead()` is called by the view layer, not by the sim, in GDScript; `sim/engine.py`
  compacts at wave end inside `run()`. **These are not the same moment.** Either make the reset
  unconditional at wave boundaries in both, or make the identity independent of index — a
  monotonically increasing spawn serial assigned at construction is the honest fix, and it is a
  field written once, like `lane` in {{WAR-01}}.
- Multiply order: `damage * scale * dmg_mult * ramp_mult` is not bit-identical to
  `damage * ramp_mult * scale * dmg_mult`. Fix the order in both files and say so in both.
- The ramp interacts with splash: splash damage at `scale = 0.5` should probably not ramp.
  Decide, and make both files agree.
- {{ART-05}} is a hard prerequisite for shipping, not for the rules — the rules can land first,
  but a held beam drawn as a 0.18 s snap makes the mechanic unreadable.

## Files likely touched

- `data/schema/towers.schema.json`, `data/towers.json`
- `sim/content.py`, `sim/engine.py`, `scripts/anchor_sim.gd`
- `scripts/hud.gd`
- `docs/DECISIONS.md`, `docs/NOMENCLATURE.md`
