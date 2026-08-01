# Standing orders

*The owner's working instructions for any session on this project. Injected automatically at
session start by the `SessionStart` hook in `.claude/settings.json`, so a fresh session gets
them without anyone pasting. Edit here; do not duplicate into a prompt, or the two drift.*

---

## How to work

Run `/session-start`, then work the Theatre Scale programme until one of three things is true:
**there is nothing left to do**, **you are 100% blocked on a decision only the owner can make**,
or **the owner tells you to stop.** Do not stop to check in. Do not wrap early. If a workstream
finishes and another can start, start it.

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

## Wrapping and compaction

`/session-wrap` runs before `/compact`, always, and in that order — a compaction that happens
before the wrap loses the reasoning the wrap is supposed to record.

A **`PreCompact` hook** now runs the *mechanical* half automatically: `tools/reap.py`,
`tools/backlog.py render`, and `tools/session.py --tier 2` to regenerate `docs/STATE.md`'s AUTO
block. **It cannot write the hand-written half** — the "what the last session did", the priority
list, and the traps are prose only the model can produce.

So when compaction is approaching, or when the hook has just fired: **write the narrative half of
`docs/STATE.md` before letting the context go.** It is the file that survives, and it is written
for someone with no memory of the conversation.
