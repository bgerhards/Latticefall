extends Control
## Title screen and anchor select. The scene the game boots into.
##
## The anchor grid is built in code because it is a view of data: 24 anchors, their act,
## their title and their clear state all come from res://data and Progress. Authoring 24
## buttons in a .tscn would be 24 places to forget to update.
##
## CLI handling lives here rather than in main.gd because this is now the boot scene:
## `-- --anchor anchor-07` or `-- --shot …` must reach the game without a human pressing
## anything, or the gate's self-screenshot has nothing to screenshot.

const GAME_SCENE := "res://scenes/main.tscn"
const DRAFT_SCENE := "res://scenes/draft.tscn"
const DIFFICULTIES := ["standard", "hard", "brutal"]
## One anchor button, and the gap between two of them. Named because the grid's column
## count is now solved from them against the live viewport width rather than fixed at the
## eight that only ever fitted a 1920 px design space.
const ANCHOR_W := 172.0
const ANCHOR_H := 64.0
const GRID_GAP := 10.0
## Act titles come from docs/STORY.md. They are the only place in the UI that names an
## act, so they carry the tone the anchor numbers cannot.
const ACT_TITLES := {
	1: "ACT I  ·  DEAD AIR",
	2: "ACT II  ·  SALVAGE RIGHTS",
	3: "ACT III  ·  THE HOLLOW",
}

## Colours and sizes come from `Ui` — they are accessibility policy, and policy that is
## copy-pasted into five scripts cannot be audited or corrected in one place.
const C_VERD := Ui.C_VERD
const C_AMBER := Ui.C_AMBER
const C_MUTED := Ui.C_MUTED
const C_DIM := Ui.C_DIM
const C_BONE := Ui.C_BONE

var _difficulty_buttons: Array[Button] = []
var _grid: GridContainer
var _detail: Label
var _carrying: Label
var _options: OptionsMenu
var _body: Control

## `-- --shot-menu <path> [frame]` screenshots the menu and quits, so the gate can
## assert that the boot scene draws something. Same reasoning as main.gd's --shot: a
## check that greps for script errors passes happily on a screen that renders nothing.
var _menu_shot: String = ""
var _menu_shot_at: int = 30
var _menu_frame: int = 0
## `-- --a11y <path>`, as main.gd. Written on the frame the menu screenshot is taken.
var _a11y_path: String = ""
var _open_options_at_boot := false


func _ready() -> void:
	if _boot_from_cli():
		return
	RenderingServer.set_default_clear_color(Color(0.055, 0.078, 0.09))
	_build()
	Progress.changed.connect(_refresh)
	Audio.music("A1-BLD_carrier_signal.ogg")


const CliArgsScript := preload("res://scripts/cli_args.gd")
## PRC-12: `--draft` forwards its own trailing flags (`--seed`, `--draft-lives`, `--shot`,
## `--a11y`) straight to `draft.gd`'s own parser rather than reading them here — this file
## only needs to know that `--draft` itself takes none, and that its *own* `--shot`/
## `--anchor`/`--difficulty` still have to be readable before deferring, since Progress is
## set from here.
const KNOWN_FLAGS := {
	"--anchor": 1, "--difficulty": 1, "--draft": 0, "--shot": [1, 2],
	"--shot-menu": [1, 2], "--a11y": 1, "--options": 0,
}


