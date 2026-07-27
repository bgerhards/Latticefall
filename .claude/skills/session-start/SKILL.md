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
5. **Run the gate:** `.venv/bin/python tools/check.py`. If it fails on arrival, that is
   the first thing to report — it means the last session left something broken.

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
