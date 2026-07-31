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
## Presentation-only signals for the combat FX layer. None of them may be read by anything
## that decides a rule — see _step()/_damage() for exactly where and why each fires; the
## same contract as the `aim` key precedent already in this file.
signal shot_fired(placed: Dictionary, from_tile: Vector2, to_tile: Vector2, target_kind: Dictionary)
signal unit_damaged(unit_kind: Dictionary, at_tile: Vector2, amount: float, killed: bool, shielded_resist: bool)
signal splash_landed(at_tile: Vector2, radius: float)

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

## Per-effect placed lists (LF-099). _covered_by() walks one of these instead of every
## placed emplacement, so a board with nothing carrying `effect` never pays for the
## walk. Kept fresh two ways: unconditionally at the top of tick(), before bus_load() —
## the primary point, mirroring sim/engine.py's _tick_once() — and eagerly, right after
## every write to `placed`/`.online`: build_at(), sell(), upgrade(), set_online(). A
## tick-only rebuild would leave capacity() blind to a restorer built or sold moments
## ago by the exact same caller that then asks for a fresh number (parity.gd's
## _try_build() calls build_at() and capacity() alternately, just as
## sim/engine.py's _try_build() does) — a real behaviour change, not a rounding error.
var _eff_slow: Array[Dictionary] = []
var _eff_damp: Array[Dictionary] = []
var _eff_reveal: Array[Dictionary] = []
var _eff_restore: Array[Dictionary] = []

# ─────────────────────────────────────────────────────── GDScript-only state ──
#
# Overcharge, Shutter and veterancy (see data/tuning.json's `abilities` and `veterancy`
# blocks), plus per-emplacement targeting priority. None of this exists in sim/engine.py and
# none of it is set by any policy tools/test_parity.py runs — every field below defaults to
# the value that reproduces this file's *previous* behaviour exactly, and the only way any
# of them ever changes is a call from scripts/anchor_view.gd in response to the player
# pressing something. Decision 033 already set this precedent for sell(); this is the same
# argument applied to four more mechanics instead of one.
var overcharge_active: bool = false
var _overcharge_fire_rate_bonus: float = 0.0
var _overcharge_draw_mult: float = 1.0

var shutter_active: bool = false
var _shutter_hold_tiles: float = 0.0
var _shutter_draw_mw: float = 0.0

## [{kills, damage_mult, range_mult}, ...], authored ascending by kills. Empty (the default,
## and the only state any parity run ever sees) means _veteran_rank() always returns {} and
## every multiplier below reads as 1.0 regardless of a placed record's kill count.
var _veterancy_ranks: Array = []

# Waypoints are held as float64 arrays, not PackedVector2Array. Vector2 stores float32
# components, so every position the sim derived from one was a rounded copy of what the
# Python reference computed — invisible until Act II, where damper coverage turned a
# sub-ulp position difference into a different bus load, a different fire rate, and six
# extra leaks. Decision 030.
#
# WAR-01: an anchor carries 1-5 lanes, so each of these is now an Array holding one
# PackedFloat64Array per lane (except path_length itself, which needs only one float per
# lane and is a PackedFloat64Array directly), built in the same loop order as
# sim/content.py's Lane.build(). A lane is addressed everywhere below by its integer
# INDEX into these arrays — stable, orderable, identical in both languages — never by
# its `id`, which is authoring/dialog/HUD-only.
var _wx: Array = []              # Array[PackedFloat64Array], one per lane
var _wy: Array = []              # Array[PackedFloat64Array], one per lane
## Elevation LEVEL per waypoint (never pixels, never a world height — PRD 2.3). Carried
## for the migration TER-01 will read; no rule below reads it yet.
var _wz: Array = []              # Array[PackedInt64Array], one per lane
var _seg_len: Array = []         # Array[PackedFloat64Array], one per lane
var _cum_len: Array = []         # Array[PackedFloat64Array], one per lane
var path_length: PackedFloat64Array = PackedFloat64Array()   # one entry per lane


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

	_wx = []
	_wy = []
	_wz = []
	_seg_len = []
	_cum_len = []
	path_length = PackedFloat64Array()
	for lane_doc in anchor.get("paths", []):
		var wx := PackedFloat64Array()
		var wy := PackedFloat64Array()
		var wz := PackedInt64Array()
		for p in lane_doc.get("waypoints", []):
			wx.append(float(p[0]))
			wy.append(float(p[1]))
			wz.append(int(p[2]) if p.size() > 2 else 0)
		var seg := PackedFloat64Array()
		var cum := PackedFloat64Array([0.0])
		var total := 0.0
		for i in range(wx.size() - 1):
			var d: float = abs(wx[i + 1] - wx[i]) + abs(wy[i + 1] - wy[i])   # axis-aligned
			seg.append(d)
			total += d
			cum.append(total)
		_wx.append(wx)
		_wy.append(wy)
		_wz.append(wz)
		_seg_len.append(seg)
		_cum_len.append(cum)
		path_length.append(total)


