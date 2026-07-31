extends SceneTree
## Differential + timing probe for CombatFx.hit_flash_at()'s tile-bucket lookup. CAM-08 /
## LF-100: fx_additive.gd's _draw_hit_flashes() calls hit_flash_at() once per live unit, and
## the old implementation scanned the *entire* live hit list on every call — quadratic in
## unit count (measured: 7.67 ms at 900 units, 29% of draw cost). combat_fx.gd now buckets
## _hits by integer tile once per frame and hit_flash_at() only walks the 3x3 neighbourhood
## of the query tile.
##
## This script proves two things a screenshot cannot:
##
##   1. CORRECTNESS — the bucketed hit_flash_at() picks the exact same hit as the original
##      linear scan (kept below as `_linear_hit_flash_at()`, a byte-for-byte copy of the
##      pre-CAM-08 algorithm) for every query in a synthetic 900-unit frame, including the
##      adversarial cases the bucket boundary and the tie-break rule can get wrong: exact
##      ties (two hits landing on the same unit in the same frame — plausible with several
##      towers converging fire), radius-boundary distances, bucket-boundary tile fractions,
##      and negative coordinates (free placement / terrain can put a footprint below 0).
##   2. COST — hit_flash_at() timed directly (Time.get_ticks_usec(), no engine draw overhead
##      in the loop) at 150 / 300 / 600 / 900 units against a hit list of equal size (the
##      near-worst case per the issue: "the hit list grows with the number of units being
##      shot"), scattered along a path-width band so *local* density near a query tile stays
##      roughly constant as N grows — which is exactly the property bucketing is supposed to
##      exploit. The linear reference is timed the same way for comparison.
##
## No `--profile` hook exists yet in main.gd (that is CAM-06's job, not built at the time
## this shipped) so this is a standalone microbenchmark of hit_flash_at() in isolation, not
## a reproduction of LF-100's original in-engine measurement. It is offered as evidence of
## the *shape* of the fix (linear-ish vs. quadratic, and the crossover point) rather than as
## a restatement of the original 7.67 ms figure — see the PR notes for that caveat spelled
## out against the real numbers.
##
##   godot --headless --path . --script res://scripts/test/hit_bucket.gd

const CombatFxScript := preload("res://scripts/combat_fx.gd")

const RADIUS := 0.55           # mirrors CombatFx.HIT_MATCH_RADIUS; asserted equal below


func _init() -> void:
	assert(is_equal_approx(CombatFxScript.HIT_MATCH_RADIUS, RADIUS),
		"HIT_MATCH_RADIUS drifted from this test's copy — update RADIUS above")

	var ok := true
	ok = _run_differential("adversarial", _adversarial_hits(), _adversarial_queries()) and ok
	var fuzz := _fuzz_case(300, 900, 20)
	ok = _run_differential("fuzz", fuzz[0], fuzz[1]) and ok
	_run_benchmark()

	print("HIT_BUCKET_RESULT %s" % ("PASS" if ok else "FAIL"))
	quit(0 if ok else 1)


# ──────────────────────────────────────────────────────────── reference ──

func _linear_hit_flash_at(hits: Array[Dictionary], tile: Vector2) -> Dictionary:
	## Exact copy of combat_fx.gd's pre-CAM-08 hit_flash_at() body: scan _hits in order,
	## `d2 <= best_d` — the ground truth the bucketed version is measured against.
	var best: Dictionary = {}
	var best_d := RADIUS * RADIUS
	for h in hits:
		var d: Vector2 = h["tile"] - tile
		var d2 := d.length_squared()
		if d2 <= best_d:
			best_d = d2
			best = h
	if best.is_empty():
		return {}
	var frac: float = 1.0 - clampf(float(best["age"]) / float(best["life"]), 0.0, 1.0)
	if frac <= 0.0:
		return {}
	return {"strength": frac, "shielded_resist": bool(best["shielded_resist"])}


# ──────────────────────────────────────────────────────── fixture cases ──

