class_name BoardProps
extends Node2D
## TER-07: `class_name` added so `anchor_view.gd` can call `pillar_cap()`/`pillar_faces()` as
## `BoardProps.pillar_cap(...)` — Godot's static type checker would not resolve a `static
## func` through a bare `preload()` constant (no class identity to resolve against), only
## through an actual class name, the same way every other cross-script static call in this
## codebase (`IsoScript.tile_to_screen()`, itself `class_name Iso`) already works.
##
## Fixed set dressing that belongs to the anchor rather than to the fight: the ring at the
## lane exit, the bindstone at the entrance, ground sigils and platform edging.
##
## Drawn ABOVE the board and entities (see DRAW_Z below), not at the −1 the scene
## authored: AnchorView draws ground tiles and every entity as one CanvasItem, tiled
## edge-to-edge with no gaps, so at −1 anything here that overlapped a tile's own
## footprint — every ground sigil, and most of the ring, which stands tall enough to
## rise into the screen region the mosaic assigns to farther-back tiles — was painted
## over and simply never appeared. Confirmed by probing a bright shape at −1 and again
## at a positive index and screenshotting both; only the second one showed anything.
## Set here, in code, because the .tscn's initial value belongs to another pass.
## Still under CombatFx/GlowLayer/FxAdditive (8/10/14), so debris and the additive
## glow layer read on top of stone and alloy the way they are meant to. Props are
## geometry, not entities — they never depth-sort against units, and pretending they do
## would cost a shared drawable list for one correct frame in a hundred; the one
## deliberate consequence is that the ring now generally wins that contest at the exit
## tile, which reads right — it is what a leaked unit is understood to go through.
const DRAW_Z := 1

const IsoScript := preload("res://scripts/iso.gd")

var view: Node2D

# ─────────────────────────────────────────────────────────────── geometry ──

const RING_OUTER_RX := 72.0
const RING_OUTER_RY := 118.0
const RING_INNER_RX := 44.0
const RING_INNER_RY := 72.0
const RING_BASE_H := 30.0
const RING_BASE_SCALE := 1.28
const RING_TICKS := 40
const RING_BAND_SEGMENTS := 40
const WARD_COUNT := 6                    # six. never seven, never nine. never captioned.
const WARD_HW := 14.0
const WARD_HH := 9.5
const WARD_RADIUS_FACTOR := 0.985

const BIND_H := 46.0
const BIND_SCALE := 0.30

const EDGE_THICK := 46.0

## The ring's own mass reads as unlit stone and alloy with light IN it, not as a
## light-coloured object — a first pass at ALLOY_MID 0.40 rendered as a near-white donut,
## brighter than the emplacements and brighter than anything in the fight, which inverts
## the value hierarchy the brief sets ("everything you add must sit under the units and
## emplacements"). Every structural tone here is now dark; ALLOY_LIGHT survives only as a
## thin rim stroke and the tick marks, never a fill.
const ALLOY_LIGHT := Color(0.58, 0.62, 0.68)
const ALLOY_MID := Color(0.145, 0.16, 0.185)
const ALLOY_DARK := Color(0.075, 0.085, 0.10)
const STONE_DARK := Color(0.035, 0.04, 0.048)
## Deliberately darker than the band it sits in — an unlit ward has to read as a
## recessed socket, not as "the band, but slightly duller" — with its outline (drawn at
## full ALLOY_LIGHT contrast, see _draw_ring) doing the rest of the legibility work.
const WARD_DARK := Color(0.045, 0.05, 0.06)

## Threshold base tint per act: cold Ordinal blue-white in Acts I-II — the ring is
## precursor tech and does not change with who is currently fighting over it — and a
## desaturated, faintly wrong violet-green in Act III, tying the anchor itself to the
## Hollow's advance rather than leaving that entirely to the backdrop.
const THRESHOLD_TINT := {
	1: Color(0.62, 0.86, 0.98),
	2: Color(0.60, 0.84, 0.95),
	3: Color(0.62, 0.55, 0.66),
}
## A lit ward needs to read as *emitting*, not as a pale tile — this layer has no
## additive material to bloom it the way GlowLayer bloomed a sprite's glow pass
## (decision 007), so the colour itself has to carry that. Pushed brighter and more
## saturated than THRESHOLD_TINT, which is deliberately cooler and dimmer because the
## bore is meant to look like glass, not a bulb.
const WARD_LIT := {
	1: Color(0.55, 0.95, 1.00),
	2: Color(0.60, 0.92, 0.85),
	3: Color(0.72, 0.60, 0.88),
}
const THRESHOLD_TINT_ALT := Color(0.42, 0.62, 0.52)   # act III breathes toward this

