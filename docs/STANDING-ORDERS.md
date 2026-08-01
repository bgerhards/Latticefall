# Standing orders

*The owner's working instructions for any session on this project. Injected automatically at
session start by the `SessionStart` hook in `.claude/settings.json`, so a fresh session gets
them without anyone pasting. Edit here; do not duplicate into a prompt, or the two drift.*

---

## The context rule — read this before anything else

**Watch the `<total_tokens>N tokens left</total_tokens>` block.** It is emitted after every tool
result because `totalTokensReminder` is set to `countdown` in `.claude/settings.json`. `N` is the
**remaining** context, and the window is **1,000,000**.

| remaining | what to do |
|---|---|
| **above 550,000** | work normally |
| **550,000 – 500,000** | **finish the current PR and stop taking new work.** Do not start a workstream you cannot land. |
| **below 500,000** | **wrap now**, before doing anything else |

Below 500,000 you are past 50%, which is where the owner has seen agents start hallucinating in
other projects. Do not negotiate with the number.

**Wrapping means, in this order:**

1. Land or explicitly park anything in flight — merge the open PR, or commit and say plainly
   what is unfinished. Never leave a workstream half-edited in the tree.
2. Run `/session-wrap`.
3. **Write the narrative half of `docs/STATE.md` yourself** — "Where the project is", "What the
   last session did", the priority list, new traps. The `PreCompact` hook regenerates the AUTO
   block; **it cannot write prose**, and this is the only moment you still have the conversation
   that makes the prose worth anything.
4. Commit and push it.
5. Then keep working normally. Auto-compaction fires on its own; the `PostCompact` hook injects
   the standing orders and a resume instruction, and the loop continues with **no input from the
   owner**.

**You cannot invoke `/compact` or `/clear`** — they are built-in CLI commands, not skills, and
there is no tool for them. You do not need to. Auto-compaction is the trigger; your job is to be
*already wrapped* when it arrives, which is the whole point of wrapping at 50% instead of 95%.

---

## How to work

Read `docs/STATE.md` first, then work the Theatre Scale programme until one of three things is
true: **there is nothing left to do**, **you are 100% blocked on a decision only the owner can
make**, or **the owner tells you to stop.** Do not stop to check in. Do not wrap early (except
for the context rule above). If a workstream finishes and another can start, start it.

**The backlog is the queue.** `docs/STATE.md`'s priority list, then
`.venv/bin/python tools/backlog.py list`. **If the backlog is empty and nothing is in flight,
stop and say so** — that is a legitimate outcome, and inventing work to look busy is worse than
halting.

**Delegate.** Use `/dispatch` to brief every subagent so ownership and the shared-tree rules stay
consistent. Run at most **one or two Godot-launching workstreams** at once.

**Land work with `/ship`, in small pull requests, often.** One workstream, one branch, one PR,
merged as soon as CI is green. Never batch unrelated work onto one branch.

**Every pull request updates the journal.** Invoke the `chronicler` agent as part of shipping — a
short entry saying what was accomplished, the numbers behind it, and the screenshots. Copy images
into `docs/chronicle/assets/`, **never link `/tmp`**. The journal is append-only: when something
is later overturned, write a *new* entry and link back rather than correcting the old one, and
**put the failures in on purpose.** It publishes to <https://bgerhards.github.io/Latticefall/> on
every merge and it matters to the owner.

**Decide things yourself.** Quality first, then speed, then cost. **A measured "no" is a good
outcome** — the owner would rather have a refusal with numbers than a green tick. Report
faithfully: if a check failed, say so with the output; if a step was skipped, say it was skipped.

**Bring the owner only what is genuinely theirs to decide** — `docs/DECISIONS.md` and PRD §6 are
where those live. Make it obvious and put it at the **top**, not buried.

**Keep notes as you go** so the next session can pick up mid-flight, and **adjust for
inefficiencies you hit rather than working around them twice.**

---

## What is actually owner-gated right now

**Nothing.** As of 2026-08-01, PRD §6 reads *"All five are now taken."*

This section exists because a previous restart prompt listed five items as owner-gated that had
all already been decided — indentation (`PRC-11` → decision **068**, tabs), `PRESSURE_FLOOR`
(`LF-131` → **067**, deleted), line of sight (`TER-12` → **069**, out), the regional power grid
(`WAR-11` → **069**, out), and the 512 px atlas cell (`LF-151` → **066**, stays 256). It also
named `#83` (parity never runs in CI) as the highest-value open work; that closed, and parity now
runs on **both Linux and Windows** on every pull request that touches a rules file.

**Do not re-open a decision from a prompt. Check `docs/DECISIONS.md` first** — it is append-only
and it is the authority. If this section still says "nothing", believe it or verify it, but do
not go hunting for the five above.

---

## Highest-value open work

Read `docs/STATE.md`'s priority list — it is rebuilt every session and is more current than this
file. As of 2026-08-01 the top of it is:

1. **`PLC-07`** — the placement UI. `PLC-06` landed the free cursor and a turret now stands at a
   fractional position, so the owner's original complaint is finally addressable in play. The
   ghost and full legality presentation are what remain.
2. **`LF-185`** — slot geometry is bad on far more anchors than the five ever declared, and free
   placement is now the tool to fix it.
3. **`LF-080`** — the layout generator. Decision **073** set the board target to 48², which
   killed hand-authoring, so this is on the critical path before any theatre-scale content.

---

## The standing correction that outranks the backlog

The owner interrupted a session to ask whether it was working on **the game** or on
**infrastructure**. It was mostly infrastructure — one of the first six PRs changed the game.

**Default to game-facing work.** A tooling defect that is annoying but not blocking goes in the
backlog and waits. If you find yourself filling time between agents with tooling fixes, that is
the failure mode; pick up the next game workstream instead.

---

## The loop, and which parts are automatic

```
SessionStart hook ──► these orders injected ──► read STATE.md ──► work the backlog
                                                       │
                     ┌─────────────────────────────────┘
                     ▼
   countdown drops below 500,000 remaining
                     │
                     ▼
   YOU wrap: land or park in-flight work, /session-wrap,
   write STATE.md's narrative, commit, push          ◄── the only manual link,
                     │                                    and the one that matters
                     ▼
   auto-compaction fires on its own
                     │
                     ├─► PreCompact hook: reap + backlog render + STATE AUTO block (81 s)
                     ▼
   PostCompact hook ──► re-injects these orders, reports branch / HEAD /
                        uncommitted count / backlog size, says "resume"
                     │
                     └─► work continues. No input from the owner.
```

**Automatic:** the orders arriving, the mechanical wrap, the resume after compaction, the
reaper at session end.

**Yours:** noticing the countdown, and writing the prose. Nothing else can do either.

**Timings, measured:** the `PreCompact` mechanical wrap is **81 s** — almost entirely the tier-2
gate; reap and backlog-render are ~1 s combined. A tier-4 wrap was measured at **45–60 minutes**
(it re-runs the Windows parity leg locally), which is why `--tier 2` is hardcoded in the hook and
why decision 070 says the gate never sits on the critical path.

**If auto-compaction catches you before you wrapped**, the `PreCompact` hook says so explicitly
and the `PostCompact` hook tells the next reader to treat `STATE.md`'s prose as stale and rebuild
from `git log`, the merged PRs and the journal. That is a safety net, not the plan. The plan is to
wrap at 50%.
