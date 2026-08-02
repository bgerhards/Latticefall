#!/usr/bin/env python3
"""
Make a merge close its issue, in both of this project's two tracking systems.

**The defect this exists for, proven live rather than reasoned about (LF-212).** Pull request
#130 was squash-merged to `main` with the body line ``Closes `PLC-03` ``. Thirty-seven minutes
later, GitHub issue #31 — the projection of `docs/issues/PLC-03-*.md` — was still `OPEN` with
`closedAt: null`. GitHub acts *only* on `Closes #<number>`; a spec id in backticks is prose it
reads past. Measured over the 51 merged pull requests available at the time: **zero** carried a
closing keyword GitHub could resolve, only three attempted a `Closes <SPEC-ID>` phrasing at
all, thirty issues stood open, and forty open backlog items were named by a pull request that
had already merged. That last number is deliberately *not* "forty stale items" — a pull request
naming `LF-211` may have **filed** it rather than fixed it, which is exactly what #130 did — and
the fact that nothing in the repository distinguishes "this PR fixed it" from "this PR mentioned
it" is the defect, not a caveat on it.

Three independent holes produced that, and this file closes the first two:

  1. No workflow triggers on `pull_request: closed`. `gate.yml`, `parity-shard.yml` and
     `parity-windows.yml` are open/synchronize only, so nothing observes a merge.
  2. `tools/issues.py close` is excellent and is *a line in a prompt* — `autoloop.py`'s brief
     and step 8 of the `ship` skill. When the **owner** merges by hand, no code runs at all.
     Decision 051 exists because remembered rules fail; this was a remembered rule.
  3. Two tracking systems that do not know about each other: `backlog.json` (`LF-nnn`, closed
     with `tools/backlog.py done`) and `docs/issues/*.md` projected to GitHub (`PLC-03`,
     `PRC-15`, …, closed with `tools/issues.py close`). The ship loop touched the second and
     never the first.

**The mechanism, chosen to match what each system actually is.**

A `docs/issues/` spec is projected to something *outside* the repository, so the close has to
happen outside too — and GitHub already does it, atomically, on merge, no matter who presses
the button. So the pull request body carries a **resolved** `Closes #N`, looked up from
`docs/issues/.map.json` rather than typed, and a `pull_request` CI check fails the PR when a
spec id is declared closed without one.

A `backlog.json` item is a file **in** the repository. Nothing external needs to be told; the
item is marked done in the pull request's *own* commit, so merging is what lands it and there
is no second step to forget. A tier-1 gate check asserts it, which is cheap and works offline.

Both halves read the same declaration: a **`Closes:` trailer** in a commit message on the
branch. One convention, checked two ways.

    git commit -m "fix(gate): ...

    Closes: LF-212
    Closes: PRC-15"

Usage:
    tools/traceability.py check                    # the tier-1 gate side: trailers resolve
    tools/traceability.py pr-lines                 # what the PR body needs, ready to paste
    tools/traceability.py check-pr --body-file F   # the CI side: the PR body carries them
    tools/traceability.py check --range main..HEAD # override the auto-detected range

`check` exits 0 when there is nothing to check — a branch with no `Closes:` trailer is not an
error, because plenty of pull requests are refactors that close nothing. What it refuses to
allow is a trailer that *claims* a close the repository cannot back up.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_PATH = ROOT / "docs" / "issues" / ".map.json"
BACKLOG_PATH = ROOT / "backlog.json"

## A backlog id versus a spec id. `LF-nnn` lives in backlog.json and is closed by a commit in
## the pull request itself; everything else (`PLC-03`, `PRC-15`, `TS-01`, `BAL-06`, …) is a
## spec under docs/issues/ projected to a GitHub issue, and is closed by GitHub on merge.
BACKLOG_ID = re.compile(r"^LF-\d+$")
ANY_ID = re.compile(r"\b([A-Z]{2,4}-\d+)\b")

## The trailer. Deliberately `Closes:` with a colon — git's own trailer syntax — so that
## `git interpret-trailers` and `git log --format=%(trailers)` can read it, and so that prose
## in a commit body ("this closes the gap LF-105 describes") cannot be mistaken for a
## declaration. The bare `Closes LF-105` shape is what failed on #130.
TRAILER = re.compile(r"^\s*Closes:\s*(.+?)\s*$", re.MULTILINE)

## What GitHub itself will act on in a pull request body. Case-insensitive, and the number is
## required — this is precisely the pattern #130's body did not match.
GH_CLOSING = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b")


def sh(*args: str) -> tuple[int, str]:
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def load_map() -> dict[str, int]:
    if not MAP_PATH.exists():
        return {}
    return {k: int(v) for k, v in json.loads(MAP_PATH.read_text()).items()}


def load_backlog() -> dict[str, str]:
    if not BACKLOG_PATH.exists():
        return {}
    return {i["id"]: i.get("status", "open")
            for i in json.loads(BACKLOG_PATH.read_text())["items"]}


def default_range() -> str | None:
    """The commits this branch adds, or None when that cannot be established honestly.

    CI checks out shallow and a pull request's base may simply not be in the object store, so
    this returns None rather than guessing a range — a check that silently examines zero
    commits and reports ok is the failure mode this whole file exists to prevent. The caller
    turns None into a *skip with a stated reason*, never into a pass.
    """
    for base in ("origin/main", "main"):
        code, out = sh("git", "merge-base", base, "HEAD")
        if code == 0 and out.strip():
            return f"{out.strip()}..HEAD"
    return None


def declared_closes(rev_range: str) -> dict[str, list[str]]:
    """Ids declared closed by a `Closes:` trailer, mapped to the commits that declared them."""
    code, out = sh("git", "log", "--format=%H%x00%B%x01", rev_range)
    if code != 0:
        return {}
    found: dict[str, list[str]] = {}
    for record in out.split("\x01"):
        if "\x00" not in record:
            continue
        sha, body = record.split("\x00", 1)
        sha = sha.strip()
        for line in TRAILER.findall(body):
            ## One trailer may name several ids, comma- or space-separated.
            for ident in ANY_ID.findall(line):
                found.setdefault(ident, []).append(sha[:7])
    return found


def classify(ids: list[str]) -> tuple[list[str], list[str]]:
    backlog = [i for i in ids if BACKLOG_ID.match(i)]
    specs = [i for i in ids if not BACKLOG_ID.match(i)]
    return sorted(backlog), sorted(specs)


def verify(ids: dict[str, list[str]]) -> list[str]:
    """Every declared close must be backed by the repository. Returns the problems."""
    problems: list[str] = []
    backlog_ids, spec_ids = classify(list(ids))
    status = load_backlog()
    mapping = load_map()

    for i in backlog_ids:
        where = ", ".join(ids[i])
        if i not in status:
            problems.append(f"{i} (commit {where}) is not in backlog.json")
        elif status[i] not in ("done", "dropped"):
            problems.append(
                f"{i} (commit {where}) is still {status[i]!r} in backlog.json — "
                f"run `tools/backlog.py done {i}` and amend it into this branch, because "
                f"backlog.json is a file in this repo and merging is what must land the close")

    for i in spec_ids:
        where = ", ".join(ids[i])
        if i not in mapping:
            problems.append(
                f"{i} (commit {where}) has no GitHub issue in docs/issues/.map.json — "
                f"run `tools/issues.py create` first, or fix the id")
    return problems


def required_pr_lines(ids: dict[str, list[str]]) -> list[str]:
    """The `Closes #N` lines the pull request body must carry for GitHub to act on merge."""
    mapping = load_map()
    _, spec_ids = classify(list(ids))
    return [f"Closes #{mapping[i]}  <!-- {i} -->" for i in spec_ids if i in mapping]


def cmd_check(a: argparse.Namespace) -> int:
    rev_range = a.range or default_range()
    if rev_range is None:
        print("skip: no merge base against origin/main or main — cannot establish the range "
              "of commits this branch adds (shallow clone?). Not a pass.")
        return 0
    ids = declared_closes(rev_range)
    if not ids:
        print(f"ok: no `Closes:` trailer in {rev_range} — nothing claimed, nothing to verify")
        return 0
    problems = verify(ids)
    if problems:
        print(f"FAIL: {len(problems)} unbacked close declaration(s) in {rev_range}")
        for p in problems:
            print(f"  - {p}")
        return 1
    backlog_ids, spec_ids = classify(list(ids))
    print(f"ok: {len(ids)} close(s) declared and backed — "
          f"{len(backlog_ids)} backlog, {len(spec_ids)} spec")
    return 0


def cmd_pr_lines(a: argparse.Namespace) -> int:
    rev_range = a.range or default_range()
    if rev_range is None:
        print("no merge base — cannot determine what this branch closes", file=sys.stderr)
        return 1
    ids = declared_closes(rev_range)
    lines = required_pr_lines(ids)
    backlog_ids, _ = classify(list(ids))
    if not lines and not backlog_ids:
        print("(nothing declared closed by a `Closes:` trailer on this branch)")
        return 0
    for line in lines:
        print(line)
    if backlog_ids:
        print(f"<!-- backlog {', '.join(backlog_ids)} close via this PR's own commit to "
              f"backlog.json; GitHub has no issue for them -->")
    return 0


def cmd_check_pr(a: argparse.Namespace) -> int:
    """CI side: the pull request body must carry a `Closes #N` GitHub can actually resolve.

    Reads the body from a file or an environment variable rather than an argument, because a
    pull request body is untrusted text that will eventually contain a backtick, a `$` or a
    newline, and interpolating it into a shell command is how a workflow becomes an injection.
    """
    if a.body_file:
        body = Path(a.body_file).read_text(encoding="utf-8", errors="replace")
    elif a.body_env:
        body = os.environ.get(a.body_env, "")
    else:
        body = sys.stdin.read()

    rev_range = a.range or default_range()
    if rev_range is None:
        print("skip: no merge base — cannot read this branch's `Closes:` trailers. Not a pass.")
        return 0
    ids = declared_closes(rev_range)
    required = required_pr_lines(ids)
    if not required:
        print("ok: no spec issue declared closed by a trailer — nothing required in the body")
        return 0

    present = {int(n) for n in GH_CLOSING.findall(body)}
    mapping = load_map()
    _, spec_ids = classify(list(ids))
    missing = [i for i in spec_ids if i in mapping and mapping[i] not in present]
    if missing:
        print("FAIL: the pull request body does not close the issue(s) this branch declares.")
        print("GitHub acts ONLY on `Closes #<number>` — a spec id in backticks is prose it")
        print("reads past, which is exactly how PR #130 merged while issue #31 stayed open.")
        print("\nAdd these lines to the pull request body:\n")
        for line in required:
            print(f"  {line}")
        print(f"\nMissing: {', '.join(missing)}")
        return 1
    print(f"ok: body closes {len(required)} issue(s) — {', '.join(spec_ids)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--range", help="rev range to read trailers from (default: merge-base..HEAD)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="every `Closes:` trailer is backed by the repository")
    sub.add_parser("pr-lines", help="print the `Closes #N` lines the PR body needs")
    p = sub.add_parser("check-pr", help="the PR body carries the required `Closes #N`")
    p.add_argument("--body-file", help="file holding the pull request body")
    p.add_argument("--body-env", help="environment variable holding the pull request body")
    a = ap.parse_args()
    return {"check": cmd_check, "pr-lines": cmd_pr_lines, "check-pr": cmd_check_pr}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
