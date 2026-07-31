extends Node2D
## The fight, as seen rather than as computed: projectiles, impacts, debris, numbers.
##
## Owns the particle pool. Draws the non-emissive half of it at z 8 — debris, ground
## decals, floating text — while its sibling FxAdditive draws the emissive half at z 14
## through an additive material. One pool, two draw passes, because a CanvasItem cannot
## change blend mode partway through a _draw().
##
## Nothing here may decide a rule. It listens to AnchorSim's presentation signals and
## renders what already happened — the same contract as the `aim` key in anchor_sim.gd.
## The rules do not model projectile travel time (sim/engine.py's module docstring says so
## outright), so every hit already landed the instant it was fired; the travel animation
## below is a delay this layer invents cosmetically and nothing reads it back.

const IsoScript := preload("res://scripts/iso.gd")

# ─────────────────────────────────────────────────────────────── tuning ──

const MAX_FX := 480                  # hard cap on the particle pool; oldest evicted first
const HIT_FLASH_LIFE := 0.16
## A hit flash lives well under a second and a unit moves at most a few hundredths of a
## tile in that time, so "nearest live hit within this radius" is an unambiguous match in
## practice — see hit_flash_at()'s doc for why position, not identity, is the key at all.
const HIT_MATCH_RADIUS := 0.55
## hit_flash_at() looks up a 3x3 neighbourhood of unit-size (1x1 tile) buckets around the
## query tile. That is exact, not a heuristic, *because* HIT_MATCH_RADIUS < 1.0: a hit whose
## bucket differs from the query tile's bucket by more than one cell in either axis is, by
## construction of a unit-tile bucket, more than 1.0 tiles away on that axis alone — already
## outside HIT_MATCH_RADIUS. If HIT_MATCH_RADIUS ever grows past 1.0 this neighbourhood must
## grow with it (a radius of e.g. 1.4 needs a 5x5 lookup), so the two are coupled even though
## nothing in the type system says so — this comment is the only thing that will.
const _HIT_BUCKET_OFFSETS: Array[Vector2i] = [
	Vector2i(-1, -1), Vector2i(0, -1), Vector2i(1, -1),
	Vector2i(-1, 0),  Vector2i(0, 0),  Vector2i(1, 0),
	Vector2i(-1, 1),  Vector2i(0, 1),  Vector2i(1, 1),
]
const BEAM_CHARGE_TIME := 0.25       # seconds before fire_interval elapses that the lance glows
const FIELD_PULSE_PERIOD := 2.3
## ART-05 fallback ramp: seconds of continuous hold to visually reach full tint/width on a
## "sustained" beam, used only when the placed record carries no rules `ramp_mult` yet (i.e.
## before {{WAR-14}} lands). Purely cosmetic — see _compute_ramp_frac().
const SUSTAINED_RAMP_TIME := 2.5

## Board FX colours the data does not supply. Named here, not inline, so a colour choice
## reads as a decision rather than a magic literal.
const C_RICOCHET := Color(0.80, 0.90, 1.0)      # cold hard white-blue: "wrong weapon" must not
                                                 # read like a normal hit
const C_SHOCK_RING := Color(1.0, 0.95, 0.85)    # death shockwave — warm-white so it reads over
                                                 # every faction's shard tint
const C_SCORCH := Color(0.04, 0.03, 0.03, 0.55) # heavy-kill ground scorch; the one FX here
                                                 # that lingers
const C_BOUNTY := Color(0.95, 0.85, 0.45)       # echoes the HUD's funds amber; kept small — it
                                                 # is texture, not a second scoreboard
const C_DUST := Color(0.62, 0.55, 0.42)         # mortar/flak burst dust, desaturated so it
                                                 # never fights a weapon's own colour
const C_LEAK := Color(0.86, 0.30, 0.24)         # matches anchor_view's C_ALERT red, so a leak
                                                 # reads as the same alarm colour as brownout

## faction id -> shard base colour. Deliberately distinct at a glance per the brief: amber/
## bronze, pale steel-with-orange, desaturated violet-white.
const FACTION_SHARD := {
	"ordinal": Color(0.80, 0.58, 0.22),
	"sable-reach": Color(0.80, 0.82, 0.86),
	"hollow": Color(0.62, 0.55, 0.72),
}

