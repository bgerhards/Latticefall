extends Node
## Autoload `Recoveries`. What the player picked up between anchors, and what it does.
##
## The between-anchor recovery draft (see `recoveries` in data/tuning.json) is the thread
## the campaign was missing: twenty-four anchors with nothing accumulating across them, and
## an unlock schedule the player did not choose is a calendar, not progression. After an
## anchor clears, the player is offered `draft_size` recovered Ordinal fragments and keeps
## one for the rest of the campaign.
##
## This file owns two things: the pool (loaded from data/tuning.json, the same file the
## design note lives in) and a typed accessor per effect, so a call site never reasons about
## the pool directly — it asks `Recoveries.tower_draw_mult()` and gets a number that is 1.0
## (or 0, for an additive effect) when the player owns nothing that touches it. Every
## accessor's identity value is documented on the accessor itself.
##
## Ownership of *whether* an effect is owned is Progress's — `Progress.owned_recoveries` is
## the persisted list, because Progress is the save and losing this list should cost exactly
## what losing a cleared anchor costs, no more. This file is the pool plus the arithmetic.
##
## **These accessors are not read by the sim, and must not be.** Six of the nine effects are
## applied by `scripts/loadout.gd`, which transforms private copies of the tower, enemy and
## anchor dictionaries *before* `AnchorSim.setup()` is handed them. `scripts/anchor_sim.gd`
## is a port of `sim/engine.py` and the two are diffed on every commit by
## `tools/test_parity.py`; adding a `Recoveries.*` branch there would make parity depend on
## nobody happening to own a recovery when the harness runs, where transforming the inputs
## keeps the sim a pure function of its data and parity holds by construction. Read
## `loadout.gd`'s docstring before adding an effect to the pool — an effect that cannot be
## expressed as a change to a *field* does not belong in it.
##
## The three exceptions — `sell_refund_add`, `surge_charge_mult`, `veterancy_mult` — are read
## directly at their call sites, and are safe for the opposite reason: selling, the bindstone
## abilities and veterancy are GDScript-only systems `sim/engine.py` does not model at all,
## the precedent decision 033 set for sell().

## One recovered fragment per kept campaign choice.
const DRAFT_SIZE_DEFAULT := 3

var draft_size: int = DRAFT_SIZE_DEFAULT
var _pool: Array = []          # ordered as authored in tuning.json
var _by_id: Dictionary = {}    # id -> pool entry
## `grade.thresholds` from tuning.json, in the order authored — descending by lives_frac,
## which `verdict_name()` depends on.
var _grade_thresholds: Array = []


func _ready() -> void:
	_load()


# ──────────────────────────────────────────────────────────────── loading ──

func _load() -> void:
	## Content (scripts/content.gd) has no generic "read arbitrary data file" entry point —
	## only towers, enemies, one anchor and one dialog file at a time — so this follows its
	## _read() pattern (open, parse, verify it is an object) rather than reaching into its
	## private method or opening the file a third, different way.
	var f := FileAccess.open("res://data/tuning.json", FileAccess.READ)
	if f == null:
		push_error("recoveries: cannot open data/tuning.json (%d)" % FileAccess.get_open_error())
		return
	var doc: Variant = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("recoveries: data/tuning.json is not a JSON object")
		return
	var rec: Dictionary = doc.get("recoveries", {})
	draft_size = int(rec.get("draft_size", DRAFT_SIZE_DEFAULT))
	_pool = rec.get("pool", [])
	_by_id.clear()
	for e in _pool:
		_by_id[String(e["id"])] = e
	var grade: Dictionary = doc.get("grade", {})
	_grade_thresholds = grade.get("thresholds", [])


# ──────────────────────────────────────────────────────────────── owning ──

func owned() -> Array:
	## The player's current list. Progress is the single source of truth for *which* ids are
	## owned — it is the save, and this stays a read-through rather than a cached copy so
	## the two can never disagree with each other.
	return Progress.owned_recoveries


