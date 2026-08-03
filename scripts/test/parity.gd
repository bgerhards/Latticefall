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

## PLC-04. Mirrors sim/engine.py's LATTICE_SPACING — see that constant's own comment for
## why it is a binary fraction and must stay one: every candidate position is
## `index * LATTICE_SPACING`, and `built` below formats it with `%.4f` on both sides.
const LATTICE_SPACING := 0.5

## PLC-04. Mirrors sim/engine.py's LATTICE_UNSEEDED: "no emplacement on this lane yet" in
## _try_build()'s per-lane maximin scan. A plain large finite float, never INF — it is
## compared and subtracted like any other arclength, and an infinity would produce a NaN
## out of `BIG - BIG` the moment anyone reorders the expression. No real lane approaches
## it (the longest path in the 24 anchors is 37 tiles).
const LATTICE_UNSEEDED := 1.0e9

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
		# PLC-04: built ONCE per anchor, here, outside both the policy and the difficulty
		# loop — not per run and emphatically not per _try_build() iteration. Mirrors
		# sim/engine.py memoising it on the Anchor dataclass, which is the same "one build
		# per anchor" lifetime by a different mechanism (this file re-reads the anchor doc
		# per anchor id and holds no Anchor object to hang a cache on).
		var lattice := _build_candidate_lattice(anchor)
		var available: Array = []
		for tid in towers:
			if String(towers[tid].get("unlocked_at", "anchor-01")) <= String(aid):
				available.append(tid)
		available.sort()
		for policy in _policies(available):
			for diff in ["standard", "hard", "brutal"]:
				out.append(_run(anchor, towers, enemies, policy, diff, lattice))

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
		policy: Dictionary, diff: String, lattice: Dictionary) -> Dictionary:
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
		_try_build(s, policy, buildable, lattice)
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


func _overlaps(s, x: float, y: float) -> bool:
	## PLC-04. Mirrors sim/engine.py's Sim._overlaps() — the ONE per-candidate legality
	## test _try_build() still runs, because it is the only part of _placement_reason()
	## that is not anchor-static. Bounds and lane standoff were applied once, when
	## _build_candidate_lattice() built the lattice below.
	##
	## Arithmetic copied term for term from scripts/anchor_sim.gd's _placement_reason()
	## overlap branch (`<`, never `<=`: a touching footprint is legal). It subsumes the
	## old _is_occupied() — two records at the identical position are at distance 0, below
	## any positive rr — which is why that function is gone rather than kept alongside.
	var rr: float = AnchorSimScript.FOOTPRINT_RADIUS + AnchorSimScript.FOOTPRINT_RADIUS
	for p in s.placed:
		var dx: float = x - float(p["x"])
		var dy: float = y - float(p["y"])
		if dx * dx + dy * dy < rr * rr:
			return true
	return false


