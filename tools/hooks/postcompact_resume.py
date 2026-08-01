#!/usr/bin/env python3
"""Restart the work loop after a compaction, so nobody has to type anything.

This is the closing link in the zero-touch loop the owner asked for:

    SessionStart -> standing orders -> work the backlog
                 -> at 50% context, the model wraps itself (narrative STATE.md, commit, push)
                 -> auto-compact fires
                 -> PreCompact runs the mechanical wrap
                 -> [THIS HOOK] re-injects the orders and says "resume"
                 -> work continues

Without this, a compaction ends with a summary and no instruction, and the session stalls
waiting for a human to say "carry on". `PostCompact` is the only event that fires *after* the
summary exists, and it supports `additionalContext`, so it is the one place a "keep going"
can be delivered into the fresh context.

Two things it deliberately does NOT assume.

It does not assume the compaction was clean. If it was an AUTO compaction, the model had no
turn in which to write the narrative half of `docs/STATE.md` (see `precompact_wrap.py`), so
this tells the reader to verify STATE against git rather than trust it. Manual compactions get
the lighter message.

It does not assume there is work. The owner's rule is explicit: *if there is nothing in the
backlog then we have nothing to do.* So the resume instruction ends by saying that stopping is
a legitimate outcome — a loop that invents work to stay busy is worse than one that halts.

Best-effort. A failure here must never break a session that has just successfully compacted.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "bin" / "python"
ORDERS = ROOT / "docs" / "STANDING-ORDERS.md"


def git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=20)
        return r.stdout.strip()
    except Exception:                                          # noqa: BLE001
        return ""


def open_backlog_count() -> str:
    """How much work is left, so the resume message can say 'stop' when it should."""
    if not PY.exists():
        return "unknown"
    try:
        r = subprocess.run([str(PY), "tools/backlog.py", "list"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60)
        return str(sum(1 for line in r.stdout.splitlines() if line.startswith("[ ]")))
    except Exception:                                          # noqa: BLE001
        return "unknown"


def main() -> int:
    trigger = "auto"
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if str(payload.get("trigger") or payload.get("matcher") or "").lower() == "manual":
            trigger = "manual"
    except Exception:                                          # noqa: BLE001
        pass

    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    head = git("log", "--oneline", "-1") or "?"
    dirty = git("status", "--porcelain")
    n_open = open_backlog_count()

    lines = [
        "CONTEXT WAS JUST COMPACTED. Resume work; no one needs to prompt you.",
        "",
        f"  branch        {branch}",
        f"  HEAD          {head}",
        f"  uncommitted   {len(dirty.splitlines()) if dirty else 0} file(s)",
        f"  backlog open  {n_open}",
        "",
    ]

    if trigger == "auto":
        lines += [
            "This was an AUTO compaction, so the previous turn had no opportunity to write the",
            "narrative half of docs/STATE.md. Treat its hand-written sections as possibly stale —",
            "the AUTO block below the marker is current, the prose above it may not be. Verify",
            "against `git log --oneline -20`, the merged PRs, and docs/chronicle/chronicle.json",
            "before trusting it, and say in your first message that this happened.",
            "",
        ]
    else:
        lines += [
            "This was a manual compaction, so the wrap should have run with full context. Still",
            "confirm docs/STATE.md matches `git log` before relying on it.",
            "",
        ]

    if dirty:
        lines += [
            "THERE ARE UNCOMMITTED CHANGES. Work was in flight when the context ended. Find out",
            "what they are before starting anything new — they may be a half-finished workstream",
            "that needs completing, or an agent's edits that were never landed.",
            "",
        ]

    lines += [
        "WHAT TO DO NOW:",
        "  1. Read docs/STATE.md — the priority list is the queue.",
        "  2. Resume the highest-value item, or finish whatever the uncommitted changes are.",
        "  3. Keep working. Do not stop to check in.",
        "",
        "IF THE BACKLOG IS EMPTY AND NOTHING IS IN FLIGHT, STOP AND SAY SO. Having nothing to do",
        "is a legitimate outcome and the owner has said as much; inventing work to stay busy is",
        "worse than halting.",
    ]

    if ORDERS.exists():
        try:
            lines += ["", "--- standing orders (docs/STANDING-ORDERS.md) ---", "",
                      ORDERS.read_text(encoding="utf-8")]
        except Exception:                                      # noqa: BLE001
            pass

    print(json.dumps({
        "systemMessage": f"post-compact resume · {branch} · {n_open} open · "
                         f"{len(dirty.splitlines()) if dirty else 0} uncommitted",
        "hookSpecificOutput": {
            "hookEventName": "PostCompact",
            "additionalContext": "\n".join(lines),
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
