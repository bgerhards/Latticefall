# Latticefall — Theatre Scale

*Product requirements. Written 2026-07-30, after the owner played the build and asked for a
war. Supersedes nothing; extends everything. Every number in the "measured" sections was
measured on this machine against this code — where something was not measured it says so.*

---

## 1. What we are building

Latticefall today is a competent tower defence with one genuinely good idea: **power is the
real currency**. Emplacements cost money to build and draw continuous MW to run, and
exceeding reactor capacity browns the whole bus out at a price set by how far over you are.
That idea is intact and it is the thing everything below must serve.

What the game lacks is **ground**. The largest board is 18×15 tiles, peak screen presence is
32 units, one lane, one entrance, one exit, and a fixed list of 8–12 places you are allowed
to build. The owner's words: *"I want a battle, I WANT A WAR."*

**Theatre Scale is the programme that turns a screen into a battlefield.**

### The five pillars

1. **Ground you cannot cover.** Boards large enough that choosing *where* to make a stand is
   the first decision of every level.
2. **Build where you want.** No slots. Select an emplacement, put the cursor where it should
   go, place it. Fixed slots are the single most limiting thing in the current design.
3. **Terrain that means something.** Elevation and bridges, so a board has high ground,
   dead ground, and places worth holding.
4. **Weapons you can see from across the valley.** Spectacle that is *different shapes of
   draw*, never simply bigger numbers — the power economy has to stay the point.
5. **Power as a front line.** Supply you build, route and defend, and an enemy that can take
   it away.

### The one rule that outranks the pillars

**The rules of the game exist twice** — `scripts/anchor_sim.gd` for the engine and
`sim/engine.py` as the reference — and `tools/test_parity.py` diffs them over 864 runs on
every commit. That is what makes any balance claim in this project falsifiable. Every item
below either keeps both implementations in step or is explicitly presentation-only. Nothing
ships that makes parity a matter of luck.

---

## 2. The measured foundation

This programme rests on measurements taken before any of it was planned. They are here
because several of them **overturn assumptions the codebase is built on**, and a plan that
ignored them would be wrong in expensive ways.

### 2.1 Determinism — decision 030 is partly wrong, and it was blocking us

Decision 030 says the rules use squared distances and axis-aligned paths because *"Godot's
`Vector2` is float32 and `distance_to` is not correctly rounded"*. That bundled two causes,
and only one survives contact with a probe.

Probe: 100,000 float64 pairs across five value regimes, 24 operations, raw IEEE-754 bytes
compared — on CPython, Linux Godot 4.7.1, **and Windows Godot 4.7.1**.

| Operation | Verdict |
|---|---|
| `+ − × ÷`, `sqrt`, `fmod`, `floor`, `min`/`max`, comparisons | **Safe.** 0 mismatches out of 100,000 on all three runtimes. |
| `atan2`, `sin`, `cos`, `tan`, `pow`, `log`, `exp` | **Banned.** Windows Godot (MSVC UCRT) diverges from CPython and Linux Godot (glibc). |
| anything through `Vector2` | **Banned**, unchanged — it is float32. |

- **`sqrt` is correctly rounded by IEEE-754 §5.4.1**, both runtimes issue `SQRTSD`, and it
  matched 100,000/100,000. The culprit in decision 030 was `distance_to`, a *float32*
  helper. Measured: for 2,000,000 points on an exact integer radius, float32 and float64
  disagree on `<= r` **10.2%** of the time. That is the six-leak divergence, quantified.
- Windows divergence rates on gameplay-scale arguments: `atan2` 0.084%, `sin` 0.133%,
  `cos` 0.120%, `pow` 0.130%, `log` 0.031%, `exp` 0.069%, **`tan` 4.32%**. Stable across
  runs — a library difference, not nondeterminism.
- An end-to-end off-grid loop — continuous float64 positions, `sqrt`-normalised directions,
  remainder-carrying advance, 4,000 ticks × 64 units — came out **bit-identical on all
  three runtimes**.

**Consequences.** Off-grid geometry is safe. "Attack in any direction" needs *no angles in
the rules*: a firing arc is `dot(normalise(to_target), facing) >= cos_half_angle`, with
`cos_half_angle` a constant authored in `data/towers.json`. Facing and yaw stay
presentation-only, where decision 049 already put them.