## Fallback for a tower with no "fx" block in data — schema marks it optional on purpose.
const DEFAULT_FX := {
	"class": "bolt", "colour": "#ffffff", "core": "#ffffff",
	"speed": 20.0, "width": 3.0, "trail": 0.1,
}

## Trauma amounts. Small on purpose — see AnchorView.add_trauma(): this must never make the
## game hard to read, and the offset it drives is capped hard on that side too.
const TRAUMA_HEAVY_KILL := 0.35
const TRAUMA_MORTAR_IMPACT := 0.22
const TRAUMA_LEAK := 0.55            # the worst thing that can happen to the player, so the
                                      # biggest shake in the game

var view: Node2D

var _fx: Array[Dictionary] = []
## Sprite-flash lookup only — never drawn. {tile, age, life, shielded_resist}.
var _hits: Array[Dictionary] = []
## Vector2i(floori(tile.x), floori(tile.y)) -> Array[int] of indices into _hits, one entry
## per hit whose tile falls in that unit-tile cell. Rebuilt once per frame in _step_hits(),
## not per hit_flash_at() call — see that function's doc for why a per-lookup rebuild would
## put the quadratic term right back.
var _hit_buckets: Dictionary = {}
## unit_damaged events waiting out the travel time of the shot that caused them, so the
## impact reads in sync with the projectile's arrival rather than the instant the rules
## resolved it.
var _pending: Array[Dictionary] = []
var _last_shot: Dictionary = {}
## Reference into _fx for the shell this tower's shot just spawned, so a splash_landed
## arriving a line later can size that shell's eventual burst without a second signal.
var _last_shell: Dictionary = {}
## ART-05: state for every live "sustained" (held) beam, keyed on the firing placed record's
## `slot` (Vector2i — safe, per anchor_view.gd:983-991's precedent: placed records are only
## ever compared on `slot`, never by identity). One entry per {hold_start, end_time, offset,
## from, to, colour, core, width, ramp_tint, flicker, ramp}.
##
## Deliberately never pushed into `_fx`: a sustained beam lives for seconds, and MAX_FX's
## oldest-evicted pool would evict it mid-life under any real board load — exactly the failure
## this class exists to avoid (see the acceptance criteria in docs/issues/ART-05-persistent-
## beam.md). This dict is bounded by the number of live "sustained" emplacements, not by unit
## or shot count, so it needs no eviction policy of its own — the same reasoning that already
## keeps a beam tower's charge-up glow and a field tower's pulse ring (fx_additive.gd) outside
## the pool. Coordinates with {{WAR-06}}'s future per-category budget for `_fx` by simply never
## competing with it.
var _beams: Dictionary = {}


func _ready() -> void:
	view = get_parent() as Node2D


func bind(anchor_sim) -> void:
	## Wires the presentation signals. Called from AnchorView.boot() once the sim exists —
	## _ready() runs before boot(), so there is nothing to listen to at that point.
	##
	## Cleared here, not just left to drop naturally: if the sim is ever rebound onto a live
	## CombatFx instance, a slot key from the old sim could otherwise survive and be misread
	## against the new one's `placed` — fail closed per decision 055 rather than rely on this
	## never happening.
	_beams.clear()
	anchor_sim.shot_fired.connect(_on_shot_fired)
	anchor_sim.unit_damaged.connect(_on_unit_damaged)
	anchor_sim.splash_landed.connect(_on_splash_landed)
	anchor_sim.unit_killed.connect(_on_unit_killed)
	anchor_sim.unit_leaked.connect(_on_unit_leaked)


func pool() -> Array[Dictionary]:
	return _fx


func beams() -> Dictionary:
	## Live "sustained" beams, slot -> state. Read-only for callers — fx_additive.gd draws
	## from this every frame; see _beams' doc for why it lives outside `pool()`.
	return _beams


