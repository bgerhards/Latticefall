---
name: session-wrap
description: Close out a Latticefall work session so the next one can resume cleanly — update state, groom backlog, record decisions, run the gate, commit. Use when finishing a task, before a long pause, or when context is about to be summarized.
---

# Session wrap

The next session has no memory of this one. Everything worth keeping has to be written
down now, in files, not left in the conversation.

**PRC-20: this wrap is judged on two numbers — owner-blocking time under 1 minute, total
wall clock under 5 minutes on an unchanged parity digest — and it must be *honest* about
what it did and did not run. A fast wrap that quietly skipped verification is strictly
worse than a slow one.** The design that gets there: start the gate first and in the
background, do everything else while it runs, join right before the commit. Nothing before
the commit depends on the gate's result, so nothing before the commit should wait for it.

## Why the default is `--tier 2`, not the full gate

Work now lands through pull requests, each gated at tier 2 or tier 4 by the `ship` skill
before it opens, with CI running tier 1 on top. By the time a wrap starts, whatever landed
has already been verified once. The wrap's own edits are `docs/STATE.md`,
`docs/BACKLOG.md`, `docs/DECISIONS.md` and `docs/chronicle/**` — none of which touch rules,
sim, or assets — so the wrap re-running the full ~9-minute, 28-check gate is mostly
re-proving what the PR flow just proved. Tier 2 (~21-25s: python/GDScript/JSON syntax,
game data, sim determinism, sprite/atlas/audio integrity, `godot boots`) is honest coverage
for what a wrap actually changes.

**What the fast path does NOT check, and this must be said out loud every time it is
used:** `scenarios pass`, `game renders`, `menu renders`, `accessibility` (tier 3), and —
the expensive one — `rules parity` and `rules parity (windows)` (tier 4, the GDScript-vs-
Python comparison this project's balance claims depend on). A tier-2 wrap is not a claim
that the rules still agree with themselves; it is a claim that the wrap's own edits did not
break anything mechanical.

Reach for `--release` (tier 4) instead when the wrap is not following a gated PR — a
hand-run wrap after a rebase, before an actual release, or any session where you want the
full nightly-grade proof regardless of what changed.

## Do this in order

1. **Reap every process this session started**, report-only first: `.venv/bin/python
   tools/reap.py`. Confirm nothing of yours is already a stray before starting anything
   new — a lease-classified `own-session`/`sibling` process from a fanned-out agent should
   not be killed here (LF-133; only the coordinator runs `--kill`, once every agent has
   reported). This is a read, not the final reap — that is the last step.

2. **Decide the tier and check the parity cache, before starting anything expensive.**

   ```
   .venv/bin/python tools/wrap_gate.py            # decide from the session's own diff
   .venv/bin/python tools/wrap_gate.py --release   # force tier 4
   ```

   This prints which tier the gate should run at and why, and the parity digest's cache
   state (hit or miss, first 12 hex chars, what it covers) — without running the 1152/1440-
   run comparison itself. Two things force tier 4 on their own, no flag required:

   - **Path escalation.** The session's own diff (everything uncommitted, plus every commit
     this branch carries that `main` does not) touches `scripts/anchor_sim.gd`, `sim/**`,
     `data/**`, or `assets/**` — exactly the files a tier-2 wrap cannot re-verify.
   - **Digest escalation.** `tools/test_parity.py`'s own content-hash digest is a cache
     MISS for any reason, including one a path filter cannot see — an engine upgrade folds
     into the digest deliberately (PRC-05), so a Godot version bump forces tier 4 even with
     zero repo diff.

   Read the line starting `WRAP_TIER=` for the tier to pass to the next step. If it says
   `ESCALATING to tier 4`, say so plainly in the wrap summary — this is not a step to
   quietly absorb.

3. **Launch the gate, in the background, first.** Nothing else in this list needs its
   result until step 7.

   ```
   LF_ALLOW_BACKGROUND=1 .venv/bin/python tools/check.py --tier <N> \
       --json /tmp/wrap-gate.json
   ```

   with the Bash tool's `run_in_background: true` on this exact call — no manual `&` or
   `nohup`. `tools/hooks/guard.py` denies backgrounding `check.py` outright unless the
   command carries `LF_ALLOW_BACKGROUND=1`; that guard exists because a background process
   the harness loses track of re-invokes the model when it finally exits, billing a session
   everyone believed was over (CLAUDE.md). Backgrounding it *through the harness's own
   `run_in_background`*, with the opt-in mark, is the sanctioned way to do this — it is
   still tracked, and you are still notified on completion, unlike a shell-level `&`/`nohup`
   which detaches the process from the harness entirely and leaves you to poll for it by
   hand. Do not combine the two.

   At tier 2 this finishes in ~25s and the background/foreground split barely matters. At
   tier 4 it can run past the 600s Bash timeout ceiling on its own — this is expected and
   is exactly why it is backgrounded rather than run with an explicit `timeout`: a `--tier 4`
   wrap should not block on the slowest check in the gate while the rest of the wrap sits
   idle waiting for permission to start.

