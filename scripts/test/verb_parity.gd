extends SceneTree
## Headless scheduled-verb parity runner (LF-244).
##
## Loads data/schema/fixtures/scheduled-verbs.json, drives scripts/anchor_sim.gd over it
## tick by tick, dispatching the fixture's verbs at named TICK INDICES, and prints one
## JSON line of everything a verb can move: funds, spend, bus load, the number of
## emplacements standing, which ticks each one fired on, and every unit's distance and
## hit points. tools/verb_parity.py drives sim/engine.py over the identical document,
## diffs the two, and checks both against arithmetic derived from the fixture.
##
## WHY THIS EXISTS. Sim._dispatch_one() accepts eight verbs and the twenty policies
## standard_policies() returns between them schedule only two -- `call_wave` and
## `ability` (surge and overcharge). So all 1,440 `rules parity` runs execute the ABSENT
## branch for target_mode, sell, upgrade, set_online, build and the shutter ability. The
## dispatch block below is mirrored from scripts/test/parity.gd's, which is where the
## GDScript side of that dispatcher lives; it has been correct and unexercised.
##
## WHY DISPATCH BY TICK AND NOT BY SCHEDULE TIME. `parity.gd` fires a schedule off
## accumulated float seconds, and that mechanism IS already covered by the two verbs
## shipped policies use. What is uncovered is the verbs themselves, so this indexes by
## tick: the two engines then dispatch on identically the same tick by construction, and
## a disagreement can only be the verb.
##
##   godot --headless --path . --script res://scripts/test/verb_parity.gd

## preload rather than the global class_name: global names come from the editor's import
## cache, which does not exist in a bare `--headless --script` run (see
## scripts/test/parity.gd and scripts/test/arc_parity.gd). AnchorSim itself may never
## reference an autoload for the same reason — see the `rules autoloads` gate check.
const AnchorSimScript := preload("res://scripts/anchor_sim.gd")

const FIXTURE := "res://data/schema/fixtures/scheduled-verbs.json"
const TUNING := "res://data/tuning.json"


