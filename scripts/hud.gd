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
var _threat_kicker: Label
var _threat_title: Label
var _threat_sub: Label
var _threat_body: Label
var _threat_footer: Label
var _threat_alert: Label
var _outcome_actions: HBoxContainer
var _next_button: Button

const MENU_SCENE := "res://scenes/menu.tscn"

## The instrument column is 330 px wide and everything in it is laid out against these,
## because the build bar's height is not known until an anchor says what it unlocks.
const BAR_TOP := 184.0
const BUTTON_H := 44.0
const PANEL_H := 420.0
## The threat panel mirrors the instrument column on the right edge. The board is centred
## and at anchor-24 is (18+15)*64 = 2112 px wide, so it runs off both sides of a 1920 px
## viewport regardless — a panel here covers far tiles, exactly as the left column does.
const THREAT_X := 1524.0
const THREAT_BODY_SIZE := 11

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

	# Two across, inside the instrument column, rather than one long row. Nine unlocked
	# emplacements at 126 px each is 1182 px of buttons on a 1920 px viewport — from Act II
	# the bar lay across the middle of the board and covered the slots it was for. LF-040.
	var unlocked: Array = Content.unlocked_at(view.anchor_id)
	var bar := GridContainer.new()
	bar.columns = 2
	bar.position = Vector2(28, BAR_TOP)
	bar.add_theme_constant_override("h_separation", 6)
	bar.add_theme_constant_override("v_separation", 6)
	root.add_child(bar)

	for tid in unlocked:
		var t: Dictionary = Content.tower(tid)
		var b := Button.new()
		b.text = "%s\n$%d · %d MW" % [t["name"], int(t["cost"]), int(t["draw_mw"])]
		b.custom_minimum_size = Vector2(150, BUTTON_H)
		Ui.style(b, 11)
		b.pressed.connect(_on_pick.bind(String(tid)))
		bar.add_child(b)
		_buttons.append(b)

	# The bar grows a row every time an act unlocks two more emplacements, so the panel
	# under it is placed against the bar's real height rather than a number that was true
	# on anchor-01.
	var rows: int = int(ceil(float(unlocked.size()) / 2.0))
	var bar_h: float = float(rows) * BUTTON_H + maxf(float(rows - 1), 0.0) * 6.0
	_build_inspector(root, BAR_TOP + bar_h + 16.0)
	_build_threat(root)

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


func _build_inspector(root: Control, top: float) -> void:
	## One panel that describes whichever emplacement the player is thinking about — the
	## one selected on the board, or failing that the one about to be built — and carries
	## the three verbs that act on it. The verbs act on `view.selected_slot`, never on the
	## hover: reaching a button means dragging the cursor across the board, and a
	## hover-targeted SELL sells whatever tile the cursor last crossed on its way over.
	##
	## Everything is placed relative to `top`, which is wherever the build bar ended.
	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = Vector2(16, top)
	panel.size = Vector2(330, PANEL_H)
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(panel)

	_kicker = _make_label(11, C_MUTED)
	_kicker.position = Vector2(28, top + 12)
	root.add_child(_kicker)

	_title = _make_label(20, C_BONE, false, true)
	_title.position = Vector2(28, top + 28)
	root.add_child(_title)

	_sub = _make_label(12, C_AMBER, true)
	_sub.position = Vector2(28, top + 56)
	root.add_child(_sub)

	root.add_child(_rule(top + 78))

	_body = _make_label(13, C_BONE, true)
	_body.position = Vector2(28, top + 86)
	root.add_child(_body)

	root.add_child(_rule(top + 228))

	_note = _make_label(12, C_MUTED)
	_note.position = Vector2(28, top + 236)
	_note.size = Vector2(306, 92)
	_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_note.clip_text = true
	root.add_child(_note)

	var verbs := HBoxContainer.new()
	verbs.position = Vector2(28, top + 336)
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
	_power_button.position = Vector2(28, top + 374)
	Ui.style(_power_button, 12)
	_power_button.pressed.connect(func(): view.toggle_at(view.selected_slot))
	root.add_child(_power_button)


func _build_threat(root: Control) -> void:
	## What is coming, and when. A tower defense played without this is a guessing game:
	## whether the wave carries air decides if a scan relay is worth 8 MW, whether it
	## carries shielded units decides between a lance and an arc node, and the wave's total
	## drain is the Act II and III power decision outright. All of it was in the anchor JSON
	## and none of it was on screen — the player could see a wave number and nothing else.
	##
	## Opposite the instrument column on purpose: the left column is what you own, this is
	## what is coming for it.
	## Sized against the busiest wave this anchor ever fields, not against a constant.
	## Anchor-01 runs one unit type and anchor-13 runs six; a fixed height leaves the first
	## as a mostly empty box with a rule floating in it.
	var groups := 1
	for w in view.sim.anchor["waves"]:
		groups = maxi(groups, Array(w.get("spawns", [])).size())
	var body_h := float(groups) * 2.0 * _mono_line_h(THREAT_BODY_SIZE)
	var rule_y := 104.0 + body_h + 10.0
	var footer_y := rule_y + 8.0
	var alert_y := footer_y + 40.0

	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = Vector2(THREAT_X, 16)
	panel.size = Vector2(380, alert_y + 32.0 - 16.0)
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(panel)

	_threat_kicker = _make_label(11, C_MUTED)
	_threat_kicker.position = Vector2(THREAT_X + 12, 28)
	root.add_child(_threat_kicker)

	_threat_title = _make_label(20, C_BONE, false, true)
	_threat_title.position = Vector2(THREAT_X + 12, 44)
	root.add_child(_threat_title)

	_threat_sub = _make_label(14, C_AMBER, true, true)
	_threat_sub.position = Vector2(THREAT_X + 12, 72)
	root.add_child(_threat_sub)

	root.add_child(_rule(96, THREAT_X + 12, 356))

	_threat_body = _make_label(11, C_BONE, true)
	_threat_body.position = Vector2(THREAT_X + 12, 104)
	root.add_child(_threat_body)

	root.add_child(_rule(rule_y, THREAT_X + 12, 356))

	_threat_footer = _make_label(12, C_MUTED, true)
	_threat_footer.position = Vector2(THREAT_X + 12, footer_y)
	root.add_child(_threat_footer)

	_threat_alert = _make_label(12, C_ALERT, false, true)
	_threat_alert.position = Vector2(THREAT_X + 12, alert_y)
	_threat_alert.size = Vector2(356, 28)
	_threat_alert.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	root.add_child(_threat_alert)


