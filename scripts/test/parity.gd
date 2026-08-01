extends SceneTree
## Headless parity runner.
##
## Replays an anchor through AnchorSim using the same deterministic build policies as
## sim/engine.py, and prints one JSON line per run. tools/test_parity.py runs the Python
## sim over the same inputs and diffs the two.
##
## This is the only thing preventing the GDScript port and the Python reference from
## quietly drifting apart, which would mean the game is not playing the level that was
## balanced and signed off.
##
##   godot --headless --path . --script res://scripts/test/parity.gd
##   godot --headless --path . --script res://scripts/test/parity.gd -- --anchor anchor-01

## preload rather than the global class_name: global names come from the editor's
## import cache, which does not exist in a bare `--headless --script` run.
const AnchorSimScript := preload("res://scripts/anchor_sim.gd")

const MAX_SIM_SECONDS := 3600.0

# BAL-01: data/tuning.json, loaded once in _init() and read by _dispatch_one()/_run()
# below. No autoload reference anywhere in this file needs these — see the
# `rules autoloads` gate check (decision 054/061) for why that distinction matters here.
var _tuning_abilities: Dictionary = {}
var _tuning_veterancy_ranks: Array = []
var _tuning_call_bonus_per_sec: float = 0.0


func _init() -> void:
	var anchor_filter := ""
	var argv := OS.get_cmdline_user_args()
	for i in range(argv.size()):
		if argv[i] == "--anchor" and i + 1 < argv.size():
			anchor_filter = argv[i + 1]

	var towers := _index(_read("res://data/towers.json").get("towers", []))
	var enemies := _index(_read("res://data/enemies.json").get("enemies", []))
	# BAL-01: abilities/veterancy/pacing, read once — _dispatch_one() and the veterancy
	# opt-in both read this. Mirrors sim/content.py's load_tuning(). Abilities keyed by
	# id the same way towers/enemies already are.
	var tuning_doc := _read("res://data/tuning.json")
	_tuning_abilities = _index(tuning_doc.get("abilities", []))
	_tuning_veterancy_ranks = Array(tuning_doc.get("veterancy", {}).get("ranks", []))
	_tuning_call_bonus_per_sec = float(tuning_doc.get("pacing", {}).get("call_bonus_per_sec", 0.0))

	var anchor_ids: Array = []
	if anchor_filter != "":
		anchor_ids.append(anchor_filter)
	else:
		var d := DirAccess.open("res://data/anchors")
		if d:
			for f in d.get_files():
				if f.begins_with("anchor-") and f.ends_with(".json"):
					anchor_ids.append(f.replace(".json", ""))
		anchor_ids.sort()

	var out: Array = []
	for aid in anchor_ids:
		var anchor := _read("res://data/anchors/%s.json" % aid)
		if anchor.is_empty():
			continue
		var available: Array = []
		for tid in towers:
			if String(towers[tid].get("unlocked_at", "anchor-01")) <= String(aid):
				available.append(tid)
		available.sort()
		for policy in _policies(available):
			for diff in ["standard", "hard", "brutal"]:
				out.append(_run(anchor, towers, enemies, policy, diff))

	print("PARITY_JSON " + JSON.stringify(out))
	quit()


# ─────────────────────────────────────────────────────────────── policies ──