var _cached_id: String = ""
var _cached_origin: Vector2 = Vector2(INF, INF)
var _act: int = 1

## TER-07: one quad per boundary tile, not one quad for the whole side — `_build_edge()`'s
## own doc explains why. `PackedVector2Array` per entry (a quad or a 2-point rim segment),
## `Array` of them per side.
var _edge_left: Array = []
var _edge_right: Array = []
var _edge_rim: Array = []

var _ring_center: Vector2 = Vector2.ZERO
var _ring_ground: Vector2 = Vector2.ZERO
var _ring_band_quads: Array = []          # PackedVector2Array x N
var _ring_ticks: Array = []               # PackedVector2Array (2 pts) x N
var _ring_wards: Array = []               # {poly: PackedVector2Array}
var _ring_base_left: PackedVector2Array = PackedVector2Array()
var _ring_base_right: PackedVector2Array = PackedVector2Array()
var _ring_base_cap: PackedVector2Array = PackedVector2Array()
var _bore_fill: PackedVector2Array = PackedVector2Array()
var _bore_ring_a: PackedVector2Array = PackedVector2Array()
var _bore_ring_b: PackedVector2Array = PackedVector2Array()

var _bind_ground: Vector2 = Vector2.ZERO
var _bind_left: PackedVector2Array = PackedVector2Array()
var _bind_right: PackedVector2Array = PackedVector2Array()
var _bind_cap: PackedVector2Array = PackedVector2Array()
var _bind_sigil: Array = []               # PackedVector2Array (2 pts) x N

var _ground_sigils: Array = []            # {c: Vector2, strokes: Array[PackedVector2Array]}
var _ring_ground_ticks: Array = []        # PackedVector2Array (2 pts) x N

var _time: float = 0.0


func _ready() -> void:
	view = get_parent() as Node2D
	z_index = DRAW_Z
	set_process(not Engine.is_editor_hint())
	if Engine.is_editor_hint():
		queue_redraw()


func _process(delta: float) -> void:
	_time += delta
	_ensure_built()
	queue_redraw()


func _draw() -> void:
	if view == null:
		return
	_ensure_built()
	if _cached_id == "":
		return
	_draw_platform_edge()
	_draw_ground_sigils()
	_draw_ring_ground_ticks()
	_draw_bindstone()
	_draw_ring()


# ───────────────────────────────────────────────────────────────── build ──

func _ensure_built() -> void:
	# Guard on `sim` rather than just the anchor id: this node's own _process() starts
	# ticking (and CanvasItem gives every new item one free _draw()) before Main._ready()
	# has called AnchorView.boot(), which is what actually runs _centre() and gives
	# `_origin` its real value — read any earlier than that and it is still Vector2.ZERO,
	# and because it never changes again on its own that zero would otherwise get cached
	# forever. `sim` is null for exactly the same window boot() has not run in, so it is
	# the reliable "is the board actually laid out yet" signal, not a redundant check.
	if view.get("sim") == null:
		return
	var origin: Vector2 = view.get("_origin")
	var aid: String = String(view.get("anchor_id"))
	if aid == _cached_id and origin == _cached_origin:
		return
	_rebuild(aid, origin)


func _rebuild(aid: String, origin: Vector2) -> void:
	_cached_id = aid
	_cached_origin = origin
	if aid == "":
		return
	var anchor: Dictionary = view.call("_anchor_data")
	if anchor.is_empty():
		_cached_id = ""
		return
	_act = int(anchor.get("act", 1))

	var rng := RandomNumberGenerator.new()
	rng.seed = hash(aid)

	var grid: Dictionary = anchor.get("grid", {"w": 12, "h": 10})
	var path: Array = anchor.get("path", [])

	_build_edge(int(grid["w"]), int(grid["h"]), origin)

	if path.size() >= 2:
		var exit_pt: Array = path[path.size() - 1]
		var enter_pt: Array = path[0]
		_ring_ground = IsoScript.tile_to_screen(float(exit_pt[0]), float(exit_pt[1])) + origin
		_bind_ground = IsoScript.tile_to_screen(float(enter_pt[0]), float(enter_pt[1])) + origin
		_build_ring()
		_build_bindstone(rng)
		_build_ring_ground_ticks()

	_build_ground_sigils(rng, anchor, origin, path)


