extends Node2D
## Root scene. Wires the anchor, HUD and dialog together.
##
## Deliberately not a @tool script: only anchor_view.gd needs to run while editing, and
## a script's tool-ness is independent of its parent's, so the board still previews.
##
## The child nodes are authored in scenes/main.tscn, not constructed here. They used to
## be built in _ready(), which made the scene file a single childless Node2D: opening the
## project showed an empty viewport and a scene dock that revealed nothing about the game.
## Authoring them costs nothing at runtime and makes the structure inspectable.
##
## Because children now _ready() before this node does, the anchor cannot be chosen in
## their _ready(). Setup is therefore explicit: boot() the view once the CLI is parsed,
## bind() the listeners, then start().

## PRC-12: the scenario runner and the shared CLI tokeniser. Preloaded, not `class_name` —
## same reasoning as `AbilityStateScript`/`MinimapScript` elsewhere: a new `class_name` is
## invisible until the editor has imported once, and the symptom is a hang, not an error
## (CLAUDE.md).
const ScenarioScript := preload("res://scripts/scenario.gd")
const CliArgsScript := preload("res://scripts/cli_args.gd")

@export var anchor_id: String = "anchor-01"
@export var difficulty: String = "standard"

@onready var view: Node2D = $AnchorView
@onready var hud: CanvasLayer = $Hud
@onready var dialog: CanvasLayer = $DialogView
@onready var pause_menu: CanvasLayer = $PauseMenu

## `-- --shot <path> [frame]` renders, saves a PNG and quits. Verification should not
## depend on capturing someone's desktop, and a screenshot the build can take itself
## is a screenshot CI can take too.
##
## Headless does not work for this: GL Compatibility reads back nothing without a real
## GPU-backed window (probed on this machine — `await RenderingServer.frame_post_draw`
## never resolves under `--headless --rendering-driver opengl3`; LF-061). So `--shot` still
## needs a real window, which by default is the one the owner is looking at. `-- --quiet-window`
## (read by the `Display` autoload in display_settings.gd, since it already owns window
## mode/flags/position and runs before this scene is even reached) sets WINDOW_FLAG_NO_FOCUS
## and WINDOW_FLAG_MOUSE_PASSTHROUGH and skips re-centring, so pairing it with the engine's
## builtin `--position <X>,<Y>` (applied at window creation, before any script runs) parks a
## fully-rendered window off every monitor without it ever taking focus or eating a click.
## `tools/shot.py` is the supported way to drive this. Default OFF: without the flag,
## `--shot` behaves exactly as it always has.
var _shot_path: String = ""
var _shot_at: int = 240
## `-- --a11y <path>` writes the text inventory for the same frame `--shot` captures.
## Same frame is the point: the analyser samples the background behind each label out of
## the PNG, so a report taken a frame later describes a screen that was never measured.
var _a11y_path: String = ""
## `-- --facings` prints the yaw every drawable was drawn at, on the frame `--shot` took.
var _dump_facings: bool = false
## `-- --lanes` prints which lane every alive unit resolved to, on the frame `--shot` took —
## the same idea as `--facings` (decision 049): a screenshot cannot settle which lane a unit
## is walking on a multi-lane anchor, so WAR-01 needs this to verify it rather than eyeball it.
var _dump_lanes: bool = false
## `-- --heights` prints `tx ty level` for every board tile whose terrain level is
## non-zero, plus the `z` of every drawable, on the frame `--shot` captures. TER-01: a
## screenshot shows *a* silhouette raised somewhere; only this settles whether tile (7,4)
## is at level 2 or level 3 — the same reasoning `--facings` (decision 049) and `--lanes`
## already give for a fact a screenshot cannot by itself distinguish.
var _dump_heights: bool = false
## `-- --dump-placeholder`: LF-046 verification — prints, for every enemy kind Content
## knows, its faction, the placeholder colour `_draw_unit()` would use, and the placeholder
## radius, without needing an actually-missing sprite to reach that code path at all (the
## `sprite coverage` gate means nothing in tracked data ever does). Fires once at boot,
## independent of `--shot`.
var _dump_placeholder: bool = false
var _frame: int = 0
var _shot_taken: bool = false
var _autoplay: bool = false
var _recorded: bool = false
var _open_pause: bool = false
var _select_nth: int = 0
var _pick_tower: String = ""
var _cursor_steps: int = 0
var _scroll_steps: int = 0
## `-- --build <tower-id>` (repeatable) puts a specific emplacement on the board.
##
## `--autoplay` cannot reach most of the library: `autobuild()` fills every free slot
## greedily from a total preference order, so a run builds all-of-one-thing and the flak
## array and mortar emplacement are never placed at all (the same limitation the grading
## policies have — LF-053). That made their projectiles unscreenshottable, so the combat FX
## for two of the six weapon classes shipped code-reviewed but never looked at. This is the
## hook that closes that hole, in the spirit of `--select`, `--pick` and `--cursor`: reach a
## state that otherwise needs a real player, rather than shipping something nobody has seen.
var _build_ids: Array[String] = []
## `-- --speed N` sets the game-speed multiplier at boot — reaching 2x/3x otherwise needs a
## real key press, same reasoning as every hook above.
var _speed_cli: float = 0.0
## `-- --ability <id>` (repeatable) skips its charge/cooldown and fires it immediately —
## overcharge active, shutter down, a surge with something in front of it to hit are all
## otherwise unreachable at --fixed-fps. Fires at boot, frame 0, before anything has spawned
## — see `--ability-at` below for firing into a wave that is actually on the board.
var _ability_ids: Array = []
## `-- --ability-at <frame> <id>` (repeatable) fires an ability at a chosen main.gd process
## frame instead of at boot, so it can land on a wave that is actually live — the frame count
## is the same counter `--shot <path> <frame>` captures against, so the fired frame is
## reproducible from the number alone (LF-028). Diagnostics print before/after state so a
## screenshot is never the only evidence: see `_fire_ability_at()`.
var _ability_at: Array = []      ## [{frame:int, id:String, fired:bool}]
## `-- --press-at <frame> <action>` (repeatable) dispatches one `lf_*` action press at a
## chosen frame — the general form of `--cursor`'s boot-only `lf_right` presses, for actions
## that need to land mid-run: `lf_target` to cycle targeting priority on the `--select`ed
## emplacement, `lf_call_wave` to skip prep once a wave is actually in prep. Repeat it at the
## same frame to press the same action more than once (e.g. three `lf_target` presses to
## reach "weakest" from the "first" default).
var _press_at: Array = []        ## [{frame:int, action:String, fired:bool}]
## `-- --chain N` sets the kill-chain streak directly — a real N-kill streak needs N kills
## inside chain_window_s of each other, which nothing at --fixed-fps can produce on its own.
var _chain_cli: int = 0
## `-- --debrief-at <frame>`: LF-065 verification hook. Presses the HUD's DEBRIEF button —
## `hud._on_next()` — at a chosen frame, exactly as a player click would, so the win ->
## draft.tscn transition is provable from a `--shot`-style run rather than trusted from
## reading `hud.gd` alone. Only meaningful once AnchorView reports `phase() == "done"` and a
## next anchor exists (the same gate `hud.gd`'s own `_refresh()` uses to show the button at
## all); firing earlier is a no-op the same way a click on a hidden button would be. -1 means
## "not requested", matching every other unset frame-scheduled hook in this file.
var _debrief_at: int = -1
var _debrief_fired: bool = false

## `-- --scenario <path>` (PRC-12): one file, driving a timeline of actions and assertions
## instead of a boot-time flag per screen. `_scenario` is untyped (a `ScenarioScript`
## instance or `null`) for the same reason `sim`/`abilities` are untyped elsewhere in this
## codebase — see anchor_view.gd's own note. Its `shot`/`a11y`/`facings` actions, if any,
## are folded into the legacy `_shot_path`/`_a11y_path`/`_dump_facings` fields above at load
## time (see `_load_scenario()`), so the existing, already-verified capture code path in
## `_process()` below runs unmodified for a scenario exactly as it does for `--shot`.
var _scenario = null
var _scenario_finished: bool = false

