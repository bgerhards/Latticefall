extends RefCounted
## PRC-12: one `--scenario <path>` file, driving a timeline of actions and assertions,
## replacing a growing pile of one-off `main.gd` CLI flags (`--paused`, `--select`, `--pick`,
## `--cursor`, `--scroll`, `--build`, `--speed`, `--ability-at`, `--press-at`, `--camera`,
## `--facings`, ...). See `data/schema/scenario.schema.json` for the file shape and the
## verb-to-legacy-flag mapping table, and
## `docs/issues/PRC-12-scenario-assertion-harness.md` for the design this implements.
##
## Owned and driven entirely by `scripts/main.gd`: this is a plain `RefCounted`, not a Node
## and not an autoload, and not a `class_name` — preloaded instead, for the same reason
## `anchor_view.gd` preloads `AbilityStateScript` and `hud.gd` preloads `MinimapScript`: a
## new `class_name` is invisible until the editor has imported once, and the symptom is a
## hang, not an error (CLAUDE.md).
##
## Deliberately NOT an expression evaluator. `expr` is a dotted path into a small exported
## state dictionary (`AnchorView.export_state()` + `Hud.export_state()`, merged by
## `snapshot()` below) — no arithmetic, no GDScript `Expression`. A scenario file is content
## (`validate_data.py` validates it against `scenario.schema.json` exactly like a tower or an
## anchor), and content that can execute is a different kind of object than content that is
## merely read.
##
## `shot`/`a11y`/`facings` are deliberately NOT dispatched as ordinary timeline actions.
## They are pulled out at load time into `shot_frame`/`shot_path`/`a11y_path`/
## `facings_wanted` so `main.gd` can feed them straight into its own existing, already-
## verified `--shot`/`--a11y`/`--facings` machinery (the freeze-before-await-frame_post_draw
## dance that fixed LF-029, the coverage/blank-frame stats, the STATE/BUS/CAMERA/AUDIO
## report lines) rather than re-implementing a second capture path that could drift from the
## first. The cost of that reuse is the one real constraint this file enforces at load time:
## a `shot` action must be the LAST thing in the timeline, because `main.gd`'s existing
## `_process()` calls `get_tree().quit()` right after taking it — nothing scheduled at a
## later frame will ever run. `a11y` must share its exact frame, because the analyser samples
## its background colours out of that same PNG (CLAUDE.md).

const KNOWN_ACTIONS := ["build", "select", "pick", "press", "gamepad", "ability", "speed",
	"scroll", "cursor", "pause", "camera", "shot", "a11y", "facings"]
## Actions folded into main.gd's pre-existing per-run fields rather than dispatched through
## `actions_at()` — see the file doc above.
const CAPTURE_ACTIONS := ["shot", "a11y", "facings"]

var anchor_id: String = ""
var difficulty: String = "standard"
var has_ui_scale: bool = false
var ui_scale: float = 1.0
var seed: int = -1                  ## reserved; unused by the anchor timeline (see schema)

var shot_frame: int = -1            ## -1 means no `shot` action was requested
var shot_path: String = ""
var a11y_frame: int = -1
var a11y_path: String = ""
var facings_frame: int = -1

var _actions: Array[Dictionary] = []   ## sorted, {"frame":, "action":, "args":, "_i":}
var _asserts: Array[Dictionary] = []   ## sorted, {"frame":, "assert":, "expr":, "expect":, "tolerance":, "_i":}
var _max_frame: int = 0
var _path: String = ""
var _error: String = ""

var _results: Array[Dictionary] = []   ## every evaluated assertion, pass or fail, in order
var _failed: bool = false

var _frame_ms: PackedFloat64Array = PackedFloat64Array()


func error() -> String:
	return _error


func path() -> String:
	return _path


func max_frame() -> int:
	return _max_frame


func failed() -> bool:
	return _failed