func _policies(ids: Array) -> Array:
	## Mirrors standard_policies() in sim/engine.py, including order.
	var mk := func(name: String, first: Array, overdraw: bool, caps: Dictionary,
			reserve: float = 0.0) -> Dictionary:
		var pref: Array = []
		for i in first:
			if ids.has(i):
				pref.append(i)
		for i in ids:
			if not pref.has(i):
				pref.append(i)
		return {"name": name, "pref": pref, "overdraw": overdraw, "caps": caps,
			"reserve": reserve}
	## Mirrors Policy.capped_core() in sim/engine.py (LF-095/BAL-02). Unlike mk() above,
	## this does NOT append the rest of `ids` after the named towers — `pref` is closed,
	## so _try_build()'s `buildable` (built straight from `pref`, see _run() below) can
	## only ever place the core tower and its fill, never fall through to the wider
	## catalog. `caps` gets the core's count merged in automatically, same as the
	## Python side's `self.caps.setdefault(core_id, core_count)`.
	var mk_core := func(name: String, core_id: String, core_count: int, fill: Array,
			extra_caps: Dictionary, reserve: float = 0.0) -> Dictionary:
		var pref: Array = []
		if ids.has(core_id):
			pref.append(core_id)
		for i in fill:
			if ids.has(i) and not pref.has(i):
				pref.append(i)
		var caps: Dictionary = extra_caps.duplicate()
		if not caps.has(core_id):
			caps[core_id] = core_count
		return {"name": name, "pref": pref, "overdraw": false, "caps": caps,
			"reserve": reserve}
	## BAL-01. `schedule` is `[[time, verb, args], ...]`, already sorted at authoring time
	## here — see _run()'s own dispatch loop for why the (time, index) total order still
	## has to be explicit rather than relied on as "the array happens to be in order":
	## Array.sort_custom() is not documented as a stable sort in Godot 4.7, unlike
	## Python's sort, so the two engines proving they agree on tie order is a real claim,
	## not a coincidence of one runtime's implementation detail.
	var mk_scheduled := func(name: String, first: Array, schedule: Array) -> Dictionary:
		var pref: Array = []
		for i in first:
			if ids.has(i):
				pref.append(i)
		for i in ids:
			if not pref.has(i):
				pref.append(i)
		var indexed: Array = []
		for i in range(schedule.size()):
			indexed.append([schedule[i][0], i, schedule[i]])
		indexed.sort_custom(func(a, b):
			if a[0] != b[0]: return a[0] < b[0]
			return a[1] < b[1])
		var sorted_schedule: Array = []
		for row in indexed:
			sorted_schedule.append(row[2])
		return {"name": name, "pref": pref, "overdraw": false, "caps": {}, "reserve": 0.0,
			"schedule": sorted_schedule}
	## BAL-01. Not a schedule — veterancy is an unconditional rule, not a scheduled
	## action (see sim/engine.py's Policy.__init__ comment). `veterancy: true` is driven
	## in _run() by calling s.set_veterancy_ranks(), the EXISTING method
	## scripts/anchor_sim.gd has always had — nothing new needed there at all.
	var mk_veterancy := func(name: String, first: Array) -> Dictionary:
		var pref: Array = []
		for i in first:
			if ids.has(i):
				pref.append(i)
		for i in ids:
			if not pref.has(i):
				pref.append(i)
		return {"name": name, "pref": pref, "overdraw": false, "caps": {}, "reserve": 0.0,
			"veterancy": true}
	return [
		mk.call("cheap-mass", ["pulse-turret"], false, {}),
		mk.call("burst", ["ion-lance", "pulse-turret"], false, {}),
		mk.call("rapid", ["arc-node", "pulse-turret"], false, {}),
		mk.call("control", ["shield-wall", "pulse-turret"], false,
			{"shield-wall": 2, "scan-relay": 1}),
		mk.call("intel-first", ["scan-relay", "pulse-turret"], false,
			{"scan-relay": 1, "shield-wall": 1}),
		mk.call("screened", ["scan-relay", "pulse-turret"], false,
			{"scan-relay": 2, "shield-wall": 1}),
		mk.call("greedy-overdraw", ["ion-lance", "arc-node"], true, {}),
		mk.call("suppression", ["anchor-damper", "pulse-turret"], false,
			{"anchor-damper": 2, "scan-relay": 1, "shield-wall": 1}, 0.20),
		mk.call("flak-screen", ["flak-array", "scan-relay"], false,
			{"scan-relay": 1, "anchor-damper": 1}, 0.15),
		mk.call("reserved-mass", ["pulse-turret", "scan-relay"], false,
			{"scan-relay": 1, "shield-wall": 1, "anchor-damper": 1}, 0.30),
		mk.call("anti-armour", ["ion-lance", "mortar-emplacement", "pulse-turret"], false,
			{"scan-relay": 1, "anchor-damper": 1}, 0.20),
		mk.call("restore-first", ["restorer", "pulse-turret"], false,
			{"restorer": 2, "scan-relay": 1, "anchor-damper": 1}, 0.10),
		# Capped-core (LF-095/BAL-02). Claims mirrored verbatim from standard_policies()
		# in sim/engine.py; see that file for the reasoning behind each.
		mk_core.call("lance-core", "ion-lance", 2, ["pulse-turret"], {}, 0.20),
		mk_core.call("mortar-core", "mortar-emplacement", 2, ["flak-array"], {}, 0.20),
		mk_core.call("damper-core", "anchor-damper", 1, ["pulse-turret"], {}, 0.15),
		mk_core.call("restorer-core", "restorer", 1, ["pulse-turret"], {}, 0.10),

		# ── BAL-01: scheduled / opted-in policies ───────────────────────────────
		# Every policy above leaves `schedule`/`veterancy` absent, which _run()'s
		# `.get(..., [])`/`.get(..., false)` reads identically to the Python side's
		# defaults — see the report for the byte-identical proof this makes possible.

		# Mirrors sim/engine.py's "call-early": converts wave 1's entire lead-in to
		# funds on the very first tick.
		mk_scheduled.call("call-early", ["pulse-turret"], [[0.0, "call_wave", {}]]),

		# Mirrors sim/engine.py's "surge-on-peak": fires Threshold Surge on a fixed
		# cadence for the whole run — the deliberately generous instrument BAL-01 asks
		# for to surface whether the ability is overpowered.
		mk_scheduled.call("surge-on-peak", ["pulse-turret"], _surge_schedule()),

		# Mirrors sim/engine.py's "overcharge-greedy" — see _overcharge_schedule()'s
		# own doc for the deliberate same-timestamp order-test pair at its front.
		mk_scheduled.call("overcharge-greedy", ["ion-lance", "pulse-turret"],
			_overcharge_schedule()),

		# Mirrors sim/engine.py's "veteran-crews".
		mk_veterancy.call("veteran-crews", ["pulse-turret", "ion-lance"]),
	]