## `-- --profile <frames>` (CAM-06/CAM-07): print mean/p95 milliseconds per draw layer
## (AnchorView, GlowLayer, FxAdditive, CombatFx) over this many rendered frames, then exit.
## The performance claims in those two issues need a falsifiable number, not a feeling —
## see docs/issues/CAM-06-cull-and-cache-board-tiles.md's own task asking for this hook,
## following the existing `_setup_cli()` idiom rather than a bespoke tool. 0 means "not
## requested"; a real frame count is always >= 1 because `--profile 0` parses as falsy here
## exactly like every other unset numeric flag in this file.
var _profile_frames: int = 0

## `-- --camera <x> <y> <zoom>` (CAM-03): point the board camera at a tile coordinate at a
## chosen zoom, bypassing pan/zoom/edge-scroll/cursor-follow entirely so a `--shot` and its
## paired `--a11y` report depend on the flag alone rather than on default framing that CAM-01
## can keep changing. Applied after `view.boot()`, which is what seeds the default framing
## this is meant to override — see AnchorView.set_camera_override()'s own doc.
var _camera_cli: Dictionary = {}       ## {} means "not passed"; else {x, y, zoom}, all float
## `-- --mouse-at <x> <y>` (logical viewport pixels): places the pointer at boot, the way a
## `--cursor` press places the board cursor — edge-scroll and wheel-zoom-about-cursor are
## otherwise unscreenshottable at --fixed-fps, because both need to know where the mouse
## actually is, and nothing moves it on its own. Warps the real cursor (so later frames'
## `get_global_mouse_position()` reads keep reporting it) and also dispatches one synthetic
## motion event, so AnchorView's own "a pointer has been seen at all" gate opens the same way
## a real move would.
var _mouse_at: Vector2 = Vector2.ZERO
var _has_mouse_at: bool = false
## `-- --drag <dx> <dy>` (logical viewport pixels): a synthetic middle-button drag of that
## screen-space delta, starting at `--mouse-at` (or the viewport centre if that was not
## given) — the only way to reach CAM-01's pan path without a real mouse.
var _drag_delta: Vector2 = Vector2.ZERO
var _has_drag: bool = false
## `-- --wheel <n>` (repeatable notches, negative zooms out): synthetic wheel ticks at
## `--mouse-at` (or the viewport centre) — reaches CAM-01's zoom-about-the-cursor path, which
## a held key or a stick axis (both zoom about the strip centre instead) cannot exercise.
var _wheel_steps: int = 0
## `-- --hold <action>` (repeatable): `Input.action_press()`, not a synthetic InputEvent —
## lf_zoom_in/lf_zoom_out are read every frame via `Input.is_action_pressed()` (a held-key
## zoom, not an edge-triggered step like the board cursor's), which only the Input singleton's
## own tracked state satisfies. Dispatching an InputEventAction the way `--cursor`/`--press-at`
## do never reaches it, because that calls AnchorView's handler directly and never touches
## Input at all. Held for the rest of the run — a --shot process quits right after capturing,
## so there is no matching --release and none is needed.
var _hold_actions: Array[String] = []

## Verification-only bookkeeping for `--ability-at`: how long after a fire to keep printing
## `ABILITY-LIVE` samples (bus load, shots/sec, shutter queue) — long enough to cover
## overcharge's 7s duration and shutter's 5s at real time, with margin.
const ABILITY_LIVE_WINDOW_FRAMES: int = 600
var _last_ability_fire_frame: int = -ABILITY_LIVE_WINDOW_FRAMES - 1
## Rolling window for the shots/sec sample in ABILITY-LIVE, populated only when
## `--ability-at` is in play (see _ready()) — this is the only thing that reads sim.shot_fired
## in this file, and it is a pure counter, never a rule.
const RATE_WINDOW_S: float = 1.0
var _shot_times: Array[float] = []

const MENU_SCENE := "res://scenes/menu.tscn"


func _ready() -> void:
	RenderingServer.set_default_clear_color(Color(0.055, 0.078, 0.09))
	# The menu is the boot scene, so it has already resolved the CLI and the player's
	# choice into Progress. _setup_cli() still runs afterwards because a --shot run
	# reaches this scene directly and its arguments must still win.
	anchor_id = Progress.selected_anchor
	difficulty = Progress.difficulty
	_setup_cli()

	view.state_changed.connect(_on_state_changed)
	view.boot(anchor_id, difficulty)
	if _dump_placeholder:
		_dump_placeholder_colors()
	if _profile_frames > 0:
		# After boot(): view.glow_layer/combat_fx/fx_additive are set in each node's own
		# _ready(), which — per the existing note on child-vs-parent ready order elsewhere
		# in this file — has already run by the time this line does, but boot() is still the
		# natural "the level exists now" point to start sampling from.
		view.start_profiling()
		if view.glow_layer:
			view.glow_layer.start_profiling()
		if view.combat_fx:
			view.combat_fx.start_profiling()
		if view.fx_additive:
			view.fx_additive.start_profiling()
	if not _camera_cli.is_empty():
		# After boot(), which is what seeds the default framing this overrides — see
		# AnchorView.set_camera_override()'s own doc for why the order matters.
		view.set_camera_override(Vector2(_camera_cli["x"], _camera_cli["y"]), _camera_cli["zoom"])
	hud.bind(view)
	if _profile_frames > 0:
		# LF-150: the minimap (hud.gd's own Control child, scripts/minimap.gd) is HUD
		# content with its own _draw() — bind() is what creates it, so profiling can only
		# start after it, unlike the four board layers above which exist as soon as
		# view.boot() returns.
		hud.start_profiling()
	dialog.bind(view)
	# Verification-only: dialog_view.gd already connects this to show the line on screen, but
	# nothing prints that a trigger actually fired — several of them (first-leak, low-lives,
	# wards-half/full, wave-called, chain-high) had never been observed to fire at all. A
	# second listener on the same signal is exactly as safe as dialog_view.gd's own — the
	# signal is public and multi-listener by design — and costs one line per trigger, ever
	# (each fires once; see AnchorView._fire()).
	view.dialog_trigger.connect(_on_dialog_trigger)
	# Only when `--ability-at` is in play: feeds ABILITY-LIVE's shots-per-second sample.
	# Guarded so an ordinary run (the CLI array empty) never even connects the listener.
	if not _ability_at.is_empty() and view.sim != null:
		view.sim.shot_fired.connect(_on_shot_fired_debug)

	Audio.music(_bed_for(anchor_id))
	if _autoplay:
		view.autobuild()
	_place_requested()
	view.start()
	if _select_nth > 0 and view.sim != null and view.sim.placed.size() >= _select_nth:
		# PLC-01: placed records carry x/y floats, not a slot.
		var p0: Dictionary = view.sim.placed[_select_nth - 1]
		view.selected_slot = Vector2i(int(p0["x"]), int(p0["y"]))
	if _pick_tower != "":
		view.select(_pick_tower)      # applied after --select, so it can be seen to win
	if _scroll_steps != 0:
		hud.scroll_panels(_scroll_steps)
	for _i in range(_cursor_steps):
		_dispatch_action_press("lf_right")
	if _has_mouse_at:
		_synth_mouse_at(_mouse_at)
	if _has_drag:
		_synth_drag(_mouse_at if _has_mouse_at else get_viewport_rect().size * 0.5, _drag_delta)
	if _wheel_steps != 0:
		_synth_wheel(_mouse_at if _has_mouse_at else get_viewport_rect().size * 0.5, _wheel_steps)
	for a in _hold_actions:
		Input.action_press(a)
	if _speed_cli > 0.0:
		view.speed = _speed_cli
	for aid in _ability_ids:
		# view.abilities is untyped Variant (see anchor_view.gd's own doc on why) — accessed
		# dynamically here for the same reason hud.gd reaches through `view` throughout.
		if view.abilities != null:
			view.abilities.force_ready(aid)
			view.activate_ability(aid)
	if _chain_cli > 0:
		view.debug_set_chain(_chain_cli)
	if _open_pause:
		# The shot counter lives in this node's _process, and show_menu() pauses the
		# tree — so without this the screenshot never happens and the run hangs.
		process_mode = Node.PROCESS_MODE_ALWAYS
		pause_menu.show_menu()


