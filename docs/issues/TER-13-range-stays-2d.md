id: TER-13
title: Range stays 2-D in v1 — high_ground_bonus is the tuning knob, not 3-D distance
labels: rules, design, risk, phase-3
depends: TER-01
milestone: E4 Terrain
---
## Problem

Once the board has height, the obvious next step is to make range a 3-D distance. **It is
physically right and it plays wrong**, and this issue exists to write that down and to
build the knob that gives the same feeling without the defect.

A weapon's reach is a radius in tile space (`_draw_range()`,
`scripts/anchor_view.gd:1121-1132`, drawn as a 2:1 ellipse for exactly that reason). If
range became `sqrt(dx² + dy² + (z·k)²)`, a turret on a 3-level hill would spend part of
its radius climbing down and would therefore cover **less plan area** than the identical
turret sitting in the valley beside it. The player's intuition — and the entire point of
high ground — says the opposite. High ground must be a benefit, and a rule that silently
punishes it is a rule players will read as a bug.

PRD §8 lists 3-D range as explicitly out of scope for v1. This issue makes that a decision
with a rejected alternative attached, and specifies the replacement.

## The knob

A per-weapon `high_ground_bonus` in `data/towers.json`: a multiplier applied to range (or
to damage — decide, do not ship both) per level of elevation advantage over the target,
capped. Elevation advantage is `shooter_z − target_z`, an integer difference, so the rule
is `+ − × ÷ min max` only — inside the safe operation set (PRD §2.1, §4 invariant 2), no
square root, no angle.

**It invalidates all 24 grades**, and it invalidates every `capacity_mw` with them: a
weapon that reaches further from a hill changes what a viable build costs, the validator's
saturation guard keys off capacity (`tools/validate/validate_data.py:150-165`), and the
mission briefs quote their own capacity aloud with a gate check verifying the spoken number
(`tools/check.py:211`, `check_dialog_capacity`). Re-grading is not optional cleanup; it is
part of the change.

## Tasks

- [ ] Write the decision entry **first**, with the rejected alternative stated as above:
      3-D range is physically correct and inverts the value of high ground. Reference
      PRD §8 and this issue.
- [ ] Confirm and document that today's range test is 2-D in both engines and stays that
      way — cite the comparison sites in `sim/engine.py` and `scripts/anchor_sim.gd` by
      line, and add a comment at each saying height is deliberately excluded.
- [ ] Add `high_ground_bonus` to `data/schema/towers.schema.json`, optional, default 0.0,
      with a `description` naming what it multiplies and its cap.
- [ ] Decide range-bonus versus damage-bonus and ship exactly one. Range is the more
      legible on screen (`_draw_range()` can show it); damage is the more common
      tower-defence idiom. Pick, justify in the decision entry, and do not leave both
      fields in the schema "for later".
- [ ] Implement it in `sim/engine.py` and `scripts/anchor_sim.gd` in the **same commit**,
      from one prose description, using only safe operations. Elevation advantage is
      `int − int`; keep it integer until the final multiply.
- [ ] Guard the degenerate cases in both engines identically: negative advantage (shooting
      uphill) is clamped at 0, not made negative; the cap is a `min`, not a branch.
- [ ] Show it. If the bonus is range, `_draw_range()` must draw the *actual* reach from
      that tile, and the inspector must say why the number differs from the tower's base
      range. A hidden bonus is a bonus the player cannot plan around.
- [ ] Re-grade all 24 anchors, re-tune, and re-check `capacity_mw` on every one against
      the validator's saturation guard.
- [ ] Re-run `check_dialog_capacity` — a brief that reads a capacity aloud must still
      match the data after re-tuning.
- [ ] Re-run `tools/density.py` and the gate's `wave density` check: a stronger high-ground
      turret changes peak units in flight, which is what that check compares acts on.

## Acceptance criteria

- A decision entry exists recording 2-D range and the rejected 3-D alternative, written
  before the code.
- With `high_ground_bonus` absent or 0.0 on every tower, **parity is byte-identical and all
  24 grades are unchanged** — the knob is inert until authored.
- With the knob authored on one weapon, both engines agree bit-for-bit across the full
  864-run sweep.
- Shooting uphill never produces a penalty — a turret at level 0 firing at level 3 behaves
  exactly as it does today.
- All 24 anchors are re-graded and every `capacity_mw` re-checked; the validator reports
  no new saturation warnings and no anchor above the 80% warn threshold that was not there
  before.
- Every brief that quotes a capacity still matches its anchor.

## Verification

```bash
.venv/bin/python tools/test_parity.py                 # 864/864, knob at 0 -> digest unchanged
.venv/bin/python -m sim.run --jobs 8                  # baseline, then with the knob
.venv/bin/python tools/sweep.py anchor-20 --jobs 8    # re-tune
.venv/bin/python tools/validate/validate_data.py      # saturation + dead slots
.venv/bin/python tools/density.py
.venv/bin/python tools/check.py --no-window           # dialog capacity, wave density
```

Paste both grade tables — before, and after with the knob authored — and the parity digest
for the knob-at-zero run.

## Risks / gotchas

- **No square root, no `pow`.** `sqrt` is provably safe (PRD §2.1) but is not needed here,
  and `pow` is banned outright — Windows Godot diverges from CPython at 0.130% on
  gameplay-scale arguments.
- Compare squared distances, as the rules already do (decision 030). A bonus applied to a
  radius must be applied **before** squaring, or it is applied to the square and the
  effect is quadratic by accident.
- `distance_to` is float32 and is banned: for 2,000,000 points on an exact integer radius,
  float32 and float64 disagree on `<= r` **10.2%** of the time — that is the six-leak
  divergence, quantified. Never route this through `Vector2`.
- The elevation advantage must be read from the same resolved terrain grid in both engines
  ({{TER-02}}), not re-derived. A one-tile parser disagreement becomes a firing
  disagreement.
- Re-grading is the expensive part, not the rule. Budget for it, and use `--jobs` — the sim
  has no RNG and no shared state, so parallel grading returns the same cells in the same
  order.
- Do not couple this to {{TER-12}}. Line of sight is a separate, owner-gated predicate;
  this knob must be shippable without it.

## Files likely touched

- `data/schema/towers.schema.json`, `data/towers.json`
- `sim/engine.py`, `scripts/anchor_sim.gd`
- `scripts/anchor_view.gd` (range drawing, inspector text)
- `data/anchors/*.json` (re-tuned capacities)
- `docs/DECISIONS.md`
