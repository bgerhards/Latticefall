extends Node
## Autoload `Display`. Everything about how the game meets the player's monitor.
##
## Latticefall shipped with the window nailed to 1440x810 in project.godot and no way to
## change it. That is fine on the machine it was built on and wrong everywhere else: a
## 4K laptop panel renders it as a postage stamp, a 1366x768 screen cannot fit it at all,
## and nobody could go fullscreen. Those are ordinary display options, but they are also
## the coarse half of the legibility problem — the fine half is the type ladder in
## ui_theme.gd, and neither one substitutes for the other.
##
## `ui_scale` is the accessibility control specifically. It sets the window's
## `content_scale_factor`, which multiplies the whole canvas, so the logical viewport
## *shrinks* as the scale rises: at 1.5 the interface is laid out in 1280x720 of design
## space and drawn at 1920x1080. That only works because the HUD derives its edges and its
## margins from the live viewport rect rather than from a hardcoded 1920, and because its
## two instrument panels scroll — see hud.gd. WCAG 2.1 SC 1.4.4 asks for 200% without loss
## of content, so the range goes to 2.0 and `tools/validate/a11y.py --shot` proves nothing
## clips at the top of it.
##
## Settings persist through Progress, next to volume, because they are the same kind of
## thing: a player preference that is not game state.

signal changed

## Godot's WINDOW_MODE_FULLSCREEN is a borderless window at desktop resolution; the
## exclusive mode is a separate enum value. Both are offered — exclusive can be lower
## latency, borderless alt-tabs cleanly — because which one behaves is driver-dependent
## and no default is right for every machine.
const MODE_WINDOWED := "windowed"
const MODE_BORDERLESS := "borderless"
const MODE_FULLSCREEN := "fullscreen"
const MODES := [MODE_WINDOWED, MODE_BORDERLESS, MODE_FULLSCREEN]

const MODE_LABELS := {
	MODE_WINDOWED: "WINDOWED",
	MODE_BORDERLESS: "BORDERLESS",
	MODE_FULLSCREEN: "FULLSCREEN",
}

## 16:9 through to 16:10 and 4:3, so an old panel is not excluded. The stretch mode is
## `canvas_items` with `expand`, so a non-16:9 aspect gains or loses board rather than
## letterboxing — which is why the HUD must not assume 1920x1080.
const RESOLUTIONS: Array[Vector2i] = [
	Vector2i(1280, 720), Vector2i(1366, 768), Vector2i(1440, 810),
	Vector2i(1600, 900), Vector2i(1920, 1080), Vector2i(2560, 1440),
	Vector2i(3840, 2160),
	Vector2i(1280, 800), Vector2i(1680, 1050), Vector2i(1920, 1200),
	Vector2i(1024, 768), Vector2i(1280, 1024),
]

## The ceiling is 200%, which is what WCAG 2.1 SC 1.4.4 asks for.
##
## `content_scale_factor` divides the logical viewport: the project renders a fixed
## 1920x1080 design space, so at 125% the interface is laid out in 1536x864, at 150% in
## 1280x720 and at 200% in 960x540. The instrument column's content is 893 px at its most
## crowded — anchor-24 unlocks nine emplacements, the datasheet reserves eight rows for the
## longest weapon sheet and the note six wrapped lines — with 98 px of pinned controls under
## it, and the threat panel is another 455 beside it. At 960x540 that is 656,000 px² of
## instrument in a 518,000 px² viewport, so no arrangement of fixed panels reaches 200%:
## this was capped at 125% until the panels could reflow, which is decision 046 and LF-045.
##
## They reflow now. Both instrument panels scroll vertically with their controls pinned
## outside the scroll region, so no control ever leaves the screen, and
## `tools/validate/a11y.py` proves it at 200% on anchor-24 as part of the gate. Decision 048.
const UI_SCALES: Array[float] = [1.0, 1.1, 1.25, 1.5, 1.75, 2.0]

const GLOW_LABELS := {0.0: "OFF", 0.5: "REDUCED", 1.0: "FULL"}
const GLOW_LEVELS: Array[float] = [0.0, 0.5, 1.0]

