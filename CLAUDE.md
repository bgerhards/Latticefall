# LATTICEFALL

Isometric tower defense. Godot 4.7.1, GL Compatibility renderer. Sprites are rendered
out of Blender 5.2 LTS, not drawn.

**Read `docs/STATE.md` first every session.** It says what is in flight. This file says
how to work; that file says where we are.

---

## The game in five lines

- You hold transit anchors on a precursor network humanity did not build and cannot switch off.
- Emplacements cost money to build and **draw continuous power** to run. Power is the real currency.
- Exceeding reactor capacity browns the whole bus out — never a blocked build. The penalty
  is **priced by how far over you are**, `min(0.70, (load/cap − 1) × 1.5)`, so a small
  overdraw is cheap and a large one is ruinous. Decision 022; a flat −40% made "never
  exceed capacity" unconditionally correct and the currency was really a wall.
- 24 anchors, 3 acts. Each act introduces one antagonist, one biome, one power tier, and one
  mechanic that invalidates the previous act's dominant strategy.
- Grounded military sci-fi. Professionals doing dangerous technical work. Dry, not heroic.

## Non-negotiables

| Rule | Why |
|---|---|
| Never use terminology from the influencing series | Legal. See `docs/NOMENCLATURE.md` — it has a **banned list**. Check it before naming anything. |
| Game content is data, not code | A level, wave, tower, or dialog line is a JSON file validated against a schema. Lets balance run headless and lets one anchor be edited without loading the codebase. |
| Verify against the installed tool, never from memory | Blender 5.2 removed `scene.node_tree` and turned Glare's settings into sockets. And the iso camera angle in this file was wrong for six sessions because it was derived from memory rather than measured (decision 017). Probe first, every time. |
| Every asset is reproducible from a script | `.blend` files and render scripts are the source. Rendered PNGs are build output that happens to be committed. |
| Loudness-match audio, never peak-normalize | Peak normalization is why programmer audio sounds flat. |

---

## Layout

```
data/            game content — the source of truth for everything tunable
  schema/        JSON Schema for each content type; CI validates against these
  towers.json    emplacement definitions (cost, draw, damage, range)
  enemies.json   unit definitions
  anchors/       one file per level: anchor-01.json … anchor-24.json
  dialog/        one file per level, keyed by trigger
sim/             headless combat simulation — pure Python, no Godot, no rendering
tools/           deterministic scripts. Everything here is runnable and idempotent.
  audio/         synth, ingest, soundboard, loop audition
  blender/       sprite render pipeline
  validate/      schema + data integrity checks
assets/
  audio/{sfx,music}/     committed build output
  renders/               committed sprite output
  blend/                 .blend sources
scenes/, scripts/        Godot
docs/            STATE, BACKLOG, DECISIONS, NOMENCLATURE, STORY
```

## Commands