func hit_flash_at(tile: Vector2) -> Dictionary:
	## Nearest recent hit within HIT_MATCH_RADIUS tiles of `tile`, or {}. Keyed by proximity
	## rather than identity: unit_damaged deliberately carries a *kind* and a position, never
	## the mutable unit dictionary (see _on_unit_damaged() and anchor_sim.gd's _damage()), so
	## there is no id to key a lookup by, and the sim itself must never grow one on the unit
	## dictionary to give us one — anchor_sim.gd's splash loop tests `i == target_i` for
	## exactly that reason. Nearest-in-range is unambiguous in practice: two units standing
	## within HIT_MATCH_RADIUS of each other for the ~0.15s a flash lives is rare, and a wrong
	## match only costs a flash on the wrong sprite for one frame.
	##
	## Looks up only the 3x3 bucket neighbourhood of `tile` (see _HIT_BUCKET_OFFSETS' doc for
	## why 3x3 is exact rather than approximate at this radius) instead of scanning every live
	## hit, so this call is O(hits in the local neighbourhood) rather than O(all live hits) —
	## CAM-08 / LF-100. `_hit_buckets` is rebuilt once per frame in _step_hits(), never here,
	## so a lookup allocates nothing beyond the Vector2i keys it constructs (a value type, not
	## a heap object).
	##
	## Tie-break is preserved exactly, not just "close enough": the original linear scan (see
	## git history / CAM-08's test) applies `best_d = d2; best = h` under `d2 <= best_d` while
	## walking _hits in ascending index order, which is equivalent to "the highest-index entry
	## among those tied for the minimum distance wins" — a running minimum where an exact tie
	## always overwrites. That characterization does not depend on scan order, so comparing
	## candidates as `(d2, i)` pairs below reproduces it exactly regardless of which bucket, or
	## which order within a bucket, a candidate is visited in.
	var cx := floori(tile.x)
	var cy := floori(tile.y)
	var best: Dictionary = {}
	var best_d := HIT_MATCH_RADIUS * HIT_MATCH_RADIUS
	var best_i := -1
	for off in _HIT_BUCKET_OFFSETS:
		var key := Vector2i(cx + off.x, cy + off.y)
		if not _hit_buckets.has(key):
			continue
		var idxs: Array = _hit_buckets[key]
		for i in idxs:
			var h: Dictionary = _hits[i]
			var d: Vector2 = h["tile"] - tile
			var d2 := d.length_squared()
			if d2 < best_d or (d2 == best_d and i > best_i):
				best_d = d2
				best_i = i
				best = h
	if best.is_empty():
		return {}
	var frac: float = 1.0 - clampf(float(best["age"]) / float(best["life"]), 0.0, 1.0)
	if frac <= 0.0:
		return {}
	return {"strength": frac, "shielded_resist": bool(best["shielded_resist"])}


func tile_radius_screen(r: float) -> Vector2:
	## Same projection AnchorView._draw_range() uses on the board: a tile-space circle of
	## radius r projects to a 2:1 iso ellipse (semi-axes r*TILE_W/2*sqrt2, r*TILE_H/2*sqrt2 —
	## derive it from tile_to_screen() if that ever looks surprising). Burst rings use this so
	## their radius matches a tower's own `splash` in tile space rather than an eyeballed
	## pixel size — the whole point per the brief is that the player learns the real footprint
	## by watching.
	const SQRT2 := 1.4142135623730951
	return Vector2(r * IsoScript.TILE_W * 0.5 * SQRT2, r * IsoScript.TILE_H * 0.5 * SQRT2)


func _push(d: Dictionary) -> void:
	if _fx.size() >= MAX_FX:
		_fx.pop_front()
	_fx.append(d)


# ─────────────────────────────────────────────────────────────── signals ──

