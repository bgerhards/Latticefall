#!/usr/bin/env python3
"""
Sweep what a multi-agent session leaves lying around.

`tools/reap.py` kills stray *processes*. This is its counterpart for stray *state*: the
throwaway `git worktree`s agents create to test something against a clean checkout, the
local branches whose pull request has already merged, and the remote-tracking refs for
branches GitHub deleted on merge.

None of it is dangerous on its own. It accumulates: a worktree of this repo is roughly
150 MB once `.godot/` and the LFS smudge are in it, agents were told to create them for
exactly the right reasons (proving a fix against a checkout that has never been imported),
and an agent that dies mid-task leaves one behind with nothing to notice. The same session
that produced this file created at least four.

Safety, because this deletes things:
  - a worktree is only removed if `git worktree list` calls it prunable, or `--force` is
    passed AND it is clean; a dirty worktree is reported and never touched
  - a branch is only deleted if `git branch --merged main` lists it, which is git's own
    definition of "its commits are already on main", never a name pattern
  - the branch this is run from, `main`, and anything checked out in another worktree are
    always excluded
  - `--dry-run` is the default. Nothing is removed unless `--apply` is passed.

    .venv/bin/python tools/gc.py              # report only
    .venv/bin/python tools/gc.py --apply      # actually sweep
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = {"main", "master"}


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def worktrees() -> list[dict]:
    """Every worktree except the main one, with whether git considers it prunable."""
    out, cur = [], {}
    for line in git("worktree", "list", "--porcelain").splitlines():
        if not line:
            if cur:
                out.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        cur[key] = val or True
    if cur:
        out.append(cur)
    return [w for w in out[1:] if "worktree" in w]


def is_clean(path: str) -> bool:
    r = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                       capture_output=True, text=True)
    return r.returncode == 0 and not r.stdout.strip()


def merged_branches() -> list[str]:
    """Local branches whose commits are already on main — git's own definition, not a
    name pattern, because a pattern would happily delete unmerged work that matched it."""
    checked_out = {w.get("branch", "").removeprefix("refs/heads/") for w in worktrees()}
    checked_out.add(git("rev-parse", "--abbrev-ref", "HEAD"))
    names = [b.strip().lstrip("* ") for b in git("branch", "--merged", "main").splitlines()]
    return [b for b in names if b and b not in PROTECTED and b not in checked_out]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually remove things")
    ap.add_argument("--force", action="store_true",
                    help="also remove worktrees git has not marked prunable, if clean")
    a = ap.parse_args()

    did = 0
    for w in worktrees():
        path = w["worktree"]
        prunable = "prunable" in w
        clean = is_clean(path)
        if not (prunable or (a.force and clean)):
            print(f"worktree KEPT   {path}"
                  f"{'  (dirty — never removed automatically)' if not clean else ''}")
            continue
        print(f"worktree {'REMOVE' if a.apply else 'would remove'}  {path}")
        if a.apply:
            subprocess.run(["git", "worktree", "remove", "--force", path], cwd=str(ROOT))
            did += 1

    if a.apply:
        git("worktree", "prune")
        git("fetch", "--prune", "origin")
    else:
        print("would prune worktree metadata and stale remote-tracking refs")

    for b in merged_branches():
        print(f"branch   {'DELETE' if a.apply else 'would delete'}  {b}  (merged into main)")
        if a.apply:
            subprocess.run(["git", "branch", "-d", b], cwd=str(ROOT),
                           capture_output=True, text=True)
            did += 1

    if not a.apply:
        print("\nnothing removed — pass --apply")
    else:
        print(f"\nswept {did} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
