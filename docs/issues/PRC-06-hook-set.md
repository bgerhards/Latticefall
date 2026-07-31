id: PRC-06
title: The hook set — deny .godot writes, deny backgrounded long runs, deny raw engine argv
labels: phase-1, tooling, process
depends: PRC-01, PRC-07
milestone: E1 Process
---
## Problem

The project has **324 lines of `CLAUDE.md`, 441 of `docs/STATE.md`, five prose skills and five
prose agent definitions — and one hook** (`.claude/settings.json` is 15 lines: a single
`SessionEnd` reap). Decision 051 already established that a rule written in prose does not
hold. The evidence, from one session of seven parallel agents (PRD §3): **five backgrounded a
long run and orphaned it**, **one blanked the owner's running game by rebuilding the import
cache** (LF-075 — the owner plays out of `D:\dev\Latticefall`, the same tree agents edit), and
a parse error masquerading as a hang cost time **five times** (LF-063, decision 055).

Every one of those is mechanically preventable at the tool-call boundary, and none of them is
prevented today.

## Tasks

- [ ] Read the existing `.claude/settings.json` and preserve the `SessionEnd` reap exactly;
      this issue adds to it and must not replace it.
- [ ] Write `tools/hooks/guard.py` — one entry point, dispatching on the hook event, reading
      the tool-call payload from stdin and returning the deny/allow decision. One file, so the
      rules are auditable in one place, and testable without the harness.
- [ ] **Deny writes under `.godot/`.** A `PreToolUse` matcher on `Write|Edit|Bash` that refuses
      any path or command touching `.godot/` other than an in-place
      `--headless --path . --import`. The denial message must name LF-075 and say "import in
      place, and say so first" — a denial that does not tell you the supported alternative just
      gets worked around.
- [ ] **Deny backgrounding a long run.** A `PreToolUse` matcher on `Bash` that refuses
      `run_in_background: true` (or a trailing `&`, or `nohup`) when the command names
      `tools/check.py`, `tools/test_parity.py`, `sim/run.py`, `tools/sweep.py`,
      `tools/audio/serve.py`, `tools/shot.py` or `blender`. `CLAUDE.md`'s "Prefer the
      foreground" is the rule; this is the enforcement. Message must state the money argument:
      a background process the harness still tracks re-invokes the model when it exits.
- [ ] **Deny raw Godot/Blender argv.** A `PreToolUse` matcher on `Bash` refusing a command that
      invokes a Godot or Blender binary directly (`/mnt/*/godot/*`, `*.exe`, `Blender.app`,
      `blender`) instead of going through `tools/toolpaths.godot_argv()` /
      `blender_argv()` — i.e. through `tools/shot.py`, `tools/check.py`,
      `tools/test_parity.py` or `tools/blender/*.py`. This is not pedantry: bypassing
      `toolpaths` skips the Xvfb wrapping (decision 052) and the WSL path translation that
      `host_path_for()` performs, and the last time a tool bypassed it the Blender pipeline
      silently processed nothing (`docs/STATE.md`, "the Blender pipeline had never been run on
      this machine and was broken two ways"). Provide the escape hatch and document it:
      an explicit `LF_ALLOW_RAW_ENGINE=1` prefix, so a deliberate probe is possible and
      visible.
- [ ] **`SubagentStop` scoped reap.** Run `tools/reap.py --kill --quiet --lease <session>` so a
      finishing subagent cleans up *its own* strays and nothing else. This depends on
      {{PRC-07}}: an unscoped reap at `SubagentStop` would kill seven legitimate siblings.
- [ ] **`PostToolUse` lint.** On `Edit|Write` of `*.gd`, run {{PRC-01}}'s single-file parse
      check; on `*.py`, run `python -m py_compile`. Both are sub-second on one file.
- [ ] Make every deny message actionable and one paragraph long, naming the backlog id or
      decision it comes from. A wall of text gets skimmed.
- [ ] Add a `--selftest` mode to `tools/hooks/guard.py` with a table of
      (event, payload, expected decision) covering each rule in both directions, so the hooks
      are testable without provoking them live.
- [ ] Add a gate check `hooks configured` that asserts `.claude/settings.json` parses, declares
      each expected hook event, and that `tools/hooks/guard.py --selftest` passes. The
      `agent models` check (`tools/check.py:326`) is the precedent: decision 051's whole point
      was replacing remembered rules with mechanical ones.
- [ ] Update `CLAUDE.md` — replace the prose rules these hooks now enforce with a pointer to
      the hook, keeping the *reasoning* (that is what a hook cannot carry).
- [ ] Add a `docs/DECISIONS.md` entry recording that the working agreement is enforced at the
      tool boundary, with the rejected alternative (more prose) and the evidence for rejecting
      it (decision 051, the seven-agent session).

## Acceptance criteria

- `tools/hooks/guard.py --selftest` passes and covers every rule in both directions.
- A `Write` to `.godot/anything` is denied; a `Write` to `scripts/hud.gd` is allowed.
- `Bash` with `run_in_background: true` running `tools/check.py` is denied; the same command in
  the foreground is allowed; a genuinely long-lived thing the task needs concurrently is
  allowed via the documented escape hatch and nothing else.
- A `Bash` command invoking a Godot binary path directly is denied and the message names
  `tools/shot.py`; the same work through `tools/shot.py` is allowed.
- Editing a `.gd` file with a deliberate parse error surfaces the diagnostic in the same turn.
- `tools/check.py --tier 1` includes `hooks configured` and it is green.
- Deleting the `SessionEnd` entry turns `hooks configured` red.

## Verification

```bash
.venv/bin/python tools/hooks/guard.py --selftest ; echo "exit=$?"
.venv/bin/python -c "import json;print(json.load(open('.claude/settings.json'))['hooks'].keys())"
.venv/bin/python tools/check.py --tier 1 2>&1 | grep 'hooks configured'
# live, in-session: attempt each denied call once and paste the refusal
```

## Risks / gotchas

- **Do not weaken the guard to make your own work easier.** These hooks constrain the agent
  writing them. Any change to `.claude/settings.json` or `CLAUDE.md` that loosens a rule needs
  the owner, not an agent's judgement.
- **The `SessionEnd` hook does not fire when the CLI is killed outright** (LF-062). It is a
  belt, not a fix; `SubagentStop` narrows the window but does not close it, and the session
  skills' reap step stays.
- An overly broad backgrounding denial will block legitimate concurrency — e.g. a long render
  that a later step genuinely needs running while other work proceeds. Match on the named
  tools, not on "anything slow".
- The raw-argv denial must not break `tools/*.py` themselves: they invoke the binary directly
  *after* asking `toolpaths`. Match on the shell command the agent issues, not on what a tool
  does internally.
- Hook payload shape and the deny protocol are harness contract, not repo contract — probe the
  actual JSON on stdin before writing the matcher, exactly as `CLAUDE.md` demands for Blender
  and Godot APIs.

## Files likely touched

- `.claude/settings.json`
- `tools/hooks/guard.py` (new)
- `tools/check.py` (one new check)
- `CLAUDE.md`, `docs/DECISIONS.md`