**And a blocker:** `tools/test_parity.py` resolves Godot through `toolpaths.godot()`, which
prefers the Linux build. **All 864 parity runs test a binary the owner does not play.** The
rules happen to use none of the divergent operations today, so cross-platform parity holds
*by accident*.

### 2.2 Scale — what breaks, in what order

The whole game today costs **1.5 ms of a 16.7 ms frame**. There is a great deal of headroom.

| Rank | What breaks | At what scale | Fix |
|---|---|---|---|
| 1 | **No camera** | Already — anchor-24 is 2,112 px against a 940 px strip | Prerequisite for everything |
| 2 | **Sprite legibility when zoomed out** | 48×48 | A design decision, not a knob |
| 3 | Draw path — `drawables()` rebuilt 4×/frame, tile loop uncached | 13.4 ms/frame at 64×64 | **2.47× measured** |
| 4 | Sim tick O(towers×units), ×3 by fast-forward | 25.1 ms/frame | **15–18× measured** |
| 5 | Hit-flash scan — the one quadratic term | 7.7 ms at 900 units | Bucket the lookup |
| 6 | Single-path rules | Day one | ~200 lines, both engines |

**The sim budget is not 16.7 ms — it is 5.6 ms.** The speed control goes to 3×, so the rules
run 90 ticks per second of wall clock. Measured at 512 units / 60 emplacements: today's loop
shape is **25.8 ms per tick**; hoisting `point_at_xy` to one pass per tick brings it to
3.1 ms; adding a uniform grid hash brings it to **1.0 ms**.

Two defects found while measuring, both in shipped code:

- **An emplacement with nothing in range re-scans every unit every tick, forever** — the
  empty-target path never resets the cooldown. Measured **2.19× → 2.87×** cost. This is
  exactly the shape a large multi-lane board produces.
- **`_covered_by()` iterates every emplacement once per unit per tick even when no
  emplacement has that effect at all** — 24,000 wasted iterations per tick at 60/400.

### 2.3 The projection with height — derived and measured

For an orthographic camera at elevation ε, world +Z projects to screen with coefficient
`cos(ε)`. With the calibrated `ortho_scale = 2.784233`:

```
s = CELL / ortho_scale = 256 / 2.784233 = 91.9463 px per world unit
k = s · cos(30°)                        = 79.6279 px per world unit of height
```

Verified by two throwaway Blender renders reproducing the production camera: measured
**k/s = 0.865994** against `cos 30° = 0.866025` — agreement to **0.004%**. It is `cos`, not
`sin`; `sin` would have given 46 px.

- **`LEVEL_PX = 32`** (one elevation level). Integer, so a raised tile lands on a whole
  pixel; two levels is exactly one tile height; it is the standard iso cube proportion.
  World height per level `= 0.401872`, derived from the solved `ortho_scale` — **never from
  the nominal 128**, because the rendered tile is geometrically 130.03 px and the "128" is a
  threshold artefact the calibration bakes in.
- **The depth coefficient is exactly 1/3.** True camera depth is
  `(√3/2√2)(tx+ty) + ½·z`, which normalises to `z_depth_per_level = LEVEL_PX / 96`.
- **A pure heightfield needs no change to `Iso.depth()` at all.** Proven with a prototype:
  a 3-level ridge in front of a tall turret, and a plateau behind a valley lane, render
  *pixel-identically* under today's depth function. Raising a tile always moves it toward
  the camera and never past a tile at larger `tx+ty`. **The z term exists solely for
  bridges**, where two surfaces share one tile.
- **Elevation must never be baked into a sprite.** It is a blit offset at draw time. The
  pivot is a property of the camera and is shared by all 26 assets; baking height would be
  LF-027 again.
- **Hard art constraint:** the measured pivot sits at y=171.5 in a 256 px cell, leaving
  **84.5 px below it**. A 2-level cliff face needs 96 px and would clip. **Cliff and pier
  assets are one level tall and stacked at draw time.**

---

## 3. The epics

Ordered so each leaves the game shippable. Full task breakdowns live in `docs/issues/`,
one file per GitHub issue, with dependencies declared as ids.

### E1 — Process and pipeline *(do first; it pays for everything after)*