func _build_edge(w: int, h: int, origin: Vector2) -> void:
	## TER-07: elevation-aware. Used to be one flat quad per side assuming every boundary
	## corner sat at level 0 (see this function's git history) — that floated the skirt
	## below a raised region touching the plate edge (anchor-01's north row is exactly
	## this case). Now walks each boundary tile individually and extrudes/traces from
	## *that tile's own* resolved height, one quad (or rim segment) per tile, so a height
	## change along an edge shows as a step — the same idiom the interior cliffs use, not a
	## continuous ramp nobody authored.
	##
	## South (`y == h-1`, the +y outer face) and east (`x == w-1`, the +x outer face) are
	## the two directions a 30° camera looks down onto (pillar_faces()'s own doc), so they
	## get the extruded skirt wall. North and west are the hidden pair — no wall, just the
	## thin top-of-plate rim highlight `_draw_platform_edge()` already drew, now following
	## each boundary tile's own height instead of a single flat line.
	_edge_left.clear()
	_edge_right.clear()
	_edge_rim.clear()
	for x in range(w):
		var hgt_s := int(view.call("height_at", x, h - 1))
		var cap_s := pillar_cap(IsoScript.tile_to_screen(float(x), float(h - 1)) + origin,
				float(hgt_s) * IsoScript.LEVEL_PX, 1.0)
		_edge_left.append(pillar_faces(cap_s, EDGE_THICK)[0])

		var hgt_n := int(view.call("height_at", x, 0))
		var cap_n := pillar_cap(IsoScript.tile_to_screen(float(x), 0.0) + origin,
				float(hgt_n) * IsoScript.LEVEL_PX, 1.0)
		_edge_rim.append(PackedVector2Array([cap_n[0], cap_n[1]]))   # -y outer edge

	for y in range(h):
		var hgt_e := int(view.call("height_at", w - 1, y))
		var cap_e := pillar_cap(IsoScript.tile_to_screen(float(w) - 1.0, float(y)) + origin,
				float(hgt_e) * IsoScript.LEVEL_PX, 1.0)
		_edge_right.append(pillar_faces(cap_e, EDGE_THICK)[1])

		var hgt_w := int(view.call("height_at", 0, y))
		var cap_w := pillar_cap(IsoScript.tile_to_screen(0.0, float(y)) + origin,
				float(hgt_w) * IsoScript.LEVEL_PX, 1.0)
		_edge_rim.append(PackedVector2Array([cap_w[3], cap_w[0]]))   # -x outer edge


## TER-07: made `static` (and public — no leading underscore) so `anchor_view.gd` can reach
## these through `preload("res://scripts/board_props.gd")`, the same way every call site in
## this codebase already reaches a static util through `IsoScript.<name>`. Cliff faces and
## ramps are the interior version of exactly the extrusion this file solved for the ring,
## the bindstone and the platform edge — one shared implementation rather than a duplicate
## copy of this geometry living in anchor_view.gd.
static func pillar_cap(base_c: Vector2, height: float, scale: float) -> PackedVector2Array:
	return IsoScript.diamond(base_c + Vector2(0, -height), scale)


static func pillar_faces(cap: PackedVector2Array, height: float) -> Array:
	## cap is [top, right, bottom, left] (Iso.diamond order). The two visible faces of an
	## extruded diamond are left→bottom and bottom→right — the same pair the platform
	## edge extrudes, for the same reason: those are the two edges a 30° elevation camera
	## looks down onto rather than edge-on. In tile space (solving Iso.diamond's four
	## corners back into tile-centre offsets: top=(-.5,-.5), right=(+.5,-.5),
	## bottom=(+.5,+.5), left=(-.5,+.5)) left→bottom is the boundary with the tile's +y
	## neighbour and bottom→right is the boundary with its +x neighbour — TER-07's interior
	## cliff faces pick left/right by which of those two neighbours is lower, using this
	## same mapping rather than re-deriving it.
	var down := Vector2(0, height)
	var left := PackedVector2Array([cap[3], cap[2], cap[2] + down, cap[3] + down])
	var right := PackedVector2Array([cap[2], cap[1], cap[1] + down, cap[2] + down])
	return [left, right]


