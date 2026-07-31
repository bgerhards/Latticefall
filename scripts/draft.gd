extends Control
## Debrief and recovery draft — shown after an anchor clears, before returning to anchor
## select. The one place the game tells the player they did *well*, not merely that they
## finished, and the one place the campaign accumulates: three recovered Ordinal fragments,
## keep one for the rest of the run.
##
## Wired into the win path (LF-065): hud.gd's DEBRIEF button (`_on_next()`) changes scene
## here instead of reloading straight into the next anchor, reading `Progress.selected_anchor`
## which is still the anchor just cleared at that point. Also reachable and screenshottable
## on its own via `-- --draft`, and stands on Progress + Recoveries either way.
##
## LF-070: the recovery-taken reaction (`_show_reaction()` below) is its own small,
## non-anchor-scoped dialog concept — `data/dialog/recovery-taken.json` against
## `data/schema/recovery-dialog.schema.json` — rather than an instance of `dialog_view.gd`,
## which is built around `AnchorView.anchor_id` and `dialog_trigger` and has no notion of a
## screen that is not any particular anchor's.
##
## Built the same way menu.gd builds the anchor grid: one scroll region carrying a column,
## because the alternative — three cards laid out at a fixed width — is exactly what
## breaks at 200% interface scale (960x540). The cards themselves reflow into a
## GridContainer whose column count is solved from the live viewport, same technique as
## menu.gd's anchor grid.

const MENU_SCENE := "res://scenes/menu.tscn"
const CARD_W := 420.0
const CARD_GAP := 16.0

const C_VERD := Ui.C_VERD
const C_AMBER := Ui.C_AMBER
const C_ALERT := Ui.C_ALERT
const C_MUTED := Ui.C_MUTED
const C_BONE := Ui.C_BONE
const C_PANEL := Ui.C_PANEL

var _anchor_id: String = "anchor-01"
var _difficulty: String = "standard"
var _seed: int = -1
## `-- --draft-lives <left> <started>` overrides the grade shown, independent of whatever
## is actually in the save. Screenshots have to be reproducible without first playing (and
## winning) a whole anchor to populate Progress.
var _override_left: int = -1
var _override_started: int = -1
var _grade: Dictionary = {}
var _offer_ids: Array = []

## `-- --shot <path> [frame]` / `-- --a11y <path>`, same contract as menu.gd and main.gd:
## render, capture on a known frame, quit. A screen unreachable at `--fixed-fps` is a screen
## nobody has looked at.
var _shot_path: String = ""
var _shot_at: int = 30
var _shot_frame: int = 0
var _a11y_path: String = ""

var _first_take: Button = null
var _scroll: ScrollContainer
var _col: VBoxContainer = null
var _grid: GridContainer = null
var _take_buttons: Array[Button] = []
## LF-070: the recovery-taken reaction lines, loaded once from their own small schema (see
## the file docstring). Populated in `_ready()`, read only by `_show_reaction()`.
var _reaction_lines: Array = []
var _reaction_box: VBoxContainer = null
var _continue_button: Button = null
## `-- --auto-take [N]` (1-based card index, default 1): verification hook — presses the
## Nth card's TAKE button at boot, exactly as a click would. The reaction it triggers is
## otherwise unreachable at `--fixed-fps` for the same reason every other click-driven state
## in this codebase gets one (see `--focus-card`'s own note, and main.gd's `--select`/
## `--pick`/`--cursor`).
var _auto_take: bool = false
## `-- --focus-card N` (1-based) grabs keyboard/gamepad focus on the Nth card's TAKE button
## at boot instead of the first. The scroll region's `follow_focus` (see Ui.scroller()) then
## carries it into view exactly as it would for a real player tabbing down past the first
## card — which is the actual claim worth proving at 200% interface scale, where the first
## card alone fills the screen. A raw scroll-offset hook would fight follow_focus instead
## of demonstrating it: the container re-centres on whatever holds focus once its layout
## resolves, which is the whole point of follow_focus and not a bug to route around.
var _focus_card: int = 0


