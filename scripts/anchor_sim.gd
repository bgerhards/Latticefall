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
## Mirrors SHIELD_LEAK in sim/engine.py. Shielding taxes damage from weapons not rated
## for it rather than blocking them outright — decision 029.
const SHIELD_LEAK: float = 0.25
## Act III decay never takes the bus below this fraction of rated capacity. Mirrors
## CAPACITY_FLOOR in sim/engine.py.
const CAPACITY_FLOOR: float = 0.45
## Fraction of what was paid that a sold emplacement returns. Below about a half,
## selling is a punishment and the player simply never does it; at 1.0 the board can be
## rebuilt free every wave and the build decision stops being a decision.
const SELL_REFUND: float = 0.6

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
## Waves begun, 0-based. Act III capacity decay is priced off this. The runner sets it
## with begin_wave() before the prep phase, so a build is made against the capacity the
## wave will actually run at.
var wave_index: int = 0

var peak_load: float = 0.0
var _load_integral: float = 0.0
var _brownout_time: float = 0.0

# Waypoints are held as two float64 arrays, not a PackedVector2Array. Vector2 stores
# float32 components, so every position the sim derived from one was a rounded copy of
# what the Python reference computed — invisible until Act II, where damper coverage
# turned a sub-ulp position difference into a different bus load, a different fire rate,
# and six extra leaks. Decision 030.
var _wx: PackedFloat64Array = PackedFloat64Array()
var _wy: PackedFloat64Array = PackedFloat64Array()
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

	_wx = PackedFloat64Array()
	_wy = PackedFloat64Array()
	for p in anchor.get("path", []):
		_wx.append(float(p[0]))
		_wy.append(float(p[1]))
	_seg_len = PackedFloat64Array()
	_cum_len = PackedFloat64Array([0.0])
	var total := 0.0
	for i in range(_wx.size() - 1):
		var d: float = abs(_wx[i + 1] - _wx[i]) + abs(_wy[i + 1] - _wy[i])   # axis-aligned
		_seg_len.append(d)
		total += d
		_cum_len.append(total)
	path_length = total


func point_at_xy(dist: float) -> PackedFloat64Array:
	## World position `dist` tiles along the path, in float64. Mirrors Anchor.point_at().
	var last: int = _wx.size() - 1
	if dist <= 0.0:
		return PackedFloat64Array([_wx[0], _wy[0]])
	if dist >= path_length:
		return PackedFloat64Array([_wx[last], _wy[last]])
	for i in range(_seg_len.size()):
		if dist <= _cum_len[i + 1]:
			var seg: float = _seg_len[i]
			var f: float = 0.0 if seg == 0.0 else (dist - _cum_len[i]) / seg
			return PackedFloat64Array([
				_wx[i] + (_wx[i + 1] - _wx[i]) * f,
				_wy[i] + (_wy[i + 1] - _wy[i]) * f])
	return PackedFloat64Array([_wx[last], _wy[last]])


func point_at(dist: float) -> Vector2:
	## Float32 convenience for drawing. Never use it for a rules decision — the sim
	## itself works in point_at_xy()'s float64.
	var p := point_at_xy(dist)
	return Vector2(p[0], p[1])


# ───────────────────────────────────────────────────────────────── power ──

func online_draw() -> float:
	var v := 0.0
	for p in placed:
		if p["online"]:
			v += float(p["tower"]["draw_mw"])
	return v


func bus_load() -> float:
	var v := online_draw()
	# Mirrors Sim.bus_load() in sim/engine.py: a damper suppresses that fraction of the
	# drain of any unit inside its radius. Decision 027.
	for u in units:
		if not u["alive"]:
			continue
		var drain := float(u["kind"].get("drains_mw", 0.0))
		if drain <= 0.0:
			continue
		var at := point_at_xy(u["dist"])
		var damp: float = minf(1.0, _covered_by("damp", at[0], at[1]))
		v += drain * (1.0 - damp)
	return v