## Loads and validates `p`. Returns `false` (with `error()` set) on anything a scenario
## must never do silently: a file that will not parse, a document not naming
## `"schema": "scenario"`, an action verb this loader does not know, an entry that is
## neither an action nor a full assertion, or a `shot`/`a11y` pairing that breaks the rule
## above — every one of these is a load-time failure, matching PRC-12's acceptance
## criterion that a mistyped verb is a schema error, not a no-op.
func load_file(p: String) -> bool:
	_path = p
	var f := FileAccess.open(p, FileAccess.READ)
	if f == null:
		_error = "cannot open %s (%d)" % [p, FileAccess.get_open_error()]
		return false
	var doc: Variant = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		_error = "%s is not a JSON object" % p
		return false
	var d: Dictionary = doc
	if String(d.get("schema", "")) != "scenario":
		_error = "%s: schema is %s, not \"scenario\"" % [p, str(d.get("schema", "<missing>"))]
		return false
	if not d.has("anchor") or not d.has("timeline"):
		_error = "%s: missing required \"anchor\" or \"timeline\"" % p
		return false
	anchor_id = String(d["anchor"])
	difficulty = String(d.get("difficulty", "standard"))
	if d.has("ui_scale"):
		has_ui_scale = true
		ui_scale = float(d["ui_scale"])
	if d.has("seed"):
		seed = int(d["seed"])

	var raw: Array = d["timeline"]
	if raw.is_empty():
		_error = "%s: timeline is empty" % p
		return false

	# Stable total order: (frame, original index) — the same argument sim/engine.py:389's
	# `queue.sort(key=lambda q: (q[0], q[1]))` makes for its own merged queue. GDScript's
	# `sort_custom` is not documented as a stable sort, so two entries at the same frame
	# need the tiebreaker made explicit rather than relying on the underlying algorithm.
	var decorated: Array = []
	for i in range(raw.size()):
		if typeof(raw[i]) != TYPE_DICTIONARY:
			_error = "%s: timeline[%d] is not an object" % [p, i]
			return false
		var e: Dictionary = raw[i].duplicate()
		e["_i"] = i
		decorated.append(e)
	decorated.sort_custom(func(a, b):
		if int(a["frame"]) != int(b["frame"]):
			return int(a["frame"]) < int(b["frame"])
		return int(a["_i"]) < int(b["_i"]))

	for e in decorated:
		if not e.has("frame"):
			_error = "%s: timeline entry %d has no frame" % [p, int(e["_i"])]
			return false
		var frame := int(e["frame"])
		_max_frame = maxi(_max_frame, frame)
		if e.has("action"):
			var verb := String(e["action"])
			if not KNOWN_ACTIONS.has(verb):
				# The load-time schema error PRC-12's acceptance criteria ask for, not a
				# silent no-op: an unrecognised verb must never reach `_process()` and be
				# quietly skipped there.
				_error = "%s: unsupported action verb %s at frame %d" % [p, verb, frame]
				return false
			var args: Array = e.get("args", [])
			match verb:
				"shot":
					if shot_frame >= 0:
						_error = "%s: more than one shot action (frames %d and %d)" % [p, shot_frame, frame]
						return false
					if args.is_empty():
						_error = "%s: shot action at frame %d has no path" % [p, frame]
						return false
					shot_frame = frame
					shot_path = String(args[0])
				"a11y":
					if args.is_empty():
						_error = "%s: a11y action at frame %d has no path" % [p, frame]
						return false
					a11y_frame = frame
					a11y_path = String(args[0])
				"facings":
					facings_frame = frame
				_:
					_actions.append({"frame": frame, "action": verb, "args": args, "_i": int(e["_i"])})
		elif e.has("assert"):
			if not e.has("expr") or not e.has("expect"):
				_error = "%s: assert entry at frame %d missing expr/expect" % [p, frame]
				return false
			_asserts.append({
				"frame": frame, "assert": String(e["assert"]), "expr": String(e["expr"]),
				"expect": e["expect"], "tolerance": float(e.get("tolerance", 0.0)),
				"_i": int(e["_i"]),
			})
		else:
			_error = "%s: timeline entry at frame %d is neither an action nor an assert" % [p, frame]
			return false

	# The reuse this file's own doc explains: shot/a11y must be the last thing that happens,
	# because main.gd's existing --shot code path quits the process right after taking it.
	if shot_frame >= 0:
		if a11y_frame >= 0 and a11y_frame != shot_frame:
			_error = "%s: a11y (frame %d) must share shot's frame (%d) — the analyser " \
				% [p, a11y_frame, shot_frame] + "samples its background out of that PNG"
			return false
		if facings_frame >= 0 and facings_frame != shot_frame:
			_error = "%s: facings (frame %d) must share shot's frame (%d) — it is only " \
				% [p, facings_frame, shot_frame] + "meaningful on the frame that was drawn"
			return false
		for a in _actions:
			if int(a["frame"]) > shot_frame:
				_error = ("%s: action %s at frame %d is scheduled after the shot frame " +
					"%d — it would never run, since --shot quits the process right after " +
					"capturing") % [p, String(a["action"]), int(a["frame"]), shot_frame]
				return false
		for a in _asserts:
			if int(a["frame"]) > shot_frame:
				_error = ("%s: assert on %s at frame %d is scheduled after the shot frame " +
					"%d — it would never run") % [p, String(a["expr"]), int(a["frame"]), shot_frame]
				return false
	return true


func actions_at(frame: int) -> Array[Dictionary]:
	var out: Array[Dictionary] = []
	for a in _actions:
		if int(a["frame"]) == frame:
			out.append(a)
	return out


