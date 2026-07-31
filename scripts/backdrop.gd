extends Node2D
## Everything behind the board: the void the anchor platform hangs in.
##
## CAM-02: a sibling of AnchorView under Main, at z −30, not a child. It used to be a child
## of AnchorView and inherit the board transform outright — which meant scaling AnchorView
## for a camera zoom tore the sky (it sizes itself to the raw viewport, independently of the
## board) and screen shake, which is AnchorView's own `position`, shook the sky along with
## it. Neither is wanted: {{CAM-01}}'s zoom needs the sky full-bleed regardless of board
## scale, and a static sky under a shaking board reads as a camera move, not a wobble. So
## this no longer reads its subject from `get_parent()` the way GlowLayer does (see that
## file's own docstring for the idiom this deliberately departs from) — `get_parent()` is
## Main now, not the board — and is instead handed the board explicitly via `view_path`,
## resolved once in `_ready()`.
##
## `PAD` (below) stays even though nothing shakes this layer any more: {{CAM-01}}'s parallax
## will slide the sky by a fraction of the camera's pan, and the overhang is what keeps that
## slide from ever baring an edge.
##
## Three layers, back to front: a vertical sky gradient with a horizon glow, a handful of
## near-invisible distant shapes implying the anchor is one node of something larger, and
## sparse drifting motes for atmosphere. Everything static (gradient bands, glow blob,
## structure silhouettes) is built once per anchor into cached arrays and only redrawn;
## only mote positions and the act-III horizon "breathing" change per frame.

## The board this backdrop is drawn for. `../AnchorView` matches the sibling layout
## scenes/main.tscn authors today; a level that reparents the board only needs to change
## this one export, not this script.
@export var view_path: NodePath = ^"../AnchorView"
var view: Node2D

# ─────────────────────────────────────────────────────────────── palette ──
#
# One mood per act, per docs/NOMENCLATURE.md: Act I is the Ordinal wardens, cold and
# clean and indifferent; Act II is Sable Reach, industrial and dirtier with warm work
# light; Act III is the Hollow, colour drained, with the horizon doing something a sky
# should not. Values are ordinary sRGB draw-call colours — this is in-engine immediate
# geometry, not the linear-then-encoded Blender sprite pipeline, so none of that
# machinery applies here.
const PALETTES := {
	1: {
		"sky_top": Color(0.020, 0.026, 0.046),
		"sky_horizon": Color(0.055, 0.086, 0.112),
		"sky_deep": Color(0.012, 0.015, 0.026),
		"glow": Color(0.42, 0.78, 0.88),
		"mote": Color(0.62, 0.82, 0.94),
		"structure": Color(0.14, 0.19, 0.24),
		"wrongness": false,
	},
	2: {
		"sky_top": Color(0.034, 0.026, 0.020),
		"sky_horizon": Color(0.132, 0.084, 0.040),
		"sky_deep": Color(0.020, 0.015, 0.010),
		"glow": Color(0.90, 0.58, 0.24),
		"mote": Color(0.78, 0.60, 0.38),
		"structure": Color(0.18, 0.13, 0.08),
		"wrongness": false,
	},
	3: {
		"sky_top": Color(0.016, 0.017, 0.020),
		"sky_horizon": Color(0.075, 0.062, 0.078),
		"sky_deep": Color(0.010, 0.010, 0.012),
		"glow": Color(0.58, 0.34, 0.52),
		"glow_alt": Color(0.32, 0.52, 0.42),   # the hue the horizon should not drift toward
		"mote": Color(0.48, 0.52, 0.50),
		"structure": Color(0.10, 0.095, 0.11),
		"wrongness": true,
	},
}

const SKY_BANDS := 20
const GLOW_RINGS := 14
const MOTE_COUNT := 48
const STRUCT_MIN := 3
const STRUCT_MAX := 5
const PAD := 60.0                # backdrop overhang past the viewport — kept for CAM-01's parallax slide

