extends Node2D
## The emissive half of the combat FX pool: tracers, beams, muzzle flash, impact bloom.
##
## Additive, at z 14 — above GlowLayer, because a shot has to read over the emplacement
## that fired it. Draws from CombatFx's pool rather than owning one, so a projectile
## cannot exist in one pass and not the other. Also draws two things that are never in the
## pool because they need no lifetime bookkeeping — a beam tower's charge-up glow and a
## field tower's pulse ring — both computed live off AnchorSim.placed every frame.

const IsoScript := preload("res://scripts/iso.gd")

const BEAM_CHARGE_TIME := 0.25
## Field towers pulse briefly, then go dark for the rest of the period — a permanent
## full-radius ring read as a UI range indicator left on by accident (a board with several
## support emplacements was a spiderweb of overlapping ellipses fighting the fight for
## attention), which is the opposite of "quiet ambience" the brief asked for. Slower cycle,
## short visible window, low peak alpha.
const FIELD_PULSE_PERIOD := 4.0
const FIELD_PULSE_DURATION := 0.85     # ring is only ever drawn inside this window
const FIELD_PULSE_PEAK_ALPHA := 0.14

const C_HIT_FLASH := Color(1, 1, 1)             # a hit reads white regardless of weapon colour
const C_RICOCHET := Color(0.80, 0.90, 1.0)      # matches combat_fx's C_RICOCHET — same event,
                                                 # same colour, different draw pass

var fx: Node2D


func _ready() -> void:
	var m := CanvasItemMaterial.new()
	m.blend_mode = CanvasItemMaterial.BLEND_MODE_ADD
	material = m
	fx = get_parent().get_node_or_null(^"CombatFx") as Node2D


func _draw() -> void:
	if fx == null or fx.view == null or fx.view.sim == null or not Sprites.ok:
		return
	# Respect Display.glow as a ceiling on additive brightness (the light-sensitivity
	# accommodation): this layer is now the brightest thing on screen, glow_layer.gd's own
	# argument for the same check.
	var glow: float = Display.glow
	if glow <= 0.0:
		return
	for e in fx.pool():
		_draw_entry(e, glow)
	_draw_hit_flashes(glow)
	_draw_beam_charges(glow)
	_draw_field_pulses(glow)
	_draw_sustained_beams(glow)


func _draw_entry(e: Dictionary, glow: float) -> void:
	match String(e["kind"]):
		"muzzle":
			_draw_muzzle(e, glow)
		"bolt":
			_draw_bolt(e, glow)
		"arc":
			_draw_arc(e, glow)
		"beam":
			_draw_beam(e, glow)
		"flak_shell":
			_draw_travel_dot(e, glow, false)
		"mortar_shell":
			_draw_travel_dot(e, glow, true)
		"ring":
			_draw_ring(e, glow)
		"spark":
			_draw_spark(e, glow)
		"flare":
			_draw_flare(e, glow)
		_:
			pass


func _draw_muzzle(e: Dictionary, glow: float) -> void:
	var frac: float = 1.0 - clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var col: Color = e["colour"]
	col.a = frac * glow
	draw_circle(e["pos"], float(e["r"]) * (0.4 + frac * 0.6), col)


func _draw_bolt(e: Dictionary, glow: float) -> void:
	## A fast slug with a bright core and a short tapering tail. `life` is real travel time
	## (distance/speed, tiles/sec) so the shot is visibly in flight and leads its target
	## rather than snapping there.
	var life: float = maxf(0.001, float(e["life"]))
	var t: float = clampf(float(e["age"]) / life, 0.0, 1.0)
	var from: Vector2 = e["from"]
	var to: Vector2 = e["to"]
	var pos: Vector2 = from.lerp(to, t)
	var trail_t: float = clampf(t - float(e["trail"]) / life, 0.0, t)
	var tail: Vector2 = from.lerp(to, trail_t)
	var colour: Color = e["colour"]
	colour.a = glow
	draw_line(tail, pos, colour, float(e["width"]))
	var core: Color = e["core"]
	core.a = glow
	draw_circle(pos, float(e["width"]) * 0.9, core)


