@tool
extends Node
## Autoload `Tuning`. Loads the player-facing floor-raisers from data/tuning.json:
## pacing, the three bindstone abilities, targeting priorities and veterancy ranks.
##
## Deliberately separate from `Recoveries`, which reads the same file — Recoveries owns
## the `recoveries` and `grade` blocks, this owns `pacing`, `abilities`, `targeting` and
## `veterancy`. Two readers of one file rather than one growing autoload doing two jobs,
## and it means this file never has to touch scripts/recoveries.gd (out of scope for the
## change that added it) to add the blocks it needed.
##
## Read data/tuning.json's own top-level "note" before changing anything here: everything
## this file hands out must stay inert unless the player acts on it. This autoload itself
## is never read by scripts/anchor_sim.gd, which is a bare RefCounted precisely so it can
## run under `godot --headless --script res://scripts/test/parity.gd` — a run that does not
## register autoloads at all (see scripts/test/facing.gd's docstring for the same fact
## about `Content`). Every value this file hands out is therefore threaded in by the caller
## (scripts/anchor_view.gd, scripts/abilities.gd) as a plain parameter, never read from
## inside anchor_sim.gd itself.
##
## `@tool` so it exists while editing too, for the same reason Content is a tool script —
## nothing here touches the scene tree or the renderer, so running in-editor is inert.

var _pacing: Dictionary = {}
var _abilities: Array = []      # as authored: [{id, name, label, charge_max, ...}, ...]
var _by_id: Dictionary = {}     # ability id -> its dict
var _targeting: Dictionary = {}
var _veterancy_ranks: Array = []


func _ready() -> void:
	_load()


func _load() -> void:
	var f := FileAccess.open("res://data/tuning.json", FileAccess.READ)
	if f == null:
		push_error("tuning: cannot open data/tuning.json (%d)" % FileAccess.get_open_error())
		return
	var doc: Variant = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("tuning: data/tuning.json is not a JSON object")
		return
	_pacing = doc.get("pacing", {})
	_abilities = doc.get("abilities", [])
	_by_id.clear()
	for a in _abilities:
		_by_id[String(a["id"])] = a
	_targeting = doc.get("targeting", {})
	_veterancy_ranks = doc.get("veterancy", {}).get("ranks", [])


# ──────────────────────────────────────────────────────────────── pacing ──

func pacing() -> Dictionary:
	return _pacing


func speeds() -> Array:
	return _pacing.get("speeds", [1.0])


func call_bonus_per_sec() -> float:
	return float(_pacing.get("call_bonus_per_sec", 0.0))


func chain_window_s() -> float:
	return float(_pacing.get("chain_window_s", 2.5))


func chain_bounty_per_kill() -> float:
	return float(_pacing.get("chain_bounty_per_kill", 0.0))


func chain_bounty_max() -> float:
	return float(_pacing.get("chain_bounty_max", 0.0))


func clean_sweep_bonus(act: int) -> int:
	var arr: Array = _pacing.get("clean_sweep_bonus_by_act", [])
	var i := clampi(act - 1, 0, arr.size() - 1)
	return int(arr[i]) if arr.size() > 0 else 0


# ────────────────────────────────────────────────────────────── abilities ──

func abilities() -> Array:
	return _abilities


func ability(id: String) -> Dictionary:
	return _by_id.get(id, {})


# ────────────────────────────────────────────────────────────── targeting ──

func targeting_modes() -> Array:
	return _targeting.get("modes", ["first"])


func targeting_default() -> String:
	return String(_targeting.get("default", "first"))


# ────────────────────────────────────────────────────────────── veterancy ──

func veterancy_ranks() -> Array:
	return _veterancy_ranks


func report() -> String:
	return "tuning pacing=%s abilities=%d targeting=%d veterancy_ranks=%d" % [
		not _pacing.is_empty(), _abilities.size(), _targeting.size(), _veterancy_ranks.size()]
