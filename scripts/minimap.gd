extends Control
## CAM-04: the wide read. `_default_target()`/`_min_zoom_for_board()` (decision 056) already
## accept detail loss at full zoom-out — a 256px sprite can render at 30-60px on a 64x64
## board — so once a board exceeds the screen this, not the board itself, is the only
## surface the whole level is legible on at once. See docs/issues/CAM-04-minimap.md.
##
## HUD content, not a board child: `hud.gd` owns placement and the panel chrome around this
## control (caption, background, legend); this file only ever draws inside its own rect and
## never touches the board's transform (decisions 045, 046, 050).
##
## Reads `view.sim.units`/`.placed`/`.anchor` directly rather than `view.drawables()` —
## `drawables()` is a per-frame *rendering* cache keyed to the yaw each sprite was drawn at
## (CAM-07), built once and shared by four drawing layers for exactly that reason; the
## minimap needs raw board positions, not a yaw, and reading it here would make this a fifth
## consumer of a cache built for a different job (CAM-07's own risk note warns against this).
##
## One pass over `sim.units`, one over `sim.placed`, no sort — the per-frame cost this issue
## asks to be bounded by.

const IsoScript := preload("res://scripts/iso.gd")

const C_GROUND := Ui.C_OVERLAY     ## the board's own silhouette, dim — a backdrop, not a signal
const C_LANE := Ui.C_MUTED         ## a line, not a fill — reads apart from the ground by shape alone
const C_HEAT := Ui.C_ALERT         ## threat wash; alpha carries the weight, hue never varies by kind
const C_ONLINE := Ui.C_VERD
const C_OFFLINE := Ui.C_DIM
## Deliberately not C_HEAT: a unit dot the same hue as the wash it usually sits inside would
## vanish into it at exactly the density this exists to show — "shows every unit alive" needs
## a mark that survives being drawn over the wash, not just under it.
const C_UNIT := Ui.C_BONE
const C_CAMERA := Ui.C_BONE
const C_FOCUS := Ui.C_AMBER

## Threat wash resolution, in this control's own pixels — deliberately *not* a tile-space
## bucket, so it tracks the minimap's own size rather than the board's (a 64x64 board and a
## 12x10 one both fit the same panel). Bounded by the number of occupied cells, which is
## bounded by the number of alive units, so this never grows independently of the entity
## count this file is already allowed to walk once.
const HEAT_CELL_PX := 10.0
const HEAT_ALPHA_MAX := 0.55

## Region-step size, as a fraction of the camera's own current view rect — see `step()`.
## Slightly under 1.0 so consecutive steps keep a sliver of overlap rather than a jump that
## could put the new region's far edge exactly where the old one's near edge was.
const STEP_FRAC := 0.85

var view: Node2D = null            ## AnchorView; bound once from hud.gd
var _focused: bool = false

var _anchor_cached: String = ""
var _board_min: Vector2 = Vector2.ZERO   ## projected top-left of the grid's bounding box
var _map_scale: float = 1.0
var _draw_offset: Vector2 = Vector2.ZERO ## letterbox centring inside this control's own rect

var _dragging: bool = false


func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_STOP


func bind(v: Node2D) -> void:
	view = v
	_recompute()


func set_focused(f: bool) -> void:
	_focused = f
	queue_redraw()


func step(dir: Vector2) -> void:
	## `hud.gd`'s `lf_up`/`lf_down`/`lf_left`/`lf_right` while minimap-focused: `dir` is
	## already screen-axis (the same convention `anchor_view.gd`'s own board cursor uses),
	## so this needs no conversion — a region is roughly one screen's worth of board.
	if view == null:
		return
	var r: Rect2 = view.camera_view_rect()
	if r.size == Vector2.ZERO:
		return
	view.pan_by(Vector2(dir.x * r.size.x, dir.y * r.size.y) * STEP_FRAC)


func _process(_d: float) -> void:
	queue_redraw()


func _recompute() -> void:
	if view == null or view.sim == null:
		return
	var grid: Dictionary = view.sim.anchor.get("grid", {"w": 12, "h": 10})
	var gw := int(grid["w"])
	var gh := int(grid["h"])
	# The Iso projection's bounding box is *always* exactly 2:1, any grid: width and height
	# both scale off `(gw + gh)`, and `TILE_W == 2 * TILE_H` (decision 002). So this letterbox
	# is defensive, not load-bearing — a panel sized to that ratio (see hud.gd's own consts)
	# never actually bars.
	_board_min = Vector2(-float(gh) * IsoScript.TILE_W * 0.5, 0.0)
	var board_w: float = float(gw + gh) * IsoScript.TILE_W * 0.5
	var board_h: float = float(gw + gh) * IsoScript.TILE_H * 0.5
	_map_scale = minf(size.x / maxf(board_w, 1.0), size.y / maxf(board_h, 1.0))
	var used := Vector2(board_w, board_h) * _map_scale
	_draw_offset = (size - used) * 0.5
	_anchor_cached = view.anchor_id


func _to_map(tile: Vector2) -> Vector2:
	return _proj_to_map(IsoScript.tile_to_screen(tile.x, tile.y))


func _proj_to_map(p: Vector2) -> Vector2:
	## For a point already in `Iso.tile_to_screen`'s own output space — `camera_view_rect()`'s
	## own units — rather than a tile coordinate. The map is a pure affine copy of that space
	## (uniform scale, no rotation), so an axis-aligned rect in one is axis-aligned in the
	## other; see the camera-rect draw call below.
	return _draw_offset + (p - _board_min) * _map_scale


func _from_map(pt: Vector2) -> Vector2:
	return IsoScript.screen_to_tile((pt - _draw_offset) / maxf(_map_scale, 0.0001) + _board_min)


