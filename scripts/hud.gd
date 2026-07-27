extends CanvasLayer
## Reactor readout, build bar, emplacement inspector, wave state. Instrument panel, not
## decoration.
##
## The inspector is the only place the game explains what an emplacement *does*. Before it,
## the build bar named a price and a draw and nothing else: range, rate of fire, what a
## weapon can shoot at, and what the support emplacements do at all were readable only in
## data/towers.json. Decision 035.

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
var _power_button: Button
var _kicker: Label
var _title: Label
var _sub: Label
var _body: Label
var _note: Label
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

	_build_inspector(root)

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

	view.state_changed.connect(refresh)
	view.wave_state.connect(func(_i, _n, _p): refresh())
	refresh()


func _build_inspector(root: Control) -> void:
	## One panel that describes whichever emplacement the player is thinking about — the
	## one selected on the board, or failing that the one about to be built — and carries
	## the three verbs that act on it. The verbs act on `view.selected_slot`, never on the
	## hover: reaching a button means dragging the cursor across the board, and a
	## hover-targeted SELL sells whatever tile the cursor last crossed on its way over.
	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = Vector2(16, 246)
	panel.size = Vector2(330, 420)
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(panel)

	_kicker = _make_label(11, C_MUTED)
	_kicker.position = Vector2(28, 258)
	root.add_child(_kicker)

	_title = _make_label(20, C_BONE, false, true)
	_title.position = Vector2(28, 274)
	root.add_child(_title)

	_sub = _make_label(12, C_AMBER, true)
	_sub.position = Vector2(28, 302)
	root.add_child(_sub)

	root.add_child(_rule(324))

	_body = _make_label(13, C_BONE, true)
	_body.position = Vector2(28, 332)
	root.add_child(_body)

	root.add_child(_rule(474))

	_note = _make_label(12, C_MUTED)
	_note.position = Vector2(28, 482)
	_note.size = Vector2(306, 92)
	_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_note.clip_text = true
	root.add_child(_note)

	var verbs := HBoxContainer.new()
	verbs.position = Vector2(28, 582)
	verbs.add_theme_constant_override("separation", 6)
	root.add_child(verbs)

	_sell_button = Button.new()
	_sell_button.custom_minimum_size = Vector2(150, 32)
	Ui.style(_sell_button, 12)
	_sell_button.pressed.connect(func(): view.sell_at(view.selected_slot))
	verbs.add_child(_sell_button)

	_upgrade_button = Button.new()
	_upgrade_button.custom_minimum_size = Vector2(150, 32)
	Ui.style(_upgrade_button, 12)
	_upgrade_button.pressed.connect(func(): view.upgrade_at(view.selected_slot))
	verbs.add_child(_upgrade_button)

	_power_button = Button.new()
	_power_button.custom_minimum_size = Vector2(306, 30)
	_power_button.position = Vector2(28, 620)
	Ui.style(_power_button, 12)
	_power_button.pressed.connect(func(): view.toggle_at(view.selected_slot))
	root.add_child(_power_button)


func _rule(y: float) -> ColorRect:
	var r := ColorRect.new()
	r.color = Color(C_MUTED, 0.28)
	r.position = Vector2(28, y)
	r.size = Vector2(306, 1)
	r.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return r


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
	_wave.text = "wave %d / %d   ·   %s" % [view.wave_number(), total, view.phase()]

	# Two independent signals on one button: dimmed means unaffordable, amber means this is
	# what a click on a free slot will build. Which one is armed used to be readable only
	# from a word at the end of the wave line, nowhere near the buttons themselves.
	for i in range(_buttons.size()):
		var tid: String = Content.unlocked_at(view.anchor_id)[i]
		_buttons[i].modulate = Color(1, 1, 1) if sim.can_afford(tid) else Color(0.5, 0.5, 0.5)
		var armed: bool = tid == view.selected_tower
		_buttons[i].add_theme_color_override("font_color", C_AMBER if armed else C_BONE)
		_buttons[i].add_theme_font_override("font", Ui.SANS_BOLD if armed else Ui.SANS)

	_refresh_inspector()

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


# ────────────────────────────────────────────────────────── inspector ──