func asserts_at(frame: int) -> Array[Dictionary]:
	var out: Array[Dictionary] = []
	for a in _asserts:
		if int(a["frame"]) == frame:
			out.append(a)
	return out


## Every `frame` any action or assert is scheduled on, ascending — `main.gd` uses this to
## know when a shot-less scenario is actually finished.
func has_anything_at_or_after(frame: int) -> bool:
	for a in _actions:
		if int(a["frame"]) >= frame:
			return true
	for a in _asserts:
		if int(a["frame"]) >= frame:
			return true
	return false


# ─────────────────────────────────────────────────────── state / asserts ──

## Merges `AnchorView.export_state()` (already `{"sim": {...}, "view": {...}}`) with
## `Hud.export_state()` under `"hud"`, so every dotted path in the schema's own examples
## (`sim.lives`, `view.camera.zoom`, `hud.selected`) resolves against one snapshot.
static func snapshot(view: Node, hud: Node) -> Dictionary:
	var s: Dictionary = view.export_state() if view != null else {}
	s["hud"] = hud.export_state() if hud != null else {}
	return s


## Walks a dotted path (`"sim.units.0.hp"`) through nested Dictionaries and Arrays. Returns
## `null` on any miss rather than erroring — a typo'd path is then a clean assertion failure
## (`got=null`) instead of a crash, which is what a content file mistake should look like.
static func resolve(expr: String, state: Dictionary) -> Variant:
	var cur: Variant = state
	for part in expr.split("."):
		if typeof(cur) == TYPE_DICTIONARY:
			if not (cur as Dictionary).has(part):
				return null
			cur = (cur as Dictionary)[part]
		elif typeof(cur) == TYPE_ARRAY:
			if not part.is_valid_int():
				return null
			var idx := int(part)
			var arr: Array = cur
			if idx < 0 or idx >= arr.size():
				return null
			cur = arr[idx]
		else:
			return null
	return cur


static func _is_number(v: Variant) -> bool:
	return typeof(v) == TYPE_INT or typeof(v) == TYPE_FLOAT


static func compare(op: String, got: Variant, expect: Variant, tolerance: float) -> bool:
	if op == "==" or op == "!=":
		var eq: bool
		if _is_number(got) and _is_number(expect):
			eq = absf(float(got) - float(expect)) <= tolerance
		else:
			eq = got == expect
		return eq if op == "==" else not eq
	# Ordering comparators only make sense on numbers; a non-numeric got/expect here is
	# treated as a failure rather than a script error — the same "clean failure, not a
	# crash" reasoning resolve() follows above.
	if not (_is_number(got) and _is_number(expect)):
		return false
	var g := float(got)
	var e := float(expect)
	match op:
		"<":
			return g < e
		"<=":
			return g <= e
		">":
			return g > e
		">=":
			return g >= e
	return false


## Evaluates every assertion scheduled at `frame`. Returns the first failure (or `{}` if
## everything at this frame passed) so `main.gd` can print `ASSERT FAIL` and stop
## immediately — PRC-12 asks for exit-on-first-failure, not a report of every failure in
## one run. Every assertion (pass or fail) is still recorded for the `SCENARIO` summary.
func run_asserts_at(frame: int, view: Node, hud: Node) -> Dictionary:
	var state := snapshot(view, hud)
	for a in asserts_at(frame):
		var got: Variant = resolve(String(a["expr"]), state)
		var passed := compare(String(a["assert"]), got, a["expect"], float(a["tolerance"]))
		var rec := {
			"frame": frame, "expr": String(a["expr"]), "op": String(a["assert"]),
			"expect": a["expect"], "got": got, "pass": passed,
		}
		_results.append(rec)
		if not passed:
			_failed = true
			return rec
	return {}


func record_frame_time(ms: float) -> void:
	_frame_ms.append(ms)


func frame_time_stats() -> Dictionary:
	if _frame_ms.is_empty():
		return {"min": 0.0, "mean": 0.0, "p95": 0.0, "max": 0.0, "n": 0}
	var sorted := _frame_ms.duplicate()
	sorted.sort()
	var n := sorted.size()
	var total := 0.0
	for v in sorted:
		total += v
	var idx := clampi(int(ceil(0.95 * float(n))) - 1, 0, n - 1)
	return {"min": sorted[0], "mean": total / float(n), "p95": sorted[idx],
		"max": sorted[n - 1], "n": n}


## `SCENARIO {json}` — the machine-readable summary `tools/scenario.py` relays and CI
## consumes: every assertion evaluated, its frame, and its result, plus frame-time stats.
func summary_json() -> String:
	var payload := {
		"scenario": _path, "anchor": anchor_id, "difficulty": difficulty,
		"pass": not _failed, "assertions": _results, "frame_time_ms": frame_time_stats(),
	}
	return "SCENARIO %s" % JSON.stringify(payload)