func _adversarial_hits() -> Array[Dictionary]:
	## Every case the risk list calls out by name: exact ties on the same tile (distinct
	## ages so the winner is identifiable), a pair straddling a bucket boundary within
	## HIT_MATCH_RADIUS of each other, a hit sitting exactly on a bucket edge (integer tile
	## coordinate), and hits at and past negative coordinates (floori vs. truncation).
	var h: Array[Dictionary] = []
	# Two hits on the same tile, same instant — the "two towers hit the same unit this
	# frame" case. Distinguishable by age so whichever is picked is provable.
	h.append({"tile": Vector2(5.0, 5.0), "age": 0.02, "life": 0.16, "shielded_resist": false})
	h.append({"tile": Vector2(5.0, 5.0), "age": 0.09, "life": 0.16, "shielded_resist": true})
	# A pair straddling the x=3/4 bucket boundary, both within RADIUS of x=3.98.
	h.append({"tile": Vector2(3.7, 2.0), "age": 0.01, "life": 0.16, "shielded_resist": false})
	h.append({"tile": Vector2(4.1, 2.0), "age": 0.05, "life": 0.16, "shielded_resist": false})
	# Sitting exactly on an integer tile edge.
	h.append({"tile": Vector2(8.0, 8.0), "age": 0.03, "life": 0.16, "shielded_resist": false})
	# Negative coordinates, straddling 0 on both axes.
	h.append({"tile": Vector2(-0.3, -0.2), "age": 0.04, "life": 0.16, "shielded_resist": true})
	h.append({"tile": Vector2(-1.1, 0.4), "age": 0.06, "life": 0.16, "shielded_resist": false})
	# One far outside everything else, so most queries have a clean non-match too.
	h.append({"tile": Vector2(40.0, 40.0), "age": 0.0, "life": 0.16, "shielded_resist": false})
	return h


func _adversarial_queries() -> Array[Vector2]:
	var q: Array[Vector2] = []
	q.append(Vector2(5.0, 5.0))          # dead on the tie — must resolve deterministically
	q.append(Vector2(5.2, 5.1))          # near the tie, still both in range
	q.append(Vector2(3.98, 2.0))         # equidistant-ish, straddles the bucket boundary
	q.append(Vector2(3.7, 2.0))          # exact match on one side of the boundary
	q.append(Vector2(8.0, 8.0))          # exact match on the bucket-edge hit
	q.append(Vector2(7.99, 7.99))        # just inside the neighbouring bucket
	q.append(Vector2(-0.3, -0.2))        # exact match, negative coordinates
	q.append(Vector2(-0.6, 0.1))         # between the two negative hits
	q.append(Vector2(-5.0, -5.0))        # far from everything — must return {}
	q.append(Vector2(0.0, 0.0))          # origin, near-ish the negative cluster
	return q


func _fuzz_case(hit_count: int, query_count: int, rng_seed: int) -> Array:
	## Broad randomized coverage, including negative coordinates, over a reproducible seed.
	seed(rng_seed)
	var hits: Array[Dictionary] = []
	for i in range(hit_count):
		hits.append({
			"tile": Vector2(randf_range(-10.0, 60.0), randf_range(-10.0, 60.0)),
			"age": randf_range(0.0, 0.15),
			"life": 0.16,
			"shielded_resist": randf() < 0.3,
		})
	var queries: Array[Vector2] = []
	for i in range(query_count):
		# Half the queries land near an existing hit (forces genuine matches and ties to be
		# exercised), half are free-scattered.
		if i % 2 == 0 and not hits.is_empty():
			var base: Vector2 = hits[randi() % hits.size()]["tile"]
			queries.append(base + Vector2(randf_range(-0.6, 0.6), randf_range(-0.6, 0.6)))
		else:
			queries.append(Vector2(randf_range(-10.0, 60.0), randf_range(-10.0, 60.0)))
	return [hits, queries]


# ──────────────────────────────────────────────────────────── differential ──

