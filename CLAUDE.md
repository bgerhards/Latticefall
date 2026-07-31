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
.venv/bin/python tools/check.py                    # the gate. run before every commit.
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
determinism, asset manifest integrity, Python syntax. **If it fails, do not commit.**

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

`tools/check.py --no-window` still exists, but it is now a **speed** option: skipping five
extra Godot launches on an otherwise fast gate, not a courtesy to whoever is at the machine.
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
