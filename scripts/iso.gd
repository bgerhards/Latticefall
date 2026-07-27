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


static func diamond(center: Vector2, scale: float = 1.0) -> PackedVector2Array:
	var hw := TILE_W * 0.5 * scale
	var hh := TILE_H * 0.5 * scale
	return PackedVector2Array([
		center + Vector2(0, -hh), center + Vector2(hw, 0),
		center + Vector2(0, hh), center + Vector2(-hw, 0),
	])
