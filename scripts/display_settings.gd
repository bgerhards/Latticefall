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
## space and drawn at 1920x1080. That only works because the HUD derives its right and
## bottom edges from the live viewport rect rather than from a hardcoded 1920 — see
## hud.gd. WCAG 2.1 SC 1.4.4 asks for 200% without loss of content, so the range goes to
## 2.0 and `tools/validate/a11y.py --shot` proves nothing clips at the top of it.
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

## The ceiling is 125%, and it is measured rather than chosen.
##
## `content_scale_factor` divides the logical viewport: the project renders a fixed
## 1920x1080 design space, so at 125% the interface is laid out in 1536x864 and at 150% in
## 1280x720. The instrument column needs about 847 px of height at its most crowded —
## anchor-24 unlocks nine emplacements, and the datasheet reserves eight rows for the
## longest weapon sheet — which 864 clears and 720 does not. Above 125% the SELL, UPGRADE
## and power controls leave the bottom of the screen, which
## `tools/validate/a11y.py --shot` reports as a clipping failure.
##
## WCAG 2.1 SC 1.4.4 asks for 200% without loss of content and this does not reach it. The
## honest position is that the type ladder did most of the work instead — body text went
## from 11-13 px to 16-18 px, a 45% increase before this control is touched at all — and
## that going further needs an instrument column that can reflow rather than a bigger
## multiplier on one that cannot. That is LF-045.
const UI_SCALES: Array[float] = [1.0, 1.1, 1.25]
## Measured minimum logical height of the instrument column at its most crowded. Any scale
## whose resulting viewport is shorter than this clips the emplacement controls.
const MIN_LOGICAL_HEIGHT := 847.0

const GLOW_LABELS := {0.0: "OFF", 0.5: "REDUCED", 1.0: "FULL"}
const GLOW_LEVELS: Array[float] = [0.0, 0.5, 1.0]

var window_mode: String = MODE_WINDOWED
var resolution: Vector2i = Vector2i(1440, 810)
var vsync: bool = true
var max_fps: int = 0                  ## 0 = uncapped
var ui_scale: float = 1.0
## Emissive layer strength. Not only a graphics knob: the additive glow is the brightest
## thing on screen, and turning it down is the obvious accommodation for light sensitivity.
var glow: float = 1.0

var _headless: bool = false
## Set when the command line dictated the display state. The save is then not allowed to
## overwrite it: a verification run that asks for 200% and silently gets the developer's
## saved 100% back is a run that proves nothing.
var _cli_locked: bool = false


func settings_locked() -> bool:
	return _cli_locked


func _ready() -> void:
	# A headless or --fixed-fps verification run must not have the player's saved window
	# mode applied to it: the gate compares screenshots against expected dimensions, and a
	# saved fullscreen would silently change every one of them.
	_headless = DisplayServer.get_name() == "headless"
	_read_cli()
	apply()


func _read_cli() -> void:
	## `-- --ui-scale 2.0` forces a scale for verification without touching the save. The
	## a11y clipping check needs to reach 200% on demand; asking a human to set it in the
	## options screen first is exactly the kind of step that never gets run.
	var argv := OS.get_cmdline_user_args()
	for i in range(argv.size()):
		if argv[i] == "--ui-scale" and i + 1 < argv.size():
			ui_scale = clampf(float(argv[i + 1]), 0.5, 3.0)
			_cli_locked = true
		elif argv[i] == "--display-defaults":
			_cli_locked = true
			window_mode = MODE_WINDOWED
			resolution = Vector2i(1440, 810)
			ui_scale = 1.0
			glow = 1.0


# ───────────────────────────────────────────────────────────────── apply ──

func apply() -> void:
	## Idempotent, and safe to call from a settings row's `value_changed`.
	var win := get_window()
	if win != null:
		win.content_scale_factor = ui_scale
	if _headless:
		changed.emit()
		return

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
	return "display %s %dx%d ui %.2fx glow %.1f" % [
		window_mode, resolution.x, resolution.y, ui_scale, glow]
