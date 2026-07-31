@tool
extends Node2D
## Playable anchor. Owns an AnchorSim and renders it.
##
## The rules live entirely in AnchorSim, which is a port of the Python reference and
## is parity-tested against it on every commit. Nothing in this file may decide a
## rule — it drives the clock, draws the result, and turns clicks into calls.
##
## Sprites are albedo + glow pairs (decision 007); the glow child layer is modulated
## by bus load so a brownout visibly dims the board.
##
## `@tool` so the board draws in the editor. Before this, scenes/main.tscn was a bare
## Node2D that built every node in _ready(), which meant opening the project showed an
## empty grey viewport and a scene dock with one childless node — the level could not
## be seen or judged without pressing Run. The editor path reads the anchor JSON and
## draws tiles, path and slots only; it never constructs a sim, never touches Audio,
## and never runs the clock.

const AnchorSimScript := preload("res://scripts/anchor_sim.gd")
const IsoScript := preload("res://scripts/iso.gd")
const AnchorDataScript := preload("res://scripts/anchor_data.gd")
const SpritesScript := preload("res://scripts/sprites.gd")
## Preloaded rather than referenced by its `class_name` — a new class_name is invisible
## until the editor has imported once (the symptom is a hang, not an error; see CLAUDE.md),
## and a preload sidesteps depending on the global class cache's timing at all.
const AbilityStateScript := preload("res://scripts/abilities.gd")

signal state_changed
signal dialog_trigger(trigger: String)
signal wave_state(index: int, total: int, phase: String)

@export var anchor_id: String = "anchor-01":
	set(value):
		anchor_id = value
		if Engine.is_editor_hint():
			_editor_refresh()
@export var difficulty: String = "standard"


func _validate_property(property: Dictionary) -> void:
	## Turn anchor_id into a dropdown of the levels that actually exist, so switching
	## the previewed anchor in the inspector cannot be misspelled.
	if property.name == "anchor_id":
		property.hint = PROPERTY_HINT_ENUM
		property.hint_string = ",".join(AnchorDataScript.anchor_ids())

const NO_SLOT := Vector2i(-999, -999)

var sim
var selected_tower: String = ""
var hovered_slot: Vector2i = NO_SLOT
## The emplacement the inspector is pointed at. Distinct from `hovered_slot` on purpose:
## sell, upgrade and the power toggle used to act on whatever the cursor was over, and
## reaching those buttons means dragging the cursor off the board and across every tile
## in between — which silently retargeted them. Decision 035.
var selected_slot: Vector2i = NO_SLOT

var _accum: float = 0.0
var _wave_index: int = -1
var _queue: Array = []
var _qi: int = 0
var _wave_t: float = 0.0
var _lead_left: float = 0.0
var _phase: String = "idle"      # idle | prep | combat | done | lost
var _fired_triggers: Dictionary = {}
var _origin: Vector2 = Vector2.ZERO
var _sim_t: float = 0.0          # total simulated seconds, for reproducibility checks
var glow_layer: Node2D
var combat_fx: Node2D
var fx_additive: Node2D

# ────────────────────────────────────────────────────────────── pacing ──
#
# data/tuning.json's `pacing` block. See its own "note" for the reasoning: prep is twenty
# seconds of dead air on every wave, and none of this touches sim/engine.py — see the
# GDScript-only state block atop anchor_sim.gd for the parity argument this all rests on.

## Game-speed multiplier — cycled by the player, defaults to the slowest authored speed.
## Applied in _process() by stepping AnchorSimScript.DT more times per real second, never by
## changing DT itself, which is what keeps a run reproducible for tools/test_parity.py: DT is
## the same 1/30 s a graded run ticks at regardless of what a *player's* clock is doing.
var speed: float = 1.0
var _speeds: Array = [1.0]

const LOW_LIVES_FRAC := 0.5      ## mirrors tuning.json grade.thresholds' CONTESTED cutoff
const CHAIN_HIGH_STREAK := 5     ## kills chained together before Control remarks on it

var chain_count: int = 0
var chain_mult: float = 0.0
var _chain_last_t: float = -999.0
var _wave_start_leaks: int = 0

# ─────────────────────────────────────────────────────────── abilities ──
#
## Charge/cooldown/duration bookkeeping for Threshold Surge, Overcharge and Shutter —
## see scripts/abilities.gd's own docstring for why this is a plain object rather than an
## autoload, and why the rules it triggers live on AnchorSim itself.
var abilities = null
## Enemy ids queued while Shutter is down, in the order they would have spawned — released
## in that order the instant the plate lifts (data/tuning.json's own note on Shutter).
var _shutter_held: Array = []

## Screen shake. Applied to this node's own `position` — every other draw call in this file
## adds `_origin` to points instead of using the node transform, which left `position` sitting
## completely unused, and every child layer (glow, board props, combat FX) inherits it for
## free as a result. `add_trauma()` is combat_fx.gd's only way to reach this.
const TRAUMA_DECAY: float = 1.7      # per second
const TRAUMA_MAX_OFFSET: float = 16.0  # px — capped hard; this must never fight readability
var _trauma: float = 0.0
var _shake_t: float = 0.0
var _shake_noise := FastNoiseLite.new()

# ────────────────────────────────────────────────────────────── camera ──
#
# CAM-01. The transform split promised above is now load-bearing: `scale` is zoom,
# `_origin` is pan (derived from the two fields below, not written to directly any more),
# `position` stays shake's alone — `_update_shake()` never touches these and this section
# never touches `position`, so the two systems compose instead of fighting.
#
# `_cam_target` is a board-*projected* point — the same units `IsoScript.tile_to_screen()`
# returns, i.e. `_origin`'s own units before this issue, not a tile coordinate — namely the
# point that is placed at the strip's centre on screen. `Vector2` (float32) is fine here
# per this issue's own risk note: the camera is presentation, and a camera-space quantity
# must never leak into anchor_sim.gd or sim/engine.py, where float64 is required.

## Decision 056: fitting a big board into the strip is the camera's job, not the sprite
## library's — raising Iso.TILE_W/TILE_H or re-authoring the atlas were both rejected.
## `ZOOM_MIN` is therefore *derived* every time the camera is applied, from the loaded
## board's own tile-space extent against the strip that is actually available right now
## (`_min_zoom_for_board()`), never a literal: a 24-anchor board and a future 64x64
## theatre-scale board need different floors, and a hardcoded one for today's boards would
## silently stop fitting the day a bigger one ships. This constant is only the absolute
## floor beneath *that* derivation, so the projection cannot degenerate to a point when the
## strip itself is nearly zero-width (200% interface scale — LF-057/045: the two panels
## alone want 948 of the 960px design space there, leaving the strip formula almost nothing
## to fit into). Detail loss at the floor is accepted; see decision 056.
const ZOOM_MIN_FLOOR := 0.05
## 1.0 is the hard ceiling: the atlas is one orthographic scale (see this file's own header
## doc, decision 056's closing paragraph), so past 1.0 sprites soften and the only real fix
## is re-rendering 224 sprites for a feature whose value is inspection.
const ZOOM_MAX := 1.0
const ZOOM_WHEEL_FACTOR := 1.15  ## multiplicative, per wheel notch, about the cursor
const ZOOM_KEY_RATE := 1.6       ## exponential per second, held lf_zoom_in/out, about the strip centre
const EDGE_SCROLL_MARGIN := 48.0     ## px of the *strip's* own edge, not the window's
const EDGE_SCROLL_MAX_SPEED := 900.0 ## board px/sec at full depth into the margin, at zoom 1
const CURSOR_FOLLOW_INSET := 40.0    ## px kept clear of the strip edge when following the cursor

var _cam_target: Vector2 = Vector2.ZERO
var _cam_zoom: float = 1.0
var _zoom_min: float = ZOOM_MIN_FLOOR
var _cam_initialised: bool = false
var _default_cam_target: Vector2 = Vector2.ZERO  ## what lf_camera_reset returns to
var _panning: bool = false                        ## middle-drag in progress
var _mouse_seen: bool = false  ## a real (or synthetic --mouse-at) pointer has been placed;
                                ## edge-scroll stays off until then so a --shot run with no
                                ## mouse input at all — e.g. sitting at (0,0) in Xvfb — cannot
                                ## silently drift the camera and make two identical runs differ.


func _ready() -> void:
	glow_layer = get_node_or_null("GlowLayer")
	combat_fx = get_node_or_null("CombatFx")
	fx_additive = get_node_or_null("FxAdditive")
	_shake_noise.seed = 1337
	_shake_noise.frequency = 1.0
	set_process(false)
	if Engine.is_editor_hint():
		# A tool script would otherwise tick and take input inside the editor.
		set_process_unhandled_input(false)
		_editor_refresh()
		return
	set_process_unhandled_input(true)


func boot(aid: String, diff: String) -> void:
	## Called by main.gd after the CLI has been parsed, not from _ready().
	##
	## The scene now authors this node as a child of Main, so _ready() here runs
	## *before* Main._ready() and therefore before `--anchor` has been read. Doing
	## setup on an explicit call keeps the CLI able to choose the level.
	anchor_id = aid
	difficulty = diff
	var anchor: Dictionary = Content.anchor(anchor_id)
	if anchor.is_empty():
		push_error("anchor_view: no data for %s" % anchor_id)
		return
	sim = AnchorSimScript.new()
	# The player's recovered fragments are applied to the *data* here, not as branches inside
	# AnchorSim — see scripts/loadout.gd for why that keeps parity with sim/engine.py holding
	# by construction. Without this call the whole recovery draft is inert (LF-074).
	sim.setup(Loadout.anchor(anchor), Loadout.towers(Content.towers),
			Loadout.enemies(Content.enemies), difficulty)
	## The ninth recovery effect. Set from OUT here rather than read from inside
	## anchor_sim.gd, because that file is preloaded by scripts/test/parity.gd as a
	## `--script` MainLoop with no autoloads — one `Recoveries.` reference in the rules
	## takes the whole script down and returns 1,152 empty parity rows. Decision 054.
	sim.sell_refund_bonus = Recoveries.sell_refund_add()
	sim.brownout_changed.connect(_on_brownout)
	sim.unit_killed.connect(_on_unit_killed)
	sim.unit_leaked.connect(func(_u): Audio.sfx("ui_deny"))
	sim.unit_leaked.connect(_on_unit_leaked_dialog)
	sim.built.connect(_on_built)
	# ART-06: recoil is driven off the same `shot_fired` signal combat_fx.gd already
	# listens to (anchor_sim.gd:58) — presentation-only, and safe to write onto `p` for
	# the same reason `aim`/`view_bucket_head` already are (placed records are only ever
	# compared by `slot`). See `_on_shot_fired()`'s own doc.
	sim.shot_fired.connect(_on_shot_fired)

	# Abilities: GDScript-only bookkeeping (scripts/abilities.gd), fed from data/tuning.json
	# via the Tuning autoload — never read by AnchorSim itself (see the GDScript-only state
	# block atop anchor_sim.gd).
	abilities = AbilityStateScript.new(Tuning.abilities())
	_speeds = Tuning.speeds()
	speed = float(_speeds[0]) if _speeds.size() > 0 else 1.0

	# Veterancy ranks, resolved once here rather than read from inside anchor_sim.gd: the
	# kill thresholds are scaled by Recoveries.veterancy_mult() (a save-file concern the
	# parity-tested file must not touch — see Recoveries' own docstring), so this is where
	# `kills * mult` actually happens, and the sim is handed the final numbers.
	var vet_mult := Recoveries.veterancy_mult()
	var ranks: Array = []
	for r in Tuning.veterancy_ranks():
		ranks.append({
			"kills": roundi(float(r.get("kills", 0)) * vet_mult),
			"damage_mult": float(r.get("damage_mult", 1.0)),
			"range_mult": float(r.get("range_mult", 1.0)),
			# "name" was missing here: _draw_rank_pip() and hud.gd's _rank_name() both read
			# r.get("name", "") off this same array, so without it every rank resolved to
			# damage_mult/range_mult correctly but the pip and the inspector's rank line
			# stayed blank forever — found by --ability-at verification printing VET lines
			# that showed a II-strength multiplier next to an empty rank name.
			"name": String(r.get("name", "")),
		})
	sim.set_veterancy_ranks(ranks)

	var unlocked: Array = Content.unlocked_at(anchor_id)
	selected_tower = String(unlocked[0]) if unlocked.size() > 0 else ""

	# CAM-06/CAM-07: a fresh anchor invalidates both the tile cache (lazily, on the next
	# _draw_board() call comparing anchor_id) and the per-frame drawables() cache. The tile
	# cache does not need clearing here — `_tile_cache_anchor != anchor_id` already catches
	# it — but the frame key and the verification counter are reset explicitly so a second
	# boot() in the same process (e.g. a future level-select flow) cannot read a stale
	# drawables_rebuild_count left over from a previous anchor.
	_drawables_cache = []
	_drawables_cache_frame = -1
	_drawables_rebuild_count = 0

	# Everything the board itself needs is done above this line. combat_fx.bind() wires a
	# cosmetic layer and goes last, guarded, for exactly the failure this project just had:
	# a parse error in combat_fx.gd left the CombatFx node scriptless, `combat_fx.bind(sim)`
	# died on a missing method, and because it used to run *before* _apply_camera() the whole
	# board collapsed to a corner over one bad type inference in the FX layer. A presentation
	# node failing to load must never be able to take the playfield down with it.
	_apply_camera()
	queue_redraw()
	if combat_fx and combat_fx.has_method("bind"):
		combat_fx.bind(sim)
	elif combat_fx:
		push_warning("anchor_view: CombatFx node has no bind() — its script failed to load; combat FX will be silent this run")


