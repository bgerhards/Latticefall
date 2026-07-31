id: PRC-15
title: Worktree per workstream, an ownership manifest, and mkdtemp for gate artefacts
labels: phase-1, tooling, process, risk
depends: PRC-07
milestone: E1 Process
---
## Problem

Theatre Scale is seven epics running in parallel, and the last multi-agent session in this
project was coordinated **by prose**: *"Seven agents in parallel, coordinated by file ownership
so they could not collide"* (`docs/STATE.md`). It mostly worked and it also produced LF-063 —
`combat_fx.gd` and `fx_additive.gd` failing to parse during a sanity run, on files *unmodified
per `git diff`*, attributed to "a concurrent agent's in-flight edit or a shared `.godot`
import-cache collision from several agents running Godot on the same working tree at once".
LF-066 was then misdiagnosed for a full pass on the back of it. Four separate backlog items
(LF-063, LF-064, LF-065, LF-074) each say some version of *"not fixed because that file was
owned by another agent this session"* — ownership was real, and it was invisible to every tool.

Concurrency also breaks the gate mechanically. `tools/check.py` writes **fixed** artefact
paths — `.godot/gate-frame.png` (`check.py:461`), `.godot/gate-menu.png` (`check.py:499`),
`.godot/gate-a11y-<case>.{png,json}` (`check.py:581`) — and then `unlink`s them
(`check.py:482`, `521`, `604-605`). Two concurrent gate runs delete each other's evidence and
the a11y analyser samples a background out of a PNG another process may have already removed.
Add `.godot/` being the shared import cache the owner's running game reads from (LF-075), and
"two agents ran the gate at once" is a class of bug this project has no defence against.

## Tasks

- [ ] Convert the gate's fixed artefact paths to a per-run `tempfile.mkdtemp()` directory,
      removed in a `finally`. One directory per gate run, named with the pid, so a crashed run
      leaves an inspectable trail rather than a mystery.
- [ ] Move the temp directory **out of `.godot/`** entirely. Nothing the gate writes belongs in
      the engine's import cache; that is what makes a gate run and a running game share state.
- [ ] Audit every other tool for fixed output paths under `.godot/` or the repo root
      (`tools/shot.py`'s `--out` is caller-supplied and fine; check `tools/sweep.py --apply`,
      `tools/session.py`, `tools/say_capacity.py`, `tools/blender/*`).
- [ ] Define `.claude/ownership.json`: a list of `{workstream, epic, globs, agent, branch,
      worktree}` entries. Globs, not directories — `scripts/` is shared by five epics and
      directory-level ownership would serialise the whole programme.
- [ ] Seed it from the Theatre Scale epics: E1 owns `tools/**`, `.claude/**`, `.github/**`;
      E2 the camera files; E3 placement; E4 terrain; E5 lanes; E6 art and `assets/**`; E7
      `sim/**`. Mark the genuinely shared files explicitly —
      `scripts/anchor_sim.gd` and `sim/engine.py` are the rules and move **together** (PRD §1),
      so they need a "requires coordination" flag rather than an owner.
- [ ] Add a `PreToolUse` hook rule to {{PRC-06}}'s `tools/hooks/guard.py` that denies
      `Edit`/`Write` to a path owned by another workstream, naming the owner and the branch in
      the refusal.
- [ ] Make the hook read the current workstream from an environment variable or from the
      worktree path, so the same rules file works for every agent without editing.
- [ ] Add `tools/worktree.py`: create a worktree per workstream under a path **outside** the
      main checkout (not `.claude/worktrees/` — see the gotchas), install the hooks
      ({{PRC-08}}), and print the ownership entry it will enforce.
- [ ] Solve the `.godot/` sharing problem for worktrees explicitly. Each worktree needs its own
      import cache, and importing from one must not disturb the main tree the owner plays from
      (LF-075). Probe whether Godot supports a per-invocation cache location; if not, document
      the constraint loudly — it is the single biggest reason worktrees are not free here.
- [ ] Add a gate check `ownership sane` (tier 1): `.claude/ownership.json` parses, no two
      workstreams claim the same glob, every claimed glob matches at least one tracked file,
      and every tracked file under `scripts/`, `sim/`, `tools/` and `data/` is claimed or
      explicitly shared.
- [ ] Add a `tools/worktree.py --status` that lists live worktrees, their branches, their
      leases ({{PRC-07}}) and anything uncommitted, so a wrap can see what is outstanding.
- [ ] Update `CLAUDE.md`'s "Scope discipline" and both session skills to name the manifest.
- [ ] Add a `docs/DECISIONS.md` entry: parallel work is bounded by a machine-readable ownership
      manifest and a worktree, superseding "coordinated by file ownership" as prose.

## Acceptance criteria

- Two `tools/check.py` runs started within a second of each other both complete, both report
  their own results, and neither reports a missing artefact. (Run tier 3 twice concurrently —
  the a11y case is the one that reads a PNG back.)
- `grep -rn 'gate-frame\|gate-menu\|gate-a11y' tools/` shows no fixed path under `.godot/`.
- `.godot/` contains no gate artefacts after a full run.
- `tools/check.py --tier 1` includes `ownership sane` and it is green.
- Two workstreams claiming `scripts/hud.gd` makes `ownership sane` red.
- An `Edit` to a file owned by another workstream is denied with a message naming the owner.
- `tools/worktree.py --status` lists each live worktree with its branch and lease count.

## Verification

```bash
( .venv/bin/python -u tools/check.py --tier 3 > /tmp/gate-a.txt 2>&1 & \
  .venv/bin/python -u tools/check.py --tier 3 > /tmp/gate-b.txt 2>&1 ; wait )
grep -c 'FAIL' /tmp/gate-a.txt /tmp/gate-b.txt        # expect 0 and 0
ls .godot | grep gate                                  # expect nothing
.venv/bin/python tools/check.py --tier 1 2>&1 | grep 'ownership sane'
.venv/bin/python tools/worktree.py --status
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **`check_banned_terms` used to rglob into `.claude/worktrees/`** and failed the gate with six
  false hits against a worktree's own copy of the nomenclature bible (LF-051). {{PRC-02}}'s
  `git ls-files` fixes the enumeration, but placing worktrees **outside** the main checkout
  avoids the whole class — a worktree inside the tree is a second copy of the repo that every
  path-walking tool has to be taught about.
- **Backlog ids collide across worktrees** (`docs/STATE.md`): the stored `next_id` counter
  cannot see items a merge brought in, and it minted a second LF-055. It is now
  `max(counter, highest id present + 1)`. Any worktree workflow must keep that property.
- **Rebuilding `.godot/` blanks the level for whoever is playing** (LF-075) — and the owner
  plays out of `D:\dev\Latticefall`, the same directory. A worktree that shares the import
  cache reintroduces LF-063's symptom exactly.
- **`reap.py --kill` is friendly fire across worktrees**, which is why {{PRC-07}} is a hard
  dependency: seven sibling processes were observed live that a literal wrap would have killed.
- Ownership is a coordination tool, not a security boundary. Make the denial message say how to
  hand a file over, or it becomes something to route around.
- Do not background the concurrent gate runs and walk away. `CLAUDE.md`: a backgrounded run
  the harness still tracks re-invokes the model when it exits, and that is billed.

## Files likely touched

- `tools/check.py` (artefact paths)
- `tools/worktree.py` (new), `.claude/ownership.json` (new)
- `tools/hooks/guard.py` (from {{PRC-06}})
- `.claude/skills/session-start/SKILL.md`, `.claude/skills/session-wrap/SKILL.md`
- `CLAUDE.md`, `docs/DECISIONS.md`