func _on_shot_fired(placed: Dictionary, from_tile: Vector2, to_tile: Vector2,
		_target_kind: Dictionary) -> void:
	var tw: Dictionary = placed["tower"]
	var fxd: Dictionary = tw.get("fx", DEFAULT_FX)
	var cls := String(fxd.get("class", "bolt"))
	var colour := Color.html(String(fxd.get("colour", "#ffffff")))
	var core := Color.html(String(fxd.get("core", fxd.get("colour", "#ffffff"))))
	var width: float = float(fxd.get("width", 3.0))
	var from: Vector2 = view.to_screen(from_tile)
	var to: Vector2 = view.to_screen(to_tile)
	var dist_tiles := from_tile.distance_to(to_tile)
	var speed: float = maxf(0.01, float(fxd.get("speed", 20.0)))
	var delay := 0.0
	_last_shell = {}

	match cls:
		"bolt":
			delay = dist_tiles / speed
			_push({"kind": "muzzle", "pos": from, "colour": core, "age": 0.0, "life": 0.08,
				"r": 10.0})
			_push({"kind": "bolt", "from": from, "to": to, "colour": colour, "core": core,
				"width": width, "trail": float(fxd.get("trail", 0.12)), "age": 0.0,
				"life": maxf(0.03, delay)})
		"arc":
			_spawn_arc(placed, from, to, colour, core, width)
		"beam":
			_push({"kind": "beam", "from": from, "to": to, "colour": colour, "core": core,
				"width": width, "age": 0.0, "life": 0.18})
		"sustained":
			_touch_beam(placed, from, to, colour, core, width, fxd)
		"flak":
			delay = dist_tiles / speed
			var shell := {"kind": "flak_shell", "from": from, "to": to, "colour": colour,
				"core": core, "width": width, "age": 0.0, "life": maxf(0.03, delay),
				"splash": 0.0}
			_push(shell)
			_last_shell = shell
		"mortar":
			delay = dist_tiles / speed
			var shell2 := {"kind": "mortar_shell", "from": from, "to": to, "colour": colour,
				"core": core, "width": width, "age": 0.0, "life": maxf(0.15, delay),
				"splash": 0.0, "apex": 46.0}
			_push(shell2)
			_last_shell = shell2
		_:
			pass

	if cls != "arc":
		_last_shot = {"class": cls, "colour": colour, "delay": delay}


func _spawn_arc(placed: Dictionary, from: Vector2, to: Vector2, colour: Color, core: Color,
		width: float) -> void:
	var main_pts := _jagged(from, to, 5, 12.0)
	var chain_pts := PackedVector2Array()
	var chain_target: Variant = _nearest_other_unit(placed, to)
	if chain_target != null:
		chain_pts = _jagged(to, chain_target, 3, 9.0)
	_push({"kind": "arc", "points": main_pts, "chain": chain_pts, "colour": colour,
		"core": core, "width": width, "age": 0.0, "life": 0.12})
	_last_shot = {"class": "arc", "colour": colour, "delay": 0.0}


func _nearest_other_unit(placed: Dictionary, exclude_screen: Vector2) -> Variant:
	## Purely cosmetic: draws a second, shorter arc from the primary target to the nearest
	## other unit in range, because the tower's own note calls this a chain. arc-node has no
	## `splash` in data — the rules never chain — so nothing found here may feed back into
	## _damage, and nothing does: this only reads view.sim.units, never writes to one.
	var tw: Dictionary = placed["tower"]
	var rng: float = float(tw["range"])
	var sx: float = float(placed["slot"].x)
	var sy: float = float(placed["slot"].y)
	var best: Variant = null
	var best_d := INF
	for u in view.sim.units:
		if not u["alive"]:
			continue
		var at: Vector2 = view.sim.point_at(u["dist"])
		var screen: Vector2 = view.to_screen(at)
		if screen.distance_squared_to(exclude_screen) < 4.0:
			continue                       # this is the primary target, not "another" unit
		var dx: float = sx - at.x
		var dy: float = sy - at.y
		if dx * dx + dy * dy > rng * rng:
			continue
		var d := screen.distance_squared_to(exclude_screen)
		if d < best_d:
			best_d = d
			best = screen
	return best


func _jagged(a: Vector2, b: Vector2, segments: int, jitter: float) -> PackedVector2Array:
	## Re-seeded every call (randf(), not a fixed pattern) so a chain-node's lightning never
	## repeats the same shape twice — cosmetic only, no sim state involved.
	var pts := PackedVector2Array([a])
	var dir := b - a
	var perp := Vector2(-dir.y, dir.x).normalized()
	for i in range(1, segments):
		var t := float(i) / float(segments)
		var base := a.lerp(b, t)
		var off := (randf() - 0.5) * 2.0 * jitter
		pts.append(base + perp * off)
	pts.append(b)
	return pts


