extends Node
## Autoload `Sprites`. Loads the rendered sprite library described by
## assets/renders/sprites.json.
##
## The manifest is written by tools/blender/render.py and carries the projection it
## was rendered at, so the game can assert that its own iso constants match the ones
## the art was produced with. A silent mismatch there would misalign every tile.

const MANIFEST := "res://assets/renders/sprites.json"

var tile_px: Vector2i = Vector2i(128, 64)
var pivot: Vector2 = Vector2(128, 128)
var elevation_deg: float = 30.0
var ok: bool = false

var _cache: Dictionary = {}      # "name|yaw|pass" -> Texture2D
var _entries: Dictionary = {}


func _ready() -> void:
	var f := FileAccess.open(MANIFEST, FileAccess.READ)
	if f == null:
		push_warning("sprites: no manifest; falling back to placeholder drawing")
		return
	var doc: Variant = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("sprites: manifest is not an object")
		return
	tile_px = Vector2i(int(doc["tile_px"][0]), int(doc["tile_px"][1]))
	pivot = Vector2(float(doc["pivot"][0]), float(doc["pivot"][1]))
	elevation_deg = float(doc.get("elevation_deg", 30.0))
	_entries = doc.get("sprites", {})

	# The art and the game must agree on tile size or every placement is off.
	var iso := preload("res://scripts/iso.gd")
	if tile_px.x != int(iso.TILE_W) or tile_px.y != int(iso.TILE_H):
		push_error("sprites: manifest tile %s but Iso is %dx%d — art and game disagree"
			% [tile_px, int(iso.TILE_W), int(iso.TILE_H)])
		return
	ok = _entries.size() > 0


func has(name: String) -> bool:
	return _entries.has(name)


func get_tex(name: String, yaw: int = 45, pass_name: String = "albedo") -> Texture2D:
	var key := "%s|%d|%s" % [name, yaw, pass_name]
	if _cache.has(key):
		return _cache[key]
	var tex: Texture2D = null
	if _entries.has(name):
		var by_yaw: Dictionary = _entries[name]
		var slot := "y%03d" % yaw
		if by_yaw.has(slot) and by_yaw[slot].has(pass_name):
			var path := "res://" + String(by_yaw[slot][pass_name])
			if ResourceLoader.exists(path):
				tex = load(path)
	_cache[key] = tex
	return tex


func report() -> String:
	return "sprites %s (%d assets)" % ["ok" if ok else "MISSING", _entries.size()]
