id: ART-05
title: A persistent-beam FX class — a held weapon, not a 0.18 s snap
labels: art, ui, phase-3
blocks: WAR-14
milestone: E6 Fidelity
---
## Problem

`data/schema/towers.schema.json`'s `fx.class` enum offers `bolt`, `arc`, `beam`, `flak`,
`mortar` and `field`, and `beam` is documented there as "charges up then snaps out full
length" — implemented in `scripts/combat_fx.gd` with `BEAM_CHARGE_TIME = 0.25` (`:25`) around
an instantaneous strike. It is a *shot*, drawn as a line. {{WAR-14}}'s Cutting Lance is a
weapon that stays on a target and ramps, and drawing a ramping held beam as a repeated snap
makes the mechanic unreadable — the player cannot see that continuity is what they are being
paid for.

Decision 053 made the fight legible by driving FX from presentation-only signals, and decision
055 requires that the cosmetic layer can never take the playfield down with it. A held beam is
the first FX that needs *state across frames tied to a live emplacement*, which is the case
those two decisions were written for.

## Tasks

- [ ] Add a `sustained` class to the `fx.class` enum in `data/schema/towers.schema.json` with
      the same "presentation-only, never read by a rule" description the block already carries,
      plus fields it needs (`width`, `core`, `colour`, `ramp_tint`, `flicker`).
- [ ] `scripts/combat_fx.gd`: a sustained effect is keyed on the **placed record** (safe — placed
      records are only ever compared by `slot`), holds a start time and an end time, and is
      re-anchored each frame from `placed["aim"]` (`anchor_sim.gd:537`), which is already the
      presentation-only aim point.
- [ ] The beam must end when the rules say the emplacement stopped firing. `shot_fired` is
      emitted per shot (`anchor_sim.gd:543`); a sustained beam should extend its own lifetime on
      each `shot_fired` from the same `placed` and expire on a timeout. **Do not add a new
      signal to the rules for this** — the FX layer inferring from existing signals is what
      keeps decision 053's contract intact.
- [ ] Tint or widen the beam by the ramp multiplier so the mechanic is visible. Read the ramp
      from the placed record; if {{WAR-14}} has not landed, drive it from elapsed hold time so
      the class is shippable on its own.
- [ ] Budget it: a sustained beam is one effect that lives for seconds, against a pool
      (`MAX_FX = 480`) evicted oldest-first (`:133-139`). A long-lived entry in an
      oldest-evicted pool is exactly the thing that gets evicted mid-life. Give sustained
      effects their own reservation, coordinating with {{WAR-06}}'s per-category budget.
- [ ] Fail closed: if the placed record disappears (sold, or the sim is rebound), the beam must
      drop, not crash. Decision 055.
- [ ] Modulate the beam's glow by bus load like every other emissive element (decision 007), so
      a brownout dims it.
- [ ] Screenshot a held beam at 100% and 200% interface scale, and capture three frames across
      a ramp to show the tint change.
- [ ] Check `docs/NOMENCLATURE.md` before naming the class anything that reaches the player.

## Acceptance criteria

- A tower with `fx.class = "sustained"` draws a continuous beam for as long as it keeps firing
  at one target, with no per-shot flicker.
- The beam is never evicted mid-life at 300 units on the board.
- Selling or removing the emplacement mid-beam drops the beam with no error in the log.
- The beam dims under a brownout along with the rest of the glow pass.
- Nothing in `scripts/anchor_sim.gd` or `sim/engine.py` changed — `git diff --stat` shows zero
  lines in either rule file.
- Three screenshots across a ramp show a visible difference.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-19 --out /tmp/beam1.png --extra --pick <lance-slot>
.venv/bin/python tools/shot.py anchor-19 --out /tmp/beam2.png --ui-scale 2.0
git diff --stat scripts/anchor_sim.gd sim/engine.py      # must be empty
.venv/bin/python tools/check.py --no-window
```

Proof to paste: the empty rule-file diff, the screenshots, and the gate line.

## Risks / gotchas

- **This is presentation only and must stay that way.** The moment the beam needs a rule to
  know when it stopped, it has become a rules change and needs both engines and a parity run.
  Infer from `shot_fired` and a timeout.
- FX state keyed on a *unit* is LF-055. Key on the placed record, which the codebase already
  establishes as safe (`anchor_view.gd:983-991`).
- `MAX_FX` eviction is oldest-first and global; a seconds-long effect is the worst case for it.
- The glow layer draws additively and is modulated by bus load; a beam drawn into the albedo
  pass will not dim and will look wrong under brownout.
- `--fixed-fps` has nobody to hold a mouse button; use `--pick` to reach the state a screenshot
  needs.

## Files likely touched

- `data/schema/towers.schema.json`
- `scripts/combat_fx.gd`, `scripts/fx_additive.gd`
- `data/towers.json` (the lance's `fx` block)
- `docs/NOMENCLATURE.md`
