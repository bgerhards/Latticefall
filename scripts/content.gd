@tool
extends Node
## Autoload `Content`. Loads game data from res://data/.
##
## Code reads data; code never contains content (decision 008). Adding an
## emplacement must never require touching a script.
##
## `@tool` because Godot only instantiates an autoload in the editor when its script
## is a tool script. Without it the `Content` singleton simply does not exist while
## editing, and the in-editor board preview in anchor_view.gd cannot read a level.
## Nothing here touches the scene tree or the renderer, so running in-editor is inert.

var towers: Dictionary = {}      # id -> Dictionary
var enemies: Dictionary = {}     # id -> Dictionary
var _anchors: Dictionary = {}    # id -> Dictionary
var _dialog: Dictionary = {}     # anchor id -> Array


func _ready() -> void:
	towers = _index(_read("res://data/towers.json").get("towers", []))
	enemies = _index(_read("res://data/enemies.json").get("enemies", []))


func _read(path: String) -> Dictionary:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("content: cannot open %s (%d)" % [path, FileAccess.get_open_error()])
		return {}
	var parsed: Variant = JSON.parse_string(f.get_as_text())
	if typeof(parsed) != TYPE_DICTIONARY:
		push_error("content: %s is not a JSON object" % path)
		return {}
	return parsed


func _index(rows: Array) -> Dictionary:
	var out := {}
	for r in rows:
		out[r["id"]] = r
	return out


## Resolve an anchor doc's optional "terrain" block into a dense row-major level grid:
## an Array of grid.h PackedInt32Array rows, each grid.w long. PackedInt32Array rows, not
## a nested Array of Array of int -- this grid is read in the draw path and a 64x64 board
## is 4,096 Variants as nested Arrays.
##
## TER-02. This algorithm exists twice -- here, and as resolve_terrain() in sim/content.py
## -- implementing the same prose from data/schema/anchor.schema.json's `terrain`
## description, byte for byte. PRD-THEATRE-SCALE.md risk #10 is exactly these two parsers
## disagreeing on one tile; data/schema/fixtures/terrain-resolution.json is the fixture
## both are proved identical against, by tools/terrain_parity.py (run through this file via
## scripts/test/terrain_parity.gd) and the 'terrain parsers agree' gate check. If either
## implementation changes, change the other the same way and re-run that check before
## touching anything else.
##
## Absent "terrain" (the case for all anchors except the TER-02 pilot) resolves to an
## all-zero grid, so every downstream consumer has exactly one code path whether or not
## the anchor declares any relief.
##
## Resolution order, matching the schema:
##   1. "heightmap" present -> return it verbatim (already levels[y][x], row-major). It
##      REPLACES "regions" entirely and is never composited with them -- the schema makes
##      declaring both a validation error rather than leaving a precedence rule for this
##      function to invent.
##   2. otherwise every tile starts at level 0, and "regions" are painted in array order:
##      each region's "rect" ([x, y, w, h], half-open in w/h) completely overwrites the
##      level of every tile it covers, including *lowering* it below what an earlier
##      region wrote. Later region wins, full stop -- this is the one sentence both
##      parsers must implement identically.
## A rect that runs off the board is clipped to the grid, never wrapped and never an
## error. "ramps" are declared metadata for a future line-of-sight / stepped-movement
## consumer (TER-06/TER-08/TER-12) and are not consulted here; they never affect the
## resolved grid.
static func resolve_terrain(doc: Dictionary) -> Array:
	var w: int = doc["grid"]["w"]
	var h: int = doc["grid"]["h"]
	var terrain: Variant = doc.get("terrain")

	if typeof(terrain) != TYPE_DICTIONARY or terrain.is_empty():
		return _zero_grid(w, h)

	var t: Dictionary = terrain
	if t.has("heightmap"):
		var grid: Array = []
		for src_row in t["heightmap"]:
			var row := PackedInt32Array()
			for v in src_row:
				row.append(int(v))
			grid.append(row)
		return grid

	var grid: Array = _zero_grid(w, h)
	for region in t.get("regions", []):
		var rect: Array = region["rect"]
		var rx: int = int(rect[0])
		var ry: int = int(rect[1])
		var rw: int = int(rect[2])
		var rh: int = int(rect[3])
		var z: int = int(region["z"])
		for y in range(maxi(0, ry), mini(h, ry + rh)):
			var row: PackedInt32Array = grid[y]
			for x in range(maxi(0, rx), mini(w, rx + rw)):
				row[x] = z
			grid[y] = row
	return grid


static func _zero_grid(w: int, h: int) -> Array:
	var grid: Array = []
	for _y in range(h):
		var row := PackedInt32Array()
		row.resize(w)
		grid.append(row)
	return grid


func anchor(id: String) -> Dictionary:
	if not _anchors.has(id):
		_anchors[id] = _read("res://data/anchors/%s.json" % id)
	return _anchors[id]


func dialog(anchor_id: String) -> Array:
	if not _dialog.has(anchor_id):
		var d := _read("res://data/dialog/%s.json" % anchor_id)
		_dialog[anchor_id] = d.get("lines", [])
	return _dialog[anchor_id]


func tower(id: String) -> Dictionary:
	return towers.get(id, {})


func enemy(id: String) -> Dictionary:
	return enemies.get(id, {})


func unlocked_at(anchor_id: String) -> Array:
	var out: Array = []
	for id in towers:
		if String(towers[id].get("unlocked_at", "anchor-01")) <= anchor_id:
			out.append(id)
	out.sort()
	return out