The project has 324 lines of `CLAUDE.md`, 441 of `STATE.md`, five prose skills, five prose
agent definitions — and **one hook, of the weakest kind**. Decision 051 already established
that a rule written in prose does not hold. In one session of seven parallel agents: five
backgrounded a long run and orphaned it, one blanked the owner's running game by rebuilding
the import cache, and a parse error masquerading as a hang cost time five times.

Highlights, all measured:
- A **GDScript parse check** across all 27 files takes **1.59 s** and catches the
  highest-frequency failure in the project. Exit code alone is unusable — spurious
  `Identifier not found: <autoload>` must be filtered by parsing `[autoload]`.
- **`git ls-files` instead of `Path.rglob`**: banned-terms goes **28.7 s → 436 ms**;
  42 s of the gate is filesystem walking on the drvfs mount.
- **Tiered gate**: ~6 s pre-commit, ~14 s pre-push, ~66 s PR, ~9 min nightly. Parity is
  83.6% of gate time and is a pure function of four paths — hash them and skip when
  unchanged.
- **`reap.py --kill` is friendly fire.** Observed live: seven legitimate sibling-agent
  processes that a literal reading of the wrap skill would have killed. Needs leases.
- **There is no CI at all.** No `.github/workflows`.

### E2 — The camera and reading a big board

Prerequisite for everything spatial. Pan, zoom, edge-scroll, cursor-follow, plus a minimap
with threat and power overlay. Two corrections to the existing scoping: `AnchorView.position`
is now taken by the screen-shake trauma system so a camera must *compose* with it, and
`backdrop.gd` sizes itself to the raw viewport independently of the board, so scaling the
parent tears it — that must be solved first.

**Blocking design question:** fitting 64×64 needs zoom **0.234×** (0.117× at 200% interface
scale), rendering a sprite at 30–60 px — likely below the point where enemy types are
distinguishable. Options: bigger tiles, a zoom floor with the minimap doing the wide read, or
silhouette-first art. **This needs the owner's decision before the camera is finished.**

### E3 — Free placement

Replace `slots` / `free_slots` / `build_at(tower_id, slot)` with a continuous float64
position plus a footprint radius. Validity becomes a rules question in both engines: in
bounds, off the lane by `lane_half_width + footprint`, no overlap — all expressible with safe
operations and no square root if compared squared.

There is **no pathfinding anywhere in the repository** — units advance a scalar `dist` along
a fixed polyline — so free placement introduces **zero lane-blocking risk**.

**The hidden cost, and it is the top risk in this document:** `validate_data.py` bounds
`capacity_mw` against `len(slots) × max_draw`. With no slots, board saturation has no
denominator. That guard exists because the failure already happened: anchor-24 reached 103%
of full-board draw and *the power decision the whole game is about had stopped existing on
five levels*. **Free placement must ship with a replacement invariant.**

Also: the keyboard/gamepad cursor is a discrete graph walk over the slot array. With no
slots it has nothing to walk, and full keyboard/gamepad control is a shipped property.

### E4 — Terrain, elevation and bridges

Elevation is cheap; bridges are what cost. The cheap first slice — **elevation only, no
bridges, no line-of-sight, procedural cliffs** — needs no change to `Iso.depth()` and no
change to `sim/engine.py`, so it is presentation-plus-data and *cannot break parity*.

- Data shape is **regions + ramps**, not a dense heightmap: it is what a generator emits and
  what a human can read. Both anchor parsers must resolve regions to heights by **one shared
  algorithm** — a one-tile disagreement would be findable only by the 9-minute parity gate.
- Terrain draws at **one yaw** (the board never rotates), so ~17 new asset types is **34
  renders**, and the atlas stays at 2 pages.
- **Build cliffs procedurally first.** `board_props.gd` already has the idiom. Zero renders,
  zero atlas growth, and the whole feature can be played and tuned before an asset is
  committed — including whether 32 px is the right step.
- **Terrain must merge into the sorted drawable list**, which collides head-on with the
  4×-per-frame rebuild: at 64×64 that would be 4,096 tiles × 4 rebuilds ≈ **52 ms/frame**.
  Terrain is static, so build its sorted list once at `boot()` and merge in O(n).