func _build_candidate_lattice(anchor: Dictionary) -> Dictionary:
	## PLC-04. Mirrors sim/engine.py's build_candidate_lattice() candidate for candidate and
	## in the same order — see that docstring for the filter, the ordering metric, the five
	## graded orderings behind the consumption rule, and why presence-weighted coverage
	## (sim/coverage.py) is deliberately NOT the metric. The two must agree exactly or
	## parity fails on the first wave of the first anchor.
	##
	## Returns the three structures _try_build() walks, all built ONCE per anchor:
	##   rows    [[x, y, lane, arclength], ...] ordered by (d2, arclength, x, y)
	##   groups  Array[PackedInt32Array] — row indices per lane, in that same order.
	##           Mirrors sim/engine.py's lattice_lane_groups(), which the Python side
	##           derives once per Sim; both are outside the build loop, which is all the
	##           acceptance criterion asks.
	##   at      "%.4f,%.4f" -> [lane, arclength], so a placed emplacement can be mapped
	##           back to its lane. Keyed by the SAME fixed-precision string the parity
	##           signature uses rather than by a float pair: lattice positions are binary
	##           fractions at 0.5 pitch, so the formatting is lossless and unique here, and
	##           it sidesteps float-keyed Dictionary hashing entirely.
	##
	## Reads the raw anchor document rather than an AnchorSim: the lattice is built once per
	## anchor, before any AnchorSim for it exists. Waypoints come straight from
	## `paths[i].waypoints` — the AUTHORED points, never resampled, same as
	## _placement_reason()'s `_wx`/`_wy` — and only elements 0 and 1 are read (a waypoint's
	## optional third element is the TER-01 elevation level and never enters this).
	##
	## Segment length is `abs(dx) + abs(dy)`, matching scripts/anchor_sim.gd:216 and
	## sim/content.py's Lane.build(): lanes are axis-aligned, so manhattan == euclidean
	## (decision 030) and there is no third expression for "how long is this segment".
	##
	## PackedFloat64Array, never PackedFloat32Array or Vector2: float32 is banned in the
	## rules and this feeds positions the parity signature formats with %.4f.
	var r: float = AnchorSimScript.FOOTPRINT_RADIUS
	var w: int = int(anchor["grid"]["w"])
	var h: int = int(anchor["grid"]["h"])
	var standoff: float = float(anchor.get("lane_half_width", 0.5)) + r

	var lanes_x: Array = []
	var lanes_y: Array = []
	var lanes_seg: Array = []
	for lane in anchor.get("paths", []):
		var xs := PackedFloat64Array()
		var ys := PackedFloat64Array()
		for wp in lane["waypoints"]:
			xs.append(float(wp[0]))
			ys.append(float(wp[1]))
		var seg := PackedFloat64Array()
		for i in range(xs.size() - 1):
			seg.append(abs(xs[i + 1] - xs[i]) + abs(ys[i + 1] - ys[i]))   # axis-aligned
		lanes_x.append(xs)
		lanes_y.append(ys)
		lanes_seg.append(seg)

	var scored: Array = []
	for iy in range(2 * h - 1):
		var y: float = float(iy) * LATTICE_SPACING
		for ix in range(2 * w - 1):
			var x: float = float(ix) * LATTICE_SPACING
			if not (x - r >= -0.5 and x + r <= float(w) - 0.5 and y - r >= -0.5 and y + r <= float(h) - 0.5):
				continue
			var best_d2 := 1e18
			var best_lane := 0
			var best_s := 0.0
			for li in range(lanes_x.size()):
				var wx: PackedFloat64Array = lanes_x[li]
				var wy: PackedFloat64Array = lanes_y[li]
				var sg: PackedFloat64Array = lanes_seg[li]
				var run := 0.0
				for i in range(wx.size() - 1):
					var ax: float = wx[i]
					var ay: float = wy[i]
					var bx: float = wx[i + 1]
					var by: float = wy[i + 1]
					var abx: float = bx - ax
					var aby: float = by - ay
					var ab2: float = abx * abx + aby * aby
					var t: float
					if ab2 <= 0.0:
						t = 0.0
					else:
						t = ((x - ax) * abx + (y - ay) * aby) / ab2
						t = minf(1.0, maxf(0.0, t))
					var cx: float = ax + abx * t
					var cy: float = ay + aby * t
					var dx: float = x - cx
					var dy: float = y - cy
					var d2: float = dx * dx + dy * dy
					# Strict `<`, so a tie keeps the FIRST lane and the FIRST segment that
					# reached this distance — the lower lane index, the earlier arclength.
					# minf() would have said the same about best_d2 and nothing about which
					# lane produced it, which is why this is a comparison now.
					if d2 < best_d2:
						best_d2 = d2
						best_lane = li
						best_s = run + sg[i] * t
					run += sg[i]
			if best_d2 < standoff * standoff:
				continue
			scored.append([best_d2, best_s, x, y, best_lane])
	## Sorted on the FULL (d2, arclength, x, y) tuple, never on a prefix of it:
	## Array.sort_custom() is not documented as stable in Godot 4.7 while Python's `sorted`
	## is, so leaning on stability to break a tie is exactly the intermittent divergence
	## LF-055 already cost this project once (PRD risk #3).
	scored.sort_custom(func(a, b):
		if a[0] != b[0]: return a[0] < b[0]
		if a[1] != b[1]: return a[1] < b[1]
		if a[2] != b[2]: return a[2] < b[2]
		return a[3] < b[3])

	var rows: Array = []
	var groups: Array = []
	for _li in range(lanes_x.size()):
		groups.append(PackedInt32Array())
	var at := {}
	for k in range(scored.size()):
		var row: Array = scored[k]
		rows.append([row[2], row[3], row[4], row[1]])         # x, y, lane, arclength
		groups[row[4]].append(k)
		at["%.4f,%.4f" % [row[2], row[3]]] = [row[4], row[1]]
	return {"rows": rows, "groups": groups, "at": at}


