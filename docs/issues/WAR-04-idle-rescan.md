id: WAR-04
title: LF-098 — an idle emplacement re-scans every unit every tick, forever
labels: perf, rules, engine, phase-2
milestone: E5 War
---
## Problem

When an emplacement comes off cooldown and finds nothing in range, both engines take the
empty-target branch **without resetting the cooldown** — `scripts/anchor_sim.gd:526-530`
(`p.erase("aim"); continue`) and `sim/engine.py:345-346` (`if target is None: continue`). The
cooldown has already gone `<= 0` at that point, so the next tick scans all U units again, and
the tick after that, indefinitely. LF-098 measured the cost: **2.19× at 8 emplacements /
32 units, rising to 2.87× at 100 / 800**, against the same board with targets in range. This
is precisely the shape a large multi-lane board produces, because most guns are idle most of
the time — which makes it a scale defect rather than a curiosity.

## Tasks

- [ ] Decide and write down the re-scan interval. A fixed **one-tick** retry (set
      `cooldown = DT` on the empty branch) is the smallest change that removes the unbounded
      re-scan while keeping "acquires the frame a unit enters range" true. A longer interval
      buys more but adds a visible acquisition delay and changes which tick a gun fires on,
      which **is** a rule change and re-grades the campaign. Prefer the one-tick retry; record
      the rejection of the longer interval and why.
- [ ] `sim/engine.py:345-346`: on `target is None`, set `p.cooldown = DT` (not
      `p.tower.fire_interval`) and `continue`.
- [ ] `scripts/anchor_sim.gd:526-530`: the identical change, keeping `p.erase("aim")` — that
      line is presentation-only and must stay (it is what points an idle gun back down the
      lane).
- [ ] Prove it does not change **which** unit is selected: the change only affects *when* the
      scan runs, and with a one-tick retry the scan runs on the same ticks it does today
      (`cooldown -= DT * rate` with `cooldown = DT` and `rate <= 1.0` means the next tick may
      leave `cooldown > 0` under brownout). **This is the trap** — under a brownout,
      `rate < 1.0`, so `DT * rate < DT` and the retry takes two ticks instead of one, which
      *is* a behaviour change. Either set the retry to `DT * rate` (both files, same
      expression, same operand order) or set a separate `next_scan_t` compared against `t`.
      Choose one, implement it identically, and say in both files why.
- [ ] Extend `tools/bench_tick.py` with an all-idle case (guns placed out of range of every
      unit) and record the before/after ms/tick against LF-098's 2.19× and 2.87× figures.
- [ ] Re-run the 864-run parity set.
- [ ] Re-grade all 24 anchors and diff. **A non-empty diff means the fix changed a rule** —
      stop and reconsider the retry expression rather than re-baselining the grades.
- [ ] Close LF-098 in `docs/BACKLOG.md` with the measured numbers.

## Acceptance criteria

- An emplacement with nothing in range performs at most one candidate scan per tick, and the
  all-idle benchmark case shows the 2.19×/2.87× penalty gone (≤ 1.15× against the
  targets-in-range board).
- `.venv/bin/python -m sim.run --jobs 8` is byte-identical to before.
- Parity 864/864, including at least one anchor whose sweep spends significant time browned
  out (the `rate < 1.0` case).
- The retry expression is character-identical in both files modulo language syntax.

## Verification

```bash
.venv/bin/python tools/bench_tick.py --units 400 --towers 60 --all-idle
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/sweep.py anchor-20 --jobs 8
```

Proof to paste: the all-idle benchmark before/after, the empty `diff`, parity's 864/864.

## Risks / gotchas

- **Brownout is the whole difficulty.** `rate = 1.0 - penalty` scales the cooldown decrement
  (`anchor_sim.gd:471`, `sim/engine.py:329`), so any constant retry value interacts with the
  penalty. Getting this wrong makes idle guns acquire one tick later during a brownout, which
  is a real rules change disguised as a performance fix.
- Overcharge multiplies `rate` above 1.0 (`anchor_sim.gd:442-443`) — GDScript-only, never seen
  by a parity run, but it means `rate` can exceed 1.0 in the shipped game and the retry
  expression must not assume otherwise.
- Do not fold this into {{WAR-03}}. The spatial hash changes *how many* candidates are
  scanned; this changes *how often*. Landing them together makes an empty grade diff
  uninformative about either.

## Files likely touched

- `sim/engine.py`
- `scripts/anchor_sim.gd`
- `tools/bench_tick.py`
- `docs/BACKLOG.md`, `docs/DECISIONS.md`