func owns(id: String) -> bool:
	return Progress.owns_recovery(id)


func grant(id: String) -> void:
	## Take one fragment. A no-op if it is already owned, or not a real id — the draft
	## screen only ever offers ids from offer(), so the second case is a caller bug, not a
	## player action, and is worth staying silent about rather than crashing on.
	if not _by_id.has(id):
		push_warning("recoveries: grant() of unknown id %s" % id)
		return
	Progress.grant_recovery(id)


# ──────────────────────────────────────────────────────────────── offer ──

func offer(seed) -> Array:
	## `draft_size` distinct ids the player does not already own, deterministic from `seed`
	## — the same clear always offers the same three, so there is no save-scumming for a
	## better draft and a screenshot taken twice is the same screenshot. `seed` may be an
	## int or a String (an anchor id is the natural seed; hashed to an int either way).
	##
	## Returns fewer than draft_size once the pool runs low, and an empty array once every
	## effect is owned — the draft screen is responsible for saying so rather than this
	## function inventing a duplicate.
	var candidates: Array[String] = []
	for e in _pool:
		var id := String(e["id"])
		if not owns(id):
			candidates.append(id)
	if candidates.size() <= draft_size:
		return candidates

	var rng := RandomNumberGenerator.new()
	rng.seed = _seed_int(seed)
	# Fisher-Yates, seeded: deterministic for a given seed and pool state, and unbiased
	# unlike a modulo-based pick.
	for i in range(candidates.size() - 1, 0, -1):
		var j := rng.randi_range(0, i)
		var tmp: String = candidates[i]
		candidates[i] = candidates[j]
		candidates[j] = tmp
	return candidates.slice(0, draft_size)


func _seed_int(seed) -> int:
	return hash(seed) if typeof(seed) == TYPE_STRING else int(seed)


# ──────────────────────────────────────────────────────────────── pool ──

func pool_entry(id: String) -> Dictionary:
	return _by_id.get(id, {})


func pool() -> Array:
	return _pool.duplicate()


func effect_text(id: String) -> String:
	## The mechanical effect stated plainly, for the draft card — "Every emplacement draws
	## 8% less" rather than "tower_draw_mult 0.92". One place to translate the pool's data
	## shape into a sentence, so the draft screen never hand-writes a percentage.
	var e := pool_entry(id)
	if e.is_empty():
		return ""
	var value := float(e.get("value", 0.0))
	match String(e.get("effect", "")):
		"tower_draw_mult":
			return "Every emplacement draws %d%% less power." % roundi((1.0 - value) * 100.0)
		"bounty_mult":
			return "Kill bounties pay %d%% more." % roundi((value - 1.0) * 100.0)
		"starting_funds_add":
			return "+$%d on hand at the start of every anchor." % roundi(value)
		"tower_damage_mult":
			return "Every emplacement hits %d%% harder." % roundi((value - 1.0) * 100.0)
		"range_mult":
			return "Every emplacement's range is %d%% longer." % roundi((value - 1.0) * 100.0)
		"sell_refund_add":
			return "Selling refunds %d more points of what was paid." % roundi(value * 100.0)
		"lives_add":
			return "+%d lives on every anchor." % roundi(value)
		"surge_charge_mult":
			return "Threshold Surge charges %d%% faster." % roundi((value - 1.0) * 100.0)
		"veterancy_mult":
			return "Veterancy ranks arrive at %d%% of the kills." % roundi(value * 100.0)
		_:
			return ""


# ─────────────────────────────────────────────────────── typed accessors ──
#
# Multiplicative effects (identity 1.0) stack by multiplication across every owned
# recovery that carries them — two range recoveries would compound rather than add, so a
# second one is never worth exactly as much as the first. Additive effects (identity 0)
# stack by addition. The pool as authored carries at most one recovery per effect, so today
# every accessor sums or multiplies across at most one term, but the accessor does not
# assume that — a future pool entry sharing an effect id costs nothing here.