func point_at_xy(lane: int, dist: float) -> PackedFloat64Array:
	## World position `dist` tiles along `lane`, in float64. Mirrors Anchor.point_at().
	## No defaulted `lane` overload — see the module-level note on why a defaulted lane
	## is exactly how a call site goes missed and silently reads lane 0 forever.
	var wx: PackedFloat64Array = _wx[lane]
	var wy: PackedFloat64Array = _wy[lane]
	var seg_len: PackedFloat64Array = _seg_len[lane]
	var cum_len: PackedFloat64Array = _cum_len[lane]
	var plen: float = path_length[lane]
	var last: int = wx.size() - 1
	if dist <= 0.0:
		return PackedFloat64Array([wx[0], wy[0]])
	if dist >= plen:
		return PackedFloat64Array([wx[last], wy[last]])
	for i in range(seg_len.size()):
		if dist <= cum_len[i + 1]:
			var seg: float = seg_len[i]
			var f: float = 0.0 if seg == 0.0 else (dist - cum_len[i]) / seg
			return PackedFloat64Array([
				wx[i] + (wx[i + 1] - wx[i]) * f,
				wy[i] + (wy[i + 1] - wy[i]) * f])
	return PackedFloat64Array([wx[last], wy[last]])


func point_at(lane: int, dist: float) -> Vector2:
	## Float32 convenience for drawing. Never use it for a rules decision — the sim
	## itself works in point_at_xy()'s float64. No defaulted `lane` overload — see the
	## note on point_at_xy() above.
	var p := point_at_xy(lane, dist)
	return Vector2(p[0], p[1])


# ───────────────────────────────────────────────────────────────── power ──

func online_draw() -> float:
	var v := 0.0
	# _overcharge_draw_mult defaults to 1.0 and overcharge_active defaults to false, so this
	# multiply is a no-op for every parity run — see the GDScript-only state block above.
	var draw_mult: float = _overcharge_draw_mult if overcharge_active else 1.0
	for p in placed:
		if p["online"]:
			v += float(p["tower"]["draw_mw"]) * draw_mult
	# Shutter draws a flat load of its own while it is down, on top of whatever is placed —
	# the relief it buys is bought on the bus like everything else in this game. Defaults to
	# 0, so this adds nothing unless the player has actually raised it.
	if shutter_active:
		v += _shutter_draw_mw
	return v


func set_overcharge(active: bool, fire_rate_bonus: float = 0.0, draw_mult: float = 1.0) -> void:
	## GDScript-only (see the state block above). `active` false and the defaults reproduce
	## this file's behaviour before Overcharge existed exactly, so scripts/anchor_view.gd is
	## the only caller and only ever passes non-default values while the ability is running.
	overcharge_active = active
	_overcharge_fire_rate_bonus = fire_rate_bonus
	_overcharge_draw_mult = draw_mult


func set_shutter(active: bool, hold_tiles: float = 0.0, draw_mw: float = 0.0) -> void:
	## GDScript-only (see the state block above). Queuing the arrivals themselves — "arrivals
	## queue instead of spawning" — is not here: spawning is already driven from outside this
	## file, by scripts/anchor_view.gd's own wave clock calling spawn(), so withholding a
	## spawn call is entirely a caller-side decision and needs no rule in this file at all.
	## What *is* a rule, because it changes a unit's movement inside tick(), is holding
	## anything already on the board within hold_tiles of the entrance at zero speed — see
	## _step().
	shutter_active = active
	_shutter_hold_tiles = hold_tiles
	_shutter_draw_mw = draw_mw


func set_veterancy_ranks(ranks: Array) -> void:
	## GDScript-only (see the state block above). `ranks` is [{kills, damage_mult,
	## range_mult}, ...], already resolved by the caller — Recoveries.veterancy_mult()
	## scaling each rank's kill threshold is a save-file concern this file must not read (see
	## Recoveries' own docstring on why it stays out of anchor_sim.gd), so scripts/
	## anchor_view.gd computes the final numbers and hands them in once, at boot.
	_veterancy_ranks = ranks