## CAM-01's hook: a pan/zoom offset from the board camera, applied to the sky as a fraction
## of the pan and *never* as a scale — the whole point of splitting this off AnchorView is
## that the board scales and the sky does not. PARALLAX is 0.0 in this issue on purpose, so
## `offset * PARALLAX` is always the zero vector and the frame is provably unchanged; CAM-01
## raises it and re-shoots. `zoom` is accepted and stored for CAM-01 to use (e.g. easing the
## parallax speed with it) but is never applied to this node's own scale.
const PARALLAX := 0.0
var _cam_offset: Vector2 = Vector2.ZERO
var _cam_zoom: float = 1.0

var _cached_id: String = ""
var _cached_vp: Vector2 = Vector2.ZERO
var _act: int = 1
var _pal: Dictionary = {}

var _sky_bands: Array = []       # {rect: Rect2, col: Color}
var _glow_polys: Array = []      # {poly: PackedVector2Array, alpha: float}
var _structures: Array = []      # {poly: PackedVector2Array, line: bool, alpha: float}
var _horizon_y: float = 0.0

var _mote_pos: PackedVector2Array = PackedVector2Array()
var _mote_vel: PackedVector2Array = PackedVector2Array()
var _mote_r: PackedFloat32Array = PackedFloat32Array()
var _mote_a: PackedFloat32Array = PackedFloat32Array()
var _mote_ph: PackedFloat32Array = PackedFloat32Array()

var _time: float = 0.0


func _ready() -> void:
	view = get_node_or_null(view_path) as Node2D
	set_process(not Engine.is_editor_hint())
	if Engine.is_editor_hint():
		queue_redraw()


func set_camera(offset: Vector2, zoom: float) -> void:
	## CAM-01's only way to move this layer. Never scales — see PARALLAX's own doc.
	_cam_offset = offset
	_cam_zoom = zoom
	queue_redraw()


func _process(delta: float) -> void:
	_time += delta
	var vp := get_viewport_rect().size
	if view == null or String(view.get("anchor_id")) != _cached_id or vp != _cached_vp:
		_rebuild(vp)
	_advance_motes(delta)
	queue_redraw()


func _rebuild(vp: Vector2) -> void:
	_cached_vp = vp
	var aid := String(view.get("anchor_id")) if view != null else ""
	_cached_id = aid
	var anchor: Dictionary = Content.anchor(aid) if aid != "" else {}
	_act = int(anchor.get("act", 1))
	_pal = PALETTES.get(_act, PALETTES[1])

	var rng := RandomNumberGenerator.new()
	rng.seed = hash(aid)

	_horizon_y = vp.y * 0.70
	_build_sky(vp)
	_build_glow(vp)
	_build_structures(rng, vp)
	_build_motes(rng, vp)


func _build_sky(vp: Vector2) -> void:
	_sky_bands.clear()
	var top: Color = _pal["sky_top"]
	var mid: Color = _pal["sky_horizon"]
	var deep: Color = _pal["sky_deep"]
	var upper_h := _horizon_y + 24.0
	var band_h := upper_h / float(SKY_BANDS)
	for i in range(SKY_BANDS):
		var t := float(i) / float(SKY_BANDS - 1)
		# ease toward the horizon rather than a linear ramp, so the glow band reads as a
		# gathering of light rather than a ruler-straight gradient.
		var e := t * t
		var col: Color = top.lerp(mid, e)
		_sky_bands.append({"rect": Rect2(-PAD, i * band_h, vp.x + PAD * 2.0, band_h + 1.0),
			"col": col})
	var lower_h := (vp.y - upper_h) + PAD
	var lower_bands := int(SKY_BANDS * 0.6)
	var lband_h := lower_h / float(lower_bands)
	for i in range(lower_bands):
		var t := float(i) / float(lower_bands - 1)
		var col: Color = mid.lerp(deep, t)
		_sky_bands.append({"rect": Rect2(-PAD, upper_h + i * lband_h, vp.x + PAD * 2.0,
			lband_h + 1.0), "col": col})