func tower_draw_mult() -> float:
	## Multiplicative, identity 1.0. Every emplacement's continuous draw scales by this.
	## Call site: scripts/anchor_sim.gd `online_draw()` — `v += float(p["tower"]["draw_mw"])`
	## becomes `v += float(p["tower"]["draw_mw"]) * Recoveries.tower_draw_mult()`.
	return _mult("tower_draw_mult")


func bounty_mult() -> float:
	## Multiplicative, identity 1.0. Stacks with the difficulty bounty multiplier that
	## already lives on AnchorSim (`bounty_mult` there is per-difficulty, not this).
	## Call site: scripts/anchor_sim.gd `_damage()` —
	## `funds += int(float(u["kind"]["bounty"]) * bounty_mult)` becomes
	## `funds += int(float(u["kind"]["bounty"]) * bounty_mult * Recoveries.bounty_mult())`.
	return _mult("bounty_mult")


func starting_funds_add() -> int:
	## Additive, identity 0. Funds present at the start of every anchor, on top of the
	## anchor's own `starting_funds`.
	## Call site: scripts/anchor_sim.gd `setup()` —
	## `funds = int(anchor.get("starting_funds", 0))` becomes
	## `funds = int(anchor.get("starting_funds", 0)) + Recoveries.starting_funds_add()`.
	return _add_int("starting_funds_add")


func tower_damage_mult() -> float:
	## Multiplicative, identity 1.0. Scales every damaging emplacement's `damage`.
	##
	## Applied by `Loadout.towers()` to a deep copy of `Content.towers` before
	## `AnchorSim.setup()` sees it — not as a branch inside the sim. This slot used to hold a
	## `brownout_slope_mult`, which was cut: `BROWNOUT_SLOPE` is a constant inside
	## `scripts/anchor_sim.gd`, there is no honest way to express it as data, and that file is
	## parity-tested against `sim/engine.py`. A rule that exists in one implementation and not
	## the other is precisely what the parity harness is there to catch, so the pool now only
	## contains effects that are a change to a *field*. See `scripts/loadout.gd`'s docstring
	## and `how_effects_apply` in data/tuning.json.
	return _mult("tower_damage_mult")


func range_mult() -> float:
	## Multiplicative, identity 1.0. Every emplacement's `range` scales by this wherever
	## range is read for a rules decision.
	## Call sites: scripts/anchor_sim.gd — the placement/target-acquisition range check
	## (`var r: float = float(p["tower"]["range"])`, ~line 292) and the fire-loop range
	## check (`var rng := float(tw["range"])`, ~line 371) both need
	## `* Recoveries.range_mult()`. The HUD's displayed range in `_stats_text()`
	## (scripts/hud.gd) should show the effective value too, once this is wired.
	return _mult("range_mult")


func sell_refund_add() -> float:
	## Additive, identity 0. Adds directly to AnchorSim.SELL_REFUND's fraction.
	## Call site: scripts/anchor_sim.gd `sell()` —
	## `var refund := int(floor(float(paid) * SELL_REFUND))` becomes
	## `... * (SELL_REFUND + Recoveries.sell_refund_add())`, clamped to 1.0 by the caller.
	return _add_float("sell_refund_add")


func lives_add() -> int:
	## Additive, identity 0. Extra lives on top of the anchor's own `lives`.
	## Call site: scripts/anchor_sim.gd `setup()` —
	## `lives = int(anchor.get("lives", 10))` becomes
	## `lives = int(anchor.get("lives", 10)) + Recoveries.lives_add()`.
	return _add_int("lives_add")


func surge_charge_mult() -> float:
	## Multiplicative, identity 1.0. Scales `charge_per_leak_cost` from the `surge` entry
	## of `abilities` in data/tuning.json.
	## Call site: prospective — Threshold Surge is not wired into scripts/anchor_sim.gd yet
	## (it exists only as data in tuning.json's `abilities` block). Wherever a kill adds
	## `leak_cost * charge_per_leak_cost` to the surge meter, that becomes
	## `leak_cost * charge_per_leak_cost * Recoveries.surge_charge_mult()`.
	return _mult("surge_charge_mult")