func capacity() -> float:
	## Capacity for the current wave. Fixed in Acts I and II; falls by
	## `capacity_decay_mw` per wave in Act III, floored at CAPACITY_FLOOR of rated.
	## Mirrors Sim.capacity_now() in sim/engine.py. Decision 031.
	var rated := float(anchor.get("capacity_mw", 0.0))
	var decay := float(anchor.get("capacity_decay_mw", 0.0))
	var base := rated
	if decay > 0.0:
		base = maxf(rated * CAPACITY_FLOOR, rated - decay * float(wave_index))
	for p in placed:
		if not p["online"]:
			continue
		var eff: Dictionary = p["tower"].get("effect", {})
		if String(eff.get("type", "")) == "restore":
			base += float(eff.get("value", 0.0))
	return base


func rated_capacity() -> float:
	## What the anchor is nominally rated at, before Act III decay. For the HUD.
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


func sell(index: int) -> int:
	## Remove an emplacement and refund SELL_REFUND of what was paid for it, upgrade
	## included. Returns the refund, or 0 if there was nothing there.
	##
	## Not modelled in sim/engine.py and not used by any grading policy: a policy builds
	## once, at the start of a wave, and never changes its mind. That keeps the two rule
	## sets in parity — the grade describes a board that was never re-planned, which is a
	## floor on what a player can do rather than a description of best play. Decision 033.
	if index < 0 or index >= placed.size():
		return 0
	var p: Dictionary = placed[index]
	var paid: int = int(p["tower"]["cost"]) + int(p.get("upgrade_paid", 0))
	var refund := int(floor(float(paid) * SELL_REFUND))
	free_slots.append(p["slot"])
	placed.remove_at(index)
	funds += refund
	funds_changed.emit(funds)
	return refund


func upgrade_cost(index: int) -> int:
	## What upgrading this emplacement would cost, or 0 if it cannot be upgraded.
	if index < 0 or index >= placed.size():
		return 0
	var p: Dictionary = placed[index]
	if bool(p.get("upgraded", false)):
		return 0
	var up: Dictionary = p["tower"].get("upgrade", {})
	if up.is_empty():
		return 0
	return int(up.get("cost", 0))


func upgrade(index: int) -> bool:
	## Spend to improve one emplacement in place. The upgraded stats are a *copy* of the
	## tower definition with the upgrade block merged in, so nothing mutates the shared
	## Content dictionary — an upgrade on one board would otherwise upgrade that
	## emplacement type for every board in the session.
	var cost := upgrade_cost(index)
	if cost <= 0 or cost > funds:
		return false
	var p: Dictionary = placed[index]
	var merged: Dictionary = p["tower"].duplicate(true)
	for k in Dictionary(p["tower"]["upgrade"]):
		if k == "cost":
			continue
		merged[k] = p["tower"]["upgrade"][k]
	merged["name"] = "%s II" % String(p["tower"]["name"])
	p["tower"] = merged
	p["upgraded"] = true
	p["upgrade_paid"] = cost
	funds -= cost
	spend += cost
	funds_changed.emit(funds)
	return true


# ────────────────────────────────────────────────────────────── coverage ──

func _covered_by(effect: String, x: float, y: float) -> float:
	## Squared-distance comparison, as in sim/engine.py — no square root is taken on
	## either side, so both runtimes do the same double arithmetic. Decision 030.
	var best := 0.0
	for p in placed:
		if not p["online"]:
			continue
		var eff: Dictionary = p["tower"].get("effect", {})
		if eff.get("type", "") != effect:
			continue
		var dx: float = float(p["slot"].x) - x
		var dy: float = float(p["slot"].y) - y
		var r: float = float(p["tower"]["range"])
		if dx * dx + dy * dy <= r * r:
			best = maxf(best, float(eff.get("value", 1.0)))
	return best


func _can_target(tw: Dictionary, u: Dictionary, revealed: bool) -> bool:
	var targets: Array = tw["targets"]
	if String(u["kind"].get("kind", "ground")) == "air":
		return targets.has("air") and revealed
	return targets.has("ground")


