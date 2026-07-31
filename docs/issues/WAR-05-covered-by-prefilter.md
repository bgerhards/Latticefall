id: WAR-05
title: LF-099 — _covered_by() iterates every emplacement per unit even when nothing has the effect
labels: perf, rules, engine, phase-2
milestone: E5 War
---
## Problem

`_covered_by(effect, x, y)` is called once per live unit per tick from the movement loop
(`scripts/anchor_sim.gd:449`, `sim/engine.py:313`), again per drain-carrying unit from
`bus_load()` (`anchor_sim.gd:260`, `sim/engine.py:216`), and again per candidate in the
targeting scan for the `reveal` test (`anchor_sim.gd:509-510`, `sim/engine.py:340`). Each call
iterates **every** placed emplacement and tests `eff.get("type") != effect` inside the loop
(`anchor_sim.gd:377-382`, `sim/engine.py:283-284`), so it costs O(towers) per unit even when no
emplacement on the board has that effect at all. LF-099 measured this as an unconditional
0.248–0.275 µs per tower-unit pair that persists even when only ~4% of towers are off
cooldown: **24,000 wasted iterations per tick at 60 emplacements and 400 units**, thirty times
a second, ninety at 3× speed.

## Tasks

- [ ] `sim/engine.py`: build per-effect lists of placed emplacements **once per tick**, at the
      top of `_tick_once()` — `slow`, `damp`, `reveal`, `restore` — preserving `self.placed`
      order exactly, so `_covered_by`'s `max` sees the same values in the same sequence.
- [ ] `sim/engine.py`: `_covered_by` iterates the pre-filtered list and returns `0.0`
      immediately when the list is empty. `max` over floats is order-independent for the
      values in play, but keep the order anyway — an unordered set here is a future parity
      bug for a reader who later changes `max` to "first wins".
- [ ] `scripts/anchor_sim.gd`: the identical structure, rebuilt in `tick()` before
      `bus_load()` runs, because `bus_load()` is the first consumer.
- [ ] Invalidate on every write to `placed` and to `online`: `build_at()`, `sell()`,
      `upgrade()`, `set_online()`, `_shed_load()` (Python), and whatever suppression
      {{WAR-10}} adds. Prefer rebuilding once per tick unconditionally over invalidation
      bookkeeping — it is O(towers) once instead of O(towers × units), and it cannot go stale.
- [ ] `capacity()` / `capacity_now()` also walks `placed` looking for `restore`
      (`anchor_sim.gd:274-279`, `sim/engine.py:200-202`); reuse the same pre-filtered list.
- [ ] Extend `tools/bench_tick.py` with a "no support emplacements on the board" case and a
      "board is half dampers" case, and record ms/tick for both, before and after.
- [ ] Re-run the 864-run parity set.
- [ ] Re-grade all 24 anchors; the diff must be empty. This is a pure filtering change — every
      value returned must be identical.
- [ ] Close LF-099 in `docs/BACKLOG.md` with the measured numbers.

## Acceptance criteria

- With no support emplacement placed, `_covered_by` performs zero per-emplacement iterations
  per unit (assert by counting in the benchmark).
- `.venv/bin/python -m sim.run --jobs 8` is byte-identical to before.
- Parity 864/864.
- The pre-filter is rebuilt at a single point in each file, and that point is named in a
  comment in the other file.

## Verification

```bash
.venv/bin/python tools/bench_tick.py --units 400 --towers 60 --no-support
.venv/bin/python tools/bench_tick.py --units 400 --towers 60 --half-dampers
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
```

Proof to paste: both benchmark before/after lines, the empty `diff`, parity's 864/864.

## Risks / gotchas

- **`max` order matters more than it looks.** `_covered_by` returns the *best* value, and
  today the "best" is resolved by `max`/`maxf` over a `placed`-ordered walk. Preserve the walk
  order so a later change to the reduction cannot silently diverge between languages.
- The `restore` effect is read by `capacity()`, which is read by `brownout_penalty()`, which
  scales `rate`, which scales every cooldown. A stale restore list is not a rounding error, it
  is a different game.
- Rebuild timing: `bus_load()` runs before `_step()` in both `tick()` and `_tick_once()`. Build
  the lists before `bus_load()` or the first tick of a wave reads an empty filter.
- Godot `Dictionary.get("type", "")` returns a `Variant`; the existing code compares it
  untyped at `anchor_sim.gd:381` and with `String()` at `:278`. Normalise to one form while
  you are in here, and check the parse output — a silently mistyped comparison is the
  highest-frequency failure in this project (PRD §3 E1).

## Files likely touched

- `sim/engine.py`
- `scripts/anchor_sim.gd`
- `tools/bench_tick.py`
- `docs/BACKLOG.md`