func _try_build(s, policy: Dictionary, buildable: Array, lattice: Dictionary) -> void:
	## PLC-04: NO SORT in this function, mirroring sim/engine.py's Sim._try_build(). Read
	## that docstring for the full reasoning; the mechanism, restated only as far as it
	## takes to check the two files against each other:
	##
	##   - `turn` picks WHICH LANE gets the next emplacement, one lane per placement,
	##     wrapping. A lane with no legal candidate left is skipped and `turn` has already
	##     moved past it, so a saturated lane cannot stall the loop.
	##   - `sep[i]` is candidate i's arclength distance to the nearest emplacement already
	##     standing ON ITS OWN LANE; within the chosen lane the winner maximises it. A lane
	##     with nothing on it yet has every sep at LATTICE_UNSEEDED, so its first pick is
	##     its best-(d2, arclength, x, y) candidate.
	##
	## THE MAXIMIN TIE-BREAK is the parity risk and is handled explicitly: a max-over-min
	## ties by construction (two candidates either side of one gap are equidistant from its
	## ends), so the scan accepts only a STRICT improvement, which hands every tie to the
	## first index in groups[li] — and that array is in ascending row order, i.e. in
	## (d2, arclength, x, y) order. Nothing relies on iteration order, on sort_custom being
	## stable, or on two candidates comparing equal (LF-055).
	##
	## THE `and` IS LOAD-BEARING for cost: _overlaps() is only reached by a candidate that
	## is already a strict improvement. `best_sep` advances only on an ACCEPTED candidate,
	## so an overlapping one never masks a later legal one at the same separation.
	##
	## `sep` is re-seeded from s.placed at the top of every call rather than carried across
	## waves: this runs once per wave, s.placed is the authority on what is standing, and a
	## scheduled build/sell verb (BAL-01) can have changed it without going through here.
	var rows: Array = lattice["rows"]
	var groups: Array = lattice["groups"]
	var at: Dictionary = lattice["at"]
	var n := rows.size()
	var n_lanes := groups.size()

	var sep := PackedFloat64Array()
	sep.resize(n)
	sep.fill(LATTICE_UNSEEDED)
	for p in s.placed:
		var key := "%.4f,%.4f" % [float(p["x"]), float(p["y"])]
		if not at.has(key):
			continue         # placed off-lattice by an explicit build_at() — not ours
		var seed_row: Array = at[key]
		var seed_lane: int = int(seed_row[0])
		var seed_s: float = float(seed_row[1])
		for i in groups[seed_lane]:
			var d0: float = seed_s - float(rows[i][3])
			if d0 < 0.0:
				d0 = -d0     # abs() by branch: safe-ops, and identical in Python
			if d0 < sep[i]:
				sep[i] = d0

	var turn := 0
	while true:
		# LF-152/decision 063: `effective_cap()` is `anchor.get("slots", []).size()` for
		# every anchor that omits max_emplacements (all 24 today), so an anchor's authored
		# slot COUNT still bounds how much the grader builds even though PLC-04 means its
		# authored slot POSITIONS no longer bound where. Since PLC-04 this is a real exit
		# condition rather than a restatement of the "no candidates left" one below it: a
		# lattice does not run out at a dozen emplacements the way a slot list did.
		# Mirrors sim/engine.py's Sim._try_build()'s own early check.
		if s.placed.size() >= s.effective_cap():
			return
		var cand_i := -1
		for _probe in range(n_lanes):
			var li := turn
			turn += 1
			if turn >= n_lanes:
				turn = 0
			var best_i := -1
			var best_sep := -1.0
			for i in groups[li]:
				if sep[i] > best_sep and not _overlaps(s, rows[i][0], rows[i][1]):
					best_sep = sep[i]
					best_i = i
			if best_i >= 0:
				cand_i = best_i
				break
		if cand_i < 0:
			return           # every lane is saturated
		var cand_x: float = rows[cand_i][0]
		var cand_y: float = rows[cand_i][1]
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
			# placed_one = true unconditionally. The chosen candidate does not change on
			# a refusal (PLC-04: `sep` only moves when something is actually placed), so
			# the outer while loop would never terminate: an infinite loop, meaning the
			# parity run HANGS rather than fails. Checking the return value is the whole
			# fix — a refusal now falls through to the next tower exactly like a
			# funds/caps/budget rejection already does. build_at() re-runs the full
			# _placement_reason(); it can only ever agree here, since the lattice is
			# pre-filtered on bounds and lane and `_overlaps()` above covers the third
			# test — but it stays checked rather than assumed.
			if s.build_at(tid, cand_x, cand_y):
				placed_one = true
				break
		if not placed_one:
			return
		# Fold the new emplacement into its own lane's separations. Same expression as the
		# seeding loop above, deliberately — one arithmetic form for "arclength distance",
		# not two that must be kept in step. Mirrors sim/engine.py's identical tail.
		var pl: int = int(rows[cand_i][2])
		var ps: float = float(rows[cand_i][3])
		for i in groups[pl]:
			var d: float = ps - float(rows[i][3])
			if d < 0.0:
				d = -d
			if d < sep[i]:
				sep[i] = d


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
