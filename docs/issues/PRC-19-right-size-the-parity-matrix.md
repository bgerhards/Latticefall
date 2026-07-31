id: PRC-19
title: Measure which of the 1152 parity runs actually discriminate, before scale multiplies them
labels: phase-1, tooling, perf, risk
depends: PRC-05
milestone: E1 Process
---
## Problem

Testing-audit finding #4. `tools/test_parity.py` runs `standard_policies()` (16 policies) ×
24 anchors × 3 difficulties = 1152 simulations through both rule engines, ~594–664s measured —
the single most expensive thing in the gate and, per the PRD's own framing, the guarantee that
matters most. `docs/PRD-THEATRE-SCALE.md` §7 risk 6 names the scaling risk directly: "parity
wall-clock at 10x units: 9 minutes → potentially hours", and `WAR-06` (raise the unit budget)
is already queued behind it. Nobody has yet measured *which* of the 1152 runs are doing real
work versus re-proving the same thing, so there is no evidence-based way to answer "is 1152
the right number" when that multiplication arrives.

**Direct experiment, run this session in a disposable `git worktree` (discarded after, never
touched the main tree):** introduced one genuine one-sided divergence — `BROWNOUT_SLOPE = 1.5`
→ `1.9` in `sim/engine.py` only, with no matching edit in `scripts/anchor_sim.gd`. Result:

```
tools/test_parity.py --anchor anchor-01   -> parity ok — 48 runs identical   (missed it)
tools/test_parity.py --anchor anchor-24   -> PARITY FAILED — 30 differences across 48 runs
                                              (lives_left, leaks, waves_cleared, even `won`
                                               itself flipped on one policy/difficulty cell)
```

This is the correct, expected result for *this* constant — anchor-01's capacity is never
pushed hard enough by any of the 16 policies to exercise the brownout slope, and anchor-24's
is. It is also direct, reproducible evidence that not every anchor pulls equal weight in the
comparison, and — until now — the project had no measurement of which ones do, only the
assumption embedded in running all 24 against everything.

Separately: `PRESSURE_FLOOR` in `sim/run.py` (a different file, the balance *grader* rather
than the parity harness, but built from the same `standard_policies()`) is already documented
dead — `LF-054`/`LF-131` measured 0 of 72 anchor×difficulty cells ever failing it, including
restricted to winning builds only. Independently re-measured during this audit
(`sim/run.py --json --jobs 4` over all 24 anchors × 3 difficulties): still 0 of 72, minimum
observed ratio 1.0 across every cell. This is not a new finding — it is already an
owner-decision item on the backlog (`LF-131`) — but it is the same shape of question this issue
asks about the parity matrix: a fixed policy/anchor product that nobody has measured for
actual discriminating power.

