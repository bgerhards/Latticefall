extends CanvasLayer
## Reactor readout, build bar, emplacement inspector, wave state. Instrument panel, not
## decoration.
##
## The inspector is the only place the game explains what an emplacement *does*. Before it,
## the build bar named a price and a draw and nothing else: range, rate of fire, what a
## weapon can shoot at, and what the support emplacements do at all were readable only in
## data/towers.json. Decision 035.

## Colours and sizes come from `Ui`: both are accessibility policy, and policy duplicated
## across five scripts cannot be audited in one place or corrected in one edit.
const C_VERD := Ui.C_VERD
const C_AMBER := Ui.C_AMBER
const C_ALERT := Ui.C_ALERT
const C_MUTED := Ui.C_MUTED
const C_BONE := Ui.C_BONE
const C_PANEL := Ui.C_PANEL

var view: Node2D
var _gauge: ColorRect
var _fill: ColorRect
var _load_label: Label
## Three fields, not one padded string. "funds $3100      lives 52      leaks 0" is 411 px
## of 18 px monospace in a 388 px column: it ran past the panel at every interface scale,
## and it only surfaced once the column started clipping its contents and `a11y.py` learned
## to measure a rect against its clip region. A string padded with spaces is a layout made
## of character counts, which is the same mistake as a hardcoded offset.
var _stat_funds: Label
var _stat_lives: Label
var _stat_leaks: Label
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

## The instrument column. Widened from 330 to 420 to carry the 16 px type ladder: the old
## column was sized around an 11-13 px ladder that failed the size floor everywhere, and
## raising the type without widening the column would simply clip it instead.
const COL_X := Ui.COL_X
const COL_W := Ui.COL_W
const PAD := Ui.PAD
const INNER_W := Ui.INNER_W
const BUTTON_H := 52.0
## The threat panel mirrors the instrument column on the right edge. Its x is computed from
## the live viewport rather than fixed at 1524, because the viewport is no longer always
## 1920 wide: the interface-scale setting divides the design space by the scale factor, and
## the resolution setting allows aspects other than 16:9. A hardcoded right edge is off
## screen at 125% and leaves a gap at 4:3.
const THREAT_W := Ui.THREAT_W
## The three verbs, pinned below the column's scroll region. Heights named rather than
## repeated: this block is measured twice, once to bound the scroller and once to lay it
## out, and two literals that must agree is one literal too many.
const VERB_H := 36.0
const POWER_H := 34.0
const VERBS_GAP := 6.0
const VERBS_H := 10.0 + VERB_H + VERBS_GAP + POWER_H + PAD

var _buttons: Array[Button] = []
var _root: Control
var _col_scroll: ScrollContainer
var _threat_scroll: ScrollContainer
var _relayout_queued := false


func _make_label(size: int, col: Color, mono: bool = false, bold: bool = false) -> Label:
	## Numbers are monospaced — the bus readout counts, and a proportional digit set
	## makes it jitter sideways as it does. See scripts/ui_theme.gd.
	return Ui.label("", size, col, mono, bold)


func bind(v: Node2D) -> void:
	## Built on an explicit call from main.gd rather than in _ready(). This node is now
	## an authored child of Main, so its _ready() runs *before* Main's — before the CLI
	## has chosen an anchor and before the sim exists.
	view = v
	_build_ui()
	# The whole panel geometry is a function of the viewport size, so a resize rebuilds it
	# rather than leaving panels laid out for a viewport that no longer exists. Both the
	# resolution and the interface-scale settings change that size while the game is running.
	get_viewport().size_changed.connect(_queue_relayout)
	view.state_changed.connect(refresh)
	view.wave_state.connect(func(_i, _n, _p): refresh())
	refresh()


func _queue_relayout() -> void:
	## Godot emits `size_changed` more than once for a single window change, and once per
	## frame while a resize is dragged. Rebuilding on each one frees nodes out from under an
	## in-flight signal, so coalesce to one rebuild per frame.
	if _relayout_queued:
		return
	_relayout_queued = true
	call_deferred("_do_relayout")