func _run_differential(label: String, hits: Array[Dictionary], queries: Array[Vector2]) -> bool:
	var cfx = CombatFxScript.new()
	cfx._hits = hits
	cfx._rebuild_hit_buckets()

	var mismatches := 0
	for tile in queries:
		var linear := _linear_hit_flash_at(hits, tile)
		var bucketed: Dictionary = cfx.hit_flash_at(tile)
		if not _same(linear, bucketed):
			mismatches += 1
			print("HIT_BUCKET_MISMATCH case=%s tile=%s linear=%s bucketed=%s"
				% [label, tile, linear, bucketed])
	print("HIT_BUCKET_DIFF case=%s queries=%d mismatches=%d"
		% [label, queries.size(), mismatches])
	return mismatches == 0


func _same(a: Dictionary, b: Dictionary) -> bool:
	if a.is_empty() != b.is_empty():
		return false
	if a.is_empty():
		return true
	return is_equal_approx(float(a["strength"]), float(b["strength"])) \
		and bool(a["shielded_resist"]) == bool(b["shielded_resist"])


# ──────────────────────────────────────────────────────────────── timing ──

func _run_benchmark() -> void:
	print("HIT_BUCKET_BENCH n linear_ms bucketed_ms linear_us_per_call bucketed_us_per_call")
	for n in [150, 300, 600, 900]:
		seed(1000 + n)
		# A path-width band (3 tiles) rather than a broad scatter: this is the shape that
		# matters, because it keeps *local* density near any one query roughly constant as N
		# grows — the property bucketing exploits. A broad scatter would make both
		# implementations look artificially fast at large N.
		var hits: Array[Dictionary] = []
		for i in range(n):
			hits.append({
				"tile": Vector2(float(i) * 0.3 + randf_range(-0.1, 0.1),
					2.0 + randf_range(-1.4, 1.4)),
				"age": randf_range(0.0, 0.15), "life": 0.16, "shielded_resist": false,
			})
		var queries: Array[Vector2] = []
		for i in range(n):
			queries.append(Vector2(float(i) * 0.3 + randf_range(-0.2, 0.2),
				2.0 + randf_range(-1.5, 1.5)))

		var t0 := Time.get_ticks_usec()
		for tile in queries:
			_linear_hit_flash_at(hits, tile)
		var t1 := Time.get_ticks_usec()
		var linear_us := t1 - t0

		var cfx = CombatFxScript.new()
		cfx._hits = hits
		cfx._rebuild_hit_buckets()
		var t2 := Time.get_ticks_usec()
		for tile in queries:
			cfx.hit_flash_at(tile)
		var t3 := Time.get_ticks_usec()
		var bucketed_us := t3 - t2

		print("HIT_BUCKET_BENCH %d %.3f %.3f %.3f %.3f" % [
			n, linear_us / 1000.0, bucketed_us / 1000.0,
			float(linear_us) / float(n), float(bucketed_us) / float(n),
		])

	_check_no_allocation()


func _check_no_allocation() -> void:
	## Not a substitute for CAM-06's MEMORY_STATIC-across-600-frames check (no engine loop
	## here), but a direct sanity check that repeated lookups do not monotonically grow
	## object count: 20000 calls against a fixed 300-hit list, object count sampled before
	## and after a warmup batch (to let any one-time lazy init settle) and again after the
	## timed batch.
	var hits: Array[Dictionary] = []
	seed(77)
	for i in range(300):
		hits.append({"tile": Vector2(randf_range(0.0, 40.0), randf_range(0.0, 40.0)),
			"age": 0.05, "life": 0.16, "shielded_resist": false})
	var cfx = CombatFxScript.new()
	cfx._hits = hits
	cfx._rebuild_hit_buckets()

	for _i in range(2000):
		cfx.hit_flash_at(Vector2(randf_range(0.0, 40.0), randf_range(0.0, 40.0)))
	var before := Performance.get_monitor(Performance.OBJECT_COUNT)
	for _i in range(20000):
		cfx.hit_flash_at(Vector2(randf_range(0.0, 40.0), randf_range(0.0, 40.0)))
	var after := Performance.get_monitor(Performance.OBJECT_COUNT)
	print("HIT_BUCKET_OBJCOUNT before=%d after=%d delta=%d"
		% [before, after, after - before])
