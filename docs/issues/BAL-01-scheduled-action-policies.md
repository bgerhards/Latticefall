id: BAL-01
title: Scheduled-action policies in sim/engine.py — a grading player that can press a button
labels: phase-3, tooling, design, risk
blocks: BAL-04
milestone: E7 Balance
---
## Problem

**This is the largest hole in the project** (LF-094, blocker; PRD §3 E7). `sim/engine.py`
expresses none of speed, call-wave, the kill chain, the three bindstone abilities, targeting
priority, veterancy or the recovery draft — so **all 24 anchor grades in `docs/STATE.md`
describe a player who uses none of them**. That was an acceptable floor when `sell()` was the
only unmodelled verb (decision 033); it is not acceptable now that a third of the game lives in
`data/tuning.json` and has never been graded. `docs/STATE.md` says it plainly: *"the abilities
are the most likely thing in the game to be accidentally overpowered"*.

The plug-in point already exists. `Sim.run` builds a `(time, item)` list and sorts it with a
total order before dispatch (`sim/engine.py:385-390`), and the prep phase already calls
`_try_build()` then `_shed_load()` at a known point (`sim/engine.py:381-382`). A policy
*script* is the same shape: a deterministic sorted list of `(time, verb, args)` merged into
the same dispatch. And the verbs already exist on the GDScript side —
`anchor_sim.gd` has `set_overcharge()` (line 192), `set_shutter()` (201),
`set_veterancy_ranks()` (214), `sell()` (314), `upgrade()` (347) and `fire_surge()` (605).
They have no Python counterpart, which is the whole gap.

**Reactive agents are explicitly out of scope** (PRD §8) — they threaten determinism, which is
the property that makes every balance claim here falsifiable.

## Tasks

- [ ] Add an optional `schedule: list[tuple[float, str, dict]]` to `Policy`
      (`sim/engine.py:120-148`), defaulting to `None`. Sort it once at construction with a
      **total** order `(time, index)` — a policy whose two actions share a timestamp must
      dispatch in the authored order on both runtimes, and `sim/engine.py:389` already shows
      why an under-specified sort key is a parity bug waiting.
- [ ] Decide and document the time base: schedule times are **seconds of sim time** (`self.t`),
      not frames and not wave-relative. Wave-relative would be more readable and is a second
      source of truth about when a wave starts; state the rejection.
- [ ] Add a dispatch point in `_tick_once()` (`sim/engine.py:426-434`) that drains every
      scheduled action whose time has passed, **before** `_step()`, so an action taken at time
      *t* affects the tick at *t* on both implementations.
- [ ] Implement the verbs in `sim/engine.py`, each mirroring `scripts/anchor_sim.gd` exactly and
      each citing the GDScript line it mirrors in a comment:
      - [ ] `speed` — the pacing multiplier. Note that the rules tick at `DT = 1/30`
            (`sim/engine.py:31`) and the speed control multiplies *ticks per wall second*, so in
            a headless sim it must be a **no-op on outcomes** unless it changes something the
            rules see. Prove that with a byte-identical run before claiming it is modelled.
      - [ ] `call_wave` — ends the lead-in early and converts the remaining seconds to funds at
            `pacing.call_bonus_per_sec` from `data/tuning.json`.
      - [ ] `ability` — `surge`, `overcharge`, `shutter`, mirroring `fire_surge()`,
            `set_overcharge()` and `set_shutter()`.
      - [ ] `target_mode` — per-emplacement targeting priority, mirroring the `match` at
            `anchor_sim.gd:496`.
      - [ ] `sell` / `upgrade` / `set_online` — mirroring `anchor_sim.gd:314`, `:347`, `:309`.
      - [ ] `build` — an explicit build at a named slot, so a scenario can express a board
            `_try_build` would never produce.
- [ ] Add veterancy and the kill chain to the tick, mirroring `anchor_sim.gd`'s
      `_veteran_rank()` (line 232) and the chain window from `tuning.pacing.chain_window_s`.
      These are **not** scheduled actions — they are rules that fire whether or not anyone
      presses anything, so they must exist in both files unconditionally or a graded run and a
      played run are different games.
- [ ] Load `data/tuning.json` in `sim/content.py` and expose it as typed values. It is content
      and must validate ({{PRC-10}}).
- [ ] **Mirror the schedule dispatch into `scripts/test/parity.gd`.** Its `_policies()`
      (line 63) already mirrors `standard_policies()` including order; the schedule must be
      mirrored the same way, and `_run()` (line 108) must drain it at the same point in the
      tick.
