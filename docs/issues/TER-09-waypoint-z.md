id: TER-09
title: The lane climbs — waypoint z, consumed from the WAR-01 path migration
labels: engine, content, phase-3
depends: TER-01, WAR-01
milestone: E4 Terrain
---
## Problem

Units are drawn at `Iso.tile_to_screen(at.x, at.y) + _origin`
(`scripts/anchor_view.gd:1021`) from a scalar `dist` along a fixed polyline. On a board
with elevation a unit walking up a ramp onto a plateau keeps drawing at sea level and
sinks through the ground — the lane needs a height, and the height has to interpolate
along the segment or the unit teleports up 32 px at a waypoint.

The naive fix is to look up the terrain height under the unit's current tile. That is
wrong on a ramp (the ramp tile is a *slope*, not a step) and wrong under a bridge deck,
where the tile has two surfaces. The lane's height belongs to the lane.

**The path migration must happen once, inside {{WAR-01}}.** Elevation wants a `z` on
waypoints; multi-lane rewrites `path` entirely. Two schema breaks, two data migrations
across 24 anchors and two 864-run parity re-runs is a waste, and the PRD says so directly
(§3 E5: "Do the path data migration once"). This issue therefore **consumes** the migrated
shape and does not define it.

## Tasks

- [ ] Agree the waypoint `z` field with {{WAR-01}} before either lands: a per-waypoint
      integer level, defaulting to 0, so an unmigrated or flat lane is unchanged.
- [ ] Interpolate `z` along the segment in the presentation layer only:
      `AnchorView.to_screen()` (`:488`) and `drawables()` (`:1014-1021`) already have the
      segment fraction implicit in `sim.point_at(dist)`. Expose a `point_at_z(dist)`
      alongside it, or extend `point_at_xy()` (`scripts/anchor_sim.gd:150`) to return a
      third component.
- [ ] **Decide explicitly whether `z` reaches the rules, and default to no.** Path length
      is `abs(dx) + abs(dy)` in both engines (`sim/content.py:104`,
      `scripts/anchor_sim.gd:139-146`); adding a height term changes every `path_length`,
      every unit `dist` and therefore every wave's pacing, which re-grades all 24 anchors.
      The PRD makes exactly this argument about Euclidean segment lengths (§3 E5). If `z`
      stays out of the length, this issue is presentation-only and parity-free — say so in
      the code, not just here.
- [ ] If height *does* enter path length, it moves in **both** rule files in the same
      commit, uses only the safe operation set (`+ − × ÷ sqrt fmod floor min max`, never
      `atan2 sin cos tan pow log exp`, never `Vector2` — PRD §2.1/§4), and the whole
      campaign is re-graded. Treat that as a separate decision entry with its own sweep.
- [ ] Make the contact shadow (`_draw_contact_shadow()`, `:1135`) follow the interpolated
      lane height, so a unit on a ramp casts its shadow on the ramp.
- [ ] Make the health bar (`_draw_health()`, `:1246`) and the FX layers
      (`fx_additive.gd:209`, `combat_fx.gd`) take the same interpolated position — they
      already read `d["at"]`, so this is free if the offset is folded into `at` as
      {{TER-01}} specifies. Verify rather than assume.
- [ ] Add a `--heights` line per live unit (from {{TER-01}}'s hook) reporting interpolated
      `z`, so a screenshot of a unit mid-ramp is falsifiable.
- [ ] Cross-check with {{TER-08}}: the validator's "lane never steps more than one level
      per tile" rule and the waypoint `z` values must agree with the resolved terrain
      grid under the lane.

## Acceptance criteria

- A unit walking a ramp rises smoothly across the ramp tile — capture frames at the start,
  middle and end of the ramp and show three distinct heights, not two.
- A unit on a plateau draws on the plateau surface; its shadow is on the plateau, not on
  the ground below.
- With `z` kept out of path length: **parity is byte-identical and all 24 grades are
  unchanged.** State this in the PR body with the parity output pasted.
- A flat anchor with no `z` on any waypoint renders and grades exactly as before.
- `--heights` reports a non-integer interpolated `z` for a unit mid-ramp.

## Verification

```bash
.venv/bin/python tools/test_parity.py            # 864/864, digest unchanged
.venv/bin/python -m sim.run --jobs 8             # 24 grades unchanged
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX \
      --shot /tmp/ramp-a.png 240 --heights /tmp/ramp-a.txt
Godot --path . --fixed-fps 60 -- --autoplay --anchor anchor-XX \
      --shot /tmp/ramp-b.png 250 --heights /tmp/ramp-b.txt
```

Proof: the two `--heights` files showing the same unit id at two different interpolated
heights, and the parity digest unchanged.

## Risks / gotchas

- **Do not migrate `path` in this issue.** If {{WAR-01}} has not landed, this issue is
  blocked. Adding a `z` here and a lane rewrite there is the double-migration the PRD
  warns against.
- Interpolating `z` in the *presentation* layer while the rules use a `dist` that ignores
  it is correct and intentional. Write it in a comment, because it looks like a bug.
- `point_at_xy()` returns a `PackedFloat64Array` (`scripts/anchor_sim.gd:150`) — float64,
  deliberately. If a third component is added there, keep it float64 and keep it out of
  any `Vector2`.
- A unit's *facing* comes from its heading (`_unit_heading()`, `:934`, decision 049).
  Height must not enter the heading, or a unit climbing a ramp faces into the sky and
  re-buckets its yaw. Facing stays 2-D.
- Bridges give one tile two surfaces; a lane crossing under a deck must take the ground
  height, not the deck's. That is why the height belongs to the waypoint rather than to
  the tile — {{TER-10}}.

## Files likely touched

- `data/schema/anchor.schema.json` (the `z` field agreed with {{WAR-01}})
- `sim/content.py`, `scripts/anchor_sim.gd`
- `scripts/anchor_view.gd`
- `scripts/main.gd`