func _do_relayout() -> void:
	_relayout_queued = false
	if view == null or view.sim == null:
		return
	_build_ui()
	refresh()


func _build_ui() -> void:
	if _root != null:
		# Detach before freeing: `queue_free` runs at the end of the frame, so a rebuild
		# that only queued would draw the old HUD underneath the new one until then.
		remove_child(_root)
		_root.queue_free()
	_buttons.clear()

	var vp := get_viewport().get_visible_rect().size
	_root = Control.new()
	_root.set_anchors_preset(Control.PRESET_FULL_RECT)
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_root)

	# The margin is derived from the viewport, not fixed: see Ui.gutter(). At 200% the two
	# panels are 948 px of a 960 px design space and there is no 16 px margin to be had.
	var g := Ui.gutter(vp)

	# The column's readout lives inside a scroll region and its three verbs are pinned below
	# it, so the controls can never be the thing that leaves the screen — which is exactly
	# what happened above 125% before (decision 046). Everything from here is laid out in
	# the scroll content's own coordinates, so the running cursor starts at PAD rather than
	# at COL_X + PAD.
	var col := Control.new()
	col.mouse_filter = Control.MOUSE_FILTER_IGNORE

	var inner_x := PAD
	# A running cursor rather than a column of literals. Every offset this replaces was a
	# number that was right for one font size, and the type ladder has just changed under
	# all of them.
	var y := PAD

	var title := _make_label(Ui.SIZE_CAPTION, C_MUTED)
	title.text = "REACTOR BUS"
	title.position = Vector2(inner_x, y)
	col.add_child(title)
	y += Ui.line_h(Ui.SIZE_CAPTION, false) + 2.0

	_load_label = _make_label(Ui.SIZE_READOUT, C_AMBER, true, true)
	_load_label.position = Vector2(inner_x, y)
	col.add_child(_load_label)
	y += Ui.line_h(Ui.SIZE_READOUT) + 4.0

	_gauge = ColorRect.new()
	_gauge.color = Color(0.07, 0.10, 0.11)
	_gauge.position = Vector2(inner_x, y)
	_gauge.size = Vector2(INNER_W, 16)
	col.add_child(_gauge)

	_fill = ColorRect.new()
	_fill.color = C_VERD
	_fill.position = Vector2(inner_x, y)
	_fill.size = Vector2(0, 16)
	col.add_child(_fill)
	y += 16.0 + 4.0

	_fault = _make_label(Ui.SIZE_BODY, C_ALERT, false, true)
	_fault.position = Vector2(inner_x, y)
	col.add_child(_fault)
	y += Ui.line_h(Ui.SIZE_BODY, false)

	# The reactor panel is added after its contents because its height is only known once
	# they have been placed, then moved behind them. No hardcoded 108.
	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = Vector2.ZERO
	panel.size = Vector2(COL_W, y + PAD * 0.5)
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	col.add_child(panel)
	col.move_child(panel, 0)

	y += PAD * 1.5

	# Fields at fractions of the column, so each one is bounded by construction and the two
	# to its right do not shift sideways when funds gains a digit. The widths are unequal
	# because the values are: funds runs to five figures, leaks to two.
	_stat_funds = _make_label(Ui.SIZE_STAT, C_BONE, true)
	_stat_funds.position = Vector2(inner_x, y)
	col.add_child(_stat_funds)
	_stat_lives = _make_label(Ui.SIZE_STAT, C_BONE, true)
	_stat_lives.position = Vector2(inner_x + INNER_W * 0.40, y)
	col.add_child(_stat_lives)
	_stat_leaks = _make_label(Ui.SIZE_STAT, C_BONE, true)
	_stat_leaks.position = Vector2(inner_x + INNER_W * 0.72, y)
	col.add_child(_stat_leaks)
	y += Ui.line_h(Ui.SIZE_STAT) + 2.0

	_wave = _make_label(Ui.SIZE_STAT, C_MUTED, true)
	_wave.position = Vector2(inner_x, y)
	col.add_child(_wave)
	y += Ui.line_h(Ui.SIZE_STAT) + PAD * 1.5

	# Two across, inside the instrument column, rather than one long row. Nine unlocked
	# emplacements at 126 px each is 1182 px of buttons on a 1920 px viewport — from Act II
	# the bar lay across the middle of the board and covered the slots it was for. LF-040.
	var unlocked: Array = Content.unlocked_at(view.anchor_id)
	var bar := GridContainer.new()
	# Three across when the viewport is short. The interface-scale setting divides the
	# logical viewport by the scale factor, so at 125% the column has 864 px to fit what it
	# lays out in 1080 — and the build bar is the cheapest row to give back, because a
	# narrower button still holds the name and price on two lines.
	var bar_cols := 3 if vp.y < 950.0 else 2
	bar.columns = bar_cols
	bar.position = Vector2(inner_x, y)
	bar.add_theme_constant_override("h_separation", 6)
	bar.add_theme_constant_override("v_separation", 6)
	col.add_child(bar)

	var btn_w := (INNER_W - 6.0 * float(bar_cols - 1)) / float(bar_cols)
	for tid in unlocked:
		var t: Dictionary = Content.tower(tid)
		var b := Ui.button("%s\n$%d · %d MW"
			% [t["name"], int(t["cost"]), int(t["draw_mw"])], Ui.SIZE_BODY)
		b.custom_minimum_size = Vector2(btn_w, BUTTON_H)
		b.pressed.connect(_on_pick.bind(String(tid)))
		bar.add_child(b)
		_buttons.append(b)

	# The bar grows a row every time an act unlocks two more emplacements, so the panel
	# under it is placed against the bar's real height rather than a number that was true
	# on anchor-01.
	var rows: int = int(ceil(float(unlocked.size()) / float(bar_cols)))
	var bar_h: float = float(rows) * BUTTON_H + maxf(float(rows - 1), 0.0) * 6.0
	# The datasheet is not a fixed height: a weapon with splash that is not rated for
	# shielding runs eight rows where a support emplacement runs five. The rule under it was
	# at a hardcoded offset and the longest sheets already touched it. Measure instead.
	var stat_rows := 1
	# The note is reserved at the height of the longest note this anchor can show, measured
	# from the font. It used to be the column's one elastic element, shrinking to nothing to
	# keep the verbs on screen; the verbs are pinned now, so the note can simply be as tall
	# as it needs and the scroll region absorbs it.
	var note_lines := 0
	for tid in unlocked:
		var t: Dictionary = Content.tower(tid)
		stat_rows = maxi(stat_rows, _stats_text(t, t.get("upgrade", {})).split("\n").size())
		note_lines = maxi(note_lines, Ui.wrapped_lines(
			String(t.get("note", "")), INNER_W, Ui.SIZE_BODY))
	var col_h := _build_inspector(col, y + bar_h + PAD, stat_rows, note_lines)

	# The scroll region takes what the content wants, or what is left after the pinned
	# verbs, whichever is smaller. At 100% nothing scrolls and the verbs sit directly under
	# the note exactly as before; at 150% and above the readout scrolls under them.
	col.custom_minimum_size = Vector2(COL_W - Ui.SCROLL_GUTTER, col_h)
	_col_scroll = Ui.scroller()
	_col_scroll.position = Vector2(g, g)
	_col_scroll.size = Vector2(COL_W, minf(col_h, vp.y - g * 2.0 - VERBS_H))
	_col_scroll.add_child(col)
	_root.add_child(_col_scroll)

	_build_verbs(_root, Vector2(g, g + _col_scroll.size.y))
	_build_threat(_root, vp, g)

	# End-of-anchor banner. Centred, large, and the only place the game tells the player
	# how to get back out of a level — without it the only exit from a finished anchor is
	# a key nobody has been told about.
	# Centred and sized against the live viewport, not against 1920x1080.
	var banner_y := vp.y * 0.35
	_outcome = _make_label(Ui.SIZE_BANNER, C_BONE, false, true)
	_outcome.position = Vector2(0, banner_y)
	_outcome.size = Vector2(vp.x, Ui.line_h(Ui.SIZE_BANNER, false))
	_outcome.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_outcome.visible = false
	_root.add_child(_outcome)

	var hint_y := banner_y + Ui.line_h(Ui.SIZE_BANNER, false) + 6.0
	_outcome_hint = _make_label(Ui.SIZE_STAT, C_MUTED)
	_outcome_hint.position = Vector2(0, hint_y)
	_outcome_hint.size = Vector2(vp.x, Ui.line_h(Ui.SIZE_STAT, false))
	_outcome_hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_outcome_hint.visible = false
	_root.add_child(_outcome_hint)

	# What to do next, offered where the player is already looking. Without this the only
	# way on from a finished anchor was a key and a menu — LF-039.
	const ACTION_W := [250.0, 230.0, 200.0]
	_outcome_actions = HBoxContainer.new()
	_outcome_actions.add_theme_constant_override("separation", 10)
	_outcome_actions.visible = false
	# The old fixed x of 660 was centred for three buttons at one font size on a 1920 px
	# viewport, and for nothing else.
	_outcome_actions.position = Vector2(
		(vp.x - (ACTION_W[0] + ACTION_W[1] + ACTION_W[2] + 20.0)) * 0.5,
		hint_y + Ui.line_h(Ui.SIZE_STAT, false) + 24.0)
	_root.add_child(_outcome_actions)

	_next_button = Ui.button("", Ui.SIZE_STAT, true)
	_next_button.custom_minimum_size = Vector2(ACTION_W[0], 44)
	_next_button.pressed.connect(_on_next)
	_outcome_actions.add_child(_next_button)

	var replay := Ui.button("REPLAY ANCHOR", Ui.SIZE_STAT)
	replay.custom_minimum_size = Vector2(ACTION_W[1], 44)
	replay.pressed.connect(func(): get_tree().reload_current_scene())
	_outcome_actions.add_child(replay)

	var ops := Ui.button("OPERATIONS", Ui.SIZE_STAT)
	ops.custom_minimum_size = Vector2(ACTION_W[2], 44)
	ops.pressed.connect(func(): get_tree().change_scene_to_file(MENU_SCENE))
	_outcome_actions.add_child(ops)