```bash
.venv/bin/python tools/reap.py                     # what of ours is still running
.venv/bin/python tools/reap.py --kill              # kill it. run at every wrap.
.venv/bin/python tools/check.py                    # the gate, tier 4 (everything, default)
.venv/bin/python tools/check.py --tier 1            # pre-commit
.venv/bin/python tools/check.py --tier 2            # pre-push
.venv/bin/python tools/check.py --tier 3            # PR
.venv/bin/python tools/check.py --list              # every check, its tier, RENDERED tag
.venv/bin/python tools/check.py --json /tmp/g.json # same run, machine-readable
.venv/bin/python tools/gate_report.py /tmp/g.json  # render that as a markdown table
.venv/bin/python tools/wrap_gate.py                # decide a wrap's gate tier from the diff
                                                   # + parity cache state, before anything
                                                   # expensive starts
.venv/bin/python tools/validate/gdscript.py        # parse every .gd, without the full gate
.venv/bin/python tools/backlog.py add "..." --kind bug --area audio
.venv/bin/python tools/validate/validate_data.py   # schemas + cross-references
.venv/bin/python tools/density.py                  # units, leak, hp, drain and screen
                                                   # presence per anchor and per act
.venv/bin/python tools/sweep.py anchor-20 --jobs 8 # grade a grid, one cell per core
.venv/bin/python -m sim.run --jobs 8               # grade every anchor, one per core
.venv/bin/python tools/validate/a11y.py <report.json> --shot <frame.png> --all
                                                   # WCAG AA contrast + text size audit
.venv/bin/python tools/audio/synth_sfx.py          # rebuild SFX bank
.venv/bin/python tools/audio/ingest_music.py       # rebuild music from masters
.venv/bin/python tools/audio/loudness.py           # ITU-R BS.1770-4 / EBU R128 LUFS check
.venv/bin/python tools/save_roundtrip.py           # save/load round trip + recovery draft,
                                                   # two real Godot processes
.venv/bin/python tools/audio/serve.py              # loop audition page
/Applications/Blender.app/Contents/MacOS/Blender -b \
  --python tools/blender/render.py -- --only pulse_turret   # render one asset
.venv/bin/python tools/blender/mask_glow.py        # ALWAYS run after rendering
.venv/bin/python tools/blender/pack_atlas.py       # ALWAYS run after mask_glow
/Applications/Godot.app/Contents/MacOS/Godot --headless --path . --import
                                                   # ALWAYS run after pack_atlas
.venv/bin/python tools/shot.py anchor-01 --out /tmp/shot.png --frames 1800
                                                   # look at the game — no window, ever
.venv/bin/python tools/shot.py anchor-24 --ui-scale 2.0 --out /tmp/s.png \
  --frames 300 --a11y /tmp/s.json                  # frame + its text inventory
```

**`tools/shot.py` is how this session looks at the game.** It launches Godot through
`tools/toolpaths.godot_argv(..., want_window=False)`, which on this machine means the
*native Linux* Godot build under an `Xvfb` virtual framebuffer — a real, GPU-backed (Mesa
llvmpipe software GL) window that no compositor ever presents to a screen, so nothing pops
up and nothing can be occluded. This is what closed LF-061; see decision 052 and
`tools/toolpaths.py`'s module docstring for the mechanics. `toolpaths.godot()` also resolves
whichever Godot binary the current machine actually has — macOS bundle, Windows console
exe, or the preferred Linux build — so a raw `/Applications/Godot.app/...` invocation is no
longer the right way to reach for a screenshot on any machine this project runs on. Reach
for the raw binary only for something `shot.py` does not wrap yet, such as
`--headless --import` after a re-render.

**Verification hooks, because `--fixed-fps` has nobody to press a key.** `--paused`,
`--select N`, `--pick <id>`, `--cursor N`, `--scroll N`, `--options`, `--ui-scale <f>` and
`--display-defaults` each reach a state that otherwise needs a real input. Add one rather
than shipping a screen nobody has looked at. `--facings` is the same idea for something a
screenshot *shows* but cannot settle: it prints the yaw every drawable was drawn at, on the
frame `--shot` captured, because four yaws of one turret differ by which side the muzzle is
on and 40 px of height. Decision 049. `--a11y <path>` must be paired with the
`--shot` on the *same frame*: the analyser samples the background out of that PNG, so a
report taken a frame later describes a screen that was never measured.

**The hooks are reached through `tools/shot.py --extra`, and `--extra` must be the LAST flag
on `shot.py`'s own command line.** It is `argparse.REMAINDER`, so everything after it is
forwarded to Godot verbatim — including tokens shaped like `--ui-scale`, which is the point.
It was `nargs="*"` until `LF-073`, which meant argparse rejected any flag-shaped token and the
tool's own documented example had never once been run; the workaround was to bypass `shot.py`
and drive the Linux binary under `xvfb-run` by hand. All ten forwarded hooks are now verified
against real captures. `--shot-menu` is the exception and does **not** work through `shot.py` —
`LF-109`.

**The interface scale goes to 200% and the worst case is `--anchor anchor-24 --ui-scale
2.0`** — a 960x540 logical viewport, into which the two instrument panels want about
1480 px of stacked height. Both panels scroll vertically with their controls pinned outside
the scroll region; `--scroll N` reaches the scrolled state. Decision 048.

