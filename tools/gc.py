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
    .venv/bin/python tools/gc.py --selftest   # assert the LF-201 invariants, then sweep
    .venv/bin/python tools/gc.py --apply      # actually sweep

**`--selftest` is deliberately NOT a gate check.** It costs 1.45 s on this machine, almost
all of it interpreter startup, and tier 1 has ~1.7 s of headroom while tier 2 is already over
its own budget (LF-178). Spending that on every gate run — which happens constantly — to
guard a tool invoked once per session, at wrap, would be the same bad trade as raising a
budget rather than removing a check. So it runs where `gc.py` runs: immediately before
`--apply`, in the `ship` skill's sweep step.
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


def checked_out_branches() -> dict[str, str]:
    """Every local branch that some worktree currently has checked out, to its path.

    Asked of `for-each-ref` rather than assembled from `git worktree list`, because
    `%(worktreepath)` is git's own answer to exactly this question and needs no parsing.
    """
    out = {}
    for line in git("for-each-ref", "--format=%(refname:short)%09%(worktreepath)",
                    "refs/heads").splitlines():
        name, _, path = line.partition("\t")
        if name and path:
            out[name] = path
    return out


def merged_branches() -> list[tuple[str, str]]:
    """Local branches whose commits are already on main, with why each survivor was kept.

    Git's own definition of merged, never a name pattern — a pattern would happily delete
    unmerged work that matched it.

    **`--format` is load-bearing, and its absence was LF-201.** Plain `git branch --merged`
    decorates its output: `* ` for the current branch and **`+ ` for a branch checked out in
    another worktree**. The old parser stripped `"* "` and not `"+ "`, so a live agent's
    branch came back as the literal string `"+ lf/thing"`, which matched nothing in the
    checked-out set — defeating the one exclusion that exists to protect it — and was then
    printed as `would delete  + lf/thing  (merged into main)`. That is what a running session
    saw, and it is why LF-201 was filed as a blocker about destroying a live agent's work.

    **It was not destroying anything, and the reason matters.** Reproduced deliberately with
    a `--no-checkout` worktree: `git branch -d "+ lf/repro-201"` fails with *branch not
    found*, and even with the name parsed correctly git refuses on its own — *cannot delete
    branch 'lf/repro-201' used by worktree at …*. Two independent guards, so the tool was
    lying about what it would do rather than doing it. A tool that reports a destruction it
    cannot perform still costs real time (a blocker-priority issue, and a session spent
    believing agent branches were being deleted), and it only stayed safe by accident of a
    malformed name — normalise the name in a later refactor and the exclusion has to be
    correct, so it is made correct here.
    """
    checked_out = checked_out_branches()
    here = git("rev-parse", "--abbrev-ref", "HEAD")
    kept: list[tuple[str, str]] = []
    for name in git("branch", "--merged", "main",
                    "--format=%(refname:short)").splitlines():
        name = name.strip()
        if not name or name in PROTECTED:
            continue
        if name == here:
            continue
        if name in checked_out:
            kept.append((name, f"checked out at {checked_out[name]}"))
            continue
        kept.append((name, ""))
    return kept


def selftest() -> int:
    """Assert the invariants LF-201 violated, against this repository's real refs.

    Deliberately not a mock. The bug was a *parsing* bug against real `git branch` output,
    and a fixture of what someone believes that output looks like is exactly what would have
    let it through — the old parser was written against a correct mental model of `* ` and an
    absent one of `+ `. So this asks the live repository and asserts on the shape of what
    comes back. It costs two `git` invocations.
    """
    problems = []
    merged = merged_branches()
    for name, reason in merged:
        if name != name.strip() or " " in name or name[:1] in "*+":
            problems.append(f"decorated or malformed branch name: {name!r}")
        if reason and "checked out at" not in reason:
            problems.append(f"unexpected keep reason for {name!r}: {reason!r}")

    ## The exclusion that LF-201 defeated: anything a worktree holds must be KEPT, never
    ## offered for deletion. Asserted against git's own `%(worktreepath)`.
    checked_out = checked_out_branches()
    offered = {n for n, reason in merged if not reason}
    overlap = offered & set(checked_out)
    if overlap:
        problems.append(f"offered to delete branches checked out in a worktree: {overlap}")

    for p in problems:
        print(f"FAIL: {p}")
    if problems:
        return 1
    print(f"ok: {len(merged)} merged branch(es) inspected, "
          f"{len(checked_out)} checked out in a worktree, no decorated names, "
          f"no checked-out branch offered for deletion")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually remove things")
    ap.add_argument("--force", action="store_true",
                    help="also remove worktrees git has not marked prunable, if clean")
    ap.add_argument("--selftest", action="store_true",
                    help="assert the LF-201 invariants against this repo's real refs")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    did, failed = 0, 0
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
            r = subprocess.run(["git", "worktree", "remove", "--force", path],
                               cwd=str(ROOT), capture_output=True, text=True)
            if r.returncode == 0:
                did += 1
            else:
                failed += 1
                print(f"         FAILED  {path}: {(r.stderr or '').strip()}")

    ## Branches BEFORE `worktree prune`, deliberately. Pruning de-registers a worktree whose
    ## directory has vanished, and de-registering is exactly what disarms git's own "cannot
    ## delete branch used by worktree" refusal — the second of the two guards that kept
    ## LF-201 from being real. Considering branches first means that guard is still armed
    ## when it matters.
    for name, keep_reason in merged_branches():
        if keep_reason:
            print(f"branch   KEPT     {name}  ({keep_reason})")
            continue
        print(f"branch   {'DELETE' if a.apply else 'would delete'}  {name}  (merged into main)")
        if a.apply:
            ## `-d`, never `-D`: git's safe delete, which refuses anything not actually
            ## merged. The return code is CHECKED — it was not, so every failed delete was
            ## silent and still counted toward "swept N item(s)". A count that includes
            ## failures is the same class of instrument as the retry loop that committed
            ## nothing twelve times without ever failing.
            r = subprocess.run(["git", "branch", "-d", name],
                               cwd=str(ROOT), capture_output=True, text=True)
            if r.returncode == 0:
                did += 1
            else:
                failed += 1
                print(f"         FAILED  {name}: {(r.stderr or '').strip()}")

    if a.apply:
        git("worktree", "prune")
        git("fetch", "--prune", "origin")
    else:
        print("would prune worktree metadata and stale remote-tracking refs")

    if not a.apply:
        print("\nnothing removed — pass --apply")
    else:
        print(f"\nswept {did} item(s)" + (f", {failed} FAILED" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