func _surge_schedule() -> Array:
	## Mirrors sim/engine.py's `surge-on-peak` schedule construction exactly (same
	## literals: 15.0 start, 40.0 spacing, 20 entries).
	var sched: Array = []
	for i in range(20):
		sched.append([15.0 + float(i) * 40.0, "ability", {"kind": "surge"}])
	return sched


func _overcharge_schedule() -> Array:
	## Mirrors sim/engine.py's `_overcharge_schedule()` exactly, including the
	## deliberate same-timestamp pair at t=5.0 (False authored before True) that is
	## the acceptance criterion's same-timestamp-order proof — see that function's
	## docstring for the full reasoning and the 42.0/7.0 tuning.json coupling note.
	var sched: Array = [
		[5.0, "ability", {"kind": "overcharge", "active": false}],
		[5.0, "ability", {"kind": "overcharge", "active": true}],
	]
	for i in range(1, 16):
		var on_t: float = 5.0 + float(i) * 42.0
		sched.append([on_t, "ability", {"kind": "overcharge", "active": true}])
		sched.append([on_t + 7.0, "ability", {"kind": "overcharge", "active": false}])
	sched.append([5.0 + 7.0, "ability", {"kind": "overcharge", "active": false}])
	return sched


func _rank(policy: Dictionary, tower_id: String) -> int:
	var i: int = policy["pref"].find(tower_id)
	return i if i >= 0 else 99


# ─────────────────────────────────────────────────── BAL-01: schedule dispatch ──