func veterancy_ranks() -> Array:
	## Read-only accessor for the HUD (scripts/hud.gd draws a rank pip and its kill count
	## from this) — already scaled by Recoveries.veterancy_mult(), because it is exactly what
	## set_veterancy_ranks() was handed. A getter changes no rule; it exists so the HUD does
	## not have to duplicate the multiplier arithmetic to show the same ranks the sim compares
	## a placed record's kills against.
	return _veterancy_ranks


func _veteran_rank(p: Dictionary) -> Dictionary:
	## The highest rank `p`'s kill count has reached, or {} if _veterancy_ranks is empty (the
	## default) or the record has not killed anything yet. Kills live on the placed record —
	## never on a unit dictionary, see the comment on _face()'s precedent in anchor_view.gd
	## and the same reasoning repeated on the splash loop below — so annotating it here is
	## exactly as safe as `aim` and `view_yaw` already are: placed records are only ever
	## compared by `slot`.
	if _veterancy_ranks.is_empty():
		return {}
	var kills := int(p.get("kills", 0))
	var best := {}
	for r in _veterancy_ranks:
		if kills >= int(r.get("kills", 0)):
			best = r
	return best


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
		var at := point_at_xy(int(u["lane"]), u["dist"])
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
	for p in _eff_restore:
		var eff: Dictionary = p["tower"].get("effect", {})
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
	# Eager, not tick-gated (LF-099): capacity()/_covered_by(), possibly called again
	# before the next tick() by whatever placed this, must see it. See _eff_slow.
	_rebuild_effect_lists()
	free_slots.erase(slot)
	funds -= int(tw["cost"])
	spend += int(tw["cost"])
	funds_changed.emit(funds)
	built.emit(tower_id, slot)
	return true


func set_online(index: int, on: bool) -> void:
	if index >= 0 and index < placed.size():
		placed[index]["online"] = on
		_rebuild_effect_lists()   # eager, not tick-gated (LF-099) — see _eff_slow


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
	## Recoveries.sell_refund_add() is read HERE rather than transformed into the sim's
	## inputs, because there is nothing to transform — sell() is GDScript-only and
	## sim/engine.py has no sell at all (decision 033), so this cannot reach parity. It is
	## one of the three effects decision 054 exempts from the Loadout path for that reason.
	## Clamped at 1.0: a refund above what was paid would make build-and-sell a money
	## printer, which no recovery in the pool is priced to be.
	var refund := int(floor(float(paid) * minf(1.0, SELL_REFUND + Recoveries.sell_refund_add())))
	free_slots.append(p["slot"])
	placed.remove_at(index)
	_rebuild_effect_lists()   # eager, not tick-gated (LF-099) — see _eff_slow
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
	_rebuild_effect_lists()   # eager, not tick-gated (LF-099) — see _eff_slow: an
	# upgrade can change a support tower's effect value (or, in principle, its type)
	funds -= cost
	spend += cost
	funds_changed.emit(funds)
	return true


# ────────────────────────────────────────────────────────────── coverage ──

func _covered_by(effect: String, x: float, y: float) -> float:
	## Squared-distance comparison, as in sim/engine.py — no square root is taken on
	## either side, so both runtimes do the same double arithmetic. Decision 030.
	##
	## Walks the pre-filtered list for `effect` (built by _rebuild_effect_lists(),
	## LF-099) instead of every placed emplacement, and returns 0.0 immediately when
	## that list is empty — a board with nothing carrying `effect` never pays for the
	## walk at all, even once, per unit, per tick.
	var eff_list: Array[Dictionary]
	match effect:
		"slow": eff_list = _eff_slow
		"damp": eff_list = _eff_damp
		"reveal": eff_list = _eff_reveal
		"restore": eff_list = _eff_restore
		_: eff_list = []
	if eff_list.is_empty():
		return 0.0
	var best := 0.0
	for p in eff_list:
		var dx: float = float(p["slot"].x) - x
		var dy: float = float(p["slot"].y) - y
		var r: float = float(p["tower"]["range"])
		if dx * dx + dy * dy <= r * r:
			var eff: Dictionary = p["tower"].get("effect", {})
			best = maxf(best, float(eff.get("value", 1.0)))
	return best