func _ready() -> void:
	RenderingServer.set_default_clear_color(Color(0.055, 0.078, 0.09))
	_parse_cli()
	_grade = _compute_grade()
	_offer_ids = Recoveries.offer(_seed)
	_reaction_lines = _load_reaction_lines()
	# Verification-only, unconditional (unlike DRAFTSHOT/DRAFT below, which only print under
	# --shot): LF-065's scene-transition proof needs evidence that this screen actually booted
	# even on a run that never asks it for a screenshot — hud.gd's win path reaching this
	# scene at all is the thing in question, not what it looks like once here.
	print("DRAFT-BOOT anchor=%s difficulty=%s verdict=%s offer=%s"
		% [_anchor_id, _difficulty, String(_grade.get("verdict", "")), ",".join(_offer_ids)])
	_build()
	# Deferred, not immediate: grab_focus() the same frame a Control is added asks
	# ScrollContainer.follow_focus to centre on geometry the layout pass has not run yet,
	# so it silently does nothing — the scroll region measures 0-sized content and has
	# nowhere to scroll to. One idle frame later the column has been sorted and every
	# card has its real position, which is what follow_focus needs to carry the focused
	# card into view.
	call_deferred("_apply_initial_focus")
	if _auto_take and not _offer_ids.is_empty():
		# Queued after _apply_initial_focus above — Godot's deferred-call queue is FIFO, so
		# this always runs second and its own focus grab (_focus_continue, inside
		# _show_reaction()) is the one that sticks.
		call_deferred("_do_auto_take")


func _apply_initial_focus() -> void:
	if _focus_card >= 1 and _focus_card <= _take_buttons.size():
		_take_buttons[_focus_card - 1].grab_focus()
	elif _first_take != null:
		_first_take.grab_focus()


func _do_auto_take() -> void:
	var idx := clampi(_focus_card - 1, 0, _offer_ids.size() - 1) if _focus_card >= 1 else 0
	var id := String(_offer_ids[idx])
	print("AUTO-TAKE id=%s" % id)
	_on_take(id)


func _load_reaction_lines() -> Array:
	## Reads data/dialog/recovery-taken.json directly against its own small schema
	## (data/schema/recovery-dialog.schema.json) — not Content.dialog()/dialog_view.gd's
	## anchor-cross-referenced path, since this reaction is not scoped to any one anchor (see
	## the file docstring). Follows Content._read()'s own open/parse/verify shape, the same
	## precedent recoveries.gd's _load() already set for data/tuning.json, rather than
	## reaching into Content's private method a second, different way.
	var f := FileAccess.open("res://data/dialog/recovery-taken.json", FileAccess.READ)
	if f == null:
		push_error("draft: cannot open data/dialog/recovery-taken.json (%d)"
			% FileAccess.get_open_error())
		return []
	var doc: Variant = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY:
		push_error("draft: data/dialog/recovery-taken.json is not a JSON object")
		return []
	return doc.get("lines", [])


func _parse_cli() -> void:
	_anchor_id = Progress.selected_anchor
	_difficulty = Progress.difficulty
	var argv := OS.get_cmdline_user_args()
	for i in range(argv.size()):
		match argv[i]:
			"--anchor":
				if i + 1 < argv.size():
					_anchor_id = argv[i + 1]
			"--difficulty":
				if i + 1 < argv.size():
					_difficulty = argv[i + 1]
			"--seed":
				if i + 1 < argv.size() and argv[i + 1].is_valid_int():
					_seed = int(argv[i + 1])
			"--draft-lives":
				if i + 1 < argv.size() and argv[i + 1].is_valid_int():
					_override_left = int(argv[i + 1])
				if i + 2 < argv.size() and argv[i + 2].is_valid_int():
					_override_started = int(argv[i + 2])
			"--shot":
				if i + 1 < argv.size():
					_shot_path = argv[i + 1]
				if i + 2 < argv.size() and argv[i + 2].is_valid_int():
					_shot_at = int(argv[i + 2])
			"--a11y":
				if i + 1 < argv.size():
					_a11y_path = argv[i + 1]
			"--focus-card":
				if i + 1 < argv.size() and argv[i + 1].is_valid_int():
					_focus_card = int(argv[i + 1])
			"--auto-take":
				_auto_take = true
	if _seed == -1:
		_seed = hash(_anchor_id)


func _compute_grade() -> Dictionary:
	if _override_left >= 0 and _override_started > 0:
		var frac := float(_override_left) / float(_override_started)
		return {
			"anchor_id": _anchor_id, "difficulty": _difficulty,
			"lives_left": _override_left, "lives_started": _override_started,
			"lives_frac": frac, "verdict": Recoveries.verdict_name(frac),
		}
	var g := Recoveries.grade_for(_anchor_id, _difficulty)
	if not g.is_empty():
		return g
	# Reached with no matching clear on record — e.g. `--draft` on a fresh save. Still has
	# to render something rather than divide by zero or show blank fields.
	var started := int(Content.anchor(_anchor_id).get("lives", 10))
	return {
		"anchor_id": _anchor_id, "difficulty": _difficulty,
		"lives_left": 0, "lives_started": started, "lives_frac": 0.0,
		"verdict": Recoveries.verdict_name(0.0),
	}


# ────────────────────────────────────────────────────────────────── build ──