func _touch_beam(placed: Dictionary, from: Vector2, to: Vector2, colour: Color, core: Color,
		width: float, fxd: Dictionary) -> void:
	## fx.class == "sustained": extend (or start) the held beam keyed on this placed record's
	## slot. Position tracking happens every frame in _step_beams(), off placed["aim"] — this
	## only marks the engagement alive on each shot_fired, per ART-05's contract against
	## decision 053: no new signal, just inferring "still firing" from the cadence of the one
	## that already exists.
	var slot: Vector2i = placed["slot"]
	var now: float = view.sim_time()
	if not _beams.has(slot):
		_beams[slot] = {"hold_start": now,
			"offset": float(absi(hash(slot)) % 1000) / 1000.0 * TAU}
	var b: Dictionary = _beams[slot]
	var tw: Dictionary = placed["tower"]
	var interval: float = float(tw.get("fire_interval", 1.0))
	# 1.6x the tower's own cadence, not a fixed constant tied to one tower's stats: comfortably
	# survives the gap between two consecutive shots at whatever fire_interval a "sustained"
	# tower ships with, while still dropping the beam soon after it truly stops firing.
	b["end_time"] = now + maxf(0.15, interval * 1.6)
	b["from"] = from
	b["to"] = to
	b["colour"] = colour
	b["core"] = core
	b["width"] = width
	b["ramp_tint"] = Color.html(
		String(fxd.get("ramp_tint", fxd.get("core", fxd.get("colour", "#ffffff")))))
	b["flicker"] = clampf(float(fxd.get("flicker", 0.06)), 0.0, 1.0)
	_beams[slot] = b


func _step_beams(_delta: float) -> void:
	if _beams.is_empty():
		return
	var now: float = view.sim_time()
	var drop: Array[Vector2i] = []
	for key in _beams.keys():
		var slot: Vector2i = key
		var b: Dictionary = _beams[slot]
		var p: Dictionary = _find_placed(slot)
		if p.is_empty() or not bool(p.get("online", true)) or not p.has("aim"):
			# Fail closed (decision 055): sold, breaker cut, or the rules dropped the target
			# (p.erase("aim") in anchor_sim.gd) — the beam ends, nothing errors.
			drop.append(slot)
			continue
		var tw: Dictionary = p["tower"]
		var fxd: Dictionary = tw.get("fx", DEFAULT_FX)
		if String(fxd.get("class", "")) != "sustained":
			drop.append(slot)     # e.g. resold into a different tower on the same slot
			continue
		if now > float(b["end_time"]):
			drop.append(slot)
			continue
		var aim: Vector2 = p["aim"]
		var slot_v: Vector2 = Vector2(float(p["slot"].x), float(p["slot"].y))
		b["from"] = view.to_screen(slot_v)
		b["to"] = view.to_screen(aim)
		b["ramp"] = _compute_ramp_frac(p, b, now)
		_beams[slot] = b
	for dead_slot in drop:
		_beams.erase(dead_slot)


func _find_placed(slot: Vector2i) -> Dictionary:
	for entry in view.sim.placed:
		var p: Dictionary = entry
		if p["slot"] == slot:
			return p
	return {}


func _compute_ramp_frac(p: Dictionary, b: Dictionary, now: float) -> float:
	## Prefers the rules' own ramp once {{WAR-14}} lands (placed["ramp_mult"] against the
	## tower's `ramp.max_mult`); until then there is no rules ramp to read, so this class is
	## shippable on its own, driven instead by how long the beam has been held continuously —
	## purely cosmetic, and replaced wholesale the moment the rules field exists.
	var tw: Dictionary = p["tower"]
	if p.has("ramp_mult") and tw.has("ramp"):
		var ramp_data: Dictionary = tw["ramp"]
		var max_mult: float = float(ramp_data.get("max_mult", 1.0))
		if max_mult > 1.0:
			return clampf((float(p["ramp_mult"]) - 1.0) / (max_mult - 1.0), 0.0, 1.0)
	var hold: float = maxf(0.0, now - float(b["hold_start"]))
	return clampf(hold / SUSTAINED_RAMP_TIME, 0.0, 1.0)


func _on_unit_damaged(unit_kind: Dictionary, at_tile: Vector2, amount: float, killed: bool,
		shielded_resist: bool) -> void:
	_pending.append({"delay": float(_last_shot.get("delay", 0.0)), "tile": at_tile,
		"kind": unit_kind, "amount": amount, "killed": killed,
		"shielded_resist": shielded_resist, "class": String(_last_shot.get("class", "bolt")),
		"colour": _last_shot.get("colour", Color.WHITE)})