**A re-render is invisible to the game until you re-import.** Godot's *game* mode never
reimports changed assets — it loads the cached `.ctex` in `.godot/imported/`. Only the
editor imports. Skipping this makes a correct art fix look like it did nothing, which has
already cost a full round of misdiagnosis. The order is always: render → `mask_glow` →
`pack_atlas` → `--import` → screenshot.

**`.godot/` is shared with whoever is playing, and rebuilding it blanks their game.** The
owner plays out of the same working tree an agent edits — `D:\dev\Latticefall` and
`/mnt/d/dev/Latticefall` are one directory. Moving or deleting `.godot/` to force a cold
import therefore pulls every imported texture out from under a running session: the menu
still draws, because it is plain UI, and **the level comes up blank**, which reads exactly
like a code regression and is not one. It cost a full diagnosis pass — Windows Godot was
made to load `main.tscn` and run 300 frames clean, four interface scales were checked, and
the save was ruled out, before the cause turned out to be the cache being rebuilt mid-play.

Two consequences. **Never rebuild the import cache without saying so first**, and prefer
`--import` in place over `mv .godot`. And note that an import run by the *Linux* Godot is
not enough for the Windows editor: opening the project there re-imports again, which is the
step that actually fixed it. If the owner reports a blank level, ask whether an import has
just happened before reading any code.

**The board draws from an atlas, not from the loose PNGs.** `pack_atlas.py` packs the 192
renders into one page per pass, so skipping it is a second way to make a correct art fix
look like it did nothing — the stale page keeps serving the old pixels. The gate's
`sprite atlas` check hashes every render and fails if the page no longer matches, so this
mistake is red rather than mysterious. The pack is a **fixed 256 px grid and never trims**:
one measured pivot serves every sprite only because every cell is identical, and trimming
would reintroduce LF-027.

`tools/check.py` is the single gate: schema validation, data cross-references, sim
determinism, asset manifest integrity, Python **and GDScript** syntax. **If it fails, do not
commit.**

**The gate enumerates files with `git ls-files`, never `rglob`.** A denylist of directories
to skip rots — it already produced six false nomenclature hits off an agent worktree under
`.claude/` (LF-051) — whereas "tracked in this repository" is the definition of the thing the
check means. It is also 45 seconds faster on WSL2, where every `stat` crosses a filesystem
boundary: banned terms alone went from 31.9 s to 1.4 s. `addons/` is excluded from the
nomenclature scan as a pathspec, because vendored third-party code is not our naming and a
red run nobody can act on is worse than no check.

**`gdscript parses` runs before `godot boots`, and that order is the point.** A GDScript
parse error is a hang or a blank frame, never an error at the failure site, so `godot boots`
reports it as something else entirely; the parse check names the file and line. It sits in
`tools/validate/gdscript.py`, launches one `--check-only` per script concurrently (31 scripts
in under two seconds), and filters the spurious "Identifier not found" that autoloads produce
when a script is checked in isolation.

**The gate is tiered, and a tier is a *minimum*.** A tier-1 check also runs at 2, 3 and 4;
no flag means tier 4, which is exactly what the gate did before tiering existed and must
never become weaker. A check the tier excluded reports `skip` with `skipped_reason: "tier"`
and stays in the JSON array — **it is not a pass**, and the tally line names every check that
did not run, because a green partial run that reads like a full one is the failure this whole
file exists to prevent. `--budget` turns tier 1's and tier 2's wall-clock budgets into
assertions, so a tier whose cost silently doubles fails rather than quietly stops being used.
**Read `TIER_BUDGET_MS` in `tools/check.py` for the numbers that actually bind** — this
sentence used to name them, tier 2's moved from 25 s to 28 s, and the sentence did not track
it. Tier 2 is currently *over* its own budget on a clean run (`LF-178`), and the fix there is
to move a check out, not to move the number again.

**A count or a cost written into prose here rots within a day.** It happened to the tier table
twice, and `check.py`'s own docstring now carries the counts with a tier-1 check (`tier counts`)
asserting them against the `CHECKS` registry on every run. Costs are deliberately *not*
asserted anywhere — they are machine- and load-dependent, and a generator to keep them honest
would be more machinery than the number is worth. So the live source is `--list` and
`--tier N --json`, and prose that states a number without one of those behind it should be
treated as stale until checked.

