id: WAR-06
title: Raise the alive-unit budget to 250-400, and keep the fight legible at that count
labels: perf, design, ui, phase-2
depends: WAR-01, WAR-03, CAM-01
blocks: WAR-07
milestone: E5 War
---
## Problem

Peak screen presence today is **32 units** (PRD §1). The programme's first pillar is a
battlefield, and the target is **250–400 alive**. Two things break on the way there and
neither is the sim loop. First, `tools/density.py` and the gate's `wave density` check are
calibrated against acts that never exceed 32, so every act comparison is against a scale that
no longer exists. Second, `scripts/combat_fx.gd:19` caps the particle pool at `MAX_FX = 480`
with **oldest-evicted** replacement (`:133-139`, `:385-386`); at 300 units firing, decision
053's combat feedback — the thing that makes a hit legible — thrashes and degrades toward
nothing, because a single wave of impacts evicts the shot trails that produced them.

## Tasks

- [ ] Measure first. Run `tools/bench_tick.py` at 250, 300 and 400 alive with a realistic
      emplacement count and record ms/tick against the **5.6 ms** budget (3× speed, PRD §2.2).
      If {{WAR-03}} has landed this should be well inside it; the point is a committed number,
      not an assumption.
- [ ] Measure the *draw* side separately: PRD §2.2 rank 3 records `drawables()` rebuilt 4× per
      frame and an uncached tile loop costing 13.4 ms/frame at 64×64, with a **2.47×** measured
      fix available. Capture the frame time at 300 units before deciding anything about FX.
- [ ] Decide the FX policy and write it into `docs/DECISIONS.md`, superseding nothing in
      decision 053 but bounding it: FX becomes **budgeted per category** (shot trails, impacts,
      deaths, leaks) rather than one oldest-evicted pool, so a wave of impacts can never
      starve the shot trails, and the categories that carry rules information (a leak, a
      shielded ricochet) get a reserved floor. Record the rejected alternative — raising
      `MAX_FX` — and why: it moves the thrash point without removing it, and it is unbounded
      memory against an unbounded unit count.
- [ ] `scripts/combat_fx.gd`: implement the per-category budget, keeping the existing eviction
      inside each category. Keep `MAX_FX` as the total ceiling.
- [ ] Add a spawn-rate governor for purely decorative FX above a threshold unit count —
      decimate shot trails (draw every Nth) rather than dropping them entirely, so the board
      still reads as "everything is firing".
- [ ] `tools/density.py`: raise the reported bands and add a peak-alive column per act; update
      the gate's `wave density` check thresholds in `tools/check.py` to the new scale.
- [ ] Author one wave table that actually reaches 300 alive, as a fixture rather than shipped
      content, and grade it.
- [ ] Screenshot the 300-unit case at 100% and 200% interface scale and run the a11y audit on
      the same frame — the HUD's threat panel and the unit counters are the things that break
      when a number goes from two digits to three.
- [ ] Re-run `tools/test_parity.py` and record the wall clock. Risk 6 in the PRD register is
      parity time at 10× units going from 9 minutes to hours; if the fixture anchors push it
      past ~15 minutes, open a backlog item for a tiered parity set rather than accepting it.
- [ ] Update `docs/STATE.md` with the measured ms/tick, ms/frame and parity wall clock.

## Acceptance criteria

- 400 alive units with 60 emplacements ticks in ≤ 5.6 ms measured, and the figure is in
  `docs/STATE.md`.
- At 300 alive, no FX category is starved: the benchmark reports a non-zero live count for
  shot trails, impacts, deaths and leaks simultaneously.
- `tools/density.py` reports peak-alive per act and the gate's `wave density` check passes on
  the new bands.
- A 300-unit frame is captured at `--ui-scale 2.0` and `tools/validate/a11y.py` on that frame
  reports no new contrast or text-size failures.
- Parity wall clock is recorded and has not silently doubled.

## Verification

```bash
.venv/bin/python tools/bench_tick.py --units 400 --towers 60
.venv/bin/python tools/density.py
.venv/bin/python tools/shot.py anchor-XX --out /tmp/mass.png --ui-scale 2.0 --a11y /tmp/mass.json
.venv/bin/python tools/validate/a11y.py /tmp/mass.json --shot /tmp/mass.png --all
.venv/bin/python tools/test_parity.py
```

Proof to paste: the benchmark's ms/tick at 400, the density table, the a11y summary line, and
parity's 864/864 with its wall clock.

## Risks / gotchas

- **`MAX_FX` eviction is oldest-first and global.** Raising the unit count without touching it
  does not produce fewer effects; it produces effects that vanish mid-life, which reads as a
  rendering bug rather than a budget.
- The FX layer subscribes to presentation-only signals (`anchor_sim.gd:51-53`) and decision 055
  says a cosmetic layer may never take the playfield down with it. A per-category budget must
  fail closed — drop the effect, never the signal handler.
- `--shot` at 300 units on a large board is the case most likely to hit the occlusion stall
  LF-061 describes; go through `tools/shot.py`, which routes via `xvfb-run` (decision 052).
  Never launch a visible gate window while the owner is at the machine.
- Do not raise unit counts inside the parity fixtures to "test at scale". Parity is already
  83.6% of gate time.

## Files likely touched

- `scripts/combat_fx.gd`
- `tools/density.py`, `tools/check.py`, `tools/bench_tick.py`
- `data/anchors/` (one fixture wave table)
- `docs/DECISIONS.md`, `docs/STATE.md`
