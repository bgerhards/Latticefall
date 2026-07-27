extends CanvasLayer
## In-anchor pause overlay. Esc pauses; the game does not simply exit any more.
##
## Authored as a child of Main and hidden until asked for. It sets `process_mode` to
## ALWAYS on itself so its buttons still work while the tree is paused — everything else,
## including the anchor view's `_process`, stops, which is the whole point.
##
## Volume lives here rather than only in the main menu because the moment a player wants
## to change it is the moment it is too loud, and that moment is mid-anchor.

const MENU_SCENE := "res://scenes/menu.tscn"

const C_MUTED := Color(0.49, 0.56, 0.57)
const C_BONE := Color(0.86, 0.89, 0.88)
const C_PANEL := Color(0.07, 0.10, 0.115, 0.96)

var _root: Control
var _shown := false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_build()
	hide_menu()


func _build() -> void:
	_root = Control.new()
	_root.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(_root)

	var dim := ColorRect.new()
	dim.color = Color(0.02, 0.03, 0.035, 0.72)
	dim.set_anchors_preset(Control.PRESET_FULL_RECT)
	_root.add_child(dim)

	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = Vector2(700, 300)
	# Tall enough for the last button: at 420 the QUIT row hung out of the bottom of
	# the panel, which is the sort of thing only a screenshot shows.
	panel.size = Vector2(520, 500)
	_root.add_child(panel)

	var col := VBoxContainer.new()
	col.position = Vector2(740, 336)
	col.custom_minimum_size = Vector2(440, 0)
	col.add_theme_constant_override("separation", 12)
	_root.add_child(col)

	col.add_child(Ui.label("PAUSED", 32, C_BONE, false, true))
	col.add_child(Ui.label("the wave is held where it stands", 13, C_MUTED))

	var gap := Control.new()
	gap.custom_minimum_size = Vector2(0, 10)
	col.add_child(gap)

	col.add_child(_slider("MUSIC", "music", col))
	col.add_child(_slider("EFFECTS", "sfx", col))

	var gap2 := Control.new()
	gap2.custom_minimum_size = Vector2(0, 10)
	col.add_child(gap2)

	col.add_child(_button("RESUME", func(): hide_menu()))
	col.add_child(_button("RESTART ANCHOR", func():
		get_tree().paused = false
		get_tree().reload_current_scene()))
	col.add_child(_button("OPERATIONS", func():
		get_tree().paused = false
		get_tree().change_scene_to_file(MENU_SCENE)))
	col.add_child(_button("QUIT", func(): get_tree().quit()))


func _button(text: String, on_press: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.custom_minimum_size = Vector2(440, 38)
	Ui.style(b, 14)
	b.pressed.connect(on_press)
	return b


func _slider(text: String, which: String, _parent: Control) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 12)
	var l := Ui.label(text, 13, C_MUTED)
	l.custom_minimum_size = Vector2(90, 0)
	row.add_child(l)
	var s := HSlider.new()
	s.min_value = 0.0
	s.max_value = 1.0
	s.step = 0.05
	s.custom_minimum_size = Vector2(300, 24)
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


func show_menu() -> void:
	_shown = true
	_root.visible = true
	get_tree().paused = true


func hide_menu() -> void:
	_shown = false
	_root.visible = false
	get_tree().paused = false


func toggle() -> void:
	if _shown:
		hide_menu()
	else:
		show_menu()


func is_open() -> bool:
	return _shown