func _on_splash_landed(_at_tile: Vector2, radius: float) -> void:
	if not _last_shell.is_empty():
		_last_shell["splash"] = radius


func _on_unit_killed(u: Dictionary) -> void:
	## `u` is the mutable per-instance unit dictionary — existing precedent for this signal:
	## anchor_view's own death-audio hook already reads it the same way, and it fired before
	## this pass touched the file. Read-only here: hp/kind/dist, never written back.
	var kind: Dictionary = u["kind"]
	var tile: Vector2 = view.sim.point_at(float(u["dist"]))
	var heavy: bool = float(kind.get("hp", 0.0)) >= 150.0
	var faction := String(kind.get("faction", "ordinal"))
	var base_col: Color = FACTION_SHARD.get(faction, Color(0.8, 0.8, 0.8))
	var pos: Vector2 = view.to_screen(tile)

	_push({"kind": "ring", "pos": pos, "colour": C_SHOCK_RING, "age": 0.0,
		"life": (0.4 if heavy else 0.28), "r0": 4.0, "r1": (60.0 if heavy else 34.0),
		"width": 3.0, "flat": false})
	_push({"kind": "muzzle", "pos": pos, "colour": Color(1, 1, 1), "age": 0.0,
		"life": 0.1, "r": (22.0 if heavy else 13.0)})

	var n := randi_range(9, 14) if heavy else randi_range(6, 9)
	for i in range(n):
		var a := randf() * TAU
		var spd := randf_range(70.0, 190.0) * (1.4 if heavy else 1.0)
		_push({"kind": "debris", "pos": pos, "vel": Vector2(cos(a), sin(a) * 0.6) * spd,
			"colour": base_col, "age": 0.0, "life": randf_range(0.5, 0.9),
			"grav": 220.0, "drag": 2.2, "size": randf_range(2.5, 5.0)})

	if heavy:
		_push({"kind": "scorch", "pos": pos, "age": 0.0, "life": 7.0})
		view.add_trauma(TRAUMA_HEAVY_KILL)

	var bounty := int(float(kind.get("bounty", 0.0)) * float(view.sim.bounty_mult))
	if bounty > 0:
		_push({"kind": "bounty", "pos": pos + Vector2(0, -20), "text": "+$%d" % bounty,
			"age": 0.0, "life": 0.9})


func _on_unit_leaked(u: Dictionary) -> void:
	## The worst thing that can happen to the player, made unmissable on the board: a hard
	## flash at the exit, a shock ring, and the strongest trauma this file has.
	var tile: Vector2 = view.sim.point_at(float(u["dist"]))
	var pos: Vector2 = view.to_screen(tile)
	_push({"kind": "muzzle", "pos": pos, "colour": C_LEAK, "age": 0.0, "life": 0.18, "r": 34.0})
	_push({"kind": "ring", "pos": pos, "colour": C_LEAK, "age": 0.0, "life": 0.55,
		"r0": 6.0, "r1": 76.0, "width": 4.0, "flat": false})
	view.add_trauma(TRAUMA_LEAK)


# ─────────────────────────────────────────────────────────────── update ──

func _process(delta: float) -> void:
	if view == null or view.sim == null:
		return
	_step_pending(delta)
	_step_pool(delta)
	_step_hits(delta)
	_step_beams(delta)


func _step_pending(delta: float) -> void:
	var keep: Array[Dictionary] = []
	for p in _pending:
		p["delay"] = float(p["delay"]) - delta
		if p["delay"] > 0.0:
			keep.append(p)
			continue
		_resolve_hit(p)
	_pending = keep


func _resolve_hit(p: Dictionary) -> void:
	var tile: Vector2 = p["tile"]
	var cls := String(p["class"])
	var colour: Color = p["colour"]
	var shielded_resist: bool = p["shielded_resist"]
	if not bool(p["killed"]):
		_hits.append({"tile": tile, "age": 0.0, "life": HIT_FLASH_LIFE,
			"shielded_resist": shielded_resist})
	_spawn_hit_impact(tile, colour, cls, shielded_resist)


