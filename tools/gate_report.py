#!/usr/bin/env python3
"""
Render a `tools/check.py --json` gate result as a GitHub-flavoured markdown table.

One code path for the machine-readable gate result, so a PR comment and the terminal never
tell two different stories about the same run. This exists ahead of any CI workflow (there
is no `.github/` directory yet) because the alternative — a workflow re-deriving a table by
scraping `tools/check.py`'s column-aligned human text — is exactly the brittleness `--json`
was added to avoid.

Reads `detail` as `--json` writes it: the check's *full* multi-line output, not the first
line the human table prints. A failure's full detail is what a PR comment needs to be
useful, so it goes in a collapsed `<details>` block per failing check rather than being
truncated to match the terminal's one-line summary.

    .venv/bin/python tools/gate_report.py gate.json
    .venv/bin/python tools/gate_report.py gate.json --out comment.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

STATUS_ICON = {"ok": "✅", "FAIL": "❌", "skip": "⏭️"}


def render(doc: dict) -> str:
    checks = doc.get("checks", [])
    summary = doc.get("summary", {})
    duration = summary.get("duration_ms", doc.get("duration_ms", 0))

    lines = [
        f"### Gate — {summary.get('passed', '?')} passed · "
        f"{summary.get('failed', '?')} failed · {summary.get('skipped', '?')} skipped "
        f"· {duration:.0f}ms",
        "",
    ]
    commit = doc.get("root_commit")
    if commit:
        # `dirty` is only trustworthy against a real working tree — a fresh CI checkout can
        # report clean even when the workflow patched files in first (see tools/check.py's
        # module docstring). Shown as a fact about this run, not proof of anything.
        dirty = " (dirty)" if doc.get("dirty") else ""
        lines += [f"commit `{commit[:12]}`{dirty}", ""]

    lines += ["| | check | ms | detail |", "|---|---|---:|---|"]
    for c in checks:
        icon = STATUS_ICON.get(c.get("status"), c.get("status", "?"))
        detail_full = c.get("detail") or ""
        first = detail_full.splitlines()[0] if detail_full else ""
        first = first.replace("|", "\\|")
        reason = c.get("skipped_reason")
        name = f"{c.get('name', '?')} ({reason})" if reason else c.get("name", "?")
        lines.append(f"| {icon} | {name} | {c.get('ms', 0):.0f} | {first} |")

    fails = [c for c in checks if c.get("status") == "FAIL"]
    if fails:
        lines += ["", "<details><summary>Failure detail</summary>", ""]
        for c in fails:
            lines += [f"**{c.get('name', '?')}**", "```", (c.get("detail") or "").rstrip(),
                     "```", ""]
        lines.append("</details>")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Render a tools/check.py --json result as a markdown table.")
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--out", type=Path, help="write to a file instead of stdout")
    args = ap.parse_args()

    doc = json.loads(args.json_path.read_text())
    text = render(doc)
    if args.out:
        args.out.write_text(text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
