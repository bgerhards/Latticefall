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
const DIFFICULTIES := ["standard", "hard", "brutal"]
## Act titles come from docs/STORY.md. They are the only place in the UI that names an
## act, so they carry the tone the anchor numbers cannot.
const ACT_TITLES := {
	1: "ACT I  ·  DEAD AIR",
	2: "ACT II  ·  SALVAGE RIGHTS",
	3: "ACT III  ·  THE HOLLOW",
}

const C_VERD := Color(0.37, 0.66, 0.58)
const C_AMBER := Color(0.91, 0.64, 0.24)
const C_MUTED := Color(0.49, 0.56, 0.57)
const C_DIM := Color(0.27, 0.32, 0.33)
const C_BONE := Color(0.86, 0.89, 0.88)
const C_PANEL := Color(0.086, 0.13, 0.145, 0.94)

var _difficulty_buttons: Array[Button] = []
var _grid: GridContainer
var _detail: Label

## `-- --shot-menu <path> [frame]` screenshots the menu and quits, so the gate can
## assert that the boot scene draws something. Same reasoning as main.gd's --shot: a
## check that greps for script errors passes happily on a screen that renders nothing.
var _menu_shot: String = ""
var _menu_shot_at: int = 30
var _menu_frame: int = 0


func _ready() -> void:
	if _boot_from_cli():
		return
	RenderingServer.set_default_clear_color(Color(0.055, 0.078, 0.09))
	_build()
	Progress.changed.connect(_refresh)
	Audio.music("A1-BLD_carrier_signal.ogg")


func _boot_from_cli() -> bool:
	## `--anchor`/`--shot` skip the menu entirely. Verification runs the real game, not
	## a menu screenshot, and a player who launches with an explicit anchor means it.
	var argv := OS.get_cmdline_user_args()
	for i in range(argv.size()):
		if argv[i] == "--anchor" and i + 1 < argv.size():
			Progress.selected_anchor = argv[i + 1]
		elif argv[i] == "--difficulty" and i + 1 < argv.size():
			Progress.difficulty = argv[i + 1]
	if argv.has("--anchor") or argv.has("--shot"):
		call_deferred("_go")
		return true
	for i in range(argv.size()):
		if argv[i] == "--shot-menu" and i + 1 < argv.size():
			_menu_shot = argv[i + 1]
			if i + 2 < argv.size() and argv[i + 2].is_valid_int():
				_menu_shot_at = int(argv[i + 2])
	return false


# ────────────────────────────────────────────────────────────────── build ──

func _label(text: String, size: int, col: Color, mono := false, bold := false) -> Label:
	return Ui.label(text, size, col, mono, bold)


func _build() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)

	var col := VBoxContainer.new()
	col.set_anchors_preset(Control.PRESET_FULL_RECT)
	col.offset_left = 120
	col.offset_top = 90
	col.offset_right = -120
	col.offset_bottom = -60
	col.add_theme_constant_override("separation", 10)
	add_child(col)

	col.add_child(_label("LATTICEFALL", 64, C_BONE, false, true))
	col.add_child(_label("TASK FORCE MERIDIAN  ·  SIXTY-ONE ANCHORS  ·  HOLDING ACTION",
		15, C_MUTED))

	var spacer := Control.new()
	spacer.custom_minimum_size = Vector2(0, 24)
	col.add_child(spacer)

	# difficulty
	var drow := HBoxContainer.new()
	drow.add_theme_constant_override("separation", 8)
	drow.add_child(_label("DIFFICULTY", 13, C_MUTED))
	for d in DIFFICULTIES:
		var b := Button.new()
		b.text = String(d).to_upper()
		Ui.style(b, 13)
		b.custom_minimum_size = Vector2(120, 30)
		b.pressed.connect(_on_difficulty.bind(String(d)))
		_difficulty_buttons.append(b)
		drow.add_child(b)
	col.add_child(drow)

	var spacer2 := Control.new()
	spacer2.custom_minimum_size = Vector2(0, 18)
	col.add_child(spacer2)

	# anchors, in three rows of eight — which is exactly the act structure, so the rows
	# are labelled rather than left as an anonymous 24-cell grid.
	for act in [1, 2, 3]:
		col.add_child(_label(ACT_TITLES[act], 13, C_MUTED))
		var row := GridContainer.new()
		row.columns = 8
		row.add_theme_constant_override("h_separation", 10)
		row.add_theme_constant_override("v_separation", 10)
		row.name = "Act%d" % act
		col.add_child(row)
		var gap := Control.new()
		gap.custom_minimum_size = Vector2(0, 12)
		col.add_child(gap)
	_grid = col.get_node("Act1")

	_detail = _label("", 14, C_MUTED)
	_detail.custom_minimum_size = Vector2(0, 26)
	col.add_child(_detail)

	var actions := HBoxContainer.new()
	actions.add_theme_constant_override("separation", 10)
	var cont := Button.new()
	cont.text = "CONTINUE"
	Ui.style(cont, 15, false, true)
	cont.custom_minimum_size = Vector2(190, 38)
	cont.pressed.connect(_on_continue)
	actions.add_child(cont)
	var quit := Button.new()
	quit.text = "QUIT"
	Ui.style(quit, 15, false, true)
	quit.custom_minimum_size = Vector2(120, 38)
	quit.pressed.connect(func(): get_tree().quit())
	actions.add_child(quit)
	col.add_child(actions)

	_refresh()


func _refresh() -> void:
	for b in _difficulty_buttons:
		var on := b.text.to_lower() == Progress.difficulty
		b.add_theme_color_override("font_color", C_AMBER if on else C_MUTED)

	var rows := {}
	for act in [1, 2, 3]:
		var r: GridContainer = _grid.get_parent().get_node("Act%d" % act)
		for c in r.get_children():
			c.queue_free()
		rows[act] = r

	for id in Progress.anchor_ids():
		var doc: Dictionary = Content.anchor(id)
		var n := int(id.substr(7))
		var b := Button.new()
		b.custom_minimum_size = Vector2(150, 60)
		b.disabled = not Progress.is_unlocked(id)
		var mark := "HELD" if Progress.is_cleared(id) else ("LOCKED" if b.disabled else "OPEN")
		b.text = "ANCHOR %02d\n%s" % [n, mark]
		Ui.style(b, 13, true)
		if b.disabled:
			b.add_theme_color_override("font_color_disabled", C_DIM)
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