func _editor_refresh() -> void:
	## Editor-only. No sim, no audio, no clock — just re-centre and repaint.
	if not is_inside_tree():
		return
	_apply_camera()
	queue_redraw()
	if glow_layer:
		glow_layer.queue_redraw()


var _autobuild := false


func autobuild() -> void:
	## Debug/smoke aid: build the way the 'cheap-mass' policy would, so combat can be
	## exercised without a human. Never called during normal play.
	##
	## It re-runs at the start of every wave, which is what the grading policies do —
	## they spend bounty income between waves. Building only once, before wave one, is a
	## different (and much worse) player: on anchor-01 it can afford three turrets out of
	## 300 starting funds and loses, while the policy that graded the level buys five
	## across six waves and clears it with all ten lives. A smoke test that plays a
	## strictly worse game than the one that was balanced is not evidence of anything.
	_autobuild = true
	_autobuild_step()


func _autobuild_step() -> void:
	var unlocked: Array = Content.unlocked_at(anchor_id)
	while sim.free_slots.size() > 0:
		var placed_one := false
		for tid in unlocked:
			var tw: Dictionary = Content.tower(tid)
			if int(tw["cost"]) > sim.funds:
				continue
			if sim.online_draw() + float(tw["draw_mw"]) > sim.capacity():
				continue
			if sim.build_at(String(tid), sim.free_slots[0]):
				placed_one = true
				break
		if not placed_one:
			return


func start() -> void:
	## Called by main after every listener is wired. Firing the opening brief from
	## _ready() emitted it before dialog_view existed, so the lines vanished.
	_begin_wave(0)
	_fire("brief")
	set_process(true)


func _anchor_data() -> Dictionary:
	## The level, whether or not a sim exists. The editor preview has no sim, and no
	## autoloads either, so it reads the file directly rather than through Content.
	if sim != null:
		return sim.anchor
	if Engine.is_editor_hint():
		return AnchorDataScript.anchor(anchor_id)
	return Content.anchor(anchor_id)


## One shared library for every previewed board in the editor, built on demand. Static so
## opening four anchor scenes does not parse the manifest four times.
static var _editor_lib: Node = null


func _sprite_lib() -> Node:
	## The Sprites autoload when it exists, and a private instance when it does not.
	##
	## In the editor the autoload is absent until the project has been reloaded since
	## sprites.gd became a tool script, which is why the preview used to draw flat-colour
	## tiles for no visible reason (LF-025). Building one here removes the failure mode
	## rather than coping with it — the same call anchor_data.gd makes for Content.
	var lib := get_node_or_null(^"/root/Sprites")
	if lib != null:
		return lib
	if not Engine.is_editor_hint():
		return null              # at runtime a missing autoload is a real fault, not a case
	if _editor_lib == null:
		_editor_lib = SpritesScript.new()
		_editor_lib.load_library()
	return _editor_lib


func _tex(sprite_name: String, yaw: int, pass_name: String) -> Texture2D:
	var lib := _sprite_lib()
	if lib == null or not lib.ok:
		return null
	return lib.get_tex(sprite_name, yaw, pass_name)


func _tex_for(d: Dictionary, pass_name: String) -> Texture2D:
	## ART-01: a drawable carrying "bucket" is a split base/head part (`get_bucket_tex()`);
	## one that carries "yaw" instead is an unsplit 4-yaw drawable — every unit today, and
	## any future tower that never gets a split render. Kept as one dispatch point rather
	## than duplicated at each of the three call sites that walk `drawables()`
	## (`_draw_entities()` here, `glow_layer.gd`, `fx_additive.gd` — the last only ever
	## reads unit entries, so it never takes the bucket branch, but the helper still has to
	## be correct for it).
	var lib := _sprite_lib()
	if lib == null or not lib.ok:
		return null
	if d.has("bucket"):
		return lib.get_bucket_tex(d["sprite"], int(d["bucket"]), pass_name)
	return lib.get_tex(d["sprite"], int(d["yaw"]), pass_name)


func _strip_geometry(vp: Vector2) -> Dictionary:
	## The free region between the two instrument panels, and its screen centre — the same
	## quantity the old `_centre()` computed as `centre_pt`, factored out because zoom-to-fit,
	## the pan clamp, edge-scroll and cursor-follow all need it. Not `vp * 0.5` — COL_W and
	## THREAT_W are different widths (420 vs 528), so the viewport's own centre sits 54px right
	## of the strip's; see the historical note this replaced in git blame for the full story of
	## why the asymmetric alternative was wrong.
	var g := Ui.gutter(vp)
	var bottom_reserve := g + Ui.dialog_h() + 8.0
	var w := vp.x - 2.0 * g - Ui.COL_W - Ui.THREAT_W
	var h := vp.y - g - bottom_reserve
	var centre := Vector2((vp.x + Ui.COL_W - Ui.THREAT_W) * 0.5,
		(g + (vp.y - bottom_reserve)) * 0.5)
	return {"centre": centre, "w": maxf(w, 1.0), "h": maxf(h, 1.0), "g": g}


func _min_zoom_for_board(strip: Dictionary, gw: int, gh: int) -> float:
	## Decision 056: the zoom floor is whatever it takes to fit *this* board's tile-space
	## extent into the strip that is actually available right now — never a literal. See the
	## field doc on `ZOOM_MIN_FLOOR` for the absolute floor beneath this and why it exists.
	var board_w: float = float(gw + gh) * IsoScript.TILE_W * 0.5
	var board_h: float = float(gw + gh) * IsoScript.TILE_H * 0.5
	if board_w <= 0.0 or board_h <= 0.0:
		return ZOOM_MAX
	return clampf(minf(strip["w"] / board_w, strip["h"] / board_h), ZOOM_MIN_FLOOR, ZOOM_MAX)


func _default_target(vp: Vector2, mid: Vector2, strip: Dictionary) -> Vector2:
	## Reproduces the old `_centre()`'s framing at zoom 1.0 exactly: the board's own centre,
	## nudged just far enough that the ring specifically — the far end of the path, where a
	## leak actually costs a life — never runs past the strip's margin, even on a board that
	## cannot possibly fit as a whole. Seeds `_cam_target` once, at boot; panning after that is
	## the player's to correct, not the camera's to keep re-imposing.
	##
	## WAR-01: an anchor now carries one ring per lane (`paths`, plural). The camera can only
	## shift, not stretch to cover two out-of-frame exits on opposite sides at once, so this
	## takes whichever lane's violation is largest per axis rather than trying to satisfy all
	## of them. A single-lane anchor has exactly one ring to consider, so this reproduces the
	## old behaviour there exactly.
	var lanes: Array = _anchor_data().get("paths", [])
	if lanes.is_empty():
		return mid
	var strip_centre: Vector2 = strip["centre"]
	var margin: float = maxf(strip["g"], 24.0)
	var nudge := Vector2.ZERO
	for lane_doc in lanes:
		var pts: Array = lane_doc.get("waypoints", [])
		if pts.is_empty():
			continue
		var ring: Array = pts[pts.size() - 1]
		var ring_screen: Vector2 = IsoScript.tile_to_screen(float(ring[0]), float(ring[1])) \
			+ (strip_centre - mid)
		var n := Vector2.ZERO
		if ring_screen.x < margin:
			n.x = margin - ring_screen.x
		elif ring_screen.x > vp.x - margin:
			n.x = (vp.x - margin) - ring_screen.x
		if ring_screen.y < margin:
			n.y = margin - ring_screen.y
		elif ring_screen.y > vp.y - margin:
			n.y = (vp.y - margin) - ring_screen.y
		if absf(n.x) > absf(nudge.x):
			nudge.x = n.x
		if absf(n.y) > absf(nudge.y):
			nudge.y = n.y
	return mid - nudge


func _clamp_target(strip: Dictionary, gw: int, gh: int, mid: Vector2) -> void:
	## The board's tile bounding box can never leave the strip entirely: the target ranges
	## over the board's own extent, inset by a margin, so some of the strip always still shows
	## board rather than letting the whole thing scroll out from under it.
	##
	## Deliberately never a hard lock to `mid`, even on an axis where the board already fits
	## the strip at this zoom (an anchor's board is never square, so this is most of the
	## range for one axis or the other). A hard lock was tried and rejected: `_zoom_at()`
	## solves for the exact target that keeps a specific board point under the cursor, and a
	## same-frame lock overwrote that solution back to centre, which broke the more specific,
	## player-facing, tested-to-the-pixel acceptance criterion ("the tile under the pointer
	## stays under the pointer... at every step from 1.0 down to ZOOM_MIN") in favour of a
	## framing convenience nobody asked to keep mid-interaction. The range below already
	## contains `mid`, so "centred when nothing has panned it" holds as the resting state —
	## seeded at boot by `_default_target()` — without fighting a live zoom or drag.
	var board_w: float = float(gw + gh) * IsoScript.TILE_W * 0.5
	var board_h: float = float(gw + gh) * IsoScript.TILE_H * 0.5
	var margin: float = maxf(strip["g"], 24.0)
	var kx: float = maxf((strip["w"] * 0.5 - margin) / _cam_zoom, 0.0)
	_cam_target.x = clampf(_cam_target.x, mid.x - board_w * 0.5 - kx, mid.x + board_w * 0.5 + kx)
	var ky: float = maxf((strip["h"] * 0.5 - margin) / _cam_zoom, 0.0)
	_cam_target.y = clampf(_cam_target.y, mid.y - board_h * 0.5 - ky, mid.y + board_h * 0.5 + ky)


func _apply_camera() -> void:
	## The single place that turns `_cam_target`/`_cam_zoom` into `_origin`/`scale`. Every
	## camera input (pan, wheel/key zoom, edge-scroll, cursor-follow, `--camera`) mutates the
	## two fields above and then calls this — it never writes `_origin` or `scale` itself.
	##
	## `scale` was off-limits before CAM-02: it is inherited by every child, and Backdrop used
	## to be one of them, sizing itself to get_viewport_rect().size independently of the board
	## — zooming AnchorView would have left the sky covering only part of the screen. CAM-02
	## moved Backdrop to a sibling of this node under Main so it no longer inherits anything
	## from here; BoardProps, CombatFx, GlowLayer and FxAdditive are still children and still
	## inherit `scale`, which is exactly what this issue's zoom needs them to do.
	var grid: Dictionary = _anchor_data().get("grid", {"w": 12, "h": 10})
	var gw: int = int(grid["w"])
	var gh: int = int(grid["h"])
	var mid := IsoScript.tile_to_screen(float(gw) * 0.5, float(gh) * 0.5)
	if Engine.is_editor_hint():
		# There is no game viewport while editing, and get_viewport_rect() would
		# return the editor's. Hang the board off this node's own origin instead,
		# so it is centred on wherever the node sits in the scene.
		_cam_zoom = 1.0
		scale = Vector2.ONE
		_cam_target = mid
		_origin = -mid
		return
	var vp := get_viewport_rect().size
	var strip := _strip_geometry(vp)
	_zoom_min = _min_zoom_for_board(strip, gw, gh)
	if not _cam_initialised:
		_cam_target = _default_target(vp, mid, strip)
		_default_cam_target = _cam_target
		_cam_initialised = true
	_cam_zoom = clampf(_cam_zoom, _zoom_min, ZOOM_MAX)
	_clamp_target(strip, gw, gh, mid)
	scale = Vector2(_cam_zoom, _cam_zoom)
	# See the derivation in this file's own camera section doc: for a board-projected point
	# p, screen = position + (p + origin) * scale must put _cam_target at the strip's centre,
	# i.e. (target + origin) * zoom == strip.centre, which solves to this.
	_origin = strip["centre"] / _cam_zoom - _cam_target