**`terrain parsers agree` is the fast-tier sibling of `rules parity`**, and it sits next to it
for that reason: the same class of risk — two implementations of one rule drifting — at a
fraction of the cost. `rules parity` would surface a one-tile terrain drift nine minutes in,
as an unexplained leak with no pointer to terrain at all. Decision 057.

**`--json [PATH]` makes the gate machine-readable.** `tools/gate_report.py` renders it as a
table for a PR comment, and `tools/session.py` prefers the JSON artefact over scraping the
human output, which is how `docs/STATE.md`'s gate block stops carrying a "not re-run" caveat.
The human output is byte-identical whether or not `--json` is passed.

**Grading is parallel; nothing else about it changed.** The sim has no RNG and no shared
state, so `--jobs` on `sim/run.py` and `tools/sweep.py` buys wall-clock and returns the
same cells in the same order. Grading 24 anchors was 3.5 minutes serially, and a balance
question usually needs the box widened two or three times before it answers.

**A wave's unit count is not its screen presence.** A Column at 0.5 tiles/sec holds the
board four times as long as a Shard, so `tools/density.py` reports peak units in flight
alongside the per-wave count, and the gate's `wave density` check compares acts on that.

---

## Art pipeline facts (verified on this machine — do not re-derive)

- **True 2:1 isometric.** Camera elevation is **30.0°** (`arcsin(0.5)`), orthographic.
  Every asset renders at yaw 45 / 135 / 225 / 315.
  **Not** `atan(1/2)` = 26.5651° — that yields a 2.222:1 tile and was wrong in this file
  until it was measured. See decision 017. Orthographic scale for a render `W` px wide is
  `W * sqrt(2) / 128`, which puts a 1x1 world tile on exactly 128x64 px.
- Blender 5.2 registers **only `BLENDER_EEVEE`**. Cycles is not enabled and is not wanted.
- `scene.node_tree` **does not exist**. The compositor is `scene.compositing_node_group`,
  a `CompositorNodeTree`, terminated by `NodeGroupOutput` — `CompositorNodeComposite` is gone.
- Glare node settings are **input sockets**, not Python properties. Values are title-case
  strings: `Type="Bloom"`, `Quality="High"`.
- The emission render pass socket is named **`Emission`** (enable `view_layer.use_pass_emit`).
- `CompositorNodeOutputFile` has `directory`/`file_name`/`file_output_items` — not
  `base_path`/`file_slots` — and its node-level format only accepts `OPEN_EXR_MULTILAYER`.
- Set `view_settings.view_transform = 'Standard'` so sprite colour matches in-engine colour.

**Glow renders opaque and must be masked.** The compositor writes alpha 1 across the
whole frame, so an unmasked glow drawn additively lifts the entire 256px cell and the
board fills with bright rectangles. `tools/blender/mask_glow.py` rewrites alpha from
luminance; it is idempotent and must run after every render.

**Colours are authored in sRGB and linearised by `mat()`.** Blender's colour inputs are
scene-linear and `view_transform='Standard'` encodes back to sRGB on write — probed here,
an emission of 0.5 is stored as 188/255. Writing a palette as though it were a display
value renders it roughly three times too light, which is what made the board a light grey
slab and turned every emitter pale (LF-023/020/022). Never put linear values in the palette.

**The sprite pivot is measured, not assumed.** The render camera is raised by
`HEIGHT_BIAS` so tall assets clear the top of the cell, which puts world (0,0,0) ~43px
below the canvas centre. `calibrate()` measures where it actually lands and writes that to
the manifest. A hardcoded `CELL//2` made every sprite draw above its own tile (LF-027).

**A self-screenshot is at 0.75 scale.** The project renders a 1920x1080 logical viewport
into a 1440x810 window with `stretch/mode="canvas_items"`. Comparing screenshot pixels
against tile maths needs the *logical* viewport size, not the image size.

**Glow is never baked into a sprite.** Each asset renders twice: albedo with compositing
*off*, glow with compositing *on* through Glare on the Emission pass. Godot draws the glow
layer additively and modulates it by bus load — so brownout visibly dims every emissive
element in the game. A baked glow cannot dim, and bleeds past the alpha silhouette.

