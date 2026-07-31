@tool
extends Node
## Autoload `Sprites`. Loads the rendered sprite library described by
## assets/renders/sprites.json.
##
## `@tool` for the same reason as content.gd: the editor board preview needs real
## tile textures, and a non-tool autoload does not exist while editing.
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

## The library is packed into one atlas page per pass by tools/blender/pack_atlas.py.
## 192 loose 256x256 textures were 192 separate resources for the board to bind; they are
## now two. The pack is a fixed grid and nothing is trimmed, which is what lets a single
## measured pivot keep serving every sprite — see that script for why trimming would
## reintroduce LF-027.
var _pages: Dictionary = {}      # pass -> Texture2D
var _index: Dictionary = {}      # pass -> {"name|yNNN": [col, row]}
var _cell: int = 256
var atlas_ok: bool = false


func _ready() -> void:
	load_library()


func load_library() -> void:
	## Separate from _ready() so the in-editor board preview can build its own instance.
	## Godot instantiates an autoload in the editor only when its script is a tool script,
	## and only at editor startup — so a singleton that has just become `@tool` stays null
	## until the whole project is reloaded, and the preview silently falls back to flat
	## colour. anchor_data.gd exists for exactly this reason on the Content side; this is
	## the same fix for sprites. LF-025.
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

	# LF-108/ART-02: the yaw count used to be encoded independently in render.py, in this
	# manifest's own "yaws" list, and (via the bucket width it implies) in iso.gd's
	# hysteresis maths — nothing checked the three agreed, so a game built against a
	# library rendered at a different count would silently miss every texture whose slot
	# name it never asks for. `render.py` now writes "yaw_count" explicitly (falling back
	# to `len(yaws)` for a manifest rendered before that field existed); this is the one
	# place that compares it against `Iso.YAW_COUNT`, the GDScript-side source of truth.
	var manifest_yaw_count: int = int(doc.get("yaw_count", (doc.get("yaws", []) as Array).size()))
	if manifest_yaw_count != int(iso.YAW_COUNT):
		push_error(("sprites: manifest has %d yaw(s) but Iso.YAW_COUNT is %d — art and game " +
			"disagree on the yaw count") % [manifest_yaw_count, int(iso.YAW_COUNT)])
		return
	assert(iso.YAW_HYSTERESIS_FRAC < 0.5,
			"Iso.YAW_HYSTERESIS_FRAC must stay below 0.5 (LF-108) — at or above it the " +
			"hysteresis band exceeds a bucket's own half-width and every facing freezes")
	_load_atlas(doc.get("atlas", {}))
	ok = _entries.size() > 0


func _load_atlas(atlas: Dictionary) -> void:
	## Absent atlas is not an error. render.py rewrites the manifest without this section,
	## so between a re-render and a re-pack the game simply loads individual files again.
	if atlas.is_empty():
		return
	_cell = int(atlas.get("cell", 256))
	for pass_name in atlas.get("pages", {}):
		var path := "res://" + String(atlas["pages"][pass_name])
		if ResourceLoader.exists(path):
			_pages[pass_name] = load(path)
		else:
			push_warning("sprites: atlas page missing, falling back to loose files: %s" % path)
	_index = atlas.get("index", {})
	atlas_ok = _pages.size() > 0


func has(name: String) -> bool:
	return _entries.has(name)


func get_tex(name: String, yaw: int = 45, pass_name: String = "albedo") -> Texture2D:
	var key := "%s|%d|%s" % [name, yaw, pass_name]
	if _cache.has(key):
		return _cache[key]
	var tex: Texture2D = _from_atlas(name, yaw, pass_name)
	if tex == null and _entries.has(name):
		var by_yaw: Dictionary = _entries[name]
		var slot := "y%03d" % yaw
		if by_yaw.has(slot) and by_yaw[slot].has(pass_name):
			var path := "res://" + String(by_yaw[slot][pass_name])
			if ResourceLoader.exists(path):
				tex = load(path)
	_cache[key] = tex
	return tex


func _from_atlas(name: String, yaw: int, pass_name: String) -> Texture2D:
	if not _pages.has(pass_name):
		return null
	var idx: Dictionary = _index.get(pass_name, {})
	var key := "%s|y%03d" % [name, yaw]
	if not idx.has(key):
		return null
	var cell: Array = idx[key]
	var at := AtlasTexture.new()
	at.atlas = _pages[pass_name]
	at.region = Rect2(float(int(cell[0]) * _cell), float(int(cell[1]) * _cell),
			float(_cell), float(_cell))
	# Cells sit edge to edge, so a filtered sample at a boundary would pull in the
	# neighbouring sprite. Clipping is what makes a grid atlas safe to filter.
	at.filter_clip = true
	return at


func report() -> String:
	return "sprites %s (%d assets, %s)" % [
		"ok" if ok else "MISSING", _entries.size(),
		"atlas %d pages" % _pages.size() if atlas_ok else "loose files"]
