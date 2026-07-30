class_name AbilityState
extends RefCounted
## Charge / cooldown / duration bookkeeping for the three bindstone abilities
## (`abilities` in data/tuning.json). GDScript-only, the same precedent decision 033 set
## for AnchorSim.sell(): none of surge, overcharge or shutter exists in sim/engine.py, no
## grading policy in tools/test_parity.py ever touches this class, and it is never
## instantiated by scripts/test/parity.gd — so a graded run's output cannot depend on it.
##
## What actually changes the *rules* — fire rate, draw, damage, movement — lives on
## AnchorSim itself (set_overcharge(), set_shutter(), fire_surge()), so the tick-by-tick
## simulation logic stays in the one file that is parity-tested. This class only answers
## "is it ready, how charged, how long is left" and owns the timers that decide that;
## scripts/anchor_view.gd ticks it every simulated frame and calls into AnchorSim when a
## timer actually crosses a boundary.
##
## Owned by AnchorView (one instance per board), not an autoload — its state is per-run,
## not global, and letting a second board keep counting an earlier board's cooldowns would
## be exactly the shared-mutable-Content bug Loadout's docstring already warns about.

var defs: Dictionary = {}          ## id -> ability def, as authored in tuning.json
var _order: Array[String] = []     ## ids in authored order, for iteration that is stable
var charge: Dictionary = {}        ## id -> float, meaningful only when charge_max > 0
var cooldown: Dictionary = {}      ## id -> float seconds remaining
var active_left: Dictionary = {}   ## id -> float seconds remaining while its effect is live
var _first_fired: Dictionary = {}  ## id -> bool, so a caller can fire "<id>-first" once


func _init(ability_defs: Array) -> void:
	for d in ability_defs:
		var id := String(d["id"])
		defs[id] = d
		_order.append(id)
		charge[id] = 0.0
		cooldown[id] = 0.0
		active_left[id] = 0.0


func ids() -> Array[String]:
	return _order


func def(id: String) -> Dictionary:
	return defs.get(id, {})


func is_charge_gated(id: String) -> bool:
	## charge_max == 0 means the ability runs on cooldown_s instead — the schema's own
	## description of the field (data/schema/tuning.schema.json).
	return float(def(id).get("charge_max", 0.0)) > 0.0


func charge_frac(id: String) -> float:
	var cmax := float(def(id).get("charge_max", 0.0))
	return 1.0 if cmax <= 0.0 else clampf(float(charge.get(id, 0.0)) / cmax, 0.0, 1.0)


func cooldown_frac(id: String) -> float:
	## 0 means ready (or charge-gated, where cooldown plays no part); 1 means just used.
	var cd := float(def(id).get("cooldown_s", 0.0))
	return 0.0 if cd <= 0.0 else clampf(float(cooldown.get(id, 0.0)) / cd, 0.0, 1.0)


func active_frac(id: String) -> float:
	var dur := float(def(id).get("duration_s", 0.0))
	return 0.0 if dur <= 0.0 else clampf(float(active_left.get(id, 0.0)) / dur, 0.0, 1.0)


func is_active(id: String) -> bool:
	return float(active_left.get(id, 0.0)) > 0.0


func ready(id: String) -> bool:
	if not defs.has(id) or is_active(id):
		return false
	if is_charge_gated(id):
		return charge_frac(id) >= 1.0
	return float(cooldown.get(id, 0.0)) <= 0.0


func add_charge(id: String, amount: float) -> void:
	## `amount` already carries whatever multiplier the caller wants applied — the
	## charge_per_leak_cost rate and Recoveries.surge_charge_mult() are both tuning/save
	## concerns this class does not know about, by design (see the file docstring).
	if amount <= 0.0 or not is_charge_gated(id):
		return
	var cmax := float(def(id).get("charge_max", 0.0))
	charge[id] = clampf(float(charge.get(id, 0.0)) + amount, 0.0, cmax)


func force_ready(id: String) -> void:
	## Verification-only: skip straight to `ready(id) == true` without waiting on real kills
	## or a real cooldown. main.gd's `-- --ability <id>` CLI flag is the only caller — the
	## reachable-only-by-playing states (overcharge active, shutter down, a surge with
	## something to hit) are otherwise unscreenshottable at --fixed-fps, the same reasoning
	## `--build`, `--select` and `--cursor` already established in main.gd.
	if not defs.has(id):
		return
	active_left[id] = 0.0
	cooldown[id] = 0.0
	if is_charge_gated(id):
		charge[id] = float(def(id).get("charge_max", 0.0))


func began(id: String) -> void:
	## Call once an ability actually fires: resets its charge (if gated) and starts its
	## cooldown and active duration. Cooldown starts immediately, concurrent with the active
	## window, not after it — both authored cooldowns already exceed their own duration
	## (overcharge 35s cooldown vs 7s duration; shutter 50s vs 5s), so the duration is
	## already inside the cooldown and does not need to be added on top of it.
	var d := def(id)
	if is_charge_gated(id):
		charge[id] = 0.0
	cooldown[id] = float(d.get("cooldown_s", 0.0))
	active_left[id] = float(d.get("duration_s", 0.0))


func first_fire(id: String) -> bool:
	## True the first time this is asked for a given id, false ever after — the caller uses
	## it to fire the `<id>-first` dialog trigger exactly once, mirroring AnchorView._fire().
	if _first_fired.has(id):
		return false
	_first_fired[id] = true
	return true


func tick(dt: float) -> Array[String]:
	## Advance every timer by dt. Returns the ids whose active window just expired this
	## tick, so the caller can turn the matching AnchorSim-side effect back off exactly once
	## — set_overcharge(false) / set_shutter(false, ...), and release anything Shutter held.
	var expired: Array[String] = []
	for id in _order:
		if float(active_left.get(id, 0.0)) > 0.0:
			active_left[id] = maxf(0.0, float(active_left[id]) - dt)
			if float(active_left[id]) <= 0.0:
				expired.append(id)
		elif float(cooldown.get(id, 0.0)) > 0.0:
			cooldown[id] = maxf(0.0, float(cooldown[id]) - dt)
	return expired