- **Range stays 2-D in v1.** True 3-D distance is physically right and *plays wrong* — a
  turret on a hill would cover less plan area than one in the valley. A high-ground bonus is
  the tuning knob, and it invalidates all 24 grades.
- **Line of sight is optional and is the largest single piece of work here.** Made tractable
  by precomputing visibility as path intervals at build time: O(1) per tick, cheaper than the
  range test that already exists. A per-tick raycast is unaffordable and a parity minefield.
- `screen_to_tile` stops being invertible — picking becomes a front-to-back ray walk.

### E5 — Multi-lane, mass, and the war economy

Multiple simultaneous lanes and spawn points; 250–400 units alive; unit roles and formations;
player-built relay nodes; the enemy **pulse** that suppresses rather than damages; and
optionally a regional power grid.

- **Paths must stay axis-aligned** unless deliberately re-costed. Both engines compute
  segment length as `abs(dx)+abs(dy)`. Real Euclidean lengths are now *safe* (§2.1) but they
  change every `path_length`, every unit `dist`, and therefore every wave's pacing — all 24
  anchors re-graded. Cost it separately.
- **Do the path data migration once.** Elevation wants a z on waypoints; multi-lane rewrites
  `path` entirely. Two schema breaks and two parity re-runs is a waste.
- **The pulse cannot be GDScript-only.** Unlike the player's abilities (decision 033), it is
  driven by the core loop whether or not anyone presses anything, so it must exist in both
  rule files or an anchor is graded against weaker rules than it is played against. Upside:
  the existing 864-run sweep then exercises it for free.
- **`set_online()` is not reusable for suppression** — it is an unconditional write reachable
  from a player click, so the player could simply switch a suppressed gun back on.
- **New generation is nearly free**: `effect: {type: "restore"}` is already summed by
  `capacity()` in both engines. A reactor emplacement is *a new row in `towers.json`*.
- **The regional grid is the expensive one** and is recommended against for now. It rewrites
  the hottest parity-sensitive loop and touches eight tools, the briefs that read capacity
  aloud, and an instrument column already fighting for vertical space.

### E6 — Spectacle and fidelity

Weapons that are new *shapes of draw*: a **Siege Battery** that spikes the bus during a salvo
and idles near zero, a **Cutting Lance** that ramps damage while held so switching targets
wastes it, a **Spine Driver** that charges off *spare* capacity and therefore rewards staying
under budget.

Fidelity — **owned by a single dedicated agent**, per the owner's instruction:
- **16 yaws, with heads separate from bases.** Bases do not track, so they stay at 4 yaws.
  Measured: ~680 renders for heads-at-16 + bases-at-4 + units-at-8, versus 832 for a flat
  16-yaw library and 208 today — **cheaper *and* better-looking**.
- **Rotating sprites in-engine is not viable**, and this is measured rather than asserted:
  the camera is orthographic at 30°, so world verticals project to exact screen verticals.
  A 22.5° 2D rotation swings the top of a 96 px barrel **36.7 px** sideways. Even a flat
  1×1 footprint deforms — best-fit screen rotation leaves **36.5% residual** at 22.5° world
  yaw and **68.7%** at 45°.
- **`YAW_HYSTERESIS_DEG = 12.0` is larger than half a 16-yaw bucket (11.25°)** and would lock
  every facing permanently. Three places independently encode the yaw count.
- **VRAM is the real constraint**, not render time: the atlas is uploaded uncompressed
  RGBA8, so 16 yaws is **226 MB**. The whole library re-renders in under two minutes.
- **`calibrate()` cannot converge at 384 or 1024 px cells** — it corrects `ORTHO_SCALE` from
  the width ratio only. 256 and 512 work by luck. This blocks any resolution raise.

### E7 — Balance that can see the game

**The largest hole in the project.** `sim/engine.py` expresses none of speed, call-wave, the
kill chain, the three abilities, targeting priority, veterancy or the recovery draft, so all
24 anchor grades describe a player who uses none of them. Scaling the board multiplies that
gap rather than closing it.

The plug-in point already exists: `Sim.run` drives a pre-sorted `(time, action)` queue, and a
policy *script* is the same shape — a deterministic sorted list of `(time, verb, args)`
merged into the same dispatch. Policies with no schedule must run byte-identical so existing
grades stay comparable. A *reactive* agent would threaten determinism; do not start there.

