#!/usr/bin/env python3
"""
PRC-17 — "the workflow file exists" and "the workflow ran" are different claims, and this
project's own STATE.md conflated them once already: it described `nightly.yml` as "inert
behind a repo variable", which reads as a one-flag fix, when the measured reality was zero
registered runners and zero runs in the workflow's entire history. Nothing caught that until
someone went and ran `gh run list` by hand.

This is the mechanical version of that check: it asks GitHub's own API for the most recent
*successful* run of a named workflow on a given branch, and says loudly — non-zero exit,
not just a log line — when that answer is older than `--max-age-hours` or does not exist at
all. Point it at whichever workflow is this project's current source of truth for "rules
parity ran clean recently" (see the CLI default below) and it turns a silent schedule
failure into a red run someone actually sees, the same way a tier-excluded gate check reports
`skip` rather than pretending to be a pass.

Deliberately thin: no dependency beyond `gh` (already on every GitHub-hosted runner, and
what the rest of this project's tooling already shells out to — see `docs/BACKLOG.md`,
`docs/DECISIONS.md`) and the standard library. A freshness check that itself needs a wheel
installed is one more thing that can silently stop working.

Usage:
    .venv/bin/python tools/ci/freshness_check.py
    .venv/bin/python tools/ci/freshness_check.py --workflow nightly.yml
    .venv/bin/python tools/ci/freshness_check.py --workflow parity-shard.yml --workflow nightly.yml
    .venv/bin/python tools/ci/freshness_check.py --max-age-hours 24 --json
    .venv/bin/python tools/ci/freshness_check.py --repo owner/repo --branch main

Exit code is 0 only when every checked workflow's last successful run (on `--branch`) is
within `--max-age-hours`. Anything else — no run at all, or one older than the window — is
exit 1, whether run as a scheduled CI job (loud in the Actions tab, and GitHub emails the
schedule's owner on a failed scheduled run by default) or by a human at a terminal.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

# The workflow this project currently treats as "the full gate actually ran, including
# rules parity" — see PRC-17's report for why this points at the hosted sharded-parity
# workflow rather than `nightly.yml`: no self-hosted runner is registered (a deliberate,
# reported decision, not an oversight), so `nightly.yml` has zero runs in its history and
# always will until that changes. Pointing the default here at a workflow that is designed
# to actually run is what keeps this check's own default meaningful rather than permanently
# red for a reason everyone already knows. Override with `--workflow` for any other file.
DEFAULT_WORKFLOWS = ["parity-shard.yml"]

DEFAULT_MAX_AGE_HOURS = 48.0


def _run(argv: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def resolve_repo() -> str:
    r = _run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if r.returncode != 0:
        raise SystemExit(
            f"could not resolve the current repo via `gh repo view` "
            f"(exit {r.returncode}): {r.stderr.strip()}\npass --repo owner/name explicitly")
    return r.stdout.strip()


def latest_success(repo: str, workflow: str, branch: str) -> dict | None:
    """The most recent completed, successful run of `workflow` on `branch`, or None if
    there has never been one. `workflow` is the file basename (`gh`/the REST API accept
    that directly — no numeric workflow id needed)."""
    path = (f"repos/{repo}/actions/workflows/{workflow}/runs"
            f"?status=success&branch={branch}&per_page=1")
    r = _run(["gh", "api", path])
    if r.returncode != 0:
        # A 404 here almost always means the workflow file name is wrong (or the workflow
        # has literally never been triggered even once, in which case GitHub has not yet
        # registered it as a workflow at all) — surface the raw API error rather than
        # silently treating "the file doesn't exist by that name" the same as "it exists
        # but has zero runs", which is a materially different bug to go fix.
        raise SystemExit(f"gh api {path} failed (exit {r.returncode}): {r.stderr.strip()}")
    doc = json.loads(r.stdout)
    runs = doc.get("workflow_runs", [])
    return runs[0] if runs else None


def check_one(repo: str, workflow: str, branch: str, max_age_hours: float) -> dict:
    run = latest_success(repo, workflow, branch)
    if run is None:
        return {"workflow": workflow, "status": "STALE", "age_hours": None,
                "detail": "no successful run exists at all — the workflow file "
                          "existing is not evidence it has ever run"}
    finished = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
    age_hours = (datetime.now(timezone.utc) - finished).total_seconds() / 3600.0
    stale = age_hours > max_age_hours
    return {
        "workflow": workflow,
        "status": "STALE" if stale else "OK",
        "age_hours": round(age_hours, 1),
        "max_age_hours": max_age_hours,
        "run_id": run["id"],
        "run_url": run["html_url"],
        "finished_at": run["updated_at"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workflow", action="append", dest="workflows", metavar="FILE.yml",
                     help="workflow file to check (repeatable). Default: "
                          f"{', '.join(DEFAULT_WORKFLOWS)}")
    ap.add_argument("--repo", metavar="OWNER/NAME",
                     help="default: resolved via `gh repo view` in the current checkout")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    ap.add_argument("--json", action="store_true",
                     help="machine-readable output instead of the human summary")
    args = ap.parse_args()

    workflows = args.workflows or DEFAULT_WORKFLOWS
    repo = args.repo or resolve_repo()
    results = [check_one(repo, wf, args.branch, args.max_age_hours) for wf in workflows]
    stale = [r for r in results if r["status"] == "STALE"]

    if args.json:
        print(json.dumps({"repo": repo, "branch": args.branch, "results": results}, indent=2))
    else:
        for r in results:
            if r["status"] == "OK":
                print(f"[  ok  ] {r['workflow']:<24s} last success {r['age_hours']:.1f}h "
                      f"ago (< {r['max_age_hours']:.0f}h) — {r['run_url']}")
            else:
                detail = r.get("detail") or (
                    f"last success {r['age_hours']:.1f}h ago "
                    f"(> {r['max_age_hours']:.0f}h window)")
                print(f"[STALE ] {r['workflow']:<24s} {detail}")

    if stale:
        names = ", ".join(r["workflow"] for r in stale)
        print(f"\n{len(stale)}/{len(results)} workflow(s) stale: {names} — 'the workflow "
              f"file exists' is not 'the workflow ran' (PRC-17).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
