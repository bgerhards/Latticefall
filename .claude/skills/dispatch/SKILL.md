---
name: dispatch
description: Brief a subagent to work on Latticefall without colliding with other agents in the shared tree. Use before every Agent call. Carries the file-ownership rules and the traps that have actually cost this project time, so each prompt does not re-derive them.
---

# Briefing a subagent

Thirty agents ran in this working tree in one session. Everything below is a rule that was
learned by something going wrong, not a precaution. Paste the **Constraints** block into
every dispatch verbatim and fill in the ownership lists.

## Structure of a good brief

1. **What and why.** Point at the `docs/issues/<ID>.md` spec — they are detailed and are
   the source of truth. Say why the work matters, in one or two lines, because an agent that
   understands the goal makes better calls at the edges than one following a checklist.
2. **What changed recently that it will trip over.** This is the highest-value part and the
   easiest to skip. An agent reading a file that three other agents changed this hour will
   otherwise reason from a stale mental model.
3. **YOU OWN / DO NOT TOUCH**, as explicit file lists. Not directories — `scripts/` is
   shared by five epics and directory ownership serialises the whole programme.
4. **The constraints block**, below.
5. **The verification bar.** Say what proof you will accept. "Verify it works" gets a parse
   check; "prove the trigger fires, with the printed line" gets evidence.
6. **Permission to come back with a no.** The most valuable results this project has had
   were measured refusals — a 512 px atlas that would have rendered every sprite flat grey,
   a perf fix that passed parity and changed nothing. Say that a refusal with numbers is a
   successful outcome, or you will get a green tick instead of the truth.

## Constraints block — paste verbatim

```
- NEVER `git stash`, `git reset`, `git checkout --`, or `git add`. Other agents share this
  working tree and the index is global; a stash once swept eleven files across five
  workstreams and left every agent reading HEAD instead of its own edits, which does not
  error — it silently invalidates every measurement taken in that window. To compare
  against HEAD use `git show HEAD:<path>`; to keep a baseline, copy to the scratchpad.
- NEVER let a command detach. The Bash tool's default timeout is 120 s and it does not fail
  a slow command, it BACKGROUNDS it — and a tracked background process re-invokes the model
  when it exits, billing a session everyone believed was over. Pass `timeout` explicitly, up
  to 600000 ms, on: `tools/test_parity.py` (~10 min, over the ceiling — expect a
  notification), `check.py --tier 3` or `--tier 4`, an unparallelised `sim/run.py` or
  `tools/sweep.py`, a full Blender render, and any capture while the machine is loaded.
- Godot captures do not parallelise, they multiply — one takes ~8 of this machine's 16 cores,
  and three at once turned a nine-second capture into minutes. They are lease-bounded to two
  machine-wide, so a capture that pauses before starting is waiting for a slot, not hung.
  One at a time.
- Do NOT run `tools/reap.py --kill` — `CLAUDE_CODE_SESSION_ID` is per top-level session, not
  per subagent, so a sibling's live Godot classifies as own-session and gets killed. Only the
  coordinator kills, at wrap, after everyone has reported.
- Do NOT commit. Do NOT modify `docs/BACKLOG.md`, `docs/DECISIONS.md` or `CLAUDE.md` —
  report the text instead and the coordinator will apply it.
- Do NOT rebuild or move `.godot/` — the owner plays out of this same tree, and pulling the
  import cache out blanks their level in a way that reads exactly like a code regression.
  An in-place `--import` is fine when authorised.
- `--tier 1` is ~6 s and cheap; use it constantly. Do not run tier 3 or 4 unless asked.
```

## Traps to name when they apply

- **A GDScript parse error is a hang or a blank frame, never an error at the failure site.**
  `--headless --path . --check-only --script res://scripts/<f>.gd` isolates one file in
  under a second. This has cost a full misdiagnosis more than once.
- **`scripts/anchor_sim.gd` may never reference an autoload.** `parity.gd` preloads it as a
  `--script` MainLoop where autoloads do not exist, so one reference makes the whole rules
  script fail to LOAD and every parity row comes back an empty dictionary — while
  `gdscript parses` stays green.
- **A missing sprite or wrong atlas cell renders as flat placeholder grey**, which at a
  glance reads as a lighting change. Sample pixel colour against the expected material
  colour rather than looking at the frame.
- **A verification hook can run, print, and be swallowed** — `tools/shot.py` filters relayed
  markers by prefix, and has silently eaten three working hooks. Add the prefix in the same
  change as the hook.
- **Whole-frame hash comparison does not work on frames containing combat** — the spark
  effects call `randf()` unseeded by design, so two runs of identical source differ.
- **A scratch file in `data/anchors/` is treated as content** by a raw glob and will break
  the density check.
- **`--paused` opens the real pause menu and hides the board.** Use frame-count targeting.

## Scheduling

At most **one or two Godot-launching workstreams at once**; everything else can fan out
wide. Give each agent a disjoint file list, and expect to serialise anything touching
`scripts/anchor_sim.gd`, `sim/engine.py` or `scripts/test/parity.gd` — those three move
together and cannot be split.