Also required: **capped-core policies** (today every policy is a total preference order and
`_try_build` fills greedily, so a graded build is all-of-one-thing), and a
**position-aware policy** — `_slot_priority()` ranks by distance to the path and would
happily build into blind slots once line of sight exists.

---

## 4. Invariants — the things that must not break

1. **Parity.** 864 runs, both rule implementations, on every commit. Anything in the rules
   moves in both files or it does not ship.
2. **The safe operation set.** `+ − × ÷ sqrt fmod floor min max` and comparisons. Never
   `atan2 sin cos tan pow log exp`, never `Vector2` in the rules. Enforced by a gate check,
   not by memory.
3. **The power decision.** Every board must have a capacity ceiling that makes "what do I
   leave switched on" a real question. Free placement must bring a replacement invariant.
4. **Content is data.** A level, wave, tower, enemy or dialog line is JSON validated against
   a schema.
5. **Assets are reproducible from scripts.** Rendered PNGs are build output.
6. **Accessibility.** Type sizes and colours come from `Ui`; the interface scale reaches 200%;
   full keyboard, mouse and gamepad control.
7. **Nomenclature.** `docs/NOMENCLATURE.md` is the authority and its banned list is legally
   load-bearing.

---

## 5. Sequencing

```
E1 process ──┬─────────────────────────────────────────────► (pays for everything)
             │
E2 camera ───┼──► E3 free placement ──┐
             │                        ├──► E5 multi-lane + mass ──► E7 balance
E4 terrain ──┴────────────────────────┘
                                      └──► E6 spectacle + fidelity
```

- **E1 first.** Two hours of it (`git ls-files`, `shot.py --extra`) is pure win; the parse
  check and the hook set pay for themselves within a day at this rate of change.
- **E2 before anything spatial.** Everything else assumes a board you can see.
- **E4's cheap slice can run parallel to E3** — it is presentation-plus-data and cannot break
  parity. Full terrain with line of sight should follow free placement, not precede it.
- **E7 is not optional and not last in importance.** It is last in sequence only because it
  needs the verbs to exist first.

---

## 6. Open decisions that need the owner

These are not engineering calls and the programme should not guess at them.

| # | Decision | Why it cannot wait |
|---|---|---|
| 1 | **Sprite legibility at zoom-out** — bigger tiles, a zoom floor, or silhouette-first art? | Blocks finishing E2 and sets the art bar for E6. |
| 2 | **Board size target** — 32², 48², 64²? | Sets the culling, atlas and balance budgets. |
| 3 | **Line of sight — in or out?** | Largest single work item; invalidates all 24 grades. |
| 4 | **Regional power grid — in or out?** | Recommended out for now; it is the expensive one. |
| 5 | **Indentation** — tabs or spaces, pinned in `.editorconfig`. | Two lines, a 6,000-line branch waiting, and the editor silently reindents. |

---

## 7. Risk register

| # | Risk | Severity |
|---|---|---|
| 1 | Free placement removes the board-saturation denominator and silently deletes the power decision | **Blocker** |
| 2 | The parity gate never tests the Windows build the owner plays | **Blocker** |
| 3 | Grid-hash tie-break ordering breaking parity intermittently (LF-055 is the precedent) | High |
| 4 | Line of sight in two languages, gated at 864 runs | High |
| 5 | Merging terrain into a drawable list rebuilt 4× per frame — 52 ms/frame if done naively | High |
| 6 | Parity wall-clock at 10× units: 9 minutes → potentially hours | High |
| 7 | Keyboard/gamepad regression from losing the slot graph | Medium |
| 8 | `YAW_HYSTERESIS_DEG` freezing every facing at 16 yaws | Medium |
| 9 | 226 MB uncompressed atlas VRAM at 16 yaws on GL Compatibility | Medium |
| 10 | Two independent anchor parsers disagreeing on region→height resolution | Medium |

---

## 8. What is explicitly out of scope

- Destructible bridges or terrain.
- Reactive (non-scripted) grading agents — they threaten determinism.
- Board rotation. Terrain art assumes one yaw and the whole projection assumes a fixed camera.
- True curved or diagonal lanes, unless the axis-aligned path-length change is separately
  costed and the campaign re-graded.
- 3-D range in v1.
