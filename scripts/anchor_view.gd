@tool
extends Node2D
## Playable anchor. Owns an AnchorSim and renders it.
##
## The rules live entirely in AnchorSim, which is a port of the Python reference and
## is parity-tested against it on every commit. Nothing in this file may decide a
## rule — it drives the clock, draws the result, and turns clicks into calls.
##
## Sprites are albedo + glow pairs (decision 007); the glow child layer is modulated
## by bus load so a brownout visibly dims the board.
##
## `@tool` so the board draws in the editor. Before this, scenes/main.tscn was a bare
## Node2D that built every node in _ready(), which meant opening the project showed an
## empty grey viewport and a scene dock with one childless node — the level could not
## be seen or judged without pressing Run. The editor path reads the anchor JSON and
## draws tiles, path and slots only; it never constructs a sim, never touches Audio,
## and never runs the clock.

const AnchorSimScript := preload("res://scripts/anchor_sim.gd")
const IsoScript := preload("res://scripts/iso.gd")
const AnchorDataScript := preload("res://scripts/anchor_data.gd")

signal state_changed
signal dialog_trigger(trigger: String)
signal wave_state(index: int, total: int, phase: String)

@export var anchor_id: String = "anchor-01":
	set(value):
		anchor_id = value
		if Engine.is_editor_hint():
			_editor_refresh()
@export var difficulty: String = "standard"


func _validate_property(property: Dictionary) -> void:
	## Turn anchor_id into a dropdown of the levels that actually exist, so switching
	## the previewed anchor in the inspector cannot be misspelled.
	if property.name == "anchor_id":
		property.hint = PROPERTY_HINT_ENUM
		property.hint_string = ",".join(AnchorDataScript.anchor_ids())

const NO_SLOT := Vector2i(-999, -999)

var sim
var selected_tower: String = ""
var hovered_slot: Vector2i = NO_SLOT
## The emplacement the inspector is pointed at. Distinct from `hovered_slot` on purpose:
## sell, upgrade and the power toggle used to act on whatever the cursor was over, and
## reaching those buttons means dragging the cursor off the board and across every tile
## in between — which silently retargeted them. Decision 035.
var selected_slot: Vector2i = NO_SLOT

var _accum: float = 0.0
var _wave_index: int = -1
var _queue: Array = []
var _qi: int = 0
var _wave_t: float = 0.0
var _lead_left: float = 0.0
var _phase: String = "idle"      # idle | prep | combat | done | lost
var _fired_triggers: Dictionary = {}
var _origin: Vector2 = Vector2.ZERO
var _sim_t: float = 0.0          # total simulated seconds, for reproducibility checks
var glow_layer: Node2D


func _ready() -> void:
	glow_layer = get_node_or_null("GlowLayer")
	set_process(false)
	if Engine.is_editor_hint():
		# A tool script would otherwise tick and take input inside the editor.
		set_process_unhandled_input(false)
		_editor_refresh()
		return
	set_process_unhandled_input(true)


func boot(aid: String, diff: String) -> void:
	## Called by main.gd after the CLI has been parsed, not from _ready().
	##
	## The scene now authors this node as a child of Main, so _ready() here runs
	## *before* Main._ready() and therefore before `--anchor` has been read. Doing
	## setup on an explicit call keeps the CLI able to choose the level.
	anchor_id = aid
	difficulty = diff
	var anchor: Dictionary = Content.anchor(anchor_id)
	if anchor.is_empty():
		push_error("anchor_view: no data for %s" % anchor_id)
		return
	sim = AnchorSimScript.new()
	sim.setup(anchor, Content.towers, Content.enemies, difficulty)
	sim.brownout_changed.connect(_on_brownout)
	sim.unit_killed.connect(func(_u): Audio.sfx("warden_death"))
	sim.unit_leaked.connect(func(_u): Audio.sfx("ui_deny"))

	var unlocked: Array = Content.unlocked_at(anchor_id)
	selected_tower = String(unlocked[0]) if unlocked.size() > 0 else ""

	_centre()
	queue_redraw()


func _editor_refresh() -> void:
	## Editor-only. No sim, no audio, no clock — just re-centre and repaint.
	if not is_inside_tree():
		return
	_centre()
	queue_redraw()
	if glow_layer:
		glow_layer.queue_redraw()


var _autobuild := false


