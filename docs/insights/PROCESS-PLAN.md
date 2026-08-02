# Process plan — what two machines' `/insights` reports actually said

*Written 2026-08-01 from `report-2026-08-01-193255.html` (this machine) and
`report-2026-08-01-193354.html` (the origin machine), both archived alongside this file. The
seven changes below are **proposals**, not landed work. Item 1 is the existing `PRC-15`; items
2–6 are filed as `LF-212` … `LF-216`; item 7 is a practice, not a work item. **None has been
implemented.***

---

## Why this document exists

The owner ran `/insights` on two machines that have been building this project at different
times, and asked whether they would give different information.

They do — substantially — and the more useful finding is *why*. Each report can only see its
own machine's transcripts, so each one's ranked friction list is an artefact of its own
sampling window rather than a property of how the work actually goes. Read either one alone
and you would act on the wrong thing.

---

## The two reports, side by side

| | **This machine** (WSL2 / Windows)<br>`…-193255` | **The origin machine**<br>`…-193354` |
|---|---|---|
| Window | 2026-07-30 → 08-02 · 4 days | 2026-05-26 → 08-01 · 13 active days |
| Sessions | 17 total, 10 analysed | 179 total, 56 analysed, 49 graded |
| Messages | 171 | 964 |
| Commits | 177 | 214 |
| Lines | — | +74,918 / −2,980 across 704 files |
| Projects | Latticefall only | five — Latticefall (~8 sessions), product factory (~12), recipe cards (~20), finance app (~5), local infra (~6) |
| Tool mix | Bash 1296 · Edit 136 · Agent 115 | Bash 4083 · Edit 1140 · Read 922 · Write 560 |
| Ranked friction | desktop focus theft · interrupts during dispatch · unverified claims | buggy code (24) · wrong approach (14) · verbose/option menus · unbounded loops |
| Headline advice | "make every run headless" | "give one recommendation" · "deploy after merging" · "worktree per agent" |

### They disagree, and both are locally correct

The origin machine ranks **buggy code** first because it spans thirteen days and five
codebases, most of them without a gate like this project's. This machine ranks **desktop
focus theft** first because its four-day window happens to straddle the single session where
that occurred — one event, in a ten-session sample, becomes a headline.

Neither is wrong about its own data. Both are wrong as a description of the whole.

### And they lag the repository

This machine's top recommendation — a `PreToolUse` hook that blocks any Godot or Blender
command lacking `--headless` or `xvfb-run` — describes work that **decision 052 already
closed**. `tools/toolpaths.godot_argv(..., want_window=False)` runs the native Linux build
under `Xvfb`, and `tools/hooks/guard.py`'s `rule_raw_engine` already denies the bypass. The
report's "fun ending" is about a failure mode that no longer exists.

**So: treat every recommendation in an insights report as a hypothesis to check against the
gate, never as a backlog item.** One of the two headline recommendations this round was
already done.

---

## What to trust: the cross-validated signals

Only what appears independently on **both** machines survives the sampling problem. Five
things do.

1. **High-autonomy delegation is the working mode, and it ships.** 391 commits combined;
   individual sessions merged 9, 10 and 15 PRs unattended.
2. **Agents collide in a shared working tree.** Origin machine: two agents contaminated one
   branch. This machine: the entire `git stash` / `git add` rule apparatus exists because of
   a stash that swept eleven files across five workstreams.
3. **Interrupts happen when a run loops without converging** — not when it is slow. The owner
   tolerates long silence; they do not tolerate visible grinding with no verdict.
4. **Verbose output is friction, and it is distinct from verbose work.** Walls of text and
   option menus were flagged on both machines.
5. **Claims outrun verification.** A wrong root cause filed as a bug, an overstated capability
   a screenshot contradicted, a "not installed" that was installed, five PRs merged and never
   deployed.

---

## What is already solved

Verified in-repo while writing this, so that the proposals below are deltas and not a second
implementation of something that exists:

| Report recommendation | Already covered by |
|---|---|
| Headless / no foreground windows | Decision 052 · `tools/toolpaths.py` · `guard.py:rule_raw_engine` |
| Timeouts on long subprocesses | `guard.py:rule_background` |
| No `git stash` / `reset` / wide `git add` | `guard.py:rule_git_safety` |
| Kill orphaned child processes | `tools/reap.py` + leases · `SessionEnd` hook |
| One recommendation, no walls of text | `CLAUDE.md` Output Rules |
| One journal entry per PR, never batched | `ship` skill step 4 · `check.py` `chronicle current` |
| Pin subagent models | `check.py` `agent models` |
| Brief subagents with file ownership | `dispatch` skill |

---

## The seven changes worth making

Ranked by leverage. Owner has chosen **harness enforcement** — these land as `guard.py` rules,
gate checks and skill steps, not as prose, because this project's own history is that a rule
written only in prose gets violated and a rule with a check behind it does not.

### 1 · Worktree isolation per subagent — `PRC-15`

**The single highest-leverage change**, and the only one both machines' evidence points at
directly. `tools/hooks/guard.py`'s own docstring already names it: *"`PRC-15` (a worktree per
workstream) is the real fix."*

Every shared-tree rule in this repository is compensation for a missing isolation primitive —
the git denials, the coordinator-only commit path, the `index.lock` retry loop, and `LF-133`'s
unresolvable "is this my sibling's Godot or mine?" ambiguity that makes `reap.py --kill` unsafe
for any agent to run. Isolate the trees and all four dissolve at once.

