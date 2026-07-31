id: CAM-07
title: Build drawables() once per frame and share it across all four draw passes
labels: phase-2, engine, perf
depends: CAM-06
blocks: TER-05
milestone: E2 Camera
---
## Problem

`anchor_view.gd:997-1024` `drawables()` allocates one `Dictionary` per emplacement and per live
unit, computes depth, yaw (through `_face()`, which applies hysteresis) and screen position for
each, then `sort_custom`s the whole array with a GDScript lambda. Its own docstring says it
exists so *"the two passes cannot disagree"* — but it is called **four times per frame**:

- `anchor_view.gd:1152` — the contact-shadow pass in `_draw_entities()`
- `anchor_view.gd:1154` — the sprite pass, immediately after
- `glow_layer.gd:36` — the additive emissive pass
- `fx_additive.gd:209` — the hit-flash pass

Measured (LF-101): **3.2 µs per entity per call**, so 12.8 µs per entity per frame. Sharing it is
**1.46x** on its own and **2.47x** combined with {{CAM-06}}'s tile caching, at 64x64 with 60
emplacements and 400 units. It is rank 3 in the PRD's break-order table alongside the tile loop,
and it is the term that makes terrain expensive: {{TER-05}} must merge terrain into this sorted
list, and at 64² that is 4,096 tiles x 4 rebuilds ≈ **52 ms/frame** if done naively (PRD risk 5).

There is a behavioural wrinkle hiding in the fix, and it is the reason this is not a pure win:
`_face()` (`anchor_view.gd:985-994`) **mutates** `p["view_yaw"]` and applies
`YAW_HYSTERESIS_DEG` on every call. Four calls per frame means the hysteresis band is currently
evaluated four times per frame. Building once evaluates it once, which changes facing behaviour
slightly — for the better, but observably (decision 049).

## Tasks

- [ ] Cache the list keyed on `Engine.get_frames_drawn()`: `drawables()` rebuilds only when the
      cached frame number differs, otherwise returns the cached array. Lazy on first access, so
      whichever layer draws first pays and the other three do not.
- [ ] **Do not** rebuild in `_process()` and hand it out. `AnchorView` (z 0), `GlowLayer` (z 10)
      and `FxAdditive` (z 14) all draw in the same frame after `_process`, but a `queue_redraw`
      from a signal could land between — a frame-keyed lazy cache is correct under every ordering,
      a `_process`-built one is correct only under the ordering that happens to hold today.
- [ ] Return the same `Array` reference rather than a copy, and document that callers must not
      mutate it. `glow_layer.gd:36` and `fx_additive.gd:209` only read.
- [ ] Handle the `_face()` mutation deliberately: hysteresis now runs once per frame instead of
      four times. Record the change in the commit body, and prove the facings are still correct
      with `--facings` (decision 049's hook exists for exactly this — a facing is not legible
      from a PNG).
- [ ] Confirm no caller depends on the four rebuilds seeing *different* sim state within a
      frame. `fx_additive.gd:207-222` reads `view.sim.point_at(d["ref"]["dist"])` fresh rather
      than using `d["at"]` — check whether that is deliberate or an accident, and unify.
- [ ] Reduce the allocation, not just the count: a `Dictionary` per entity per frame at 400 units
      is 400 allocations a frame. Consider a flat `Array` of small typed records or reusing a
      pooled array sized to `placed.size() + units.size()`. Measure before committing to a
      rewrite — the 1.46x above is from *sharing alone*.
- [ ] Replace the `sort_custom` lambda with a key that sorts without a GDScript callback if the
      profile says the lambda dominates; `Iso.depth()` is already a single float, so a parallel
      key array and `sort()` may beat it. Measure.
- [ ] Leave the merge seam {{TER-05}} needs: terrain is static, so its sorted list is built once
      at `boot()` and merged into the per-frame list in O(n). Expose the entity list and the
      static list as two arrays with one merge point, not as one array terrain has to be
      re-inserted into.
- [ ] Measure with `--profile` (from {{CAM-06}}) at 400 units and at 900, before and after.
- [ ] Close LF-101 in `docs/BACKLOG.md`.

## Acceptance criteria

- `drawables()` executes its build body **once** per drawn frame — instrument it with a counter
  and assert 1 over a 600-frame `--profile` run.
- The frame is pixel-identical before and after at anchor-24 with a full wave on the board,
  at zoom 1.0 (SHA-256).
- `--facings` output before and after differs only in ways explained by hysteresis running once
  instead of four times, and that difference is described in the PR.
- `--profile` shows at least a **1.4x** improvement in total draw time at 60 emplacements /
  400 units on a synthetic 64x64 anchor.
- Turning the glow layer off (`Display.glow = 0`, which returns early at `glow_layer.gd:31`)
  does not change the cached list or leave it stale for the next frame.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/dr-after.png --extra --facings
sha256sum /tmp/dr-before.png /tmp/dr-after.png
diff /tmp/facings-before.txt /tmp/facings-after.txt
.venv/bin/python tools/shot.py anchor-24 --out /tmp/p.png --extra --profile 600
.venv/bin/python tools/check.py --no-window && .venv/bin/python tools/reap.py
```

Proof is: the rebuild counter at 1, the matching PNG hash, the explained `--facings` diff, and
the two `--profile` blocks.

## Risks / gotchas

- **A stale cache draws last frame's positions in one layer and this frame's in another**, which
  is precisely the disagreement `drawables()`' docstring says it exists to prevent. Frame-keyed,
  lazy, never `_process`-built.
- **`_face()` writes into the `placed` dictionary.** `anchor_sim.gd:541-546` notes those records
  are safe to annotate *because they are only ever compared by `slot`* — and {{PLC-01}} removes
  `slot`. Coordinate: the comparison key changes under free placement and this annotation rides
  on it.
- **`GlowLayer` never redrew for the entire shipped life of the game** because it was drawn once
  in `_ready()` before `boot()` set `sim` (`docs/STATE.md`). A caching change that quietly stops
  a layer redrawing would be invisible in exactly the same way. Screenshot every layer.
- Presentation only — no parity exposure. Say so in the PR.
- Do not fold the minimap ({{CAM-04}}) into this list; it needs raw sim state and would inherit
  the yaw mutation.

## Files likely touched

- `scripts/anchor_view.gd` (`drawables`, `_face`, `_draw_entities`)
- `scripts/glow_layer.gd`, `scripts/fx_additive.gd` (call sites; read-only contract)
- `scripts/main.gd` (`--profile`, shared with {{CAM-06}})
- `docs/BACKLOG.md`, `docs/STATE.md`