func _build() -> void:
	set_anchors_preset(Control.PRESET_FULL_RECT)

	_scroll = Ui.scroller()
	_scroll.set_anchors_preset(Control.PRESET_FULL_RECT)
	var vp := get_viewport().get_visible_rect().size
	var margin := minf(120.0, vp.x * 0.0625)
	_scroll.offset_left = margin
	_scroll.offset_top = minf(90.0, vp.y * 0.083)
	_scroll.offset_right = -margin
	_scroll.offset_bottom = -minf(60.0, vp.y * 0.055)
	add_child(_scroll)

	var col := VBoxContainer.new()
	col.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	col.add_theme_constant_override("separation", 10)
	_scroll.add_child(col)
	_col = col

	col.add_child(Ui.label("DEBRIEF", Ui.SIZE_CAPTION, C_MUTED))
	_build_verdict(col)

	var gap := Control.new()
	gap.custom_minimum_size = Vector2(0, 22)
	col.add_child(gap)

	if _offer_ids.is_empty():
		col.add_child(Ui.label("RECOVERED", Ui.SIZE_LEAD, C_BONE, false, true))
		col.add_child(Ui.label(
			"Every Ordinal fragment worth carrying already is. Nothing more to recover.",
			Ui.SIZE_BODY, C_MUTED))
	else:
		col.add_child(Ui.label("RECOVERED — CHOOSE ONE", Ui.SIZE_LEAD, C_BONE, false, true))
		col.add_child(Ui.label(
			"Kept for the rest of the campaign. The other two stay in the wreckage.",
			Ui.SIZE_BODY, C_MUTED))

	var gap2 := Control.new()
	gap2.custom_minimum_size = Vector2(0, 12)
	col.add_child(gap2)

	# Column count solved from the live viewport, same technique as menu.gd's anchor grid:
	# a GridContainer does not wrap on its own, it forces its parent wider, which is exactly
	# what put three side-by-side cards off the right edge of a 960px design space.
	var avail_w := vp.x - margin * 2.0 - Ui.SCROLL_GUTTER
	var cols := clampi(int(floorf((avail_w + CARD_GAP) / (CARD_W + CARD_GAP))), 1, 3)
	var grid := GridContainer.new()
	grid.columns = cols
	grid.add_theme_constant_override("h_separation", int(CARD_GAP))
	grid.add_theme_constant_override("v_separation", int(CARD_GAP))
	col.add_child(grid)
	_grid = grid

	for id in _offer_ids:
		grid.add_child(_build_card(id))

	if _offer_ids.is_empty():
		var cont := Ui.button("CONTINUE", Ui.SIZE_STAT, true)
		# VBoxContainer stretches a FILL child (the default) to its own width, which is the
		# whole scroll region — fine for a label, not for a button meant to read as one
		# action among the same-sized ones elsewhere on this screen.
		cont.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		cont.custom_minimum_size = Vector2(210, 42)
		cont.pressed.connect(func(): get_tree().change_scene_to_file(MENU_SCENE))
		col.add_child(cont)
		_first_take = cont


func _build_verdict(col: VBoxContainer) -> void:
	var n := 0
	if _anchor_id.length() >= 9:
		n = int(_anchor_id.substr(7))
	var verdict := String(_grade.get("verdict", ""))
	var frac := float(_grade.get("lives_frac", 0.0))

	var banner := Ui.label(verdict, Ui.SIZE_BANNER, _verdict_color(verdict, frac), false, true)
	col.add_child(banner)

	var sub := "ANCHOR %02d  ·  %s  ·  %d of %d lives held" % [
		n, String(_grade.get("difficulty", _difficulty)).to_upper(),
		int(_grade.get("lives_left", 0)), int(_grade.get("lives_started", 0))]
	col.add_child(Ui.label(sub, Ui.SIZE_STAT, C_MUTED, true))


func _verdict_color(verdict: String, frac: float) -> Color:
	match verdict:
		"HELD", "SECURED":
			return C_VERD
		"CONTESTED":
			return C_AMBER
		"MAULED":
			return C_ALERT
		_:
			return C_VERD if frac >= 0.8 else (C_AMBER if frac >= 0.5 else C_ALERT)


