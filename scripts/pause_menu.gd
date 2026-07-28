extends CanvasLayer
## In-anchor pause overlay. Esc pauses; the game does not simply exit any more.
##
## Authored as a child of Main and hidden until asked for. It sets `process_mode` to
## ALWAYS on itself so its buttons still work while the tree is paused — everything else,
## including the anchor view's `_process`, stops, which is the whole point.
##
## Volume lives here rather than only in the main menu because the moment a player wants
## to change it is the moment it is too loud, and that moment is mid-anchor. Display and
## legibility settings are here for the same reason, behind OPTIONS: text that is too
## small to read is discovered while playing, not on the title screen.

const MENU_SCENE := "res://scenes/menu.tscn"

var _root: Control
var _panel: PanelContainer
var _options: OptionsMenu
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

	# Centred on anchors and sized by its contents. The panel used to be a fixed 520x500
	# rectangle at a fixed (700,300) — correct only at 1920x1080, and already once wrong
	# when the QUIT row grew out of the bottom of it. Both of those are now impossible:
	# there is no hardcoded height to be wrong, and no hardcoded centre to be off.
	var centre := CenterContainer.new()
	centre.set_anchors_preset(Control.PRESET_FULL_RECT)
	_root.add_child(centre)

	_panel = PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Ui.C_OVERLAY
	sb.border_color = Color(Ui.C_MUTED, 0.25)
	sb.set_border_width_all(1)
	sb.set_content_margin_all(32)
	_panel.add_theme_stylebox_override("panel", sb)
	centre.add_child(_panel)

	var col := VBoxContainer.new()
	col.custom_minimum_size = Vector2(440, 0)
	col.add_theme_constant_override("separation", 12)
	_panel.add_child(col)

	col.add_child(Ui.label("PAUSED", 32, Ui.C_BONE, false, true))
	col.add_child(Ui.label("the wave is held where it stands", Ui.SIZE_BODY, Ui.C_MUTED))

	var gap := Control.new()
	gap.custom_minimum_size = Vector2(0, 10)
	col.add_child(gap)

	col.add_child(_button("RESUME", func(): hide_menu()))
	col.add_child(_button("OPTIONS", func(): _open_options()))
	col.add_child(_button("RESTART ANCHOR", func():
		get_tree().paused = false
		get_tree().reload_current_scene()))
	col.add_child(_button("OPERATIONS", func():
		get_tree().paused = false
		get_tree().change_scene_to_file(MENU_SCENE)))
	col.add_child(_button("QUIT", func(): get_tree().quit()))

	_options = OptionsMenu.new()
	_options.visible = false
	_options.closed.connect(_close_options)
	_root.add_child(_options)


func _button(text: String, on_press: Callable) -> Button:
	var b := Ui.button(text, Ui.SIZE_STAT)
	b.custom_minimum_size = Vector2(440, 42)
	b.pressed.connect(on_press)
	return b


func _open_options() -> void:
	_panel.visible = false
	_options.visible = true


func _close_options() -> void:
	_options.visible = false
	_panel.visible = true


func show_menu() -> void:
	_shown = true
	_root.visible = true
	_close_options()
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