## Screen-shake accommodation, same shape as glow's: `shake` already existed as a field
## (`Display.shake` above, `set_shake()` below) with nowhere in the options panel to reach
## it — LF-063. Reusing GLOW_LEVELS' 0/0.5/1.0 ladder rather than inventing a new one: the
## two accommodations read the same way (off / reduced / full) and a player who wants one
## reduced usually wants the other reduced too.
const SHAKE_LABELS := {0.0: "OFF", 0.5: "REDUCED", 1.0: "FULL"}
const SHAKE_LEVELS: Array[float] = [0.0, 0.5, 1.0]

var window_mode: String = MODE_WINDOWED
var resolution: Vector2i = Vector2i(1440, 810)
var vsync: bool = true
var max_fps: int = 0                  ## 0 = uncapped
var ui_scale: float = 1.0
## Emissive layer strength. Not only a graphics knob: the additive glow is the brightest
## thing on screen, and turning it down is the obvious accommodation for light sensitivity.
var glow: float = 1.0
## Screen-shake multiplier. combat_fx.gd's trauma sources (heavy kills, mortar impacts,
## leaks) still fire at 0 — this scales how much of it reaches AnchorView.position, down to
## nothing for a player who finds camera shake disorienting. Reached from the options panel's
## SCREEN SHAKE row (options_menu.gd), same cycler shape as EMISSIVE GLOW. Closes LF-063.
var shake: float = 1.0

## CAM-01: pointer-near-strip-edge auto-scroll. Defaults on, but toggleable — an always-on
## edge scroll is hostile to a trackpad (the pointer rests near an edge constantly while
## moving to it) and to a screen magnifier (the magnified viewport is itself near an edge
## most of the time). See anchor_view.gd's `_edge_scroll()`.
var edge_scroll: bool = true

var _headless: bool = false
## Set when the command line dictated the display state. The save is then not allowed to
## overwrite it: a verification run that asks for 200% and silently gets the developer's
## saved 100% back is a run that proves nothing.
var _cli_locked: bool = false
## `-- --quiet-window`, paired with the engine's builtin `--position <X>,<Y>` (applied at
## window creation, before any script runs — see main.gd's `--shot` doc). GL Compatibility
## reads back nothing headless (LF-061), so a self-screenshot needs a real GPU-backed
## window, but verification must not steal the owner's cursor or sit over their desktop.
## This is the owner of window mode/flags/position, so it is where `--quiet-window` is
## read and acted on, not main.gd — by the time main.tscn's own _ready runs, the window
## has already been shown once as the menu's boot scene.
var quiet_window: bool = false


func settings_locked() -> bool:
	return _cli_locked


func _ready() -> void:
	# A headless or --fixed-fps verification run must not have the player's saved window
	# mode applied to it: the gate compares screenshots against expected dimensions, and a
	# saved fullscreen would silently change every one of them.
	_headless = DisplayServer.get_name() == "headless"
	_read_cli()
	apply()


const CliArgsScript := preload("res://scripts/cli_args.gd")
const KNOWN_FLAGS := {"--ui-scale": 1, "--display-defaults": 0, "--quiet-window": 0}


func _read_cli() -> void:
	## `-- --ui-scale 2.0` forces a scale for verification without touching the save. The
	## a11y clipping check needs to reach 200% on demand; asking a human to set it in the
	## options screen first is exactly the kind of step that never gets run.
	##
	## PRC-12: tokenised through `scripts/cli_args.gd` now, the shared parser also used by
	## `main.gd`/`menu.gd`/`draft.gd` — this file used to walk `OS.get_cmdline_user_args()`
	## by hand in its own `if/elif` dialect, one of four that had all drifted slightly.
	var p := CliArgsScript.parse(OS.get_cmdline_user_args(), CliArgsScript.ALL_FLAGS)
	if CliArgsScript.has(p, "--ui-scale"):
		ui_scale = clampf(CliArgsScript.float_val(p, "--ui-scale", 0, ui_scale), 0.5, 3.0)
		_cli_locked = true
	if CliArgsScript.has(p, "--display-defaults"):
		_cli_locked = true
		window_mode = MODE_WINDOWED
		resolution = Vector2i(1440, 810)
		ui_scale = 1.0
		glow = 1.0
	if CliArgsScript.has(p, "--quiet-window"):
		quiet_window = true