## Every flag `_setup_cli()` reads, and its arity — see `scripts/cli_args.gd`'s own doc for
## the `int` vs `[min, max]` shape. Documentation only: `_setup_cli()` tokenises against
## `CliArgsScript.ALL_FLAGS` (the union across all four CLI-reading scripts), not this local
## copy, so a flag meant for `menu.gd`/`draft.gd`/`display_settings.gd` never spuriously
## warns here — see `ALL_FLAGS`'s own doc for why one shared registry is what
## the four independent parsers actually need. Every flag below is a **deprecated shim**
## over `--scenario` per PRC-12's own task list — kept working exactly as before, not
## removed, because CLAUDE.md, tools/check.py and tools/shot.py all still name them.
const KNOWN_FLAGS := {
	"--shot": [1, 2], "--a11y": 1, "--facings": 0, "--lanes": 0, "--heights": 0,
	"--dump-placeholder": 0,
	"--anchor": 1, "--autoplay": 0, "--paused": 0, "--select": 1, "--pick": 1,
	"--scroll": 1, "--cursor": 1, "--build": 1, "--difficulty": 1, "--speed": 1,
	"--ability": 1, "--ability-at": 2, "--press-at": 2, "--chain": 1, "--debrief-at": 1,
	"--camera": 3, "--mouse-at": 2, "--drag": 2, "--wheel": 1, "--hold": 1, "--profile": 1,
	"--scenario": 1,
}


func _setup_cli() -> void:
	var argv := OS.get_cmdline_user_args()
	var p := CliArgsScript.parse(argv, CliArgsScript.ALL_FLAGS)

	# --shot / --a11y / --facings / --lanes / --dump-placeholder — see each field's own
	# doc above for why each exists; only the parsing moved.
	_shot_path = CliArgsScript.str_val(p, "--shot", 0, _shot_path)
	_shot_at = CliArgsScript.int_val(p, "--shot", 1, _shot_at)
	_a11y_path = CliArgsScript.str_val(p, "--a11y", 0, _a11y_path)
	_dump_facings = CliArgsScript.has(p, "--facings")
	_dump_lanes = CliArgsScript.has(p, "--lanes")
	_dump_heights = CliArgsScript.has(p, "--heights")
	_dump_placeholder = CliArgsScript.has(p, "--dump-placeholder")

	if CliArgsScript.has(p, "--anchor"):
		anchor_id = CliArgsScript.str_val(p, "--anchor", 0, anchor_id)
	_autoplay = CliArgsScript.has(p, "--autoplay")
	_open_pause = CliArgsScript.has(p, "--paused")
	_select_nth = CliArgsScript.int_val(p, "--select", 0, _select_nth)
	_pick_tower = CliArgsScript.str_val(p, "--pick", 0, _pick_tower)
	_scroll_steps = CliArgsScript.int_val(p, "--scroll", 0, _scroll_steps)
	_cursor_steps = CliArgsScript.int_val(p, "--cursor", 0, _cursor_steps)
	# Repeatable: `--build mortar-emplacement --build flak-array` puts one of each on the
	# next two free slots — the only way to photograph the weapons autobuild never reaches.
	# See _place_requested().
	_build_ids = CliArgsScript.all_str(p, "--build", 0)
	if CliArgsScript.has(p, "--difficulty"):
		difficulty = CliArgsScript.str_val(p, "--difficulty", 0, difficulty)
	_speed_cli = CliArgsScript.float_val(p, "--speed", 0, _speed_cli)
	_ability_ids = CliArgsScript.all_str(p, "--ability", 0)
	for occ in p.get("--ability-at", []):
		_ability_at.append({"frame": int(occ[0]), "id": String(occ[1]), "fired": false})
	for occ in p.get("--press-at", []):
		_press_at.append({"frame": int(occ[0]), "action": String(occ[1]), "fired": false})
	_chain_cli = CliArgsScript.int_val(p, "--chain", 0, _chain_cli)
	_debrief_at = CliArgsScript.int_val(p, "--debrief-at", 0, _debrief_at)
	if CliArgsScript.has(p, "--camera"):
		_camera_cli = {
			"x": CliArgsScript.float_val(p, "--camera", 0),
			"y": CliArgsScript.float_val(p, "--camera", 1),
			"zoom": CliArgsScript.float_val(p, "--camera", 2),
		}
	if CliArgsScript.has(p, "--mouse-at"):
		_mouse_at = Vector2(CliArgsScript.float_val(p, "--mouse-at", 0),
			CliArgsScript.float_val(p, "--mouse-at", 1))
		_has_mouse_at = true
	if CliArgsScript.has(p, "--drag"):
		_drag_delta = Vector2(CliArgsScript.float_val(p, "--drag", 0),
			CliArgsScript.float_val(p, "--drag", 1))
		_has_drag = true
	_wheel_steps = CliArgsScript.int_val(p, "--wheel", 0, _wheel_steps)
	_hold_actions = CliArgsScript.all_str(p, "--hold", 0)
	_profile_frames = CliArgsScript.int_val(p, "--profile", 0, _profile_frames)

	if _profile_frames > 0:
		# Profiling needs the run to still be going at frame _profile_frames — extending
		# _shot_at (rather than adding a second, separate quit condition) reuses the single
		# existing pause-and-quit path in _process() unchanged, whether or not --shot was
		# also passed. See _process()'s own note on why render_loop_enabled also has to stay
		# on for the whole window when profiling, not just the last few frames before a shot.
		_shot_at = maxi(_shot_at, _profile_frames)

	if CliArgsScript.has(p, "--scenario"):
		_load_scenario(CliArgsScript.str_val(p, "--scenario", 0, ""))


## PRC-12: `-- --scenario <path>` loads and validates the file (`scripts/scenario.gd`), then
## folds its anchor/difficulty/ui_scale and its (at most one) shot/a11y/facings action into
## the exact same fields `_setup_cli()`'s legacy flags already populate — so the rest of
## this file's boot and capture logic runs completely unmodified for a scenario. A load
## failure (bad JSON, unknown schema, an unsupported action verb, a shot/a11y frame
## mismatch) is a **hard failure**, printed and non-zero, never a silent no-op — PRC-12's
## own acceptance criterion.
func _load_scenario(path: String) -> void:
	var s = ScenarioScript.new()
	if not s.load_file(path):
		push_error("scenario: %s" % s.error())
		print("SCENARIO-LOAD-FAIL %s" % s.error())
		call_deferred("_quit_with", 1)
		return
	_scenario = s
	anchor_id = s.anchor_id
	if s.difficulty != "":
		difficulty = s.difficulty
	if s.has_ui_scale:
		# Mirrors display_settings.gd's own `-- --ui-scale` handling (Display._read_cli()):
		# set the fields directly and mark CLI-locked rather than call set_ui_scale(), which
		# also persists to the save file — a verification run must not touch the player's
		# progress.json (see Display.settings_locked()'s own doc).
		Display.ui_scale = s.ui_scale
		Display._cli_locked = true
		Display.apply()
	if s.shot_frame >= 0:
		_shot_path = s.shot_path
		_shot_at = s.shot_frame
	if s.a11y_frame >= 0:
		_a11y_path = s.a11y_path
	if s.facings_frame >= 0:
		_dump_facings = true