func _rebuild_effect_lists() -> void:
	## Rebuilds the four per-effect placed lists from `placed`, preserving order.
	## Mirrors sim/engine.py's Sim._rebuild_effect_lists() — see its docstring for the
	## full rationale (LF-099) and the list of call sites in each file. `maxf()` over
	## the values in play does not care what order this list is in, but the walk order
	## is kept identical to `placed`'s anyway, so a future change to "first wins" cannot
	## silently diverge between the two engines.
	_eff_slow = []
	_eff_damp = []
	_eff_reveal = []
	_eff_restore = []
	for p in placed:
		if not p["online"]:
			continue
		var eff: Dictionary = p["tower"].get("effect", {})
		match String(eff.get("type", "")):
			"slow": _eff_slow.append(p)
			"damp": _eff_damp.append(p)
			"reveal": _eff_reveal.append(p)
			"restore": _eff_restore.append(p)


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
	# Primary rebuild point (LF-099) — see the comment on _eff_slow above. Unconditional
	# every tick, not gated by a dirty flag; before bus_load(), its first consumer this
	# tick. Named here because sim/engine.py's _tick_once() rebuilds at the equivalent
	# point, for the same reason.
	_rebuild_effect_lists()
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
	# Overcharge's fire_rate_bonus, applied on top of the brownout penalty rather than
	# instead of it — the note in data/tuning.json is explicit that the ability has to lose
	# on a saturated bus for the power economy to mean anything, and this is what makes that
	# true: `rate` can still fall below 1.0 with the bonus applied, exactly as it does today
	# when overcharge_active is false (the default, and the only state a parity run sees).
	if overcharge_active:
		rate *= (1.0 + _overcharge_fire_rate_bonus)

	for u in units:
		if not u["alive"]:
			continue
		var lane: int = int(u["lane"])
		var at := point_at_xy(lane, u["dist"])
		var slow := _covered_by("slow", at[0], at[1])
		var speed: float = float(u["kind"]["speed"]) * (slow if slow > 0.0 else 1.0)
		# Shutter holds anything already inside hold_tiles of the entrance at zero speed
		# while it is down. _shutter_hold_tiles defaults to 0 and shutter_active to false, so
		# this changes nothing for a graded run.
		if shutter_active and float(u["dist"]) <= _shutter_hold_tiles:
			speed = 0.0
		u["dist"] = float(u["dist"]) + speed * DT
		if float(u["dist"]) >= path_length[lane]:
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
		# Veterancy: identity (1.0, 1.0) whenever _veterancy_ranks is empty, which is every
		# parity run — see set_veterancy_ranks()'s doc. Support emplacements never reach this
		# far (damage <= 0.0 already continued above), so a support tower's own range check
		# never scales by its own rank; it has no kills to have earned one with.
		var vet := _veteran_rank(p)
		var dmg_mult: float = float(vet.get("damage_mult", 1.0))
		var rng_mult: float = float(vet.get("range_mult", 1.0))
		var sx := float(p["slot"].x)
		var sy := float(p["slot"].y)
		var rng := float(tw["range"]) * rng_mult
		var target: Dictionary = {}
		# The target's *index*, because the splash loop below has to exclude the unit it
		# already hit and `==` on a Dictionary is a value comparison in Godot 4.7 — two
		# units of the same kind with equal hp and equal dist compare equal. sim/engine.py
		# writes `u is target`, an identity test, so the two implementations disagreed the
		# moment a wave put two identical units at the same distance. Latent rather than
		# live only because same-kind units spawn on different ticks. LF-055.
		var target_i := -1
		# Per-emplacement targeting priority (data/tuning.json's `targeting`), cycled by the
		# player on the selected emplacement. "first" reproduces this loop's original
		# behaviour exactly — furthest along the path wins ties — and stays the default, so a
		# placed record nobody has touched (every one built by build_at(), including every
		# grading policy's) has no "target_mode" key at all and this `match` falls through to
		# the same branch the old unconditional comparison used. Decision-033-shaped: the
		# other three modes are GDScript-only and sim/engine.py has no equivalent.
		var mode := String(p.get("target_mode", "first"))
		for i in range(units.size()):
			var u: Dictionary = units[i]
			if not u["alive"]:
				continue
			var at := point_at_xy(int(u["lane"]), u["dist"])
			var dx: float = sx - at[0]
			var dy: float = sy - at[1]
			if dx * dx + dy * dy > rng * rng:
				continue
			var revealed: bool = String(u["kind"].get("kind", "ground")) != "air" \
				or _covered_by("reveal", at[0], at[1]) > 0.0
			if not _can_target(tw, u, revealed):
				continue
			var keep: bool
			match mode:
				"last":
					keep = target.is_empty() or float(u["dist"]) < float(target["dist"])
				"strongest":
					keep = target.is_empty() or float(u["hp"]) > float(target["hp"])
				"weakest":
					keep = target.is_empty() or float(u["hp"]) < float(target["hp"])
				_:
					keep = target.is_empty() or float(u["dist"]) > float(target["dist"])
			if keep:
				target = u
				target_i = i
		if target.is_empty():
			# Ready to fire with nothing to shoot: drop the aim so the view can point the
			# emplacement back down the lane instead of at whatever it last killed.
			p.erase("aim")
			continue
		var tp := point_at_xy(int(target["lane"]), target["dist"])
		# Presentation only, and the only line in this file that is not a rule: where the
		# shot went, so anchor_view can face the emplacement at it. Deliberately absent from
		# sim/engine.py — the headless reference has nothing to draw, nothing reads this, and
		# parity compares outcomes. Position is unaffected by _damage, so computing it here
		# rather than inside the splash branch is the same number.
		p["aim"] = Vector2(float(tp[0]), float(tp[1]))
		# Presentation only: fires the instant the shot leaves the barrel, so the FX layer can
		# animate travel time the rules deliberately do not model (sim/engine.py's module
		# docstring: "What is not modelled, deliberately: projectile travel time"). `p` is the
		# same placed record `_face()` already annotates safely (compared only on `slot`);
		# `target["kind"]` is content data, not the mutable target dictionary.
		shot_fired.emit(p, Vector2(sx, sy), p["aim"], target["kind"])
		# Veterancy kills are counted on `p`, never on a unit — the same reasoning as
		# target_i above, repeated: a unit dictionary must never grow a key, and `placed`
		# records are safe because they are only ever compared by `slot`. Tracked
		# unconditionally, independent of whether _veterancy_ranks is set, so the count is
		# already correct the moment scripts/anchor_view.gd turns veterancy on mid-run.
		if _damage(target, tw, 1.0, dmg_mult):
			p["kills"] = int(p.get("kills", 0)) + 1
		var splash := float(tw.get("splash", 0.0))
		if splash > 0.0:
			# Presentation only: lets the view size the burst at the radius the rules
			# actually use, rather than a guess that could drift from tw["splash"].
			splash_landed.emit(p["aim"], splash)
			for i in range(units.size()):
				if i == target_i:
					continue
				var u: Dictionary = units[i]
				if not u["alive"]:
					continue
				var up := point_at_xy(int(u["lane"]), u["dist"])
				var sdx: float = up[0] - tp[0]
				var sdy: float = up[1] - tp[1]
				if sdx * sdx + sdy * sdy <= splash * splash:
					if _damage(u, tw, 0.5, dmg_mult):
						p["kills"] = int(p.get("kills", 0)) + 1
		p["cooldown"] = float(tw["fire_interval"])


