extends Control
class_name OptionsMenu
## The options panel, used by both the title screen and the in-anchor pause overlay.
##
## One implementation rather than two, because the settings a player wants are not the same
## as the settings a player can reach: volume already lived in the pause overlay (decision
## 034, "the moment a player wants to change it is the moment it is too loud"), and display
## and legibility settings deserve the same treatment. Somebody who cannot read the build
## bar discovers that mid-anchor, not on the title screen.
##
## Every row is a cycler — label, current value, and a step in each direction — instead of
## a dropdown. A dropdown needs a click to reveal its own contents, so its options are
## invisible to the keyboard and gamepad navigation the game already has, and OptionButton
## opens a native popup that does not inherit the interface scale it is there to change.
##
## Laid out with containers and centred on anchors, so it survives the one setting that
## resizes the logical viewport underneath it while it is open.

signal closed

const FPS_CAPS: Array[int] = [0, 60, 120, 144, 240]

var _rows: Array = []
var _panel: PanelContainer
var _col: VBoxContainer


func _ready() -> void:
	## Sized explicitly rather than left to the anchor preset. Added mid-`_build()` of its
	## parent, this node's rect is computed against a parent whose own size has not been
	## resolved yet, so the layout pass clamps it to its minimum size — which is the panel —
	## and the CenterContainer then has nothing to centre within. The result is a panel
	## pinned to the top-left corner with a dim overlay that covers only itself.
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_fit()
	get_viewport().size_changed.connect(_fit)
	_build()
	_refresh()


func _fit() -> void:
	## Closing the panel rebuilds the title screen, which detaches this node before the
	## deferred free runs. The viewport connection outlives the detach by that much, so a
	## resize landing in the gap would call `get_viewport()` on an orphan and get null.
	if not is_inside_tree():
		return
	position = Vector2.ZERO
	size = get_viewport().get_visible_rect().size


func _build() -> void:
	var dim := ColorRect.new()
	dim.color = Color(0.02, 0.03, 0.035, 0.72)
	dim.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(dim)

	# Centred by anchor rather than by a computed position: the interface-scale row changes
	# the size of the viewport this panel is sitting in, while it is on screen.
	var centre := CenterContainer.new()
	centre.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(centre)

	_panel = PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Ui.C_OVERLAY
	sb.border_color = Color(Ui.C_MUTED, 0.25)
	sb.set_border_width_all(1)
	sb.set_content_margin_all(28)
	_panel.add_theme_stylebox_override("panel", sb)
	centre.add_child(_panel)

	_col = VBoxContainer.new()
	_col.add_theme_constant_override("separation", 10)
	_panel.add_child(_col)

	_col.add_child(Ui.label("OPTIONS", Ui.SIZE_LEAD, Ui.C_BONE, false, true))
	_col.add_child(Ui.label("display, legibility and volume", Ui.SIZE_BODY, Ui.C_MUTED))
	_col.add_child(_gap(12))

	_col.add_child(_section("DISPLAY"))
	_add_cycler("MODE", func(): return Display.MODE_LABELS[Display.window_mode],
		func(step: int): _cycle_mode(step))
	_add_cycler("RESOLUTION", func(): return _resolution_text(),
		func(step: int): _cycle_resolution(step))
	_add_cycler("V-SYNC", func(): return "ON" if Display.vsync else "OFF",
		func(_s: int): Display.set_vsync(not Display.vsync))
	_add_cycler("FRAME CAP", func(): return _fps_text(),
		func(step: int): _cycle_fps(step))

	_col.add_child(_gap(10))
	_col.add_child(_section("LEGIBILITY"))
	_add_cycler("INTERFACE SCALE", func(): return "%d%%" % roundi(Display.ui_scale * 100.0),
		func(step: int): _cycle_ui_scale(step))
	_add_cycler("EMISSIVE GLOW", func(): return Display.GLOW_LABELS.get(Display.glow, "FULL"),
		func(step: int): _cycle_glow(step))

	_col.add_child(_gap(10))
	_col.add_child(_section("AUDIO"))
	_col.add_child(_slider("MUSIC", "music"))
	_col.add_child(_slider("EFFECTS", "sfx"))

	_col.add_child(_gap(16))
	var back := Ui.button("BACK", Ui.SIZE_STAT, true)
	back.custom_minimum_size = Vector2(0, 42)
	back.pressed.connect(func(): closed.emit())
	_col.add_child(back)


