#!/usr/bin/env python3
"""
Render a `tools/check.py --json` gate result as a GitHub-flavoured markdown table.

One code path for the machine-readable gate result, so a PR comment and the terminal never
tell two different stories about the same run. `.github/workflows/gate.yml` (PRC-08) is the
caller: it runs `tools/check.py --tier 1 --json gate.json` and posts this renderer's output
as the PR comment, rather than a workflow re-deriving a table by scraping `tools/check.py`'s
column-aligned human text — exactly the brittleness `--json` was added to avoid.

Reads `detail` as `--json` writes it: the check's *full* multi-line output, not the first
line the human table prints. A failure's full detail is what a PR comment needs to be
useful, so it goes in a collapsed `<details>` block per failing check rather than being
truncated to match the terminal's one-line summary.

    .venv/bin/python tools/gate_report.py gate.json
    .venv/bin/python tools/gate_report.py gate.json --out comment.md
    .venv/bin/python tools/gate_report.py gate.json --fail-on-subsystem-skip

`--fail-on-subsystem-skip` is PRC-08's CI escalation: a `skipped_reason: "subsystem"` means a
check found its own subsystem missing (no `sim/`, no sprite manifest, no nomenclature bible,
...). Locally that is often a legitimate "not built yet"; on a CI runner that is supposed to
have every subsystem present it is a broken environment, and the plain exit code from
`tools/check.py` alone cannot tell the two apart — a subsystem skip is not a `FAIL` and does
not turn the gate red on its own. This flag makes it one, without changing what the table
renders, so the PR comment and the exit code agree on what "broken environment" looks like.
`skipped_reason: "tier"` and `"flag"` are unaffected — those are the *caller's own* choice
about that run, not a fact about the environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATUS_ICON = {"ok": "✅", "FAIL": "❌", "skip": "⏭️"}


def render(doc: dict) -> str:
    checks = doc.get("checks", [])
    summary = doc.get("summary", {})
    duration = summary.get("duration_ms", doc.get("duration_ms", 0))
    # `tier` was added by {{PRC-04}}; a JSON artefact from before that lands has no key at
    # all (older readers must not choke on it either way — a missing key is not "tier 1").
    tier = doc.get("tier")
    tier_tag = f"tier {tier} — " if tier is not None else ""

    lines = [
        f"### Gate — {tier_tag}{summary.get('passed', '?')} passed · "
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


def subsystem_skips(doc: dict) -> list[str]:
    """Names of every check that reported `skip` with `skipped_reason: "subsystem"` — a fact
    about the environment, not a choice the run made (see module docstring)."""
    return [c.get("name", "?") for c in doc.get("checks", [])
            if c.get("status") == "skip" and c.get("skipped_reason") == "subsystem"]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Render a tools/check.py --json result as a markdown table.")
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--out", type=Path, help="write to a file instead of stdout")
    ap.add_argument("--fail-on-subsystem-skip", action="store_true",
                    help="exit 1 if any check reports skipped_reason=subsystem — a missing "
                         "subsystem is a broken environment in CI, not an acceptable skip "
                         "(PRC-08). Does not change the rendered table.")
    args = ap.parse_args()

    doc = json.loads(args.json_path.read_text())
    text = render(doc)
    if args.out:
        args.out.write_text(text)
    else:
        print(text, end="")

    if args.fail_on_subsystem_skip:
        missing = subsystem_skips(doc)
        if missing:
            print(f"gate_report: {len(missing)} check(s) skipped for a missing subsystem — "
                  f"broken environment, not an acceptable skip in CI: {', '.join(missing)}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