func _build_card(id: String) -> PanelContainer:
	var e := Recoveries.pool_entry(id)
	var card := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = C_PANEL
	sb.border_color = Color(C_MUTED, 0.28)
	sb.set_border_width_all(1)
	sb.set_content_margin_all(16)
	card.add_theme_stylebox_override("panel", sb)
	card.custom_minimum_size = Vector2(CARD_W, 0)

	var body := VBoxContainer.new()
	body.add_theme_constant_override("separation", 8)
	card.add_child(body)

	body.add_child(Ui.label("RECOVERED", Ui.SIZE_CAPTION, C_MUTED))
	body.add_child(Ui.label(String(e.get("name", id)), Ui.SIZE_LEAD, C_BONE, false, true))

	var blurb := Ui.label(String(e.get("blurb", "")), Ui.SIZE_BODY, C_MUTED)
	blurb.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	blurb.custom_minimum_size = Vector2(CARD_W - 32.0, 0)
	body.add_child(blurb)

	var rule := ColorRect.new()
	rule.color = Ui.C_RULE
	rule.custom_minimum_size = Vector2(0, 1)
	body.add_child(rule)

	var effect := Ui.label(Recoveries.effect_text(id), Ui.SIZE_BODY, C_AMBER, false, true)
	effect.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	effect.custom_minimum_size = Vector2(CARD_W - 32.0, 0)
	body.add_child(effect)

	var gap := Control.new()
	gap.custom_minimum_size = Vector2(0, 4)
	body.add_child(gap)

	var take := Ui.button("TAKE", Ui.SIZE_STAT, true)
	take.custom_minimum_size = Vector2(0, 42)
	take.pressed.connect(_on_take.bind(id))
	body.add_child(take)
	_take_buttons.append(take)
	if _first_take == null:
		_first_take = take

	return card


func _on_take(id: String) -> void:
	Recoveries.grant(id)
	_show_reaction(id)


func _show_reaction(id: String) -> void:
	## LF-070: recovery-taken finally has somewhere to fire — replaces the card grid with
	## the reaction lines and its own CONTINUE, so a second TAKE press cannot grant a second
	## fragment (the buttons that would do it are hidden with the rest of the grid) and the
	## player has read the line before the scene changes, rather than it being cut off by an
	## immediate transition.
	if _grid != null:
		_grid.visible = false
	print("RECOVERY-TAKEN id=%s lines=%d" % [id, _reaction_lines.size()])
	if _reaction_lines.is_empty():
		# Defensive only — data/schema/recovery-dialog.schema.json requires at least one line.
		get_tree().change_scene_to_file(MENU_SCENE)
		return
	if _reaction_box == null:
		_reaction_box = _build_reaction_box()
		_col.add_child(_reaction_box)
	_reaction_box.visible = true
	call_deferred("_focus_continue")


func _build_reaction_box() -> VBoxContainer:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)

	var gap := Control.new()
	gap.custom_minimum_size = Vector2(0, 4)
	box.add_child(gap)

	for line in _reaction_lines:
		var speaker := String(line.get("speaker", ""))
		var who := Ui.label(speaker.to_upper(), Ui.SIZE_CAPTION,
			C_AMBER if speaker == "control" else C_VERD, false, true)
		box.add_child(who)
		var text := Ui.label(String(line.get("text", "")), Ui.SIZE_BODY, C_BONE)
		text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		text.custom_minimum_size = Vector2(CARD_W * 2.0 + CARD_GAP - 32.0, 0)
		box.add_child(text)

	var gap2 := Control.new()
	gap2.custom_minimum_size = Vector2(0, 10)
	box.add_child(gap2)

	_continue_button = Ui.button("CONTINUE", Ui.SIZE_STAT, true)
	_continue_button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	_continue_button.custom_minimum_size = Vector2(210, 42)
	_continue_button.pressed.connect(func(): get_tree().change_scene_to_file(MENU_SCENE))
	box.add_child(_continue_button)

	return box


func _focus_continue() -> void:
	if _continue_button != null:
		_continue_button.grab_focus()


# ─────────────────────────────────────────────────────────────── verify ──

func _process(_delta: float) -> void:
	if _shot_path == "":
		return
	_shot_frame += 1
	if _shot_frame < _shot_at:
		return
	var path := _shot_path
	_shot_path = ""
	get_tree().paused = true
	await RenderingServer.frame_post_draw
	var img := get_viewport().get_texture().get_image()
	var err := img.save_png(path)
	print("DRAFTSHOT %s err=%d %dx%d" % [path, err, img.get_width(), img.get_height()])
	if _a11y_path != "":
		A11yProbe.write(_a11y_path, A11yProbe.capture(self, get_viewport(), {
			"scene": "draft", "anchor": _anchor_id, "shot": path,
		}))
	print("DRAFT anchor=%s difficulty=%s verdict=%s lives=%d/%d offer=%s"
		% [_anchor_id, _difficulty, String(_grade.get("verdict", "")),
		   int(_grade.get("lives_left", 0)), int(_grade.get("lives_started", 0)),
		   ",".join(_offer_ids)])
	print("PROGRESS %s" % Progress.report())
	get_tree().quit()