func _quit_with(code: int) -> void:
	get_tree().quit(code)


func _place_requested() -> void:
	## Build each `--build <tower-id>` on the next free slot, funding it if the anchor's
	## starting money will not stretch.
	##
	## Granting funds is deliberate and it is why this is a verification hook and not a
	## cheat: the point is to photograph a weapon's projectile, and on anchor-06 the mortar
	## costs more than the anchor starts with, so "can the player afford it on wave one" is
	## a different question that would silently produce an empty board and a screenshot of
	## nothing. Everything else goes through the normal `sim.build_at()`, so slot occupancy,
	## bus load and the free-slot list all end up exactly as a real build leaves them. The
	## granted amount is printed, because a shot is only evidence if the state it captured
	## is known (LF-028).
	if _build_ids.is_empty() or view.sim == null:
		return
	for tid in _build_ids:
		_build_one(tid)
	view.queue_redraw()


## PRC-12: shared by `_place_requested()` (boot-time `--build`) and the scenario runner's
## `build` action — one place funding-grants and places a tower, so the two paths cannot
## silently diverge on what a verification build actually does to the sim. `slot_override`
## is `NO_SLOT` (view.NO_SLOT) for "next free slot", which is the only thing `--build` itself
## has ever offered; the scenario `build` verb may name a specific slot instead.
func _build_one(tid: String, slot_override: Vector2i = Vector2i(-999, -999)) -> void:
	if not Content.towers.has(tid):
		push_warning("main: --build %s is not a tower id" % tid)
		return
	var slot: Vector2i
	# PLC-01: view.sim.free_slots no longer exists -- available_slots() computes the
	# same list (anchor's authored slots minus whatever is occupied) on demand.
	var free: Array = view.sim.available_slots()
	if slot_override != Vector2i(-999, -999):
		if not Array(free).has(slot_override):
			push_warning("main: --build %s slot (%d,%d) is not free" % [tid, slot_override.x, slot_override.y])
			return
		slot = slot_override
	else:
		if free.is_empty():
			push_warning("main: --build %s has no free slot left" % tid)
			return
		slot = free[0]
	var cost := int(Content.tower(tid)["cost"])
	if cost > int(view.sim.funds):
		# Annotated, not inferred: `view.sim` is an untyped var, so `view.sim.funds` is a
		# Variant and `:=` cannot infer at PARSE time — which fails the whole script, so
		# menu.gd cannot load main.tscn and the game hangs on the menu instead of
		# reporting anything. Same trap that took the playfield down via fx_additive.gd.
		var granted: int = cost - int(view.sim.funds)
		view.sim.funds += granted
		print("BUILD-GRANT %s +%d" % [tid, granted])
	if view.sim.build_at(tid, float(slot.x), float(slot.y)):
		print("BUILD %s at (%d,%d)" % [tid, slot.x, slot.y])


## LF-139: `--press-at` and `--cursor` used to call `view._action_input(press)` directly,
## which reaches only `AnchorView`'s own input handling — an action handled by a *different*
## node's `_unhandled_input()` (`hud.gd`'s `lf_hud_toggle`, `lf_minimap_focus`,
## `lf_panel_up`/`down`) was invisible to it, which is why both needed their own bespoke
## boot flag (`--hud-hidden`, `--minimap-focus`/`--minimap-step`) instead of just being
## reachable through the general mechanism. `Input.parse_input_event()` is the real input
## pipeline every actual keypress/pad button goes through — it reaches every node's
## `_unhandled_input()` in the engine's own dispatch order, honouring `set_input_as_handled()`
## exactly as a real press would, so this one call now reaches anything a real key can reach.
##
## VERIFIED, not assumed (CLAUDE.md's own rule): `Input.parse_input_event()` queues the
## event rather than dispatching it inline, so its effect is not observable until the
## *following* frame, never the one the press was issued on. Measured with a scenario that
## pressed `lf_hud_toggle` at frame 10 and asserted `hud.hud_hidden` — `true` at frame 10
## itself failed (still `false`), `true` at frame 11 passed. A `--press-at N action` or a
## scenario `press` action therefore needs its assertion scheduled at frame N+1 or later,
## not frame N — documented in `data/schema/scenario.schema.json`'s own description of the
## `press`/`cursor` verbs so a scenario author does not have to rediscover this the same way.
func _dispatch_action_press(action: String) -> void:
	var press := InputEventAction.new()
	press.action = action
	press.pressed = true
	Input.parse_input_event(press)


## PRC-18: the `gamepad` scenario verb. `_dispatch_action_press()` above synthesizes an
## `InputEventAction` — already action-shaped, so it proves `Input.parse_input_event()`
## reaches every node's `_unhandled_input()` (LF-139) but says nothing about whether a
## genuinely DEVICE-shaped event (what a real controller actually produces) is matched
## against `project.godot`'s `[input]` bindings the same way. Probed standalone first
## (`--script`, no scene tree) before wiring this in: a synthetic `InputEventJoypadButton`/
## `InputEventJoypadMotion` on device 0 is recognised by `Input.is_action_pressed()` one
## frame after `parse_input_event()` — same one-frame queueing delay `press`/`cursor`
## already have (see that doc above) — and `Input.get_connected_joypads()` stayed EMPTY
## throughout, i.e. the InputMap matches a synthetic event by device index/button/axis
## regardless of whether a physical pad is attached. `device` is always 0, matching every
## `lf_*` gamepad binding in `project.godot`.
func _dispatch_gamepad_event(args: Array) -> void:
	var kind := String(args[0])
	if kind == "button":
		var btn := InputEventJoypadButton.new()
		btn.device = 0
		btn.button_index = int(args[1])
		btn.pressed = true
		Input.parse_input_event(btn)
	elif kind == "axis":
		var mot := InputEventJoypadMotion.new()
		mot.device = 0
		mot.axis = int(args[1])
		mot.axis_value = float(args[2]) if args.size() > 2 else 1.0
		Input.parse_input_event(mot)
	else:
		push_error("scenario: gamepad action has unknown kind %s (want button|axis)" % kind)
	print("GAMEPAD frame=%d kind=%s args=%s" % [_frame, kind, str(args)])


func _bed_for(aid: String) -> String:
	var act := int(Content.anchor(aid).get("act", 1))
	match act:
		2:
			return "A2-BLD_contract_terms.ogg"
		3:
			return "A3-BLD_circulatory.ogg"
		_:
			return "A1-BLD_carrier_signal.ogg"


## Frames of real drawing kept before the captured one. The board is immediate-mode and
## carries no frame-to-frame render state, so one warm frame would do; three is cheap
## insurance for anything that eases toward a target over a few frames rather than being
## computed outright, and it keeps the captured frame from ever being the first one drawn.
const SHOT_WARMUP_FRAMES: int = 3