func _spawn_hit_impact(tile: Vector2, colour: Color, cls: String, shielded_resist: bool) -> void:
	var pos: Vector2 = view.to_screen(tile)
	if shielded_resist:
		# A hit that mostly bounced reads completely differently from a normal one: a hard
		# flat ricochet flare and a ring on the shield, not a spark — "wrong weapon for this
		## target", not a number.
		_push({"kind": "ring", "pos": pos, "colour": C_RICOCHET, "age": 0.0, "life": 0.22,
			"r0": 5.0, "r1": 24.0, "width": 3.0, "flat": false})
		for i in range(5):
			_push({"kind": "flare", "pos": pos, "colour": C_RICOCHET, "age": 0.0, "life": 0.12,
				"ang": (float(i) / 5.0) * TAU + 0.3, "len": 16.0})
		return
	var n := 3 if cls in ["flak", "mortar"] else 6
	for i in range(n):
		var a := randf() * TAU
		var spd := randf_range(50.0, 140.0)
		_push({"kind": "spark", "pos": pos, "vel": Vector2(cos(a), sin(a) * 0.6) * spd,
			"colour": colour, "age": 0.0, "life": randf_range(0.14, 0.26), "grav": 0.0,
			"drag": 3.0})


func _step_pool(delta: float) -> void:
	var keep: Array[Dictionary] = []
	var spawned: Array[Dictionary] = []
	for e in _fx:
		var age: float = float(e["age"])
		var life: float = float(e["life"])
		var new_age := age + delta
		match String(e["kind"]):
			"spark", "debris":
				var vel: Vector2 = e["vel"]
				vel.y += float(e.get("grav", 0.0)) * delta
				vel *= 1.0 / (1.0 + float(e.get("drag", 0.0)) * delta)
				e["vel"] = vel
				e["pos"] = e["pos"] + vel * delta
			"flak_shell":
				if age < life and new_age >= life:
					spawned.append_array(_burst_ring_and_shrapnel(e, false))
			"mortar_shell":
				if age < life and new_age >= life:
					spawned.append_array(_burst_ring_and_shrapnel(e, true))
					view.add_trauma(TRAUMA_MORTAR_IMPACT)
			_:
				pass
		e["age"] = new_age
		if new_age < life:
			keep.append(e)
	keep.append_array(spawned)
	if keep.size() > MAX_FX:
		keep = keep.slice(keep.size() - MAX_FX, keep.size())
	_fx = keep


func _burst_ring_and_shrapnel(shell: Dictionary, is_mortar: bool) -> Array[Dictionary]:
	var pos: Vector2 = shell["to"]
	var splash_tiles: float = float(shell.get("splash", 0.0))
	var r := tile_radius_screen(splash_tiles) if splash_tiles > 0.0 else Vector2(24.0, 12.0)
	var out: Array[Dictionary] = []
	out.append({"kind": "muzzle", "pos": pos, "colour": shell["core"], "age": 0.0,
		"life": 0.1, "r": 16.0})
	out.append({"kind": "ring", "pos": pos, "colour": shell["colour"], "age": 0.0,
		"life": 0.32, "r0": 6.0, "r1": r.x, "r1y": r.y, "width": 3.0, "flat": true})
	if is_mortar:
		out.append({"kind": "dust", "pos": pos, "age": 0.0, "life": 0.7, "r1": r.x, "r1y": r.y})
		for i in range(6):
			var a := randf() * TAU
			out.append({"kind": "debris", "pos": pos,
				"vel": Vector2(cos(a), sin(a) * 0.5) * randf_range(50.0, 120.0),
				"colour": C_DUST, "age": 0.0, "life": randf_range(0.4, 0.7),
				"grav": 260.0, "drag": 2.0, "size": randf_range(2.0, 4.0)})
	var n := randi_range(8, 12)
	for i in range(n):
		var a2 := randf() * TAU
		out.append({"kind": "spark", "pos": pos,
			"vel": Vector2(cos(a2), sin(a2) * 0.6) * randf_range(90.0, 220.0),
			"colour": shell["colour"], "age": 0.0, "life": randf_range(0.2, 0.4),
			"grav": 60.0, "drag": 1.6})
	return out