func dialog_reserve(vp: Vector2) -> float:
	## What the threat panel must leave at the bottom of the screen. The dialog panel starts
	## to the right of the instrument column and runs to the right edge, and it is on a later
	## CanvasLayer — so it draws over whatever is beneath it. At 1920 the threat panel ends
	## hundreds of pixels above the band and this costs nothing; at 960 it is the difference
	## between reading the air warning and having a radio line sit on top of it.
	return Ui.gutter(vp) + Ui.dialog_h() + 8.0


func _build_inspector(col: Control, top: float, stat_rows: int, note_lines: int) -> float:
	## The readout half of the panel that describes whichever emplacement the player is
	## thinking about — the one selected on the board, or failing that the one about to be
	## built. The three verbs that act on it are pinned outside the scroll region by
	## `_build_verbs`, because a control the player must scroll to find is a control that
	## clipped as far as they are concerned.
	##
	## Everything is placed relative to `top`, which is wherever the build bar ended, and to
	## the measured height of the tallest datasheet and longest note this anchor can show.
	## Returns the height of the whole column's content.
	var inner_x := PAD
	var y := top + PAD

	_kicker = _make_label(Ui.SIZE_CAPTION, C_MUTED)
	_kicker.position = Vector2(inner_x, y)
	col.add_child(_kicker)
	y += Ui.line_h(Ui.SIZE_CAPTION, false) + 2.0

	_title = _make_label(Ui.SIZE_LEAD, C_BONE, false, true)
	_title.position = Vector2(inner_x, y)
	col.add_child(_title)
	y += Ui.line_h(Ui.SIZE_LEAD, false) + 2.0

	_sub = _make_label(Ui.SIZE_BODY, C_AMBER, true)
	_sub.position = Vector2(inner_x, y)
	col.add_child(_sub)
	y += Ui.line_h(Ui.SIZE_BODY) + 8.0

	col.add_child(_rule(y, inner_x, INNER_W))
	y += 8.0

	_body = _make_label(Ui.SIZE_BODY, C_BONE, true)
	_body.position = Vector2(inner_x, y)
	col.add_child(_body)
	y += float(stat_rows) * Ui.line_h(Ui.SIZE_BODY) + 10.0

	col.add_child(_rule(y, inner_x, INNER_W))
	y += 8.0

	# The note is reserved at its measured height rather than clipped to whatever is left.
	# It used to be the column's one elastic element — squeezed to nothing at raised
	# interface scales, and capped at five lines even at 100%, which cut the shield wall's
	# six-line note mid-sentence — because the alternative was pushing SELL, UPGRADE and the
	# power switch off the bottom of the screen. The verbs are pinned now, so that trade is
	# gone: the note gets the lines it needs and the scroll region carries the overflow.
	var note_h := float(note_lines) * Ui.line_h(Ui.SIZE_BODY, false)
	_note = _make_label(Ui.SIZE_BODY, C_MUTED)
	_note.position = Vector2(inner_x, y)
	_note.size = Vector2(INNER_W, note_h)
	_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_note.visible = note_h > 0.0
	col.add_child(_note)
	y += note_h + 10.0

	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = Vector2(0.0, top)
	panel.size = Vector2(COL_W, y - top)
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	col.add_child(panel)
	# Behind the labels it backs, which were added before it. The reactor panel above is a
	# sibling at a different y and does not overlap, so their relative order is immaterial.
	col.move_child(panel, 0)
	return y