func _boot_from_cli() -> bool:
	## `--anchor`/`--shot` skip the menu entirely. Verification runs the real game, not
	## a menu screenshot, and a player who launches with an explicit anchor means it.
	var argv := OS.get_cmdline_user_args()
	var p := CliArgsScript.parse(argv, CliArgsScript.ALL_FLAGS)
	if CliArgsScript.has(p, "--anchor"):
		Progress.selected_anchor = CliArgsScript.str_val(p, "--anchor", 0, Progress.selected_anchor)
	if CliArgsScript.has(p, "--difficulty"):
		Progress.difficulty = CliArgsScript.str_val(p, "--difficulty", 0, Progress.difficulty)
	if CliArgsScript.has(p, "--draft"):
		# `-- --draft [--anchor id] [--difficulty d] [--seed n] [--draft-lives L S] --shot …`
		# opens the debrief/recovery screen directly. It is otherwise unreachable at
		# `--fixed-fps` — reaching it for real needs a played and won anchor — and a screen
		# nobody has screenshotted is a screen nobody has looked at. project.godot's
		# run/main_scene stays menu.tscn, so this scene is where the flag has to be caught.
		call_deferred("_go_draft")
		return true
	if CliArgsScript.has(p, "--anchor") or CliArgsScript.has(p, "--shot") \
			or CliArgsScript.has(p, "--scenario"):
		# PRC-12: `--scenario <path>` names its own anchor internally (scripts/scenario.gd's
		# `anchor` field) — it does not need `--anchor` alongside it to reach the game, and
		# without this branch a `--scenario`-only launch sat on the menu forever, since
		# nothing else here recognised it as "skip straight to the level".
		call_deferred("_go")
		return true
	if CliArgsScript.has(p, "--shot-menu"):
		_menu_shot = CliArgsScript.str_val(p, "--shot-menu", 0, _menu_shot)
		_menu_shot_at = CliArgsScript.int_val(p, "--shot-menu", 1, _menu_shot_at)
	_a11y_path = CliArgsScript.str_val(p, "--a11y", 0, _a11y_path)
	if CliArgsScript.has(p, "--options"):
		# Opens the options panel at boot. Same reasoning as main.gd's --paused and
		# --select: reaching it needs a click, --fixed-fps has nobody to click, and a
		# screen that is never screenshotted is a screen nobody has looked at.
		_open_options_at_boot = true
	return false


# ────────────────────────────────────────────────────────────────── build ──

func _label(text: String, size: int, col: Color, mono := false, bold := false) -> Label:
	return Ui.label(text, size, col, mono, bold)