# ───────────────────────────────────────────────────────────────── apply ──

func apply() -> void:
	## Idempotent, and safe to call from a settings row's `value_changed`.
	var win := get_window()
	if win != null:
		win.content_scale_factor = ui_scale
	if _headless:
		changed.emit()
		return

	if quiet_window:
		# NO_FOCUS stops the window ever taking keyboard focus; MOUSE_PASSTHROUGH stops it
		# eating clicks meant for whatever is under it. Re-applied on every apply() (not
		# just once at boot) so nothing later in this function can silently reactivate it.
		DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_NO_FOCUS, true)
		DisplayServer.window_set_flag(DisplayServer.WINDOW_FLAG_MOUSE_PASSTHROUGH, true)

	Engine.max_fps = max_fps
	DisplayServer.window_set_vsync_mode(
		DisplayServer.VSYNC_ENABLED if vsync else DisplayServer.VSYNC_DISABLED)

	match window_mode:
		MODE_FULLSCREEN:
			DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_EXCLUSIVE_FULLSCREEN)
		MODE_BORDERLESS:
			DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_FULLSCREEN)
		_:
			# Order matters: leave fullscreen before resizing, or the size is applied to a
			# window that is about to be replaced by the desktop resolution and is lost.
			if DisplayServer.window_get_mode() != DisplayServer.WINDOW_MODE_WINDOWED:
				DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_WINDOWED)
			DisplayServer.window_set_size(_fitted(resolution))
			# Centring would drag the window back over the screen that --quiet-window (and
			# the caller's --position, applied by the engine before any script ran) just
			# parked it off of.
			if not quiet_window:
				_centre()
	changed.emit()


func _fitted(want: Vector2i) -> Vector2i:
	## Never open a window bigger than the screen it is on — an oversized window puts its
	## own title bar off the top of the display, where it cannot be dragged back.
	var screen := DisplayServer.screen_get_usable_rect(
		DisplayServer.window_get_current_screen())
	return Vector2i(mini(want.x, screen.size.x), mini(want.y, screen.size.y))


func _centre() -> void:
	var screen := DisplayServer.screen_get_usable_rect(
		DisplayServer.window_get_current_screen())
	var size := DisplayServer.window_get_size()
	DisplayServer.window_set_position(
		screen.position + (screen.size - size) / 2)


# ──────────────────────────────────────────────────────────────── mutate ──

func set_window_mode(m: String) -> void:
	window_mode = m
	_persist()


func set_resolution(r: Vector2i) -> void:
	resolution = r
	window_mode = MODE_WINDOWED       # picking a size means wanting a window that size
	_persist()


func set_vsync(on: bool) -> void:
	vsync = on
	_persist()


func set_max_fps(fps: int) -> void:
	max_fps = fps
	_persist()


func set_ui_scale(s: float) -> void:
	ui_scale = s
	_persist()


func set_glow(g: float) -> void:
	glow = g
	_persist()


func set_shake(s: float) -> void:
	shake = s
	_persist()


func set_edge_scroll(on: bool) -> void:
	edge_scroll = on
	_persist()


func _persist() -> void:
	apply()
	Progress.save_state()


func available_resolutions() -> Array[Vector2i]:
	## Only the sizes that actually fit this monitor, plus whatever is currently set so the
	## list never fails to show the player's own choice.
	if _headless:
		return RESOLUTIONS
	var screen := DisplayServer.screen_get_usable_rect(
		DisplayServer.window_get_current_screen()).size
	var out: Array[Vector2i] = []
	for r in RESOLUTIONS:
		if (r.x <= screen.x and r.y <= screen.y) or r == resolution:
			out.append(r)
	out.sort_custom(func(a, b): return a.x * a.y < b.x * b.y)
	return out


func report() -> String:
	return "display %s %dx%d ui %.2fx glow %.1f shake %.1f edge_scroll %s" % [
		window_mode, resolution.x, resolution.y, ui_scale, glow, shake,
		"on" if edge_scroll else "off"]
