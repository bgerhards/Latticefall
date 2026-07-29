extends SceneTree
## Headless facing-stability probe. Decision 048.
##
##   godot --headless --path . --script res://scripts/test/facing.gd -- --anchor anchor-07
##
## Facing is chosen from a heading, and the failure mode of any such rule is *strobing* —
## a unit or a turret flipping between two of the four rendered yaws frame after frame,
## which a screenshot cannot show and a person watching cannot count. This counts it.
##
##   1. Unit yaw is a pure function of path distance, so the whole path is swept: how many
##      times does the facing change, and does it ever return to a yaw it just left inside
##      a tile of travel?
##   2. Emplacement yaw follows the aim the sim records, so real waves are run and the
##      changes are counted twice — with the hysteresis band and without it. The delta is
##      what the band is buying.
##
## anchor_view.gd cannot be preloaded here — it references the `Content` autoload, which a
## bare `--script` run does not register — so the two three-line heading expressions it
## builds are repeated below. The thing under test, `Iso.yaw_for_heading`, is the real one.

const AnchorSimScript := preload("res://scripts/anchor_sim.gd")
const IsoScript := preload("res://scripts/iso.gd")

const TANGENT_EPS := 0.35


func _init() -> void:
	var aid := "anchor-07"
	var argv := OS.get_cmdline_user_args()
	for i in range(argv.size()):
		if argv[i] == "--anchor" and i + 1 < argv.size():
			aid = argv[i + 1]

	var towers := _index(_read("res://data/towers.json").get("towers", []))
	var enemies := _index(_read("res://data/enemies.json").get("enemies", []))
	var anchor := _read("res://data/anchors/%s.json" % aid)

	var sim = AnchorSimScript.new()
	sim.setup(anchor, towers, enemies, "standard")

	# ── 1. units ────────────────────────────────────────────────────────────
	var steps := 8000
	var prev := -1
	var seq: Array = []
	for i in range(steps + 1):
		var d: float = float(sim.path_length) * float(i) / float(steps)
		var back: Vector2 = sim.point_at(maxf(0.0, d - TANGENT_EPS))
		var fwd: Vector2 = sim.point_at(minf(float(sim.path_length), d + TANGENT_EPS))
		var yaw: int = IsoScript.yaw_for_heading(fwd - back)
		if yaw != prev:
			seq.append([d, yaw])
			prev = yaw
	var strobe := 0
	for i in range(seq.size() - 2):
		if seq[i][1] == seq[i + 2][1] and float(seq[i + 2][0]) - float(seq[i][0]) < 1.0:
			strobe += 1
	print("UNIT %s path_len=%.2f changes=%d strobes_within_1_tile=%d"
			% [aid, sim.path_length, seq.size() - 1, strobe])
	print("UNIT seq=%s" % [seq])

	# ── 2. emplacements ─────────────────────────────────────────────────────
	var ids: Array = towers.keys()
	ids.sort()
	while sim.free_slots.size() > 0:
		var placed_one := false
		for tid in ids:
			var tw: Dictionary = towers[tid]
			if int(tw["cost"]) > sim.funds or String(tw.get("unlocked_at", "anchor-01")) > aid:
				continue
			if sim.online_draw() + float(tw["draw_mw"]) > sim.capacity():
				continue
			if sim.build_at(String(tid), sim.free_slots[0]):
				placed_one = true
				break
		if not placed_one:
			break

	var hyst := {}
	var raw := {}
	var last_raw_change := {}
	var last_hyst_change := {}
	var hyst_strobes := 0
	var hyst_changes := 0
	var raw_changes := 0
	var raw_strobes := 0
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
				sim.spawn(String(queue[qi][1]))
				qi += 1
			sim.tick()
			wt += AnchorSimScript.DT
			ticks += 1
			for p in sim.placed:
				var key := "%d,%d" % [p["slot"].x, p["slot"].y]
				var slot := Vector2(float(p["slot"].x), float(p["slot"].y))
				var aim: Variant = p.get("aim", null)
				var h: Vector2 = (aim as Vector2) - slot if aim != null else Vector2.ZERO
				var hy: int = IsoScript.yaw_for_heading(h, int(hyst.get(key, -1)),
						IsoScript.YAW_HYSTERESIS_DEG)
				if hyst.has(key) and hyst[key] != hy:
					hyst_changes += 1
					if int(last_hyst_change.get(key + "|y", -1)) == hy \
							and ticks - int(last_hyst_change.get(key, -999)) < 30:
						hyst_strobes += 1
					last_hyst_change[key] = ticks
					last_hyst_change[key + "|y"] = hyst[key]
				hyst[key] = hy
				var rw: int = IsoScript.yaw_for_heading(h, -1)
				if raw.has(key) and raw[key] != rw:
					raw_changes += 1
					if int(last_raw_change.get(key + "|y", -1)) == rw \
							and ticks - int(last_raw_change.get(key, -999)) < 30:
						raw_strobes += 1
					last_raw_change[key] = ticks
					last_raw_change[key + "|y"] = raw[key]
				raw[key] = rw
		sim.prune_dead()
		if sim.lives <= 0:
			break
	print(("TOWER built=%d ticks=%d hysteresis: changes=%d strobes=%d · "
			+ "raw: changes=%d strobes=%d")
			% [sim.placed.size(), ticks, hyst_changes, hyst_strobes, raw_changes, raw_strobes])
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
