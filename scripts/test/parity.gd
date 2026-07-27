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


func _init() -> void:
	var anchor_filter := ""
	var argv := OS.get_cmdline_user_args()
	for i in range(argv.size()):
		if argv[i] == "--anchor" and i + 1 < argv.size():
			anchor_filter = argv[i + 1]

	var towers := _index(_read("res://data/towers.json").get("towers", []))
	var enemies := _index(_read("res://data/enemies.json").get("enemies", []))

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
	]


func _rank(policy: Dictionary, tower_id: String) -> int:
	var i: int = policy["pref"].find(tower_id)
	return i if i >= 0 else 99


# ────────────────────────────────────────────────────────────────── run ──

func _run(anchor: Dictionary, towers: Dictionary, enemies: Dictionary,
		policy: Dictionary, diff: String) -> Dictionary:
	var s := AnchorSimScript.new()
	s.setup(anchor, towers, enemies, diff)

	var buildable: Array = policy["pref"].duplicate()
	# (rank, id) — id breaks the tie between everything the policy does not rank, which
	# sort_custom would otherwise resolve arbitrarily. Mirrors Sim.buildable.
	buildable.sort_custom(func(a, b):
		var ra := _rank(policy, a)
		var rb := _rank(policy, b)
		return ra < rb if ra != rb else a < b)

	var waves_cleared := 0
	var died_on: int = -1

	for wi in range(anchor["waves"].size()):
		_try_build(s, policy, buildable)
		_shed(s, policy)
		var lead := float(anchor["waves"][wi].get("lead_in", 20.0))
		for _i in range(int(lead / AnchorSimScript.DT)):
			s.tick()

		var q: Array = s.wave_queue(wi)
		var wave_t := 0.0
		var qi := 0
		while true:
			while qi < q.size() and float(q[qi][0]) <= wave_t + 1e-9:
				s.spawn(String(q[qi][1]))
				qi += 1
			s.tick()
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

	var built: Array = []
	for p in s.placed:
		built.append("%s@%d,%d" % [p["tower"]["id"], p["slot"].x, p["slot"].y])

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


func _slot_priority(s) -> Array:
	## Same metric as engine.py: distance from the slot to the nearest sampled point
	## on the path, sampled at the same resolution so both pick the same slot.
	## Squared distances in float64, matching Sim._slot_priority(). Decision 030.
	var steps: int = maxi(2, int(s.path_length))
	var scored: Array = []
	for slot in s.free_slots:
		var best := 1e18
		for i in range(steps + 1):
			var p: PackedFloat64Array = s.point_at_xy(s.path_length * float(i) / float(steps))
			var dx: float = p[0] - float(slot.x)
			var dy: float = p[1] - float(slot.y)
			best = minf(best, dx * dx + dy * dy)
		scored.append([best, slot.x, slot.y, slot])
	scored.sort_custom(func(a, b):
		if a[0] != b[0]: return a[0] < b[0]
		if a[1] != b[1]: return a[1] < b[1]
		return a[2] < b[2])
	var out: Array = []
	for row in scored:
		out.append(row[3])
	return out


func _try_build(s, policy: Dictionary, buildable: Array) -> void:
	while s.free_slots.size() > 0:
		var order := _slot_priority(s)
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
			s.build_at(tid, order[0])
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
		s.placed[worst]["online"] = false


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
