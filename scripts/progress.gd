extends Node
## Autoload `Progress`. What the player has cleared, and on which difficulty.
##
## Deliberately tiny and deliberately JSON: the save is a readable file the player (or a
## bug report) can open, and nothing in it is load-bearing for the rules. Losing it costs
## unlocks, not correctness — the anchors themselves are content and live in res://.
##
## Anchors unlock in order. An anchor is available if it is the first one, or if the one
## before it has been cleared on any difficulty. That keeps the story order intact without
## making a hard difficulty a prerequisite for seeing the next scene.

const SAVE_PATH := "user://progress.json"
## 2 adds the display block. 3 adds owned_recoveries — the between-anchor recovery draft's
## kept picks. Bumped rather than added silently so a save written by this build is
## identifiable; version 1 and 2 both still load, because every new field has a default and
## an older save simply leaves them at it. A save with no `owned_recoveries` key at all
## loads with the field at its default empty array — nothing more is needed to migrate it.
const SAVE_VERSION := 3

## anchor id -> {difficulty: best lives remaining}
var cleared: Dictionary = {}
## Difficulty chosen in the menu; the game scene reads it when the CLI does not override.
var difficulty: String = "standard"
## Anchor chosen in the menu, for the same reason.
var selected_anchor: String = "anchor-01"
## Player volume, 0..1 linear. Converted to dB by the audio director.
var music_volume: float = 0.8
var sfx_volume: float = 0.9
## Ids from `recoveries.pool` in data/tuning.json the player has kept from a debrief draft.
## Progress owns this list because it is the save — losing it should cost exactly what
## losing a cleared anchor costs, no more. The pool itself, the accessors that turn an id
## into a rule, and the draft that offers three of them live in the Recoveries autoload;
## this stays a plain array of strings so the save format does not know what an effect does.
var owned_recoveries: Array[String] = []

signal changed


func _ready() -> void:
	load_state()


# ─────────────────────────────────────────────────────────────────── query ──

func anchor_ids() -> PackedStringArray:
	var out := PackedStringArray()
	for i in range(1, 25):
		out.append("anchor-%02d" % i)
	return out


func is_cleared(anchor_id: String, diff: String = "") -> bool:
	if not cleared.has(anchor_id):
		return false
	if diff == "":
		return true
	return Dictionary(cleared[anchor_id]).has(diff)


func is_unlocked(anchor_id: String) -> bool:
	var ids := anchor_ids()
	var i := Array(ids).find(anchor_id)
	if i <= 0:
		return i == 0
	return is_cleared(ids[i - 1])


func next_anchor() -> String:
	## The first anchor that is unlocked and not yet cleared — what CONTINUE runs.
	for id in anchor_ids():
		if is_unlocked(id) and not is_cleared(id):
			return id
	return anchor_ids()[0]


func best_lives(anchor_id: String, diff: String) -> int:
	if not is_cleared(anchor_id, diff):
		return 0
	return int(Dictionary(cleared[anchor_id])[diff])


func cleared_count() -> int:
	return cleared.size()


func owns_recovery(id: String) -> bool:
	return owned_recoveries.has(id)


# ──────────────────────────────────────────────────────────────── mutation ──

func mark_cleared(anchor_id: String, diff: String, lives_left: int) -> void:
	var row: Dictionary = cleared.get(anchor_id, {})
	# Keep the best result rather than the latest: a scrappier repeat run should not
	# overwrite evidence of a clean one.
	if not row.has(diff) or int(row[diff]) < lives_left:
		row[diff] = lives_left
	cleared[anchor_id] = row
	save_state()
	changed.emit()


func grant_recovery(id: String) -> void:
	## Keep one recovered fragment for the rest of the campaign. A no-op if it is already
	## owned — the draft never re-offers an owned id, but a second call (a double-click on
	## TAKE, say) must not be able to make an additive effect count twice.
	if owned_recoveries.has(id):
		return
	owned_recoveries.append(id)
	save_state()
	changed.emit()


func reset() -> void:
	cleared = {}
	owned_recoveries = []
	save_state()
	changed.emit()


# ───────────────────────────────────────────────────────────────── storage ──

## Where the save lived while the project was still named after its repository.
## Decision 040 renamed the application to Latticefall, and Godot derives user:// from
## that name — so on the rename every existing player's progress became a file the game
## no longer looks at. It is still on disk, so read it once rather than silently starting
## them over.
const LEGACY_DIR_NAMES := ["Defend-Claude", "defend-claude"]


