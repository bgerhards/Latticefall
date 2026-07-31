extends Node2D
## Additive emissive layer, drawn on top of the board and modulated by reactor load.
##
## This is the payoff for keeping glow out of the sprite (decision 007): a brownout
## dims every emissive element in the game by changing one number, and the albedo
## underneath is untouched.

## Authored as a child of AnchorView in scenes/main.tscn, so it takes its subject from
## its parent rather than being handed one — there is no arrangement in which this layer
## draws for a node other than the one it hangs under.
var view: Node2D

## The brownout dim, shared with fx_additive.gd's pooled combat FX (bolt/arc/flak/mortar/
## field) and its sustained-beam pass (LF-117) — both are additive layers subject to the
## same decision-007 rule, so this is the one place the number lives rather than two
## literals that could silently drift apart.
const BROWNOUT_FACTOR := 0.35


func _ready() -> void:
	view = get_parent() as Node2D
	var m := CanvasItemMaterial.new()
	m.blend_mode = CanvasItemMaterial.BLEND_MODE_ADD
	material = m
	z_index = 10
	# Changing the glow setting has to reach the canvas: this layer only redraws when asked,
	# so without this the option appears to do nothing until the next thing moves.
	Display.changed.connect(queue_redraw)


## CAM-06/CAM-07 verification: `-- --profile <frames>` (main.gd) times this layer's own
## `_draw()` calls in milliseconds. Off by default; see anchor_view.gd's `start_profiling()`
## for the fuller doc this mirrors — duplicated per layer rather than shared through a base
## class, since each of the four profiled scripts (AnchorView, GlowLayer, FxAdditive,
## CombatFx) has a different parent type already.
var _profile_ticks: PackedFloat64Array = PackedFloat64Array()
var _profiling: bool = false


func start_profiling() -> void:
	_profiling = true
	_profile_ticks.clear()


func profile_stats() -> Dictionary:
	if _profile_ticks.is_empty():
		return {"mean": 0.0, "p95": 0.0, "n": 0}
	var sorted := _profile_ticks.duplicate()
	sorted.sort()
	var n := sorted.size()
	var total := 0.0
	for v in sorted:
		total += v
	var idx := clampi(int(ceil(0.95 * float(n))) - 1, 0, n - 1)
	return {"mean": total / float(n), "p95": sorted[idx], "n": n}


func _draw() -> void:
	if not _profiling:
		_draw_impl()
		return
	var t0 := Time.get_ticks_usec()
	_draw_impl()
	_profile_ticks.append(float(Time.get_ticks_usec() - t0) / 1000.0)


func _draw_impl() -> void:
	if view == null or view.sim == null or not Sprites.ok:
		return
	# Brownout dimming is the mechanic (decision 007); the Display factor is the player's
	# own ceiling on top of it. The additive layer is the brightest thing on screen, so
	# turning it down is the accommodation for light sensitivity — and at 0 the layer costs
	# nothing to draw rather than drawing black.
	if Display.glow <= 0.0:
		return
	var energy: float = (BROWNOUT_FACTOR if view.sim.brownout else 1.0) * Display.glow
	var tint := Color(1, 1, 1, energy)
	for d in view.drawables():
		# ART-01/LF-157: a drawable carrying "bucket" is a split base/head part and is
		# fetched by bucket index; everything else (every unit today) still carries a
		# degree "yaw" and goes through the untouched get_tex() path. Both parts of a
		# placed tower appear in this same list, so both get their glow drawn here — the
		# additive layer must not disagree with the albedo pass in anchor_view.gd about
		# what a placed tower looks like.
		var tex: Texture2D
		if d.has("bucket"):
			tex = Sprites.get_bucket_tex(d["sprite"], int(d["bucket"]), "glow")
		else:
			tex = Sprites.get_tex(d["sprite"], d["yaw"], "glow")
		if tex == null:
			continue
		if d["kind"] == "tower" and not d["online"]:
			continue
		draw_texture(tex, d["at"] - Sprites.pivot, tint)
		# ART-06: a head mid-traverse cross-fades in the albedo pass (anchor_view.gd's
		# `_draw_entities()`) — the glow layer must not disagree about what a placed tower
		# looks like this frame, same rule this file's own comment above already states
		# for the plain bucket/yaw split. Only ever set on a head entry.
		if d.has("trans_from_bucket"):
			var from_tex := Sprites.get_bucket_tex(d["sprite"], int(d["trans_from_bucket"]), "glow")
			if from_tex != null:
				var fade := Color(tint.r, tint.g, tint.b, tint.a * float(d["trans_from_alpha"]))
				draw_texture(from_tex, d["at"] - Sprites.pivot, fade)