**A specific structural candidate.** Four of the sixteen policies —
`call-early`, `surge-on-peak`, `overcharge-greedy`, `veteran-crews` (BAL-01's scheduled/
opted-in set) — exist, by their own code comments in `sim/engine.py`, to prove a *mechanism*
reaches the grader identically in both engines: schedule dispatch order, ability magnitudes,
the veterancy ladder. That is a claim about shared dispatch code, which runs identically
regardless of which anchor invokes it — unlike `LF-145`'s targeting bug, which was genuinely
anchor/geometry-specific (anchor-09's flank lane) and would need broad anchor coverage to
catch. These four policies alone are 288 of the 1152 runs (25%) in service of a claim that has
not been shown to need per-anchor coverage at all.

## Tasks

- [ ] Write a small mutation-testing harness (a Python script, run only in a disposable
      worktree per this issue's own method) that applies a battery of deliberate one-sided
      single-constant changes to `sim/engine.py` — brownout slope/cap, a tower's damage/draw,
      a policy cap, the leak/`dist` comparison, a veterancy multiplier, and similar — and
      records, for each mutation, exactly which `(anchor, policy, difficulty)` cells actually
      go red.
- [ ] From that table, identify: (a) anchors that never discriminate any mutation tried
      (candidates for the same "does it earn its slice" scrutiny anchor-01 got here for the
      brownout case), (b) policies whose discrimination is fully subsumed by another policy's
      results, (c) whether the four BAL-01 scheduled/opted-in policies actually need all 24
      anchors to prove their mechanism claim, or whether a small fixed subset (e.g. one anchor
      per act) would catch the same class of bug.
- [ ] Cross-reference `docs/STATE.md`'s anchor-grades table
      (`distinct_winning_builds`/`distinct_builds_tried` per anchor/difficulty) as a cheaper,
      already-computed starting signal for how much a given anchor's 16-policy run actually
      diversifies, before running the full mutation sweep.
- [ ] **Do not cut anything on the strength of this issue alone.** Report the discrimination
      table and present any reduction as a decision for the owner, per the working agreement's
      reservation of genuinely-theirs calls — shrinking the one thing that "makes a balance
      claim in this project falsifiable" is exactly such a call, not an engineering judgement
      to make unilaterally.
- [ ] If the table supports a reduction the owner accepts, add an explicit, named
      `--subset {full,mechanism,geometry}`-style flag to `tools/test_parity.py` rather than
      silently shrinking the default — CI's tier-4 run must stay the full 1152 unless the owner
      says otherwise, with a smaller subset available as an explicitly weaker, faster local
      signal.
- [ ] Re-surface `LF-131`/`PRESSURE_FLOOR` alongside this report rather than duplicating it —
      it is the same "fixed matrix, unmeasured discrimination" question, already on the
      backlog, already needing the same owner decision (raise the floor, or delete and stop
      implying the grader tests pressure at all).

## Acceptance criteria

- A written discrimination table (this issue's closing report, or a generated
  `tools/parity_discrimination.json`) naming, for at least 10 deliberate single-constant
  mutations, which anchors and which policies caught each one.
- At least one concrete, evidence-backed recommendation about anchor or policy count is
  presented to the owner as an explicit decision, not silently acted on.
- `tools/test_parity.py`'s default full run (`--tier 4`, no `--anchor`/`--shard`) is unchanged
  — still all 1152 — unless the owner has explicitly accepted a specific, named reduction.

## Verification

```bash
# the experiment already run once for this issue's own evidence, reproducible in a fresh
# disposable worktree (never in the main tree — see CLAUDE.md's worktree/no-stash rules):
git worktree add /mnt/d/dev/lf-audit-wt HEAD
cd /mnt/d/dev/lf-audit-wt
sed -i 's/BROWNOUT_SLOPE = 1.5/BROWNOUT_SLOPE = 1.9/' sim/engine.py
/path/to/.venv/bin/python tools/test_parity.py --anchor anchor-01   # expect clean (miss)
/path/to/.venv/bin/python tools/test_parity.py --anchor anchor-24   # expect PARITY FAILED
cd /mnt/d/dev/Latticefall
git worktree remove /mnt/d/dev/lf-audit-wt --force
.venv/bin/python tools/reap.py
# once the harness exists:
.venv/bin/python tools/parity_discrimination.py --mutations 10 --jobs 8
```

## Risks / gotchas

- A mutation battery that never touches targeting/geometry code would falsely clear every
  anchor of `LF-145`'s class of risk — state the sample's own coverage limits plainly in the
  report; "never discriminated in this sample" is not "structurally cannot discriminate".
- Any repeat of the destructive experiment above must stay in a disposable worktree per this
  project's own constraint (never `git stash`/`reset`/`checkout --` in the shared tree) and
  must end with `tools/reap.py` — a wedged Godot from an aborted parity run survives its parent
  exactly as the ordinary gate's does.
- This is explicitly an investigation issue, not a cost-cutting mandate. `PRESSURE_FLOOR`
  (`LF-131`) is the cautionary precedent in the other direction: it was measured dead twice and
  *still* needs an owner decision rather than a unilateral delete, because deleting it also
  deletes the implied promise that the grader tests pressure at all.
- `tools/parity_costs.json`'s existing LPT bin-packing (PRC-05) optimises wall-clock for
  whatever the anchor/policy set already is — it has nothing to say about whether that set is
  the right one, and this issue should not be confused with re-tuning that machinery.

## Files likely touched

- `tools/parity_discrimination.py` (new)
- `tools/test_parity.py` (optional `--subset` flag, only if the owner accepts a reduction)
- `docs/BACKLOG.md` (an owner-decision item once the table exists, alongside the existing
  `LF-131` entry)
