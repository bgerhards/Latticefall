class_name Iso
extends RefCounted
## True 2:1 isometric projection. Decision 002.
##
## Tile width is exactly twice tile height, which requires a camera elevation of
## 30 deg (arcsin 0.5) — the same angle every sprite is rendered at. These constants
## and that angle are one decision; changing either invalidates the sprite library.

const TILE_W: float = 128.0
const TILE_H: float = 64.0


static func tile_to_screen(tx: float, ty: float) -> Vector2:
	return Vector2((tx - ty) * TILE_W * 0.5, (tx + ty) * TILE_H * 0.5)


static func screen_to_tile(p: Vector2) -> Vector2:
	var a := p.x / (TILE_W * 0.5)
	var b := p.y / (TILE_H * 0.5)
	return Vector2((a + b) * 0.5, (b - a) * 0.5)


## Draw order. Painter's algorithm on tile depth; ties broken by x so the result
## is stable frame to frame rather than flickering on equal depth.
static func depth(tx: float, ty: float) -> float:
	return (tx + ty) * 1000.0 + tx


# ────────────────────────────────────────────────────────────────── facing ──

## The rendered yaw that faces tile +x, and the origin of the mapping below.
##
## Measured, not derived. Every asset is modelled facing Blender world **+Y** —
## pulse_turret's muzzle sphere sits at (0, 0.63, 0.70), and the relay dish, the lance
## muzzle and warden_heavy's eye are all on that side — and render.py orbits one camera to
## yaw 45/135/225/315 at 30° elevation. Probing the rendered *glow* pass for pulse_turret,
## whose only emitter is that muzzle, puts it horizontally at (+41, −20) px from the sprite
## pivot at yaw 045, (+41, +20) at 135, (−41, +20) at 225 and (−41, −20) at 315 — within a
## pixel of the projection maths. So the four sprites face the four screen diagonals: NE,
## SE, SW, NW in that order. `tile_to_screen` sends tile +x to screen right-down and tile
## −y to right-up, which pins it to
##
##     yaw 045 → tile ( 0,−1)      yaw 135 → tile (+1, 0)
##     yaw 225 → tile ( 0,+1)      yaw 315 → tile (−1, 0)
##
## i.e. tile-space heading angle = yaw − 135. Getting the handedness backwards is not
## subtle — a unit walking down-left would face up-right — so it is worth the probe rather
## than a remembered sign. Before this, every drawable was hardcoded to yaw 45 and 156 of
## the 208 atlas cells were never sampled. LF-050, decision 049.
const YAW_FOR_PLUS_X: int = 135

## Number of rendered yaws a facing can resolve to. **The single source of truth for the
## yaw count on the GDScript side** — `tools/blender/render.py` carries its own, independent
## `YAW_COUNT` on the Python side (one per language, per LF-108/ART-02, since the two files
## can't share a constant across the Blender/Godot process boundary), and `sprites.gd`
## refuses to load a sprite manifest whose recorded yaw count disagrees with this one. Before
## this, the count was encoded a third time as the bare literals `45.0` and `90` inside
## `yaw_for_heading()` below — raising it there alone silently detached the hysteresis test
## from the bucket width it was supposed to be a fraction of. LF-108.
const YAW_COUNT: int = 4

## Angular width of one yaw's bucket, derived from YAW_COUNT rather than a second constant.
const BUCKET_DEG: float = 360.0 / YAW_COUNT