func _damage(u: Dictionary, tw: Dictionary, scale: float, dmg_mult: float = 1.0) -> bool:
	# Armour first, then the shield tax on what got through. Mirrors Sim._damage().
	# `dmg_mult` defaults to 1.0 — identity — so every existing call before veterancy existed
	# is untouched; it is veterancy's damage_mult (see _veteran_rank()), applied here rather
	# than by scaling tw["damage"] itself because tw may be the *shared* Content.towers
	# dictionary and scaling it in place would buff that tower type for every board in the
	# session, the same trap AnchorSim.upgrade() already avoids by merging into a duplicate.
	var after_armour: float = maxf(0.0, float(tw["damage"]) * scale * dmg_mult
		- float(u["kind"].get("armour", 0.0)))
	var shield_scale := _shield_scale(tw, u)
	var dealt: float = after_armour * shield_scale
	u["hp"] = float(u["hp"]) - dealt
	var killed: bool = float(u["hp"]) <= 0.0
	# Presentation only: emitted after the hp change so the view can react to the outcome —
	# a hit flash, or a hard ricochet read when shielded_resist is true — without
	# recomputing shield logic itself. Carries the *kind* dictionary and a position, never
	# the mutable unit dictionary `u`: this file's splash loop above tests `i == target_i`
	# rather than `u == target` for exactly the reason a unit dictionary must never grow a
	# key (see the comment on target_i), and a listener holding a live reference to `u` would
	# be one keystroke away from writing to it.
	unit_damaged.emit(u["kind"], point_at(int(u["lane"]), float(u["dist"])), dealt, killed,
		shield_scale == SHIELD_LEAK)
	if killed:
		u["alive"] = false
		funds += int(float(u["kind"]["bounty"]) * bounty_mult)
		funds_changed.emit(funds)
		unit_killed.emit(u)
	# Return value is new: the caller (the placed loop in _step()) uses it to count a kill on
	# the emplacement's own placed record for veterancy. sim/engine.py's Sim._damage() has no
	# return value and nothing here reads this one for a rule, so it is presentation-adjacent
	# bookkeeping, not a rule change — the *rest* of this function's outcome (hp, funds,
	# alive, the signals) is identical to before.
	return killed


