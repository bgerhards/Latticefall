id: TER-11
title: Projectiles and FX draw over decks — accept in v1, measure before spending anything
labels: engine, art, risk, phase-3
depends: TER-10
milestone: E4 Terrain
---
## Problem

`CombatFx` and `FxAdditive` are separate `CanvasItem` nodes at fixed `z_index` **8** and
**14** (`scenes/main.tscn:31`, `:39`), above the entire board, with `GlowLayer` at 10
between them (`scripts/glow_layer.gd:19`). That structure is not incidental: a
`CanvasItem` cannot change blend mode part-way through its own `_draw()`, and `FxAdditive`
sets `BLEND_MODE_ADD` on its material for the whole node (`scripts/fx_additive.gd:29-30`).
Additive tracers, beams, muzzle flash and impact bloom therefore composite over
*everything*, including a bridge deck they should pass behind.

The z term from {{TER-10}} **cannot fix this**. Depth ordering operates inside one
`CanvasItem`'s draw list; `z_index` ordering between sibling nodes is resolved by the
canvas, and no per-primitive depth value crosses that boundary.

**This is accepted in v1.** A tracer clipping over a deck edge for two frames of flight is
a much smaller defect than the alternative, and the alternative has a real cost. But the
size of the defect has not been *seen*, and the PRD is explicit: measure it in a
screenshot before spending anything.

## The fallback, if measurement says it matters

Option 2: split each FX layer into a **below-deck** and an **above-deck** node — four
nodes instead of two, at `z_index` 6/8 and 12/14, each with its own material, with the FX
pool partitioned by the emitter's and target's deck relationship at spawn time. It is
mechanical, it doubles the node count for the FX system, and it needs a rule for a
projectile whose flight crosses the deck plane mid-arc. It is not worth doing on
speculation.

## Tasks

- [ ] Build a screenshot that maximises the defect: an emplacement firing across a bridge,
      a beam weapon held on a target under the deck, an impact bloom under the deck, and a
      field pulse ring (`fx_additive.gd:209`, `FIELD_PULSE_*`) crossing the deck edge.
- [ ] Capture it at 100% and at 200% interface scale, and on the widest anchor, since the
      FX layers do not scale with the board the way the deck does.
- [ ] **Judge it and write the judgement down** in `docs/DECISIONS.md`: accepted, or
      Option 2 scheduled. This is a decision entry either way — "we looked and it was
      fine" is exactly the kind of thing that gets re-litigated in six months.
- [ ] If accepted: add a comment at `fx_additive.gd`'s node docstring and at
      `scenes/main.tscn`'s FX nodes naming the limitation, the reason (`CanvasItem` cannot
      change blend mode mid-draw), and this issue, so the next person does not "discover"
      it as a bug.
- [ ] If accepted: file the residual as a backlog item with `tools/backlog.py add`, not as
      a silent omission.
- [ ] If rejected: implement Option 2, and specify the mid-arc crossing rule explicitly —
      simplest defensible rule is to classify by the **target's** deck relationship for the
      whole flight, because a projectile is short-lived and a rule that switches mid-flight
      produces a visible pop.
- [ ] Either way, confirm the deck's own procedural geometry from {{TER-10}} is in the
      **board's** draw list and not in an FX layer — if a deck ever ends up above z 8 the
      problem inverts and units start drawing under it.

## Acceptance criteria

- A capture exists showing every FX class over a deck, at both interface scales, committed
  under `docs/shots/`.
- A decision entry records the call with the evidence referenced by filename.
- If accepted: the limitation is commented at both the script and the scene, and a backlog
  item exists.
- If Option 2 ships: a projectile fired at a target under a deck is occluded by the deck
  for its whole flight, and one fired at a target on the deck is not; both captured.
- No change to `sim/engine.py` or `scripts/anchor_sim.gd` in either branch — FX is
  presentation, and decision 053 already holds that the fight is drawn from presentation
  signals and the rules never learn they are being watched.

## Verification

```bash
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX \
      --shot docs/shots/deck-fx-100.png 400
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX --ui-scale 2.0 \
      --shot docs/shots/deck-fx-200.png 400 --a11y /tmp/deck-fx.json
```

The `--a11y` report must be paired with the `--shot` on the **same frame** — the analyser
samples the background out of that PNG, so a report taken a frame later describes a screen
that was never measured.

Proof is the two PNGs plus the decision entry quoting them.

## Risks / gotchas

- **Do not attempt to solve this with `z` or with the painter's key.** It is a
  `CanvasItem`/`z_index` boundary, not a sort-order problem. Time spent there is wasted.
- Do not set `BLEND_MODE_ADD` per-primitive "just for this case" — it is a material
  property of the node (`fx_additive.gd:29-30`), and changing it mid-draw is exactly what
  the engine does not support.
- `combat_fx.gd` owns the pool and `fx_additive.gd` draws from it rather than owning one,
  specifically so a projectile cannot exist in one pass and not the other
  (`fx_additive.gd:5-6`). Any partition must partition **both** consistently or that
  invariant breaks.
- Decision 055 — a cosmetic layer may never be able to take the playfield down with it.
  Whatever ships here must fail soft.
- The gate's `game renders` check launches a **visible** Godot window and macOS throttles
  an occluded window, which is how one run took 36 minutes and still passed (LF-061). Ask
  before running the full gate while the owner is at the machine, and offer
  `tools/check.py --no-window`.

## Files likely touched

- `scenes/main.tscn`
- `scripts/fx_additive.gd`, `scripts/combat_fx.gd`
- `docs/DECISIONS.md`, `docs/BACKLOG.md`, `docs/shots/`