func _dispatch_schedule(s, sched: Array, sched_i: int, charge_state: Dictionary) -> Array:
	## Mirrors sim/engine.py's Sim._dispatch_schedule(). Returns [new_sched_i,
	## call_wave_requested] rather than mutating anything by reference — GDScript has
	## no out-param for a plain int, and AnchorSim itself has no schedule state of its
	## own for this to live on (see _run()'s own comment on why the driver holds it).
	## `sched` is already sorted on a total (time, index) order by _policies()'
	## mk_scheduled(), so draining it in list order from `sched_i` onward IS draining
	## it in that total order. `charge_state` (LF-163) is passed through unchanged to
	## _dispatch_one() — see that function's own doc.
	var call_wave_requested := false
	var i := sched_i
	while i < sched.size() and float(sched[i][0]) <= s.t + 1e-9:
		var item: Array = sched[i]
		i += 1
		if _dispatch_one(s, String(item[1]), item[2], charge_state):
			call_wave_requested = true
	return [i, call_wave_requested]


func _dispatch_one(s, verb: String, args: Dictionary, charge_state: Dictionary) -> bool:
	## Mirrors sim/engine.py's Sim._dispatch_one(). Returns true only for "call_wave" —
	## the one verb whose effect the CALLER (_run()'s lead-in loop) has to finish, the
	## same way Sim._dispatch_one() only sets a flag for _advance() to act on.
	## `charge_state` (LF-163) is a Dictionary the caller owns — GDScript has no float
	## out-param — kept fed by the `unit_killed` signal connected in _run() below,
	## mirroring scripts/abilities.gd's AbilityState.charge.
	match verb:
		"speed":
			# No-op on outcomes, deliberately — see sim/engine.py's own docstring on
			# this verb for why the headless sim has nothing for "speed" to multiply.
			pass
		"call_wave":
			return true
		"ability":
			match String(args.get("kind", "")):
				"surge":
					# LF-163: charge-gated, mirroring scripts/abilities.gd's
					# AbilityState.ready()/began() — a scheduled cast whose time has
					# come but which has not earned a full charge from kills yet is
					# consumed (drained from the schedule) but does nothing, same as
					# scripts/anchor_view.gd's activate_ability() returning {} and
					# playing a deny cue on a not-ready press. Charge resets to 0 on
					# an actual cast.
					var cfg: Dictionary = _tuning_abilities.get("surge", {})
					var cmax: float = float(cfg.get("charge_max", 0.0))
					if cmax <= 0.0 or float(charge_state.get("surge", 0.0)) >= cmax:
						s.fire_surge(cfg)
						charge_state["surge"] = 0.0
				"overcharge":
					var active: bool = bool(args.get("active", true))
					var cfg: Dictionary = _tuning_abilities.get("overcharge", {})
					s.set_overcharge(active,
						float(cfg.get("fire_rate_bonus", 0.0)) if active else 0.0,
						float(cfg.get("draw_mult", 1.0)) if active else 1.0)
				"shutter":
					var active2: bool = bool(args.get("active", true))
					var cfg2: Dictionary = _tuning_abilities.get("shutter", {})
					s.set_shutter(active2,
						float(cfg2.get("hold_tiles", 0.0)) if active2 else 0.0,
						float(cfg2.get("draw_mw", 0.0)) if active2 else 0.0)
		"target_mode":
			var idx := int(args["index"])
			if idx >= 0 and idx < s.placed.size():
				s.placed[idx]["target_mode"] = String(args.get("mode", "first"))
		"sell":
			s.sell(int(args["index"]))
		"upgrade":
			s.upgrade(int(args["index"]))
		"set_online":
			s.set_online(int(args["index"]), bool(args.get("on", true)))
		"build":
			var slot: Array = args["slot"]
			s.build_at(String(args["tower"]), float(slot[0]), float(slot[1]))
		_:
			push_error("unknown scheduled verb %s" % verb)
	return false


# ────────────────────────────────────────────────────────────────── run ──

