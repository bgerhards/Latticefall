@tool
extends RefCounted
## Direct, dependency-free reads of res://data/. Used by the in-editor board preview.
##
## The preview cannot go through the `Content` autoload. Godot instantiates an autoload
## in the editor only when its script is a tool script, and only at editor startup — so
## a singleton that has just been made `@tool` is still null until the whole project is
## reloaded, and the preview silently draws nothing. Reading the file is a few lines and
## removes that failure mode completely.
##
## This is a read path for authoring aids only. The running game uses Content, which is
## the single loader for actual play (decision 008 — code reads data, never contains it).

static func read_json(path: String) -> Dictionary:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return {}
	var parsed: Variant = JSON.parse_string(f.get_as_text())
	return parsed if typeof(parsed) == TYPE_DICTIONARY else {}


static func anchor(id: String) -> Dictionary:
	return read_json("res://data/anchors/%s.json" % id)


static func anchor_ids() -> PackedStringArray:
	## Every authored level, so the preview can offer a dropdown rather than a raw string.
	var out := PackedStringArray()
	var dir := DirAccess.open("res://data/anchors")
	if dir == null:
		return out
	for f in dir.get_files():
		if f.ends_with(".json"):
			out.append(f.get_basename())
	out.sort()
	return out