func autobuild() -> void:
	## Debug/smoke aid: build the way the 'cheap-mass' policy would, so combat can be
	## exercised without a human. Never called during normal play.
	##
	## It re-runs at the start of every wave, which is what the grading policies do —
	## they spend bounty income between waves. Building only once, before wave one, is a
	## different (and much worse) player: on anchor-01 it can afford three turrets out of
	## 300 starting funds and loses, while the policy that graded the level buys five
	## across six waves and clears it with all ten lives. A smoke test that plays a
	## strictly worse game than the one that was balanced is not evidence of anything.
	_autobuild = true
	_autobuild_step()


func _autobuild_step() -> void:
	var unlocked: Array = Content.unlocked_at(anchor_id)
	while sim.free_slots.size() > 0:
		var placed_one := false
		for tid in unlocked:
			var tw: Dictionary = Content.tower(tid)
			if int(tw["cost"]) > sim.funds:
				continue
			if sim.online_draw() + float(tw["draw_mw"]) > sim.capacity():
				continue
			if sim.build_at(String(tid), sim.free_slots[0]):
				placed_one = true
				break
		if not placed_one:
			return


func start() -> void:
	## Called by main after every listener is wired. Firing the opening brief from
	## _ready() emitted it before dialog_view existed, so the lines vanished.
	_begin_wave(0)
	_fire("brief")
	set_process(true)


func _anchor_data() -> Dictionary:
	## The level, whether or not a sim exists. The editor preview has no sim, and no
	## autoloads either, so it reads the file directly rather than through Content.
	if sim != null:
		return sim.anchor
	if Engine.is_editor_hint():
		return AnchorDataScript.anchor(anchor_id)
	return Content.anchor(anchor_id)


func _sprite_lib() -> Node:
	## The Sprites autoload if it exists. It does not exist while editing unless the
	## project has been reloaded since sprites.gd became a tool script, so every caller
	## must cope with null and fall back to flat-colour drawing.
	return get_node_or_null(^"/root/Sprites")


func _tex(sprite_name: String, yaw: int, pass_name: String) -> Texture2D:
	var lib := _sprite_lib()
	if lib == null or not lib.ok:
		return null
	return lib.get_tex(sprite_name, yaw, pass_name)


func _centre() -> void:
	## Centre the board so the whole diamond fits the viewport.
	var grid: Dictionary = _anchor_data().get("grid", {"w": 12, "h": 10})
	var w: int = int(grid["w"])
	var h: int = int(grid["h"])
	var mid := IsoScript.tile_to_screen(float(w) * 0.5, float(h) * 0.5)
	if Engine.is_editor_hint():
		# There is no game viewport while editing, and get_viewport_rect() would
		# return the editor's. Hang the board off this node's own origin instead,
		# so it is centred on wherever the node sits in the scene.
		_origin = -mid
		return
	var vp := get_viewport_rect().size
	_origin = Vector2(vp.x * 0.5, vp.y * 0.42) - mid


# ─────────────────────────────────────────────────────────────── clock ──

func _process(delta: float) -> void:
	if sim == null or _phase in ["done", "lost"]:
		return
	_accum += minf(delta, 0.25)      # clamp so a stall cannot fast-forward the level
	while _accum >= AnchorSimScript.DT:
		_accum -= AnchorSimScript.DT
		_sim_t += AnchorSimScript.DT
		_advance()
	queue_redraw()


func _advance() -> void:
	if _phase == "prep":
		_lead_left -= AnchorSimScript.DT
		sim.tick()
		if _lead_left <= 0.0:
			_phase = "combat"
			_fire("wave-start:%d" % (_wave_index + 1))
			wave_state.emit(_wave_index + 1, sim.anchor["waves"].size(), _phase)
		return

	if _phase == "combat":
		while _qi < _queue.size() and float(_queue[_qi][0]) <= _wave_t + 1e-9:
			sim.spawn(String(_queue[_qi][1]))
			_qi += 1
		sim.tick()
		_wave_t += AnchorSimScript.DT

		if sim.lives <= 0:
			_phase = "lost"
			Audio.stinger("SYS-LOS")
			_fire("debrief")
			state_changed.emit()
			return
		if _qi >= _queue.size() and not sim.any_alive():
			sim.prune_dead()
			_fire("wave-clear:%d" % (_wave_index + 1))
			if _wave_index + 1 >= sim.anchor["waves"].size():
				_phase = "done"
				Audio.stinger("SYS-WIN")
				_fire("debrief")
			else:
				_begin_wave(_wave_index + 1)
			state_changed.emit()