- [ ] **Prove the no-schedule path is byte-identical.** Run the full grader before and after and
      diff the JSON: every one of the 24 anchors × 3 difficulties × 12 policies must produce an
      identical `Outcome`. If a single field moves, the change is wrong — existing grades stop
      being comparable and `docs/STATE.md`'s grade table becomes a lie.
- [ ] Re-run the 864-run parity check and confirm it is still identical.
- [ ] Add two or three scheduled policies to `standard_policies()` (`sim/engine.py:441`) —
      e.g. `surge-on-peak`, `overcharge-greedy`, `call-early` — and mirror them in
      `parity.gd`. Adding policies grows the parity matrix; measure the new wall clock and
      confirm {{PRC-05}}'s hash gating and sharding absorb it.
- [ ] Consider expressing the schedule in {{PRC-12}}'s scenario file format so one file can
      drive both the engine and the reference sim; if the formats diverge, say why in the
      docstring.
- [ ] Update `docs/STATE.md`'s "What does not exist" section — it currently says the grades
      describe a player who presses nothing — and regenerate the grade table.
- [ ] Add a `docs/DECISIONS.md` entry: grading policies may carry a deterministic schedule;
      reactive agents are rejected, with determinism as the reason and PRD §8 as the reference.

## Acceptance criteria

- With no schedule, `sim/run.py --jobs 8` produces JSON **byte-identical** to the same command
  on `main`, for all 24 anchors and all three difficulties.
- `rules parity` reports 864 (or the new count) runs identical.
- A scheduled `surge` policy visibly changes an outcome — at least one anchor where the
  scheduled policy wins and the equivalent unscheduled one does not.
- Two scheduled actions at the same timestamp dispatch in authored order, identically in Python
  and GDScript — assert it with a deliberate same-time pair in a parity case.
- `data/tuning.json` values reach `sim/engine.py`: changing `pacing.call_bonus_per_sec` changes
  a `call_wave` policy's `spend`/`funds` and nothing else.
- Every verb implemented in `sim/engine.py` has a comment naming the `anchor_sim.gd` line it
  mirrors.

## Verification

```bash
.venv/bin/python -m sim.run --jobs 8 --json > /tmp/grades-after.json     # vs the same on main
diff <(jq -S . /tmp/grades-before.json) <(jq -S . /tmp/grades-after.json)   # must be empty
.venv/bin/python tools/test_parity.py                                    # "parity ok — N runs identical"
.venv/bin/python -m sim.run --anchor anchor-13 --json | jq '.[].winning_policies'
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **Parity is the invariant that outranks the pillars** (PRD §1). Every verb added here moves
  in `sim/engine.py` **and** `scripts/test/parity.gd`, and any rule (veterancy, kill chain) also
  in `scripts/anchor_sim.gd`, or it does not ship.
- **Godot compares `Dictionary` by value; Python compares by identity** — LF-055 is the
  precedent, and it stayed latent until two identical units shared a tick. Any new
  identity-sensitive comparison in a verb (e.g. "sell *this* emplacement") must be by index.
- **The safe operation set is `+ − × ÷ sqrt fmod floor min max` and comparisons.** No
  `atan2 sin cos tan pow log exp`, no `Vector2` (PRD §2.1, §4.2; measured Windows divergence up
  to 4.32% on `tan`). Ability falloff is currently `130 * lerp(0.35, 1, frac)` — `lerp` is
  linear and safe; confirm nothing in the tuning maths reaches for a banned op. {{BAL-07}} makes
  this mechanical.
- **`set_online()` is not reusable for suppression** (PRD §3 E5) — it is an unconditional write
  reachable from a player click. If a scheduled verb uses it, a suppressed gun could be switched
  back on.
- **Anything in `data/tuning.json` is inert unless the player acts, by design** (the file's own
  `note`, decision 033). Modelling the abilities in Python is exactly the moment that contract
  changes; the veterancy/kill-chain half must land in `anchor_sim.gd` too or the two rule files
  diverge silently.
- The grade table in `docs/STATE.md` is a floor. Once policies can press buttons, a level that
  "grades clean" may be trivially clean — {{BAL-04}} is where that is reckoned with, and the
  sweep's `PRESSURE_FLOOR` (`sim/run.py:36`) is already a dead check (LF-054).
- Parity wall clock is 542 s today and grows with the policy count. Land {{PRC-05}} first or
  every commit pays for it.

## Files likely touched

- `sim/engine.py`, `sim/content.py`, `sim/run.py`
- `scripts/test/parity.gd`, `scripts/anchor_sim.gd` (veterancy / kill chain only)
- `data/tuning.json`, `data/schema/tuning.schema.json`
- `docs/STATE.md`, `docs/DECISIONS.md`, `backlog.json`