func _draw_arc(e: Dictionary, glow: float) -> void:
	## Instant, no travel time: a jagged multi-segment polyline, re-seeded per shot, with a
	## bright flash at both ends and a fainter secondary arc to the nearest other unit in
	## range — the tower calls itself a chain in its own note, so this sells that even though
	## only the primary target actually takes damage.
	var frac: float = 1.0 - clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var colour: Color = e["colour"]
	colour.a = frac * glow
	var core: Color = e["core"]
	core.a = frac * glow
	var pts: PackedVector2Array = e["points"]
	draw_polyline(pts, colour, float(e["width"]) * 1.6)
	draw_polyline(pts, core, float(e["width"]) * 0.6)
	var chain: PackedVector2Array = e.get("chain", PackedVector2Array())
	if chain.size() > 1:
		var faded := colour
		faded.a *= 0.55
		draw_polyline(chain, faded, float(e["width"]))
	draw_circle(pts[0], float(e["width"]) * 2.2, core)
	draw_circle(pts[pts.size() - 1], float(e["width"]) * 2.2, core)


func _draw_beam(e: Dictionary, glow: float) -> void:
	## The big gun: a thick blue-white lance that snaps out full-length, a hot core, a wide
	## soft outer glow, and a heavy bloom where it lands.
	var frac: float = 1.0 - clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var from: Vector2 = e["from"]
	var to: Vector2 = e["to"]
	var width: float = float(e["width"])
	var outer: Color = e["colour"]
	outer.a = frac * 0.5 * glow
	draw_line(from, to, outer, width * 2.6)
	var main: Color = e["colour"]
	main.a = frac * glow
	draw_line(from, to, main, width)
	var core: Color = e["core"]
	core.a = frac * glow
	draw_line(from, to, core, width * 0.4)
	draw_circle(to, width * 1.8 * (0.5 + frac * 0.5), Color(core.r, core.g, core.b, frac * glow))


func _draw_travel_dot(e: Dictionary, glow: float, arced: bool) -> void:
	var life: float = maxf(0.001, float(e["life"]))
	var t: float = clampf(float(e["age"]) / life, 0.0, 1.0)
	var pos: Vector2 = Vector2(e["from"]).lerp(e["to"], t)
	if arced:
		# Mortar: lobbed on a parabola, so the shell reads above the board — the falling
		# contact shadow that keeps the arc legible in isometric is combat_fx's non-emissive
		# half, drawn from this same pool entry.
		var apex: float = float(e.get("apex", 40.0))
		pos.y -= sin(PI * t) * apex
	var colour: Color = e["colour"]
	colour.a = glow
	draw_circle(pos, float(e["width"]), colour)
	var core: Color = e["core"]
	core.a = glow
	draw_circle(pos, float(e["width"]) * 0.5, core)


func _draw_ring(e: Dictionary, glow: float) -> void:
	var t: float = clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var col: Color = e["colour"]
	col.a = (1.0 - t) * glow
	var r0: float = float(e["r0"])
	var r1: float = float(e["r1"])
	var r: float = lerpf(r0, r1, t)
	var ry: float = (r * 0.5) if bool(e.get("flat", false)) else r
	if e.has("r1y"):
		ry = lerpf(r0 * 0.5, float(e["r1y"]), t)
	var pos: Vector2 = e["pos"]
	var pts := PackedVector2Array()
	for i in range(24):
		var a := TAU * float(i) / 24.0
		pts.append(pos + Vector2(cos(a) * r, sin(a) * ry))
	pts.append(pts[0])
	draw_polyline(pts, col, float(e["width"]))


func _draw_spark(e: Dictionary, glow: float) -> void:
	var frac: float = 1.0 - clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var col: Color = e["colour"]
	col.a = frac * glow
	var pos: Vector2 = e["pos"]
	var vel: Vector2 = e["vel"]
	var tail := pos - vel * 0.03
	draw_line(tail, pos, col, 2.0)