func _process(_delta: float) -> void:
	## PRC-12: frame-time instrumentation for the `SCENARIO` summary — mean/p95/min/max
	## milliseconds of this node's own per-frame work (this file's scheduling, AnchorView's
	## sim step, every draw layer). `t0` is timestamped unconditionally (one syscall-free
	## `Time.get_ticks_usec()`) but only ever turned into a recorded sample by
	## `_record_frame_time()`, which is a no-op when no scenario is running — so this changes
	## nothing about an ordinary run or a legacy `--shot`.
	##
	## Deliberately still ONE function, not a wrapper `await`-ing a renamed body: an earlier
	## version of this split `_process()` into a thin timing wrapper around `_process_frame()`
	## and `await`ed the call, on the theory that `await`ing an already-resolved return value
	## is free. Empirically it was not safe here — every capture, including an ordinary
	## `--shot` with no scenario involved at all, stopped completing at all after the split.
	## Reverted; `_record_frame_time()` is called at each of this function's existing early
	## returns instead, which changes no control flow at all, only adds a call.
	## There is no separate physics step to time here — the whole sim and every draw layer
	## are driven from this single `_process()`, not from `_physics_process()` (this project
	## has none), so a scenario's frame-time report is one number, not the process/physics
	## pair the issue's task list imagined.
	var t0 := Time.get_ticks_usec()
	if _shot_taken:
		_record_frame_time(t0)
		return
	_frame += 1
	# Scheduled verification hooks run every frame regardless of whether a shot was
	# requested — both are no-ops (empty arrays) unless `--ability-at` or `--press-at` was
	# passed on the command line, so this changes nothing about a normal run or an ordinary
	# `--shot`.
	_process_ability_schedule()
	_process_press_schedule()
	_process_debrief_schedule()
	if _debrief_fired:
		# This node's scene is being replaced (see _process_debrief_schedule()) — every line
		# below this point belongs to the anchor being left, including the `render_loop_enabled`
		# recompute a few lines down, which would otherwise immediately re-disable the reset
		# that function just made *in this same frame* (its own write runs first, then this
		# function's un-guarded fall-through used to run right over it). Returning here is
		# what makes that reset actually stick for whatever scene loads next.
		_record_frame_time(t0)
		return
	_process_scenario_frame()
	if _scenario_finished:
		_record_frame_time(t0)
		return
	if _frame - _last_ability_fire_frame >= 0 \
			and _frame - _last_ability_fire_frame <= ABILITY_LIVE_WINDOW_FRAMES \
			and _frame % 15 == 0:
		_dump_ability_live()
	if _shot_path == "":
		if _profile_frames > 0 and _frame >= _profile_frames:
			# --profile with no --shot: nothing pauses the tree or wants a PNG, so this is
			# its own small quit path rather than routing through the --shot branch below.
			_shot_taken = true
			_print_profile_stats()
			get_tree().quit()
		_record_frame_time(t0)
		return
	# Draw only the frames that end up in the PNG.
	#
	# A capture at frame 1800 used to *render* 1800 frames to keep the 1800th. Nothing reads
	# the other 1799 — they exist so the sim can reach the state being photographed, and the
	# sim advances in AnchorView._process, which runs whether or not the frame is drawn.
	# Turning the render loop off until the last few frames therefore captures exactly the
	# same image for a fraction of the work, and it matters because verification now renders
	# on a software rasteriser (decision 052): a frame costs real time there, where on a GPU
	# it was free enough not to notice.
	#
	# Deliberately not `--fixed-fps 0` or a bigger DT: the sim is stepped at a fixed
	# AnchorSimScript.DT and the capture must stay reproducible from _shot_at alone (LF-029).
	# This changes how many frames are *painted*, never how many are *simulated*.
	#
	# --profile is the one thing that needs every frame actually drawn, not just the last
	# few before the shot: it measures each draw layer's own _draw() cost, which is zero on
	# a frame the render loop skipped. _setup_cli() already folded _profile_frames into
	# _shot_at, so this only has to keep the render loop on for the whole window.
	RenderingServer.render_loop_enabled = _profile_frames > 0 or _frame >= _shot_at - SHOT_WARMUP_FRAMES
	if _frame >= _shot_at:
		_shot_taken = true
		# Freeze before awaiting. --fixed-fps disables real-time sync, so the loop
		# spins as fast as it can and hundreds of frames elapse while this coroutine
		# is suspended — which advanced the sim past the frame that was asked for and
		# made the same command capture different states run to run. Pausing first
		# makes the captured content depend on _shot_at alone. This was LF-029.
		get_tree().paused = true
		await RenderingServer.frame_post_draw
		var img := get_viewport().get_texture().get_image()
		var err := img.save_png(_shot_path)
		print("SHOT %s err=%d %dx%d" % [_shot_path, err, img.get_width(), img.get_height()])
		# Alongside SHOT/FRAME/STATE/AUDIO/FACE, whether or not --camera was passed (CAM-03):
		# a report that does not say where the camera was is the problem this exists to fix.
		var cam: Dictionary = view.camera_state()
		print("CAMERA %.3f %.3f %.4f" % [cam["x"], cam["y"], cam["zoom"]])
		if _a11y_path != "":
			A11yProbe.write(_a11y_path, A11yProbe.capture(self, get_viewport(), {
				"scene": "game", "anchor": anchor_id, "shot": _shot_path,
				"camera": {"x": cam["x"], "y": cam["y"], "zoom": cam["zoom"]},
			}))
		var stats := _frame_stats(img)
		print("FRAME coverage=%.4f distinct=%d" % [stats["coverage"], stats["distinct"]])
		# What the sim actually reached by this frame. A screenshot is only evidence
		# if the state it captured is known and repeatable — see LF-028.
		print("STATE frame=%d sim_t=%.3f wave=%d phase=%s lives=%d leaks=%d funds=%d hover=(%d,%d)"
			% [_frame, view.sim_time(), view.wave_number(), view.phase(),
			   view.sim.lives, view.sim.leaks, view.sim.funds,
			   view.hovered_slot.x, view.hovered_slot.y])
		print("BUS load=%.1f cap=%.1f draw=%.1f penalty=%.3f overcharge=%s shutter=%s shutter_queue=%d"
			% [view.sim.bus_load(), view.sim.capacity(), view.sim.online_draw(),
			   view.sim.penalty_now(), view.sim.overcharge_active, view.sim.shutter_active,
			   view.shutter_queue_size()])
		_dump_veterancy()
		print("AUDIO %s" % Audio.report())
		if _dump_facings:
			# ART-01/LF-157: a split tower drawable carries "bucket"/"yaw_count" instead of
			# a degree "yaw" (16 buckets isn't a whole-degree quantity, see Iso.yaw_for_
			# heading()'s own assert) — print both forms so a placed tower's base and head
			# buckets are each visible on their own FACE line, not folded into one.
			for d in view.drawables():
				if d.has("bucket"):
					print("FACE %s %s bucket=%d/%d at=(%.0f,%.0f)"
						% [d["kind"], d["sprite"], d["bucket"], d["yaw_count"],
						   d["at"].x, d["at"].y])
				else:
					print("FACE %s %s yaw=%d at=(%.0f,%.0f)"
						% [d["kind"], d["sprite"], d["yaw"], d["at"].x, d["at"].y])
		if _dump_lanes:
			for d in view.drawables():
				if d["kind"] != "unit":
					continue
				var u: Dictionary = d["ref"]
				print("LANE %s lane=%d dist=%.2f at=(%.0f,%.0f)"
					% [d["sprite"], int(u["lane"]), float(u["dist"]), d["at"].x, d["at"].y])
		if _dump_heights:
			# TER-01: a screenshot shows *a* silhouette raised somewhere; this settles
			# which tile and by how many levels, and *where on screen* to look for the
			# proof — "measured from the PNG, not eyeballed" needs a pixel coordinate, not
			# just a level number. TILE carries `at` (the same pre-zoom local coordinate
			# FACE/LANE/DRAW below already report) for every board tile, not only the
			# non-zero ones the issue names as the minimum: a raised tile's offset can only
			# be measured against a *real*, actually-rendered level-0 tile's screen
			# position, and printing only the non-zero cells would throw away exactly the
			# reference points that comparison needs. DRAW lines cover every entity on
			# screen this frame, mirroring --facings/--lanes' own "walk drawables()" shape.
			var grid: Dictionary = view.sim.anchor.get("grid", {"w": 0, "h": 0})
			for ty in range(int(grid.get("h", 0))):
				for tx in range(int(grid.get("w", 0))):
					var lvl: int = view.height_at(tx, ty)
					var at: Vector2 = view.to_screen(Vector2(tx, ty))
					print("HEIGHT TILE %d %d %d at=(%.1f,%.1f)" % [tx, ty, lvl, at.x, at.y])
			for d in view.drawables():
				print("HEIGHT DRAW %s %s z=%.3f at=(%.0f,%.0f)"
					% [d["kind"], d["sprite"], float(d.get("z", 0.0)), d["at"].x, d["at"].y])
		if _profile_frames > 0:
			_print_profile_stats()
		if _scenario != null:
			# PRC-12: a scenario's `shot` action reuses this exact capture code (see
			# `_load_scenario()`'s own doc) rather than a second implementation — the only
			# thing scenario-specific left is which quit() this is, since a real STATE/BUS/
			# CAMERA/AUDIO report was just printed above regardless of which flow produced it.
			_finish_scenario(not _scenario.failed())
		else:
			get_tree().quit()
	_record_frame_time(t0)