func _build() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)

	# Everything except the options overlay hangs off this, so opening options can hide the
	# title screen wholesale rather than drawing on top of it.
	_body = Control.new()
	_body.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(_body)

	# Everything below is inside a scroll region. Three rows of eight anchor buttons is
	# 1446 px wide and roughly 1000 px tall whatever the viewport is, so at 200% interface
	# scale — a 960x540 design space — the title screen put CONTINUE, OPTIONS and QUIT off
	# the bottom of the screen and half the anchors off the right. The grid reflows to the
	# width it has (below), and the scroller carries whatever height is left over.
	var scroll := Ui.scroller()
	scroll.set_anchors_preset(Control.PRESET_FULL_RECT)
	# Margins in proportion to the viewport rather than a fixed 120 px: the interface-scale
	# setting shrinks the logical viewport, and a 120 px margin on each side of a 960 px
	# design space is a quarter of the screen given to nothing.
	var vp := get_viewport().get_visible_rect().size
	scroll.offset_left = minf(120.0, vp.x * 0.0625)
	scroll.offset_top = minf(90.0, vp.y * 0.083)
	scroll.offset_right = -scroll.offset_left
	scroll.offset_bottom = -minf(60.0, vp.y * 0.055)
	_body.add_child(scroll)

	var col := VBoxContainer.new()
	col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	col.add_theme_constant_override("separation", 10)
	scroll.add_child(col)

	# How many anchors fit across, measured rather than fixed at eight. Eight is the act
	# structure and stays the ceiling, but a button is 172 px and the row cannot be wider
	# than the space it is in — a GridContainer does not wrap, it simply forces its parent
	# wider, which is what pushed every other row off the right edge at 200%.
	var grid_w := vp.x - scroll.offset_left * 2.0 - Ui.SCROLL_GUTTER
	var cols := clampi(int(floorf((grid_w + GRID_GAP) / (ANCHOR_W + GRID_GAP))), 2, 8)

	col.add_child(_label("LATTICEFALL", Ui.SIZE_DISPLAY, C_BONE, false, true))
	col.add_child(_label("TASK FORCE MERIDIAN  ·  SIXTY-ONE ANCHORS  ·  HOLDING ACTION",
		Ui.SIZE_STAT, C_MUTED))

	var spacer := Control.new()
	spacer.custom_minimum_size = Vector2(0, 24)
	col.add_child(spacer)

	# difficulty
	var drow := HBoxContainer.new()
	drow.add_theme_constant_override("separation", 8)
	drow.add_child(_label("DIFFICULTY", Ui.SIZE_BODY, C_MUTED))
	for d in DIFFICULTIES:
		var b := Ui.button(String(d).to_upper(), Ui.SIZE_BODY)
		b.custom_minimum_size = Vector2(140, 36)
		b.pressed.connect(_on_difficulty.bind(String(d)))
		_difficulty_buttons.append(b)
		drow.add_child(b)
	col.add_child(drow)

	# What the player is carrying into the run: one compact line, not a panel of its own —
	# recoveries.gd's design note is explicit that this is meant to accumulate quietly across
	# twenty-four anchors, not to compete with the anchor grid for the player's attention.
	_carrying = _label("", Ui.SIZE_BODY, C_MUTED)
	_carrying.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	col.add_child(_carrying)

	var spacer2 := Control.new()
	spacer2.custom_minimum_size = Vector2(0, 18)
	col.add_child(spacer2)

	# anchors, one block per act — which is exactly the act structure, so the blocks are
	# labelled rather than left as an anonymous 24-cell grid.
	for act in [1, 2, 3]:
		col.add_child(_label(ACT_TITLES[act], Ui.SIZE_CAPTION, C_MUTED))
		var row := GridContainer.new()
		row.columns = cols
		row.add_theme_constant_override("h_separation", int(GRID_GAP))
		row.add_theme_constant_override("v_separation", int(GRID_GAP))
		row.name = "Act%d" % act
		col.add_child(row)
		var gap := Control.new()
		gap.custom_minimum_size = Vector2(0, 12)
		col.add_child(gap)
	_grid = col.get_node("Act1")

	# Wrapped, not clipped. The hover line names the anchor, its capacity, its wave count
	# and its decay rate, which runs past 840 px at a narrow scale — and a GridContainer
	# sibling means a too-wide label widens the whole column rather than being cut.
	_detail = _label("", Ui.SIZE_STAT, C_MUTED)
	_detail.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_detail.custom_minimum_size = Vector2(0, 2.0 * Ui.line_h(Ui.SIZE_STAT, false))
	col.add_child(_detail)

	var actions := HBoxContainer.new()
	actions.add_theme_constant_override("separation", 10)
	var cont := Ui.button("CONTINUE", Ui.SIZE_STAT, true)
	cont.custom_minimum_size = Vector2(210, 42)
	cont.pressed.connect(_on_continue)
	actions.add_child(cont)
	var opts := Ui.button("OPTIONS", Ui.SIZE_STAT, true)
	opts.custom_minimum_size = Vector2(170, 42)
	opts.pressed.connect(_open_options)
	actions.add_child(opts)
	var quit := Ui.button("QUIT", Ui.SIZE_STAT, true)
	quit.custom_minimum_size = Vector2(140, 42)
	quit.pressed.connect(func(): get_tree().quit())
	actions.add_child(quit)
	col.add_child(actions)

	_options = OptionsMenu.new()
	_options.visible = false
	_options.closed.connect(_close_options)
	add_child(_options)

	_refresh()
	if _open_options_at_boot:
		# Cleared on use: closing options rebuilds the title screen, and a flag that
		# survived the rebuild would reopen the panel it was just asked to close.
		_open_options_at_boot = false
		_open_options()


func _open_options() -> void:
	_body.visible = false
	_options.visible = true


func _close_options() -> void:
	# The interface scale changes the size of the design space the title screen was laid
	# out in, so it is rebuilt rather than left at the previous viewport's proportions.
	_rebuild()


func _rebuild() -> void:
	## `remove_child` before `queue_free`, not `queue_free` alone: the free is deferred to
	## the end of the frame, so a rebuild that only queues would leave the old title screen
	## parented and visible underneath the new one for a frame.
	for c in get_children():
		remove_child(c)
		c.queue_free()
	_difficulty_buttons.clear()
	_options = null
	_build()