## Audio pipeline facts

- ffmpeg here has **no libvorbis** — only the experimental native `vorbis`. Encode Vorbis
  with libsndfile (`soundfile`), and **stream it in blocks**: libsndfile segfaults on a
  single multi-million-frame write.
- Music masters are **not in git** (415 MB vs a 1 GB LFS quota). They live in
  `~/Latticefall-masters/`, verified by the SHA-256 in `assets/audio/music_manifest.json`.
- Loops are baked: the tail splice best correlating with the head, then an equal-power
  crossfade. No engine-side loop logic.
- Automated seam metrics **do not** describe loop quality here — the crossfade joins samples
  already adjacent in the source, so the seam is continuous by construction. Judge by ear
  with the loop audition page. Do not "improve" the splice scorer against a seam number.

---

## Output Rules

* Be concise. Sacrifice grammar for brevity.
* No walls of text. User will ask for clarification.
* Give one recommendation only. Lead with it.
* Include only actionable information.
* Precision over completeness.

These outrank any habit of explaining. They do **not** outrank reporting faithfully: a
failed check, a skipped step or an unverified claim is actionable information and gets said,
in a line. Brevity is not a licence to round a result up.

## Working agreement

**One recommendation, never a menu.** Per the Output Rules above, and for a reason specific
to this project: options presented without a pick get re-litigated next session. Decide,
say what you decided in a line, do it. If it turns out wrong, correct it and keep going.
Only stop for something genuinely needed from the owner — `docs/DECISIONS.md` §6 of the PRD
is where the genuinely-theirs calls live.

**Keep working.** Do not stop after a task to check in. Finish it, pick the next thing,
and start. Stop only when out of work or explicitly told to.

**No preamble, no recap.** Do not restate what was just read or narrate what is about to be
done. Lead with the result, then the evidence for it.

**Land work in small pull requests, often.** One workstream, one branch, one PR, merged as
soon as it is green — `lf/<epic>-<short-slug>`, e.g. `lf/cam-minimap`. Not because the repo
has other contributors, but because a PR is where CI runs before the work is on `main`, and
because a fifty-commit push is unreviewable in a way that ten five-commit PRs are not. The
chronicle only publishes on a push to `main`, so a merged PR is also what makes the journey
visible.

Push and open the PR when the workstream's own verification passes — `--tier 2` for anything
that does not touch the rules, `--tier 4` when it does. Merge with `gh pr merge --squash
--delete-branch` once CI is green. Do not batch several unrelated workstreams into one branch;
the point is that a red run names one thing.

**Record the journey, and do it in the pull request.** `docs/chronicle/` is a build journal
published to GitHub Pages on every merge to `main` — a documentary of how this game got
made, written as it happens, live at https://bgerhards.github.io/Latticefall/. The
`chronicler` agent owns it and **every PR updates it**: what landed, the numbers behind it,
and the screenshots. It is step 4 of the `ship` skill rather than an afterthought, because
it is the step most likely to be skipped and the one the owner cares most about.

Two rules make it worth having. **It is append-only**: an entry records what was true and
believed on the day it was written, and when something is later overturned you write a *new*
entry saying so and link back — never quietly correct the old one, for the same reason
`docs/DECISIONS.md` is append-only. And **the failures go in**: the perf fix that passed
864-run parity and did nothing, the atlas rebuild that made every sprite flat grey, the stash
that swept eleven files across five workstreams. A journal of only successes is a lie by
omission and a much duller read.

Images must be **copied into `docs/chronicle/assets/` and committed**, never linked from
`/tmp` or from a scratchpad — those vanish, and an entry pointing at a dead image is worse
than one with no image. Note they are ordinary tracked files, not LFS, so publishing never
touches the LFS quota.

**Backlog before work.** Anything discovered mid-task that isn't part of the current task
goes in the backlog, not into the current change. `tools/backlog.py add`.

**Decisions are append-only.** `docs/DECISIONS.md` holds one entry per real decision with
its rejected alternatives. If a question feels already-settled, it is in there — read it
rather than re-opening it. Add an entry when a decision is made, never edit an old one;
supersede it with a new entry that references it.

