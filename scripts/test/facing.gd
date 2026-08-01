extends SceneTree
## Headless facing-stability probe. Decision 048, extended by LF-108/ART-02.
##
##   godot --headless --path . --script res://scripts/test/facing.gd -- \
##       --anchor anchor-07 --yaws 4,8,16 --frac 0,0.05,0.1,0.15,0.2,0.25
##
## Facing is chosen from a heading, and the failure mode of any such rule is *strobing* —
## a unit or a turret flipping between two of the four rendered yaws frame after frame,
## which a screenshot cannot show and a person watching cannot count. This counts it.
##
##   1. Unit yaw is a pure function of path distance, so the whole path is swept: how many
##      times does the facing change, and does it ever return to a yaw it just left inside
##      a tile of travel? Units get no hysteresis (decision 049) — this reruns that proof
##      at every requested yaw count, not just 4, because the monotonic-rotation argument
##      decision 049 made is about the heading itself, not about how many buckets divide it.
##   2. Emplacement yaw follows the aim the sim records. Combat is simulated **once** — the
##      aim/slot heading is presentation-only and does not feed back into targeting, so one
##      recorded run is replayed through as many (yaw_count, hysteresis_frac) combinations
##      as `--yaws`/`--frac` ask for, which is what makes the LF-108 sweep affordable and
##      guarantees every row of the table is measuring identical underlying data.
##
## Printed once per combination, tagged for tools/yaw_band.py to scrape regardless of
## whatever else Blender/Godot prints around it (same trick render.py's HASHES_JSON and
## terrain_parity.gd's TERRAIN_PARITY_JSON use):
##
##   BAND_JSON {"anchor":"anchor-07","yaws":4,"frac":0.15,"changes":59,"reversals":3}
##   UNIT_JSON {"anchor":"anchor-07","yaws":16,"changes":6,"strobes_within_1_tile":0}
##
## anchor_view.gd cannot be preloaded here — it references the `Content` autoload, which a
## bare `--script` run does not register — so the two three-line heading expressions it
## builds are repeated below. The thing under test, `Iso.bucket_index_for_heading` (and, for
## the production YAW_COUNT=4 case, `Iso.yaw_for_heading`), is the real one.

const AnchorSimScript := preload("res://scripts/anchor_sim.gd")
const IsoScript := preload("res://scripts/iso.gd")

const TANGENT_EPS := 0.35
const REVERSAL_WINDOW_TICKS := 30   # matches the window decision 049 measured "40
                                     # reversals inside half a second" with


