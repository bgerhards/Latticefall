id: WAR-12
title: Euclidean path lengths and diagonal lanes — explicitly optional, re-grades the campaign
labels: rules, design, risk, phase-4
depends: WAR-01
milestone: E5 War
---
## Problem

Both engines compute a path segment as `abs(dx) + abs(dy)` — `scripts/anchor_sim.gd:142-147`,
`sim/content.py:104` — and `tools/validate/validate_data.py:98-100` hard-rejects any diagonal
segment. Decision 030 bundled two causes for that, and PRD §2.1 shows only one survives a
probe: `sqrt` is correctly rounded by IEEE-754 §5.4.1, both runtimes issue `SQRTSD`, and it
matched **100,000/100,000** on CPython, Linux Godot 4.7.1 and Windows Godot 4.7.1. The culprit
was `Vector2.distance_to`, a float32 helper — for 2,000,000 points on an exact integer radius,
float32 and float64 disagree on `<= r` **10.2%** of the time. An end-to-end off-grid loop with
float64 positions, `sqrt`-normalised directions and remainder-carrying advance came out
bit-identical on all three runtimes over 4,000 ticks × 64 units.

So diagonal lanes are now *safe*. They are not *free*: switching the segment metric changes
every `path_length`, every unit `dist`, and therefore every wave's arrival timing on every
anchor. **All 24 anchors get re-graded.** PRD §8 lists curved and diagonal lanes as out of
scope unless separately costed — this is that separate costing, and it should not be started
alongside {{WAR-01}}.

## Tasks

- [ ] Confirm the probe rather than trusting the write-up: re-run the `sqrt` determinism check
      on this machine against Linux Godot **and** the Windows Godot the owner actually plays,
      and paste both results. PRD §2.1 also records that `tools/test_parity.py` resolves Godot
      through `toolpaths.godot()`, which prefers the Linux build — **all 864 parity runs test
      a binary the owner does not play.** That is PRD risk 2 and it is a blocker for this
      issue specifically, because this is the first change that would rely on the operation
      set being identical across platforms.
- [ ] `sim/content.py:101-109`: segment length becomes `sqrt(dx*dx + dy*dy)`. Keep the multiply
      order and the addition order fixed.
- [ ] `scripts/anchor_sim.gd:142-147`: the identical expression via `sqrt()`, never
      `Vector2.length()` and never `distance_to` — those are float32 (decision 030's surviving
      half).
- [ ] `tools/validate/validate_data.py:98-100`: replace the diagonal rejection with a segment
      **minimum length** check and an in-grid check. Keep rejecting zero-length segments.
- [ ] The path-tile expansion at `:104-112` assumes axis-aligned segments to enumerate occupied
      tiles for the slot-on-path test. A diagonal segment needs a proper rasterisation (a
      supercover walk), and it must agree with whatever free placement (PLC-01) uses for its
      `lane_half_width` test, or a slot is legal to the validator and illegal to the game.
- [ ] Re-grade all 24 anchors and diff. **The diff will be large and that is expected.** Record
      the before/after grade table in the issue and in `docs/STATE.md`.
- [ ] Sweep every anchor whose grade moved out of band and re-tune. Budget this properly: it is
      the campaign, not a change.
- [ ] Re-run parity, on the Windows build as well as Linux.
- [ ] `docs/DECISIONS.md`: supersede the geometry half of decision 030 with a new entry citing
      the probe, and state that `Vector2` remains banned in the rules.

## Acceptance criteria

- The `sqrt` probe is re-run and pasted for both Godot builds, and both report 0 mismatches
  out of 100,000.
- Parity passes 864/864 on **both** Godot builds.
- All 24 anchors are re-graded and every one is inside its intended band, with the sweeps that
  got them there recorded.
- `validate_data.py` accepts a diagonal lane and still rejects a slot sitting on one.
- No `Vector2` appears in either rule file's path maths.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt          # expected to be large; attach it
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/sweep.py --act 2 --jobs 8
.venv/bin/python tools/density.py
```

## Risks / gotchas

- **The parity gate does not test the binary the owner plays** (PRD risk 2). Fix or at least
  characterise that before relying on cross-platform `sqrt`.
- `atan2`, `sin`, `cos`, `tan`, `pow`, `log`, `exp` remain **banned** — Windows Godot diverges
  from CPython and Linux Godot at 0.031%–4.32% depending on the function. A diagonal lane is
  no reason to reach for an angle; a firing arc is `dot(normalise(v), facing) >= cos_half`
  with the cosine authored as a constant in `data/towers.json` (PRD §2.1).
- Every wave's pacing changes, so every "this anchor is tuned" claim in `docs/STATE.md` and
  every sweep result on disk becomes stale simultaneously.
- The slot-on-path tile expansion is easy to get subtly wrong for a diagonal and the symptom is
  a slot the validator allows and the player cannot build on.

## Files likely touched

- `sim/content.py`, `scripts/anchor_sim.gd`
- `tools/validate/validate_data.py`
- `data/anchors/*.json` (retuning), `docs/STATE.md`, `docs/DECISIONS.md`