# ─────────────────────────────────────────────────────────────── widgets ──

func _gap(h: int) -> Control:
	var c := Control.new()
	c.custom_minimum_size = Vector2(0, h)
	return c


func _section(text: String) -> Label:
	return Ui.label(text, Ui.SIZE_CAPTION, Ui.C_AMBER, false, true)


func _add_cycler(name: String, get_text: Callable, step: Callable) -> void:
	## label ......... [<]  VALUE  [>]
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)

	var l := Ui.label(name, Ui.SIZE_BODY, Ui.C_MUTED)
	l.custom_minimum_size = Vector2(230, 0)
	l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	row.add_child(l)

	var left := Ui.button("<", Ui.SIZE_BODY)
	left.custom_minimum_size = Vector2(40, 34)
	left.pressed.connect(func(): step.call(-1); _refresh())
	row.add_child(left)

	var value := Ui.label("", Ui.SIZE_BODY, Ui.C_BONE, true)
	value.custom_minimum_size = Vector2(190, 0)
	value.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	value.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	row.add_child(value)

	var right := Ui.button(">", Ui.SIZE_BODY)
	right.custom_minimum_size = Vector2(40, 34)
	right.pressed.connect(func(): step.call(1); _refresh())
	row.add_child(right)

	_col.add_child(row)
	_rows.append({"value": value, "get_text": get_text, "left": left, "right": right,
		"name": name})


func _slider(text: String, which: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	var l := Ui.label(text, Ui.SIZE_BODY, Ui.C_MUTED)
	l.custom_minimum_size = Vector2(230, 0)
	l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	row.add_child(l)
	var s := HSlider.new()
	s.min_value = 0.0
	s.max_value = 1.0
	s.step = 0.05
	s.custom_minimum_size = Vector2(310, 30)
	s.value = Progress.music_volume if which == "music" else Progress.sfx_volume
	s.value_changed.connect(func(v: float):
		if which == "music":
			Progress.music_volume = v
		else:
			Progress.sfx_volume = v
		Progress.save_state()
		Audio.apply_volumes())
	row.add_child(s)
	return row


func _refresh() -> void:
	for r in _rows:
		r["value"].text = String(r["get_text"].call())
	# Resolution is meaningless while the window is fullscreen — the desktop decides. Grey
	# the row rather than hiding it, so the setting does not appear and disappear.
	var windowed := Display.window_mode == Display.MODE_WINDOWED
	for r in _rows:
		if r["name"] == "RESOLUTION":
			r["left"].disabled = not windowed
			r["right"].disabled = not windowed
			r["value"].add_theme_color_override("font_color",
				Ui.C_BONE if windowed else Ui.C_DIM)


# ───────────────────────────────────────────────────────────────── steps ──

func _cycle(list: Array, current: Variant, step: int) -> Variant:
	var i := list.find(current)
	if i < 0:
		i = 0
	return list[posmod(i + step, list.size())]


func _cycle_mode(step: int) -> void:
	Display.set_window_mode(String(_cycle(Display.MODES, Display.window_mode, step)))


func _cycle_resolution(step: int) -> void:
	var list := Display.available_resolutions()
	if list.is_empty():
		return
	Display.set_resolution(_cycle(list, Display.resolution, step))


func _cycle_ui_scale(step: int) -> void:
	Display.set_ui_scale(float(_cycle(Display.UI_SCALES, Display.ui_scale, step)))


func _cycle_glow(step: int) -> void:
	Display.set_glow(float(_cycle(Display.GLOW_LEVELS, Display.glow, step)))


func _cycle_fps(step: int) -> void:
	Display.set_max_fps(int(_cycle(FPS_CAPS, Display.max_fps, step)))


func _resolution_text() -> String:
	if Display.window_mode != Display.MODE_WINDOWED:
		var s := DisplayServer.window_get_size()
		return "%d x %d" % [s.x, s.y]
	return "%d x %d" % [Display.resolution.x, Display.resolution.y]


func _fps_text() -> String:
	return "UNCAPPED" if Display.max_fps == 0 else str(Display.max_fps)


func _unhandled_input(event: InputEvent) -> void:
	if not visible:
		return
	if event.is_action_pressed("lf_cancel") or event.is_action_pressed("lf_pause"):
		get_viewport().set_input_as_handled()
		closed.emit()