func _record_frame_time(t0: int) -> void:
	if _scenario != null:
		_scenario.record_frame_time(float(Time.get_ticks_usec() - t0) / 1000.0)


func _frame_stats(img: Image) -> Dictionary:
	## How much of the frame is actually drawn on, and how varied it is.
	##
	## The boot check runs headless and only greps for script errors, so a scene that
	## builds no nodes passes it perfectly — which is how main.tscn stayed a childless
	## Node2D. A blank frame scores coverage ~0 and distinct ~1; the gate asserts on
	## these numbers so "renders nothing" is a red run rather than a green one.
	const CLEAR := Color(0.055, 0.078, 0.09)
	const STEP := 4                      # ~72k samples at 1440x810; plenty, and quick
	var lit := 0
	var total := 0
	var buckets := {}
	for y in range(0, img.get_height(), STEP):
		for x in range(0, img.get_width(), STEP):
			var c := img.get_pixel(x, y)
			total += 1
			if absf(c.r - CLEAR.r) + absf(c.g - CLEAR.g) + absf(c.b - CLEAR.b) > 0.02:
				lit += 1
			buckets[Vector3i(int(c.r * 16.0), int(c.g * 16.0), int(c.b * 16.0))] = true
	return {
		"coverage": float(lit) / maxf(float(total), 1.0),
		"distinct": buckets.size(),
	}


func _on_state_changed() -> void:
	## Record the clear exactly once, the first time the view reports `done`. The signal
	## fires on every wave boundary, so this has to be idempotent — a second call would
	## be harmless to Progress but would re-emit `changed` for nothing.
	if _recorded or view.phase() != "done":
		return
	_recorded = true
	Progress.mark_cleared(anchor_id, difficulty, view.sim.lives)
	print("CLEARED %s %s lives=%d" % [anchor_id, difficulty, view.sim.lives])


func _on_dialog_trigger(trigger: String) -> void:
	## Verification-only print, see the connection in _ready(). Every trigger the game fires
	## goes through AnchorView._fire(), so this one line covers first-leak, low-lives,
	## wards-half, wards-full, wave-called, chain-high, brownout, surge-ready and every
	## *-first cue without a bespoke hook per trigger.
	print("DIALOG-TRIGGER %s frame=%d sim_t=%.3f" % [trigger, _frame, view.sim_time()])


func _process_ability_schedule() -> void:
	if _ability_at.is_empty() or view == null or view.sim == null:
		return
	for entry in _ability_at:
		if bool(entry.get("fired", false)) or int(entry["frame"]) != _frame:
			continue
		entry["fired"] = true
		_fire_ability_at(String(entry["id"]))


func _fire_ability_at(id: String) -> void:
	## `--ability-at <frame> <id>`: force the ability ready (same skip `--ability` already
	## uses — abilities.gd's force_ready(), the sanctioned way to reach a state that needs
	## real charge/cooldown time nothing at --fixed-fps can produce) and fire it at a chosen,
	## reproducible frame instead of at boot. Prints enough state before and after that the
	## claims in data/tuning.json's own notes are checkable from the log alone: surge's
	## falloff (full at the ring, falloff_min at the mouth) and its pushback, and the bus
	## numbers overcharge/shutter actually move.
	if view.abilities == null:
		push_warning("main: --ability-at %s has no AbilityState to fire" % id)
		return
	var pre_load: float = view.sim.bus_load()
	var pre_cap: float = view.sim.capacity()
	var pre_draw: float = view.sim.online_draw()
	var pre_penalty: float = view.sim.penalty_now()
	print("ABILITY-AT id=%s frame=%d sim_t=%.3f load=%.1f cap=%.1f draw=%.1f penalty=%.3f"
		% [id, _frame, view.sim_time(), pre_load, pre_cap, pre_draw, pre_penalty])

	var before: Array = []
	if id == "surge":
		for i in range(view.sim.units.size()):
			var u: Dictionary = view.sim.units[i]
			if bool(u["alive"]):
				before.append({
					"i": i, "kind": String(u["kind"]["id"]),
					"dist": float(u["dist"]), "hp": float(u["hp"]),
					"shielded": bool(u["kind"].get("shielded", false)),
					"armour": float(u["kind"].get("armour", 0.0)),
				})
				var b: Dictionary = before[before.size() - 1]
				print("SURGE-BEFORE i=%d kind=%s dist=%.2f hp=%.1f shielded=%s armour=%.1f"
					% [b["i"], b["kind"], b["dist"], b["hp"], b["shielded"], b["armour"]])

	view.abilities.force_ready(id)
	var result: Dictionary = view.activate_ability(id)
	print("ABILITY-FIRED id=%s result=%s" % [id, result])

	var post_load: float = view.sim.bus_load()
	var post_cap: float = view.sim.capacity()
	var post_draw: float = view.sim.online_draw()
	var post_penalty: float = view.sim.penalty_now()
	print(("ABILITY-POST id=%s load=%.1f cap=%.1f draw=%.1f penalty=%.3f " +
		   "overcharge=%s shutter=%s shutter_queue=%d")
		% [id, post_load, post_cap, post_draw, post_penalty,
		   view.sim.overcharge_active, view.sim.shutter_active, view.shutter_queue_size()])

	if id == "surge" and view.sim.path_length > 0.0:
		var pl: float = view.sim.path_length
		for b in before:
			var i: int = int(b["i"])
			if i >= view.sim.units.size():
				continue
			var u: Dictionary = view.sim.units[i]
			var frac: float = clampf(float(b["dist"]) / pl, 0.0, 1.0)
			print(("SURGE-AFTER i=%d kind=%s dist_before=%.2f dist_after=%.2f " +
				   "hp_before=%.1f hp_after=%.1f alive=%s frac_along=%.3f shielded=%s")
				% [i, b["kind"], b["dist"], float(u["dist"]), b["hp"], float(u["hp"]),
				   bool(u["alive"]), frac, b["shielded"]])
	_last_ability_fire_frame = _frame


func _on_shot_fired_debug(_placed: Dictionary, _from_tile: Vector2, _to_tile: Vector2, _target_kind: Dictionary) -> void:
	_shot_times.append(view.sim_time())


func _prune_shot_log() -> void:
	var cutoff: float = view.sim_time() - RATE_WINDOW_S
	while _shot_times.size() > 0 and float(_shot_times[0]) < cutoff:
		_shot_times.pop_front()


func _dump_ability_live() -> void:
	## Verification-only: printed every 15 frames for ABILITY_LIVE_WINDOW_FRAMES after an
	## `--ability-at` fire, so overcharge's fire-rate effect and shutter's hold both show up as
	## a *change over time* in the log — a single before/after pair proves a toggle flipped,
	## not that it did anything continuous. shots_last_1s is a real count of AnchorSim's own
	## shot_fired signal, not a computed estimate.
	if view == null or view.sim == null:
		return
	_prune_shot_log()
	var nearest := -1.0
	for u in view.sim.units:
		if bool(u["alive"]) and (nearest < 0.0 or float(u["dist"]) < nearest):
			nearest = float(u["dist"])
	print(("ABILITY-LIVE frame=%d sim_t=%.3f shots_last_%.0fs=%d load=%.1f cap=%.1f " +
		   "penalty=%.3f overcharge=%s shutter=%s shutter_queue=%d nearest_alive_dist=%.2f")
		% [_frame, view.sim_time(), RATE_WINDOW_S, _shot_times.size(), view.sim.bus_load(),
		   view.sim.capacity(), view.sim.penalty_now(), view.sim.overcharge_active,
		   view.sim.shutter_active, view.shutter_queue_size(), nearest])