func fire_surge(cfg: Dictionary) -> Dictionary:
	## Threshold Surge (data/tuning.json `abilities`), GDScript-only per the state block
	## above — no grading policy ever calls this, so it cannot move a graded run's numbers.
	## Fires once, on the player's own action: a travelling wall of light down the lane, full
	## `cfg.damage` at the ring (dist == path_length, where a leak actually happens) falling
	## to `cfg.falloff_min` of it at the lane's mouth (dist == 0), because the discharge has
	## to originate at the ring the player is defending for the fiction to hold. Armour still
	## applies — armour is physical, shielding is not the same thing, decision 029 already
	## draws that line — but the shield tax never does; there is no call to _shield_scale()
	## anywhere below, which is what "ignores shielding" means here. Every survivor, hit or
	## not, is pushed back cfg.pushback_tiles: the wave physically reaches everything alive.
	## Returns {"kills": int, "damage": float} so the caller can size the trauma/dialog cue
	## on what actually happened rather than guessing.
	var damage := float(cfg.get("damage", 0.0))
	var falloff_min := float(cfg.get("falloff_min", 1.0))
	var pushback := float(cfg.get("pushback_tiles", 0.0))
	var kills := 0
	var total_dealt := 0.0
	for u in units:
		if not u["alive"]:
			continue
		var plen: float = path_length[int(u["lane"])]
		var dist: float = float(u["dist"])
		var frac: float = falloff_min if plen <= 0.0 else \
			lerpf(falloff_min, 1.0, clampf(dist / plen, 0.0, 1.0))
		var armour: float = float(u["kind"].get("armour", 0.0))
		var dealt: float = maxf(0.0, damage * frac - armour)
		if dealt > 0.0:
			u["hp"] = float(u["hp"]) - dealt
			total_dealt += dealt
		if float(u["hp"]) <= 0.0:
			u["alive"] = false
			funds += int(float(u["kind"]["bounty"]) * bounty_mult)
			funds_changed.emit(funds)
			unit_killed.emit(u)
			kills += 1
		else:
			u["dist"] = maxf(0.0, dist - pushback)
	return {"kills": kills, "damage": total_dealt}


func spawn(enemy_id: String, lane: int = 0) -> void:
	## `lane` defaults to 0 — unlike point_at()/point_at_xy(), this is a public entry
	## point external callers (scripts/anchor_view.gd, tools) reach for, and 0 is the
	## right default for a single-lane anchor exactly the way the schema's own `lane`
	## default is. The unit dictionary's `lane` key is written here, at construction,
	## and never afterwards — see the note on target_i in _step() for why a unit
	## dictionary must never grow a key post-construction (LF-055).
	var e: Dictionary = enemies[enemy_id]
	units.append({"kind": e, "hp": float(e["hp"]) * hp_mult, "dist": 0.0, "alive": true,
		"lane": lane})


func begin_wave(index: int) -> void:
	## Call before the prep phase of wave `index` (0-based). Mirrors the assignment to
	## Sim.wave_index at the top of Sim.run()'s wave loop.
	wave_index = index


func wave_queue(index: int) -> Array:
	## [[time, lane, enemy_id], ...] sorted the same way engine.py sorts it. A two-lane
	## anchor makes simultaneous spawns the normal case rather than the rare one, so the
	## tie-break has to be a full total order — (time, lane, enemy_id) — in both
	## languages, not merely "usually agrees". Mirrors Sim.run()'s queue sort exactly.
	var w: Dictionary = anchor["waves"][index]
	var q: Array = []
	for sp in w.get("spawns", []):
		var interval := float(sp.get("interval", 1.0))
		var delay := float(sp.get("delay", 0.0))
		var lane := int(sp.get("lane", 0))
		for n in range(int(sp["count"])):
			q.append([delay + n * interval, lane, String(sp["enemy"])])
	q.sort_custom(func(a, b):
		if a[0] != b[0]: return a[0] < b[0]
		if a[1] != b[1]: return a[1] < b[1]
		return a[2] < b[2])
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
