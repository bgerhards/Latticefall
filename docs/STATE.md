# State

*Rewritten at the end of each session. Describes now, for someone with no memory of the
conversation that produced it. Last updated: 2026-07-26.*

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
| **Content data** | `anchor-01` complete: layout, 5 waves, 12 dialog lines. 5 emplacements, 4 enemies. Schema-validated. |
| **Tooling** | `tools/check.py` gate — 7 checks passing, 1 skipped. Backlog with stable IDs. 5 agents, 5 skills. |

## What does not exist

- Any Godot scene, script, or running game. `LF-001`.
- The headless combat simulator. `LF-002`. Until it exists, no balance claim is verifiable
  and `check.py` skips its determinism check.
- Anchors 02–24, and their dialog.
- Sprite atlas packing, and any real (non-blockout) art.

## Next task

**`LF-002` — write the headless combat simulator**, before `LF-001`.

Reason: decision 003 makes power a scalar over time specifically so an anchor can be graded
without rendering. Building the Godot layer first means balancing by hand and by eye, which
is the failure mode the whole data-driven design exists to avoid. The sim also gives
`check.py` its determinism check, which is currently the only skipped one.

It should read `data/`, run fixed-timestep with a seeded RNG, and report per-wave outcome,
peak and mean bus load, and how many distinct builds clear the anchor.

## Open with the user

- **CC0-by-default** for the 12 organic SFX — stated, not explicitly confirmed. Proceeding
  unless told otherwise.
- Nothing else is blocked on a decision.

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
