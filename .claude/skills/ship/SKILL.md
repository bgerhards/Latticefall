---
name: ship
description: Land a finished workstream — gate it, branch it, journal it, PR it, merge it. Use whenever a piece of work is done and needs to reach main. Replaces the ad-hoc commit-and-push habit with one repeatable loop that cannot forget the journal.
---

# Ship a workstream

One workstream, one branch, one PR, merged as soon as it is green. This exists because a
fifty-commit push to `main` happened once: unreviewable, CI ran only after the work had
landed, and the chronicle — which publishes on a merge — stayed invisible for a whole
session.

**Every PR updates the journal. No exceptions.** That is the step most likely to be skipped
and the one the owner cares most about, so it is step 4 of 8 rather than an afterthought.

## The loop

1. **Verify before branching.** `--tier 2` for anything that does not touch the rules,
   `--tier 4` when it does. Do not open a PR on work you have not gated — CI runs tier 1
   only, so a green PR check is not a green gate.
   ```bash
   .venv/bin/python tools/check.py --tier 2          # ~23 s
   .venv/bin/python tools/check.py                   # tier 4, ~11 min, rules changes only
   ```
   A rules change also needs `tools/test_parity.py`, which now exceeds the Bash tool's
   600 s ceiling — expect it to background and be notified.

2. **Branch.** `lf/<epic>-<short-slug>` — `lf/cam-minimap`, `lf/bal-windows-parity`. The
   epic prefix is the `docs/issues/` id family, so a branch says which programme it serves.

3. **Commit with `git commit --only <paths>`**, never `git add` then commit. Other agents
   share this working tree and the index is global; a stage-then-commit pair has already
   lost a commit here. Wrap it in an `index.lock` retry loop.

   The message says *why*, names the measurement, and records what was **not** done. A
   commit body is the chronicler's primary source — write it for that reader.

4. **Journal it.** Invoke the `chronicler` agent with what landed. It appends to
   `docs/chronicle/chronicle.json` and regenerates; it never rewrites an existing entry.
   Give it the numbers and the failures, not a summary — an entry that only records the win
   is worth less than no entry.

   Copy any image into `docs/chronicle/assets/` with a dated name. Never link `/tmp`; it
   vanishes, and a dead image is worse than none.

5. **Push and open the PR.** The body carries the same substance as the commit — what broke,
   what was measured, what is still red. Say plainly if something is knowingly failing.

6. **Watch CI, do not poll it.** `gh run watch <id> --exit-status --compact` blocks properly.

7. **Merge.** `gh pr merge <n> --squash --delete-branch`, then `git checkout main &&
   git pull --ff-only`.

8. **Close the issue with evidence.**
   ```bash
   .venv/bin/python tools/issues.py close PRC-04 --note "<what landed and how it was proved>"
   ```
   The note is required. A bare close leaves the evidence in a commit message nobody will
   find from the issue, and this project's whole method is that a claim is falsifiable.

## Then

`.venv/bin/python tools/gc.py --apply` sweeps merged branches, stale remote refs and any
throwaway worktree an agent left behind. A worktree of this repo is ~150 MB.

## Rules that make this work

- **Never batch unrelated workstreams onto one branch.** The point is that a red run names
  one thing.
- **Never `git stash`, `git reset`, `git checkout --` or `git add`** while agents share the
  tree. A stash swept eleven files across five workstreams once, and every agent then read
  `HEAD` instead of its own edits — which does not error, it silently invalidates every
  measurement taken in that window.
- **A knowingly-red PR is fine if the body says so and why.** A silently-red one is not.
