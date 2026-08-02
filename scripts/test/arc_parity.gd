extends SceneTree
## Headless firing-arc parity runner (PLC-03).
##
## Loads data/schema/fixtures/firing-arc.json, drives scripts/anchor_sim.gd over it tick
## by tick, and prints one JSON line: for every emplacement, the string of ticks it fired
## on, plus the unit's distance along the lane at each of them. tools/arc_parity.py drives
## sim/engine.py over the identical document and diffs the two, then checks each firing
## window against the fixture's own analytic bounds.
##
## Why the fire pattern and not the arc predicate itself: "in arc" has no public accessor
## in either engine, and giving it one would mean the rule existed twice in one file —
## exactly the drift this project spends nine minutes a run guarding against. Whether an
## emplacement FIRES on a given tick is the observable the arc decides, it is an integer
## signal that compares exactly across two languages, and it goes through the real
## _select_target() in both. The fixture's probe walker has a million hit points so it
## survives the whole run and the pattern is never a statement about a kill.
##
##   godot --headless --path . --script res://scripts/test/arc_parity.gd

## preload rather than the global class_name: global names come from the editor's import
## cache, which does not exist in a bare `--headless --script` run (see
## scripts/test/parity.gd). AnchorSim itself may never reference an autoload for the same
## reason — see the `rules autoloads` gate check.
const AnchorSimScript := preload("res://scripts/anchor_sim.gd")

const FIXTURE := "res://data/schema/fixtures/firing-arc.json"


func _init() -> void:
	var f := FileAccess.open(FIXTURE, FileAccess.READ)
	if f == null:
		push_error("arc_parity: cannot open %s" % FIXTURE)
		quit(1)
		return
	var doc: Variant = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("arc_parity: fixture is not a JSON object")
		quit(1)
		return

	var towers := _index(doc.get("towers", []))
	var enemies := _index(doc.get("enemies", []))

	var s := AnchorSimScript.new()
	s.setup(doc["anchor"], towers, enemies, String(doc.get("difficulty", "standard")))

	for b in doc.get("builds", []):
		if not s.build_at(String(b["tower"]), float(b["x"]), float(b["y"])):
			push_error("arc_parity: build refused: %s at (%s, %s) — the fixture's own "
				% [b["tower"], b["x"], b["y"]]
				+ "geometry must satisfy _is_placeable()")
			quit(1)
			return
	for sp in doc.get("spawns", []):
		s.spawn(String(sp["enemy"]), int(sp.get("lane", 0)))

	var n: int = int(doc.get("ticks", 0))
	# One string of "0"/"1" per emplacement, plus the unit's own dist at each tick. Both
	# are compared against sim/engine.py's, character for character and float for float.
	var fired: Array = []
	for i in range(s.placed.size()):
		fired.append("")
	var dists: Array = []
	for _t in range(n):
		# A shot is the only thing that RAISES `cooldown` — _step() sets it to
		# fire_interval on firing and otherwise decrements it by DT * rate, so
		# "after > before" is exactly "fired this tick" with no new accessor.
		var before: Array = []
		for p in s.placed:
			before.append(float(p["cooldown"]))
		s.tick()
		for i in range(s.placed.size()):
			fired[i] += "1" if float(s.placed[i]["cooldown"]) > float(before[i]) else "0"
		dists.append(float(s.units[0]["dist"]) if s.units.size() > 0 else -1.0)

	# full_precision, or JSON.stringify rounds every `dist` to Godot's short float form
	# and a cross-engine comparison would be measuring the serializer.
	print("ARC_PARITY_JSON " + JSON.stringify({
		"fired": fired,
		"dists": dists,
		"lives": s.lives,
		"leaks": s.leaks,
	}, "", true, true))
	quit()


func _index(rows: Array) -> Dictionary:
	var out := {}
	for r in rows:
		out[r["id"]] = r
	return out