## Hysteresis expressed as a **fraction of a half-bucket**, not as a fixed degree value.
## LF-108: `YAW_HYSTERESIS_DEG` used to be an independent constant (12.0), chosen when
## `YAW_COUNT` was 4 and a half-bucket was 45.0°. Raising `YAW_COUNT` shrinks the bucket but
## does not move a fixed degree value, so the old constant went from "12° of overshoot
## allowed past a 45° edge" to "12° of overshoot allowed past an 11.25° edge" the moment
## something raised YAW_COUNT to 16 without touching it — a band *wider* than the bucket it
## was hysteresis for, which is what pins a facing to whatever it last was and never lets
## `yaw_for_heading()`'s early return below release it. A fraction of the bucket survives a
## yaw-count change by construction; a degree value is the bug this issue exists to fix.
## Measured (not picked) by `tools/yaw_band.py`'s sweep against anchor-07 — the same anchor
## and the same changes/reversals metric decision 049 used — over `scripts/test/facing.gd`'s
## real combat replay, the actual `Iso.bucket_index_for_heading()` this file ships:
##
##   yaws   frac=0 (raw)     frac=0.25 (chosen)
##      4   84 changes/21 rev   78 changes/16 rev
##      8  126 changes/19 rev  113 changes/16 rev
##     16  307 changes/90 rev  263 changes/59 rev
##
## 0.25 sits almost exactly where decision 049's original 12.0/45.0 = 0.267 landed, cuts
## reversals at every yaw count tested (most at 16, where the freeze risk this issue exists
## for is worst), and leaves 0.25 of headroom under the 0.5 guard below. The reversal count
## keeps falling past 0.25 (to 11/16/48 at frac=0.45), but at the cost of a wider dead zone
## before a facing releases — visible lag, not just fewer reversals — so this stops short of
## the guard rather than chasing the last few reversals to the edge of it.
const YAW_HYSTERESIS_FRAC: float = 0.25

## Degree view of the same band, kept only because `anchor_view.gd` and
## `scripts/test/facing.gd` already take a degree parameter and neither is this issue's to
## rewrite. **Derived, not a second source of truth** — move `YAW_HYSTERESIS_FRAC` and this
## follows; do not edit this number directly.
const YAW_HYSTERESIS_DEG: float = BUCKET_DEG * 0.5 * YAW_HYSTERESIS_FRAC


static func heading_for_yaw(yaw: int) -> Vector2:
	## The tile-space heading a rendered yaw looks like on the board.
	##
	## `cos`/`sin` on a bare angle are banned elsewhere in this codebase (Decision 030 keeps
	## them out of `anchor_sim.gd` so the Python and GDScript reference sims can't drift on
	## float rounding) — but facing is presentation-only, never read back by the sim, so the
	## ban does not apply here. Decision 049. Verified to keep the right handedness at
	## non-4-yaw bucket counts too: this is a plain angle-to-vector conversion with no
	## dependency on YAW_COUNT, so the NE/SE/SW/NW ordering above generalises to any bucket
	## count `bucket_index_for_heading()` below is asked to measure.
	var a := deg_to_rad(float(yaw - YAW_FOR_PLUS_X))
	return Vector2(cos(a), sin(a))


static func yaw_for_heading(heading: Vector2, previous: int = -1,
		hysteresis_deg: float = 0.0) -> int:
	## The rendered yaw nearest a tile-space heading, in **degrees** — the API every current
	## caller (`anchor_view.gd`, `scripts/test/facing.gd`) already uses.
	##
	## Bucketed in *tile* space, not screen space. The projection is 2:1, so the four screen
	## diagonals sit 53° apart across one axis and 127° across the other; bucketing a screen
	## angle would hand two of the four sprites most of the circle.
	##
	## Only correct while `BUCKET_DEG` is a whole number of degrees — true for YAW_COUNT=4
	## (90°) and =8 (45°), false for =16 (22.5°), because a yaw can't be a fraction of a
	## degree through this `-> int` return. That is deliberate scope, not an oversight: this
	## issue (ART-02/LF-108) fixes the yaw-count/hysteresis wiring at the count already
	## shipping (4) and proves the band at 16 via `bucket_index_for_heading()` below, which
	## returns a bucket **index** instead of a degree for exactly this reason. Handing
	## `anchor_view.gd` a non-integer-degree yaw is ART-01's job, once it exists to render
	## the extra sprites those buckets would need. The assert makes the boundary loud rather
	## than a silently-truncated wrong answer the next time YAW_COUNT changes.
	assert(is_equal_approx(BUCKET_DEG, roundf(BUCKET_DEG)),
			("yaw_for_heading() returns whole degrees; YAW_COUNT=%d gives a %.3f° bucket " +
			"that isn't one — use bucket_index_for_heading() instead (see ART-01)")
			% [YAW_COUNT, BUCKET_DEG])
	if heading.length_squared() < 1e-12:
		return previous if previous >= 0 else YAW_FOR_PLUS_X
	if previous >= 0 and hysteresis_deg > 0.0:
		var off := absf(rad_to_deg(heading_for_yaw(previous).angle_to(heading)))
		if off <= BUCKET_DEG * 0.5 + hysteresis_deg:
			return previous
	var deg := rad_to_deg(atan2(heading.y, heading.x))
	return wrapi(YAW_FOR_PLUS_X + int(roundf(BUCKET_DEG)) * roundi(deg / BUCKET_DEG), 0, 360)