func _refresh_inspector() -> void:
	var sim = view.sim
	var idx: int = view.placed_index_at(view.selected_slot)
	if idx < 0:
		_show_build_selection()
		return

	var p: Dictionary = sim.placed[idx]
	var t: Dictionary = p["tower"]
	var online: bool = p["online"]
	var up: Dictionary = t.get("upgrade", {})

	_kicker.text = "EMPLACEMENT · SLOT %d,%d" % [view.selected_slot.x, view.selected_slot.y]
	_title.text = String(t["name"])
	_sub.text = "ONLINE · drawing %d MW" % int(t["draw_mw"]) if online else "OFFLINE · drawing 0 MW"
	_sub.add_theme_color_override("font_color", C_VERD if online else C_MUTED)
	_body.text = _stats_text(t, up)
	_note.text = String(t.get("note", ""))

	var paid: int = int(t["cost"]) + int(p.get("upgrade_paid", 0))
	_sell_button.disabled = false
	_sell_button.text = "SELL  +$%d" % int(floor(float(paid) * sim.SELL_REFUND))

	var cost: int = sim.upgrade_cost(idx)
	_upgrade_button.disabled = cost <= 0 or cost > sim.funds
	_upgrade_button.text = "UPGRADE  $%d" % cost if cost > 0 else "FULLY UPGRADED"

	_power_button.disabled = false
	_power_button.text = ("TAKE OFFLINE — FREES %d MW" % int(t["draw_mw"])) if online \
			else ("BRING ONLINE — COSTS %d MW" % int(t["draw_mw"]))


func _show_build_selection() -> void:
	## Nothing selected on the board, so the panel describes what a click would build.
	var t: Dictionary = Content.tower(view.selected_tower)
	if t.is_empty():
		_kicker.text = "EMPLACEMENT"
		_title.text = "—"
		_sub.text = "click an emplacement to inspect it"
		_sub.add_theme_color_override("font_color", C_MUTED)
		_body.text = ""
		_note.text = ""
	else:
		_kicker.text = "READY TO BUILD"
		_title.text = String(t["name"])
		_sub.text = "$%d · %d MW continuous" % [int(t["cost"]), int(t["draw_mw"])]
		_sub.add_theme_color_override("font_color", C_AMBER)
		_body.text = _stats_text(t, {})
		_note.text = String(t.get("note", ""))

	_sell_button.disabled = true
	_sell_button.text = "SELL  —"
	_upgrade_button.disabled = true
	_upgrade_button.text = "UPGRADE  —"
	_power_button.disabled = true
	_power_button.text = "SELECT AN EMPLACEMENT ON THE BOARD"


func _stats_text(t: Dictionary, up: Dictionary) -> String:
	## The whole datasheet, with the second tier shown as a delta where there is one, so
	## "is this upgrade worth 160" is answerable without buying it. Monospaced and padded
	## so the arrows line up into a column.
	var rows: Array[String] = []
	var dmg := float(t.get("damage", 0.0))
	var iv := float(t.get("fire_interval", 1.0))
	if dmg > 0.0:
		rows.append(_row("damage", "%.0f" % dmg, _fmt(up, "damage", "%.0f")))
		rows.append(_row("every", "%.2f s" % iv, _fmt(up, "fire_interval", "%.2f s")))
		var next_dps := ""
		if up.has("damage") or up.has("fire_interval"):
			next_dps = "%.1f" % (float(up.get("damage", dmg))
					/ maxf(float(up.get("fire_interval", iv)), 0.001))
		rows.append(_row("dps", "%.1f" % (dmg / maxf(iv, 0.001)), next_dps))
	else:
		rows.append(_row("damage", "none — support", ""))
	rows.append(_row("range", "%.1f tiles" % float(t.get("range", 0.0)),
			_fmt(up, "range", "%.1f tiles")))
	rows.append(_row("draw", "%d MW" % int(t.get("draw_mw", 0)), _fmt(up, "draw_mw", "%d MW")))
	rows.append(_row("hits", _targets(t), ""))
	if float(t.get("splash", 0.0)) > 0.0:
		rows.append(_row("splash", "%.1f tiles" % float(t["splash"]),
				_fmt(up, "splash", "%.1f tiles")))
	var effect: Dictionary = t.get("effect", {})
	if not effect.is_empty():
		rows.append(_row("effect", _effect_text(effect), ""))
	return "\n".join(rows)


func _fmt(up: Dictionary, key: String, spec: String) -> String:
	## The upgraded value of one stat, or "" when the second tier does not change it.
	return spec % float(up[key]) if up.has(key) else ""


func _row(label: String, cur: String, nxt: String) -> String:
	var line := "%-7s %s" % [label, cur]
	if nxt != "" and nxt != cur:
		line += "  →  %s" % nxt
	return line


func _targets(t: Dictionary) -> String:
	## What the weapon is rated for. `shielded` is the one that is not obvious: an
	## unrated weapon still lands SHIELD_LEAK of its damage rather than nothing at all
	## (decision 029), so the word is "rated", not "only".
	var names: Array[String] = []
	for x in t.get("targets", []):
		names.append(String(x))
	return " · ".join(names) if names.size() > 0 else "nothing"


func _effect_text(effect: Dictionary) -> String:
	var value := float(effect.get("value", 0.0))
	match String(effect.get("type", "")):
		"slow":
			return "slows %d%% in range" % roundi(value * 100.0)
		"reveal":
			return "reveals air for every gun"
		"damp":
			return "damps %d%% of drain in range" % roundi(value * 100.0)
		"restore":
			return "+%d MW bus capacity" % roundi(value)
	return String(effect.get("type", ""))


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