func _board_to_screen_global(tile: Vector2) -> Vector2:
	## The true on-screen (global/viewport) position of a tile, after both `_origin`/`scale`
	## (pan/zoom) and `position` (shake) — what cursor-follow and edge-scroll compare against
	## the strip rect, which is itself in that same global space.
	return position + to_screen(tile) * _cam_zoom


func _zoom_at(mouse_global: Vector2, factor: float) -> void:
	## Wheel zoom: the board point under the pointer stays under the pointer. Solve for the
	## `_cam_target` that keeps `board_pt` (the point currently under the cursor, read off the
	## *current* `_origin`) mapped to the same screen position once the new zoom is applied.
	var new_zoom := clampf(_cam_zoom * factor, _zoom_min, ZOOM_MAX)
	if is_equal_approx(new_zoom, _cam_zoom):
		return
	var vp := get_viewport_rect().size
	var strip := _strip_geometry(vp)
	var board_pt := to_local(mouse_global) - _origin
	_cam_target = board_pt - (mouse_global - position - strip["centre"]) / new_zoom
	_cam_zoom = new_zoom
	_apply_camera()


func _zoom_key(rate_dt: float) -> void:
	## Keyboard/gamepad zoom: about the strip's own centre, not the cursor — there is no
	## pointer to anchor to from a gamepad, and the mouse is irrelevant to this input path.
	_cam_zoom = clampf(_cam_zoom * exp(rate_dt), _zoom_min, ZOOM_MAX)
	_apply_camera()


func _cam_reset() -> void:
	_cam_target = _default_cam_target
	_cam_zoom = 1.0
	Audio.sfx("ui_click")
	_apply_camera()


func _follow_cursor() -> void:
	## Keyboard/gamepad board navigation pans just enough to keep the cursor inside an inset
	## of the strip. Deliberately not wired to the mouse — see `_unhandled_input`'s
	## InputEventMouseMotion branch, which updates `hovered_slot` on hover but never calls
	## this — panning on every hover would fight the player's own hand on the wheel or the
	## middle-drag. This is the *only* path that gives keyboard/gamepad panning, on purpose
	## (LF-052): it needs no separate pan control of its own.
	if Engine.is_editor_hint():
		return
	var vp := get_viewport_rect().size
	var strip := _strip_geometry(vp)
	var inset: float = maxf(strip["g"], CURSOR_FOLLOW_INSET)
	var rect := Rect2(strip["centre"] - Vector2(strip["w"], strip["h"]) * 0.5,
		Vector2(strip["w"], strip["h"])).grow(-inset)
	var pt := _board_to_screen_global(Vector2(hovered_slot))
	var shift := Vector2.ZERO
	if pt.x < rect.position.x:
		shift.x = pt.x - rect.position.x
	elif pt.x > rect.end.x:
		shift.x = pt.x - rect.end.x
	if pt.y < rect.position.y:
		shift.y = pt.y - rect.position.y
	elif pt.y > rect.end.y:
		shift.y = pt.y - rect.end.y
	if shift == Vector2.ZERO:
		return
	_cam_target += shift / _cam_zoom
	_apply_camera()


func _edge_scroll(delta: float) -> void:
	## Pointer within EDGE_SCROLL_MARGIN of the *strip's* own edge scrolls at a rate
	## proportional to depth into the margin — not the window edge, because the instrument
	## panels are opaque and sit at the window edge, so a window-edge margin would be
	## unreachable without the pointer first crossing an opaque panel.
	if Engine.is_editor_hint() or _panning or not _mouse_seen:
		return
	var vp := get_viewport_rect().size
	var strip := _strip_geometry(vp)
	var rect := Rect2(strip["centre"] - Vector2(strip["w"], strip["h"]) * 0.5,
		Vector2(strip["w"], strip["h"]))
	var m := get_global_mouse_position()
	if not rect.has_point(m):
		# LF-161: every branch below is a one-sided distance-to-edge check ("is m.x less
		# than the left threshold", "is m.x greater than the right threshold"), each clamped
		# to [0, 1] — but a one-sided clamp still saturates at 1.0 for a point arbitrarily
		# far *past* that threshold, and the strip's own edge sits directly against an
		# opaque instrument panel. So a pointer anywhere in the build palette (left panel)
		# or the threat sheet (right panel) satisfied "m.x < left threshold" or
		# "m.x > right threshold" just as well as one pixel inside the margin, and
		# produced the same full-speed push — moving to click a tower button scrolled the
		# board at EDGE_SCROLL_MAX_SPEED the instant the pointer crossed into the panel, not
		# only near it. Contained here: outside the strip entirely, no push at all.
		return
	var push := Vector2.ZERO
	if m.x < rect.position.x + EDGE_SCROLL_MARGIN:
		push.x = -clampf((rect.position.x + EDGE_SCROLL_MARGIN - m.x) / EDGE_SCROLL_MARGIN, 0.0, 1.0)
	elif m.x > rect.end.x - EDGE_SCROLL_MARGIN:
		push.x = clampf((m.x - (rect.end.x - EDGE_SCROLL_MARGIN)) / EDGE_SCROLL_MARGIN, 0.0, 1.0)
	if m.y < rect.position.y + EDGE_SCROLL_MARGIN:
		push.y = -clampf((rect.position.y + EDGE_SCROLL_MARGIN - m.y) / EDGE_SCROLL_MARGIN, 0.0, 1.0)
	elif m.y > rect.end.y - EDGE_SCROLL_MARGIN:
		push.y = clampf((m.y - (rect.end.y - EDGE_SCROLL_MARGIN)) / EDGE_SCROLL_MARGIN, 0.0, 1.0)
	if push == Vector2.ZERO:
		return
	_cam_target += push * (EDGE_SCROLL_MAX_SPEED / _cam_zoom) * delta
	_apply_camera()


func set_camera_override(tile: Vector2, zoom: float) -> void:
	## CAM-03's `--camera <x> <y> <zoom>` hook: point the camera at a board tile coordinate
	## directly, bypassing every interactive path, so a verification run's frame depends on
	## the flag alone. Called after boot() so it wins over the default framing boot() just
	## seeded; zoom is clamped the same as every other path (`_apply_camera()`), so an
	## out-of-range value clamps rather than producing an unreachable state.
	_cam_initialised = true
	_cam_target = IsoScript.tile_to_screen(tile.x, tile.y)
	_cam_zoom = zoom
	_apply_camera()


func camera_state() -> Dictionary:
	## What main.gd's CAMERA report line and the --a11y header read: the board tile the
	## camera is centred on, and the zoom — self-describing, so a report or a screenshot is
	## never silently paired with the wrong frame (CAM-03).
	var t := IsoScript.screen_to_tile(_cam_target)
	return {"x": t.x, "y": t.y, "zoom": _cam_zoom}


func camera_view_rect() -> Rect2:
	## CAM-04: the board-projected rectangle (`Iso.tile_to_screen`'s own units — the space
	## `_cam_target` itself always lives in) currently visible in the strip between the two
	## instrument panels. This is what the minimap draws as the camera box, and the region
	## size its keyboard/gamepad stepping (`pan_by()` below) moves by.
	##
	## Solved from the same equation `_apply_camera()` derives `_origin` from — screen =
	## (projected + origin) * zoom — inverted for `projected` at the strip's own corners.
	## Ignores shake's `position`: that is a presentation wobble under 16px
	## (`TRAUMA_MAX_OFFSET`) that would only ever jitter the box, never mean anything.
	if Engine.is_editor_hint() or sim == null:
		return Rect2()
	var vp := get_viewport_rect().size
	var strip := _strip_geometry(vp)
	var half := Vector2(strip["w"], strip["h"]) * 0.5
	var top_left: Vector2 = strip["centre"] - half
	return Rect2(top_left / _cam_zoom - _origin, Vector2(strip["w"], strip["h"]) / _cam_zoom)


func pan_by(delta_projected: Vector2) -> void:
	## CAM-04: nudge `_cam_target` by a screen-axis-aligned amount already expressed in the
	## camera's own projected units (`camera_view_rect()`'s own space) — the minimap's
	## focused keyboard/gamepad region-stepping is the only caller. Goes through the same
	## clamp/apply path every other camera input uses (`_apply_camera()`'s own doc), so a
	## step here can never walk the camera off the board, exactly like a drag or an
	## edge-scroll.
	_cam_target += delta_projected
	_apply_camera()


# ─────────────────────────────────────────────────────────────── clock ──

func _process(delta: float) -> void:
	if sim == null or _phase in ["done", "lost"]:
		return
	# `speed` steps AnchorSimScript.DT more times per real second; DT itself never changes —
	# see the field's own doc. Clamped before the multiply so a stall is still bounded in
	# real seconds first, then scaled, rather than a stall at 3x fast-forwarding 3x further.
	_accum += minf(delta, 0.25) * speed
	while _accum >= AnchorSimScript.DT:
		_accum -= AnchorSimScript.DT
		_sim_t += AnchorSimScript.DT
		_advance()
	_update_shake(delta)
	if _panning and not Input.is_mouse_button_pressed(MOUSE_BUTTON_MIDDLE):
		# The button-up event does not always reach this window — releasing outside it while
		# it is unfocused is the case this exists for. Checked every frame rather than only on
		# an event so a drag can never stay latched past the release itself.
		_panning = false
	if Display.edge_scroll:
		_edge_scroll(delta)
	if Input.is_action_pressed("lf_zoom_in"):
		_zoom_key(ZOOM_KEY_RATE * delta)
	elif Input.is_action_pressed("lf_zoom_out"):
		_zoom_key(-ZOOM_KEY_RATE * delta)
	queue_redraw()
	# GlowLayer, CombatFx and FxAdditive are separate CanvasItems: queue_redraw() on this
	# node does not propagate to children, so without this the additive glow pass drew
	# once on the first frame and never again — brownout dimming (decision 007) never
	# actually reached the screen. Combat FX needs the same per-frame redraw to animate.
	if glow_layer:
		glow_layer.queue_redraw()
	if combat_fx:
		combat_fx.queue_redraw()
	if fx_additive:
		fx_additive.queue_redraw()


func _advance() -> void:
	_tick_abilities()

	if _phase == "prep":
		_lead_left -= AnchorSimScript.DT
		sim.tick()
		if _lead_left <= 0.0:
			_phase = "combat"
			_fire("wave-start:%d" % (_wave_index + 1))
			wave_state.emit(_wave_index + 1, sim.anchor["waves"].size(), _phase)
		return

	if _phase == "combat":
		while _qi < _queue.size() and float(_queue[_qi][0]) <= _wave_t + 1e-9:
			# Shutter: "arrivals queue instead of spawning" is entirely a caller-side
			# decision — spawning was already driven from here, not from inside AnchorSim —
			# so withholding the call is the whole implementation. See set_shutter()'s doc.
			# WAR-01: the queue entry is now [time, lane, enemy_id] (sim.wave_queue()'s own
			# doc), so a held arrival has to remember its lane too, not just the enemy id.
			if abilities != null and abilities.is_active("shutter"):
				_shutter_held.append([_queue[_qi][1], _queue[_qi][2]])
			else:
				sim.spawn(String(_queue[_qi][2]), int(_queue[_qi][1]))
			_qi += 1
		sim.tick()
		_wave_t += AnchorSimScript.DT

		if sim.lives <= 0:
			_phase = "lost"
			Audio.stinger("SYS-LOS")
			_fire("debrief")
			state_changed.emit()
			return
		if _qi >= _queue.size() and not sim.any_alive():
			sim.prune_dead()
			_fire("wave-clear:%d" % (_wave_index + 1))
			# Clean sweep: this wave's own leak count, not the run's — the delta since
			# _begin_wave() snapshotted it. GDScript-only funds, exactly like the kill chain.
			if sim.leaks == _wave_start_leaks:
				var bonus := Tuning.clean_sweep_bonus(int(sim.anchor.get("act", 1)))
				if bonus > 0:
					sim.funds += bonus
					sim.funds_changed.emit(sim.funds)
					Audio.sfx("clean_sweep")
					# Verification-only: this branch had never fired before this session's
					# tooling exercised it (needs a wave with zero leaks). Printed rather than
					# silent so a run's own log is evidence the bonus actually paid out.
					print("CLEAN-SWEEP wave=%d bonus=%d funds=%d" % [_wave_index + 1, bonus, sim.funds])
			if _wave_index + 1 >= sim.anchor["waves"].size():
				_phase = "done"
				Audio.stinger("SYS-WIN")
				_fire("debrief")
			else:
				_begin_wave(_wave_index + 1)
			state_changed.emit()