**State before context.** Long sessions get summarized. `docs/STATE.md` is what survives —
keep it current, and write it for someone with no memory of the conversation.

**Report faithfully.** If a check fails, say so with the output. If something was skipped,
say it was skipped. Half the value of the tooling here is that it makes claims falsifiable.

**Killing `check.py` does not kill its Godot.** The parity check spawns a headless Godot
that survives `pkill -f check.py`, gets reparented to init, and keeps a core at 100%. The
next gate run then takes nearly twice as long for no visible reason. Kill the Godot too,
and confirm with `ps` that nothing is left — the same class of mistake as the background
load-test loops recorded in `docs/STATE.md`.

**A survivor costs money, not just a core.** `tools/reap.py --kill` is the reaper and it is
not optional: run it at every wrap and check it at every start, and paste what it printed.
Recording the trap above in this file for several sessions did **not** stop it happening,
because it relied on someone remembering to run `ps` — so it is a script, a step in both
session skills, and a `SessionEnd` hook in `.claude/settings.json`. Three known survivors:
the parity check's Godot, `tools/audio/serve.py` (`serve_forever()`, no exit condition at
all), and the `--jobs` worker pools of `sim/run.py` and `tools/sweep.py` when their parent
dies. The cost is not the fan: **a background process the agent harness is still tracking
re-invokes the model when it finally exits or emits**, so a forgotten loop bills tokens
against a session that everyone believed was over. This has already spilled the owner's
subscription usage into paid credits once. Treat it as a money bug, not as hygiene.

**The reaper is scoped by leases now, and `--kill` is still not safe in a fan-out.** Every
launch site takes a lease under `.cache/leases/` naming its pid, session, tool, argv and a
TTL, and `reap.py` classifies each stray by walking the parent chain to the lease that owns
it — `orphan`, `expired`, `own-session`, `sibling`, `unleased`. Plain `--kill` spares a
sibling; `--all` ends it and says how many it took. That replaces classification by
command-line shape, which could not tell "leaked" from "mid-capture right now" and which,
during one seven-agent session, listed six to eight processes belonging to other agents that
`--kill` would have destroyed.

**But `CLAUDE_CODE_SESSION_ID` is per top-level CLI session, not per subagent** — a sibling
fanned out by the same orchestrator inherits it, so `own-session` is coarser than "this
agent" and plain `--kill` can still silently end a live sibling's Godot. `LF-133`. Until
that has a finer identifier: **an agent never runs `--kill`**; the coordinator runs it at
wrap, once every agent has reported.

**Captures are bounded to two at a time**, through the same lease. Not politeness —
measured: Godot capture is llvmpipe software GL and one capture takes about eight of this
machine's sixteen cores, so three at once drove load average to 18.6 and turned a
nine-second frame into minutes, which is what made agents hit the command timeout and stall.
`LF-116`.

**Godot capture is invisible now — `LF-061` is closed, not bounded.** `game renders`,
`menu renders` and `accessibility` need a real GPU-backed Godot frame (GL Compatibility
reads back nothing headlessly), but on this machine that no longer means a window on the
owner's desktop. `tools/toolpaths.godot_argv(..., want_window=False)` prefers the native
Linux Godot build and runs it under an `Xvfb` virtual framebuffer via `xvfb-run` — a real
window that no compositor ever presents to a screen, so nothing steals focus and nothing can
be occluded. That was the actual mechanism behind the old failure mode: **macOS throttled a
window it considered occluded**, stalling `await RenderingServer.frame_post_draw` in
`main.gd` until the window was visible again — which is how one `game renders` once took
**36 minutes** and still reported ok. With no desktop in the loop, that stall cannot happen.
Decision 052; `tools/shot.py` is the everyday way to reach this from a session.

`tools/check.py --no-window` still exists, but it is now a **speed** option: skipping the
Godot-launching checks in `RENDERED` — nine as of `PRC-18` (`game renders`, `menu renders`,
`accessibility`, the five per-scenario checks and `save roundtrip`); run `--list` for the live
set — on an otherwise fast gate, not a courtesy to whoever is at the machine.
Reach for it when time matters more than completeness — not out of politeness. A machine
with no native Linux Godot build or no `xvfb-run` installed falls back to a real, visible
window exactly as before, so the old caution still applies there.