func _build_glow(vp: Vector2) -> void:
	## A soft horizon blob, faked the way immediate-mode 2D fakes bloom: several
	## concentric ellipses of falling alpha, cached as point sets and just translated at
	## draw time. Small, flat and dim on purpose: this reads as light gathering low on
	## the horizon, not as an object floating in the frame — the brief is explicit that
	## everything here sits under the fight in value and saturation, and an early pass
	## (rx to 0.62 of the viewport, peak alpha 0.10, only 6 steps) drew a nearly-solid
	## pale disc that was the single brightest, most legible shape on screen. This is a
	## fifth the width, roughly an eighth the peak alpha, flatter (a band, not a lens),
	## and enough rings that the falloff has no visible banding. Widened and raised
	## slightly from the very first correction, which overshot from "dominates the
	## frame" to nearly invisible against the platform — this is still a fraction of
	## the original, just enough to read as a gathering of light rather than nothing.
	_glow_polys.clear()
	var cx := vp.x * 0.5
	for i in range(GLOW_RINGS):
		var t := float(i) / float(GLOW_RINGS - 1)
		var rx := lerpf(vp.x * 0.045, vp.x * 0.17, t)
		var ry := rx * 0.18
		var pts := PackedVector2Array()
		const N := 28
		for j in range(N):
			var a := TAU * float(j) / float(N)
			pts.append(Vector2(cx + cos(a) * rx, _horizon_y + sin(a) * ry))
		var alpha := 0.024 * (1.0 - t) * (1.0 - t) * (1.0 - t)
		_glow_polys.append({"poly": pts, "alpha": alpha})


func _build_structures(rng: RandomNumberGenerator, vp: Vector2) -> void:
	_structures.clear()
	var count := rng.randi_range(STRUCT_MIN, STRUCT_MAX)
	for i in range(count):
		var x := rng.randf_range(vp.x * 0.03, vp.x * 0.97)
		# keep the middle third clearer — that is where the ring will dominate the frame,
		# and a shape competing with it there reads as clutter rather than distance.
		if x > vp.x * 0.36 and x < vp.x * 0.64:
			x += vp.x * 0.30 * (1.0 if rng.randf() < 0.5 else -1.0)
		var base_y := _horizon_y + rng.randf_range(-10.0, 14.0)
		if rng.randi_range(0, 1) == 0:
			var h := rng.randf_range(50.0, 150.0)
			var w0 := rng.randf_range(9.0, 20.0)
			var w1 := w0 * rng.randf_range(0.3, 0.55)
			var lean := rng.randf_range(-8.0, 8.0)
			var poly := PackedVector2Array([
				Vector2(x - w0 * 0.5, base_y), Vector2(x + w0 * 0.5, base_y),
				Vector2(x + w1 * 0.5 + lean, base_y - h),
				Vector2(x - w1 * 0.5 + lean, base_y - h),
			])
			_structures.append({"poly": poly, "line": false,
				"alpha": rng.randf_range(0.05, 0.11)})
		else:
			var rx := rng.randf_range(36.0, 84.0)
			var ry := rx * 0.40
			var a0 := rng.randf_range(0.0, TAU)
			var span := rng.randf_range(1.0, 2.3)
			var n := 16
			var pts := PackedVector2Array()
			for j in range(n + 1):
				var a := a0 + span * float(j) / float(n)
				pts.append(Vector2(x + cos(a) * rx, base_y - ry * 0.6 + sin(a) * ry))
			_structures.append({"poly": pts, "line": true,
				"alpha": rng.randf_range(0.06, 0.13)})