4. **While the gate runs, do the parts that do not depend on it:**
   - **Backlog.** Close what got done (`backlog.py done LF-NNN --note "..."`), open anything
     discovered (`backlog.py add`). An item finished but not closed is worse than never
     filed.
   - **Decisions.** Did anything get *settled* this session — a rejected alternative, a
     constraint discovered, an approach chosen? Append an entry to `docs/DECISIONS.md`.
     Append only; supersede, never edit.
   - **STATE narrative.** The hand-written prose half of `docs/STATE.md` — not the AUTO
     block, which step 7 regenerates from the gate's own JSON.
   - **Journal.** The chronicle entry — what landed, the numbers behind it, screenshots
     copied into `docs/chronicle/assets/` and committed.

   None of this reads the gate's result. If any of it turns up something that *does* need
   verifying beyond tier 2 — a rules change, a data change — that should already have been
   caught by step 2's escalation; if it was not (the change happened *during* this step,
   after the gate already started), say so and re-run from step 2 rather than trusting a
   gate that started before the edit existed.

5. **End the turn here — do not keep the conversation open waiting on the gate.** This is
   the owner-blocking-time boundary PRC-20 is judged against, and it is a real turn
   boundary, not a figure of speech: at tier 2 the gate is done in ~25-30s regardless (steps
   1-4 plus this one measure well under a minute end to end, ~1:20-1:50 wall clock even
   under heavy concurrent machine load — see PRC-20's own measurement), so there is little
   to gain from ending early there. At tier 4 it is the whole point: say plainly that the
   gate is running in the background at tier N and why, note anything steps 3-4 already
   produced, and stop — the backgrounded `check.py` keeps running and the harness's own
   completion notification is what resumes steps 6-8 in a later turn. Do not sit in the
   same turn polling for a 9-minute-class run just because the tooling *could* finish it
   in one sitting; the owner should not have to wait through that any more than they should
   wait through the gate itself running in the foreground.

6. **Join.** On the gate's completion notification (or immediately, if it already finished
   while steps 3-4 ran), read `/tmp/wrap-gate.json`. Do not proceed to step 7 if it failed —
   leaving the tree broken taxes the next session heavily. `tools/gate_report.py
   /tmp/wrap-gate.json` renders it as a table if a human-readable summary is useful here.

7. **Rewrite `docs/STATE.md`'s AUTO block from the gate that already ran** — never re-run
   it a second time just to build this block:

   ```
   .venv/bin/python tools/session.py --tier <N> --gate-from /tmp/wrap-gate.json
   ```

   `--tier <N>` matters even when reading from `--gate-from`: LF-115 (fixed by PRC-20) used
   to let a STATE regeneration silently run — or claim — a full tier-4 gate regardless of
   what was actually asked for. `check.py`'s own summary line already says which tier ran
   (`"tier N — X passed · ..."`), and that line lands verbatim in STATE's Gate section, so a
   tier-2 wrap's STATE block says "tier 2" rather than reading like a full run. The rest of
   `docs/STATE.md` — not the AUTO block — is rewritten by hand from step 4's narrative work,
   not appended.

8. **Commit.** Conventional prefix. The body explains *why*, and records any toolchain trap
   discovered so the next session does not rediscover it at cost. Mention the tier the gate
   ran at and, if parity was skipped, that it was cached (or that it ran fresh) — the commit
   message is part of the falsifiable record too.

## What belongs in a commit body

Not what changed — the diff says that. Record:
- why the change was made
- what was tried and rejected, and the measurement that rejected it
- any API or tooling fact that contradicts what you would have assumed

Those three things are what stops the same mistake being made twice.

## Before you say the session is wrapped

Run `.venv/bin/python tools/reap.py` one last time and paste what it printed. "Clean" is a
claim that has to be falsifiable like any other — half the value of the tooling here is that
it makes claims checkable, and this is the claim that costs money when it is wrong. The
speed-up in this skill must never come from skipping this step: the gate running in the
background does not change what a leaked process costs.
