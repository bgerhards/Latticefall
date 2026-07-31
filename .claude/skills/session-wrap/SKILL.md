---
name: session-wrap
description: Close out a Latticefall work session so the next one can resume cleanly — update state, groom backlog, record decisions, run the gate, commit. Use when finishing a task, before a long pause, or when context is about to be summarized.
---

# Session wrap

The next session has no memory of this one. Everything worth keeping has to be written
down now, in files, not left in the conversation.

## Do this in order

1. **Reap every process this session started.** `.venv/bin/python tools/reap.py --kill`,
   then confirm it reports clean. A wrap that leaves something running is not a wrap: the
   parity check's headless Godot survives its parent and holds a core at 100%, `serve.py`
   has no exit condition at all, and any background process the harness is still tracking
   **re-invokes the model when it finally exits — billing tokens against a session everyone
   believed was over.** This has already cost the owner real money. Do it first, so it
   happens even if a later step goes sideways, and again at the very end.

   **Only the coordinator runs `--kill`, and only once every subagent has reported.** The
   reaper is lease-scoped now and plain `--kill` spares a lease it classifies as a sibling
   — but `CLAUDE_CODE_SESSION_ID` is per top-level CLI session, not per subagent, so a
   fanned-out sibling shares yours and gets classified `own-session`. Killing mid-fan-out
   therefore still ends live work silently. `LF-133`. If agents are running, run `reap.py`
   report-only, wait, and kill at the end.
2. **Run the gate.** `.venv/bin/python tools/check.py`. Do not proceed if it fails —
   leaving the tree broken taxes the next session heavily.
3. **Backlog.** Close what got done (`backlog.py done LF-NNN --note "..."`), open anything
   discovered (`backlog.py add`). An item finished but not closed is worse than never filed.
4. **Decisions.** Did anything get *settled* this session — a rejected alternative, a
   constraint discovered, an approach chosen? Append an entry to `docs/DECISIONS.md`.
   Append only; supersede, never edit.
5. **Rewrite `docs/STATE.md`.** Not append — rewrite. It describes *now*, and it is written
   for someone with no memory of this conversation. If it reads like notes-to-self, it is wrong.
6. **Commit.** Conventional prefix. The body explains *why*, and records any toolchain trap
   discovered so the next session does not rediscover it at cost.

## What belongs in a commit body

Not what changed — the diff says that. Record:
- why the change was made
- what was tried and rejected, and the measurement that rejected it
- any API or tooling fact that contradicts what you would have assumed

Those three things are what stops the same mistake being made twice.

## Before you say the session is wrapped

Run `.venv/bin/python tools/reap.py` one last time and paste what it printed. "Clean" is a
claim that has to be falsifiable like any other — half the value of the tooling here is that
it makes claims checkable, and this is the claim that costs money when it is wrong.
