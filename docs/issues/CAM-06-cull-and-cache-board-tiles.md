id: CAM-06
title: Cull and cache board tile drawing
labels: phase-2, engine, perf
depends: CAM-01
blocks: CAM-07, TER-05
milestone: E2 Camera
---
## Problem

`anchor_view.gd:1042-1075` `_draw_board()` rebuilds the entire static level every frame: it
recomputes `_path_tiles(anchor)`, rebuilds `slot_set` from `anchor["slots"]`, then walks the
whole grid in painter's order (`for s_ in range(w + h - 1)` / `for x in range(w)`), computing
`Iso.tile_to_screen` and a texture lookup per tile. There is **no culling and no caching**, and
none of it changes during a level — ground, path and slot tiles are fixed at `boot()`.

Measured (LF-077): **0.92 µs per tile per frame**. At 18x15 that is 0.25 ms and invisible; at
64x64 it is 4,096 tiles = **3.78 ms/frame** before a single unit moves, against a 16.7 ms budget
in which the whole game currently costs 1.5 ms. It is rank 3 in the PRD's break-order table and
the first thing that falls over as the board grows. A camera makes it worse in one direction
(zoom-out draws everything) and better in another (zoom-in makes most of it off-screen, which is
what culling exploits).

## Tasks

- [ ] Build the tile list **once at `boot()`**: an array of `{screen_pos: Vector2, kind: String}`
      in the existing painter's order. The `s_`/`x` loop *is* the sort, so the cached array needs
      no `sort_custom` — say so in the docstring, because the obvious mistake is to add one.
- [ ] Invalidate the cache on the two things that can change it: a new anchor (`boot()`) and a
      change to what counts as a slot tile (which disappears entirely under {{PLC-07}}).
- [ ] Cull against the visible rect. With {{CAM-01}} the board transform is known, so transform
      the viewport rect into board space and reject tiles outside it plus one tile of margin.
      Do the rejection on the cached screen position, not by re-projecting.
- [ ] Prefer a linear scan of the cached array with an early bounds test first; measure. Only if
      it is still hot, add a coarse index (row buckets by `tx + ty`, which is the painter's key)
      so the visible span can be found without touching every entry.
- [ ] Do **not** reach for a baked `ImageTexture` of the whole board first. It is the tempting
      answer and it breaks at 64² (a 64² board's projected bounding box is ~8,200 x 4,100 px,
      which is a 134 MB RGBA8 texture on a GL Compatibility renderer already carrying a
      226 MB-at-16-yaws atlas risk). Record that measurement in the commit body so nobody
      re-proposes it.
- [ ] Add a `--profile <frames>` hook to `main.gd` that prints mean and p95 milliseconds for
      each draw layer (`AnchorView._draw`, `GlowLayer`, `FxAdditive`, `CombatFx`) over N frames
      and exits. There is no performance gate yet ({{BAL-05}}, LF-097) and a frame-time claim
      with no hook is a feeling. Follow the existing hook idiom in `_setup_cli()`.
- [ ] Measure before and after at 18x15 (today) and at a synthetic 64x64 anchor, at 400 units,
      at zoom 1.0 and at `ZOOM_MIN`. Paste both numbers in the PR.
- [ ] Leave the cached list extensible: {{TER-05}} must merge terrain into a sorted drawable list
      built once at `boot()` and merged in O(n) — naive per-frame terrain at 64² is ~52 ms/frame
      (PRD §E4, risk 5). Design the structure so terrain can be appended and re-sorted once,
      not per frame.
- [ ] Update `docs/BACKLOG.md` (close LF-077) and `docs/STATE.md`.

## Acceptance criteria

- The rendered frame is pixel-identical before and after at anchor-01, anchor-13 and anchor-24
  at zoom 1.0 (SHA-256 match on the PNG).
- `--profile` reports `AnchorView._draw` at 18x15 no slower than before, and at a synthetic
  64x64 at least **3x** faster at zoom 1.0.
- Zoomed in far enough that a quarter of the board is on screen, tile draw cost falls by roughly
  the culled fraction — i.e. culling demonstrably fires.
- No allocation per frame in the tile path (`Performance.get_monitor(OBJECT_COUNT)` /
  `MEMORY_STATIC` flat across 600 frames).
- Switching anchors rebuilds the cache; playing anchor-13 after anchor-24 does not draw
  anchor-24's tiles.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/tiles-after.png
sha256sum /tmp/tiles-before.png /tmp/tiles-after.png       # identical
.venv/bin/python tools/shot.py anchor-24 --out /tmp/p.png --extra --profile 600
.venv/bin/python tools/check.py --no-window
.venv/bin/python tools/reap.py
```

Proof is the matching SHA-256 plus the two `--profile` blocks, before and after, on the same
synthetic 64x64 anchor.

## Risks / gotchas

- **The synthetic 64x64 anchor must not be committed as content** unless it validates — the gate
  runs `validate_data.py` over `data/anchors/`. Put it under a scratch path the validator does
  not walk, or make it valid and mark it non-shipping.
- **A cached list plus a camera is exactly where an off-by-one culling bug hides**, and it looks
  like a rendering artefact at the board edge rather than like a bug in a cache. Verify at the
  four corners of anchor-24 with {{CAM-03}}'s `--camera`.
- **`_path_tiles()` and `slot_set` are also rebuilt per frame** — they are part of the same
  waste; fold them into the same `boot()`-time pass.
- Presentation only. Nothing in this issue touches `anchor_sim.gd` or `sim/engine.py`, so parity
  is not exposed — say so explicitly in the PR so no reviewer asks for an 864-run.
- The editor preview path shares `_draw_board()` (`anchor_view.gd:1043` docstring): a cache keyed
  on `boot()` must still work when `sim == null` and `Engine.is_editor_hint()` is true.

## Files likely touched

- `scripts/anchor_view.gd` (`_draw_board`, `boot`, new cache members)
- `scripts/main.gd` (`--profile`)
- `docs/BACKLOG.md`, `docs/STATE.md`