func _run(anchor: Dictionary, towers: Dictionary, enemies: Dictionary,
		policy: Dictionary, diff: String) -> Dictionary:
	var s := AnchorSimScript.new()
	s.setup(anchor, towers, enemies, diff)

	# BAL-01: veterancy is opted into once, at setup — an unconditional rule, not a
	# scheduled action (see sim/engine.py's Policy.__init__ comment). Every existing
	# policy has no "veterancy" key at all, so this is a no-op for them: AnchorSim's
	# own `_veterancy_ranks` defaults empty, exactly as before this verb existed.
	if bool(policy.get("veterancy", false)):
		s.set_veterancy_ranks(_tuning_veterancy_ranks)

	var buildable: Array = policy["pref"].duplicate()
	# (rank, id) — id breaks the tie between everything the policy does not rank, which
	# sort_custom would otherwise resolve arbitrarily. Mirrors Sim.buildable.
	buildable.sort_custom(func(a, b):
		var ra := _rank(policy, a)
		var rb := _rank(policy, b)
		return ra < rb if ra != rb else a < b)

	var waves_cleared := 0
	var died_on: int = -1

	# BAL-01: mirrors sim/engine.py's Sim._schedule_i / Sim._call_wave_requested —
	# AnchorSim itself has no notion of a policy or a schedule (a played match has a
	# live player instead), so this driver holds the dispatch position and the
	# call_wave flag the same way Sim holds them as instance fields, persisting across
	# the WHOLE run rather than being reset per wave.
	var sched: Array = policy.get("schedule", [])
	var sched_i := 0

	# LF-163: Threshold Surge's charge, kept in a Dictionary (GDScript has no float
	# out-param) so _dispatch_one() can both read and reset it. Fed by the SAME
	# `unit_killed` signal real play's AbilityState listens to via
	# scripts/anchor_view.gd's `_charge_surge()` — AnchorSim emits it from both
	# `_damage()` and `fire_surge()` itself, so a scheduled cast earns back charge from
	# its own kills exactly as a live one does. Connected unconditionally; harmless
	# when charge_max <= 0 since _dispatch_one() never reads charge_state in that case.
	var charge_state := {"surge": 0.0}
	var surge_cfg: Dictionary = _tuning_abilities.get("surge", {})
	var surge_cmax: float = float(surge_cfg.get("charge_max", 0.0))
	var surge_per_leak: float = float(surge_cfg.get("charge_per_leak_cost", 0.0))
	if surge_cmax > 0.0 and surge_per_leak > 0.0:
		s.unit_killed.connect(func(u: Dictionary) -> void:
			var leak_cost := int(u["kind"].get("leak_cost", 1))
			charge_state["surge"] = minf(surge_cmax,
				float(charge_state.get("surge", 0.0)) + float(leak_cost) * surge_per_leak))

	for wi in range(anchor["waves"].size()):
		s.begin_wave(wi)
		_try_build(s, policy, buildable)
		_shed(s, policy)
		var lead := float(anchor["waves"][wi].get("lead_in", 20.0))
		var lead_ticks := int(lead / AnchorSimScript.DT)
		# BAL-01: call_wave can only meaningfully end THIS lead-in — this loop is the
		# only place that knows both "how many ticks are left in it" and "is this a
		# lead-in at all" (the combat while-loop below never acts on the flag it gets
		# back). Mirrors sim/engine.py's _advance(), the one caller that ever consumes
		# _call_wave_requested, for the identical reason.
		for li in range(lead_ticks):
			var pen := s.begin_tick()
			var r: Array = _dispatch_schedule(s, sched, sched_i, charge_state)
			sched_i = int(r[0])
			if bool(r[1]):
				var remaining: float = float(lead_ticks - li - 1) * AnchorSimScript.DT
				s.funds += int(floor(_tuning_call_bonus_per_sec * maxf(0.0, remaining) + 0.5))
				s.end_tick(pen)
				break
			s.end_tick(pen)

		var q: Array = s.wave_queue(wi)
		var wave_t := 0.0
		var qi := 0
		while true:
			while qi < q.size() and float(q[qi][0]) <= wave_t + 1e-9:
				s.spawn(String(q[qi][2]), int(q[qi][1]))
				qi += 1
			var pen2 := s.begin_tick()
			var r2 := _dispatch_schedule(s, sched, sched_i, charge_state)
			sched_i = r2[0]
			# call_wave firing mid-combat is never consumed here (only the lead-in loop
			# above checks the flag) — mirrors sim/engine.py's _advance() being the only
			# caller that ever reads _call_wave_requested. A stray fire here is silently
			# absorbed, same as on the Python side.
			s.end_tick(pen2)
			wave_t += AnchorSimScript.DT
			if s.lives <= 0:
				died_on = wi + 1
				break
			if qi >= q.size() and not s.any_alive():
				break
			if s.t > MAX_SIM_SECONDS:
				died_on = wi + 1
				break
		s.prune_dead()
		if died_on >= 0:
			break
		waves_cleared += 1

	# PLC-01: fixed-precision, not `%d` — positions are floats now, and this string must
	# match sim/engine.py's f"{p.x:.4f},{p.y:.4f}" bit-for-bit, or a formatting difference
	# reads as an 864-run parity failure that looks like a rules divergence. See
	# sim/engine.py's Outcome construction for the same comment.
	var built: Array = []
	for p in s.placed:
		built.append("%s@%.4f,%.4f" % [p["tower"]["id"], float(p["x"]), float(p["y"])])

	return {
		"anchor": anchor["id"],
		"difficulty": diff,
		"policy": policy["name"],
		"won": died_on < 0 and s.lives > 0,
		"waves_cleared": waves_cleared,
		"died_on_wave": died_on if died_on >= 0 else null,
		"lives_left": maxi(0, s.lives),
		"leaks": s.leaks,
		"peak_load_mw": snappedf(s.peak_load, 0.001),
		"spend": s.spend,
		"built": built,
	}