func _tick_abilities() -> void:
	if abilities == null:
		return
	for id in abilities.tick(AnchorSimScript.DT):
		match String(id):
			"overcharge":
				sim.set_overcharge(false)
				Audio.sfx("overcharge_off")
				state_changed.emit()
			"shutter":
				sim.set_shutter(false)
				Audio.sfx("shutter_up")
				# Released in original queued order — a plain Array pop-from-front already
				# preserves that, so there is nothing more to get right here. Each entry is
				# [lane, enemy_id] (see the append site above).
				for held in _shutter_held:
					sim.spawn(String(held[1]), int(held[0]))
				_shutter_held.clear()
				state_changed.emit()
			_:
				pass


func add_trauma(amount: float) -> void:
	## combat_fx.gd's only way to shake the camera. Sources: heavy kills, mortar impacts,
	## and — the biggest by far — a leak, the worst thing that can happen to the player.
	_trauma = clampf(_trauma + amount, 0.0, 1.0)


func _update_shake(delta: float) -> void:
	## offset = trauma^2 * max_offset, so a small hit barely moves the camera and only a
	## leak or a heavy kill earns the full amount — a trauma system, not a jitter. Two
	## octaves of noise rather than raw sine so it reads as a shake rather than a wobble.
	## `Display.shake` is the accommodation: 0 disables this outright.
	_shake_t += delta
	_trauma = maxf(0.0, _trauma - TRAUMA_DECAY * delta)
	var shake: float = _trauma * _trauma * TRAUMA_MAX_OFFSET * Display.shake
	if shake <= 0.001:
		position = Vector2.ZERO
		return
	var nx := _shake_noise.get_noise_1d(_shake_t * 24.0) \
		+ _shake_noise.get_noise_1d(_shake_t * 55.0 + 41.0) * 0.5
	var ny := _shake_noise.get_noise_1d(_shake_t * 24.0 + 97.0) \
		+ _shake_noise.get_noise_1d(_shake_t * 55.0 + 133.0) * 0.5
	position = Vector2(nx, ny) * shake


func to_screen(tile: Vector2) -> Vector2:
	## Presentation helper for combat_fx/fx_additive: the same projection _draw_board() and
	## drawables() use, exposed so the FX layer never has to duplicate or re-derive _origin.
	return IsoScript.tile_to_screen(tile.x, tile.y) + _origin


func drawable_texture(sprite_name: String, yaw: int, pass_name: String) -> Texture2D:
	## Read-only accessor so fx_additive can redraw a unit's own albedo for a hit flash
	## without reaching into _sprite_lib() directly.
	return _tex(sprite_name, yaw, pass_name)


func _begin_wave(index: int) -> void:
	_wave_index = index
	sim.begin_wave(index)          # Act III: the bus loses its decay before the prep phase
	_wave_start_leaks = sim.leaks  # clean sweep is scored against this wave's own leaks
	if _autobuild:
		_autobuild_step()
	_queue = sim.wave_queue(index)
	_qi = 0
	_wave_t = 0.0
	_lead_left = float(sim.anchor["waves"][index].get("lead_in", 20.0))
	_phase = "prep"
	wave_state.emit(index + 1, sim.anchor["waves"].size(), _phase)


func _on_unit_killed(u: Dictionary) -> void:
	## Debris only under the big ones. Every construct dying in a cloud of rubble turns a
	## wave of drones into gravel; reserving it for heavies makes it read as mass rather
	## than as a death sound, and keeps it out of the way when six things die at once.
	Audio.sfx("warden_death")
	if float(u["kind"].get("hp", 0.0)) >= 150.0:
		Audio.sfx("debris_settle", -5.0)
	_charge_surge(u)
	_advance_chain(u)


func _charge_surge(u: Dictionary) -> void:
	## Threshold Surge charges on kills, not on a clock: leak_cost * charge_per_leak_cost,
	## scaled by Recoveries.surge_charge_mult() — a save-file concern read here rather than
	## inside anchor_sim.gd for the same reason veterancy's ranks are resolved in boot().
	if abilities == null:
		return
	var cfg := Tuning.ability("surge")
	var per := float(cfg.get("charge_per_leak_cost", 0.0))
	if per <= 0.0:
		return
	var leak_cost := int(u["kind"].get("leak_cost", 1))
	# `abilities` is untyped (see its declaration's own doc — a new class_name is invisible
	# until the editor has imported once, and typing this var AbilityState risks the same
	# parse-time hang), so `:=` on anything read through it cannot infer a type and fails to
	# parse the whole file. Explicit `bool` sidesteps that, matching the trap CLAUDE.md
	# documents for `sim` and any other untyped/Node2D-through receiver.
	var was_ready: bool = abilities.ready("surge")
	abilities.add_charge("surge", float(leak_cost) * per * Recoveries.surge_charge_mult())
	if not was_ready and abilities.ready("surge"):
		Audio.sfx("surge_ready")
		_fire("surge-ready")


func _advance_chain(u: Dictionary) -> void:
	## Kills inside chain_window_s of each other stack a bounty multiplier, capped at
	## chain_bounty_max (data/tuning.json `pacing`). GDScript-only funds layered on top of
	## the bounty AnchorSim._damage() already paid — see the note atop anchor_sim.gd's new
	## state block; this file is never read by tools/test_parity.py at all.
	var window := Tuning.chain_window_s()
	chain_count = (chain_count + 1) if (_sim_t - _chain_last_t) <= window else 1
	_chain_last_t = _sim_t
	chain_mult = minf(Tuning.chain_bounty_max(), Tuning.chain_bounty_per_kill() * float(chain_count))
	Audio.sfx("chain_up_%d" % clampi(chain_count, 1, 8))
	if chain_mult > 0.0:
		var base := int(float(u["kind"].get("bounty", 0.0)) * sim.bounty_mult)
		var bonus := roundi(float(base) * chain_mult)
		if bonus > 0:
			sim.funds += bonus
			sim.funds_changed.emit(sim.funds)
	if chain_count >= CHAIN_HIGH_STREAK:
		_fire("chain-high")


func debug_set_chain(n: int) -> void:
	## Verification-only: main.gd's `-- --chain N` CLI flag. Reaching a real N-kill streak
	## needs N kills inside chain_window_s of each other, which --fixed-fps has no player to
	## produce — the same reasoning every other CLI verification hook in main.gd is built on.
	chain_count = n
	_chain_last_t = _sim_t
	chain_mult = minf(Tuning.chain_bounty_max(), Tuning.chain_bounty_per_kill() * float(n))


func chain_active() -> bool:
	## Whether the streak is still "live" for display purposes — chain_count itself never
	## resets except on the next kill, so a HUD reading it directly would show a stale streak
	## forever between waves.
	return chain_count > 0 and (_sim_t - _chain_last_t) <= Tuning.chain_window_s()


func _on_unit_leaked_dialog(_u: Dictionary) -> void:
	## first-leak and low-lives are authored on nearly every anchor and, before this, never
	## fired — a leak read as a UI blip (a deny tone) and nothing else.
	_fire("first-leak")
	var starting := int(sim.anchor.get("lives", 10))
	if starting > 0 and float(sim.lives) / float(starting) <= LOW_LIVES_FRAC:
		_fire("low-lives")


func _on_built(_tower_id: String, _slot: Vector2i) -> void:
	## "Ward" is Control's word for a built emplacement (data/dialog's wards-half/wards-full
	## lines) — ward_engage_{1..6} is an indexed, counted cue, played on every build, and the
	## two dialog thresholds are the ring being half and fully engaged.
	# `sim` is untyped by this file's existing convention (see the field's own declaration
	# and CLAUDE.md's note on the parse-time trap), so both need an explicit type rather
	# than `:=`.
	var total: int = sim.placed.size() + sim.free_slots.size()
	var n: int = sim.placed.size()
	Audio.sfx("ward_engage_%d" % clampi(n, 1, 6))
	if total > 0 and n * 2 >= total:
		_fire("wards-half")
	if total > 0 and n >= total:
		_fire("wards-full")


func call_wave() -> void:
	## Skip the rest of prep and take call_bonus_per_sec funds per second not spent waiting —
	## twenty seconds of dead air turned into a decision (data/tuning.json `pacing`'s note).
	if sim == null or _phase != "prep":
		Audio.sfx("ui_deny")
		return
	var bonus := call_wave_bonus()
	sim.funds += bonus
	sim.funds_changed.emit(sim.funds)
	_lead_left = 0.0
	Audio.sfx("wave_call")
	_fire("wave-called")
	state_changed.emit()


func call_wave_bonus() -> int:
	## The bonus on offer *right now* — shown before the player commits, so calling the wave
	## is a decision rather than a surprise (the spec's own framing).
	return roundi(Tuning.call_bonus_per_sec() * lead_left())


func cycle_speed() -> void:
	if _speeds.is_empty():
		return
	var i := _speeds.find(speed)
	var ni := wrapi(i + 1, 0, _speeds.size())
	Audio.sfx("speed_up" if ni > maxi(i, 0) else "speed_down")
	speed = float(_speeds[ni])
	state_changed.emit()


func cycle_targeting() -> void:
	## First/last/strongest/weakest, cycled on the selected emplacement. "first" — furthest
	## along the path — is what an untouched placed record already does (see the comment in
	## anchor_sim.gd's _step()), so this never has to write a value for an anchor nobody has
	## touched targeting on.
	var i := placed_index_at(selected_slot)
	if i < 0:
		Audio.sfx("ui_deny")
		return
	var modes: Array = Tuning.targeting_modes()
	if modes.is_empty():
		return
	var p: Dictionary = sim.placed[i]
	var cur := String(p.get("target_mode", Tuning.targeting_default()))
	var mi := modes.find(cur)
	p["target_mode"] = String(modes[wrapi(mi + 1, 0, modes.size())])
	Audio.sfx("ui_click")
	state_changed.emit()


func activate_ability(id: String) -> Dictionary:
	## Returns {} when the ability did not fire (unknown id, or not ready). Otherwise a
	## Dictionary describing the outcome — for "surge" this is fire_surge()'s own
	## {"kills": int, "damage": float}, always tagged with "id" so a caller that fires several
	## abilities can tell results apart without re-deriving which one they came from. main.gd's
	## `--ability-at` verification hook is the only caller that reads this; every other call
	## site (the lf_ability_1/2/3 handlers in _action_input()) discards it exactly as it
	## discarded the previous void return.
	if sim == null or abilities == null or not abilities.ready(id):
		Audio.sfx("ui_deny")
		return {}
	var cfg := Tuning.ability(id)
	var result: Dictionary = {}
	match id:
		"surge":
			result = sim.fire_surge(cfg)
			Audio.sfx("surge_fire")
		"overcharge":
			sim.set_overcharge(true, float(cfg.get("fire_rate_bonus", 0.0)),
				float(cfg.get("draw_mult", 1.0)))
			Audio.sfx("overcharge_on")
		"shutter":
			sim.set_shutter(true, float(cfg.get("hold_tiles", 0.0)),
				float(cfg.get("draw_mw", 0.0)))
			Audio.sfx("shutter_down")
		_:
			return {}
	result["id"] = id
	add_trauma(float(cfg.get("trauma", 0.0)))
	abilities.began(id)
	if abilities.first_fire(id):
		_fire("%s-first" % id)
	state_changed.emit()
	return result


func shutter_queue_size() -> int:
	## Verification accessor: how many arrivals Shutter is currently holding back from
	## spawning at all (data/tuning.json's own note — "the wave does not go away, it arrives
	## all at once"). main.gd's `--ability-at` diagnostics read this; nothing else does.
	return _shutter_held.size()


func _on_brownout(active: bool) -> void:
	Audio.sfx("brownout_alarm" if active else "brownout_recover")
	Audio.set_brownout(active)
	if active:
		_fire("brownout")
	state_changed.emit()


func _fire(trigger: String) -> void:
	## Each trigger fires once. A repeated line reads as a bug to the player.
	if _fired_triggers.has(trigger):
		return
	_fired_triggers[trigger] = true
	dialog_trigger.emit(trigger)


# ─────────────────────────────────────────────────────────────── input ──

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		_mouse_seen = true
		var t := IsoScript.screen_to_tile(to_local(get_global_mouse_position()) - _origin)
		hovered_slot = Vector2i(roundi(t.x), roundi(t.y))
		if _panning:
			# Board space, not screen space: at zoom != 1 a screen-pixel drag has to move the
			# camera target by more (zoomed out) or less (zoomed in) than the mouse moved, or
			# the board visibly slides at the wrong speed under the cursor.
			_cam_target -= event.relative / _cam_zoom
			_apply_camera()
		queue_redraw()
	elif event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
			_click(hovered_slot)
		elif event.button_index == MOUSE_BUTTON_RIGHT and event.pressed:
			toggle_at(hovered_slot)
		elif event.button_index == MOUSE_BUTTON_MIDDLE:
			# Never left-drag: left click arms builds and selects emplacements (`_click()`
			# below), so drag-to-pan on it would turn every slipped click into a camera move
			# (LF-052). Middle is free for exactly this.
			_panning = event.pressed
		elif event.button_index == MOUSE_BUTTON_WHEEL_UP and event.pressed:
			_zoom_at(get_global_mouse_position(), ZOOM_WHEEL_FACTOR)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN and event.pressed:
			_zoom_at(get_global_mouse_position(), 1.0 / ZOOM_WHEEL_FACTOR)
	else:
		_action_input(event)