func _begin_wave(index: int) -> void:
	_wave_index = index
	sim.begin_wave(index)          # Act III: the bus loses its decay before the prep phase
	if _autobuild:
		_autobuild_step()
	_queue = sim.wave_queue(index)
	_qi = 0
	_wave_t = 0.0
	_lead_left = float(sim.anchor["waves"][index].get("lead_in", 20.0))
	_phase = "prep"
	wave_state.emit(index + 1, sim.anchor["waves"].size(), _phase)


func _on_brownout(active: bool) -> void:
	Audio.sfx("brownout_alarm" if active else "brownout_recover")
	Audio.set_brownout(active)
	if active:
		_fire("brownout")
	state_changed.emit()


func _fire(trigger: String) -> void:
	## Each trigger fires once. A repeated line reads as a bug to the player.
	if _fired_triggers.has(trigger):
		return
	_fired_triggers[trigger] = true
	dialog_trigger.emit(trigger)


# ─────────────────────────────────────────────────────────────── input ──

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		var t := IsoScript.screen_to_tile(get_global_mouse_position() - _origin)
		hovered_slot = Vector2i(roundi(t.x), roundi(t.y))
		queue_redraw()
	elif event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_LEFT:
			_click(hovered_slot)
		elif event.button_index == MOUSE_BUTTON_RIGHT:
			toggle_at(hovered_slot)


func placed_index_at(slot: Vector2i) -> int:
	for i in range(sim.placed.size()):
		if sim.placed[i]["slot"] == slot:
			return i
	return -1


func sell_at(slot: Vector2i) -> void:
	var i := placed_index_at(slot)
	if i < 0:
		Audio.sfx("ui_deny")
		return
	sim.sell(i)
	if selected_slot == slot:
		selected_slot = NO_SLOT      # the inspector was pointed at something that is gone
	Audio.sfx("ui_sell")
	state_changed.emit()
	queue_redraw()


func upgrade_at(slot: Vector2i) -> void:
	var i := placed_index_at(slot)
	if i < 0 or not sim.upgrade(i):
		Audio.sfx("ui_deny")
		return
	Audio.sfx("ui_upgrade")
	Audio.sfx("power_online")
	state_changed.emit()
	queue_redraw()


func _click(slot: Vector2i) -> void:
	## One click, three outcomes, in this order: point the inspector at an emplacement that
	## is already there, build on a free slot, or put the inspector down. Selecting is
	## checked first because a built slot is never a free slot, so the two can never race.
	if placed_index_at(slot) >= 0:
		selected_slot = slot
		Audio.sfx("ui_click")
		state_changed.emit()
		queue_redraw()
		return
	if not sim.free_slots.has(slot):
		# Bare ground. Deselecting is a deliberate act, not a failed one — no deny cue.
		selected_slot = NO_SLOT
		state_changed.emit()
		queue_redraw()
		return
	if selected_tower == "" or not sim.can_afford(selected_tower):
		Audio.sfx("ui_deny")
		return
	if sim.build_at(selected_tower, slot):
		selected_slot = slot         # inspect and upgrade what was just built, without a hunt
		Audio.sfx("place_emplacement")
		Audio.sfx("power_online")
		state_changed.emit()
		queue_redraw()


func toggle_at(slot: Vector2i) -> void:
	## Shed an emplacement's load without losing it. Right-click does this where the cursor
	## is; the inspector does it to the selection.
	for i in range(sim.placed.size()):
		if sim.placed[i]["slot"] == slot:
			var now: bool = not sim.placed[i]["online"]
			sim.set_online(i, now)
			Audio.sfx("power_online" if now else "power_offline")
			state_changed.emit()
			return
	Audio.sfx("ui_deny")


func select(tower_id: String) -> void:
	## Arming something to build puts the board selection down. The inspector can only
	## describe one emplacement, and picking from the build bar is the player asking about
	## the one they just picked — leaving the board selection up left the panel describing a
	## turret on the board while the bar highlighted a different one the player was reading
	## about, which is the panel answering a question nobody asked.
	selected_tower = tower_id
	selected_slot = NO_SLOT
	Audio.sfx("ui_click")
	queue_redraw()


# ──────────────────────────────────────────────────────────────── draw ──

