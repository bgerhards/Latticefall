id: PLC-03
title: Firing arcs as dot products against an authored cos_half_angle — no angles in the rules
labels: phase-2, rules, engine
depends: PLC-01
milestone: E3 Placement
---
## Problem

Free placement makes *which way an emplacement points* a real question for the first time: an
omnidirectional gun on a fixed slot is the same gun wherever it stands, but a gun the player
positions and a weapon that fires in a shape (the Siege Battery, the Cutting Lance, the Spine
Driver's pierce line — PRD §E6) both need an arc, and an arc looks exactly like a job for
`atan2`.

It is not. Probe (PRD §2.1): 100,000 float64 pairs across five value regimes, 24 operations, raw
IEEE-754 bytes compared on CPython, Linux Godot 4.7.1 **and Windows Godot 4.7.1**. `atan2 sin
cos tan pow log exp` all diverge on Windows (MSVC UCRT) from CPython and Linux Godot (glibc):
`atan2` 0.084%, `sin` 0.133%, `cos` 0.120%, `pow` 0.130%, `log` 0.031%, `exp` 0.069%, and **`tan`
4.32%**. Stable across runs — a library difference, not nondeterminism. And the parity gate would
not catch it: `tools/test_parity.py` resolves Godot through `toolpaths.godot()`, which prefers
the Linux build, so **all 864 parity runs test a binary the owner does not play** (LF-105, PRD
risk #2). The rules use none of the divergent operations today, so cross-platform parity holds
*by accident*.

So the rule to write down before anyone needs it: **a firing arc is
`dot(to_target_normalised, facing) >= cos_half_angle`, with `cos_half_angle` a constant authored
in `data/towers.json`** — never an angle difference, never `deg_to_rad`, never `atan2`. Facing and
yaw stay presentation-only, where decision 049 already put them.

## Tasks

- [ ] Add optional `cos_half_angle` (number, −1.0 … 1.0) and `facing` (two-element array, tile
      space) to `data/schema/towers.schema.json`. **Absent means omnidirectional**, so every one
      of the nine shipped rows is unchanged and parity is byte-identical. `additionalProperties:
      false` is set — the schema moves first.
- [ ] Implement the arc test in both `scripts/anchor_sim.gd`'s targeting loop (`:481-508`) and
      `sim/engine.py` (`:337`ff), guarded so an absent `cos_half_angle` skips the test entirely
      and costs nothing.
- [ ] **Avoid the square root.** `dot(to_target, facing) >= cos_half_angle * sqrt(d2)` needs one;
      squaring both sides gives `dot*dot >= cos_half_angle*cos_half_angle * d2`, which is exact
      and cheaper — **valid only when `dot >= 0`**, i.e. for half-angles under 90°. Write the sign
      guard, and put the derivation in the docstring so the next person does not "simplify" it
      away. For a half-angle over 90°, `cos_half_angle` is negative and the test inverts; handle
      or reject it in the schema, deliberately.
- [ ] Store `facing` on the placed record as two float64s at build time. Never a `Vector2`.
      Whether the player can rotate an emplacement at build time is a design question — for this
      issue, facing comes from data and does not change.
- [ ] Mirror into `scripts/test/parity.gd`.
- [ ] Scope the unsafe-op gate check ({{BAL-07}}) correctly: it must scan **only**
      `scripts/anchor_sim.gd` and `sim/engine.py`. `scripts/iso.gd` legitimately uses `cos`,
      `sin` and `atan2` in `heading_for_yaw()` and `yaw_for_heading()` — that is presentation
      (decision 049), and a check that flags it will be turned off within a week. Coordinate the
      file list with whoever owns that check.
- [ ] Add a probe test: one throwaway tower row with a 60° arc, placed so a unit passes through
      and out of the arc, driven through both engines, outcomes compared. Delete the row before
      merge or mark it non-shipping so `validate_data.py` stays clean.
- [ ] Update `CLAUDE.md`'s non-negotiables and `docs/DECISIONS.md`: the safe/banned operation
      sets, the dot-product rule for arcs, and the fact that the Windows build is the one the
      owner plays. Reference {{PLC-01}}'s supersession of decision 030 rather than repeating it.
- [ ] Note in the issue's closing comment that this lands the **mechanism and the guard**, not a
      change to any shipped weapon. The Siege Battery, Cutting Lance and Spine Driver (PRD §E6)
      are the consumers.

## Acceptance criteria

- No shipped tower row gains `cos_half_angle`, and `tools/test_parity.py` reports 864 runs
  identical with the code in place — the arc path is provably inert until data asks for it.
- With the probe row, both engines agree on every tick about whether the unit is in arc.
- `grep -nE 'atan2|deg_to_rad|\bsin\(|\bcos\(|\btan\(|\bpow\(|\blog\(|\bexp\(' scripts/anchor_sim.gd
  sim/engine.py` returns **nothing**.
- The squared form is used and the `dot >= 0` guard is present and commented.
- The unsafe-op gate check passes on `main` and fails when `atan2` is added to either rule file.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python tools/check.py                       # 864-run parity, unchanged
# deliberate break
printf '\nfunc _lf_probe(a: float) -> float:\n\treturn atan2(a, 1.0)\n' >> scripts/anchor_sim.gd
.venv/bin/python tools/check.py --no-window           # unsafe-op check must be RED
git checkout scripts/anchor_sim.gd
.venv/bin/python tools/reap.py
```

Proof is `864 runs identical` with the arc code present and inert, plus the red/green pair on the
deliberate `atan2`.

## Risks / gotchas

- **The gate never tests the Windows build the owner plays** (LF-105). Adding a banned operation
  would pass 864 runs and fail on the owner's machine, once, unreproducibly. That is why the
  static check matters more here than the parity run does.
- **`tan` diverges on 4.32% of samples.** If anyone writes an arc as a tangent ratio, it will be
  wrong on the owner's machine roughly one time in twenty-three.
- **`cos_half_angle` is authored in sRGB-free units — degrees never appear.** If a designer wants
  to think in degrees, convert in a tool that writes the JSON, never at runtime in the rules.
- **`iso.gd` uses banned ops legitimately.** Scope the check by file or it becomes noise.
- Facing on the placed record is float64; `_face()` in `anchor_view.gd` already stores a
  presentation `view_yaw` on the same dictionary — two facings on one record, with different
  meanings. Name them so nobody uses the presentation one as truth.

## Files likely touched

- `data/schema/towers.schema.json`, `data/towers.json`
- `scripts/anchor_sim.gd`, `sim/engine.py`, `scripts/test/parity.gd`
- `tools/check.py` (unsafe-op check scoping, with {{BAL-05}})
- `CLAUDE.md`, `docs/DECISIONS.md`