func _action_input(event: InputEvent) -> void:
	## Everything the mouse can do, without a mouse. LF-010.
	##
	## The cursor moves between *slots* rather than sweeping pixels: a virtual pointer on a
	## stick is slow and imprecise, and the only tiles that can be acted on are the slots
	## anyway. Directions are judged in screen space, because the player is looking at an
	## isometric projection and "up" has to mean up on the screen, not -y in tile space.
	if event.is_action_pressed("lf_up"):
		_step_cursor(Vector2(0, -1))
	elif event.is_action_pressed("lf_down"):
		_step_cursor(Vector2(0, 1))
	elif event.is_action_pressed("lf_left"):
		_step_cursor(Vector2(-1, 0))
	elif event.is_action_pressed("lf_right"):
		_step_cursor(Vector2(1, 0))
	elif event.is_action_pressed("lf_build"):
		_click(hovered_slot)
	elif event.is_action_pressed("lf_sell"):
		sell_at(selected_slot)
	elif event.is_action_pressed("lf_upgrade"):
		upgrade_at(selected_slot)
	elif event.is_action_pressed("lf_power"):
		toggle_at(selected_slot)
	elif event.is_action_pressed("lf_next"):
		_cycle_tower(1)
	elif event.is_action_pressed("lf_prev"):
		_cycle_tower(-1)
	elif event.is_action_pressed("lf_speed_cycle"):
		cycle_speed()
	elif event.is_action_pressed("lf_call_wave"):
		call_wave()
	elif event.is_action_pressed("lf_target"):
		cycle_targeting()
	elif event.is_action_pressed("lf_ability_1"):
		activate_ability("surge")
	elif event.is_action_pressed("lf_ability_2"):
		activate_ability("overcharge")
	elif event.is_action_pressed("lf_ability_3"):
		activate_ability("shutter")
	elif event.is_action_pressed("lf_camera_reset"):
		_cam_reset()


func _slot_screen(slot: Vector2i) -> Vector2:
	return IsoScript.tile_to_screen(float(slot.x), float(slot.y))


func _step_cursor(dir: Vector2) -> void:
	## Nearest slot in `dir`, preferring straight ahead over far to the side. Weighting the
	## across-axis distance is what stops a press of "right" jumping to something almost
	## directly below simply because it happens to be closer.
	var slots: Array = sim.anchor["slots"]
	if slots.is_empty():
		return
	var all: Array[Vector2i] = []
	for s in slots:
		all.append(Vector2i(int(s[0]), int(s[1])))
	if not all.has(hovered_slot):
		hovered_slot = all[0]              # first press with no cursor lands somewhere real
		queue_redraw()
		_follow_cursor()
		return
	var from := _slot_screen(hovered_slot)
	var perp := Vector2(-dir.y, dir.x)
	var best := hovered_slot
	var best_score := INF
	for cand in all:
		if cand == hovered_slot:
			continue
		var d := _slot_screen(cand) - from
		var along := d.dot(dir)
		if along <= 0.0:
			continue                       # behind the cursor
		var score := along + absf(d.dot(perp)) * 2.0
		if score < best_score:
			best_score = score
			best = cand
	if best != hovered_slot:
		hovered_slot = best
		Audio.sfx("ui_hover", -12.0)
		queue_redraw()
		_follow_cursor()


func _cycle_tower(step: int) -> void:
	var unlocked: Array = Content.unlocked_at(anchor_id)
	if unlocked.is_empty():
		return
	var i := unlocked.find(selected_tower)
	select(String(unlocked[wrapi(i + step, 0, unlocked.size())]))
	state_changed.emit()


func placed_index_at(slot: Vector2i) -> int:
	for i in range(sim.placed.size()):
		if sim.placed[i]["slot"] == slot:
			return i
	return -1


func sell_at(slot: Vector2i) -> void:
	var i := placed_index_at(slot)
	if i < 0:
		Audio.sfx("ui_deny")
		return
	sim.sell(i)
	if selected_slot == slot:
		selected_slot = NO_SLOT      # the inspector was pointed at something that is gone
	Audio.sfx("ui_sell")
	state_changed.emit()
	queue_redraw()


func upgrade_at(slot: Vector2i) -> void:
	var i := placed_index_at(slot)
	if i < 0 or not sim.upgrade(i):
		Audio.sfx("ui_deny")
		return
	Audio.sfx("ui_upgrade")
	Audio.sfx("power_online")
	state_changed.emit()
	queue_redraw()


func _click(slot: Vector2i) -> void:
	## One click, three outcomes, in this order: point the inspector at an emplacement that
	## is already there, build on a free slot, or put the inspector down. Selecting is
	## checked first because a built slot is never a free slot, so the two can never race.
	if placed_index_at(slot) >= 0:
		selected_slot = slot
		Audio.sfx("ui_click")
		state_changed.emit()
		queue_redraw()
		return
	if not sim.free_slots.has(slot):
		# Bare ground. Deselecting is a deliberate act, not a failed one — no deny cue.
		selected_slot = NO_SLOT
		state_changed.emit()
		queue_redraw()
		return
	if selected_tower == "" or not sim.can_afford(selected_tower):
		Audio.sfx("ui_deny")
		return
	if sim.build_at(selected_tower, slot):
		selected_slot = slot         # inspect and upgrade what was just built, without a hunt
		Audio.sfx("place_emplacement")
		Audio.sfx("power_online")
		state_changed.emit()
		queue_redraw()


func toggle_at(slot: Vector2i) -> void:
	## Shed an emplacement's load without losing it. Right-click does this where the cursor
	## is; the inspector does it to the selection.
	for i in range(sim.placed.size()):
		if sim.placed[i]["slot"] == slot:
			var now: bool = not sim.placed[i]["online"]
			sim.set_online(i, now)
			Audio.sfx("power_online" if now else "power_offline")
			state_changed.emit()
			return
	Audio.sfx("ui_deny")


func select(tower_id: String) -> void:
	## Arming something to build puts the board selection down. The inspector can only
	## describe one emplacement, and picking from the build bar is the player asking about
	## the one they just picked — leaving the board selection up left the panel describing a
	## turret on the board while the bar highlighted a different one the player was reading
	## about, which is the panel answering a question nobody asked.
	selected_tower = tower_id
	selected_slot = NO_SLOT
	Audio.sfx("ui_click")
	queue_redraw()


# ──────────────────────────────────────────────────────────────── draw ──

const C_TILE := Color(0.09, 0.13, 0.15)
const C_TILE_ALT := Color(0.10, 0.15, 0.17)

# ─────────────────────────────────────────────────────────── tile cache ──
#
# CAM-06: ground/path/slot tiles never change during a level — `_draw_board()` used to
# recompute `_path_tiles()`, rebuild `slot_set` and walk the whole grid every single frame,
# which is 4,096 tile draws before a unit moves at a synthetic 64x64 board. The cache below
# is built once, lazily, the first `_draw_board()` call for a given `anchor_id`; every later
# call culls the cached list against the current camera rect instead of rebuilding it.

## One entry per board tile, in painter's order: `{"pos": Vector2, "kind": String,
## "alt": bool}`. `pos` is `IsoScript.tile_to_screen(x, y)` **without** `_origin` added —
## `_origin` moves with the camera every frame, so it is added back at draw time, not baked
## into the cache. The `s_`/`x` build loop in `_rebuild_tile_cache()` *is* the depth sort
## (increasing tx+ty, ties broken by x), so this array needs no `sort_custom` — the obvious
## mistake here is to add one.
var _tile_cache: Array[Dictionary] = []
## The anchor_id `_tile_cache` was built for. "" means unbuilt. `_draw_board()` compares
## this against the live `anchor_id` every call and rebuilds on a mismatch — lazy rather
## than an explicit call from `boot()`/`_editor_refresh()`, so a cache is correct under any
## path that ends up drawing a different anchor, not just the ones this file remembers to
## invalidate from by hand (the same reasoning `drawables()`'s frame-keyed cache below uses).
var _tile_cache_anchor: String = ""

## LF-046: cached once, the highest `hp` among every enemy kind `Content` knows — -1.0 means
## unbuilt. `_draw_unit()`'s placeholder radius used to divide by 220 (Warden Heavy's own hp,
## hardcoded), so anything heavier saturated at the same radius as Warden Heavy and any
## future roster change silently stopped meaning what the comment beside it claimed.
var _max_enemy_hp: float = -1.0

## Slack, in screen px at zoom 1.0, added around the camera-derived visible rect before a
## cached tile is culled. 256, not 128 (one tile's own diamond footprint): the atlas packs
## every sprite into a fixed 256px cell (CLAUDE.md — "never trims"), and a tall or wide tile
## texture can extend past its diamond before the alpha silhouette starts. This margin also
## absorbs screen shake's up-to-16px offset (TRAUMA_MAX_OFFSET), which culling deliberately
## ignores rather than tracking exactly — see `_draw_board()`'s own note.
const TILE_CULL_MARGIN_PX := 256.0


func _rebuild_tile_cache(anchor: Dictionary) -> void:
	## Folds in `_path_tiles()` and the slot-tile set, which used to be rebuilt inline in
	## `_draw_board()` every frame too — same waste, same fix.
	var grid: Dictionary = anchor.get("grid", {"w": 12, "h": 10})
	var gw := int(grid["w"])
	var gh := int(grid["h"])
	var path_tiles := _path_tiles(anchor)
	var slot_set := {}
	for slot in anchor.get("slots", []):
		slot_set[Vector2i(int(slot[0]), int(slot[1]))] = true
	_tile_cache.clear()
	# painter's order: increasing tile depth, so a nearer tile overdraws a farther one —
	# this loop *is* the sort (see `_tile_cache`'s own doc).
	for s_ in range(gw + gh - 1):
		for x in range(gw):
			var y := s_ - x
			if y < 0 or y >= gh:
				continue
			var cell := Vector2i(x, y)
			var kind := "tile_ground"
			if path_tiles.has(cell):
				kind = "tile_path"
			elif slot_set.has(cell):
				kind = "tile_slot"
			_tile_cache.append({
				"pos": IsoScript.tile_to_screen(float(x), float(y)),
				"kind": kind,
				"alt": (x + y) % 2 == 0,
			})
	_tile_cache_anchor = anchor_id
const C_PATH := Color(0.30, 0.22, 0.10)
const C_SLOT := Color(0.20, 0.34, 0.31)
const C_VERD := Color(0.37, 0.66, 0.58)
const C_AMBER := Color(0.91, 0.64, 0.24)
const C_ALERT := Color(0.82, 0.33, 0.25)
const C_SHADOW := Color(0.0, 0.0, 0.0, 0.34)
const C_BONE := Color(0.86, 0.89, 0.88)

## LF-046: faction id -> the sprite-missing placeholder's colour, mirroring combat_fx.gd's
## FACTION_SHARD identity (amber/bronze, pale steel, desaturated violet) so a unit with no
## rendered sprite yet still reads as the right side. This used to be a two-way branch —
## "ordinal" got C_ALERT, everything else got C_AMBER — which silently drew Sable Reach and
## Hollow in the same colour; a fourth faction would have joined them with no error at all,
## since `sprite coverage` only guards the real render path, never this fallback one.
const FACTION_PLACEHOLDER_COLOR := {
	"ordinal": Color(0.80, 0.58, 0.22),
	"sable-reach": Color(0.80, 0.82, 0.86),
	"hollow": Color(0.62, 0.55, 0.72),
}
## An unlisted faction gets this rather than quietly matching one of the above — loud and
## unmistakably not a real faction's colour, so a fifth faction with no palette entry yet is
## visibly wrong instead of invisibly wrong.
const C_FACTION_UNKNOWN := Color(0.85, 0.20, 0.85)


# ────────────────────────────────────────────────────────────── facing ──
#
# Which of the four rendered yaws a drawable is shown at. The mapping from a tile-space
# heading to a yaw is measured, and lives in iso.gd next to the projection it comes out
# of; what lives here is only where each kind of entity's heading comes from.

## Tiles either side of the unit used for the path tangent. Long enough that the facing
## swings through a corner over about half a second rather than snapping, short enough
## that it is exactly the leg direction everywhere else.
const TANGENT_EPS := 0.35

