id: BAL-02
title: Capped-core policies so a graded build can be mixed
labels: phase-3, tooling, design
blocks: BAL-04
milestone: E7 Balance
---
## Problem

Every `Policy` is a total preference order and `_try_build` fills every free slot greedily from
the top (`sim/engine.py:242-264`), so **a graded build is all-of-one-thing**. "Three pulse
turrets in front of an ion lance" is not in the search space (LF-095, superseding LF-053).
That makes design claims unfalsifiable by the harness that exists to test them:
`data/enemies.json` says the Picket exists *"so the pulse turret earns its slot again"*, and
`docs/STATE.md` records flatly that **"escort value is asserted, not proved"**.

The partial fix already in the file shows the shape of the real one. `Policy.caps`
(`sim/engine.py:143-146`) exists because "intel-first" built four scan relays and no guns, and
no policy could express the one sensible board for anchor-02. Caps solve *support* saturation;
they do not solve **composition**, because once the capped tower is exhausted the loop still
fills the remainder from a single next-best entry.

The same root cause makes `--autoplay` unable to show a mixed board (LF-072): anchor-09,
anchor-13, anchor-16 and anchor-21 all built out one tower type and never touched
`flak-array` or `mortar-emplacement` even many waves in — which is why `--build <tower-id>`
had to be added to `main.gd` just to photograph a mortar.

This is a **prerequisite for grading a 60-emplacement board meaningfully** (LF-095): whatever
free placement and multi-lane do to slot counts, an all-of-one-thing board tells you nothing.

## Tasks

- [ ] Define the policy shape: a `core` (tower id + count) plus a `fill` preference, e.g.
      `Policy("lance-core", core=("ion-lance", 2), fill=["pulse-turret", "arc-node"])`. Keep
      `caps` working — it is a different question (support saturation) and existing policies
      depend on it.
- [ ] Extend `_try_build` (`sim/engine.py:242`) to satisfy the core first, then fill, then stop.
      Preserve the existing early-exit semantics (`else: return` when nothing affordable fits)
      exactly — that is what stops an infinite loop on a full board.
- [ ] Keep the **slot** decision separate from the **tower** decision. `_slot_priority()`
      (`sim/engine.py:225-240`) ranks by squared distance to the path and returns the nearest
      free slot; a core tower probably wants the *best* slot and fill towers the rest, so make
      the ordering explicit rather than incidental. {{BAL-03}} replaces the ranking itself.
- [ ] Mirror the whole thing in `scripts/test/parity.gd` — `_policies()` (line 63) and
      `_try_build()` (line 196) already mirror the Python structure field for field, including
      the `(rank, id)` tie-break, and must continue to.
- [ ] Add a first set of capped-core policies to `standard_policies()`
      (`sim/engine.py:441-489`): a lance core with turret fill, a mortar core with flak fill,
      a damper core with turret fill (Act II), a restorer core with turret fill (Act III).
      Choose them to test specific design claims, and write the claim being tested into each
      policy's comment — the existing policies already do this and it is the reason the file is
      readable.
- [ ] Measure the cost: each policy multiplies the grader **and** the 864-run parity matrix.
      Record the new run count and wall clock, and confirm {{PRC-05}}'s hash gating and
      cost-balanced sharding keep the gate usable.
- [ ] Fix the sibling defect in the engine while the claim is testable: `--autoplay`'s
      `_autobuild_step` in `scripts/anchor_view.gd` has the same all-of-one-thing behaviour
      (LF-072). Give it a capped-core mode so a screenshot can show a mixed board without
      `--build`.
- [ ] Re-grade and inspect: does any anchor's set of winning builds change qualitatively? A
      capped-core policy winning where no total-order policy did is the evidence that the
      search space was too small.
- [ ] Revisit `PRESSURE_FLOOR` in `sim/run.py:36` — it is a **dead check**: peak load ratio is
      ≥100% of capacity on all 24 anchors × 3 difficulties, and ≥75% everywhere even with the
      greedy-overdraw policy excluded, so "the player is pressed against capacity" can never
      fail (LF-054). Mixed builds are exactly the population where it might start
      discriminating; either measure pressure on winning builds only or raise the floor.
- [ ] Update `docs/STATE.md`'s "Escort value is asserted, not proved" paragraph with what is now
      proved, and close LF-095 and LF-053.

## Acceptance criteria

- `sim/engine.py` can express "N of tower A, remainder tower B" and at least four such policies
  are in `standard_policies()`.
- At least one anchor reports a winning build containing **two different weapon ids** — check
  the `built` list in the grader's JSON, which currently never does on a mixed-roster anchor.
- The escort claim becomes falsifiable: state a specific claim (e.g. "on anchor-13 the Picket
  makes a turret-fill board beat a pure-lance board") and record whether the grader confirms or
  refutes it. **Either answer is a pass for this issue**; an unfalsifiable one is not.
- `rules parity` is identical across the new policy set (Python vs GDScript).
- Existing unscheduled, non-core policies produce byte-identical outcomes to `main` — the
  refactor must not move any published grade.
- `--autoplay` on anchor-13 produces a board with more than one tower id, verified by
  screenshot and by the `STATE` line.

## Verification

```bash
.venv/bin/python -m sim.run --jobs 8 --json > /tmp/grades.json
.venv/bin/python - <<'EOF'
import json
d=json.load(open('/tmp/grades.json'))
for a in d:
    for o in a.get('outcomes', a.get('results', [])):
        ids={b.split('@')[0] for b in o['built']}
        if o['won'] and len(ids)>1: print(a['anchor'], o['policy'], sorted(ids))
EOF
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/shot.py anchor-13 --out /tmp/mixed.png --frames 3600
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **The `(rank, id)` tie-break is load-bearing.** `sim/engine.py:168-171` sorts buildable
  towers by `(policy.rank(t.id), t.id)` because everything unranked shares rank 99, and Python's
  stable sort would hand back `towers.json`'s file order while GDScript hands back alphabetical
  — a parity failure that stayed hidden until Act II added three towers whose file order and
  alphabetical order finally disagreed. Any new ordering must be equally total.
- **A sweep proves nothing outside its grid** (`docs/STATE.md`), and `tools/sweep.py` prints
  its box next to the verdict for that reason. New policies change what "clean" means; do not
  re-tune anchors in this issue — that is {{BAL-04}}.
- **Check the harness before the level.** Seven times in this project the grader, the scorer,
  the sweep's grid or the transform — not the content — was what was broken (decisions 023,
  024, 028, 044). Confirm the new policies do what they say on a hand-checkable anchor before
  reading anything into a campaign-wide result.
- More policies means a longer parity run and a longer grade. 542 s of parity today, at 12
  policies; going to 16 is a third more. Land {{PRC-05}} first.
- Do not fold {{BAL-01}}'s schedules into this issue. Composition and timing are separate
  axes and mixing them makes the byte-identical regression test impossible to interpret.

## Files likely touched

- `sim/engine.py` (`Policy`, `_try_build`, `standard_policies`)
- `scripts/test/parity.gd` (`_policies`, `_try_build`)
- `sim/run.py` (`PRESSURE_FLOOR`)
- `scripts/anchor_view.gd` (`_autobuild_step` only)
- `docs/STATE.md`, `backlog.json`, `docs/BACKLOG.md`