func _ellipse_points(cx: float, cy: float, rx: float, ry: float, n: int) -> PackedVector2Array:
	var pts := PackedVector2Array()
	for i in range(n):
		var a := TAU * float(i) / float(n)
		pts.append(Vector2(cx + cos(a) * rx, cy + sin(a) * ry))
	return pts


func _build_ring() -> void:
	var base_cap := pillar_cap(_ring_ground, RING_BASE_H, RING_BASE_SCALE)
	var base_faces := pillar_faces(base_cap, RING_BASE_H)
	_ring_base_cap = base_cap
	_ring_base_left = base_faces[0]
	_ring_base_right = base_faces[1]

	_ring_center = _ring_ground + Vector2(0, -(RING_BASE_H + RING_OUTER_RY))
	var cx := _ring_center.x
	var cy := _ring_center.y

	var outer := _ellipse_points(cx, cy, RING_OUTER_RX, RING_OUTER_RY, RING_BAND_SEGMENTS)
	var inner := _ellipse_points(cx, cy, RING_INNER_RX, RING_INNER_RY, RING_BAND_SEGMENTS)
	_ring_band_quads.clear()
	for i in range(RING_BAND_SEGMENTS):
		var j := (i + 1) % RING_BAND_SEGMENTS
		_ring_band_quads.append(PackedVector2Array([outer[i], outer[j], inner[j], inner[i]]))

	_ring_ticks.clear()
	for i in range(RING_TICKS):
		var a := TAU * float(i) / float(RING_TICKS)
		var p0 := Vector2(cx + cos(a) * RING_OUTER_RX * 0.90, cy + sin(a) * RING_OUTER_RY * 0.90)
		var p1 := Vector2(cx + cos(a) * RING_OUTER_RX * 1.05, cy + sin(a) * RING_OUTER_RY * 1.05)
		_ring_ticks.append(PackedVector2Array([p0, p1]))

	_ring_wards.clear()
	for i in range(WARD_COUNT):
		var a := -PI * 0.5 + TAU * float(i) / float(WARD_COUNT)
		var pos := Vector2(cx + cos(a) * RING_OUTER_RX * WARD_RADIUS_FACTOR,
			cy + sin(a) * RING_OUTER_RY * WARD_RADIUS_FACTOR)
		var tangent := Vector2(-RING_OUTER_RX * sin(a), RING_OUTER_RY * cos(a)).normalized()
		var normal := (pos - _ring_center).normalized()
		var poly := PackedVector2Array([
			pos + tangent * WARD_HW + normal * WARD_HH,
			pos - tangent * WARD_HW + normal * WARD_HH,
			pos - tangent * WARD_HW - normal * WARD_HH,
			pos + tangent * WARD_HW - normal * WARD_HH,
		])
		_ring_wards.append({"poly": poly, "pos": pos})

	_bore_fill = _ellipse_points(cx, cy, RING_INNER_RX * 0.94, RING_INNER_RY * 0.94, 40)
	_bore_ring_a = _ellipse_points(cx, cy, RING_INNER_RX * 0.80, RING_INNER_RY * 0.80, 72)
	_bore_ring_b = _ellipse_points(cx, cy, RING_INNER_RX * 0.52, RING_INNER_RY * 0.52, 56)


func _build_bindstone(rng: RandomNumberGenerator) -> void:
	var cap := pillar_cap(_bind_ground, BIND_H, BIND_SCALE)
	var faces := pillar_faces(cap, BIND_H)
	_bind_cap = cap
	_bind_left = faces[0]
	_bind_right = faces[1]
	# Carved sigils on the canted face: the same small hard-angled glyph as the ground
	# field, scaled up slightly and centred on the cap so it reads as deliberate carving
	# rather than ground clutter.
	var centre := (cap[0] + cap[2]) * 0.5
	_bind_sigil.clear()
	for stroke in _make_sigil(rng, 9.0):
		var a: Vector2 = centre + stroke[0]
		var b: Vector2 = centre + stroke[1]
		_bind_sigil.append(PackedVector2Array([a, b]))


