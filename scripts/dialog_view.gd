extends CanvasLayer
## Typewriter dialog. No voice acting (decision: budget goes to writing volume).
##
## Mid-wave lines never block input and are always skippable — the schema forbids a
## line from being critical precisely so the player can ignore this entirely.

const C_VERD := Color(0.37, 0.66, 0.58)
const C_AMBER := Color(0.91, 0.64, 0.24)
const C_BONE := Color(0.86, 0.89, 0.88)
const CPS := 42.0

var view: Node2D
var _panel: ColorRect
var _who: Label
var _text: Label
var _queue: Array = []
var _full: String = ""
var _shown: float = 0.0
var _hold: float = 0.0


func _ready() -> void:
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)

	_panel = ColorRect.new()
	_panel.color = Color(0.086, 0.13, 0.145, 0.94)
	_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_panel.visible = false
	root.add_child(_panel)

	_who = Label.new()
	_who.add_theme_font_size_override("font_size", 12)
	_who.add_theme_color_override("font_color", C_VERD)
	root.add_child(_who)

	_text = Label.new()
	_text.add_theme_font_size_override("font_size", 16)
	_text.add_theme_color_override("font_color", C_BONE)
	_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	root.add_child(_text)

	_layout()
	get_viewport().size_changed.connect(_layout)
	if view:
		view.dialog_trigger.connect(on_trigger)


func _layout() -> void:
	var vp := get_viewport().get_visible_rect().size
	var h := 96.0
	_panel.position = Vector2(16, vp.y - h - 16)
	_panel.size = Vector2(vp.x - 32, h)
	_who.position = _panel.position + Vector2(16, 10)
	_text.position = _panel.position + Vector2(16, 30)
	_text.size = Vector2(_panel.size.x - 32, h - 40)


func on_trigger(trigger: String) -> void:
	for line in Content.dialog(view.anchor_id):
		if String(line.get("trigger", "")) == trigger:
			_queue.append(line)
	if _full == "" and _queue.size() > 0:
		_next()


func _next() -> void:
	if _queue.is_empty():
		_panel.visible = false
		_who.text = ""
		_text.text = ""
		_full = ""
		return
	var line: Dictionary = _queue.pop_front()
	_full = String(line.get("text", ""))
	_who.text = String(line.get("speaker", "")).to_upper()
	_who.add_theme_color_override("font_color",
		C_AMBER if String(line.get("speaker", "")) == "control" else C_VERD)
	_shown = 0.0
	_hold = 0.0
	_panel.visible = true


func _process(delta: float) -> void:
	if _full == "":
		return
	if _shown < float(_full.length()):
		_shown = minf(_shown + CPS * delta, float(_full.length()))
		var n := int(_shown)
		if _text.text.length() != n:
			_text.text = _full.substr(0, n)
			if n % 3 == 0:
				Audio.sfx("ui_hover", -18.0)
	else:
		_hold += delta
		if _hold > 2.6:
			_next()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and event.keycode == KEY_SPACE:
		if _full != "" and _shown < float(_full.length()):
			_shown = float(_full.length())
			_text.text = _full
		else:
			_next()