func veterancy_mult() -> float:
	## Multiplicative, identity 1.0. Scales the `kills` threshold on each rank in
	## `veterancy.ranks` — 0.7 means a rank arrives at 70% of the kills, i.e. the threshold
	## is multiplied by this, not divided.
	## Call site: prospective — veterancy is not wired into scripts/anchor_sim.gd yet (data
	## only, in tuning.json's `veterancy` block; the note there says kills are meant to be
	## counted "on the placed record"). Wherever a placed emplacement's kill count is
	## compared against `rank["kills"]`, compare against
	## `rank["kills"] * Recoveries.veterancy_mult()` instead.
	return _mult("veterancy_mult")


func _mult(effect: String) -> float:
	var m := 1.0
	for id in owned():
		var e := pool_entry(id)
		if String(e.get("effect", "")) == effect:
			m *= float(e.get("value", 1.0))
	return m


func _add_float(effect: String) -> float:
	var v := 0.0
	for id in owned():
		var e := pool_entry(id)
		if String(e.get("effect", "")) == effect:
			v += float(e.get("value", 0.0))
	return v


func _add_int(effect: String) -> int:
	return roundi(_add_float(effect))


# ──────────────────────────────────────────────────────────────── grade ──
#
## The debrief verdict (`grade` in data/tuning.json), scored on the fraction of lives kept.
## Lives lost to a leak already scale by `leak_cost` (decision 047) before they ever reach
## here, so `lives_frac` is already the number the design note describes.

func verdict_name(lives_frac: float) -> String:
	## The first threshold (authored descending) the fraction clears, or the most lenient
	## one if it clears none of them — a clear always has at least 1 life, so in practice
	## this only ever falls through on a very large `lives` anchor with exactly 1 kept.
	for t in _grade_thresholds:
		if lives_frac >= float(t.get("lives_frac", 0.0)):
			return String(t.get("name", ""))
	if _grade_thresholds.is_empty():
		return ""
	return String(_grade_thresholds[-1].get("name", ""))


func grade_for(anchor_id: String, difficulty: String) -> Dictionary:
	## The verdict for one cleared anchor+difficulty. Empty if it was never cleared on that
	## difficulty — Content (anchor "lives") is the source of lives_started because the
	## anchor's own JSON is a floor-raiser's baseline and Recoveries.lives_add() has not
	## been wired into the sim yet (see lives_add() above); once it is, the difference is
	## already inert here — grade_for is asked "what happened", not "what could".
	if not Progress.is_cleared(anchor_id, difficulty):
		return {}
	var started := int(Content.anchor(anchor_id).get("lives", 10))
	var left := Progress.best_lives(anchor_id, difficulty)
	var frac := float(left) / maxf(float(started), 1.0)
	return {
		"anchor_id": anchor_id, "difficulty": difficulty,
		"lives_left": left, "lives_started": started, "lives_frac": frac,
		"verdict": verdict_name(frac),
	}


func best_grade_for(anchor_id: String) -> Dictionary:
	## The best verdict across every difficulty the anchor has been cleared on — what the
	## anchor-select grid badges a cleared anchor with. Empty if never cleared.
	if not Progress.is_cleared(anchor_id):
		return {}
	var best := {}
	var best_frac := -1.0
	for diff in Dictionary(Progress.cleared.get(anchor_id, {})).keys():
		var g := grade_for(anchor_id, String(diff))
		if g.is_empty():
			continue
		if float(g["lives_frac"]) > best_frac:
			best_frac = float(g["lives_frac"])
			best = g
	return best


func report() -> String:
	return "recoveries %d/%d owned, draft_size %d" % [owned().size(), _pool.size(), draft_size]
