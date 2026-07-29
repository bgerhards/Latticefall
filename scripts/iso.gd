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

## Degrees of overshoot past the 45° bucket edge before a facing re-buckets. Whoever holds
## the previous yaw wins ties, which is what keeps a target sitting on a boundary from
## strobing between two sprites.
const YAW_HYSTERESIS_DEG: float = 12.0


static func heading_for_yaw(yaw: int) -> Vector2:
	## The tile-space heading a rendered yaw looks like on the board.
	var a := deg_to_rad(float(yaw - YAW_FOR_PLUS_X))
	return Vector2(cos(a), sin(a))


static func yaw_for_heading(heading: Vector2, previous: int = -1,
		hysteresis_deg: float = 0.0) -> int:
	## The rendered yaw nearest a tile-space heading.
	##
	## Bucketed in *tile* space, not screen space. The projection is 2:1, so the four screen
	## diagonals sit 53° apart across one axis and 127° across the other; bucketing a screen
	## angle would hand two of the four sprites most of the circle.
	if heading.length_squared() < 1e-12:
		return previous if previous >= 0 else YAW_FOR_PLUS_X
	if previous >= 0 and hysteresis_deg > 0.0:
		var off := absf(rad_to_deg(heading_for_yaw(previous).angle_to(heading)))
		if off <= 45.0 + hysteresis_deg:
			return previous
	var deg := rad_to_deg(atan2(heading.y, heading.x))
	return wrapi(YAW_FOR_PLUS_X + 90 * roundi(deg / 90.0), 0, 360)


static func diamond(center: Vector2, scale: float = 1.0) -> PackedVector2Array:
	var hw := TILE_W * 0.5 * scale
	var hh := TILE_H * 0.5 * scale
	return PackedVector2Array([
		center + Vector2(0, -hh), center + Vector2(hw, 0),
		center + Vector2(0, hh), center + Vector2(-hw, 0),
	])