func _init() -> void:
	var doc: Variant = _read_json(FIXTURE)
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("verb_parity: fixture is not a JSON object")
		quit(1)
		return
	var tuning: Variant = _read_json(TUNING)
	if typeof(tuning) != TYPE_DICTIONARY:
		push_error("verb_parity: data/tuning.json is not a JSON object")
		quit(1)
		return
	var abilities := {}
	for a in tuning.get("abilities", []):
		abilities[a["id"]] = a

	var towers := _index(doc.get("towers", []))
	var enemies := _index(doc.get("enemies", []))

	var s := AnchorSimScript.new()
	s.setup(doc["anchor"], towers, enemies, String(doc.get("difficulty", "standard")))

	for b in doc.get("builds", []):
		if not s.build_at(String(b["tower"]), float(b["x"]), float(b["y"])):
			push_error("verb_parity: build refused: %s at (%s, %s) — the fixture's own "
				% [b["tower"], b["x"], b["y"]]
				+ "geometry must satisfy _is_placeable()")
			quit(1)
			return

	# tick -> the actions and spawns due on it. Both are keyed the same way so the two
	# engines cannot disagree about ordering within a tick: spawns first, then actions,
	# then the tick itself.
	var by_tick_actions := {}
	for a in doc.get("actions", []):
		var t := int(a["tick"])
		if not by_tick_actions.has(t):
			by_tick_actions[t] = []
		by_tick_actions[t].append(a)
	var by_tick_spawns := {}
	for sp in doc.get("spawns", []):
		var t2 := int(sp["tick"])
		if not by_tick_spawns.has(t2):
			by_tick_spawns[t2] = []
		by_tick_spawns[t2].append(sp)

	var n: int = int(doc.get("ticks", 0))
	var fired: Array = []          # one "0"/"1" string per BUILD INDEX, padded on removal
	var funds: Array = []
	var spend: Array = []
	var load: Array = []
	var placed_count: Array = []
	var dists: Array = []          # per tick, one float per unit ever spawned
	var hps: Array = []
	var max_placed := 0

	for t in range(n):
		for sp in by_tick_spawns.get(t, []):
			s.spawn(String(sp["enemy"]), int(sp.get("lane", 0)))
		for a in by_tick_actions.get(t, []):
			_dispatch(s, String(a["verb"]), a.get("args", {}), abilities)

		# A shot is the only thing that RAISES `cooldown` — see arc_parity.gd's own note.
		var before: Array = []
		for p in s.placed:
			before.append(float(p["cooldown"]))
		s.tick()

		if s.placed.size() > max_placed:
			max_placed = s.placed.size()
		while fired.size() < s.placed.size():
			# A row that appears mid-run is back-filled with the ticks it did not exist
			# for, so every string stays the same length and index i means the same
			# emplacement on both sides.
			fired.append("-".repeat(t))
		for i in range(fired.size()):
			if i < s.placed.size() and i < before.size():
				fired[i] += "1" if float(s.placed[i]["cooldown"]) > float(before[i]) else "0"
			elif i < s.placed.size():
				fired[i] += "0"     # built this tick, had no `before` to compare against
			else:
				fired[i] += "-"     # sold; the row is gone but the column stays aligned

		funds.append(int(s.funds))
		spend.append(int(s.spend))
		load.append(float(s.bus_load()))
		placed_count.append(int(s.placed.size()))
		var d: Array = []
		var h: Array = []
		for u in s.units:
			d.append(float(u["dist"]))
			h.append(float(u["hp"]))
		dists.append(d)
		hps.append(h)

	# full_precision, or JSON.stringify rounds every float to Godot's short form and the
	# cross-engine comparison would be measuring the serializer.
	print("VERB_PARITY_JSON " + JSON.stringify({
		"fired": fired,
		"funds": funds,
		"spend": spend,
		"load": load,
		"placed_count": placed_count,
		"dists": dists,
		"hps": hps,
		"lives": s.lives,
		"leaks": s.leaks,
	}, "", true, true))
	quit()


func _dispatch(s: Variant, verb: String, args: Dictionary, abilities: Dictionary) -> void:
	## Mirrors scripts/test/parity.gd's `_dispatch_one()` match block verbatim, which in
	## turn mirrors Sim._dispatch_one() in sim/engine.py. Kept as a copy rather than
	## imported because parity.gd is a SceneTree script with its own fixture loading; the
	## `safe operations` gate check scans this file for exactly that reason.
	##
	## `call_wave`, and the surge and overcharge abilities, are deliberately NOT here.
	## They are the three things shipped policies already schedule, so `rules parity`
	## covers them across all 1,440 runs; and `call_wave` shortens a wave lead-in, which
	## a fixture with no waves has nothing to do with. This file covers what nothing else
	## does.
	match verb:
		"speed":
			# A deliberate no-op in the rules — the sim ticks at a fixed DT and "speed"
			# is scripts/anchor_view.gd's wall-clock pacing, a presentation concept.
			# BAL-01 required this be PROVED rather than assumed, and
			# tools/verb_parity.py's stripped-actions control run is that proof, and it
			# runs on every invocation rather than behind a flag.
			pass
		"ability":
			match String(args.get("kind", "")):
				"shutter":
					var active2: bool = bool(args.get("active", true))
					var cfg2: Dictionary = abilities.get("shutter", {})
					s.set_shutter(active2,
						float(cfg2.get("hold_tiles", 0.0)) if active2 else 0.0,
						float(cfg2.get("draw_mw", 0.0)) if active2 else 0.0)
				_:
					push_error("verb_parity fixture scheduled an out-of-scope ability %s"
						% String(args.get("kind", "")))
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


func _read_json(path: String) -> Variant:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("verb_parity: cannot open %s" % path)
		return null
	return JSON.parse_string(f.get_as_text())


func _index(rows: Array) -> Dictionary:
	var out := {}
	for r in rows:
		out[r["id"]] = r
	return out