var _idle_facing: Dictionary = {}      # slot -> heading toward the nearest path point


func _unit_heading(u: Dictionary) -> Vector2:
	## Direction of travel, as a centred difference along the path. Each leg is straight, so
	## this is exactly the leg direction along it and rotates monotonically from one leg to
	## the next through a corner — which is why units need no hysteresis: the heading never
	## re-crosses a bucket boundary it has just crossed.
	##
	## WAR-01: `path_length` is now one entry per lane and `point_at()` takes the lane as its
	## first argument (no defaulted overload — a missed call site is meant to be loud); the
	## unit's own `lane` key, written once at `spawn()` and never after, says which.
	var lane: int = int(u["lane"])
	var d: float = float(u["dist"])
	var back: Vector2 = sim.point_at(lane, maxf(0.0, d - TANGENT_EPS))
	var fwd: Vector2 = sim.point_at(lane, minf(float(sim.path_length[lane]), d + TANGENT_EPS))
	return fwd - back


func _tower_heading(p: Dictionary) -> Vector2:
	## What it last fired at, or the lane if it has not fired. `aim` is written by the sim
	## at the moment of the shot and erased when it has a shot ready and nothing to take it
	## on, so an emplacement tracks its target and returns to watching the path between waves.
	var slot := Vector2(float(p["slot"].x), float(p["slot"].y))
	var aim: Variant = p.get("aim", null)
	if aim != null:
		var h: Vector2 = (aim as Vector2) - slot
		if h.length_squared() > 1e-6:
			return h
	return _idle_heading(slot)


func _idle_heading(slot: Vector2) -> Vector2:
	## Toward the nearest point on the path. An emplacement pointing at open ground reads as
	## broken, and the lane is the only thing on the board worth watching. Constant per slot,
	## so it is computed once and cached — it is geometry, not a rule.
	if _idle_facing.has(slot):
		return _idle_facing[slot]
	# WAR-01: nearest point across every lane, not one path — an emplacement watches
	# whichever lane it is actually closest to.
	var lanes: Array = _anchor_data().get("paths", [])
	var best := Vector2.ZERO
	var best_d := INF
	for lane_doc in lanes:
		var pts: Array = lane_doc.get("waypoints", [])
		for i in range(pts.size() - 1):
			var a := Vector2(float(pts[i][0]), float(pts[i][1]))
			var b := Vector2(float(pts[i + 1][0]), float(pts[i + 1][1]))
			var ab := b - a
			var t := 0.0
			if ab.length_squared() > 0.0:
				t = clampf((slot - a).dot(ab) / ab.length_squared(), 0.0, 1.0)
			var q := a + ab * t
			var d := slot.distance_squared_to(q)
			if d < best_d:
				best_d = d
				best = q - slot
	_idle_facing[slot] = best
	return best


## ART-01/LF-157: the head/base split needs two independent bucket resolutions per
## emplacement — 4 for the base (matches the old combined sprite), 16 for the head, so a
## turret's gun tracks sixteen ways while its housing still snaps through the same four
## quarter-turns it always did. Each resolution is its own hysteresis state and needs its
## own persisted previous bucket: collapsing them onto one field would make the base's
## wide (90°) dead zone gate the head's fine (22.5°) one, or vice versa. Sprite names for
## the split parts (`<id>_base` / `<id>_head`) are fixed by `render_split_asset()` in
## `tools/blender/render.py`; only the bucket count differs per part.
const BASE_YAW_COUNT := 4
const HEAD_YAW_COUNT := 16

# ART-06: traverse/recoil/reload. All three are pure presentation, read off the placed
# record's existing rules-owned fields (`aim`, `cooldown`, `tower.fire_interval`) and the
# `shot_fired` signal already emitted for combat_fx.gd's benefit — nothing here is a new
# rule and nothing here is written back to anything `sim/engine.py` or `anchor_sim.gd`
# reads. Costed against zero new rendered cells: traverse is a cross-fade between two
# buckets ART-01 already rendered, recoil is a screen-space translation, and the reload
# readout is drawn with `draw_arc()`, not a sprite — see this issue's own report for the
# measured page/VRAM/render-time deltas (all zero).
##
## Timed off `sim.t` (simulated seconds, DT=1/30 — anchor_sim.gd), not `Engine`/`Time` wall
## clock or `_process(delta)`: `cooldown`/`fire_interval` are already in that unit, and
## `sim.t` keeps advancing at exactly the rate the player's chosen game speed multiplies
## it by (`_process()`'s `_accum += delta * speed` above), so a recoil or a traverse at 4x
## speed compresses the same way the rest of the board already does, rather than one
## clock racing the other.
const TRAVERSE_TRANS_S := 0.15   ## cross-fade duration for a head bucket change
const RECOIL_DURATION_S := 0.12  ## decay window, per this issue's own task list
const RECOIL_MAX_PX := 6.0       ## capped screen-space kick, head layer only


func _on_shot_fired(placed: Dictionary, from_tile: Vector2, to_tile: Vector2,
		_target_kind: Dictionary) -> void:
	## Presentation-only recoil bookkeeping. `placed` is the exact Dictionary reference
	## `anchor_sim.gd`'s `_step()` holds in its own `placed` array (it emits `p` directly,
	## same object `_face_parts()` below already annotates), so writing `view_recoil_*`
	## keys onto it is exactly as safe as `aim`/`view_bucket_head` already are — placed
	## records are only ever compared by `slot`, never by value. Direction is stored as
	## the raw tile-space delta (unnormalised — a zero-length one, an emplacement firing
	## on its own tile, is possible and is guarded at the read site instead) rather than
	## a screen-space vector, so it survives a camera rotation this project does not have
	## but a Vector2 delta costs nothing extra to keep general.
	placed["view_recoil_t0"] = sim.t
	placed["view_recoil_dir"] = to_tile - from_tile


func _face_parts(p: Dictionary, heading: Vector2) -> Dictionary:
	## Bucket an emplacement's heading at both resolutions, remembering each answer
	## separately on the placed record.
	##
	## The state lives on the emplacement and deliberately **not** on a unit: Godot 4.7
	## compares Dictionaries by value, and anchor_sim's splash loop tests `u == target`, so
	## one extra key on a unit dictionary would change which units count as the target and
	## put the two rule implementations out of parity. `placed` records are only ever
	## compared on their slot, so they are safe to annotate.
	var prev_base: int = int(p.get("view_bucket_base", -1))
	var base := IsoScript.bucket_index_for_heading(
			heading, BASE_YAW_COUNT, prev_base, IsoScript.YAW_HYSTERESIS_FRAC)
	p["view_bucket_base"] = base
	var prev_head: int = int(p.get("view_bucket_head", -1))
	var head := IsoScript.bucket_index_for_heading(
			heading, HEAD_YAW_COUNT, prev_head, IsoScript.YAW_HYSTERESIS_FRAC)
	# ART-06 traverse: remember the bucket just left, and when, so _build_drawables() can
	# cross-fade from it for TRAVERSE_TRANS_S instead of snapping. Only when the head
	# actually moved and there was a real previous bucket to fade from — a brand-new
	# emplacement's first frame (prev_head == -1) has nothing to fade from and must not
	# manufacture one.
	if head != prev_head and prev_head >= 0:
		p["view_head_trans_from"] = prev_head
		p["view_head_trans_t0"] = sim.t
	p["view_bucket_head"] = head
	return {"base": base, "head": head}


func _reload_frac(p: Dictionary, online: bool) -> float:
	## ART-06 reload readout. `cooldown` counts down from `fire_interval` to 0 and is reset
	## to `fire_interval` the instant a shot lands (anchor_sim.gd's `_step()`, mirrored in
	## sim/engine.py) — so this fraction is 1.0 the frame a shot fires and reaches exactly
	## 0.0 the frame the gun is ready to fire again, which is the acceptance criterion
	## verbatim. A support tower (`damage <= 0.0`) never has its cooldown ticked by the fire
	## loop at all — `cooldown` simply stays whatever `build_at()` initialised it to (0.0)
	## forever — so this naturally returns 0.0 for one without a separate check; the
	## `fire_interval > 0.0` guard below is only to keep a division safe, not to distinguish
	## a weapon from a support tower.
	if not online:
		return 0.0
	var fi := float(p["tower"].get("fire_interval", 0.0))
	if fi <= 0.0:
		return 0.0
	return clampf(float(p.get("cooldown", 0.0)) / fi, 0.0, 1.0)


func _recoil_offset(p: Dictionary) -> Vector2:
	## ART-06 recoil. Screen-space translation only (never a rotation — see this issue's
	## own risk note and ART-01's measured 36.7px swing on a rotated 96px barrel), applied
	## to the head layer's draw position and nowhere else — the base's own `at` in
	## `_build_drawables()` above is untouched. Decays from `RECOIL_MAX_PX` to 0 over
	## `RECOIL_DURATION_S` of `sim.t`, eased (squared) so the kick reads as an impulse
	## rather than a linear slide back.
	if not p.has("view_recoil_t0"):
		return Vector2.ZERO
	var rt: float = sim.t - float(p["view_recoil_t0"])
	if rt >= RECOIL_DURATION_S or rt < 0.0:
		return Vector2.ZERO
	var dir: Vector2 = p.get("view_recoil_dir", Vector2.ZERO)
	if dir.length_squared() < 1e-9:
		return Vector2.ZERO
	# tile_to_screen() is linear in (tx, ty), so the screen-space direction of a tile-space
	# delta is just that same projection of the delta itself, normalised. A zero-length
	# result here (the projection of a nonzero tile vector cannot be zero, since the
	# transform is invertible) never reaches — guarded above on `dir` instead.
	var screen_dir := IsoScript.tile_to_screen(dir.x, dir.y).normalized()
	var k: float = 1.0 - rt / RECOIL_DURATION_S
	return -screen_dir * RECOIL_MAX_PX * k * k


## CAM-07: last frame `drawables()` built its list for, and the cached result — see
## `drawables()`'s own doc for why the key is `Engine.get_frames_drawn()` rather than
## something built once in `_process()`. -1 never matches a real frame number, so the very
## first call always builds.
var _drawables_cache: Array = []
var _drawables_cache_frame: int = -1
## Verification-only counter (main.gd's `-- --profile`): how many times the build body
## below has actually run, so "once per frame" is something a 600-frame run can assert on
## rather than trust. Reset in `boot()`.
var _drawables_rebuild_count: int = 0


func drawables() -> Array:
	## One ordered list, shared by the contact-shadow pass, the sprite pass, the additive
	## glow layer and the hit-flash pass, so none of them can disagree about contents,
	## facing or depth order.
	##
	## CAM-07: cached per drawn frame. `Engine.get_frames_drawn()`, not a flag set in
	## `_process()` — `AnchorView` (z 0), `GlowLayer` (z 10) and `FxAdditive` (z 14) all
	## draw after `_process()` in the ordinary case, but a `queue_redraw()` reached from a
	## signal handler could still land between two of those draws within the same rendered
	## frame; a cache keyed on the frame the engine is *actually drawing* is correct under
	## every ordering, one built eagerly in `_process()` is only correct under the ordering
	## that happens to hold today. Lazy: whichever layer draws first this frame pays the
	## build cost (`_build_drawables()`) and the rest read the cached `Array`.
	##
	## Returns the cached `Array` itself, not a copy — callers (`glow_layer.gd`,
	## `fx_additive.gd`) must treat it as read-only. Both currently only read it.
	var frame := Engine.get_frames_drawn()
	if frame != _drawables_cache_frame:
		_drawables_cache = _build_drawables()
		_drawables_cache_frame = frame
		_drawables_rebuild_count += 1
	return _drawables_cache


func drawables_rebuild_count() -> int:
	## Verification-only accessor for main.gd's `-- --profile`.
	return _drawables_rebuild_count