func _init() -> void:
	var aid := "anchor-07"
	var yaw_counts: Array[int] = [IsoScript.YAW_COUNT]
	var fracs: Array[float] = [IsoScript.YAW_HYSTERESIS_FRAC]
	var argv := OS.get_cmdline_user_args()
	for i in range(argv.size()):
		if argv[i] == "--anchor" and i + 1 < argv.size():
			aid = argv[i + 1]
		elif argv[i] == "--yaws" and i + 1 < argv.size():
			yaw_counts = []
			for tok in argv[i + 1].split(","):
				if tok.strip_edges() != "":
					yaw_counts.append(int(tok.strip_edges()))
		elif argv[i] == "--frac" and i + 1 < argv.size():
			fracs = []
			for tok in argv[i + 1].split(","):
				if tok.strip_edges() != "":
					fracs.append(float(tok.strip_edges()))

	var towers := _index(_read("res://data/towers.json").get("towers", []))
	var enemies := _index(_read("res://data/enemies.json").get("enemies", []))
	var anchor := _read("res://data/anchors/%s.json" % aid)
	# WAR-01 (in flight, concurrent with this issue): anchor_sim.gd's setup() now reads a
	# per-lane anchor["paths"] = [{"waypoints": [...]}], but data/anchors/*.json has not
	# been migrated off the legacy single anchor["path"] = [[x,y], ...] yet — reading a
	# not-yet-migrated anchor here would hand setup() zero lanes and an empty path_length.
	# Normalising the legacy shape locally (never writing it back to the file) keeps this
	# measurement runnable against today's data without touching data/** or anchor_sim.gd,
	# either of which belongs to whoever is mid-migration on WAR-01. Lane 0 only — this
	# harness, like decision 049's, measures a single path.
	if not anchor.has("paths") and anchor.has("path"):
		anchor["paths"] = [{"waypoints": anchor["path"]}]

	var sim = AnchorSimScript.new()
	sim.setup(anchor, towers, enemies, "standard")
	var plen: float = float(sim.path_length[0]) if sim.path_length.size() > 0 else 0.0

	# ── 1. units — recorded once, replayed at every requested yaw count ────────
	var steps := 8000
	var headings: Array[Vector2] = []
	var dists: Array[float] = []
	for i in range(steps + 1):
		var d: float = plen * float(i) / float(steps)
		var back: Vector2 = sim.point_at(0, maxf(0.0, d - TANGENT_EPS))
		var fwd: Vector2 = sim.point_at(0, minf(plen, d + TANGENT_EPS))
		headings.append(fwd - back)
		dists.append(d)

	for yc in yaw_counts:
		var prev := -1
		var seq: Array = []
		for i in range(headings.size()):
			var yaw: int = IsoScript.bucket_index_for_heading(headings[i], yc)
			if yaw != prev:
				seq.append([dists[i], yaw])
				prev = yaw
		var strobe := 0
		for i in range(seq.size() - 2):
			if seq[i][1] == seq[i + 2][1] and float(seq[i + 2][0]) - float(seq[i][0]) < 1.0:
				strobe += 1
		print("UNIT yaws=%d path_len=%.2f changes=%d strobes_within_1_tile=%d"
				% [yc, plen, seq.size() - 1, strobe])
		print("UNIT_JSON %s" % JSON.stringify({
			"anchor": aid, "yaws": yc, "changes": seq.size() - 1,
			"strobes_within_1_tile": strobe,
		}))

	# ── 2. emplacements — combat simulated once, headings recorded per tick ────
	var ids: Array = towers.keys()
	ids.sort()
	while true:
		var free: Array = sim.available_slots()   # PLC-01: computed, not sim.free_slots
		if free.is_empty():
			break
		var placed_one := false
		for tid in ids:
			var tw: Dictionary = towers[tid]
			if int(tw["cost"]) > sim.funds or String(tw.get("unlocked_at", "anchor-01")) > aid:
				continue
			if sim.online_draw() + float(tw["draw_mw"]) > sim.capacity():
				continue
			if sim.build_at(String(tid), float(free[0].x), float(free[0].y)):
				placed_one = true
				break
		if not placed_one:
			break

	# key -> Array[Vector2], one entry per tick this key had a placed record.
	var recorded: Dictionary = {}
	var ticks := 0
	for w in range(mini(3, anchor["waves"].size())):
		sim.begin_wave(w)
		var queue: Array = sim.wave_queue(w)
		var qi := 0
		var wt := 0.0
		var guard := 0
		while (qi < queue.size() or sim.any_alive()) and guard < 9000:
			guard += 1
			while qi < queue.size() and float(queue[qi][0]) <= wt + 1e-9:
				sim.spawn(String(queue[qi][2]), int(queue[qi][1]))
				qi += 1
			sim.tick()
			wt += AnchorSimScript.DT
			ticks += 1
			for p in sim.placed:
				# PLC-01: placed records carry x/y floats, not a slot.
				var key := "%d,%d" % [int(float(p["x"])), int(float(p["y"]))]
				var slot := Vector2(float(p["x"]), float(p["y"]))
				var aim: Variant = p.get("aim", null)
				var h: Vector2 = (aim as Vector2) - slot if aim != null else Vector2.ZERO
				if not recorded.has(key):
					recorded[key] = []
				(recorded[key] as Array).append(h)
		sim.prune_dead()
		if sim.lives <= 0:
			break

	for yc in yaw_counts:
		for frac in fracs:
			var prev_idx: Dictionary = {}
			var last_change_tick: Dictionary = {}
			var last_change_idx: Dictionary = {}
			var changes := 0
			var reversals := 0
			for key in recorded:
				var seq: Array = recorded[key]
				var tick := 0
				for h in seq:
					var idx: int = IsoScript.bucket_index_for_heading(
							h, yc, int(prev_idx.get(key, -1)), frac)
					if prev_idx.has(key) and int(prev_idx[key]) != idx:
						changes += 1
						if int(last_change_idx.get(key, -1)) == idx \
								and tick - int(last_change_tick.get(key, -999)) \
										< REVERSAL_WINDOW_TICKS:
							reversals += 1
						last_change_tick[key] = tick
						last_change_idx[key] = prev_idx.get(key, -1)
					prev_idx[key] = idx
					tick += 1
			print("BAND anchor=%s built=%d ticks=%d yaws=%d frac=%.4f changes=%d reversals=%d"
					% [aid, sim.placed.size(), ticks, yc, frac, changes, reversals])
			print("BAND_JSON %s" % JSON.stringify({
				"anchor": aid, "built": sim.placed.size(), "ticks": ticks,
				"yaws": yc, "frac": frac, "changes": changes, "reversals": reversals,
			}))
	quit()


func _read(path: String) -> Dictionary:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return {}
	var d: Variant = JSON.parse_string(f.get_as_text())
	return d if typeof(d) == TYPE_DICTIONARY else {}


func _index(rows: Array) -> Dictionary:
	var out := {}
	for r in rows:
		out[String(r["id"])] = r
	return out