func _build_ring_ground_ticks() -> void:
	## A counted ring of tick marks on the ground plane around the ring's footprint,
	## sampled in tile space and projected — same technique anchor_view uses for weapon
	## reach — so the ellipse it traces is the correct 2:1 ground squash rather than the
	## ring's own steeper standing squash.
	_ring_ground_ticks.clear()
	var origin := _cached_origin
	var tile: Vector2 = IsoScript.screen_to_tile(_ring_ground - origin)
	# TER-07: one added term — the ring's own footprint tile's height, applied uniformly to
	# the whole tick ring rather than sampled per point (the ring stands on one tile; it does
	# not need to follow terrain the way a reach ring crossing several tiles does).
	var hz := IsoScript.height_offset(float(view.call("height_at", int(round(tile.x)),
			int(round(tile.y)))))
	const N := 28
	const R0 := 2.05
	const R1 := 2.35
	for i in range(N):
		var a := TAU * float(i) / float(N)
		var dir := Vector2(cos(a), sin(a))
		var p0 := IsoScript.tile_to_screen(tile.x + dir.x * R0, tile.y + dir.y * R0) + origin + hz
		var p1 := IsoScript.tile_to_screen(tile.x + dir.x * R1, tile.y + dir.y * R1) + origin + hz
		_ring_ground_ticks.append(PackedVector2Array([p0, p1]))


func _make_sigil(rng: RandomNumberGenerator, step: float) -> Array:
	## An abstract Ordinal glyph: 2-4 straight strokes between points on a 3x3 grid.
	## Hard angles only (no curve primitive exists here to draw one by accident), and no
	## isolated dot ever stands alone the way an apostrophe would — every stroke has two
	## endpoints on the grid. Deterministic: the caller's rng is seeded from the anchor id.
	var pts: Array[Vector2] = []
	for gx in range(-1, 2):
		for gy in range(-1, 2):
			pts.append(Vector2(gx, gy) * step)
	var strokes: Array = []
	var k := rng.randi_range(2, 4)
	var guard := 0
	while strokes.size() < k and guard < 12:
		guard += 1
		var a: Vector2 = pts[rng.randi_range(0, pts.size() - 1)]
		var b: Vector2 = pts[rng.randi_range(0, pts.size() - 1)]
		if a.is_equal_approx(b):
			continue
		strokes.append(PackedVector2Array([a, b]))
	return strokes


func _build_ground_sigils(rng: RandomNumberGenerator, anchor: Dictionary, origin: Vector2,
		path: Array) -> void:
	## TER-07: `+ IsoScript.height_offset(...)` on `c` below is the one added term — a sigil
	## on a raised region now sits on its own tile's surface instead of at level 0 under it.
	_ground_sigils.clear()
	var grid: Dictionary = anchor.get("grid", {"w": 12, "h": 10})
	var w := int(grid["w"])
	var h := int(grid["h"])
	var occupied := _path_and_slot_tiles(anchor)
	var ring_tile := Vector2.ZERO
	if path.size() >= 2:
		var exit_pt: Array = path[path.size() - 1]
		ring_tile = Vector2(float(exit_pt[0]), float(exit_pt[1]))
	for x in range(w):
		for y in range(h):
			var cell := Vector2i(x, y)
			if occupied.has(cell):
				continue
			var d := Vector2(float(x), float(y)).distance_to(ring_tile)
			var p := clampf(0.60 - d * 0.045, 0.035, 0.60)
			if rng.randf() > p:
				continue
			var hgt := int(view.call("height_at", x, y))
			var c := IsoScript.tile_to_screen(float(x), float(y)) + origin \
					+ IsoScript.height_offset(float(hgt))
			_ground_sigils.append({"c": c, "strokes": _make_sigil(rng, 6.0)})


