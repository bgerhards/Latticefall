#!/usr/bin/env python3
"""Inject `docs/STANDING-ORDERS.md` into every new session's context.

Why a hook rather than a pasted prompt. The owner's working instructions — delegate, ship small
PRs, journal every one, a measured "no" beats a green tick — were being re-pasted at the start of
each session. That worked until the pasted copy went stale: one restart prompt listed five
questions as "owner-gated" that had **all already been decided** (066, 067, 068, 069 twice), and
named an issue as the highest-value open work that had since closed. A session starting from that
text would have re-opened five settled decisions, which is the failure `LF-195` and `LF-205`
already record one level down, in the backlog.

So the instructions live in a tracked file that is reviewed like any other, and this hook reads
it. One copy, in git, with history. If it is wrong, it is wrong in a place someone will find.

`SessionStart` supports `additionalContext`, which is exactly the right shape: the text lands in
the model's context without occupying a user turn, before any work starts.

Best-effort by construction. If the file is missing or unreadable, emit nothing and exit 0 — a
session that starts without its standing orders is a worse session, but a session that refuses to
start is a broken one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORDERS = ROOT / "docs" / "STANDING-ORDERS.md"

# A guard against the file growing without anyone noticing: standing orders that no longer fit
# in a glance are standing orders nobody reads. Tune deliberately if the file earns the space.
MAX_CHARS = 12_000


def main() -> int:
    try:
        sys.stdin.read()
    except Exception:                                          # noqa: BLE001
        pass

    if not ORDERS.exists():
        return 0
    try:
        text = ORDERS.read_text(encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        return 0

    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS] + (
            "\n\n[...truncated — docs/STANDING-ORDERS.md has outgrown the "
            f"{MAX_CHARS}-character budget in tools/hooks/standing_orders.py. "
            "Read the file directly, and consider whether it should be shorter.]"
        )

    print(json.dumps({
        "suppressOutput": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "The owner's standing orders for this project, injected automatically from "
                "docs/STANDING-ORDERS.md. They are the working agreement for this session; "
                "CLAUDE.md still governs how the tooling is used, and docs/STATE.md is more "
                "current than the priority list below.\n\n" + text
            ),
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