func _process_press_schedule() -> void:
	if _press_at.is_empty() or view == null or view.sim == null:
		return
	for entry in _press_at:
		if bool(entry.get("fired", false)) or int(entry["frame"]) != _frame:
			continue
		entry["fired"] = true
		var action := String(entry["action"])
		if action == "lf_target":
			var idx: int = view.placed_index_at(view.selected_slot)
			if idx >= 0:
				_dump_target_state("TARGET-BEFORE", idx)
		elif action == "lf_call_wave":
			print("CALL-WAVE-BEFORE frame=%d funds=%d lead_left=%.2f phase=%s"
				% [_frame, view.sim.funds, view.lead_left(), view.phase()])
		_dispatch_action_press(action)
		print("PRESS-AT frame=%d action=%s" % [_frame, action])
		if action == "lf_target":
			var idx2: int = view.placed_index_at(view.selected_slot)
			if idx2 >= 0:
				_dump_target_state("TARGET-AFTER", idx2)
		elif action == "lf_call_wave":
			print("CALL-WAVE-AFTER frame=%d funds=%d lead_left=%.2f phase=%s"
				% [_frame, view.sim.funds, view.lead_left(), view.phase()])


# ───────────────────────────────────────────────────────────── scenario ──
#
# PRC-12. `_scenario` is null on any run that did not pass `--scenario`, so every function
# here is a no-op guarded at the top — none of this changes an ordinary run or a legacy-flag
# `--shot`.

func _process_scenario_frame() -> void:
	if _scenario == null or _scenario_finished:
		return
	for a in _scenario.actions_at(_frame):
		_run_scenario_action(String(a["action"]), a["args"])
	# Annotated, not inferred: `_scenario` is untyped (see its declaration's own doc), so
	# `.run_asserts_at()` returns a Variant and `:=` cannot infer at PARSE time — which fails
	# the whole script to LOAD, not merely this call, and the symptom is a hang or a blank
	# frame at a completely different line (CLAUDE.md; the same trap `_place_requested()`'s
	# own `granted` annotation avoids for the same reason).
	var fail: Dictionary = _scenario.run_asserts_at(_frame, view, hud)
	if not fail.is_empty():
		print("ASSERT FAIL frame=%d expr=%s got=%s want=%s"
			% [int(fail["frame"]), String(fail["expr"]), str(fail["got"]), str(fail["expect"])])
		_finish_scenario(false)
		return
	# A scenario with no `shot` action has no other quit condition — main.gd's ordinary
	# `--shot` machinery is what ends every other run. See scenario.gd's own doc for why a
	# `shot` action's frame is instead where this always happens (the legacy capture code
	# quits right after taking it).
	if _scenario.shot_frame < 0 and _frame >= _scenario.max_frame():
		_finish_scenario(true)


func _run_scenario_action(verb: String, args: Array) -> void:
	## The action-verb dispatcher backing every entry in `data/schema/scenario.schema.json`
	## except `shot`/`a11y`/`facings`, which `_load_scenario()` folds into this file's
	## existing per-run fields instead (see that function's doc). Every branch here calls
	## the exact same method a legacy flag or a real player action already calls — this is a
	## frame-scheduled dispatcher, not a second implementation of what any of them do.
	match verb:
		"build":
			var tid := String(args[0])
			if args.size() >= 3:
				_build_one(tid, Vector2i(int(args[1]), int(args[2])))
			else:
				_build_one(tid)
			view.queue_redraw()
		"select":
			if view.sim != null:
				var n := int(args[0])
				if n > 0 and view.sim.placed.size() >= n:
					# PLC-01: placed records carry x/y floats, not a slot.
					var pn: Dictionary = view.sim.placed[n - 1]
					view.selected_slot = Vector2i(int(pn["x"]), int(pn["y"]))
		"pick":
			view.select(String(args[0]))
		"press":
			# LF-139: routed through the real input pipeline (see _dispatch_action_press()'s
			# own doc), so a `press` action reaches an action handled outside AnchorView —
			# hud.gd's lf_hud_toggle/lf_minimap_focus/lf_panel_up/down — exactly as
			# `--press-at` now does.
			_dispatch_action_press(String(args[0]))
			print("PRESS-AT frame=%d action=%s" % [_frame, String(args[0])])
		"gamepad":
			# PRC-18: unlike `press` above (action-shaped InputEventAction), this synthesizes
			# a real device-shaped InputEventJoypadButton/InputEventJoypadMotion — see
			# _dispatch_gamepad_event()'s own doc for why that distinction is the point.
			_dispatch_gamepad_event(args)
		"ability":
			_fire_ability_at(String(args[0]))
		"speed":
			view.speed = float(args[0])
		"scroll":
			hud.scroll_panels(int(args[0]))
		"cursor":
			for _i in range(int(args[0])):
				_dispatch_action_press("lf_right")
		"pause":
			process_mode = Node.PROCESS_MODE_ALWAYS
			pause_menu.show_menu()
		"camera":
			view.set_camera_override(Vector2(float(args[0]), float(args[1])), float(args[2]))
		_:
			# Unreachable in practice: scenario.gd's load_file() already rejects any verb
			# outside KNOWN_ACTIONS before this dispatcher ever runs. Guarded anyway rather
			# than trusted, per the same reasoning as every other "this should not happen"
			# branch in this file.
			push_error("scenario: action %s reached dispatch with no handler" % verb)


func _finish_scenario(passed: bool) -> void:
	if _scenario_finished:
		return
	_scenario_finished = true
	print(_scenario.summary_json())
	get_tree().quit(0 if passed else 1)


func _process_debrief_schedule() -> void:
	## LF-065 verification: `--debrief-at <frame>` presses `hud`'s DEBRIEF button at the
	## given frame, exactly as `_process_press_schedule()` presses an `lf_*` action — the
	## button itself is a `pressed` signal on a Control, not an input action, so it needs its
	## own hook rather than reusing `--press-at`. Prints the state that makes the transition
	## checkable (which anchor Progress still names, and that a next anchor exists) before
	## calling `hud._on_next()`, since the scene swap it triggers frees this node before
	## another print could run.
	if _debrief_fired or _debrief_at < 0 or _frame != _debrief_at or view == null:
		return
	_debrief_fired = true
	print(("DEBRIEF-PRESS frame=%d anchor=%s progress_selected=%s phase=%s")
		% [_frame, anchor_id, Progress.selected_anchor, view.phase()])
	# `RenderingServer.render_loop_enabled` is a global engine switch this same file turns
	# off below (in the ordinary --shot path) to skip painting every frame before the one
	# that gets captured — it is not scoped to this scene, so it survives a scene change.
	# Left off, whatever scene loads next can `await RenderingServer.frame_post_draw`
	# forever, because nothing ever draws a frame to post. Found live: a chained
	# `--debrief-at`+`--shot` run hung the full --timeout waiting on exactly that await in
	# draft.gd. Restoring it here is what a real player's transition already gets for free
	# (render_loop_enabled is only ever turned off on a --shot run to begin with).
	RenderingServer.render_loop_enabled = true
	hud._on_next()
	print("DEBRIEF-PRESSED frame=%d progress_selected_after=%s" % [_frame, Progress.selected_anchor])


