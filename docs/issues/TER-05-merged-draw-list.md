id: TER-05
title: Merged terrain + entity draw list, built once at boot and merged in O(n)
labels: engine, perf, phase-2
depends: TER-01, TER-04, CAM-06, CAM-07
blocks: TER-10
milestone: E4 Terrain
---
## Problem

Terrain is not a background — a raised surface can occlude a unit behind it and be
occluded by one in front, so it has to participate in the same painter's sort as
emplacements and units. Today those are two separate passes: `_draw_board()`
(`scripts/anchor_view.gd:1042`) sweeps every tile back-to-front, then `_draw_entities()`
(`:1148`) sorts and draws the entity list. Merging them collides head-on with the fact
that **`drawables()` is rebuilt four times per frame** — `anchor_view.gd:1152`, `:1154`,
`glow_layer.gd:36`, `fx_additive.gd:209` — and each rebuild sorts from scratch
(`anchor_view.gd:1024`).

The measured consequence (PRD §2.2, risk #5): at 64×64 that is
**4,096 tiles × 4 rebuilds × 3.2 µs ≈ 52 ms/frame**, against a whole-game budget that
currently sits at **1.5 ms of a 16.7 ms frame**. Doing this naively is a 35× regression on
the entire renderer.

The fix is structural, not micro-optimisation: **terrain is static**. Its sorted list is
built once at `boot()` and never rebuilt. Each frame merges the small sorted entity list
into it in O(n) — a linear merge of two sorted sequences, no sort at all.

## Hard prerequisites

**{{CAM-07}} (drawables built once per frame) and {{CAM-06}} (tile culling and caching)
must land first.** Without {{CAM-07}} the merge happens four times a frame instead of
once; without {{CAM-06}} the merge walks all 4,096 tiles even when 200 are on screen.
Neither is optional and neither belongs to this issue.

## Tasks

- [ ] Build the static terrain drawable list in `boot()` (`scripts/anchor_view.gd:129`):
      one entry per tile carrying `depth`, `z`, screen `at` (with the {{TER-01}} height
      offset folded in), and the tile kind (`tile_ground` / `tile_path` / `tile_slot`, and
      later the cliff faces from {{TER-07}}). Sort it once, with the {{TER-04}} total order.
- [ ] Invalidate and rebuild that list on exactly the events that can change it: a new
      anchor, an `_origin` change (`_centre()`, `:289`), an interface-scale change, and
      the editor preview refresh (`_editor_refresh()`, `:197`). Nothing else. Assert in a
      comment that terrain is immutable during a fight — destructible terrain is
      explicitly out of scope (PRD §8).
- [ ] Replace `_draw_board()`'s tile sweep and `_draw_entities()`'s two sorted passes with
      one merged walk: `merge(static_terrain_slice, sorted_entities)` where the terrain
      slice comes from {{CAM-06}}'s culled range.
- [ ] Accept the shadow-ordering change and write it down. `_draw_entities()` currently
      draws **all** contact shadows, then **all** sprites (`:1152`, `:1154`), specifically
      so a nearer sprite's shadow cannot land on a farther sprite already drawn. In a
      merged list that becomes per-entity: shadow, then sprite, then the next entity. This
      is **more** correct against terrain (a shadow now lands on the surface it belongs to,
      under the cliff edge in front of it) and **slightly less** correct between two
      adjacent entities (a near unit's shadow can now clip a far unit's feet). Accept it;
      the terrain case is the common one and the entity case is a few pixels at the ankle.
      Put this trade-off in the code comment, not only in this issue.
- [ ] Keep `drawables()` as the single shared source for `glow_layer.gd` and
      `fx_additive.gd` — the invariant in its docstring ("the two passes cannot disagree
      about contents, facing or depth order", `:998`) survives the rewrite. The glow pass
      must skip terrain entries cheaply rather than looking up a null texture 4,096 times.
- [ ] Benchmark before and after at the target board size with a synthetic anchor. Record
      ms/frame for: today's code at 18×15, today's shape extrapolated to 64×64, and the
      merged implementation at 64×64.
- [ ] Add the frame-cost numbers to `docs/STATE.md` so the next scale decision has a
      measured baseline rather than the PRD's estimate.

## Acceptance criteria

- The terrain list is sorted **once** per anchor. Instrument it: a counter incremented in
  the sort, asserted to be 1 after 600 frames of gameplay.
- Frame time at 64×64 with 250 units is **under 4 ms** for the whole draw path — versus
  the ~52 ms the naive shape costs. If it is not, the issue is not done.
- A unit walking behind a 2-level ridge is occluded by it; the same unit in front of it
  draws over it. Screenshot both.
- A contact shadow falls on the surface the entity stands on, not on the ground plane
  below a raised tile.
- Parity unchanged — this is a draw-path issue and touches no rule.
- Two captures of the same frame remain byte-identical ({{TER-04}}).

## Verification

```bash
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX --shot /tmp/merge.png 600 \
      --heights /tmp/merge-heights.txt --perf /tmp/merge-perf.json
```

The proof is the perf JSON's draw-path ms at the target board size next to the pre-change
number, plus two screenshots — a unit behind the ridge and the same unit in front of it —
and the sort-count assertion printing 1.

## Risks / gotchas

- **Do not land this before {{CAM-07}} and {{CAM-06}}.** A merged list rebuilt 4× per
  frame over an unculled board is the 52 ms/frame regression, and it will be blamed on
  terrain rather than on the rebuild count.
- Do not "optimise" by re-sorting the merged list. It is a **merge** of two already-sorted
  sequences; the moment someone calls `sort_custom` on the union, the O(n log n) is back.
- The terrain list holds screen positions, which depend on `_origin`. `_origin` moves on
  `_centre()` and on interface-scale change (`hud.gd` derives from the live viewport rect,
  and the scale divides the logical viewport). Missing that invalidation is a board that
  silently draws in the wrong place at 125%.
- `board_props.gd` is a separate `CanvasItem` at `DRAW_Z = 1` and deliberately does *not*
  depth-sort against entities (`scripts/board_props.gd:14-18`). Leave that as-is; folding
  props into the merged list is a different issue with its own argument.
- GDScript array-of-Dictionary is heavy. At 4,096 static entries built once, that is
  acceptable; at 4,096 rebuilt per frame it is not. The whole design rests on "once".

## Files likely touched

- `scripts/anchor_view.gd`
- `scripts/glow_layer.gd`, `scripts/fx_additive.gd`
- `scripts/main.gd` (perf hook)
- `docs/STATE.md`