func _path_and_slot_tiles(anchor: Dictionary) -> Dictionary:
	var out := {}
	var pts: Array = anchor.get("path", [])
	for i in range(pts.size() - 1):
		var a := Vector2i(int(pts[i][0]), int(pts[i][1]))
		var b := Vector2i(int(pts[i + 1][0]), int(pts[i + 1][1]))
		var step := Vector2i(signi(b.x - a.x), signi(b.y - a.y))
		var cur := a
		out[cur] = true
		while cur != b:
			cur += step
			out[cur] = true
	for s in anchor.get("slots", []):
		out[Vector2i(int(s[0]), int(s[1]))] = true
	return out


# ────────────────────────────────────────────────────────────────── draw ──

func _draw_platform_edge() -> void:
	var right_col := Color(STONE_DARK.r * 1.3, STONE_DARK.g * 1.3, STONE_DARK.b * 1.3)
	var rim_col := Color(ALLOY_LIGHT, 0.30)
	for q in _edge_left:
		draw_colored_polygon(q, STONE_DARK)
	for q in _edge_right:
		draw_colored_polygon(q, right_col)
	for seg in _edge_rim:
		draw_line(seg[0], seg[1], rim_col, 2.0)


func _draw_ground_sigils() -> void:
	var col := Color(ALLOY_MID, 0.30)
	for s in _ground_sigils:
		var c: Vector2 = s["c"]
		for stroke in s["strokes"]:
			draw_line(c + stroke[0], c + stroke[1], col, 1.2)


func _draw_ring_ground_ticks() -> void:
	if _ring_ground_ticks.is_empty():
		return
	var col := Color(ALLOY_MID, 0.22)
	for t in _ring_ground_ticks:
		draw_line(t[0], t[1], col, 1.2)


func _draw_bindstone() -> void:
	if _bind_cap.is_empty():
		return
	draw_colored_polygon(_bind_left, ALLOY_DARK)
	draw_colored_polygon(_bind_right, Color(ALLOY_DARK.r * 1.25, ALLOY_DARK.g * 1.25,
		ALLOY_DARK.b * 1.25))
	draw_colored_polygon(_bind_cap, ALLOY_MID)
	draw_polyline(_bind_cap + PackedVector2Array([_bind_cap[0]]), Color(ALLOY_LIGHT, 0.55), 1.5)
	var sigil_col := Color(ALLOY_LIGHT, 0.6)
	for stroke in _bind_sigil:
		draw_line(stroke[0], stroke[1], sigil_col, 1.3)


func _lit_count() -> int:
	if view == null:
		return 0
	var sim = view.get("sim")
	if sim == null:
		return 0
	var waves: Array = sim.anchor.get("waves", [])
	var total := waves.size()
	if total <= 0:
		return 0
	var wn := int(view.call("wave_number"))
	if wn <= 0:
		return 0
	return clampi(int(ceil(6.0 * float(wn) / float(total))), 0, WARD_COUNT)


func _draw_ring() -> void:
	if _ring_base_cap.is_empty():
		return
	var glow_ceiling: float = clampf(Display.glow, 0.0, 1.0)
	var lit: int = _lit_count()
	var frac := float(lit) / float(WARD_COUNT)

	draw_colored_polygon(_ring_base_left, STONE_DARK)
	draw_colored_polygon(_ring_base_right, Color(STONE_DARK.r * 1.3, STONE_DARK.g * 1.3,
		STONE_DARK.b * 1.3))
	draw_colored_polygon(_ring_base_cap, ALLOY_DARK)

	for q in _ring_band_quads:
		draw_colored_polygon(q, ALLOY_MID)
	for t in _ring_ticks:
		draw_line(t[0], t[1], Color(ALLOY_LIGHT, 0.35), 1.0)
	# rim highlight: the upper third of the band reads brighter, as if lit from above —
	# cheap to fake with a translucent arc rather than real lighting. Ellipse points start
	# at angle 0 (screen east) and sweep clockwise, so the top of the ring (screen north,
	# minimum y) sits at index 18 of 24; the slice spans either side of it.
	draw_polyline(_ellipse_points(_ring_center.x, _ring_center.y, RING_OUTER_RX * 1.005,
		RING_OUTER_RY * 1.005, 24).slice(14, 23), Color(ALLOY_LIGHT, 0.45), 2.5)

	_draw_threshold(frac, glow_ceiling)

	var hot: Color = WARD_LIT.get(_act, WARD_LIT[1])
	for i in range(_ring_wards.size()):
		var wdict: Dictionary = _ring_wards[i]
		var is_lit: bool = i < lit
		var col := WARD_DARK
		if is_lit:
			var pulse := 1.0
			if i == lit - 1:
				pulse = 0.85 + 0.15 * sin(_time * 2.2)
			# A cheap halo, same trick as the backdrop's horizon glow: a soft low-alpha
			# disc behind the block, since this layer has no additive material of its
			# own to bloom it the way GlowLayer bloomed an emplacement's glow pass.
			draw_circle(wdict["pos"], WARD_HW * 1.9, Color(hot.r, hot.g, hot.b,
				0.30 * pulse * glow_ceiling))
			col = WARD_DARK.lerp(Color(hot.r * pulse, hot.g * pulse, hot.b * pulse, 1.0),
				glow_ceiling)   # a ward's own light bows to the same ceiling
		draw_colored_polygon(wdict["poly"], col)
		# An unlit ward is nearly the same value as the bore behind it, so the socket
		# reads by its outline alone; a lit one gets a near-white edge on top of the
		# fill so the block itself looks like it is throwing light, not just tinted.
		var outline := Color(ALLOY_LIGHT, 0.6) if not is_lit else \
			Color(1.0, 1.0, 1.0, 0.7 * glow_ceiling)
		draw_polyline(wdict["poly"] + PackedVector2Array([wdict["poly"][0]]), outline, 1.4)


