# State

*Rewritten at the end of each session. Describes now, for someone with no memory of the
conversation that produced it. Last updated: 2026-07-26 (sim landed).*

---

## Where the project is

**Foundation and pipelines are done. The game itself is not started.**

Audio is complete end to end. Art pipeline is proven but not productionized. There is no
Godot gameplay code at all — no scenes, no scripts, no simulation. Everything built so far
is infrastructure for building the game, plus one anchor's worth of content data.

Be honest about this when reporting progress: three subsystems exist and zero of the game.

## What works

| | |
|---|---|
| **Audio — music** | 14 Suno tracks ingested. 415 MB WAV → 28 MB Ogg, 35.9 min. Loops baked with correlation-matched splices, auditioned by ear and approved. Masters live outside git in `~/Latticefall-masters/`, verified by SHA-256 in the manifest. |
| **Audio — SFX** | 35 of ~60 effects, synthesized from code and byte-reproducible. The remaining ~12 organic ones need CC0 sourcing (LF-005). |
| **Art pipeline** | Proven, not productionized. Iso camera, two-pass albedo + glow, Blender 5.2 API traps all mapped. Output so far is blockouts, not art (LF-003). |
| **Content data** | `anchor-01` complete and graded: layout, 6 waves, 12 dialog lines. 5 emplacements, 4 enemies. Schema-validated. |
| **Simulation** | `sim/` — headless, fixed-timestep, **no RNG in the core loop**, so determinism is structural. Grades an anchor across 6 build policies x 3 difficulties in ~0.9 s. |
| **Tooling** | `tools/check.py` gate — **8 checks passing, 0 skipped**. Backlog with stable IDs. 5 agents, 5 skills. |

## What does not exist

- Any Godot scene, script, or running game. `LF-001`. **This is the whole game.**
- Anchors 02–24, and their dialog.
- Sprite atlas packing, and any real (non-blockout) art.

## Next task

**`LF-001` — the Godot gameplay layer.** Everything it needs now exists: validated content
data, a graded anchor, an audio bank, and a reference implementation of the rules in `sim/`.

Build anchor-01 playable end to end: iso grid, path, build slots, the reactor bus readout,
brownout, waves, and the dialog triggers. The simulation rules already live in `sim/engine.py`
— **do not reimplement them from scratch in GDScript and let the two drift.** Where a rule
must exist in both, the sim is the reference and any divergence is a bug.

Use the `godot-engineer` agent; it holds the Compatibility-renderer constraints.

## Open with the user

- **CC0-by-default** for the 12 organic SFX — stated, not explicitly confirmed. Proceeding
  unless told otherwise.
- Nothing else is blocked on a decision.

## Live design concern

`LF-014`. With homogeneous emplacements, overdrawing the bus is **never** rational: N towers
at 60% fire rate is 0.6N effective, which on a slot-limited board is always worse than
running `capacity/draw` at full rate. Brownout is currently a punishment, not a tradeoff.

It should become a real choice once draws are heterogeneous — briefly raising a 40 MW shield
wall on a 60 MW bus, for instance. That is unverified. If it does not hold once anchor-04
unlocks the shield wall, the core hook is weaker than decision 003 assumes and the emplacement
draw table needs rethinking. Check this early, not at anchor 20.

## Traps that have already cost time

Recorded so they are not rediscovered. Full detail in `CLAUDE.md`.

- Blender 5.x removed `scene.node_tree` and turned Glare's settings into input sockets.
- Forgetting Blender's scene wipe leaves the default startup light in, silently washing out
  every render. This produced a wrong diagnosis once already.
- ffmpeg here has no libvorbis; libsndfile segfaults on large single Vorbis writes.
- `requestAnimationFrame` stops dead in a background tab — it froze a page's playhead while
  audio kept playing, and hung an automated test.
- **Do not tune the music loop splicer against a seam metric.** It was tried and measurably
  made loops worse. Judge by ear. See decision 011.
