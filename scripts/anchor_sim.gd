class_name AnchorSim
extends RefCounted
## The rules of a Latticefall anchor, with no rendering and no node tree.
##
## This is a deliberate port of sim/engine.py. **The Python sim is the reference.**
## Any divergence between the two is a bug in this file, not in the sim — balance is
## graded against the Python version, so if they disagree the game is not playing the
## level that was signed off.
##
## scripts/test/parity.gd runs both and compares. tools/check.py runs that on every
## commit, which is the only thing that keeps this port honest over time.
##
## Kept free of Node so it can run headless in a test and inside a scene unchanged.

const DT: float = 1.0 / 30.0
# Mirrors brownout_penalty() in sim/engine.py — decision 022, superseding the flat
# 0.40 of decision 003. Priced by how far over the bus is, not as a cliff, so the
# power budget is a currency rather than a wall. Any change here must be made there
# too; tools/test_parity.py diffs the two on every commit.
const BROWNOUT_SLOPE: float = 1.5
const BROWNOUT_MAX_PENALTY: float = 0.70

## name -> [enemy hp multiplier, bounty multiplier]. Mirrors DIFFICULTIES in engine.py.
const DIFFICULTIES: Dictionary = {
	"standard": [1.00, 1.00],
	"hard": [1.35, 0.90],
	"brutal": [1.55, 0.80],
}

signal wave_started(index: int)
signal wave_cleared(index: int)
signal brownout_changed(active: bool)
signal unit_killed(unit: Dictionary)
signal unit_leaked(unit: Dictionary)
signal built(tower_id: String, slot: Vector2i)
signal lives_changed(lives: int)
signal funds_changed(funds: int)

var anchor: Dictionary
var towers: Dictionary
var enemies: Dictionary
var difficulty: String = "standard"

var hp_mult: float = 1.0
var bounty_mult: float = 1.0

var funds: int = 0
var spend: int = 0
var lives: int = 0
var leaks: int = 0
var placed: Array[Dictionary] = []      # {tower, slot:Vector2i, online:bool, cooldown:float}
var free_slots: Array[Vector2i] = []
var units: Array[Dictionary] = []       # {kind, hp, dist, alive}
var t: float = 0.0
var brownout: bool = false

var peak_load: float = 0.0
var _load_integral: float = 0.0
var _brownout_time: float = 0.0

var _waypoints: PackedVector2Array = PackedVector2Array()
var _seg_len: PackedFloat64Array = PackedFloat64Array()
var _cum_len: PackedFloat64Array = PackedFloat64Array()
var path_length: float = 0.0


func setup(anchor_data: Dictionary, tower_defs: Dictionary, enemy_defs: Dictionary,
		diff: String = "standard") -> void:
	anchor = anchor_data
	towers = tower_defs
	enemies = enemy_defs
	difficulty = diff
	var m: Array = DIFFICULTIES.get(diff, DIFFICULTIES["standard"])
	hp_mult = m[0]
	bounty_mult = m[1]

	funds = int(anchor.get("starting_funds", 0))
	lives = int(anchor.get("lives", 10))
	spend = 0
	leaks = 0

	free_slots.clear()
	for s in anchor.get("slots", []):
		free_slots.append(Vector2i(int(s[0]), int(s[1])))

	_waypoints = PackedVector2Array()
	for p in anchor.get("path", []):
		_waypoints.append(Vector2(float(p[0]), float(p[1])))
	_seg_len = PackedFloat64Array()
	_cum_len = PackedFloat64Array([0.0])
	var total := 0.0
	for i in range(_waypoints.size() - 1):
		var a := _waypoints[i]
		var b := _waypoints[i + 1]
		var d: float = abs(b.x - a.x) + abs(b.y - a.y)   # axis-aligned segments
		_seg_len.append(d)
		total += d
		_cum_len.append(total)
	path_length = total


func point_at(dist: float) -> Vector2:
	if dist <= 0.0:
		return _waypoints[0]
	if dist >= path_length:
		return _waypoints[_waypoints.size() - 1]
	for i in range(_seg_len.size()):
		if dist <= _cum_len[i + 1]:
			var seg: float = _seg_len[i]
			var f: float = 0.0 if seg == 0.0 else (dist - _cum_len[i]) / seg
			return _waypoints[i].lerp(_waypoints[i + 1], f)
	return _waypoints[_waypoints.size() - 1]


# ───────────────────────────────────────────────────────────────── power ──

func online_draw() -> float:
	var v := 0.0
	for p in placed:
		if p["online"]:
			v += float(p["tower"]["draw_mw"])
	return v


func bus_load() -> float:
	var v := online_draw()
	for u in units:
		if u["alive"]:
			v += float(u["kind"].get("drains_mw", 0.0))
	return v


func capacity() -> float:
	return float(anchor.get("capacity_mw", 0.0))


# ───────────────────────────────────────────────────────────────── build ──

func can_afford(tower_id: String) -> bool:
	return int(towers[tower_id].get("cost", 0)) <= funds


func build_at(tower_id: String, slot: Vector2i) -> bool:
	if not free_slots.has(slot) or not towers.has(tower_id):
		return false
	var tw: Dictionary = towers[tower_id]
	if int(tw["cost"]) > funds:
		return false
	placed.append({"tower": tw, "slot": slot, "online": true, "cooldown": 0.0})
	free_slots.erase(slot)
	funds -= int(tw["cost"])
	spend += int(tw["cost"])
	funds_changed.emit(funds)
	built.emit(tower_id, slot)
	return true


