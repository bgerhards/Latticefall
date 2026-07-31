id: PRC-02
title: Replace Path.rglob with git ls-files across the gate
labels: phase-0, tooling, perf
blocks: PRC-04, BAL-07
milestone: E1 Process
---
## Problem

Three gate checks walk the whole working tree with `Path.rglob`, on a WSL2 `drvfs` mount
where every `stat` crosses a filesystem boundary. Measured from `docs/STATE.md`'s gate block:
`banned terms` **28,678 ms**, `json parses` **7,905 ms**, `python syntax` **5,837 ms** —
about **42 s of the 649 s gate is filesystem walking**, and `banned terms` alone is the third
most expensive check in the suite. Swapping the enumeration for `git ls-files` measures
**28.7 s → 436 ms** on `banned terms`.

It is also a **correctness** fix, not only a speed one. `rglob` returns untracked scratch
files, build output, and agent worktrees. That has already turned the gate red once for a
file that is the authority on the terms it was flagged for (LF-051: a worktree under
`.claude/` carrying its own copy of `docs/NOMENCLATURE.md`, six false hits). The response was
a hand-maintained `SKIP_DIRS = {".venv", "addons", ".godot", ".claude"}` at
`tools/check.py:385` — a denylist that rots, versus `git ls-files`, which is the definition of
"in this repository" and needs no maintenance.

## Tasks

- [ ] Add a single `tracked(*globs: str) -> list[Path]` helper in `tools/check.py` (or
      `tools/toolpaths.py` if other tools want it) that shells `git ls-files -z -- <globs>`,
      splits on NUL, and returns absolute paths under `ROOT`.
- [ ] Handle the not-a-git-checkout case: fall back to the current `rglob` walk and say so in
      the check detail, so an export tarball still gates rather than crashing.
- [ ] Use `-z` and split on `\0`. Paths in this repo include spaces (`assets/renders/…` does
      not today, but `docs/` filenames are not guaranteed) and newline-splitting is the classic
      way this helper breaks silently.
- [ ] Convert `check_python_syntax` (`tools/check.py:83-84`) to `tracked("*.py")`; drop the
      `.venv`/`addons` part filter, which becomes dead.
- [ ] Convert `check_json_parses` (`tools/check.py:270-271`) to `tracked("*.json")`; drop the
      `.venv`/`addons`/`.godot` filter.
- [ ] Convert `check_banned_terms` (`tools/check.py:386-388`) to
      `tracked("*.py", "*.json", "*.gd", "*.md")`; **delete `SKIP_DIRS`** and leave a comment
      recording that LF-051's worktree false-positive is now structurally impossible rather
      than denylisted.
- [ ] Keep the `nomenclature-exempt` marker path exactly as it is — it is a per-file property,
      not a path property, and `git ls-files` does not replace it.
- [ ] Confirm the tracked-file counts against the current gate output: `python syntax` reports
      35 files, `banned terms` reports 132. Any change in those numbers is a file that either
      was or was not being checked before, and must be explained in the commit body.
- [ ] Check whether `git ls-files` includes deleted-but-staged paths in this repo's state; if
      so filter with `-c` (cached) or add an `exists()` guard, because a missing path makes
      `read_text()` raise and the check reports "check itself raised".
- [ ] Measure before and after with the gate's own timing column and record both numbers in
      the commit body.
- [ ] Update `CLAUDE.md`'s gate description and regenerate `docs/STATE.md`'s gate block with
      `.venv/bin/python tools/session.py`.

## Acceptance criteria

- `banned terms` completes in under 2,000 ms (measured baseline 28,678 ms).
- The sum of `python syntax` + `json parses` + `banned terms` drops by at least 35 s.
- `tools/check.py` contains no `SKIP_DIRS` constant and no `rglob` call outside the
  not-a-git-checkout fallback.
- Creating an untracked `docs/scratch-nomenclature.md` containing a banned term leaves
  `banned terms` green; `git add`ing it turns it red. (This is the LF-051 regression test,
  in both directions.)
- `python syntax` still reports 35 files and `banned terms` still reports 132, or the commit
  body explains every delta.

## Verification

```bash
.venv/bin/python -u tools/check.py 2>&1 | grep -E 'python syntax|json parses|banned terms'
# LF-051 regression, both directions
printf 'kawoosh\n' > docs/scratch-nom.md
.venv/bin/python tools/check.py --list >/dev/null && \
  .venv/bin/python -u tools/check.py 2>&1 | grep 'banned terms'   # expect ok
git add docs/scratch-nom.md
.venv/bin/python -u tools/check.py 2>&1 | grep 'banned terms'     # expect FAIL naming the file
git rm -f --cached docs/scratch-nom.md && rm docs/scratch-nom.md
```

Proof: the three timing figures, and a green→red→green transition driven only by `git add`.

## Risks / gotchas

- **The gate scans the backlog** (`docs/STATE.md`). A backlog item that quotes a banned term
  turns `banned terms` red — describe the hits, never repeat them. That is unchanged here,
  but the verification step above deliberately writes one, so delete the scratch file.
- `docs/NOMENCLATURE.md` is exempted by exact path comparison (`p != nom`) *and* by the
  `nomenclature-exempt` marker. Do not "simplify" one away; the marker is what travels with
  a file that moves.
- `git ls-files` inside a worktree lists that worktree's files, which is correct and is the
  point — but it means a gate run inside `.claude/worktrees/…` checks that worktree. Note it;
  {{PRC-15}} owns the worktree story.
- LFS pointer files: `git ls-files` lists `assets/renders/*.png`, which none of these three
  checks glob for. Do not widen the globs to `*` without thinking about 224 sprite blobs.
- **Python block-buffers a redirected stdout** — use `python -u` when piping.

## Files likely touched

- `tools/check.py`
- `tools/toolpaths.py` (only if the helper is shared)
- `CLAUDE.md`, `docs/STATE.md`