func _adopt_legacy_save() -> FileAccess:
	## Returns an open handle to a pre-rename save, having first copied it into the new
	## location so this only ever happens once. Null when there is nothing to adopt.
	var here := ProjectSettings.globalize_path("user://").trim_suffix("/")
	var parent := here.get_base_dir()
	for name in LEGACY_DIR_NAMES:
		var candidate := parent.path_join(name).path_join("progress.json")
		if candidate == ProjectSettings.globalize_path(SAVE_PATH):
			continue
		var src := FileAccess.open(candidate, FileAccess.READ)
		if src == null:
			continue
		var text := src.get_as_text()
		var dst := FileAccess.open(SAVE_PATH, FileAccess.WRITE)
		if dst != null:
			dst.store_string(text)
			dst.close()
		print("progress: adopted save from %s" % candidate)
		return FileAccess.open(SAVE_PATH, FileAccess.READ)
	return null


func load_state() -> void:
	var f := FileAccess.open(SAVE_PATH, FileAccess.READ)
	if f == null:
		f = _adopt_legacy_save()
	if f == null:
		return
	var doc: Variant = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_warning("progress: save is not an object; starting fresh")
		return
	# A save from a future version is not readable, and guessing at it would silently
	# discard the player's history. Leave it alone and start empty.
	if int(doc.get("version", 0)) > SAVE_VERSION:
		push_warning("progress: save version %d is newer than %d; ignoring"
			% [int(doc["version"]), SAVE_VERSION])
		return
	cleared = doc.get("cleared", {})
	difficulty = String(doc.get("difficulty", "standard"))
	music_volume = float(doc.get("music_volume", music_volume))
	sfx_volume = float(doc.get("sfx_volume", sfx_volume))
	# A pre-recovery-draft save has no owned_recoveries key at all — .get()'s default is an
	# empty array, so version 1 and 2 saves load with nothing owned rather than an error.
	# Built element-by-element rather than assigned directly: `doc.get(...)` returns an
	# untyped Array from JSON.parse_string, and this is Array[String].
	owned_recoveries.clear()
	for id in doc.get("owned_recoveries", []):
		owned_recoveries.append(String(id))
	_load_display(doc.get("display", {}))


func _load_display(d: Dictionary) -> void:
	## Display settings live on the Display autoload, which owns applying them; Progress
	## owns only the file. Autoload order puts Display before Progress, so the object
	## exists by the time this runs, and Display.apply() happens in its own _ready() —
	## after this, because Progress.load_state() is called from _ready() one node earlier.
	if d.is_empty() or Display.settings_locked():
		return
	Display.window_mode = String(d.get("window_mode", Display.window_mode))
	var res: Array = d.get("resolution", [])
	if res.size() == 2:
		Display.resolution = Vector2i(int(res[0]), int(res[1]))
	Display.vsync = bool(d.get("vsync", Display.vsync))
	Display.max_fps = int(d.get("max_fps", Display.max_fps))
	Display.ui_scale = float(d.get("ui_scale", Display.ui_scale))
	Display.glow = float(d.get("glow", Display.glow))
	# Display._ready() already ran and applied the defaults; these values arrived after it,
	# so they have to be pushed to the window here or the save is read and ignored.
	Display.apply()


func save_state() -> void:
	var f := FileAccess.open(SAVE_PATH, FileAccess.WRITE)
	if f == null:
		push_error("progress: cannot write %s (%d)" % [SAVE_PATH, FileAccess.get_open_error()])
		return
	f.store_string(JSON.stringify({
		"version": SAVE_VERSION,
		"cleared": cleared,
		"difficulty": difficulty,
		"music_volume": music_volume,
		"sfx_volume": sfx_volume,
		"owned_recoveries": owned_recoveries,
		"display": {
			"window_mode": Display.window_mode,
			"resolution": [Display.resolution.x, Display.resolution.y],
			"vsync": Display.vsync,
			"max_fps": Display.max_fps,
			"ui_scale": Display.ui_scale,
			"glow": Display.glow,
		},
	}, "  "))


func report() -> String:
	return "progress %d/24 cleared, difficulty %s, %d recoveries owned" % [
		cleared_count(), difficulty, owned_recoveries.size()]