const C_TILE := Color(0.09, 0.13, 0.15)
const C_TILE_ALT := Color(0.10, 0.15, 0.17)
const C_PATH := Color(0.30, 0.22, 0.10)
const C_SLOT := Color(0.20, 0.34, 0.31)
const C_VERD := Color(0.37, 0.66, 0.58)
const C_AMBER := Color(0.91, 0.64, 0.24)
const C_ALERT := Color(0.82, 0.33, 0.25)
const C_SHADOW := Color(0.0, 0.0, 0.0, 0.34)
const C_BONE := Color(0.86, 0.89, 0.88)


func drawables() -> Array:
	## One ordered list, shared by the albedo draw and the additive glow layer, so the
	## two passes cannot disagree about contents or depth order.
	var out: Array = []
	for p in sim.placed:
		out.append({
			"depth": IsoScript.depth(p["slot"].x, p["slot"].y),
			"kind": "tower",
			"sprite": String(p["tower"]["id"]).replace("-", "_"),
			"yaw": 45,
			"online": bool(p["online"]),
			"at": IsoScript.tile_to_screen(float(p["slot"].x), float(p["slot"].y)) + _origin,
			"ref": p,
		})
	for u in sim.units:
		if not u["alive"]:
			continue
		var at: Vector2 = sim.point_at(u["dist"])
		out.append({
			"depth": IsoScript.depth(at.x, at.y),
			"kind": "unit",
			"sprite": String(u["kind"]["id"]).replace("-", "_"),
			"yaw": 45,
			"online": true,
			"at": IsoScript.tile_to_screen(at.x, at.y) + _origin,
			"ref": u,
		})
	out.sort_custom(func(a, b): return a["depth"] < b["depth"])
	return out


func _draw() -> void:
	var anchor: Dictionary = _anchor_data()
	if anchor.is_empty():
		return
	_draw_board(anchor)
	if sim == null:
		_draw_editor_overlay(anchor)     # no sim means we are previewing, not playing
		return
	_draw_reach()
	_draw_hover()
	_draw_entities()
	_draw_selection()


func _draw_board(anchor: Dictionary) -> void:
	## The static level: ground, path and slot tiles. Shared by the running game and
	## the editor preview so the two can never disagree about what a level looks like.
	var grid: Dictionary = anchor["grid"]
	var path_tiles := _path_tiles(anchor)

	var slot_set := {}
	for slot in anchor["slots"]:
		slot_set[Vector2i(int(slot[0]), int(slot[1]))] = true

	# painter's order: increasing tile depth, so a nearer tile overdraws a farther one
	for s_ in range(int(grid["w"]) + int(grid["h"]) - 1):
		for x in range(int(grid["w"])):
			var y := s_ - x
			if y < 0 or y >= int(grid["h"]):
				continue
			var cell := Vector2i(x, y)
			var c := IsoScript.tile_to_screen(float(x), float(y)) + _origin
			var kind := "tile_ground"
			if path_tiles.has(cell):
				kind = "tile_path"
			elif slot_set.has(cell):
				kind = "tile_slot"
			var tex: Texture2D = _tex(kind, 45, "albedo")
			if tex != null:
				draw_texture(tex, c - _sprite_lib().pivot)
			else:
				var col := C_TILE if (x + y) % 2 == 0 else C_TILE_ALT
				if path_tiles.has(cell):
					col = C_PATH
				elif slot_set.has(cell):
					col = C_SLOT
				draw_colored_polygon(IsoScript.diamond(c, 0.98), col)


func _draw_hover() -> void:
	var anchor: Dictionary = sim.anchor
	var is_slot := false
	for slot in anchor["slots"]:
		if Vector2i(int(slot[0]), int(slot[1])) == hovered_slot:
			is_slot = true
			break
	if not is_slot or not sim.free_slots.has(hovered_slot):
		return
	var hc := IsoScript.tile_to_screen(float(hovered_slot.x), float(hovered_slot.y)) + _origin
	var ring := IsoScript.diamond(hc, 0.92)
	draw_polyline(ring + PackedVector2Array([ring[0]]), C_AMBER, 2.0)


func _draw_reach() -> void:
	## Range, drawn on the ground, because "3.2 tiles" in the inspector does not answer the
	## only question that matters: does this gun cover that corner. Bone is what the selected
	## emplacement covers now — red if it is offline and covering nothing. Amber is what the
	## armed emplacement in the build bar *would* cover if it were built on the hovered slot.
	var i := placed_index_at(selected_slot)
	if i >= 0:
		var p: Dictionary = sim.placed[i]
		_draw_range(Vector2(selected_slot), float(p["tower"]["range"]),
				Color(C_BONE if p["online"] else C_ALERT, 0.5))
	if selected_tower != "" and sim.free_slots.has(hovered_slot) and hovered_slot != selected_slot:
		var tw: Dictionary = Content.tower(selected_tower)
		if not tw.is_empty():
			_draw_range(Vector2(hovered_slot), float(tw["range"]), Color(C_AMBER, 0.4))