func _refresh() -> void:
	for b in _difficulty_buttons:
		var on := b.text.to_lower() == Progress.difficulty
		b.add_theme_color_override("font_color", C_AMBER if on else C_MUTED)

	if Progress.owned_recoveries.is_empty():
		_carrying.text = "CARRYING: nothing recovered yet"
	else:
		var names: Array[String] = []
		for id in Progress.owned_recoveries:
			names.append(String(Recoveries.pool_entry(id).get("name", id)))
		_carrying.text = "CARRYING: %s" % "  ·  ".join(names)

	var rows := {}
	for act in [1, 2, 3]:
		var r: GridContainer = _grid.get_parent().get_node("Act%d" % act)
		for c in r.get_children():
			c.queue_free()
		rows[act] = r

	for id in Progress.anchor_ids():
		var doc: Dictionary = Content.anchor(id)
		var n := int(id.substr(7))
		# `Ui.button` sets `font_disabled_color`, which is the theme item that exists. This
		# code set `font_color_disabled` — not a theme item at all, so the override was
		# accepted in silence and never drawn, and every locked anchor fell back to Godot's
		# default half-transparent grey at 2.0:1 against the background.
		var b := Ui.button("", Ui.SIZE_BODY)
		Ui.style(b, Ui.SIZE_BODY, true)
		b.custom_minimum_size = Vector2(ANCHOR_W, ANCHOR_H)
		b.disabled = not Progress.is_unlocked(id)
		# The debrief verdict (data/tuning.json's `grade` block), not a flat "HELD" for any
		# clear at all — scored on the best fraction of lives kept across every difficulty
		# this anchor has been cleared on, so a scrappy first clear and a clean replay read
		# differently on the grid the same way they read differently on the debrief itself.
		var mark := "LOCKED" if b.disabled else "OPEN"
		if Progress.is_cleared(id):
			mark = String(Recoveries.best_grade_for(id).get("verdict", "HELD"))
		b.text = "ANCHOR %02d\n%s" % [n, mark]
		if b.disabled:
			pass
		elif Progress.is_cleared(id):
			b.add_theme_color_override("font_color", C_VERD)
		else:
			b.add_theme_color_override("font_color", C_AMBER)
		b.pressed.connect(_on_anchor.bind(id))
		b.mouse_entered.connect(_on_hover.bind(id))
		rows[int(doc.get("act", 1))].add_child(b)

	_detail.text = "%d of 24 anchors held" % Progress.cleared_count()


# ─────────────────────────────────────────────────────────────── handlers ──

func _on_hover(anchor_id: String) -> void:
	var doc: Dictionary = Content.anchor(anchor_id)
	var bits := "%s  ·  %d MW  ·  %d waves" % [
		String(doc.get("title", "?")), int(doc.get("capacity_mw", 0)),
		Array(doc.get("waves", [])).size()]
	if float(doc.get("capacity_decay_mw", 0.0)) > 0.0:
		bits += "  ·  bus decays %d MW/wave" % int(doc["capacity_decay_mw"])
	if not Progress.is_unlocked(anchor_id):
		bits += "   [LOCKED]"
	_detail.text = bits


func _on_difficulty(d: String) -> void:
	Progress.difficulty = d
	Progress.save_state()
	_refresh()


func _on_anchor(anchor_id: String) -> void:
	Progress.selected_anchor = anchor_id
	_go()


func _on_continue() -> void:
	Progress.selected_anchor = Progress.next_anchor()
	_go()


func _go() -> void:
	get_tree().change_scene_to_file(GAME_SCENE)


func _go_draft() -> void:
	get_tree().change_scene_to_file(DRAFT_SCENE)


func _process(_delta: float) -> void:
	if _menu_shot == "":
		return
	_menu_frame += 1
	if _menu_frame < _menu_shot_at:
		return
	var path := _menu_shot
	_menu_shot = ""
	get_tree().paused = true
	await RenderingServer.frame_post_draw
	var img := get_viewport().get_texture().get_image()
	var err := img.save_png(path)
	print("MENUSHOT %s err=%d %dx%d" % [path, err, img.get_width(), img.get_height()])
	if _a11y_path != "":
		A11yProbe.write(_a11y_path, A11yProbe.capture(self, get_viewport(), {
			"scene": "menu", "shot": path,
		}))
	var lit := 0
	var total := 0
	for y in range(0, img.get_height(), 4):
		for x in range(0, img.get_width(), 4):
			var c := img.get_pixel(x, y)
			total += 1
			if absf(c.r - 0.055) + absf(c.g - 0.078) + absf(c.b - 0.09) > 0.02:
				lit += 1
	print("MENUFRAME coverage=%.4f buttons=%d" % [float(lit) / maxf(float(total), 1.0),
		_grid.get_child_count()])
	print("PROGRESS %s" % Progress.report())
	get_tree().quit()


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("lf_cancel") or event.is_action_pressed("lf_pause"):
		get_tree().quit()
