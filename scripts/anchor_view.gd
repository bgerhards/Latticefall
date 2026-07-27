extends Node2D
## Playable anchor. Owns an AnchorSim and renders it.
##
## The rules live entirely in AnchorSim, which is a port of the Python reference and
## is parity-tested against it on every commit. Nothing in this file may decide a
## rule — it drives the clock, draws the result, and turns clicks into calls.
##
## Art is placeholder geometry. Sprites arrive as albedo + glow pairs (decision 007);
## the glow layer will be modulated by bus load so brownout visibly dims the board.

const AnchorSimScript := preload("res://scripts/anchor_sim.gd")
const IsoScript := preload("res://scripts/iso.gd")

signal state_changed
signal dialog_trigger(trigger: String)
signal wave_state(index: int, total: int, phase: String)

@export var anchor_id: String = "anchor-01"
@export var difficulty: String = "standard"

var sim
var selected_tower: String = ""
var hovered_slot: Vector2i = Vector2i(-999, -999)

var _accum: float = 0.0
var _wave_index: int = -1
var _queue: Array = []
var _qi: int = 0
var _wave_t: float = 0.0
var _lead_left: float = 0.0
var _phase: String = "idle"      # idle | prep | combat | done | lost
var _fired_triggers: Dictionary = {}
var _origin: Vector2 = Vector2.ZERO


func _ready() -> void:
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
	set_process(false)
	set_process_unhandled_input(true)


func autobuild() -> void:
	## Debug/smoke aid: fill slots the way the 'cheap-mass' policy would, so combat can
	## be exercised without a human. Never called during normal play.
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


func _centre() -> void:
	## Centre the board so the whole diamond fits the viewport.
	var grid: Dictionary = Content.anchor(anchor_id).get("grid", {"w": 12, "h": 10})
	var w: int = int(grid["w"])
	var h: int = int(grid["h"])
	var vp := get_viewport_rect().size
	var mid := IsoScript.tile_to_screen(float(w) * 0.5, float(h) * 0.5)
	_origin = Vector2(vp.x * 0.5, vp.y * 0.42) - mid


# ─────────────────────────────────────────────────────────────── clock ──

func _process(delta: float) -> void:
	if _phase in ["done", "lost"]:
		return
	_accum += minf(delta, 0.25)      # clamp so a stall cannot fast-forward the level
	while _accum >= AnchorSimScript.DT:
		_accum -= AnchorSimScript.DT
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
			_toggle(hovered_slot)


func _click(slot: Vector2i) -> void:
	if selected_tower == "" or not sim.free_slots.has(slot):
		Audio.sfx("ui_deny")
		return
	if not sim.can_afford(selected_tower):
		Audio.sfx("ui_deny")
		return
	if sim.build_at(selected_tower, slot):
		Audio.sfx("place_emplacement")
		Audio.sfx("power_online")
		state_changed.emit()


func _toggle(slot: Vector2i) -> void:
	for i in range(sim.placed.size()):
		if sim.placed[i]["slot"] == slot:
			var now: bool = not sim.placed[i]["online"]
			sim.set_online(i, now)
			Audio.sfx("power_online" if now else "power_offline")
			state_changed.emit()
			return
	Audio.sfx("ui_deny")


func select(tower_id: String) -> void:
	selected_tower = tower_id
	Audio.sfx("ui_click")


# ──────────────────────────────────────────────────────────────── draw ──

const C_TILE := Color(0.09, 0.13, 0.15)
const C_TILE_ALT := Color(0.10, 0.15, 0.17)
const C_PATH := Color(0.30, 0.22, 0.10)
const C_SLOT := Color(0.20, 0.34, 0.31)
const C_VERD := Color(0.37, 0.66, 0.58)
const C_AMBER := Color(0.91, 0.64, 0.24)
const C_ALERT := Color(0.82, 0.33, 0.25)


func _draw() -> void:
	if sim == null:
		return
	var anchor: Dictionary = sim.anchor
	var grid: Dictionary = anchor["grid"]
	var path_tiles := _path_tiles()

	for y in range(int(grid["h"])):
		for x in range(int(grid["w"])):
			var c := IsoScript.tile_to_screen(float(x), float(y)) + _origin
			var col := C_TILE if (x + y) % 2 == 0 else C_TILE_ALT
			if path_tiles.has(Vector2i(x, y)):
				col = C_PATH
			draw_colored_polygon(IsoScript.diamond(c, 0.98), col)

	for slot in anchor["slots"]:
		var sv := Vector2i(int(slot[0]), int(slot[1]))
		var c := IsoScript.tile_to_screen(float(sv.x), float(sv.y)) + _origin
		var free: bool = sim.free_slots.has(sv)
		var col := C_SLOT if free else Color(0.16, 0.22, 0.24)
		draw_colored_polygon(IsoScript.diamond(c, 0.86), col)
		if free and sv == hovered_slot:
			draw_polyline(IsoScript.diamond(c, 0.9) + PackedVector2Array([IsoScript.diamond(c, 0.9)[0]]),
					C_AMBER, 2.0)

	# emplacements, then units, sorted by depth so overlap reads correctly
	var drawables: Array = []
	for p in sim.placed:
		drawables.append([IsoScript.depth(p["slot"].x, p["slot"].y), "tower", p])
	for u in sim.units:
		if u["alive"]:
			var at: Vector2 = sim.point_at(u["dist"])
			drawables.append([IsoScript.depth(at.x, at.y), "unit", u])
	drawables.sort_custom(func(a, b): return a[0] < b[0])

	var dim: float = 0.6 if sim.brownout else 1.0
	for d in drawables:
		if d[1] == "tower":
			_draw_tower(d[2], dim)
		else:
			_draw_unit(d[2])


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


func _path_tiles() -> Dictionary:
	var out := {}
	var pts: Array = sim.anchor["path"]
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