**Prefer the foreground, and never leave a watch armed.** Background a command only when the
task genuinely cannot continue without it running concurrently — not to avoid a timeout on
something you are going to wait for anyway. The gate takes eleven minutes; wait for it. If
something *is* backgrounded, it is finished when its exit has been seen and `reap.py` says
clean. Never leave a `Monitor`, a `/loop`, a `tail -f` or a poll loop running past the task
that needed it; each wake-up is a billed turn.

**Subagents run on Sonnet 5.** Every definition in `.claude/agents/` carries
`model: sonnet` in its frontmatter, and it stays there — an agent with no `model:` key
silently inherits the parent, which is Opus, and a fan-out of five Opus agents is the most
expensive thing this project can do by accident. The **built-in** agents (`Explore`, `Plan`,
`general-purpose`) have no frontmatter to carry the key, so pass `model: "sonnet"` in the
`Agent` call itself. Verify with:
`awk 'FNR==1{n=0} /^---$/{n++;next} n==1' .claude/agents/*.md | grep -c '^model: sonnet'` → 5.
The `FNR==1{n=0}` is load-bearing: awk's counter persists across files, so without it only
the first agent is ever inspected and the check reports 1 no matter what the others say.

**`scripts/anchor_sim.gd` may never reference an autoload, and the symptom is not an error.**
`scripts/test/parity.gd` `preload`s the rules and runs them as a `--script` MainLoop, where
autoloads **do not exist**. One `Recoveries.` in that file makes the whole script fail to
load, so `AnchorSimScript.new()` returns a bare `GDScript` with no `new`, and all 1,152
parity rows come back as **empty dictionaries** — the harness then dies on `KeyError:
'anchor'`, pointing at itself rather than at the line that broke. `gdscript parses` stays
green throughout, because `--check-only` resolves autoloads from `project.godot` and the
runtime does not. This is what decision 054 is mechanically for: a recovery transforms the
sim's **inputs** — a field the caller sets, like `sell_refund_bonus` — and never branches
inside the sim. The same applies to `Content`, `Progress`, `Tuning`, `Display` and `Audio`.

**Pass an explicit timeout to anything that runs longer than two minutes.** The Bash tool's
default is 120 s and it does not fail a slow command — it pushes it into the *background*,
where the harness keeps tracking it and re-invokes the model when it eventually exits. Four
agents in one session hit this and each stopped mid-task to wait on a run it had not meant to
background. Telling an agent "never background" does not help, because the agent is not
choosing to. Pass `timeout` (milliseconds, up to 600000) on: `tools/test_parity.py` (~9 min),
`tools/check.py` at tier 3 or 4, `sim/run.py` and `tools/sweep.py` without `--jobs`, a full
Blender library render (~2m12s), and **any `shot.py` capture while the machine is loaded** —
see `LF-116`, where three concurrent captures turned a nine-second frame into minutes.

**`git stash` is not a private operation, and neither is `git add`.** When several agents share
one working tree — which is how this project fans out — the working tree and the index are
*global*. An agent that stashed to get a clean baseline swept up **eleven files across five
workstreams** and left the tree looking like `HEAD`; every other agent then spent minutes
reading `HEAD` content instead of its own edits, which silently invalidates any measurement
taken in that window. It was recovered only because the stash was popped. Likewise a stray
`git add` or `git reset` changes what the coordinator's commit captures — a stage-then-commit
pair lost a commit outright, and the retry loop then committed nothing twelve times in a row
without failing.

So: **an agent never runs `git stash`, `git reset`, `git checkout -- <path>` or `git add`.** To
compare against `HEAD`, use `git show HEAD:<path>` and diff it yourself; to keep a baseline,
copy the file to the scratchpad. The coordinator commits with `git commit --only <paths>`,
which is atomic and index-independent, wrapped in an `index.lock` retry loop because
concurrent agents *will* be holding it. `PRC-15` (a worktree per workstream) is the real fix.

