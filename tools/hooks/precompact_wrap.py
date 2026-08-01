#!/usr/bin/env python3
"""Run the MECHANICAL half of the session wrap before Claude Code compacts the context.

Why this exists, and what it deliberately does not do.

The owner asked for a hook that fires at 50% context and runs `/session-wrap` then `/compact`,
in that order. **Claude Code has no context-percentage hook event** — the full list is
PreToolUse, PostToolUse, PostToolUseFailure, PostToolBatch, Notification, UserPromptSubmit,
UserPromptExpansion, SessionStart, SessionEnd, Stop, StopFailure, SubagentStart, SubagentStop,
PreCompact, PostCompact, PermissionRequest, PermissionDenied, Setup, and a handful of
workspace events. None of them is "context reached N%".

`PreCompact` is the better anchor anyway: it fires immediately before compaction, whether that
compaction is automatic (context filled) or manual (`/compact`). "Wrap, then compact, in that
order" is exactly what a PreCompact hook *is* — and it triggers off the real event instead of a
guessed threshold that would fire early on a short session and late on a long one.

WHAT THIS CANNOT DO, stated plainly so nobody assumes otherwise:

  - It cannot invoke `/session-wrap`. Hooks run shell commands; slash commands are model-facing.
  - It therefore cannot write the *narrative* half of `docs/STATE.md` — "what the last session
    did", the priority list, the traps. That is prose only the model produces, and it is the
    half that actually matters to the next session.

So this runs the mechanical steps that are pure tooling, and then **tells the model, through
`additionalContext`, that the narrative half is its job.** `PreCompact`'s output is injected
before the summary is produced, which is the last moment that instruction can still land.

Ordering is deliberate: reap first (a stray Godot burns a core and bills tokens against a
session everyone thinks is over — CLAUDE.md calls it a money bug), then render the backlog so
`docs/BACKLOG.md` matches `backlog.json`, then regenerate STATE's AUTO block.

`--tier 2` is not a shortcut. Decision 070: the wrap gates at tier 2 and the gate never sits on
the critical path. A tier-4 run here would take ~20 minutes re-proving on this machine what CI
already proves on both platforms, while the context it is trying to save fills up further.

Everything is best-effort. A hook that fails must never block compaction — losing the wrap is
survivable, losing the whole context because a subprocess errored is not.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "bin" / "python"

# (argv, human label, seconds). Ordered: reap, then render, then regenerate.
STEPS: list[tuple[list[str], str, int]] = [
    ([str(PY), "tools/reap.py"], "reap", 60),
    ([str(PY), "tools/backlog.py", "render"], "backlog render", 60),
    ([str(PY), "tools/session.py", "--tier", "2"], "STATE (tier 2 gate)", 900),
]

NARRATIVE_REMINDER = """\
A PreCompact wrap just ran the MECHANICAL half of the session wrap. It regenerated
docs/STATE.md's AUTO block, rendered the backlog and reaped stray processes.

It CANNOT write the half that matters. Before this context is compacted away, make sure
docs/STATE.md's hand-written sections are current — "Where the project is", "What the last
session did", the priority list, and any new traps — written for someone with no memory of
this conversation. Also confirm anything in flight is committed or explicitly recorded as
unfinished, and that docs/DECISIONS.md has an entry for every real decision taken.

If that is already done, say so and carry on; do not redo it."""


def run(argv: list[str], label: str, timeout: int) -> str:
    try:
        r = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"{label}: TIMED OUT after {timeout}s (skipped, not fatal)"
    except Exception as exc:                                   # noqa: BLE001
        return f"{label}: could not run ({exc.__class__.__name__}) — skipped"
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    last = tail[-1] if tail else "(no output)"
    return f"{label}: {'ok' if r.returncode == 0 else f'exit {r.returncode}'} — {last}"


def main() -> int:
    try:                       # PreCompact sends JSON on stdin; we do not need it, but
        sys.stdin.read()       # draining avoids a broken pipe on the caller's side.
    except Exception:          # noqa: BLE001
        pass

    if not PY.exists():        # No venv — say so rather than failing silently.
        print(json.dumps({"systemMessage": "pre-compact wrap skipped: .venv missing"}))
        return 0

    results = [run(argv, label, t) for argv, label, t in STEPS]
    print(json.dumps({
        "systemMessage": "pre-compact wrap — " + " · ".join(results),
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": NARRATIVE_REMINDER + "\n\n" + "\n".join(results),
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
