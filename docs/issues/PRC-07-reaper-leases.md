id: PRC-07
title: Scope the reaper with leases — reap.py --kill is friendly fire
labels: phase-0, tooling, process, risk
blocks: PRC-06, PRC-15
milestone: E1 Process
---
## Problem

`tools/reap.py` classifies a process as "ours" purely by **command-line shape**
(`tools/reap.py:115-148`): any Godot with `--headless` or `--fixed-fps`, any `Xvfb` with
`-screen 0 1600x900x24`, any Blender `-b` under this repo path, any Python naming this repo
and one of `OUR_TOOLS`. Its own docstring admits the consequence — *"this still cannot
distinguish 'leaked' from 'legitimately mid-capture right now'"* (`tools/reap.py:41-45`).

That stopped being theoretical. **Observed live during the theatre-scale audit: `reap.py
--kill` would have killed seven legitimate sibling-agent processes** — captures and sweeps
that were mid-flight for other agents working in the same tree (PRD §3). And `CLAUDE.md`
instructs running exactly that at every wrap, while `.claude/settings.json` runs it on
`SessionEnd`. A literal reading of the wrap skill is a fan-out killer.

The mitigation must not weaken the thing the reaper exists for: a survivor is a **money bug**,
not hygiene — a background process the harness still tracks re-invokes the model when it
exits, and this has already spilled the owner's subscription into paid credits once
(`CLAUDE.md`).

## Tasks

- [ ] Design the lease file format and write it into the module docstring:
      `{pid, ppid, session_id, agent, tool, argv, started_at, expires_at, repo}` as one JSON
      file per launch under `.cache/leases/<pid>.json` (gitignored).
- [ ] Add `tools/lease.py` with `acquire(tool, argv, ttl_s) -> Lease` as a context manager that
      writes the file on entry and removes it on exit, including on exception. Stdlib only
      (`CLAUDE.md` conventions).
- [ ] Source `session_id` from the harness environment (`CLAUDE_SESSION_ID` or equivalent);
      probe what is actually exported rather than assuming, and fall back to the top-level
      shell PID so a lease always has *some* owner.
- [ ] Wrap every launch site: `tools/shot.py:run_shot`, `tools/check.py:run`,
      `tools/test_parity.py:run_godot`, `tools/blender/render.py`'s Blender invocation,
      `tools/audio/serve.py`, and the `Pool` fan-outs in `sim/run.py:48` and
      `tools/sweep.py:154-161`.
- [ ] For the `Xvfb` case, record the lease against the `xvfb-run` child *and* note that the
      `Xvfb` grandchild carries no repo path — the lease must map screen spec + start time to
      the owning session, since that is the only handle (`tools/reap.py:63-67`).
- [ ] Rewrite `reap.find()` to classify each match into: **orphan** (`ppid == 1`), **expired**
      (a lease whose `expires_at` has passed, or a matching process with no lease at all *and*
      an `etime` beyond a generous floor), **own-session** (lease `session_id` equals this
      one), or **sibling** (a live lease belonging to another session).
- [ ] `--kill` kills orphan, expired and own-session. It **never** kills sibling. Print
      siblings in the report with their session id and "not killed — belongs to another
      session", so the information is not lost.
- [ ] Add `--all` for the deliberate nuclear option, with a printed warning naming the count of
      sibling processes it is about to end. The owner must be able to clear a machine.
- [ ] Keep the no-lease case safe in the *right* direction: a matching process with no lease is
      exactly the pre-lease survivor this tool was written for, so after a grace period it is
      killable — but report it as "unleased" so a tool that forgot to acquire one is visible.
- [ ] Add `--json` output so {{PRC-06}}'s `SubagentStop` hook and {{PRC-15}}'s ownership
      tooling can consume the result.
- [ ] Garbage-collect lease files whose pid no longer exists, on every run.
- [ ] Add a gate check `leases wired` asserting that every launch site listed above is inside a
      lease context (grep the call sites, or expose a registry the check reads) — the same
      argument as the `agent models` check.
- [ ] Update `CLAUDE.md`'s reaper paragraph, `docs/STATE.md`'s "Kill what you start", and both
      session skills, replacing "run `--kill` at every wrap" with the scoped semantics.
- [ ] Add a `docs/DECISIONS.md` entry: leases, with the rejected alternatives (process-group
      kill — defeated by the reparenting this file exists for; a pid allowlist per session —
      cannot survive the parent dying, which is the case that matters).

## Acceptance criteria

- With two shells: shell A starts `tools/shot.py anchor-06 --out /tmp/a.png --frames 1800`;
  shell B (different session id) runs `tools/reap.py --kill`. A's capture **completes
  successfully** and B's report lists it as a sibling, not killed.
- Same setup, but B runs `tools/reap.py --kill --all`: A's capture dies and B prints the
  warning naming one sibling.
- A deliberately orphaned Godot (`--fixed-fps` run whose parent is killed, `ppid == 1`) is
  found and killed by `--kill` from any session.
- A lease file whose process is gone is removed on the next run; `ls .cache/leases` shows no
  stale entries after a clean session.
- `tools/reap.py` with no arguments still exits 1 when anything is found and 0 when clean,
  as the session skills depend on.
- `tools/check.py --tier 1` includes `leases wired` and it is green.

## Verification

```bash
# terminal A
.venv/bin/python tools/shot.py anchor-24 --out /tmp/a.png --frames 1800
# terminal B, while A runs
.venv/bin/python tools/reap.py            # lists A as sibling, exit 1
.venv/bin/python tools/reap.py --kill     # must NOT kill A
# back in A: expect a normal SHOT/FRAME relay and exit 0
ls .cache/leases                          # empty after both finish
```

## Risks / gotchas

- **Do not make the reaper timid.** The failure it prevents costs the owner money. If in doubt
  between killing an orphan and sparing it, kill it — an orphan by definition has no session
  to bill.
- The `SessionEnd` hook **does not fire when the CLI is killed outright** (LF-062), so leases
  must expire on a TTL as well as on clean exit, or a hard-killed session leaves permanent
  "sibling" ghosts that make `--kill` useless.
- `xvfb-run`'s cleanup is a shell trap that **measurably does not fire** under this project's
  own concurrency — three `Xvfb` servers survived their Godot children on this machine
  (`tools/reap.py:16-21`). The lease must cover the wrapper *and* the framebuffer.
- `tools/reap.py` is deliberately stdlib-only and dependency-free, and duplicates
  `XVFB_SCREEN_SPEC` as a literal rather than importing `toolpaths`. Keep that property;
  `tools/lease.py` must not drag a dependency in.
- `NEVER = ("blender-mcp", "godot-ai", "reap.py --kill")` exists because killing an MCP bridge
  removes a capability mid-session. Leave it.
- Paste what `reap.py` printed, at every wrap. That instruction is in `CLAUDE.md` and it stays.

## Files likely touched

- `tools/reap.py`, `tools/lease.py` (new)
- `tools/shot.py`, `tools/check.py`, `tools/test_parity.py`, `tools/blender/render.py`,
  `tools/audio/serve.py`, `sim/run.py`, `tools/sweep.py` (lease acquisition only)
- `.gitignore`, `CLAUDE.md`, `docs/STATE.md`, `docs/DECISIONS.md`
- `.claude/skills/session-start/SKILL.md`, `.claude/skills/session-wrap/SKILL.md`
