id: PRC-20
title: The session wrap must finish in five minutes, and must never block the owner for more than one
labels: phase-0, process, tooling, risk
milestone: E1 Process
---
## Problem

**The owner does not have thirty-two minutes at the end of a session, and the wrap took that
and was still not finished.** This is the highest-priority item in the programme and comes
before any other work.

Measured during the wrap of 2026-07-31, at which point roughly 20 of >32 minutes had passed
with the final commit and journal still outstanding:

| step | cost |
|---|---|
| `rules parity` | **~13 min** — 1440 runs, plus a Windows leg |
| `accessibility` | ~52 s |
| `scenarios pass` | ~37 s |
| `game renders` | ~11 s |
| the other ~26 checks | ~2 min combined |
| backlog, STATE rewrite, journal, commits | seconds each, but serialised behind all of the above |

Total tier 4 on that run: **1,755,759 ms — 29 minutes.**

Three things are wrong, and only one of them is the check being slow.

**1. The wrap runs the full gate unconditionally, and it is the wrong place to do it.** Work
now lands through pull requests, each gated at tier 2 or tier 4 before it opens, with CI
running tier 1 on top. By the time the wrap starts, everything on `main` has already been
verified. The wrap's full gate is largely re-proving what the PR flow just proved.

**2. The fix already exists and is not being used on this path.** `PRC-05` added a
content-hash cache — parity skips entirely when nothing that can affect it has changed,
reporting `skip` with reason `cached` rather than `ok` — plus `--shard I/N` with LPT
bin-packing. A wrap that shells `tools/check.py` with no flags pays full price even when the
digest is unchanged.

**3. Everything is serialised behind the slowest check.** The backlog groom, the STATE
rewrite, the journal entry and the commits do not depend on the gate's result until the very
last step. They are pure wall-clock spent waiting.

The cost is also growing, and growing *because of good work*: parity was 864 runs at ~9
minutes in the morning and 1440 by the evening, because `BAL-02` added four capped-core
policies and `BAL-01` four scheduled ones. Every policy that makes grading more truthful
multiplies the matrix. `#85` already covers right-sizing it — 288 of the 1440 runs exercise
one shared dispatch mechanism across all 24 anchors.

## The two numbers this issue is judged against

- **Owner-blocking time: under 1 minute.** The wrap must reach "you can walk away" fast.
- **Total wall clock: under 5 minutes** on an unchanged digest, and it must be *honest* about
  what it did and did not run — a fast wrap that quietly skipped verification is strictly
  worse than a slow one.

## Tasks

- [ ] **Reorder the wrap so the gate is not on the critical path.** Start the gate first, in
      the background, then do the backlog groom, decisions, STATE rewrite and journal entry
      while it runs. Join before the commit. Nothing before the commit depends on its result.
- [ ] **Default the wrap to `--tier 2` plus a parity digest check**, not tier 4. Justify it in
      the skill itself: every commit on `main` arrived through a gated PR, so the wrap is
      verifying the *wrap's own edits* — STATE, backlog, chronicle — which touch no rules and
      no assets. Add `--tier 4` as an explicit `--release` path for when it is actually
      warranted.
- [ ] **Make the cache visible.** If parity's digest is unchanged, the wrap should say so in
      one line and move on. Today it is impossible to tell a cached skip from a slow run
      without watching. Print the digest and what it covers.
- [ ] **Fail loudly if the fast path is taken when it should not be.** If the wrap's own diff
      touches `scripts/anchor_sim.gd`, `sim/**`, `data/**` or `assets/**`, the fast path is
      wrong and the wrap must escalate to tier 4 by itself rather than trusting the operator
      to notice.
- [ ] Measure the result and record it. A claim that the wrap is now fast is exactly the kind
      of claim this project requires to be falsifiable.
- [ ] Update `.claude/skills/session-wrap/SKILL.md` to the new order, and say plainly in it
      what the fast path does not check.

## Acceptance criteria

- A wrap with no rules, data or asset changes completes in **under 5 minutes** wall clock and
  blocks the owner for **under 1 minute**, measured with `/usr/bin/time` and recorded.
- A wrap that *does* touch the rules escalates to tier 4 on its own, without being asked.
- The output states which tier ran and, if parity was skipped, the digest and why — so a
  reader can tell a legitimate skip from an accidental one.
- `tools/reap.py` still reports clean at the end. The speed-up must not come from skipping
  the reap, which is the step that costs real money when it is missed.

## Verification

```bash
/usr/bin/time -f "%E wall" .venv/bin/python tools/check.py --tier 2
.venv/bin/python tools/test_parity.py --json | head -3      # digest, cached or not
git diff --name-only HEAD~1 | grep -E '^(sim|scripts|data|assets)/' && echo "must escalate"
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **A fast wrap that skips verification is worse than a slow one.** The whole value of this
  project's tooling is that a claim is falsifiable; the fast path must be honest about its
  own scope, not merely quiet.
- **Tier 4 exceeds the 600 s command ceiling** and gets backgrounded, so "run the gate" is
  already not a blocking call — the wrap must handle a backgrounded gate deliberately rather
  than by accident.
- The parity digest deliberately covers the Godot binary's version string, so an engine
  upgrade invalidates the cache with no repo diff. That is correct and must not be optimised
  away.
- Do not speed this up by weakening what a pull request runs. The wrap can be cheap *because*
  the PR was not.

## Files likely touched

- `.claude/skills/session-wrap/SKILL.md`
- `tools/check.py`, `tools/test_parity.py`, `tools/session.py`
- `docs/STATE.md`