func _build_verbs(root: Control, at: Vector2) -> void:
	## SELL, UPGRADE and the power switch, pinned flush under the column's scroll region.
	##
	## They are outside it deliberately. These are the only controls in the column that act
	## on the board — everything above them is a readout — and above 125% interface scale
	## there is not enough logical height for both the readout and the verbs. Scrolling the
	## readout under a fixed footer keeps every control on screen at every scale, which is
	## the whole of LF-045; the previous answer was to stop offering the scale.
	##
	## They act on `view.selected_slot`, never on the hover: reaching a button means dragging
	## the cursor across the board, and a hover-targeted SELL sells whatever tile the cursor
	## last crossed on its way over.
	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = at
	panel.size = Vector2(COL_W, VERBS_H)
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(panel)

	var inner_x := at.x + PAD
	var y := at.y + 10.0

	var verbs := HBoxContainer.new()
	verbs.position = Vector2(inner_x, y)
	verbs.add_theme_constant_override("separation", VERBS_GAP)
	root.add_child(verbs)

	var verb_w := (INNER_W - VERBS_GAP) / 2.0
	_sell_button = Ui.button("", Ui.SIZE_BODY)
	_sell_button.custom_minimum_size = Vector2(verb_w, VERB_H)
	_sell_button.pressed.connect(func(): view.sell_at(view.selected_slot))
	verbs.add_child(_sell_button)

	_upgrade_button = Ui.button("", Ui.SIZE_BODY)
	_upgrade_button.custom_minimum_size = Vector2(verb_w, VERB_H)
	_upgrade_button.pressed.connect(func(): view.upgrade_at(view.selected_slot))
	verbs.add_child(_upgrade_button)
	y += VERB_H + VERBS_GAP

	_power_button = Ui.button("", Ui.SIZE_BODY)
	_power_button.custom_minimum_size = Vector2(INNER_W, POWER_H)
	_power_button.position = Vector2(inner_x, y)
	_power_button.pressed.connect(func(): view.toggle_at(view.selected_slot))
	root.add_child(_power_button)


