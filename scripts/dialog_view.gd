extends CanvasLayer
## Typewriter dialog. No voice acting (decision: budget goes to writing volume).
##
## Mid-wave lines never block input and are always skippable — the schema forbids a
## line from being critical precisely so the player can ignore this entirely.

const C_VERD := Ui.C_VERD
const C_AMBER := Ui.C_AMBER
const C_BONE := Ui.C_BONE
const CPS := 42.0

var view: Node2D
var _panel: ColorRect
var _who: Label
var _text: Label
var _queue: Array = []
var _full: String = ""
var _shown: float = 0.0
var _hold: float = 0.0


func bind(v: Node2D) -> void:
	## Built on an explicit call from main.gd rather than in _ready() — see hud.gd.
	## The opening brief is fired by view.start(), which main.gd calls only after every
	## listener is bound, so no line can be emitted before this node can hear it.
	view = v
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)

	_panel = ColorRect.new()
	_panel.color = Ui.C_PANEL
	_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_panel.visible = false
	root.add_child(_panel)

	_who = Label.new()
	Ui.style(_who, Ui.SIZE_CAPTION, false, true)
	_who.add_theme_color_override("font_color", C_VERD)
	root.add_child(_who)

	_text = Label.new()
	Ui.style(_text, Ui.SIZE_STAT)
	_text.add_theme_color_override("font_color", C_BONE)
	_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	root.add_child(_text)

	_layout()
	get_viewport().size_changed.connect(_layout)
	view.dialog_trigger.connect(on_trigger)


func _layout() -> void:
	var vp := get_viewport().get_visible_rect().size
	# Measured from the type it holds — a speaker line, then two wrapped lines of dialog —
	# rather than the fixed 96 that was chosen when the body text was 4 px smaller.
	var who_h := Ui.line_h(Ui.SIZE_CAPTION, false)
	var body_h := 2.0 * Ui.line_h(Ui.SIZE_STAT, false)
	var h := who_h + body_h + 26.0
	# Starts where the instrument column ends rather than at the left edge. Spanning the
	# full width put a bar under the column, and the column needs the height more than the
	# dialog needs the extra 428 px of line.
	var x := Ui.COL_X + Ui.COL_W + 8.0
	_panel.position = Vector2(x, vp.y - h - 16)
	_panel.size = Vector2(maxf(vp.x - x - 16.0, 200.0), h)
	_who.position = _panel.position + Vector2(16, 8)
	_text.position = _panel.position + Vector2(16, 8 + who_h + 4.0)
	_text.size = Vector2(_panel.size.x - 32, body_h)


func on_trigger(trigger: String) -> void:
	for line in Content.dialog(view.anchor_id):
		if String(line.get("trigger", "")) == trigger:
			_queue.append(line)
	if _full == "" and _queue.size() > 0:
		_next()


func _next() -> void:
	if _queue.is_empty():
		if _panel.visible:
			Audio.sfx("comms_close", -9.0)     # the channel closes once, not once per line
		_panel.visible = false
		_who.text = ""
		_text.text = ""
		_full = ""
		return
	var line: Dictionary = _queue.pop_front()
	# The radio opens before anyone speaks. Every line used to be announced by ui_hover,
	# the quietest menu tick in the bank — a UI sound doing a soldier's job.
	Audio.sfx("comms_squelch", -7.0)
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
				# Kept as the typing texture, dropped 4 dB now that the squelch carries
				# the transition — two cues competing for one moment read as one mess.
				Audio.sfx("ui_hover", -22.0)
	else:
		_hold += delta
		if _hold > 2.6:
			_next()


func _unhandled_input(event: InputEvent) -> void:
	if _panel == null:
		return                                   # not bound yet; nothing to advance
	if event.is_action_pressed("lf_confirm"):
		if _full == "" and not _panel.visible:
			return                      # nothing to advance; let the board have the press
		# Consumed, so the gamepad button that advances a line does not also place an
		# emplacement on whatever the cursor happens to be over.
		get_viewport().set_input_as_handled()
		if _full != "" and _shown < float(_full.length()):
			_shown = float(_full.length())
			_text.text = _full
		else:
			_next()