func _build_drawables() -> Array:
	## `_face_parts()` (bucket + hysteresis, now for both base and head) runs once per frame
	## per emplacement instead of
	## up to four times — see this file's own risk note in docs/issues/CAM-07 on why that
	## is safe: sim state is frozen for the whole render pass (it only advances in
	## `_process()`, which always completes before any `_draw()` this frame), so every one
	## of the old four calls was already computing from the same heading and converged to
	## the same bucketed yaw. Verified empirically with `--facings`, not just argued — see
	## this issue's PR notes.
	var out: Array = []
	for p in sim.placed:
		# ART-01/LF-157: two drawables per placed tower, not one — the base (4 buckets) and
		# the head (16) are separate sprites (`<id>_base` / `<id>_head`) drawn at the same
		# screen point. They share "depth" by construction (same slot), so `out.sort_custom`
		# below needs an explicit tiebreak ("layer") to guarantee the head never sorts under
		# the base — `sort_custom` is not stable, and an equal-depth pair would otherwise
		# flip order intermittently.
		var buckets := _face_parts(p, _tower_heading(p))
		var base_id := String(p["tower"]["id"]).replace("-", "_")
		var depth := IsoScript.depth(p["slot"].x, p["slot"].y)
		var at := IsoScript.tile_to_screen(float(p["slot"].x), float(p["slot"].y)) + _origin
		var online := bool(p["online"])
		out.append({
			"depth": depth,
			"layer": 0,
			"kind": "tower",
			"part": "base",
			"sprite": base_id + "_base",
			"bucket": buckets["base"],
			"yaw_count": BASE_YAW_COUNT,
			"online": online,
			"at": at,
			"ref": p,
			# ART-06 reload readout: fraction of `fire_interval` remaining, 0 when ready
			# to fire (also 0, harmlessly, for a support tower -- see `_reload_frac()`'s
			# own doc). Read off the placed record's own rules-owned fields, never
			# written back to them.
			"reload_frac": _reload_frac(p, online),
		})
		# ART-06 recoil/traverse, computed once here rather than inside
		# `_draw_entities()`/`glow_layer.gd` -- one source for every layer that reads
		# this list, same reasoning `_face_parts()` above already documents for the
		# bucket itself.
		var head_at := at + _recoil_offset(p)
		var head_entry := {
			"depth": depth,
			"layer": 1,
			"kind": "tower",
			"part": "head",
			"sprite": base_id + "_head",
			"bucket": buckets["head"],
			"yaw_count": HEAD_YAW_COUNT,
			"online": online,
			"at": head_at,
			"ref": p,
		}
		var trans_from := int(p.get("view_head_trans_from", -1))
		if trans_from >= 0:
			var trans_t0: float = float(p.get("view_head_trans_t0", -INF))
			var tt: float = sim.t - trans_t0
			if tt < TRAVERSE_TRANS_S:
				# Fading OUT: 1.0 at the instant the bucket changed (so the first frame of
				# a transition looks identical to the frame before it) down to 0.0 at
				# TRAVERSE_TRANS_S (so the last frame is indistinguishable from no
				# transition at all -- no snap-then-fade double motion).
				head_entry["trans_from_bucket"] = trans_from
				head_entry["trans_from_alpha"] = clampf(1.0 - tt / TRAVERSE_TRANS_S, 0.0, 1.0)
		out.append(head_entry)
	for u in sim.units:
		if not u["alive"]:
			continue
		var at: Vector2 = sim.point_at(int(u["lane"]), u["dist"])
		out.append({
			"depth": IsoScript.depth(at.x, at.y),
			"layer": 0,
			"kind": "unit",
			"sprite": String(u["kind"]["id"]).replace("-", "_"),
			"yaw": IsoScript.yaw_for_heading(_unit_heading(u)),
			"online": true,
			"at": IsoScript.tile_to_screen(at.x, at.y) + _origin,
			# Raw tile-space point, before projection — fx_additive.gd's hit-flash pass used
			# to recompute this itself via a fresh `sim.point_at(d["ref"]["dist"])` call.
			# Harmless (sim state cannot change mid-render-pass, so it always recomputed the
			# same point `at` already is), but two reads of the same fact is exactly the
			# thing that could silently drift if either side ever changed independently —
			# CAM-07's own review note asks this to be unified. One source now.
			"tile": at,
			"ref": u,
		})
	out.sort_custom(func(a, b):
		if a["depth"] == b["depth"]:
			return int(a.get("layer", 0)) < int(b.get("layer", 0))
		return a["depth"] < b["depth"])
	return out


## CAM-06/CAM-07 verification: `-- --profile <frames>` (main.gd) times this layer's own
## `_draw()` calls in milliseconds. See the shared doc atop `start_profiling()` below.
var _profile_ticks: PackedFloat64Array = PackedFloat64Array()
var _profiling: bool = false


func start_profiling() -> void:
	## Verification-only. Off by default: an ordinary run pays nothing for this beyond the
	## one extra branch in `_draw()` below. `main.gd`'s `-- --profile <frames>` is the only
	## caller — see docs/issues/CAM-06's own task asking for a falsifiable draw-cost number
	## rather than a claim with no hook.
	_profiling = true
	_profile_ticks.clear()


func profile_stats() -> Dictionary:
	## `{"mean": ms, "p95": ms, "n": sample count}` across every `_draw()` call since
	## `start_profiling()`. All zero/`n=0` if nothing has been sampled yet.
	if _profile_ticks.is_empty():
		return {"mean": 0.0, "p95": 0.0, "n": 0}
	var sorted := _profile_ticks.duplicate()
	sorted.sort()
	var n := sorted.size()
	var total := 0.0
	for v in sorted:
		total += v
	var idx := clampi(int(ceil(0.95 * float(n))) - 1, 0, n - 1)
	return {"mean": total / float(n), "p95": sorted[idx], "n": n}


func _draw() -> void:
	if not _profiling:
		_draw_impl()
		return
	var t0 := Time.get_ticks_usec()
	_draw_impl()
	_profile_ticks.append(float(Time.get_ticks_usec() - t0) / 1000.0)


func _draw_impl() -> void:
	var anchor: Dictionary = _anchor_data()
	if anchor.is_empty():
		return
	_draw_board(anchor)
	if sim == null:
		_draw_editor_overlay(anchor)     # no sim means we are previewing, not playing
		return
	_draw_reach()
	_draw_hover()
	_draw_entities()
	_draw_selection()


func _draw_board(anchor: Dictionary) -> void:
	## The static level: ground, path and slot tiles. Shared by the running game and
	## the editor preview so the two can never disagree about what a level looks like.
	##
	## CAM-06: the tile list is `_tile_cache`, built once per anchor (see its own doc).
	## What runs here every frame is only culling the cached list against the current
	## camera rect and drawing what survives.
	if _tile_cache_anchor != anchor_id:
		_rebuild_tile_cache(anchor)

	var lib := _sprite_lib()
	var pivot: Vector2 = lib.pivot if lib != null else Vector2.ZERO

	# No culling in the editor preview: get_viewport_rect() there is the editor's own
	# viewport, not this board's, and every shipped anchor is small enough (max 18x15) that
	# culling buys nothing an editor artist would notice. The synthetic 64x64 board this
	# issue exists for is a play-mode verification fixture, not something previewed.
	var cull: bool = not Engine.is_editor_hint()
	var vmin := Vector2.ZERO
	var vmax := Vector2.ZERO
	if cull:
		var vp := get_viewport_rect().size
		# `pos` in the cache is board-projected, pre-`_origin` (see its own doc); screen =
		# (pos + origin) * zoom, ignoring shake's `position` (absorbed into the margin — see
		# TILE_CULL_MARGIN_PX). Solving that for `pos` against the viewport rect, expanded by
		# the margin on the screen-space side (hence dividing the margin by zoom too), gives
		# the visible band in cache space.
		var margin: float = TILE_CULL_MARGIN_PX / _cam_zoom
		vmin = -_origin - Vector2(margin, margin)
		vmax = vp / _cam_zoom - _origin + Vector2(margin, margin)

	for t in _tile_cache:
		var p: Vector2 = t["pos"]
		if cull and (p.x < vmin.x or p.x > vmax.x or p.y < vmin.y or p.y > vmax.y):
			continue
		var c := p + _origin
		var kind: String = t["kind"]
		var tex: Texture2D = lib.get_tex(kind, 45, "albedo") if (lib != null and lib.ok) else null
		if tex != null:
			draw_texture(tex, c - pivot)
		else:
			var col: Color = C_TILE if bool(t["alt"]) else C_TILE_ALT
			if kind == "tile_path":
				col = C_PATH
			elif kind == "tile_slot":
				col = C_SLOT
			draw_colored_polygon(IsoScript.diamond(c, 0.98), col)


func _draw_hover() -> void:
	var anchor: Dictionary = sim.anchor
	var is_slot := false
	for slot in anchor["slots"]:
		if Vector2i(int(slot[0]), int(slot[1])) == hovered_slot:
			is_slot = true
			break
	if not is_slot or not sim.free_slots.has(hovered_slot):
		return
	var hc := IsoScript.tile_to_screen(float(hovered_slot.x), float(hovered_slot.y)) + _origin
	var ring := IsoScript.diamond(hc, 0.92)
	draw_polyline(ring + PackedVector2Array([ring[0]]), C_AMBER, 2.0)


func _draw_reach() -> void:
	## Range, drawn on the ground, because "3.2 tiles" in the inspector does not answer the
	## only question that matters: does this gun cover that corner. Bone is what the selected
	## emplacement covers now — red if it is offline and covering nothing. Amber is what the
	## armed emplacement in the build bar *would* cover if it were built on the hovered slot.
	var i := placed_index_at(selected_slot)
	if i >= 0:
		var p: Dictionary = sim.placed[i]
		_draw_range(Vector2(selected_slot), float(p["tower"]["range"]),
				Color(C_BONE if p["online"] else C_ALERT, 0.5))
	if selected_tower != "" and sim.free_slots.has(hovered_slot) and hovered_slot != selected_slot:
		var tw: Dictionary = Content.tower(selected_tower)
		if not tw.is_empty():
			_draw_range(Vector2(hovered_slot), float(tw["range"]), Color(C_AMBER, 0.4))


func _draw_selection() -> void:
	## Drawn after the sprites, unlike the hover ring: the emplacement stands on its own
	## tile and covers most of it, so a ring drawn on the ground under a 256px sprite is
	## four white specks around its base and reads as nothing at all.
	if placed_index_at(selected_slot) < 0:
		return
	var c := IsoScript.tile_to_screen(float(selected_slot.x), float(selected_slot.y)) + _origin
	var ring := IsoScript.diamond(c, 1.0)
	draw_polyline(ring + PackedVector2Array([ring[0]]), Color(C_BONE, 0.85), 2.0)
	# Corner ticks, so the selection is legible against a bright tile as well as a dark one.
	for corner in ring:
		draw_circle(corner, 3.0, C_BONE)


func _draw_range(centre: Vector2, r: float, col: Color) -> void:
	## The rules compare distance in *tile* space (decision 030), so reach is a circle there
	## and a 2:1 ellipse once projected — the same ratio as the tile, for the same reason.
	## Sampling the tile-space circle and projecting each point draws exactly the set the
	## weapon can reach, and stays correct if the projection ever changes.
	const SEGMENTS := 48
	var pts := PackedVector2Array()
	for i in range(SEGMENTS):
		var a := TAU * float(i) / float(SEGMENTS)
		pts.append(IsoScript.tile_to_screen(centre.x + cos(a) * r, centre.y + sin(a) * r) + _origin)
	pts.append(pts[0])
	draw_polyline(pts, col, 2.0)


func _draw_contact_shadow(at: Vector2, radius: float) -> void:
	## Without this a sprite reads as floating over the board rather than standing on
	## it (LF-024). Drawn in engine rather than baked into the sprite: a baked shadow
	## would be part of the albedo silhouette and could not sit under the *neighbouring*
	## tile, which is exactly where a contact shadow has to fall. The ellipse is 2:1
	## because the tile is (decision 017).
	var pts := PackedVector2Array()
	for i in range(16):
		var a := TAU * float(i) / 16.0
		pts.append(at + Vector2(cos(a) * radius, sin(a) * radius * 0.5))
	draw_colored_polygon(pts, C_SHADOW)


func _draw_entities() -> void:
	var dim: float = 0.6 if sim.brownout else 1.0
	# Every shadow first, so a nearer sprite's shadow cannot land on top of a farther
	# sprite that has already been drawn. ART-01: base and head share one screen point at
	# rest, so the head part is skipped here — a second identical shadow drawn on top of
	# the first would only double its opacity, not add anything a viewer could see. ART-06:
	# recoil moves the head's own "at" by a few px, not the base's — the shadow is a
	# ground-plane thing and stays with the base regardless, which this skip already gives
	# for free.
	for d in drawables():
		if d.get("part", "") == "head":
			continue
		_draw_contact_shadow(d["at"], 27.0 if d["kind"] == "tower" else 15.0)
	for d in drawables():
		var tex: Texture2D = _tex_for(d, "albedo")
		if tex != null:
			var tint := Color(1, 1, 1)
			if d["kind"] == "tower" and not d["online"]:
				tint = Color(0.45, 0.48, 0.5)      # offline reads as cold, not just unlit
			draw_texture(tex, d["at"] - _sprite_lib().pivot, tint)
			# ART-06 traverse: the bucket just left, fading out on top of the current one —
			# see `_face_parts()`/`_build_drawables()` for how these two keys are populated.
			# Only a head drawable ever carries them.
			if d.has("trans_from_bucket"):
				var from_tex: Texture2D = _sprite_lib().get_bucket_tex(
						d["sprite"], int(d["trans_from_bucket"]), "albedo")
				if from_tex != null:
					var fade := Color(tint.r, tint.g, tint.b, tint.a * float(d["trans_from_alpha"]))
					draw_texture(from_tex, d["at"] - _sprite_lib().pivot, fade)
			if d["kind"] == "unit":
				_draw_health(d["ref"], d["at"])
		elif d["kind"] == "tower":
			# ART-01: only the base falls back to the placeholder shape — the head part
			# doing the same would draw a second, identical placeholder on top of it.
			if d.get("part", "") != "head":
				_draw_tower(d["ref"], dim)
		else:
			_draw_unit(d["ref"])
		if d["kind"] == "tower" and d.get("part", "") != "head":
			_draw_rank_pip(d["ref"], d["at"])
			# ART-06 reload readout, on the base per this issue's own task list. Nothing
			# drawn at 0.0 — ready-and-idle and offline both read as "nothing here", which
			# is deliberate: see this issue's report for why a third, suppressed, reading
			# is not yet possible.
			_draw_reload_arc(d["at"], float(d.get("reload_frac", 0.0)))