func _build_threat(root: Control, vp: Vector2, g: float) -> void:
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

	# Right-anchored to the live viewport. At 125% interface scale the design space is
	# 1536 px wide, and a panel pinned at 1524 would be a 12 px sliver at the edge.
	var px := vp.x - g - THREAT_W
	# Laid out in the scroll content's own coordinates, as the instrument column is.
	var inner_x := PAD
	var inner_w := Ui.THREAT_INNER_W
	var threat := Control.new()
	threat.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var y := PAD

	_threat_kicker = _make_label(Ui.SIZE_CAPTION, C_MUTED)
	_threat_kicker.position = Vector2(inner_x, y)
	threat.add_child(_threat_kicker)
	y += Ui.line_h(Ui.SIZE_CAPTION, false) + 2.0

	_threat_title = _make_label(Ui.SIZE_LEAD, C_BONE, false, true)
	_threat_title.position = Vector2(inner_x, y)
	threat.add_child(_threat_title)
	y += Ui.line_h(Ui.SIZE_LEAD, false) + 2.0

	_threat_sub = _make_label(Ui.SIZE_STAT, C_AMBER, true, true)
	_threat_sub.position = Vector2(inner_x, y)
	threat.add_child(_threat_sub)
	y += Ui.line_h(Ui.SIZE_STAT) + 8.0

	threat.add_child(_rule(y, inner_x, inner_w))
	y += 8.0

	_threat_body = _make_label(Ui.SIZE_BODY, C_BONE, true)
	_threat_body.position = Vector2(inner_x, y)
	threat.add_child(_threat_body)
	# Two lines per spawn group — a name row and a traits row — sized against the busiest
	# wave this anchor ever fields, not against a constant. Anchor-01 runs one unit type and
	# anchor-13 runs six; a fixed height leaves the first a mostly empty box.
	y += float(groups) * 2.0 * Ui.line_h(Ui.SIZE_BODY) + 10.0

	threat.add_child(_rule(y, inner_x, inner_w))
	y += 8.0

	_threat_footer = _make_label(Ui.SIZE_BODY, C_MUTED, true)
	_threat_footer.position = Vector2(inner_x, y)
	threat.add_child(_threat_footer)
	y += 2.0 * Ui.line_h(Ui.SIZE_BODY) + 8.0

	var alert_h := 2.0 * Ui.line_h(Ui.SIZE_BODY, false)
	_threat_alert = _make_label(Ui.SIZE_BODY, C_ALERT, false, true)
	_threat_alert.position = Vector2(inner_x, y)
	_threat_alert.size = Vector2(inner_w, alert_h)
	_threat_alert.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	threat.add_child(_threat_alert)
	y += alert_h + PAD

	var panel := ColorRect.new()
	panel.color = C_PANEL
	panel.position = Vector2.ZERO
	panel.size = Vector2(THREAT_W, y)
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	threat.add_child(panel)
	threat.move_child(panel, 0)

	# Bounded by the dialog band rather than by the viewport: the dialog draws over this
	# panel, so the panel stops above it and scrolls if the wave needs more room than that
	# leaves. At 1920x1080 this never engages; at 960x540 it is 78 px of unit rows.
	threat.custom_minimum_size = Vector2(THREAT_W - Ui.SCROLL_GUTTER, y)
	_threat_scroll = Ui.scroller()
	_threat_scroll.position = Vector2(px, g)
	_threat_scroll.size = Vector2(THREAT_W, minf(y, vp.y - g - dialog_reserve(vp)))
	_threat_scroll.add_child(threat)
	root.add_child(_threat_scroll)