**But `git commit --only` is for MODIFICATIONS to tracked files. It cannot add, it cannot
remove, and it fails silently at both** — reporting success while committing something other
than what the message says. This bit twice in one session (`LF-180`). `--only <directory>`
omitted an untracked file, so a chronicle entry's `index.html` and `chronicle.json` shipped
**without the entry page they link to** — a dead link on the published site, nothing red
anywhere; and naming the new file directly then errors with `did not match any file(s) known
to git`. Separately, 222 files staged for removal with `git rm --cached` still existed on
disk, so `--only` saw them present, **discarded the staged deletion**, and committed six files
while the message claimed 222 removals.

The two escapes, both narrow enough to stay safe in a shared tree:

```bash
git update-index --add <file> && git commit --only <file>   # introduce a NEW file
git diff --cached --name-only                                # must be empty, THEN:
git rm --cached <paths> && git commit                        # remove tracked files
```

**Assert afterwards, every time a commit adds or removes a file** — `git status --porcelain
<dir>` empty for an add, `git ls-files <glob>` for a removal. The failure mode is a successful
commit that did the wrong thing, so the only defence is checking.

**`--user-data-dir` is not a flag this Godot build recognises, and it is ignored *silently*.**
Confirmed against `--help`. A tool that passes it believing it has isolated `user://` is
writing to the **real save** — which is what happened: an early `tools/save_roundtrip.py` ran
twice against the live Linux dev save before an unchanged-mtime check caught it (`LF-175`).
Set `XDG_DATA_HOME` in the child environment, which the Linux backend does honour, **and
assert the default location's mtime is unchanged after the run**. Isolation here has to be
checked, never assumed, because the failure mode is silent and destructive — the same class as
the `.godot/` rule above, on the same shared machine.

**Scope discipline.** Finish the whole task; if part is blocked, finish everything else and
say plainly what was left and why.

## Conventions

- Python: stdlib + numpy/soundfile only, type hints on function signatures, module
  docstring explaining *why* the file exists. Scripts are idempotent and safe to re-run.
- GDScript: `snake_case` members, `PascalCase` classes, typed vars, signals over polling.
- **Type sizes and colours come from `Ui`, never as literals.** They are accessibility
  policy: the ladder starts at `SIZE_BODY = 16` and every colour was solved for a contrast
  ratio against the real composited panel, not picked by eye. Decisions 045 and 046.
- **A new `class_name` is invisible until the editor imports, and the symptom is a hang.**
  Not "unknown identifier" — the global class cache has no entry, so the scene never
  finishes loading. Run `--headless --path . --import` and confirm the name is in
  `.godot/global_script_class_cache.cfg` before running the game.
- **A theme override under a name the theme does not know is accepted in silence.** The
  Button item is `font_disabled_color`; `font_color_disabled` is not an item at all, and
  `menu.gd` set it on all sixteen locked anchors for several sessions. Verify a theme key
  against `ThemeDB.get_default_theme().get_color_list(...)` — the same class of mistake as
  a mistyped `InputEvent`. Likewise `get_theme_font_size("font_size")`, not `("font")`.
- **`Label.clip_text` clips horizontally only.** A zero-height label still draws a full
  line, over whatever is beneath it.
- **No HUD offset is a literal.** `hud.gd` walks a running cursor over measured line
  heights, and its right and bottom edges come from the live viewport rect — the interface
  scale divides the logical viewport, so a panel pinned at 1524 is a sliver at 125%.
- Input goes through the action map, never a raw keycode. Actions are `lf_*` and are
  generated by `tools/godot/setup_input.gd` — **do not hand-edit `[input]` in
  `project.godot`**, because a typo in a serialized `InputEvent` produces an action that
  silently never fires rather than an error. Decision 042.
- Data: `snake_case` keys, IDs are `kebab-case`, every file has `"schema"` naming its schema.
  That key is now **load-bearing, not decorative**: `validate_data.py` discovers every tracked
  `data/**/*.json` and dispatches on it, so a new content type is a schema file plus a `"schema"`
  key and **no validator edit**. It also asserts the reverse — a schema no document exercises is
  an error, which is how `data/tuning.json` sat unvalidated for a session (`LF-064`).
- Commits: conventional prefix, subject under 72 chars, body explains *why* and lists any
  API traps discovered so the next session does not rediscover them.
