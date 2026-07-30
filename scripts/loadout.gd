class_name Loadout
extends RefCounted
## Applies the player's recovered fragments to a level's data, before the sim sees it.
##
## Every recovery in `data/tuning.json` is a change to a number the sim reads out of its
## inputs — an emplacement's draw, a unit's bounty, the anchor's starting funds. So this
## transforms the *data*, and `AnchorSim` is handed the result. Nothing about the rules
## changes and no branch is added to `scripts/anchor_sim.gd`.
##
## That is not a stylistic preference. `anchor_sim.gd` is a port of `sim/engine.py` and the
## two are diffed on every commit by `tools/test_parity.py`; a rule present in one and
## absent from the other means the game is not playing the level that was balanced, which
## is the single failure that harness exists to catch. Multiplying `draw_mw` inside
## `online_draw()` would have been three characters and would have made parity depend on
## nobody owning a recovery at the moment the harness happened to run. Transforming the
## inputs instead makes the sim a pure function of its data, so parity holds *by
## construction*: hand it the same dictionaries and it produces the same run, recoveries or
## not. See `how_effects_apply` in data/tuning.json for the same argument from the data side.
##
## The three effects that are NOT expressible as data — `sell_refund_add`,
## `surge_charge_mult`, `veterancy_mult` — are safe to read directly at their call sites for
## the opposite reason: selling, the bindstone abilities and veterancy are GDScript-only
## systems that `sim/engine.py` does not model at all, the precedent decision 033 set.
##
## Everything here returns **deep copies**. `Content.towers` is a shared autoload dictionary
## handed to every board in the session; scaling a tower's draw in place would apply the
## player's recovery to the anchor-select preview, to the next anchor, and to the numbers the
## inspector reads — the same class of bug `AnchorSim.upgrade()` avoids by merging into a
## duplicate rather than mutating the shared definition.


static func towers(source: Dictionary) -> Dictionary:
	## `Content.towers` with draw, range and damage scaled by what the player is carrying.
	##
	## The `upgrade` block is scaled too. An upgraded emplacement replaces its own stats with
	## the merged upgrade values (see AnchorSim.upgrade()), so a recovery that skipped the
	## upgrade block would silently stop applying the moment the player upgraded — the buff
	## would appear to be *lost* by improving the gun, which is the opposite of what both
	## systems promise.
	var draw_mult := Recoveries.tower_draw_mult()
	var range_mult := Recoveries.range_mult()
	var damage_mult := Recoveries.tower_damage_mult()
	if is_equal_approx(draw_mult, 1.0) and is_equal_approx(range_mult, 1.0) \
			and is_equal_approx(damage_mult, 1.0):
		return source
	var out: Dictionary = {}
	for id in source:
		var tw: Dictionary = (source[id] as Dictionary).duplicate(true)
		_scale_tower(tw, draw_mult, range_mult, damage_mult)
		if tw.has("upgrade"):
			_scale_tower(tw["upgrade"], draw_mult, range_mult, damage_mult)
		out[id] = tw
	return out


static func _scale_tower(tw: Dictionary, draw_mult: float, range_mult: float,
		damage_mult: float) -> void:
	## Draw stays an integer because the HUD prints it as one and the reactor readout is the
	## number the whole game is about — "12.4 MW" in a column of whole numbers reads as a bug.
	## Rounded, not truncated, so an 8% reduction on 12 MW is 11 and not 11 by accident.
	if tw.has("draw_mw"):
		tw["draw_mw"] = maxi(1, roundi(float(tw["draw_mw"]) * draw_mult))
	if tw.has("range"):
		tw["range"] = float(tw["range"]) * range_mult
	# Support emplacements carry damage 0 and must keep it: scaling zero is still zero, but
	# being explicit stops a future non-zero default from arming the shield wall.
	if tw.has("damage") and float(tw["damage"]) > 0.0:
		tw["damage"] = float(tw["damage"]) * damage_mult


static func enemies(source: Dictionary) -> Dictionary:
	## `Content.enemies` with bounty scaled. Bounty is the only enemy field a recovery
	## touches — deliberately. Scaling hp or speed would move the balance the anchors were
	## graded against; scaling what a kill pays moves the player's economy, which is what
	## meta-progression is allowed to do.
	var mult := Recoveries.bounty_mult()
	if is_equal_approx(mult, 1.0):
		return source
	var out: Dictionary = {}
	for id in source:
		var e: Dictionary = (source[id] as Dictionary).duplicate(true)
		if e.has("bounty"):
			e["bounty"] = maxi(1, roundi(float(e["bounty"]) * mult))
		out[id] = e
	return out


static func anchor(source: Dictionary) -> Dictionary:
	## The level with starting funds and lives raised by what the player is carrying.
	##
	## `lives` is additive and `starting_funds` is additive for the same reason: both are
	## compared against a per-anchor budget the player can reason about — lives against the
	## wave's total leak_cost, funds against the price of one emplacement — and a percentage
	## of a quantity that ranges from 10 to 52 across the campaign is not a promise anyone can
	## hold in their head.
	var funds_add := Recoveries.starting_funds_add()
	var lives_add := Recoveries.lives_add()
	if funds_add == 0 and lives_add == 0:
		return source
	var out: Dictionary = source.duplicate(true)
	out["starting_funds"] = int(out.get("starting_funds", 0)) + funds_add
	out["lives"] = int(out.get("lives", 10)) + lives_add
	return out