func _mono_line_h(size: int) -> float:
	## Measured from the font, not assumed. A Label stacks its `line_spacing` theme constant
	## (3 by default) on top of the face's own height, so an 11 px mono line is about 19 px,
	## not 15 — and a guessed 15 drew the footer on top of the last two rows of the unit list.
	return Ui.MONO.get_height(size) + 3.0


func _rule(y: float, x: float = 28.0, w: float = 306.0) -> ColorRect:
	var r := ColorRect.new()
	r.color = Color(C_MUTED, 0.28)
	r.position = Vector2(x, y)
	r.size = Vector2(w, 1)
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
	_refresh_threat()

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


# ───────────────────────────────────────────────────────────── threat ──

func _refresh_threat() -> void:
	var sim = view.sim
	var waves: Array = sim.anchor["waves"]
	var idx: int = view.wave_number() - 1
	if idx < 0 or idx >= waves.size():
		return
	var phase: String = view.phase()
	var spawns: Array = waves[idx].get("spawns", [])

	_threat_kicker.text = "INCOMING"
	_threat_title.text = "WAVE %d OF %d" % [idx + 1, waves.size()]

	# The prep clock was already running and only the sim could see it. Twenty seconds is
	# a bounty spent or not spent, so it is the number the player is actually working to.
	match phase:
		"prep":
			_threat_sub.text = "SPAWNS IN %d s" % ceili(view.lead_left())
			_threat_sub.add_theme_color_override("font_color", C_AMBER)
		"combat":
			_threat_sub.text = "IN PROGRESS · %d STILL UP" % _alive_count()
			_threat_sub.add_theme_color_override("font_color", C_ALERT)
		_:
			_threat_sub.text = "ANCHOR HELD" if phase == "done" else "ANCHOR LOST"
			_threat_sub.add_theme_color_override("font_color",
					C_VERD if phase == "done" else C_ALERT)

	var lines: Array[String] = []
	var total_units := 0
	var total_drain := 0.0
	var has_air := false
	for spawn in spawns:
		var e: Dictionary = Content.enemy(String(spawn.get("enemy", "")))
		if e.is_empty():
			continue
		var count: int = int(spawn.get("count", 1))
		total_units += count
		total_drain += float(e.get("drains_mw", 0.0)) * float(count)
		if String(e.get("kind", "ground")) == "air":
			has_air = true
		lines.append("%2d x  %s" % [count, String(e["name"])])
		lines.append("      %s" % _enemy_traits(e))
	_threat_body.text = "\n".join(lines)

	var footer: Array[String] = ["%d units" % total_units]
	if total_drain > 0.0:
		# The one number that decides an Act II or III build: every unit alive is capacity
		# stolen, so this is how far the bus is about to be pushed over on its own.
		footer.append("up to %d MW stolen while alive" % roundi(total_drain))
	_threat_footer.text = "\n".join(footer)

	# A pulse turret is rated for air and still cannot touch it unless a scan relay is
	# revealing it. That rule is invisible, costs the anchor, and is worth stating outright.
	_threat_alert.text = ("AIR IN THIS WAVE — NOTHING CAN ENGAGE IT WITHOUT A SCAN RELAY ONLINE"
			if has_air and not _has_reveal() else "")


func _enemy_traits(e: Dictionary) -> String:
	## The row under a unit's name: what it takes to kill and what it does to the bus.
	var parts: Array[String] = ["%d hp" % int(e.get("hp", 0)), "%.2f spd" % float(e.get("speed", 1.0))]
	if String(e.get("kind", "ground")) == "air":
		parts.append("AIR")
	if bool(e.get("shielded", false)):
		parts.append("SHIELD")
	if float(e.get("armour", 0.0)) > 0.0:
		parts.append("ARM %d" % int(e["armour"]))
	if float(e.get("drains_mw", 0.0)) > 0.0:
		parts.append("DRAIN %d" % int(e["drains_mw"]))
	return " · ".join(parts)


func _alive_count() -> int:
	var n := 0
	for u in view.sim.units:
		if u["alive"]:
			n += 1
	return n


func _has_reveal() -> bool:
	for p in view.sim.placed:
		if p["online"] and String(Dictionary(p["tower"].get("effect", {})).get("type", "")) == "reveal":
			return true
	return false


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