func _draw_flare(e: Dictionary, glow: float) -> void:
	## A hard, flat ricochet read — fixed-length dashes, not moving sparks — so a shielded
	## hit visibly does not behave like a normal one.
	var frac: float = 1.0 - clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var col: Color = e["colour"]
	col.a = frac * glow
	var pos: Vector2 = e["pos"]
	var ang: float = float(e["ang"])
	var length: float = float(e["len"])
	var dir := Vector2(cos(ang), sin(ang) * 0.6)
	draw_line(pos, pos + dir * length, col, 2.5)


func _draw_hit_flashes(glow: float) -> void:
	## The brief bright modulate a hit unit gets: an additive copy of its own albedo, tinted
	## white (or ricochet blue) and faded by the flash's remaining life. Modulating the
	## albedo draw itself would clamp at 1.0 in GL Compatibility's non-HDR pipeline and never
	## actually brighten — an additive overlay on top genuinely does.
	##
	## This loop itself is still O(units) — every drawable has to be checked for its own
	## flash — but `fx.hit_flash_at()` used to scan the *entire* live hit list per call,
	## making the whole pass quadratic in unit count (CAM-08 / LF-100). combat_fx.gd now
	## buckets hits by integer tile, so the per-unit lookup below only walks a local 3x3
	## neighbourhood; nothing here changed to make that true.
	var view: Node2D = fx.view
	for d in view.drawables():
		if d["kind"] != "unit":
			continue
		var tile: Vector2 = view.sim.point_at(float(d["ref"]["dist"]))
		var hit: Dictionary = fx.hit_flash_at(tile)
		if hit.is_empty():
			continue
		var tex: Texture2D = view.drawable_texture(d["sprite"], d["yaw"], "albedo")
		if tex == null:
			continue
		var base_col: Color = C_RICOCHET if bool(hit["shielded_resist"]) else C_HIT_FLASH
		var a: float = float(hit["strength"]) * glow
		draw_texture(tex, d["at"] - Sprites.pivot, Color(base_col.r, base_col.g, base_col.b, a))


func _draw_beam_charges(glow: float) -> void:
	## The ion lance's ~0.25s charge-up glow, driven directly off cooldown vs fire_interval
	## rather than a pooled particle — it needs no lifetime of its own, only the tower's.
	var view: Node2D = fx.view
	for p in view.sim.placed:
		var tw: Dictionary = p["tower"]
		var fxd: Dictionary = tw.get("fx", {})
		if String(fxd.get("class", "")) != "beam" or not p["online"]:
			continue
		var cd: float = float(p.get("cooldown", 0.0))
		if cd <= 0.0 or cd > BEAM_CHARGE_TIME:
			continue
		var frac: float = 1.0 - cd / BEAM_CHARGE_TIME
		var pos: Vector2 = view.to_screen(Vector2(float(p["slot"].x), float(p["slot"].y))) + Vector2(0, -34)
		var col := Color.html(String(fxd.get("core", fxd.get("colour", "#ffffff"))))
		col.a = frac * glow
		draw_circle(pos, 4.0 + frac * 10.0, col)


