id: ART-06
title: Turret animation — traverse, recoil and reload
labels: art, ui, phase-3
depends: ART-01
milestone: E6 Fidelity
---
## Problem

An emplacement in Latticefall is a still image that swaps to another still image. Facing is
correct (decision 049) and {{ART-01}} makes it fine-grained, but a gun that fires shows nothing
at all: `scripts/anchor_view.gd:997-1024` emits one static drawable per emplacement and
`shot_fired` (`anchor_sim.gd:543`) only reaches `combat_fx.gd`, which draws the *projectile*.
The result is that at any distance the board reads as a set of markers rather than machinery,
and the player cannot tell a gun that is reloading from a gun that has no target — which is
information the rules already have and the screen throws away.

Three animations carry almost all of that: **traverse** (the head sweeping between yaw buckets
instead of snapping), **recoil** (a short offset on the shot), and **reload** (a visible state
between the shot and the next one, sized by `fire_interval`).

## Tasks

- [ ] Decide the technique and record it. Traverse is **interpolation between two rendered
      buckets**, not a rotation — rotating a sprite with height breaks the isometric invariant
      (see {{ART-01}}'s risks: 36.7 px of swing on a 96 px barrel at 22.5°). At 16 buckets the
      step is 22.5°, small enough that a short cross-fade or a one-bucket lead reads as motion.
- [ ] Recoil is a **screen-space offset along the firing direction**, applied to the head layer
      only, decaying over ~0.12 s. It is a translation, so it does not deform anything. Cap the
      offset in pixels and derive the direction from `placed["aim"]`.
- [ ] Reload is a **readout, not a sprite** in the first pass: a thin arc or bar on the base,
      driven by `placed["cooldown"]` against `tower["fire_interval"]`. Both are already on the
      placed record.
- [ ] All three read from the placed record and from presentation-only signals. **No change to
      `scripts/anchor_sim.gd` or `sim/engine.py`** — assert that with a diff in the acceptance
      criteria.
- [ ] Animation state keyed on the placed record (safe: compared only by `slot`), never on a
      unit (LF-055).
- [ ] Suppression ({{WAR-10}}) must be visible in the same language: a suppressed gun should not
      traverse and should not show a reload arc, or the player cannot tell it apart from a gun
      with no target.
- [ ] Budget: at 60 emplacements this is 60 extra draw calls per frame plus the head layer from
      {{ART-01}}. Measure frame time before and after at a realistic board and at 300 units
      ({{WAR-06}}).
- [ ] Respect the accessibility policy: any new colour or size comes from `Ui`, never a literal
      (decisions 045/046). A reload arc is UI drawn on the board and is subject to the same
      contrast requirement as anything else.
- [ ] Add a `--paused` screenshot mid-recoil and mid-reload, using the existing verification
      hooks, because a 0.12 s effect cannot be caught by chance.
- [ ] Run the a11y audit on a frame containing the reload readouts.

## Acceptance criteria

- `git diff --stat scripts/anchor_sim.gd sim/engine.py` is empty.
- A turret changing target visibly sweeps rather than snapping, at 16 buckets.
- A shot produces a visible recoil on the head layer only; the base does not move.
- A reload readout tracks `cooldown / fire_interval` and reaches zero exactly when the gun
  fires again.
- A suppressed emplacement is visually distinct from an idle one and from an offline one —
  three states, three readings.
- Frame time at 60 emplacements and 300 units has not regressed beyond a stated budget.
- `tools/validate/a11y.py` reports no new failures on a frame containing reload readouts.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-12 --out /tmp/recoil.png --extra --paused --cursor 40
.venv/bin/python tools/shot.py anchor-12 --out /tmp/reload.png --ui-scale 2.0 --a11y /tmp/reload.json
.venv/bin/python tools/validate/a11y.py /tmp/reload.json --shot /tmp/reload.png --all
git diff --stat scripts/anchor_sim.gd sim/engine.py
.venv/bin/python tools/check.py --no-window
```

Proof to paste: the empty rule-file diff, the two screenshots, the a11y summary, and the frame
time before/after.

## Risks / gotchas

- **Never rotate a sprite with height.** The whole isometric look is that world verticals
  project to exact screen verticals at 30° elevation. Interpolate between rendered buckets or
  translate; never rotate.
- Cross-fading two buckets doubles the draw calls for the head during a traverse. At 60
  emplacements all traversing at once that is the worst case — measure it, do not assume it is
  free.
- The reload arc is drawn on the board and competes with the range reticle, the selection
  outline and the threat readouts. Decision 035 already had to make selection legible; do not
  regress it.
- `placed["cooldown"]` is a rules field being *read* by presentation. That is fine and already
  done for `aim`; writing to it from the view would not be.
- The FX pool cap (`MAX_FX = 480`) does not cover these if they are drawn directly by
  `anchor_view.gd` rather than pushed into the pool. Decide which, and keep it consistent.

## Files likely touched

- `scripts/anchor_view.gd`, `scripts/combat_fx.gd`
- `scripts/ui.gd` (any new colour or size)
- `tools/blender/render.py` (if traverse needs an intermediate rendered part)
- `docs/DECISIONS.md`