func _draw_selection() -> void:
	## Drawn after the sprites, unlike the hover ring: the emplacement stands on its own
	## tile and covers most of it, so a ring drawn on the ground under a 256px sprite is
	## four white specks around its base and reads as nothing at all.
	if placed_index_at(selected_slot) < 0:
		return
	var c := IsoScript.tile_to_screen(float(selected_slot.x), float(selected_slot.y)) + _origin
	var ring := IsoScript.diamond(c, 1.0)
	draw_polyline(ring + PackedVector2Array([ring[0]]), Color(C_BONE, 0.85), 2.0)
	# Corner ticks, so the selection is legible against a bright tile as well as a dark one.
	for corner in ring:
		draw_circle(corner, 3.0, C_BONE)


func _draw_range(centre: Vector2, r: float, col: Color) -> void:
	## The rules compare distance in *tile* space (decision 030), so reach is a circle there
	## and a 2:1 ellipse once projected — the same ratio as the tile, for the same reason.
	## Sampling the tile-space circle and projecting each point draws exactly the set the
	## weapon can reach, and stays correct if the projection ever changes.
	const SEGMENTS := 48
	var pts := PackedVector2Array()
	for i in range(SEGMENTS):
		var a := TAU * float(i) / float(SEGMENTS)
		pts.append(IsoScript.tile_to_screen(centre.x + cos(a) * r, centre.y + sin(a) * r) + _origin)
	pts.append(pts[0])
	draw_polyline(pts, col, 2.0)


func _draw_contact_shadow(at: Vector2, radius: float) -> void:
	## Without this a sprite reads as floating over the board rather than standing on
	## it (LF-024). Drawn in engine rather than baked into the sprite: a baked shadow
	## would be part of the albedo silhouette and could not sit under the *neighbouring*
	## tile, which is exactly where a contact shadow has to fall. The ellipse is 2:1
	## because the tile is (decision 017).
	var pts := PackedVector2Array()
	for i in range(16):
		var a := TAU * float(i) / 16.0
		pts.append(at + Vector2(cos(a) * radius, sin(a) * radius * 0.5))
	draw_colored_polygon(pts, C_SHADOW)


func _draw_entities() -> void:
	var dim: float = 0.6 if sim.brownout else 1.0
	# Every shadow first, so a nearer sprite's shadow cannot land on top of a farther
	# sprite that has already been drawn.
	for d in drawables():
		_draw_contact_shadow(d["at"], 27.0 if d["kind"] == "tower" else 15.0)
	for d in drawables():
		var tex: Texture2D = _tex(d["sprite"], d["yaw"], "albedo")
		if tex != null:
			var tint := Color(1, 1, 1)
			if d["kind"] == "tower" and not d["online"]:
				tint = Color(0.45, 0.48, 0.5)      # offline reads as cold, not just unlit
			draw_texture(tex, d["at"] - _sprite_lib().pivot, tint)
			if d["kind"] == "unit":
				_draw_health(d["ref"], d["at"])
		elif d["kind"] == "tower":
			_draw_tower(d["ref"], dim)
		else:
			_draw_unit(d["ref"])


func _draw_editor_overlay(anchor: Dictionary) -> void:
	## Authoring aid, editor only. Shows the things a level is actually made of —
	## where units enter and leave, which way the path runs, and which tiles are
	## buildable — so an anchor can be judged without running it.
	var pts: Array = anchor.get("path", [])
	if pts.size() >= 2:
		var line := PackedVector2Array()
		for p in pts:
			line.append(IsoScript.tile_to_screen(float(p[0]), float(p[1])) + _origin)
		draw_polyline(line, Color(C_AMBER, 0.55), 3.0)
		for i in range(pts.size() - 1):
			_draw_arrow(line[i], line[i + 1])
		_draw_marker(line[0], C_VERD, "IN")
		_draw_marker(line[line.size() - 1], C_ALERT, "OUT")

	for i in range(anchor.get("slots", []).size()):
		var slot: Array = anchor["slots"][i]
		var c := IsoScript.tile_to_screen(float(slot[0]), float(slot[1])) + _origin
		var ring := IsoScript.diamond(c, 0.88)
		draw_polyline(ring + PackedVector2Array([ring[0]]), Color(C_VERD, 0.9), 2.0)
		_label(c + Vector2(0, 4), str(i + 1), Color(C_VERD, 0.9))


