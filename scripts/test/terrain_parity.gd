extends SceneTree
## Headless terrain-resolution parity runner (TER-02).
##
## Loads data/schema/fixtures/terrain-resolution.json, resolves every case through
## Content's resolve_terrain() (scripts/content.gd), and prints one JSON line.
## tools/terrain_parity.py runs sim/content.py's resolve_terrain() over the same fixture
## and diffs the two grids tile for tile — that pair disagreeing on one tile, silently, is
## PRD-THEATRE-SCALE.md risk #10, and this is the check that exists to catch it before the
## 9-minute `rules parity` gate would (as "some unit leaked in one engine and not the
## other", with no pointer back to terrain at all).
##
##   godot --headless --path . --script res://scripts/test/terrain_parity.gd

## preload rather than the global class_name: global names come from the editor's import
## cache, which does not exist in a bare `--headless --script` run (see scripts/test/parity.gd).
const ContentScript := preload("res://scripts/content.gd")

const FIXTURE := "res://data/schema/fixtures/terrain-resolution.json"


func _init() -> void:
	var f := FileAccess.open(FIXTURE, FileAccess.READ)
	if f == null:
		push_error("terrain_parity: cannot open %s" % FIXTURE)
		quit(1)
		return
	var doc: Variant = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("terrain_parity: fixture is not a JSON object")
		quit(1)
		return

	var out: Array = []
	for case in doc.get("cases", []):
		var grid: Array = ContentScript.resolve_terrain(case["doc"])
		var rows: Array = []
		for row in grid:
			rows.append(Array(row))  # PackedInt32Array -> plain Array so JSON.stringify keeps ints
		out.append({"name": case["name"], "grid": rows})

	print("TERRAIN_PARITY_JSON " + JSON.stringify(out))
	quit()