func set_online(index: int, on: bool) -> void:
	if index >= 0 and index < placed.size():
		placed[index]["online"] = on


# ────────────────────────────────────────────────────────────── coverage ──

func _covered_by(effect: String, at: Vector2) -> float:
	var best := 0.0
	for p in placed:
		if not p["online"]:
			continue
		var eff: Dictionary = p["tower"].get("effect", {})
		if eff.get("type", "") != effect:
			continue
		if Vector2(p["slot"]).distance_to(at) <= float(p["tower"]["range"]):
			best = maxf(best, float(eff.get("value", 1.0)))
	return best


func _can_target(tw: Dictionary, u: Dictionary, revealed: bool) -> bool:
	var targets: Array = tw["targets"]
	if String(u["kind"].get("kind", "ground")) == "air":
		return targets.has("air") and revealed
	if bool(u["kind"].get("shielded", false)):
		return targets.has("shielded")
	return targets.has("ground")


# ─────────────────────────────────────────────────────────────── ticking ──

func brownout_penalty(load_mw: float, cap_mw: float) -> float:
	## Fire-rate penalty in [0, BROWNOUT_MAX_PENALTY]. 0 at or under capacity.
	if cap_mw <= 0.0 or load_mw <= cap_mw:
		return 0.0
	return minf(BROWNOUT_MAX_PENALTY, (load_mw / cap_mw - 1.0) * BROWNOUT_SLOPE)


func penalty_now() -> float:
	## For the HUD: how hard the bus is being punished right now.
	return brownout_penalty(bus_load(), capacity())


func tick() -> void:
	var load := bus_load()
	var penalty := brownout_penalty(load, capacity())
	var over := penalty > 0.0
	if over != brownout:
		brownout = over
		brownout_changed.emit(over)
	peak_load = maxf(peak_load, load)
	_load_integral += load * DT
	if over:
		_brownout_time += DT
	t += DT
	_step(penalty)


func _step(penalty: float) -> void:
	var rate: float = 1.0 - penalty

	for u in units:
		if not u["alive"]:
			continue
		var at := point_at(u["dist"])
		var slow := _covered_by("slow", at)
		var speed: float = float(u["kind"]["speed"]) * (slow if slow > 0.0 else 1.0)
		u["dist"] = float(u["dist"]) + speed * DT
		if float(u["dist"]) >= path_length:
			u["alive"] = false
			leaks += 1
			lives -= 1
			lives_changed.emit(lives)
			unit_leaked.emit(u)

	for p in placed:
		var tw: Dictionary = p["tower"]
		if not p["online"] or float(tw["damage"]) <= 0.0:
			continue
		p["cooldown"] = float(p["cooldown"]) - DT * rate
		if float(p["cooldown"]) > 0.0:
			continue
		var slot_v := Vector2(p["slot"])
		var target: Dictionary = {}
		for u in units:
			if not u["alive"]:
				continue
			var at := point_at(u["dist"])
			if slot_v.distance_to(at) > float(tw["range"]):
				continue
			var revealed: bool = String(u["kind"].get("kind", "ground")) != "air" \
				or _covered_by("reveal", at) > 0.0
			if not _can_target(tw, u, revealed):
				continue
			if target.is_empty() or float(u["dist"]) > float(target["dist"]):
				target = u
		if target.is_empty():
			continue
		_damage(target, tw, 1.0)
		var splash := float(tw.get("splash", 0.0))
		if splash > 0.0:
			var tp := point_at(target["dist"])
			for u in units:
				if u == target or not u["alive"]:
					continue
				if point_at(u["dist"]).distance_to(tp) <= splash:
					_damage(u, tw, 0.5)
		p["cooldown"] = float(tw["fire_interval"])


func _damage(u: Dictionary, tw: Dictionary, scale: float) -> void:
	var dealt: float = maxf(0.0, float(tw["damage"]) * scale - float(u["kind"].get("armour", 0.0)))
	u["hp"] = float(u["hp"]) - dealt
	if float(u["hp"]) <= 0.0:
		u["alive"] = false
		funds += int(float(u["kind"]["bounty"]) * bounty_mult)
		funds_changed.emit(funds)
		unit_killed.emit(u)


func spawn(enemy_id: String) -> void:
	var e: Dictionary = enemies[enemy_id]
	units.append({"kind": e, "hp": float(e["hp"]) * hp_mult, "dist": 0.0, "alive": true})


func wave_queue(index: int) -> Array:
	## [[time, enemy_id], ...] sorted the same way engine.py sorts it.
	var w: Dictionary = anchor["waves"][index]
	var q: Array = []
	for sp in w.get("spawns", []):
		var interval := float(sp.get("interval", 1.0))
		var delay := float(sp.get("delay", 0.0))
		for n in range(int(sp["count"])):
			q.append([delay + n * interval, String(sp["enemy"])])
	q.sort_custom(func(a, b): return a[0] < b[0] if a[0] != b[0] else a[1] < b[1])
	return q


func prune_dead() -> void:
	var keep: Array[Dictionary] = []
	for u in units:
		if u["alive"]:
			keep.append(u)
	units = keep


func any_alive() -> bool:
	for u in units:
		if u["alive"]:
			return true
	return false


func mean_load() -> float:
	return 0.0 if t == 0.0 else _load_integral / t


func brownout_fraction() -> float:
	return 0.0 if t == 0.0 else _brownout_time / t