The `Agent` tool now takes `isolation: "worktree"` natively, so this is mostly a briefing
change plus a sweep, not new infrastructure.

- `dispatch` skill gains a worktree section: every **code-writing** subagent launches with
  `isolation: "worktree"`; read-only agents (Explore, grading runs) stay in-tree, where a
  worktree would cost ~150 MB and buy nothing.
- `guard.py` gains `rule_worktree_expected` — **warn, not deny** — when an `Edit`/`Write`
  lands outside the caller's worktree while more than one agent lease is live.
- The existing git-denial rules stay until this is proven in a real fan-out. Retiring them is
  a separate change, after evidence, not part of this one.

Supersedes `LF-133` rather than fixing it.

### 2 · Cull the tooling backlog, then gate against starvation — `LF-212`

A finding **neither report could see**, because neither can read the backlog: of 72 open items,
**32 are `tooling` and 35 are `chore`**. The standing order says "default to game-facing work."
That is true of how sessions are run and false of the queue they run against — which means the
correction is being applied one session at a time to a problem that regenerates.

- One triage pass over all 32 tooling items. Each must name the game work it unblocks, or be
  closed won't-do with a stated reason. Expect roughly 20 closures.
- `tools/backlog.py list` defaults to game-facing areas; `--all` shows the rest.
- New tier-1 check `backlog composition`: fails when non-game-facing work exceeds 40% of open
  items. The drift becomes red rather than invisible.

**"Non-game-facing" means `tooling` + `meta` + `build` together, deliberately.** Filing this
plan's own proposals as `meta` rather than `tooling` would otherwise deflate the number
without a single item changing — the metric has to be immune to the label, or the first thing
it measures is how the labels were chosen. Items 3–6 below are filed as `meta` and *do* count
against the 40%.

### 3 · A convergence budget on investigative work — `LF-213`

The origin machine's dominant friction: six sessions interrupted mid-loop, with twenty-plus
image crops loaded and no verdict ever delivered. Those sessions graded `not_achieved` purely
because nothing was emitted before the owner's patience ran out.

This project's analogue is a balance sweep or a blank-screen diagnosis — the same shape, a
loop that consumes budget without producing a partial answer.

- `dispatch` skill's verification bar gains a **budget line**: state the maximum number of
  sweeps, captures or renders up front, and emit an interim verdict at the halfway mark
  rather than batching everything to the end.
- An investigative agent must return a verdict *with a confidence* even when its budget is
  exhausted. "Still looking" is a failed return, not a partial one.

### 4 · Evidence, or it did not happen — `LF-214`

Both machines, and the class of error the owner has had to personally disprove more than once.
`tools/issues.py close --note` already requires a note — but the note is prose, and nothing
checks that it points at anything.

- `docs/evidence/<ID>/` holds the artefact: a screenshot, a command transcript with its exit
  code, or a gate JSON.
- `tools/issues.py close` requires `--evidence <path>` when the note contains a behavioural
  claim — `works`, `fixed`, `verified`, `renders`, `passes`.
- New tier-1 check `evidence present`: every issue closed within the last 20 commits resolves
  to an artefact that exists.

### 5 · Merged ≠ visible to the owner — `LF-215`

The origin machine's "merged five PRs, deployed none, and the feature stayed invisible." This
project has an exact analogue already documented at length in `CLAUDE.md`: a merged asset or
rules change is invisible until the **Windows** editor re-imports, and the symptom is a blank
level that reads exactly like a code regression. It has already cost one full diagnosis pass.

- `ship` skill gains a step: if the PR touched `assets/renders/**`, any `*.import`, or the
  atlas, both the PR body and the merge report carry a **RE-IMPORT REQUIRED** line.
- The `sprite atlas` check extends to assert that flag is present on such a branch.

### 6 · Drain before wrap — `LF-216`

This machine: a session reported itself wrapped while two workstreams sat parked on background
runs that had already finished. Nothing failed; nobody harvested.

- `session-wrap` skill gains a first step: enumerate every subagent and background run
  dispatched this session with its status. Land each finished one. Park each unfinished one
  with an explicit blocker. No summary is written until that list is clean.
- The wrap report is capped at **15 bullets**; the detail goes to
  `docs/sessions/session-<date>.md` and the report links it rather than pasting it.

### 7 · Keep the insights honest

- Run `/insights` on **both** machines each time, and archive both reports into
  `docs/insights/` so the next comparison has a tracked baseline rather than two files in a
  download folder.
- Check every recommendation against the repository before it becomes a backlog item. This
  round, one of the two headline recommendations was already implemented and the other was
  half-implemented.

---

## What this document is not

None of the seven changes are implemented. This is the analysis and the proposal; the changes
land after the owner reviews them. Item 1 is the existing `PRC-15`; items 2–6 are filed as `LF-212` … `LF-216`, so they are
queued rather than remembered.

## Sources

- [`report-2026-08-01-193255.html`](report-2026-08-01-193255.html) — this machine, 4-day window, Latticefall only
- [`report-2026-08-01-193354.html`](report-2026-08-01-193354.html) — the origin machine, 13-day window, five projects
- [`process-plan.html`](process-plan.html) — this document, in the house style