static func bucket_slot(bucket: int) -> String:
	## The one place the "bNN" slot-name string is written down on the GDScript side
	## (ART-01). Mirrors `tools/blender/render.py`'s `bucket_slot()` byte-for-byte —
	## Python and GDScript can't share a constant across the Blender/Godot process
	## boundary (the same problem `YAW_COUNT` solves differently, see that file's own
	## module docstring), so this stays a small, obviously-correct one-liner in both
	## languages rather than derived independently at each call site. Zero-padded to 2
	## digits: 16 buckets is the largest count anything renders today, and "b00".."b15"
	## sorts lexicographically the same as numerically, which is what
	## `tools/blender/pack_atlas.py`'s `sorted(by_yaw)` depends on to pack
	## deterministically.
	return "b%02d" % bucket


static func bucket_index_for_heading(heading: Vector2, yaw_count: int,
		previous_index: int = -1, hysteresis_frac: float = 0.0) -> int:
	## The rendered-yaw **bucket index** (0 .. yaw_count-1) nearest a tile-space heading,
	## generic over `yaw_count` — unlike `yaw_for_heading()` above, this works whether or not
	## `360.0 / yaw_count` is a whole number, because it never has to express the answer as a
	## degree. This is the primitive `tools/yaw_band.py` sweeps across YAW_COUNT=4/8/16 to
	## measure the hysteresis band (LF-108); it is not wired into any production call site —
	## `yaw_for_heading()` above is still what `anchor_view.gd` calls, unchanged, so the
	## YAW_COUNT=4 game is bit-for-bit what it was before this file changed.
	##
	## `hysteresis_frac` is the same fraction-of-a-half-bucket `YAW_HYSTERESIS_FRAC` is,
	## applied at whatever `yaw_count` is being measured rather than the shipped one.
	var bucket_deg := 360.0 / float(yaw_count)
	if heading.length_squared() < 1e-12:
		return previous_index if previous_index >= 0 else 0
	var deg := rad_to_deg(atan2(heading.y, heading.x)) - float(YAW_FOR_PLUS_X)
	if previous_index >= 0 and hysteresis_frac > 0.0:
		var prev_deg := float(previous_index) * bucket_deg
		var off := absf(wrapf(deg - prev_deg, -180.0, 180.0))
		if off <= bucket_deg * 0.5 * (1.0 + hysteresis_frac):
			return previous_index
	return wrapi(roundi(wrapf(deg, 0.0, 360.0) / bucket_deg), 0, yaw_count)


static func diamond(center: Vector2, scale: float = 1.0) -> PackedVector2Array:
	var hw := TILE_W * 0.5 * scale
	var hh := TILE_H * 0.5 * scale
	return PackedVector2Array([
		center + Vector2(0, -hh), center + Vector2(hw, 0),
		center + Vector2(0, hh), center + Vector2(-hw, 0),
	])