func _draw_arrow(a: Vector2, b: Vector2) -> void:
	var mid := (a + b) * 0.5
	var dir := (b - a).normalized()
	var perp := Vector2(-dir.y, dir.x)
	draw_colored_polygon(PackedVector2Array([
		mid + dir * 9.0, mid - dir * 5.0 + perp * 5.0, mid - dir * 5.0 - perp * 5.0,
	]), Color(C_AMBER, 0.85))


func _draw_marker(c: Vector2, col: Color, text: String) -> void:
	draw_circle(c, 9.0, Color(col, 0.85))
	_label(c + Vector2(0, -16), text, col)


func _label(c: Vector2, text: String, col: Color) -> void:
	var font := ThemeDB.fallback_font
	var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, 12).x
	draw_string(font, c - Vector2(w * 0.5, 0), text, HORIZONTAL_ALIGNMENT_LEFT, -1, 12, col)


func _draw_tower(p: Dictionary, dim: float) -> void:
	var c := IsoScript.tile_to_screen(float(p["slot"].x), float(p["slot"].y)) + _origin
	var online: bool = p["online"]
	var body := Color(0.42, 0.46, 0.48) if online else Color(0.22, 0.24, 0.26)
	draw_colored_polygon(IsoScript.diamond(c + Vector2(0, -14), 0.55), body)
	draw_rect(Rect2(c + Vector2(-9, -34), Vector2(18, 22)), body)
	if online:
		# emissive stand-in. dims with bus load, which is the whole point of
		# keeping glow a separate layer rather than baking it (decision 007).
		var glow := (C_VERD if not sim.brownout else C_ALERT)
		draw_circle(c + Vector2(0, -34), 5.0, Color(glow.r, glow.g, glow.b, dim))


func _draw_health(u: Dictionary, c: Vector2) -> void:
	var kind: Dictionary = u["kind"]
	var frac: float = clampf(float(u["hp"]) / (float(kind["hp"]) * sim.hp_mult), 0.0, 1.0)
	if frac >= 0.999:
		return                                   # full health bars are visual noise
	draw_rect(Rect2(c + Vector2(-11, -30), Vector2(22, 3)), Color(0, 0, 0, 0.65))
	draw_rect(Rect2(c + Vector2(-11, -30), Vector2(22.0 * frac, 3)),
			C_VERD if frac > 0.35 else C_ALERT)


func _draw_unit(u: Dictionary) -> void:
	var at: Vector2 = sim.point_at(u["dist"])
	var c := IsoScript.tile_to_screen(at.x, at.y) + _origin
	var kind: Dictionary = u["kind"]
	var col := C_ALERT if String(kind.get("faction", "")) == "ordinal" else C_AMBER
	var r: float = 7.0 + 5.0 * clampf(float(kind["hp"]) / 220.0, 0.0, 1.0)
	draw_circle(c + Vector2(0, -8), r, col)
	var frac: float = clampf(float(u["hp"]) / (float(kind["hp"]) * sim.hp_mult), 0.0, 1.0)
	draw_rect(Rect2(c + Vector2(-10, -24), Vector2(20, 3)), Color(0, 0, 0, 0.6))
	draw_rect(Rect2(c + Vector2(-10, -24), Vector2(20.0 * frac, 3)), C_VERD)


func _path_tiles(anchor: Dictionary) -> Dictionary:
	var out := {}
	var pts: Array = anchor["path"]
	for i in range(pts.size() - 1):
		var a := Vector2i(int(pts[i][0]), int(pts[i][1]))
		var b := Vector2i(int(pts[i + 1][0]), int(pts[i + 1][1]))
		var step := Vector2i(signi(b.x - a.x), signi(b.y - a.y))
		var cur := a
		out[cur] = true
		while cur != b:
			cur += step
			out[cur] = true
	return out


func phase() -> String:
	return _phase


func wave_number() -> int:
	return _wave_index + 1


func lead_left() -> float:
	## Seconds of prep remaining before the current wave spawns. The clock was already
	## running and only the sim could see it, so a player in prep had no idea whether they
	## had twenty seconds to spend a bounty or two.
	return maxf(_lead_left, 0.0)


func sim_time() -> float:
	return _sim_t
