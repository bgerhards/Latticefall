---
name: session-start
description: Orient at the beginning of a Latticefall work session — read current state, open backlog, and settled decisions before touching anything. Use when starting work, resuming after a break, or when context was summarized and you are unsure where things stand.
---

# Session start

Context does not survive. This file is how a session picks up without re-deriving,
re-asking, or re-litigating.

## Do this in order

1. **Read `docs/STATE.md`.** What is in flight, what is blocked, what was last touched.
2. **Read the backlog:** `.venv/bin/python tools/backlog.py list`
3. **Skim `docs/DECISIONS.md` headings.** You do not need the bodies. You need to know
   which questions are closed so you do not reopen one.
4. **Check the tree is clean:** `git status -sb` and `git log --oneline -5`.
5. **Check nothing survived the last session:** `.venv/bin/python tools/reap.py`. A stray
   headless Godot from the previous parity check holds a core at 100% indefinitely, and a
   background process the harness still tracks bills tokens when it eventually exits. If it
   finds anything, `--kill` it and **say so** — it means the last wrap did not hold.
6. **Run the gate:** `.venv/bin/python tools/check.py --tier 2`. If it fails on arrival, that
   is the first thing to report — it means the last session left something broken.

   **Tier 2, not tier 4 and not tier 1.** This step carried no `--tier` until now, which meant
   it silently ran tier 4 — 22–36 minutes at the top of every session, re-proving on arrival
   what the previous wrap and CI both already proved. Tier 1 is the wrong cut in the other
   direction: the arrival failure this project actually has is the blank level from a stale or
   rebuilt `.godot/` import cache, which reads exactly like a code regression and has cost a
   full diagnosis pass — and `godot boots` and `sprite atlas`, the two checks that would catch
   it, are both tier 2.

## Then

State in one short paragraph: where the project is, what you believe the next task is, and
anything you found already broken. Do not start work until that is said — if the read of
the situation is wrong, it is much cheaper to correct now.

## Rules that make this work

- **Do not re-ask settled questions.** If projection, scope, tone, engine, hook, or audio
  approach comes up, it is in `DECISIONS.md`. Read it and move on.
- **Do not trust a memory of the toolchain.** `CLAUDE.md` records verified API facts about
  Blender 5.2, ffmpeg and libsndfile that contradict what a model is likely to assume.
- **Do not start a second thing before finishing the first.** Discoveries go to the backlog.
