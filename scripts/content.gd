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