func _load_frac() -> float:
	## Global today (`sim.bus_load() / sim.capacity()`) — the one seam a regional power grid
	## (LF-086) would replace with a per-cell figure. Every reader of "how loaded is the bus"
	## on this control goes through here, not the sim fields directly, so that is a one-line
	## change when it comes.
	if view == null or view.sim == null:
		return 0.0
	return clampf(view.sim.bus_load() / maxf(view.sim.capacity(), 1.0), 0.0, 1.0)


func _gui_input(event: InputEvent) -> void:
	## Click centres the camera on the point clicked (at the camera's current zoom); holding
	## and dragging keeps doing that continuously. Both reuse `set_camera_override()`/
	## `camera_state()` exactly as `--camera` and `--select` already do — no new AnchorView
	## surface for this half of the interaction.
	if view == null:
		return
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		_dragging = event.pressed
		if event.pressed:
			_pan_to(event.position)
		accept_event()
	elif event is InputEventMouseMotion and _dragging:
		_pan_to(event.position)
		accept_event()


func _pan_to(local_pt: Vector2) -> void:
	var tile := _from_map(local_pt)
	view.set_camera_override(tile, float(view.camera_state()["zoom"]))


## LF-150: `-- --profile <frames>` (main.gd) previously instrumented only the four board
## layers (AnchorView, GlowLayer, FxAdditive, CombatFx) — this Control's own `_draw()` is
## HUD content and had no hook reaching it at all, so the minimap could not be measured
## against its own budget and no future HUD performance work could be either. Same pattern
## as the four board layers (see glow_layer.gd's own doc on why it is duplicated per file
## rather than shared through a base class: each profiled script already has a different
## parent type).
var _profile_ticks: PackedFloat64Array = PackedFloat64Array()
var _profiling: bool = false


func start_profiling() -> void:
	_profiling = true
	_profile_ticks.clear()


func profile_stats() -> Dictionary:
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
	if view == null or view.sim == null:
		return
	if _anchor_cached != view.anchor_id:
		_recompute()
	var sim = view.sim
	var anchor: Dictionary = sim.anchor
	var grid: Dictionary = anchor.get("grid", {"w": 12, "h": 10})
	var gw := int(grid["w"])
	var gh := int(grid["h"])

	# Power overlay: the reactor's own load/capacity ratio, tinting the whole frame — the
	# ambient "how hot is the bus" cue; the precise number stays in the REACTOR BUS readout.
	var load_col: Color = C_HEAT if sim.brownout else C_ONLINE
	draw_rect(Rect2(Vector2.ZERO, size), Color(load_col, 0.35 + 0.45 * _load_frac()), false, 2.0)
	if _focused:
		draw_rect(Rect2(Vector2.ZERO, size), C_FOCUS, false, 2.0)

	# 1. board extent
	var corners := PackedVector2Array([
		_to_map(Vector2(0, 0)), _to_map(Vector2(gw, 0)),
		_to_map(Vector2(gw, gh)), _to_map(Vector2(0, gh)),
	])
	draw_colored_polygon(corners, C_GROUND)

	# 2. lane polyline(s)
	for lane_doc in anchor.get("paths", []):
		var pts: Array = lane_doc.get("waypoints", [])
		if pts.size() < 2:
			continue
		var line := PackedVector2Array()
		for p in pts:
			line.append(_to_map(Vector2(float(p[0]), float(p[1]))))
		draw_polyline(line, C_LANE, 2.0)

	# 3. threat heat term — bucketed by leak_cost (decision 047), not by count: a Column and
	# a Shard are not the same amount of trouble. One pass over sim.units, no sort.
	var buckets := {}
	var max_w := 0.0
	for u in sim.units:
		if not bool(u["alive"]):
			continue
		var at: Vector2 = sim.point_at(int(u["lane"]), float(u["dist"]))
		var m := _to_map(at)
		var cell := Vector2i(int(floor(m.x / HEAT_CELL_PX)), int(floor(m.y / HEAT_CELL_PX)))
		var w: float = maxf(1.0, float(u["kind"].get("leak_cost", 1)))
		var total: float = float(buckets.get(cell, 0.0)) + w
		buckets[cell] = total
		max_w = maxf(max_w, total)
	if max_w > 0.0:
		for cell in buckets:
			var frac: float = float(buckets[cell]) / max_w
			var c: Vector2 = Vector2(cell.x, cell.y) * HEAT_CELL_PX
			draw_rect(Rect2(c, Vector2(HEAT_CELL_PX, HEAT_CELL_PX)),
				Color(C_HEAT, frac * HEAT_ALPHA_MAX))

	# 4. emplacement marks — shape carries online/offline, not colour alone: a filled square
	# is online, a hollow one is offline, so a greyscale copy still tells them apart.
	for p in sim.placed:
		var c: Vector2 = _to_map(Vector2(p["slot"]))
		var online: bool = bool(p["online"])
		draw_rect(Rect2(c - Vector2(3, 3), Vector2(6, 6)),
			C_ONLINE if online else C_OFFLINE, online, 1.5)

	# 5. unit marks
	for u in sim.units:
		if not bool(u["alive"]):
			continue
		var at: Vector2 = sim.point_at(int(u["lane"]), float(u["dist"]))
		draw_circle(_to_map(at), 1.5, C_UNIT)

	# 6. camera viewport rectangle — `camera_view_rect()` is already in the same projected
	# space `_proj_to_map()` maps from, so this is a plain affine transform of the rect, not
	# a re-derivation of it.
	var r: Rect2 = view.camera_view_rect()
	if r.size != Vector2.ZERO:
		draw_rect(Rect2(_proj_to_map(r.position), r.size * _map_scale), C_CAMERA, false, 2.0)