func _is_occupied(s, x: float, y: float) -> bool:
	## Mirrors sim/engine.py's Sim._occupied() / scripts/anchor_sim.gd's _occupied().
	## PLC-01: there is no free-list on `s` any more — occupancy is derived from `s.placed`
	## directly wherever a caller external to AnchorSim needs it, same as the rules files.
	for p in s.placed:
		if float(p["x"]) == x and float(p["y"]) == y:
			return true
	return false


func _slot_priority(s) -> Array:
	## Same metric as engine.py: distance from the slot to the nearest sampled point on
	## ANY lane, sampled at the same resolution so both pick the same slot. Squared
	## distances in float64, matching Sim._slot_priority(). Decision 030. WAR-01: every
	## lane is sampled and the minimum kept — a slot near just one of several lanes is
	## still worth a slot, so "nearest the path" means "nearest the nearest lane".
	## PLC-01: no `s.free_slots` to read any more — the available positions are the
	## anchor's authored `slots` filtered to those `_is_occupied()` says nothing already
	## sits on, recomputed here the same way sim/engine.py's Sim._slot_priority()
	## recomputes it from `self.a.slots` every call. Returns `[x, y]` pairs, never a
	## Vector2/Vector2i (float32, banned in the rules).
	var scored: Array = []
	for raw in s.anchor.get("slots", []):
		var sx: float = float(raw[0])
		var sy: float = float(raw[1])
		if _is_occupied(s, sx, sy):
			continue
		var best := 1e18
		for lane in range(s.path_length.size()):
			var plen: float = s.path_length[lane]
			var steps: int = maxi(2, int(plen))
			for i in range(steps + 1):
				var p: PackedFloat64Array = s.point_at_xy(lane, plen * float(i) / float(steps))
				var dx: float = p[0] - sx
				var dy: float = p[1] - sy
				best = minf(best, dx * dx + dy * dy)
		scored.append([best, sx, sy])
	scored.sort_custom(func(a, b):
		if a[0] != b[0]: return a[0] < b[0]
		if a[1] != b[1]: return a[1] < b[1]
		return a[2] < b[2])
	var out: Array = []
	for row in scored:
		out.append([row[1], row[2]])
	return out