func _rule(y: float, x: float = PAD, w: float = INNER_W) -> ColorRect:
	var r := ColorRect.new()
	r.color = Ui.C_RULE
	r.position = Vector2(x, y)
	r.size = Vector2(w, 1)
	r.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return r


func _on_pick(tid: String) -> void:
	view.select(tid)
	refresh()


func _unhandled_input(event: InputEvent) -> void:
	## `lf_panel_up` / `lf_panel_down` — PageUp/PageDown, or the right stick.
	##
	## The mouse wheel scrolls whichever panel it is over for free, but a keyboard or gamepad
	## player has no pointer to put over one, and neither instrument panel has a focus chain
	## for `follow_focus` to work with: their controls are bound to board actions instead
	## (`lf_sell`, `lf_upgrade`, `lf_power`, `lf_next`). Text that one input method cannot
	## reach is still loss of content under SC 1.4.4, so both panels move together on one
	## action pair. They only have anywhere to move at 150% and above.
	if event.is_action_pressed("lf_panel_down"):
		scroll_panels(1)
	elif event.is_action_pressed("lf_panel_up"):
		scroll_panels(-1)
	else:
		return
	get_viewport().set_input_as_handled()


func scroll_panels(steps: int) -> void:
	## Four body lines per press. Public because `-- --scroll N` drives it: a scroll region
	## that has never been screenshotted scrolled is a scroll region nobody has looked at,
	## and `--fixed-fps` has nobody to press PageDown.
	var step := steps * roundi(Ui.line_h(Ui.SIZE_BODY) * 4.0)
	var panels: Array[ScrollContainer] = []
	if _col_scroll != null:
		panels.append(_col_scroll)
	if _threat_scroll != null:
		panels.append(_threat_scroll)
	for s in panels:
		s.scroll_vertical += step


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
	_fill.size.x = INNER_W * clampf(load / maxf(cap, 1.0), 0.0, 1.0)
	_fill.color = C_ALERT if sim.brownout else C_VERD
	# The penalty scales with the overdraw (decision 022), so the readout has to show
	# the real number — a fixed "-40%" would be lying about the decision the player is
	# making, which is *how far* over to go, not merely whether.
	_fault.text = ("BUS OVERDRAW — ALL SYSTEMS %d%% FIRE RATE"
			% [-roundi(sim.penalty_now() * 100.0)]) if sim.brownout else ""

	_stat_funds.text = "funds $%d" % sim.funds
	_stat_lives.text = "lives %d" % sim.lives
	_stat_leaks.text = "leaks %d" % sim.leaks
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
	var total_leak_cost := 0
	var has_air := false
	for spawn in spawns:
		var e: Dictionary = Content.enemy(String(spawn.get("enemy", "")))
		if e.is_empty():
			continue
		var count: int = int(spawn.get("count", 1))
		total_units += count
		total_drain += float(e.get("drains_mw", 0.0)) * float(count)
		total_leak_cost += int(e.get("leak_cost", 1)) * count
		if String(e.get("kind", "ground")) == "air":
			has_air = true
		# Leak cost goes on the name row, not the trait row. The trait row is already the
		# widest string the panel can be asked to draw — 51 monospaced characters, which is
		# what THREAT_W is sized from — and another field there would run off the screen.
		# The name row is half that, and a unit that costs more than one life is exactly the
		# thing worth naming loudly. Decision 047.
		var cost := int(e.get("leak_cost", 1))
		lines.append("%2d x  %s%s" % [count, String(e["name"]),
			"   %d lives each" % cost if cost > 1 else ""])
		lines.append("      %s" % _enemy_traits(e))
	_threat_body.text = "\n".join(lines)

	# What the wave costs if none of it is stopped. Since decision 047 a leak costs the
	# unit's `leak_cost` rather than a flat life, so "5 units" and "11 lives" are different
	# numbers — and without this the player watches lives fall by four for one leak with
	# nothing on screen explaining why.
	var footer: Array[String] = ["%d units · %d lives if it all leaks"
		% [total_units, total_leak_cost]]
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
	##
	## HP is the *difficulty-scaled* figure, not the number in enemies.json. Brutal is
	## 1.55x (decision 014), so the panel used to tell a brutal player a Column had 520 hp
	## when the thing walking at them had 806. This panel exists to be planned against, and
	## the health bars over the units already scale — `anchor_view` divides by
	## `sim.hp_mult` — so the two readouts disagreed with each other as well as with the
	## board. LF-047.
	var hp: float = float(e.get("hp", 0)) * (view.sim.hp_mult if view != null and view.sim != null else 1.0)
	var parts: Array[String] = ["%d hp" % roundi(hp), "%.2f spd" % float(e.get("speed", 1.0))]
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
	# "hits: ground · air" reads as "cannot touch shielded units", which is not the rule.
	# An unrated weapon still lands SHIELD_LEAK of its damage, armour subtracted first
	# (decision 029) — the difference between a bad answer and no answer, and the player
	# was being shown the wrong one of those.
	if dmg > 0.0 and not Array(t.get("targets", [])).has("shielded"):
		rows.append(_row("vs shield", "%d%% damage" % roundi(view.sim.SHIELD_LEAK * 100.0), ""))
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
	var line := "%-9s %s" % [label, cur]
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