func _shield_scale(tw: Dictionary, u: Dictionary) -> float:
	## Mirrors Sim._shield_scale(): a weapon not rated for shielding still lands a
	## quarter of its damage.
	if bool(u["kind"].get("shielded", false)) and not Array(tw["targets"]).has("shielded"):
		return SHIELD_LEAK
	return 1.0


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
		var at := point_at_xy(u["dist"])
		var slow := _covered_by("slow", at[0], at[1])
		var speed: float = float(u["kind"]["speed"]) * (slow if slow > 0.0 else 1.0)
		u["dist"] = float(u["dist"]) + speed * DT
		if float(u["dist"]) >= path_length:
			u["alive"] = false
			leaks += 1
			# Priced by the unit, not a flat 1 — and this must stay byte-identical to
			# sim/engine.py, including the default of 1 for a unit that does not name one.
			# Decision 047.
			lives -= int(u["kind"].get("leak_cost", 1))
			lives_changed.emit(lives)
			unit_leaked.emit(u)

	for p in placed:
		var tw: Dictionary = p["tower"]
		if not p["online"] or float(tw["damage"]) <= 0.0:
			continue
		p["cooldown"] = float(p["cooldown"]) - DT * rate
		if float(p["cooldown"]) > 0.0:
			continue
		var sx := float(p["slot"].x)
		var sy := float(p["slot"].y)
		var rng := float(tw["range"])
		var target: Dictionary = {}
		for u in units:
			if not u["alive"]:
				continue
			var at := point_at_xy(u["dist"])
			var dx: float = sx - at[0]
			var dy: float = sy - at[1]
			if dx * dx + dy * dy > rng * rng:
				continue
			var revealed: bool = String(u["kind"].get("kind", "ground")) != "air" \
				or _covered_by("reveal", at[0], at[1]) > 0.0
			if not _can_target(tw, u, revealed):
				continue
			if target.is_empty() or float(u["dist"]) > float(target["dist"]):
				target = u
		if target.is_empty():
			# Ready to fire with nothing to shoot: drop the aim so the view can point the
			# emplacement back down the lane instead of at whatever it last killed.
			p.erase("aim")
			continue
		var tp := point_at_xy(target["dist"])
		# Presentation only, and the only line in this file that is not a rule: where the
		# shot went, so anchor_view can face the emplacement at it. Deliberately absent from
		# sim/engine.py — the headless reference has nothing to draw, nothing reads this, and
		# parity compares outcomes. Position is unaffected by _damage, so computing it here
		# rather than inside the splash branch is the same number.
		p["aim"] = Vector2(float(tp[0]), float(tp[1]))
		_damage(target, tw, 1.0)
		var splash := float(tw.get("splash", 0.0))
		if splash > 0.0:
			for u in units:
				if u == target or not u["alive"]:
					continue
				var up := point_at_xy(u["dist"])
				var sdx: float = up[0] - tp[0]
				var sdy: float = up[1] - tp[1]
				if sdx * sdx + sdy * sdy <= splash * splash:
					_damage(u, tw, 0.5)
		p["cooldown"] = float(tw["fire_interval"])


func _damage(u: Dictionary, tw: Dictionary, scale: float) -> void:
	# Armour first, then the shield tax on what got through. Mirrors Sim._damage().
	var after_armour: float = maxf(0.0, float(tw["damage"]) * scale
		- float(u["kind"].get("armour", 0.0)))
	var dealt: float = after_armour * _shield_scale(tw, u)
	u["hp"] = float(u["hp"]) - dealt
	if float(u["hp"]) <= 0.0:
		u["alive"] = false
		funds += int(float(u["kind"]["bounty"]) * bounty_mult)
		funds_changed.emit(funds)
		unit_killed.emit(u)


func spawn(enemy_id: String) -> void:
	var e: Dictionary = enemies[enemy_id]
	units.append({"kind": e, "hp": float(e["hp"]) * hp_mult, "dist": 0.0, "alive": true})


func begin_wave(index: int) -> void:
	## Call before the prep phase of wave `index` (0-based). Mirrors the assignment to
	## Sim.wave_index at the top of Sim.run()'s wave loop.
	wave_index = index


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