func _try_build(s, policy: Dictionary, buildable: Array) -> void:
	while true:
		# LF-152/decision 063: provably a no-op for every anchor that omits
		# max_emplacements (all 24 today) — `effective_cap()` is `anchor.get("slots",
		# []).size()` in that case (PLC-01: there is no `free_slots` list left to test
		# directly), so this and the `if order.is_empty(): return` just below it are the
		# same exit condition restated. Mirrors sim/engine.py's Sim._try_build()'s own
		# early check.
		if s.placed.size() >= s.effective_cap():
			return
		var order := _slot_priority(s)
		if order.is_empty():
			return
		var placed_one := false
		for tid in buildable:
			var tw: Dictionary = s.towers[tid]
			if int(tw["cost"]) > s.funds:
				continue
			# Mirrors Policy.caps in sim/engine.py — see the note there.
			if policy["caps"].has(tid):
				var built := 0
				for p in s.placed:
					if String(p["tower"]["id"]) == tid:
						built += 1
				if built >= int(policy["caps"][tid]):
					continue
			var projected: float = s.online_draw() + float(tw["draw_mw"])
			# Mirrors Policy.reserve in sim/engine.py: headroom left for enemy drain.
			var budget: float = s.capacity() * (1.0 - float(policy.get("reserve", 0.0)))
			if not policy["overdraw"] and projected > budget:
				continue
			# LF-152: the trap decision 063 named — build_at() can now REFUSE (the cap
			# check above), and this call ignored its return value entirely, setting
			# placed_one = true unconditionally. PLC-01: `order` never shrinks on a
			# refusal either (it is recomputed fresh next iteration from `s.placed`), so
			# the outer while loop would never terminate: an infinite loop, meaning the
			# parity run HANGS rather than fails. Checking the return value is the whole
			# fix — a refusal now falls through to the next candidate exactly like a
			# funds/caps/budget rejection already does.
			if s.build_at(tid, order[0][0], order[0][1]):
				placed_one = true
				break
		if not placed_one:
			return


func _shed(s, policy: Dictionary) -> void:
	if policy["overdraw"]:
		return
	while s.online_draw() > s.capacity():
		var worst := -1
		var worst_key := [-1, -1.0]
		for i in range(s.placed.size()):
			if not s.placed[i]["online"]:
				continue
			var k := [_rank(policy, String(s.placed[i]["tower"]["id"])),
					float(s.placed[i]["tower"]["draw_mw"])]
			if worst < 0 or k[0] > worst_key[0] or (k[0] == worst_key[0] and k[1] > worst_key[1]):
				worst = i
				worst_key = k
		if worst < 0:
			return
		# set_online(), not a direct write to s.placed[worst]["online"] -- LF-099
		# gave AnchorSim.capacity() a pre-filtered restore list that is only kept
		# fresh by set_online()/build_at()/sell()/upgrade() rebuilding it eagerly;
		# writing the Dictionary key directly from outside the class bypasses that
		# and capacity() then reads a stale list. Mirrors Sim._shed_load() in
		# sim/engine.py, which is the one caller of the equivalent Python path and
		# is itself a method of Sim, so it cannot make this mistake.
		s.set_online(worst, false)


# ───────────────────────────────────────────────────────────────── util ──

func _read(path: String) -> Dictionary:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("cannot open %s" % path)
		return {}
	var v: Variant = JSON.parse_string(f.get_as_text())
	return v if typeof(v) == TYPE_DICTIONARY else {}


func _index(rows: Array) -> Dictionary:
	var out := {}
	for r in rows:
		out[r["id"]] = r
	return out
