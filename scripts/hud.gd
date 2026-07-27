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
var _outcome: Label
var _outcome_hint: Label
var _sell_button: Button
var _upgrade_button: Button
var _outcome_actions: HBoxContainer
var _next_button: Button

const MENU_SCENE := "res://scenes/menu.tscn"
var _buttons: Array[Button] = []


func _make_label(size: int, col: Color, mono: bool = false, bold: bool = false) -> Label:
	## Numbers are monospaced — the bus readout counts, and a proportional digit set
	## makes it jitter sideways as it does. See scripts/ui_theme.gd.
	return Ui.label("", size, col, mono, bold)


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

	_load_label = _make_label(26, C_AMBER, true, true)
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

	_fault = _make_label(12, C_ALERT, false, true)
	_fault.position = Vector2(28, 98)
	root.add_child(_fault)

	_stat = _make_label(14, C_BONE, true)
	_stat.position = Vector2(28, 134)
	root.add_child(_stat)

	_wave = _make_label(14, C_MUTED, true)
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
		Ui.style(b, 11)
		b.pressed.connect(_on_pick.bind(String(tid)))
		bar.add_child(b)
		_buttons.append(b)

	# End-of-anchor banner. Centred, large, and the only place the game tells the player
	# how to get back out of a level — without it the only exit from a finished anchor is
	# a key nobody has been told about.
	_outcome = _make_label(40, C_BONE, false, true)
	_outcome.position = Vector2(0, 380)
	_outcome.size = Vector2(1920, 60)
	_outcome.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_outcome.visible = false
	root.add_child(_outcome)

	_outcome_hint = _make_label(15, C_MUTED)
	_outcome_hint.position = Vector2(0, 436)
	_outcome_hint.size = Vector2(1920, 24)
	_outcome_hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_outcome_hint.visible = false
	root.add_child(_outcome_hint)

	# What to do next, offered where the player is already looking. Without this the only
	# way on from a finished anchor was a key and a menu — LF-039.
	_outcome_actions = HBoxContainer.new()
	_outcome_actions.position = Vector2(660, 480)
	_outcome_actions.add_theme_constant_override("separation", 10)
	_outcome_actions.visible = false
	root.add_child(_outcome_actions)

	_next_button = Button.new()
	_next_button.custom_minimum_size = Vector2(210, 40)
	Ui.style(_next_button, 14, false, true)
	_next_button.pressed.connect(_on_next)
	_outcome_actions.add_child(_next_button)

	var replay := Button.new()
	replay.text = "REPLAY ANCHOR"
	replay.custom_minimum_size = Vector2(190, 40)
	Ui.style(replay, 14)
	replay.pressed.connect(func(): get_tree().reload_current_scene())
	_outcome_actions.add_child(replay)

	var ops := Button.new()
	ops.text = "OPERATIONS"
	ops.custom_minimum_size = Vector2(170, 40)
	Ui.style(ops, 14)
	ops.pressed.connect(func(): get_tree().change_scene_to_file(MENU_SCENE))
	_outcome_actions.add_child(ops)

	# Sell / upgrade act on whatever the cursor is over. A selection model would need a
	# second concept of "selected" alongside the build cursor, and the board already
	# highlights the hovered slot — so the panel names the emplacement it will act on.
	var manage := HBoxContainer.new()
	manage.position = Vector2(28, 240)
	manage.add_theme_constant_override("separation", 6)
	root.add_child(manage)

	_sell_button = Button.new()
	_sell_button.custom_minimum_size = Vector2(190, 34)
	Ui.style(_sell_button, 12)
	_sell_button.pressed.connect(func(): view.sell_at(view.hovered_slot))
	manage.add_child(_sell_button)

	_upgrade_button = Button.new()
	_upgrade_button.custom_minimum_size = Vector2(210, 34)
	Ui.style(_upgrade_button, 12)
	_upgrade_button.pressed.connect(func(): view.upgrade_at(view.hovered_slot))
	manage.add_child(_upgrade_button)

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
	# The penalty scales with the overdraw (decision 022), so the readout has to show
	# the real number — a fixed "-40%" would be lying about the decision the player is
	# making, which is *how far* over to go, not merely whether.
	_fault.text = ("BUS OVERDRAW — ALL SYSTEMS %d%% FIRE RATE"
			% [-roundi(sim.penalty_now() * 100.0)]) if sim.brownout else ""

	_stat.text = "funds $%d      lives %d      leaks %d" % [sim.funds, sim.lives, sim.leaks]
	var total: int = sim.anchor["waves"].size()
	_wave.text = "wave %d / %d   ·   %s   ·   selected: %s" % [
		view.wave_number(), total, view.phase(),
		Content.tower(view.selected_tower).get("name", "—")]

	for i in range(_buttons.size()):
		var tid: String = Content.unlocked_at(view.anchor_id)[i]
		_buttons[i].modulate = Color(1, 1, 1) if sim.can_afford(tid) else Color(0.5, 0.5, 0.5)

	var idx: int = view.placed_index_at(view.hovered_slot)
	if idx < 0:
		_sell_button.disabled = true
		_upgrade_button.disabled = true
		_sell_button.text = "SELL  —"
		_upgrade_button.text = "UPGRADE  —"
	else:
		var p: Dictionary = sim.placed[idx]
		var paid: int = int(p["tower"]["cost"]) + int(p.get("upgrade_paid", 0))
		_sell_button.disabled = false
		_sell_button.text = "SELL %s  +$%d" % [String(p["tower"]["name"]),
			int(floor(float(paid) * sim.SELL_REFUND))]
		var up: int = sim.upgrade_cost(idx)
		_upgrade_button.disabled = up <= 0 or up > sim.funds
		_upgrade_button.text = ("UPGRADE  $%d · %d MW" % [up, int(
			Dictionary(p["tower"].get("upgrade", {})).get("draw_mw", p["tower"]["draw_mw"]))]
			) if up > 0 else "UPGRADED"

	var phase: String = view.phase()
	_outcome.visible = phase in ["done", "lost"]
	_outcome_hint.visible = _outcome.visible
	_outcome_actions.visible = _outcome.visible
	if _outcome.visible:
		var held := phase == "done"
		_outcome.text = "ANCHOR HELD" if held else "ANCHOR LOST"
		_outcome.add_theme_color_override("font_color", C_VERD if held else C_ALERT)
		_outcome_hint.text = ("%d lives remaining   ·   %d leaks"
			% [sim.lives, sim.leaks]) if held else "the ring did not hold"
		# The next anchor only exists if this one was held and it is not the last.
		var nxt := _next_anchor_id()
		_next_button.visible = held and nxt != ""
		if _next_button.visible:
			_next_button.text = "NEXT: ANCHOR %s" % nxt.substr(7)


func _next_anchor_id() -> String:
	var ids := Progress.anchor_ids()
	var i := Array(ids).find(view.anchor_id)
	return String(ids[i + 1]) if i >= 0 and i + 1 < ids.size() else ""


func _on_next() -> void:
	var nxt := _next_anchor_id()
	if nxt == "":
		return
	Progress.selected_anchor = nxt
	get_tree().reload_current_scene()
