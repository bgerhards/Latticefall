extends CanvasLayer
## Reactor readout, build bar, wave state. Instrument panel, not decoration.

const C_VERD := Color(0.37, 0.66, 0.58)
const C_AMBER := Color(0.91, 0.64, 0.24)
const C_ALERT := Color(0.82, 0.33, 0.25)
const C_MUTED := Color(0.49, 0.56, 0.57)
const C_BONE := Color(0.86, 0.89, 0.88)
const C_PANEL := Color(0.086, 0.13, 0.145, 0.94)

var view: Node2D
var _gauge: ColorRect
var _fill: ColorRect
var _load_label: Label
var _stat: Label
var _wave: Label
var _fault: Label
var _buttons: Array[Button] = []


func _mono(size: int) -> Font:
	return ThemeDB.fallback_font


func _make_label(size: int, col: Color) -> Label:
	var l := Label.new()
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", col)
	return l


func bind(v: Node2D) -> void:
	## Built on an explicit call from main.gd rather than in _ready(). This node is now
	## an authored child of Main, so its _ready() runs *before* Main's — before the CLI
	## has chosen an anchor and before the sim exists.
	view = v
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(root)

	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = Vector2(16, 16)
	panel.size = Vector2(330, 108)
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(panel)

	var title := _make_label(11, C_MUTED)
	title.text = "REACTOR BUS"
	title.position = Vector2(28, 24)
	root.add_child(title)

	_load_label = _make_label(26, C_AMBER)
	_load_label.position = Vector2(28, 40)
	root.add_child(_load_label)

	_gauge = ColorRect.new()
	_gauge.color = Color(0.07, 0.10, 0.11)
	_gauge.position = Vector2(28, 78)
	_gauge.size = Vector2(306, 16)
	root.add_child(_gauge)

	_fill = ColorRect.new()
	_fill.color = C_VERD
	_fill.position = Vector2(28, 78)
	_fill.size = Vector2(0, 16)
	root.add_child(_fill)

	_fault = _make_label(12, C_ALERT)
	_fault.position = Vector2(28, 98)
	root.add_child(_fault)

	_stat = _make_label(14, C_BONE)
	_stat.position = Vector2(28, 134)
	root.add_child(_stat)

	_wave = _make_label(14, C_MUTED)
	_wave.position = Vector2(28, 154)
	root.add_child(_wave)

	var bar := HBoxContainer.new()
	bar.position = Vector2(28, 184)
	bar.add_theme_constant_override("separation", 6)
	root.add_child(bar)

	for tid in Content.unlocked_at(view.anchor_id):
		var t: Dictionary = Content.tower(tid)
		var b := Button.new()
		b.text = "%s\n$%d · %d MW" % [t["name"], int(t["cost"]), int(t["draw_mw"])]
		b.custom_minimum_size = Vector2(126, 46)
		b.add_theme_font_size_override("font_size", 11)
		b.pressed.connect(_on_pick.bind(String(tid)))
		bar.add_child(b)
		_buttons.append(b)

	view.state_changed.connect(refresh)
	view.wave_state.connect(func(_i, _n, _p): refresh())
	refresh()


func _on_pick(tid: String) -> void:
	view.select(tid)
	refresh()


func _process(_d: float) -> void:
	refresh()


func refresh() -> void:
	if view == null or view.sim == null:
		return
	var sim = view.sim
	var load: float = sim.bus_load()
	var cap: float = sim.capacity()

	_load_label.text = "%d / %d MW" % [roundi(load), roundi(cap)]
	_load_label.add_theme_color_override("font_color", C_ALERT if sim.brownout else C_AMBER)
	_fill.size.x = 306.0 * clampf(load / maxf(cap, 1.0), 0.0, 1.0)
	_fill.color = C_ALERT if sim.brownout else C_VERD
	_fault.text = "BUS OVERDRAW — ALL SYSTEMS -40% FIRE RATE" if sim.brownout else ""

	_stat.text = "funds $%d      lives %d      leaks %d" % [sim.funds, sim.lives, sim.leaks]
	var total: int = sim.anchor["waves"].size()
	_wave.text = "wave %d / %d   ·   %s   ·   selected: %s" % [
		view.wave_number(), total, view.phase(),
		Content.tower(view.selected_tower).get("name", "—")]

	for i in range(_buttons.size()):
		var tid: String = Content.unlocked_at(view.anchor_id)[i]
		_buttons[i].modulate = Color(1, 1, 1) if sim.can_afford(tid) else Color(0.5, 0.5, 0.5)
