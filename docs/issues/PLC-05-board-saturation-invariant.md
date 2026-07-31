id: PLC-05
title: A replacement board-saturation invariant, before free placement removes the denominator
labels: phase-2, rules, risk, tooling
blocks: PLC-01
milestone: E3 Placement
---
## Problem

**This is risk #1 in the PRD (§7, severity Blocker) and LF-107. {{PLC-01}} must not merge before
it.**

`tools/validate/validate_data.py:146-165` bounds an anchor's `capacity_mw` against
`len(doc["slots"]) * max_draw`: it errors when capacity covers every slot at maximum draw, and
warns above `SATURATION_WARN` (80%). Free placement deletes `slots`, and with no slots there is
**no denominator** — the guard has nothing to divide by and silently stops guarding.

That guard is not theoretical. `CLAUDE.md`, `docs/STATE.md` ("Traps that have already cost
time") and decision 048 all record the same event: a sweep was free to pay for heavier waves
with reactor capacity, anchor-24 reached **103% of what would run every slot at maximum draw**,
every anchor still graded clean, the validator said nothing until the last one tipped over, and
**the power decision the whole game is about had stopped existing on five levels**. The grader
cannot see this — a level with no decision in it is still winnable. Act I sits at 29-38% of
saturation; that band is the thing being protected.

Power as the real currency is the one genuinely good idea the PRD says everything else must
serve (§1), and invariant 3 in §4 is *"every board must have a capacity ceiling that makes 'what
do I leave switched on' a real question"*. Free placement is the change most likely to delete it
by accident.

## The three candidate denominators

**1. A hard emplacement cap** — `max_emplacements` authored per anchor. Denominator becomes
`max_emplacements * max_draw`, arithmetically identical to today with `len(slots)` renamed.
It is a *rule*, so it must exist in both engines and be enforced at build time. Downside: a
number the player can feel, and one more thing the HUD must explain.

**2. Buildable area ÷ footprint area** — count legal tiles (in bounds, off the lane by
`lane_half_width + footprint`), divide by the area one emplacement denies, times a packing
efficiency. Derived from geometry the game already has, no new rule, no new authored number.
Downsides: the packing constant has to be chosen and justified (hexagonal packing is 0.9069, but
the real bound is lower because the lane standoff carves the region), and it is an upper bound
the player cannot reach, so the guard is looser than today's.

**3. Funds** — the true limit is money: starting funds plus all obtainable bounties. Most honest,
but bounties depend on kills, kills depend on the build, and the guard would become a function
of the grader rather than of the data. Rejected for that circularity, but write the rejection
down.

**Recommendation: (1) as the rule, (2) as a second validator bound.** The cap is the only option
that gives a *player-visible* ceiling and a single integer both engines can enforce; the area
bound catches a cap authored absurdly high, which is exactly the drift that happened last time.

## Tasks

- [ ] Measure first. Print, for all 24 anchors, today's `len(slots)`, `max_draw`, `saturated`,
      and `capacity_mw / saturated`. That table is the calibration target — the replacement must
      reproduce Act I at 29-38% and keep every anchor under `SATURATION_WARN`.
- [ ] Add `max_emplacements` (integer, required) to `data/schema/anchor.schema.json` and author
      it into all 24 anchor files at today's `len(slots)`, so the change is provably neutral.
- [ ] Enforce it in **both** rule implementations: `scripts/anchor_sim.gd`'s `build_at()`
      (`:294-306`) refuses when `placed.size() >= max_emplacements`; `sim/engine.py`'s
      `_try_build()` (`:242-264`) stops on the same condition. `scripts/test/parity.gd:197-217`
      carries its own copy of the build loop and must move with them.
- [ ] Rewrite `validate_data.py:146-165` to use `max_emplacements * max_draw`, keeping the
      existing error/warn structure and the existing prose (it names Act I's 29-38% band, which
      is load-bearing context for whoever hits it).
- [ ] Add the area-derived second bound as a **warning**, not an error, naming both numbers when
      they disagree by more than 2x — a cap far above what the board can physically hold is a
      content bug even if capacity is fine.
- [ ] Fix LF-103 in the same function while it is open: the guard computes `max_draw` from base
      `draw_mw` only and never looks at `upgrade.draw_mw`, so upgraded draw is invisible to it.
      Latent today, live the moment the spectacle weapons land with steep upgrade draws.
- [ ] Move `tools/sweep.py`'s capacity bound (currently 70% of `len(slots) * max_draw`) onto the
      new denominator, or the sweep will re-create the original failure from the other side.
- [ ] Surface the cap in the HUD: an `N / M emplacements` readout. Sizes and colours from `Ui`,
      never literals (decisions 045, 046). Expect the a11y text-item count (182 today) to move.
- [ ] Re-grade all 24 anchors and confirm no verdict changes: `.venv/bin/python -m sim.run
      --jobs 8`.
- [ ] Write a `docs/DECISIONS.md` entry recording the chosen denominator and both rejected
      alternatives, referencing decision 048 rather than editing it (append-only).
- [ ] Update `CLAUDE.md`'s non-negotiables table and `docs/STATE.md`'s trap list, and close
      LF-107.

## Acceptance criteria

- `validate_data.py` errors on an anchor whose `capacity_mw >= max_emplacements * max_draw`,
  where `max_draw` accounts for upgrades.
- All 24 anchors report a saturation fraction inside the same band they report today (Act I
  29-38%, none above `SATURATION_WARN`), before free placement and after.
- `build_at()` returns `false` at the cap in `anchor_sim.gd`, `_try_build()` stops at the cap in
  `sim/engine.py`, and `parity.gd` agrees — 864 runs identical.
- `.venv/bin/python -m sim.run --jobs 8` reproduces all 24 verdicts as `ok` with unchanged
  distinct-winning-build counts.
- A deliberately over-capacity anchor (capacity raised past the cap) is a **red** validator run
  with a message naming the cap and the draw.
- `tools/sweep.py` refuses to propose a capacity above the new bound.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py            # clean on all 24
.venv/bin/python -m sim.run --jobs 8                        # every verdict ok, unchanged
# deliberate break
python - <<'EOF'
import json,pathlib
p=pathlib.Path('data/anchors/anchor-24.json'); d=json.loads(p.read_text())
d['capacity_mw']=d['max_emplacements']*40; p.write_text(json.dumps(d,indent=2))
EOF
.venv/bin/python tools/validate/validate_data.py            # must be RED, naming the cap
git checkout data/anchors/anchor-24.json
.venv/bin/python tools/check.py                             # incl. 864-run rules parity
.venv/bin/python tools/reap.py
```

Proof is the pair — clean before the break, red after, clean after the revert — plus the
saturation table before and after showing the same fractions.

## Risks / gotchas

- **The grader cannot see this failure.** A level with no power decision in it is still winnable
  and still grades `ok`. Do not accept "the sweep is green" as evidence; the evidence is the
  saturation table.
- **`max_emplacements` is a rule, so it is parity-exposed.** Three files move together:
  `anchor_sim.gd`, `sim/engine.py`, `scripts/test/parity.gd`. A cap enforced in two of the three
  is an 864-run failure, and parity is 83.6% of gate time — get it right before running it.
- **The briefs speak their own numbers.** Control reads the bus figure aloud in every anchor and
  `tools/say_capacity.py` reconciles them; sixteen briefs had drifted once already. If any
  capacity moves, re-run it — the gate checks it.
- **A backlog or docs line that quotes a banned term turns the nomenclature check red.** Describe,
  never repeat.
- Do not let the area bound become the *only* bound. It is an upper bound the player cannot
  reach, so on its own it is a looser guard than the one being replaced.

## Files likely touched

- `tools/validate/validate_data.py` (`:146-165`)
- `data/schema/anchor.schema.json`, `data/anchors/anchor-01.json` … `anchor-24.json`
- `scripts/anchor_sim.gd` (`build_at`), `sim/engine.py` (`_try_build`),
  `scripts/test/parity.gd`
- `sim/content.py` (the `Anchor` dataclass and `load_anchor`)
- `tools/sweep.py`, `scripts/hud.gd`
- `docs/DECISIONS.md`, `CLAUDE.md`, `docs/STATE.md`, `docs/BACKLOG.md`