func _draw_rank_pip(p: Dictionary, at: Vector2) -> void:
	## Veterancy (data/tuning.json `veterancy`): "shows a rank pip on its base" per the note.
	## Empty rank (no ranks set, or no kills yet) draws nothing — an untouched board looks
	## exactly as it always has.
	var ranks: Array = sim.veterancy_ranks()
	if ranks.is_empty():
		return
	var kills := int(p.get("kills", 0))
	var name := ""
	for r in ranks:
		if kills >= int(r.get("kills", 0)):
			name = String(r.get("name", ""))
	if name == "":
		return
	var c := at + Vector2(-26, -48)
	draw_circle(c, 9.0, Color(C_AMBER, 0.92))
	_label(c + Vector2(0, 4), name, Color(0.09, 0.07, 0.03))


func _draw_reload_arc(at: Vector2, frac: float) -> void:
	## ART-06 reload readout, on the base's own foot point rather than the rank pip's
	## upper-left offset (`Vector2(-26, -48)` above) or the selection ring's tile-diamond
	## radius (`IsoScript.diamond` at scale 1.0, roughly 64x32 half-extents) — Ui.BOARD_ARC_R
	## sits well inside both, per this issue's own risk note about competing with existing
	## board furniture. Colour and geometry come from `Ui` (decisions 045/046), not a
	## literal: `Ui.C_AMBER` already reads "armed, attention, cost" elsewhere in the HUD,
	## which is exactly what a gun mid-reload is.
	if frac <= 0.0:
		return
	const START := -PI * 0.5   # 12 o'clock
	draw_arc(at + Vector2(0, 8), Ui.BOARD_ARC_R, START, START + TAU * frac, 20,
			Ui.C_AMBER, Ui.BOARD_ARC_W, true)


func _draw_editor_overlay(anchor: Dictionary) -> void:
	## Authoring aid, editor only. Shows the things a level is actually made of —
	## where units enter and leave, which way the path runs, and which tiles are
	## buildable — so an anchor can be judged without running it.
	# WAR-01: one line/IN/OUT pair per lane, not one path.
	for lane_doc in anchor.get("paths", []):
		var pts: Array = lane_doc.get("waypoints", [])
		if pts.size() < 2:
			continue
		var line := PackedVector2Array()
		for p in pts:
			line.append(IsoScript.tile_to_screen(float(p[0]), float(p[1])) + _origin)
		draw_polyline(line, Color(C_AMBER, 0.55), 3.0)
		for i in range(pts.size() - 1):
			_draw_arrow(line[i], line[i + 1])
		_draw_marker(line[0], C_VERD, "IN")
		_draw_marker(line[line.size() - 1], C_ALERT, "OUT")

	for i in range(anchor.get("slots", []).size()):
		var slot: Array = anchor["slots"][i]
		var c := IsoScript.tile_to_screen(float(slot[0]), float(slot[1])) + _origin
		var ring := IsoScript.diamond(c, 0.88)
		draw_polyline(ring + PackedVector2Array([ring[0]]), Color(C_VERD, 0.9), 2.0)
		_label(c + Vector2(0, 4), str(i + 1), Color(C_VERD, 0.9))


func _draw_arrow(a: Vector2, b: Vector2) -> void:
	var mid := (a + b) * 0.5
	var dir := (b - a).normalized()
	var perp := Vector2(-dir.y, dir.x)
	draw_colored_polygon(PackedVector2Array([
		mid + dir * 9.0, mid - dir * 5.0 + perp * 5.0, mid - dir * 5.0 - perp * 5.0,
	]), Color(C_AMBER, 0.85))


func _draw_marker(c: Vector2, col: Color, text: String) -> void:
	draw_circle(c, 9.0, Color(col, 0.85))
	_label(c + Vector2(0, -16), text, col)


func _label(c: Vector2, text: String, col: Color) -> void:
	var font := ThemeDB.fallback_font
	var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, 12).x
	draw_string(font, c - Vector2(w * 0.5, 0), text, HORIZONTAL_ALIGNMENT_LEFT, -1, 12, col)


func _draw_tower(p: Dictionary, dim: float) -> void:
	var c := IsoScript.tile_to_screen(float(p["slot"].x), float(p["slot"].y)) + _origin
	var online: bool = p["online"]
	var body := Color(0.42, 0.46, 0.48) if online else Color(0.22, 0.24, 0.26)
	draw_colored_polygon(IsoScript.diamond(c + Vector2(0, -14), 0.55), body)
	draw_rect(Rect2(c + Vector2(-9, -34), Vector2(18, 22)), body)
	if online:
		# emissive stand-in. dims with bus load, which is the whole point of
		# keeping glow a separate layer rather than baking it (decision 007).
		var glow := (C_VERD if not sim.brownout else C_ALERT)
		draw_circle(c + Vector2(0, -34), 5.0, Color(glow.r, glow.g, glow.b, dim))


func _draw_health(u: Dictionary, c: Vector2) -> void:
	var kind: Dictionary = u["kind"]
	var frac: float = clampf(float(u["hp"]) / (float(kind["hp"]) * sim.hp_mult), 0.0, 1.0)
	if frac >= 0.999:
		return                                   # full health bars are visual noise
	draw_rect(Rect2(c + Vector2(-11, -30), Vector2(22, 3)), Color(0, 0, 0, 0.65))
	draw_rect(Rect2(c + Vector2(-11, -30), Vector2(22.0 * frac, 3)),
			C_VERD if frac > 0.35 else C_ALERT)


func _enemy_hp_ceiling() -> float:
	## The scale for `_draw_unit()`'s placeholder radius, computed from the real roster
	## instead of one hardcoded unit's hp. Cached after the first call — `Content.enemies` is
	## loaded once at boot and never changes underneath a running board.
	if _max_enemy_hp < 0.0:
		_max_enemy_hp = 1.0     # floor, so a division never sees 0 if enemies is somehow empty
		for e in Content.enemies.values():
			_max_enemy_hp = maxf(_max_enemy_hp, float(e.get("hp", 0.0)))
	return _max_enemy_hp


func placeholder_radius(hp: float) -> float:
	## Verification-only accessor for LF-046: `_draw_unit()`'s own radius formula, exposed so
	## `main.gd`'s `--dump-placeholder` can prove the scale is the real roster's max hp and
	## not one hardcoded unit's, without needing an actually-missing sprite to reach
	## `_draw_unit()` itself — the `sprite coverage` gate check means nothing in the tracked
	## data ever misses one, by design.
	return 7.0 + 5.0 * clampf(hp / _enemy_hp_ceiling(), 0.0, 1.0)


func placeholder_color(faction: String) -> Color:
	## Verification-only accessor for LF-046, mirroring `placeholder_radius()` above:
	## `_draw_unit()`'s own colour lookup, so `--dump-placeholder` can prove every faction in
	## `Content.enemies` resolves to a distinct colour (the bug was Sable Reach and Hollow
	## both falling through to the same amber) without needing a missing sprite to reach it.
	return FACTION_PLACEHOLDER_COLOR.get(faction, C_FACTION_UNKNOWN)


func _draw_unit(u: Dictionary) -> void:
	var at: Vector2 = sim.point_at(int(u["lane"]), u["dist"])
	var c := IsoScript.tile_to_screen(at.x, at.y) + _origin
	var kind: Dictionary = u["kind"]
	var col: Color = FACTION_PLACEHOLDER_COLOR.get(
		String(kind.get("faction", "")), C_FACTION_UNKNOWN)
	var r: float = 7.0 + 5.0 * clampf(float(kind["hp"]) / _enemy_hp_ceiling(), 0.0, 1.0)
	draw_circle(c + Vector2(0, -8), r, col)
	var frac: float = clampf(float(u["hp"]) / (float(kind["hp"]) * sim.hp_mult), 0.0, 1.0)
	draw_rect(Rect2(c + Vector2(-10, -24), Vector2(20, 3)), Color(0, 0, 0, 0.6))
	draw_rect(Rect2(c + Vector2(-10, -24), Vector2(20.0 * frac, 3)), C_VERD)


func _path_tiles(anchor: Dictionary) -> Dictionary:
	## WAR-01: the union of every lane's tiles — a shared cell where two lanes cross is one
	## entry either way, which is exactly what a Dictionary-as-set already gives for free.
	var out := {}
	for lane_doc in anchor.get("paths", []):
		var pts: Array = lane_doc.get("waypoints", [])
		for i in range(pts.size() - 1):
			var a := Vector2i(int(pts[i][0]), int(pts[i][1]))
			var b := Vector2i(int(pts[i + 1][0]), int(pts[i + 1][1]))
			var step := Vector2i(signi(b.x - a.x), signi(b.y - a.y))
			var cur := a
			out[cur] = true
			while cur != b:
				cur += step
				out[cur] = true
	return out


func phase() -> String:
	return _phase


func wave_number() -> int:
	return _wave_index + 1


func lead_left() -> float:
	## Seconds of prep remaining before the current wave spawns. The clock was already
	## running and only the sim could see it, so a player in prep had no idea whether they
	## had twenty seconds to spend a bounty or two.
	return maxf(_lead_left, 0.0)


func sim_time() -> float:
	return _sim_t


func export_state() -> Dictionary:
	## PRC-12: the small state dictionary `scripts/scenario.gd`'s assertions read via a
	## dotted path (`sim.lives`, `view.camera.zoom`, ...). Deliberately narrow — this is
	## what a scenario file is allowed to *see*, not a general debug dump — but wide enough
	## to express every legacy verification hook's own printed state (STATE/BUS/CAMERA in
	## main.gd's `_process()`) plus per-unit and per-emplacement lookups
	## (`sim.units.<i>.hp`, `sim.placed.<i>.kills`) for the surge/veterancy assertions
	## `data/scenarios/abilities.json` needs. `hud.gd`'s `export_state()` is merged in
	## under `"hud"` by `Scenario.snapshot()`, not here — this file has no reference to the
	## HUD node (see the class doc's note on no `get_node()` chains across scene boundaries).
	if sim == null:
		return {"sim": {}, "view": {}}
	var units: Array = []
	for u in sim.units:
		units.append({
			"hp": float(u["hp"]), "dist": float(u["dist"]), "alive": bool(u["alive"]),
			"lane": int(u["lane"]), "kind": String(u["kind"]["id"]),
		})
	var placed: Array = []
	for p in sim.placed:
		var s: Vector2i = p["slot"]
		placed.append({
			"tower": String(p["tower"]["id"]), "online": bool(p["online"]),
			"kills": int(p.get("kills", 0)),
			"target_mode": String(p.get("target_mode", Tuning.targeting_default())),
			"slot_x": s.x, "slot_y": s.y,
		})
	var cam := camera_state()
	return {
		"sim": {
			"lives": int(sim.lives), "leaks": int(sim.leaks), "funds": int(sim.funds),
			"bus_load": sim.bus_load(), "capacity": sim.capacity(),
			"penalty": sim.penalty_now(), "brownout": bool(sim.brownout),
			"wave": wave_number(), "phase": phase(), "units_alive": _units_alive_count(),
			"chain_count": chain_count, "chain_active": chain_active(),
			"units": units, "placed": placed,
		},
		"view": {
			"camera": {"x": cam["x"], "y": cam["y"], "zoom": cam["zoom"]},
			"selected_tower": selected_tower,
			"selected_slot": {"x": selected_slot.x, "y": selected_slot.y},
			"speed": speed, "phase": phase(), "wave": wave_number(),
		},
	}


func _units_alive_count() -> int:
	var n := 0
	for u in sim.units:
		if bool(u["alive"]):
			n += 1
	return n
