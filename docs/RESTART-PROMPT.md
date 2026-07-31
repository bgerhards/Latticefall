# Restart prompt

*Paste this to start an autonomous session. It is deliberately short — everything it relies
on lives in `CLAUDE.md`, `docs/STATE.md`, and the `session-start` / `dispatch` / `ship` /
`session-wrap` skills, which is where it should live so this prompt does not drift.*

---

Run `/session-start`, then work the Theatre Scale programme until one of three things is
true: there is nothing left to do, you are 100% blocked on a decision only I can make, or I
tell you to stop. Do not stop to check in. Do not wrap early. If a workstream finishes and
another can start, start it.

Delegate. Use `/dispatch` to brief every subagent so ownership and the shared-tree rules are
consistent, and run at most one or two Godot-launching workstreams at once.

**Land work with `/ship`, in small pull requests, often.** One workstream, one branch, one
PR, merged as soon as CI is green. Never batch unrelated work onto one branch.

**Every pull request updates the journal.** Invoke the `chronicler` agent as part of
shipping — a short entry saying what we accomplished, the numbers that back it, and the
screenshots. Copy images into `docs/chronicle/assets/`, never link `/tmp`. The journal is
append-only: when something is later overturned, write a new entry and link back rather than
correcting the old one, and put the failures in on purpose. This publishes to
https://bgerhards.github.io/Latticefall/ on every merge and it matters to me.

Decide things yourself. Quality first, then speed, then cost. A measured "no" is a good
outcome — I would rather have a refusal with numbers than a green tick. Report faithfully:
if a check failed, say so with the output; if a step was skipped, say it was skipped.

Bring me only what is genuinely mine to decide — `docs/DECISIONS.md` and PRD §6 are where
those live. Make it obvious and put it at the top, not buried.

Keep notes as you go so the next session can pick up mid-flight, and adjust for
inefficiencies you hit rather than working around them twice.

---

## What I will actually be asked

- **Owner-gated right now:** indentation (`PRC-11`), whether `PRESSURE_FLOOR` should be
  raised or deleted (`LF-131`), line of sight (`TER-12`), the regional power grid
  (`WAR-11`), and whether a 512 px atlas cell means sprites twice as *big* or the same size
  at twice the *resolution* (`LF-151`).
- **Highest-value open work:** `#83` — `rules parity` has never run in CI, not once. The
  guarantee everything rests on is only ever checked when someone remembers.