func _step_hits(delta: float) -> void:
	var keep: Array[Dictionary] = []
	for h in _hits:
		h["age"] = float(h["age"]) + delta
		if float(h["age"]) < float(h["life"]):
			keep.append(h)
	_hits = keep
	_rebuild_hit_buckets()


func _rebuild_hit_buckets() -> void:
	## Once per frame — after ageing/pruning, before _draw() reads them — rather than once
	## per hit_flash_at() call, which is the whole point (CAM-08 / LF-100). _hits changes at
	## most once per frame (aged here, appended in _resolve_hit() earlier in the same
	## _process()), so this is the only place the bucket dictionary needs rebuilding.
	_hit_buckets.clear()
	for i in range(_hits.size()):
		var tile: Vector2 = _hits[i]["tile"]
		var key := Vector2i(floori(tile.x), floori(tile.y))
		if _hit_buckets.has(key):
			(_hit_buckets[key] as Array).append(i)
		else:
			_hit_buckets[key] = [i]


# ──────────────────────────────────────────────────────────────── draw ──

func _draw() -> void:
	if view == null or view.sim == null:
		return
	for e in _fx:
		match String(e["kind"]):
			"debris":
				_draw_debris(e)
			"scorch":
				_draw_scorch(e)
			"dust":
				_draw_dust(e)
			"bounty":
				_draw_bounty(e)
			"mortar_shell":
				_draw_mortar_shadow(e)
			_:
				pass


func _draw_debris(e: Dictionary) -> void:
	var frac: float = 1.0 - clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var col: Color = e["colour"]
	col.a = frac
	var size: float = float(e.get("size", 3.0))
	var pos: Vector2 = e["pos"]
	draw_rect(Rect2(pos - Vector2(size, size) * 0.5, Vector2(size, size)), col)


func _draw_scorch(e: Dictionary) -> void:
	var frac: float = 1.0 - clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var col := C_SCORCH
	col.a *= clampf(frac * 2.5, 0.0, 1.0)     # holds solid, then fades only in its last stretch
	var pos: Vector2 = e["pos"]
	var pts := PackedVector2Array()
	for i in range(16):
		var a := TAU * float(i) / 16.0
		pts.append(pos + Vector2(cos(a) * 22.0, sin(a) * 11.0))
	draw_colored_polygon(pts, col)


func _draw_dust(e: Dictionary) -> void:
	var frac: float = clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var col := C_DUST
	col.a = (1.0 - frac) * 0.5
	var pos: Vector2 = e["pos"]
	var r1: float = float(e["r1"]) * (0.3 + frac * 0.9)
	var r1y: float = float(e["r1y"]) * (0.3 + frac * 0.9)
	var pts := PackedVector2Array()
	for i in range(24):
		var a := TAU * float(i) / 24.0
		pts.append(pos + Vector2(cos(a) * r1, sin(a) * r1y))
	pts.append(pts[0])
	draw_polyline(pts, col, 3.0)


func _draw_bounty(e: Dictionary) -> void:
	var frac: float = clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var col := C_BOUNTY
	col.a = 1.0 - frac
	var pos: Vector2 = e["pos"] - Vector2(0, frac * 26.0)
	var font := ThemeDB.fallback_font
	var text: String = e["text"]
	var w := font.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, 13).x
	draw_string(font, pos - Vector2(w * 0.5, 0), text, HORIZONTAL_ALIGNMENT_LEFT, -1, 13, col)


func _draw_mortar_shadow(e: Dictionary) -> void:
	var t: float = clampf(float(e["age"]) / float(e["life"]), 0.0, 1.0)
	var ground: Vector2 = Vector2(e["from"]).lerp(e["to"], t)
	var height_frac: float = sin(PI * t)      # 0 at either end of the arc, 1 at the apex
	var r := lerpf(9.0, 4.0, height_frac)
	var col := Color(0, 0, 0, lerpf(0.4, 0.12, height_frac))
	var pts := PackedVector2Array()
	for i in range(12):
		var a := TAU * float(i) / 12.0
		pts.append(ground + Vector2(cos(a) * r, sin(a) * r * 0.5))
	draw_colored_polygon(pts, col)