func _dump_target_state(tag: String, idx: int) -> void:
	## Verification-only: what the selected emplacement's targeting priority (data/tuning.json
	## `targeting`) actually resolves to right now — its mode, what it is aiming at, and every
	## alive unit within its range, so the aim can be checked against the mode's own rule
	## (furthest along for "first", nearest hp for "weakest", ...) rather than trusted.
	var p: Dictionary = view.sim.placed[idx]
	var tw: Dictionary = p["tower"]
	var sx: float = float(p["x"])
	var sy: float = float(p["y"])
	var rng: float = float(tw["range"])
	var aim: Variant = p.get("aim", null)
	var mode: String = String(p.get("target_mode", Tuning.targeting_default()))
	print("%s frame=%d slot=(%d,%d) mode=%s aim=%s" % [tag, _frame, int(sx), int(sy), mode, str(aim)])
	for u in view.sim.units:
		if not bool(u["alive"]):
			continue
		var at: Vector2 = view.sim.point_at(int(u["lane"]), float(u["dist"]))
		var dx: float = sx - at.x
		var dy: float = sy - at.y
		if dx * dx + dy * dy <= rng * rng:
			print("  %s-CANDIDATE kind=%s dist=%.2f hp=%.1f" % [tag, String(u["kind"]["id"]), float(u["dist"]), float(u["hp"])])


func _dump_veterancy() -> void:
	## Verification-only, printed alongside the STATE line at the captured shot frame: every
	## placed emplacement's kill count, the rank it resolves to under data/tuning.json
	## `veterancy`'s thresholds, and the multipliers that rank actually carries — so the amber
	## pip on a turret's base (visible in the screenshot) has printed state behind it rather
	## than being taken on faith.
	if view.sim == null:
		return
	var ranks: Array = view.sim.veterancy_ranks()
	if ranks.is_empty():
		return
	for p in view.sim.placed:
		var kills: int = int(p.get("kills", 0))
		var best: Dictionary = {}
		for r in ranks:
			if kills >= int(r.get("kills", 0)):
				best = r
		var slot := Vector2i(int(p["x"]), int(p["y"]))  # PLC-01: x/y, not slot
		var base_range: float = float(p["tower"]["range"])
		print("VET slot=(%d,%d) tower=%s kills=%d rank=%s damage_mult=%.2f range_mult=%.2f base_range=%.2f"
			% [slot.x, slot.y, String(p["tower"]["id"]), kills, String(best.get("name", "")),
			   float(best.get("damage_mult", 1.0)), float(best.get("range_mult", 1.0)), base_range])


func _dump_placeholder_colors() -> void:
	## LF-046 verification: one line per enemy kind Content knows, plus one synthetic
	## unrecognised faction, proving `_draw_unit()`'s placeholder colour no longer collapses
	## Sable Reach and Hollow into the same amber, and its radius no longer saturates every
	## enemy at or above Warden Heavy's 220 hp to the same size.
	for id in Content.enemies.keys():
		var kind: Dictionary = Content.enemies[id]
		var faction := String(kind.get("faction", ""))
		var hp := float(kind.get("hp", 0.0))
		var col: Color = view.placeholder_color(faction)
		var r: float = view.placeholder_radius(hp)
		print("PLACEHOLDER id=%s faction=%s hp=%.0f color=(%.2f,%.2f,%.2f) radius=%.2f"
			% [id, faction, hp, col.r, col.g, col.b, r])
	var unknown_col: Color = view.placeholder_color("some-fifth-faction")
	print("PLACEHOLDER id=(synthetic) faction=some-fifth-faction color=(%.2f,%.2f,%.2f)"
		% [unknown_col.r, unknown_col.g, unknown_col.b])


func _print_profile_stats() -> void:
	## `-- --profile <frames>` (CAM-06/CAM-07): mean/p95 milliseconds per draw layer over
	## every frame actually rendered since boot(), plus how many times drawables() ran its
	## build body — CAM-07's own acceptance criterion is that number reading 1-per-frame,
	## not a feeling that sharing helped.
	_print_layer_stats("AnchorView._draw", view.profile_stats())
	if view.glow_layer:
		_print_layer_stats("GlowLayer._draw", view.glow_layer.profile_stats())
	if view.fx_additive:
		_print_layer_stats("FxAdditive._draw", view.fx_additive.profile_stats())
	if view.combat_fx:
		_print_layer_stats("CombatFx._draw", view.combat_fx.profile_stats())
	# LF-150: the minimap is HUD content, not a board layer, but it has its own _draw()
	# and its own performance budget — see hud.gd's start_profiling()/profile_stats().
	_print_layer_stats("Hud.Minimap._draw", hud.profile_stats())
	print("DRAWABLES rebuilds=%d" % view.drawables_rebuild_count())


func _print_layer_stats(label: String, stats: Dictionary) -> void:
	print("PROFILE layer=%s n=%d mean_ms=%.4f p95_ms=%.4f"
		% [label, int(stats["n"]), float(stats["mean"]), float(stats["p95"])])


func _unhandled_input(event: InputEvent) -> void:
	# Bound through the input map so a gamepad reaches the pause menu too (LF-010).
	if event.is_action_pressed("lf_pause"):
		# Pause, rather than leave. A --shot run has no one to pause for and must
		# still exit on its own, which is what _shot_path tests.
		if _shot_path != "":
			get_tree().quit()
		else:
			pause_menu.toggle()


# ────────────────────────────────────────────────────────── camera CLI ──
#
# `--mouse-at`/`--drag`/`--wheel`: synthetic mouse input for CAM-01's pointer-driven paths
# (edge-scroll, zoom-about-cursor, middle-drag pan), which --fixed-fps otherwise has nobody
# to produce. `Viewport.warp_mouse()` — not `Input.warp_mouse()`, which takes *window/screen*
# pixels — moves the real tracked pointer in this viewport's own (stretched, logical) space,
# the same space `get_global_mouse_position()` and every `InputEvent.position` here use, so
# later per-frame reads (edge-scroll's `get_global_mouse_position()`) keep seeing it exactly
# where this function put it. The synthetic events alongside it are what AnchorView's own
# input handlers react to, exactly as `--cursor`'s synthetic `lf_right` presses above call
# `view._action_input()` directly rather than going through the engine's input queue.

func _synth_mouse_at(p: Vector2) -> void:
	get_viewport().warp_mouse(p)
	var m := InputEventMouseMotion.new()
	m.position = p
	m.global_position = p
	m.relative = Vector2.ZERO
	view._unhandled_input(m)


func _synth_drag(start: Vector2, delta: Vector2) -> void:
	get_viewport().warp_mouse(start)
	var press := InputEventMouseButton.new()
	press.button_index = MOUSE_BUTTON_MIDDLE
	press.pressed = true
	press.position = start
	press.global_position = start
	view._unhandled_input(press)

	var move := InputEventMouseMotion.new()
	move.position = start + delta
	move.global_position = start + delta
	move.relative = delta
	view._unhandled_input(move)
	get_viewport().warp_mouse(start + delta)

	var release := InputEventMouseButton.new()
	release.button_index = MOUSE_BUTTON_MIDDLE
	release.pressed = false
	release.position = start + delta
	release.global_position = start + delta
	view._unhandled_input(release)
	print("DRAG from=(%.0f,%.0f) delta=(%.0f,%.0f)" % [start.x, start.y, delta.x, delta.y])


func _synth_wheel(p: Vector2, steps: int) -> void:
	get_viewport().warp_mouse(p)
	var dir := MOUSE_BUTTON_WHEEL_UP if steps > 0 else MOUSE_BUTTON_WHEEL_DOWN
	for _i in range(absi(steps)):
		var tick := InputEventMouseButton.new()
		tick.button_index = dir
		tick.pressed = true
		tick.position = p
		tick.global_position = p
		view._unhandled_input(tick)
	print("WHEEL at=(%.0f,%.0f) steps=%d" % [p.x, p.y, steps])