func _draw_threshold(frac: float, glow_ceiling: float) -> void:
	## The bore is what the whole game is about, so it is deliberately the one place on
	## the ring allowed to be bright: cold glass with restless light in it, brightening
	## as wards engage. A first pass sat at ~0.13 base and ~0.35 peak alpha over a base
	## that was already flat, which read as "a dark oval" rather than as the unsettling
	## focal point the brief asks for — this version is louder at every step: a lighter
	## resting glass tone, a soft halo bleeding onto the band around it, and brighter,
	## thicker moving arcs.
	var base: Color = THRESHOLD_TINT.get(_act, THRESHOLD_TINT[1])
	if _act == 3:
		var w := 0.5 + 0.5 * sin(_time * 0.11)
		base = base.lerp(THRESHOLD_TINT_ALT, w * 0.5)
	draw_colored_polygon(_bore_fill, Color(base.r * 0.30, base.g * 0.34, base.b * 0.38, 1.0))
	if glow_ceiling <= 0.0:
		return
	# Never fully inert even with no ward engaged — it is meant to be watched even
	# before the first wave, not to switch on — and brighter at every step of the climb
	# to full so the ring visibly carries the same progress read the wards do.
	var energy: float = (0.16 + 0.84 * frac) * glow_ceiling
	# A soft halo just outside the bore, bleeding a little of the threshold's own light
	# onto the inner edge of the band — the same cheap-bloom trick as the ward halos.
	draw_circle(_ring_center, RING_INNER_RX * 1.35, Color(base.r, base.g, base.b, 0.10 * energy))
	draw_colored_polygon(_bore_fill, Color(base.r, base.g, base.b, 0.55 * energy))
	# Restless internal movement, faked without any per-frame trig: two dense point
	# rings are precomputed once per anchor, and each frame just slices a rolling window
	# of them — array indexing, not geometry regeneration.
	var na := _bore_ring_a.size()
	var nb := _bore_ring_b.size()
	if na > 0:
		var off_a := int(_time * 9.0) % na
		var span_a := int(na * 0.4)
		var arc_a := _slice_wrap(_bore_ring_a, off_a, span_a)
		draw_polyline(arc_a, Color(base.r, base.g, base.b, 0.75 * energy + 0.15), 2.5)
	if nb > 0:
		var off_b := int(_time * -13.0) % nb
		var span_b := int(nb * 0.5)
		var arc_b := _slice_wrap(_bore_ring_b, off_b, span_b)
		draw_polyline(arc_b, Color(1.0, 1.0, 1.0, 0.45 * energy + 0.08), 1.8)


func _slice_wrap(pts: PackedVector2Array, start: int, count: int) -> PackedVector2Array:
	var n := pts.size()
	var out := PackedVector2Array()
	var s := ((start % n) + n) % n
	for i in range(count):
		out.append(pts[(s + i) % n])
	return out