func _draw_field_pulses(glow: float) -> void:
	## Field emplacements (shield wall, scan relay, anchor damper, restorer) shoot nothing —
	## a brief, faint pulse ring at the tower's own range, plus a quiet idle shimmer, so the
	## player can see what they cover without selecting them. Ambience, not combat: no pooled
	## particle, no lifetime, just a phase derived from sim time and the slot so towers of the
	## same kind do not all pulse in lockstep. The ring is only drawn for the first
	## FIELD_PULSE_DURATION seconds of each FIELD_PULSE_PERIOD-second cycle and is dark the
	## rest of the time — held at full radius continuously it reads as a UI range indicator
	## left on by mistake, not as something the player absorbs peripherally.
	var view: Node2D = fx.view
	var t: float = view.sim_time()
	for p in view.sim.placed:
		var tw: Dictionary = p["tower"]
		var fxd: Dictionary = tw.get("fx", {})
		if String(fxd.get("class", "")) != "field" or not p["online"]:
			continue
		var col := Color.html(String(fxd.get("colour", "#ffffff")))
		var offset: float = float(absi(hash(p["slot"])) % 1000) / 1000.0 * FIELD_PULSE_PERIOD
		var phase: float = fmod(t + offset, FIELD_PULSE_PERIOD)
		var pos: Vector2 = view.to_screen(Vector2(float(p["slot"].x), float(p["slot"].y)))
		if phase < FIELD_PULSE_DURATION:
			var f: float = phase / FIELD_PULSE_DURATION
			var rng: float = float(tw["range"])
			# Annotated, not inferred: `fx` is held as a bare Node2D, so the compiler cannot
			# see tile_radius_screen()'s return type and `:=` fails to infer at PARSE time.
			# That is not a warning — the script fails to load, CombatFx's bind() call in
			# AnchorView.boot() then dies on a missing method, and boot() aborts before
			# _centre() runs, which leaves `_origin` at Vector2.ZERO and collapses the entire
			# board into a corner. One un-inferable local took the whole playfield down;
			# annotate anything reached through an untyped node reference.
			var r: Vector2 = fx.tile_radius_screen(rng * clampf(f * 1.15, 0.0, 1.0))
			var alpha: float = (1.0 - f) * FIELD_PULSE_PEAK_ALPHA
			var ring_col := Color(col.r, col.g, col.b, alpha * glow)
			var pts := PackedVector2Array()
			for i in range(28):
				var a := TAU * float(i) / 28.0
				pts.append(pos + Vector2(cos(a) * r.x, sin(a) * r.y))
			pts.append(pts[0])
			draw_polyline(pts, ring_col, 1.5)
		var shimmer: float = (0.5 + 0.5 * sin(t * 1.1 + offset)) * 0.08
		draw_circle(pos + Vector2(0, -18), 7.0, Color(col.r, col.g, col.b, shimmer * glow))


func _draw_sustained_beams(glow: float) -> void:
	## ART-05: a held beam per firing "sustained"-class emplacement, drawn every frame
	## straight off CombatFx's own _beams state — never through the MAX_FX pool (see that
	## var's doc for why). No age/life fade like the pooled "beam" kind: the whole point is
	## that it does not snap, so per entry it is either fully on or absent.
	var beams: Dictionary = fx.beams()
	if beams.is_empty():
		return
	var view: Node2D = fx.view
	var now: float = view.sim_time()
	# Decision 007 / CLAUDE.md: every emissive element dims under brownout. Mirrors
	# glow_layer.gd's own factor for sprite glow rather than inventing a second number.
	var bus_energy: float = 0.35 if bool(view.sim.brownout) else 1.0
	for key in beams.keys():
		var b: Dictionary = beams[key]
		_draw_one_beam(b, now, glow * bus_energy)


func _draw_one_beam(b: Dictionary, now: float, energy: float) -> void:
	## `energy` already folds in the accessibility ceiling (Display.glow) and the brownout
	## factor — this function just draws. `hum` is a smooth, deterministic, per-slot-offset
	## oscillation (no randf()): a sustained beam must show no per-shot flicker, and an
	## unseeded per-frame jitter here would both read as strobing (an accessibility concern
	## Display.glow's own ceiling exists for) and reintroduce LF-111's non-reproducible pixel
	## diffs into a screenshot that is otherwise easy to keep stable across runs.
	var from: Vector2 = b["from"]
	var to: Vector2 = b["to"]
	var ramp: float = float(b.get("ramp", 0.0))
	var flicker_amt: float = float(b.get("flicker", 0.0))
	var hum: float = 1.0 + flicker_amt * sin(now * 9.0 + float(b.get("offset", 0.0)))
	var width: float = float(b["width"]) * (1.0 + ramp * 0.45) * hum
	var base_colour: Color = b["colour"]
	var tint_colour: Color = b["ramp_tint"]
	var colour: Color = base_colour.lerp(tint_colour, ramp)
	var core: Color = b["core"]
	var outer: Color = colour
	outer.a = 0.5 * energy * hum
	draw_line(from, to, outer, width * 2.6)
	var main: Color = colour
	main.a = energy
	draw_line(from, to, main, width)
	var core_col: Color = core
	core_col.a = energy
	draw_line(from, to, core_col, width * 0.4)
	draw_circle(to, width * 1.8, Color(core_col.r, core_col.g, core_col.b, energy))
	draw_circle(from, width * 0.9, Color(core_col.r, core_col.g, core_col.b, energy * 0.8))
