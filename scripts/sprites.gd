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

## The library is packed into atlas pages per pass by tools/blender/pack_atlas.py.
## 192 loose 256x256 textures were 192 separate resources for the board to bind; they are
## now a small, fixed number of pages. The pack is a fixed grid and nothing is trimmed,
## which is what lets a single measured pivot keep serving every sprite — see that script
## for why trimming would reintroduce LF-027.
##
## LF-124: a pass that outgrows one page (ART-01's per-yaw head/base split, ART-04's
## 512px cells, or both at once) spills into more of them rather than one page growing
## past what GL_MAX_TEXTURE_SIZE can hold. `_pages[pass]` is therefore an Array of
## Texture2D indexed by page number, not a single texture, and `_index[pass][key]` is
## `[page, col, row]` rather than `[col, row]` — see pack_atlas.py's own doc for why that
## third element is invisible to tools/check.py's `sprite atlas` check.
var _pages: Dictionary = {}      # pass -> Array[Texture2D], indexed by page number
var _index: Dictionary = {}      # pass -> {"name|yNNN": [page, col, row]}
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
	## LF-124: "page_files" is the ordered-by-page-number list `_from_atlas()` indexes
	## into. ("pages", the flat {name: relpath} dict tools/check.py reads for existence
	## checking, is not read here — it carries the same paths but no page ordering.)
	var page_files: Dictionary = atlas.get("page_files", {})
	for pass_name in page_files:
		var rel_paths: Array = page_files[pass_name]
		var textures: Array = []
		var all_present := true
		for rel in rel_paths:
			var path := "res://" + String(rel)
			if not ResourceLoader.exists(path):
				push_warning("sprites: atlas page missing, falling back to loose files: %s" % path)
				all_present = false
				break
			textures.append(load(path))
		if all_present and not textures.is_empty():
			_pages[pass_name] = textures
	_index = atlas.get("index", {})
	atlas_ok = _pages.size() > 0


static func name_for(id: String) -> String:
	## The one place the data-id -> sprite-name convention is written down (PRC-14).
	##
	## Before this, `anchor_view.gd` derived it inline, twice over — once for a placed
	## tower, once for a unit — and `tools/blender/render.py`'s FX loader and
	## `tools/check.py`'s `sprite coverage` each re-derived the same transform a third and
	## fourth time, independently. None of those four sites could disagree today because
	## the convention is a one-line string transform, but PRC-14's whole point is that
	## "cannot disagree because it is simple" stops being true once ART-01 splits a head
	## sprite from its base and takes the naming past a bare hyphen swap. `static` so a
	## caller with no `Sprites` instance at all (the `@tool` editor path — see
	## `_sprite_lib()`'s own LF-025 doc) can still call `SpritesScript.name_for(id)`
	## without instantiating anything.
	##
	## Python has no way to import this across the Blender/Godot boundary (the same
	## problem `YAW_COUNT` solved differently — see render.py's own doc), so
	## `tools/blender/gen_assets.py` keeps a byte-identical mirror of this one line next to
	## a comment pointing back here. This is meant to be the sole site in `scripts/`
	## performing this exact string transform; `anchor_view.gd`'s two remaining inline
	## call sites still need to be swapped to call this instead (owned by another
	## workstream — see this file's own module-level history for context, PRC-14).
	return id.replace("-", "_")


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
	# [page, col, row] (LF-124) — page first, so a sprite that landed past the first
	# page's capacity still resolves to the right physical texture.
	var page_num := int(cell[0])
	var col := int(cell[1])
	var row := int(cell[2])
	var pages: Array = _pages[pass_name]
	if page_num < 0 or page_num >= pages.size():
		push_error(("sprites: %s references atlas page %d but pass %s only has %d — " +
			"manifest and packed pages disagree") % [key, page_num, pass_name, pages.size()])
		return null
	var at := AtlasTexture.new()
	at.atlas = pages[page_num]
	at.region = Rect2(float(col * _cell), float(row * _cell), float(_cell), float(_cell))
	# Cells sit edge to edge, so a filtered sample at a boundary would pull in the
	# neighbouring sprite. Clipping is what makes a grid atlas safe to filter.
	at.filter_clip = true
	return at


func report() -> String:
	var n_pages := 0
	for pass_name in _pages:
		n_pages += (_pages[pass_name] as Array).size()
	return "sprites %s (%d assets, %s)" % [
		"ok" if ok else "MISSING", _entries.size(),
		("atlas %d pages" % n_pages) if atlas_ok else "loose files"]