func _build_motes(rng: RandomNumberGenerator, vp: Vector2) -> void:
	_mote_pos.resize(MOTE_COUNT)
	_mote_vel.resize(MOTE_COUNT)
	_mote_r.resize(MOTE_COUNT)
	_mote_a.resize(MOTE_COUNT)
	_mote_ph.resize(MOTE_COUNT)
	var region := Rect2(-PAD, -PAD, vp.x + PAD * 2.0, vp.y + PAD * 2.0)
	for i in range(MOTE_COUNT):
		_mote_pos[i] = Vector2(rng.randf_range(region.position.x, region.end.x),
			rng.randf_range(region.position.y, region.end.y))
		# a slow common drift plus per-mote variance reads as one body of dust moving
		# through still air, rather than each speck doing its own thing.
		var ang := deg_to_rad(200.0 + rng.randf_range(-20.0, 20.0))
		var spd := rng.randf_range(2.5, 8.0)
		_mote_vel[i] = Vector2(cos(ang), sin(ang)) * spd
		_mote_r[i] = rng.randf_range(0.6, 2.1)
		_mote_a[i] = rng.randf_range(0.12, 0.42)
		_mote_ph[i] = rng.randf_range(0.0, TAU)


func _advance_motes(delta: float) -> void:
	var region := Rect2(-PAD, -PAD, _cached_vp.x + PAD * 2.0, _cached_vp.y + PAD * 2.0)
	if region.size.x <= 0.0:
		return
	for i in range(_mote_pos.size()):
		var p: Vector2 = _mote_pos[i] + _mote_vel[i] * delta
		if p.x < region.position.x:
			p.x += region.size.x
		elif p.x > region.end.x:
			p.x -= region.size.x
		if p.y < region.position.y:
			p.y += region.size.y
		elif p.y > region.end.y:
			p.y -= region.size.y
		_mote_pos[i] = p


func _draw() -> void:
	if view == null:
		return
	if _cached_id == "" or _cached_vp != get_viewport_rect().size:
		_rebuild(get_viewport_rect().size)
	# Translation only — see PARALLAX's own doc for why this never scales. At the default
	# PARALLAX = 0.0 this is always the identity transform, which is what keeps this issue's
	# frame pixel-identical to the pre-split one.
	draw_set_transform(_cam_offset * PARALLAX, 0.0, Vector2.ONE)
	_draw_sky()
	_draw_glow()
	_draw_structures()
	_draw_motes()


func _draw_sky() -> void:
	for b in _sky_bands:
		draw_rect(b["rect"], b["col"], true)


func _draw_glow() -> void:
	# It is the brightest thing in the backdrop, so the light-sensitivity ceiling applies
	# to it same as GlowLayer's additive pass — never brighter than the player allowed.
	var ceiling: float = clampf(Display.glow, 0.0, 1.0)
	if ceiling <= 0.0:
		return
	var base: Color = _pal["glow"]
	if bool(_pal.get("wrongness", false)):
		# The Hollow's horizon breathes toward a hue nothing natural drifts toward — slow
		# and low-amplitude, so it reads as wrong rather than as a special effect.
		var alt: Color = _pal["glow_alt"]
		var w := 0.5 + 0.5 * sin(_time * 0.11)
		base = base.lerp(alt, w)
	for g in _glow_polys:
		draw_colored_polygon(g["poly"], Color(base.r, base.g, base.b, g["alpha"] * ceiling))


func _draw_structures() -> void:
	var col: Color = _pal["structure"]
	for s in _structures:
		if s["line"]:
			draw_polyline(s["poly"], Color(col.r, col.g, col.b, s["alpha"]), 1.4)
		else:
			draw_colored_polygon(s["poly"], Color(col.r, col.g, col.b, s["alpha"]))


func _draw_motes() -> void:
	var col: Color = _pal["mote"]
	for i in range(_mote_pos.size()):
		var bob := sin(_time * 0.6 + _mote_ph[i]) * 2.0
		var p: Vector2 = _mote_pos[i] + Vector2(0, bob)
		draw_circle(p, _mote_r[i], Color(col.r, col.g, col.b, _mote_a[i]))
