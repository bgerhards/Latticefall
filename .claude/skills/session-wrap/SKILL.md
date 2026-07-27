---
name: session-wrap
description: Close out a Latticefall work session so the next one can resume cleanly — update state, groom backlog, record decisions, run the gate, commit. Use when finishing a task, before a long pause, or when context is about to be summarized.
---

# Session wrap

The next session has no memory of this one. Everything worth keeping has to be written
down now, in files, not left in the conversation.

## Do this in order

1. **Run the gate.** `.venv/bin/python tools/check.py`. Do not proceed if it fails —
   leaving the tree broken taxes the next session heavily.
2. **Backlog.** Close what got done (`backlog.py done LF-NNN --note "..."`), open anything
   discovered (`backlog.py add`). An item finished but not closed is worse than never filed.
3. **Decisions.** Did anything get *settled* this session — a rejected alternative, a
   constraint discovered, an approach chosen? Append an entry to `docs/DECISIONS.md`.
   Append only; supersede, never edit.
4. **Rewrite `docs/STATE.md`.** Not append — rewrite. It describes *now*, and it is written
   for someone with no memory of this conversation. If it reads like notes-to-self, it is wrong.
5. **Commit.** Conventional prefix. The body explains *why*, and records any toolchain trap
   discovered so the next session does not rediscover it at cost.

## What belongs in a commit body

Not what changed — the diff says that. Record:
- why the change was made
- what was tried and rejected, and the measurement that rejected it
- any API or tooling fact that contradicts what you would have assumed

Those three things are what stops the same mistake being made twice.
