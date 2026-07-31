id: PRC-11
title: Pin indentation in .editorconfig — needs the owner's call
labels: phase-0, process, tooling
milestone: E1 Process
---
## Problem

Indentation is unsettled and **the tool is settling it by attrition**. Running
`godot --headless --path . --import` rewrote `scripts/menu.gd` from tabs to four spaces by
itself — a 442-line no-op diff that had to be reverted by hand before committing, reproduced
once in a worktree with no `.godot` and never root-caused (LF-056). `scripts/main.gd` is
already four-space while every other `.gd` file is tabs (LF-059). And
`worktree-agent-a1cb438a721b228c6` carries an unmerged commit that reindents all 19 `.gd`
files to four spaces and adds an `indentation` gate check — **6,000 lines of whitespace nobody
asked for** (`docs/STATE.md`, "What does not exist").

The current `.editorconfig` is three lines: `root = true`, `[*]`, `charset = utf-8`. It says
nothing about indentation, so the editor is free to do what it did.

**This is the owner's decision, not an engineering one** — PRD §6, open decision 5. 19 of 20
files are tabs, so pinning tabs and dropping the branch is the cheap answer, but it is their
call and this issue must not pre-empt it.

## Tasks

- [ ] **Ask the owner first**, with the two options and their costs stated in one paragraph
      each: (a) pin tabs — one `.editorconfig` block, one file (`scripts/main.gd`) reindented,
      the 6,000-line branch dropped; (b) pin four spaces — merge the branch, 19 files rewritten,
      every open diff and every in-flight worktree rebased. Do not start until they answer.
- [ ] Once decided: add the block to `.editorconfig` — `[*.gd] indent_style = tab,
      indent_size = 4` (or spaces per the decision), `[*.py] indent_style = space,
      indent_size = 4`, `[*.json] indent_style = space, indent_size = 2`, plus
      `insert_final_newline` and `trim_trailing_whitespace` for all.
- [ ] Verify Godot 4.7.1's editor actually honours `.editorconfig` for script formatting on
      this machine — probe it, do not assume (`CLAUDE.md`: verify against the installed tool).
      If it does not, the real fix is an editor setting or stopping the import from touching
      scripts at all, and this issue must say which.
- [ ] Reindent the one file that disagrees with the decision (`scripts/main.gd` if tabs win),
      as a **standalone commit touching only whitespace**, so it can be skipped in `git blame`
      via `.git-blame-ignore-revs`.
- [ ] Add `.git-blame-ignore-revs` with that commit's SHA and configure
      `blame.ignoreRevsFile` in the repo's committed git config guidance (README or
      `CLAUDE.md`).
- [ ] Add an `indentation` gate check (tier 1) asserting every tracked `.gd`/`.py` file matches
      its `.editorconfig` rule. The existing branch already has one — read it and reuse it
      rather than rewriting.
- [ ] Re-run `godot --headless --path . --import` **in place** and confirm `git status` shows
      no script diff afterwards. That is the actual acceptance test for LF-056; everything else
      is bookkeeping.
- [ ] Decide the fate of `worktree-agent-a1cb438a721b228c6` explicitly — merged or deleted —
      and record it. A branch left in limbo is how this problem stayed open.
- [ ] Close LF-056 and LF-059 with the outcome, or record why one of them survives.
- [ ] Add a `docs/DECISIONS.md` entry with the owner's choice and the rejected alternative.

## Acceptance criteria

- `.editorconfig` names an `indent_style` for `*.gd`, `*.py` and `*.json`.
- `git ls-files '*.gd' | grep -v addons | xargs grep -lP '^    '` returns nothing (if tabs
  won) or returns every file (if spaces won) — one or the other, never a mixture.
- Running the editor import in place leaves `git status` clean for `scripts/*.gd`.
- The `indentation` gate check is in tier 1, passes on `main`, and fails on a file
  deliberately reindented the wrong way.
- `worktree-agent-a1cb438a721b228c6` is merged or deleted, and `docs/STATE.md` no longer
  describes it as pending.
- A `docs/DECISIONS.md` entry records the choice.

## Verification

```bash
cat .editorconfig
git ls-files '*.gd' | grep -v addons | xargs grep -cP '^\t' | sort -t: -k2 -n | head
.venv/bin/python tools/check.py --tier 1 2>&1 | grep indentation
# the real test: import in place, then look for a whitespace diff
.venv/bin/python - <<'EOF'
import subprocess, sys, pathlib
sys.path.insert(0, 'tools'); import toolpaths
argv = toolpaths.godot_argv(pathlib.Path('.'), ['--headless','--import'], want_window=True)
subprocess.run(argv, check=False)
EOF
git status --porcelain scripts/
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **Rebuilding `.godot/` blanks the level for whoever is playing** (LF-075). The owner plays
  out of this same tree. Import **in place**, and tell them before you do it. Do not move
  `.godot` aside to "force a cold import" — that is the exact sequence that was misdiagnosed as
  a code regression for a full pass.
- A whole-file reindent makes `git blame` useless unless it is isolated and ignored. Isolate
  it.
- The reindent branch's `indentation` check may encode the *opposite* decision from the one
  the owner makes. Read it before reusing it.
- Do not run this concurrently with any other `.gd` work — a whitespace commit conflicts with
  everything. Coordinate through {{PRC-15}}'s ownership manifest.
- Note that `tools/godot/setup_input.gd` and `scripts/test/*.gd` are `.gd` too and must not be
  forgotten by the check.

## Files likely touched

- `.editorconfig`, `.git-blame-ignore-revs` (new)
- `scripts/main.gd` (whitespace only, or all `scripts/*.gd` if spaces win)
- `tools/check.py` (one new check)
- `docs/DECISIONS.md`, `docs/STATE.md`, `backlog.json`, `docs/BACKLOG.md`
