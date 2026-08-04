#!/usr/bin/env python3
"""
Generate a large-board multi-lane anchor layout (LF-080 / THEATRE SCALE 05).

Decision 073 set the board target to 48 x 48 -- 2,304 tiles against the 270 of a shipped
anchor. Hand-authoring one of those is not viable: a 48-square board with four lanes wants
roughly ninety build points and three hundred tiles of lane, and every one of them has to
satisfy the bounds/standoff/no-duplicate rules `tools/validate/validate_data.py` enforces.
So the generator has to exist before the content does, which is why this file is on the
critical path rather than a convenience.

**Preview-first, like `tools/densify.py`.** Every rule in that file was wrong on its first
cut and each mistake cost a full re-sweep to discover, so it prints the table it *would*
write before writing anything. Same here, and more so: a layout is a geometry, and a
geometry is something a person can see is wrong in one second and cannot see at all in a
JSON diff. `--preview` renders the board as ASCII with the lanes, the spawns, the keep and
every derived slot on it. Look at the board before spending the cores.

**Nothing here writes to `data/anchors/`.** The output path is wherever `--out` says, and
the intended workflow is generate -> preview -> grade -> discard. That workflow is only
safe because `sim/content.py`'s `all_anchor_ids()` now discovers anchors with
`git ls-files` (LF-132): before that fix, a scratch board dropped anywhere under
`data/anchors/` became real content for `tools/density.py` and the gate's `wave density`
check, and one 64-square synthetic already broke that check outright.

WHAT IS DERIVED, AND FROM WHAT. The whole point of a generator is that the numbers come
out of the board rather than being carried forward from an 18x15 one (LF-187). Every
budget below is a shipped-content ratio times a measured property of the generated board:

    slot count       union lane tiles / TILES_PER_SLOT          (act 3 ships 3.5 tiles/slot)
    capacity_mw      slot count x max draw x SATURATION_FRAC    (act 3 ships 50%)
    starting_funds   slot count x FUNDS_PER_SLOT                (act 3 ships 336/slot)
    lives            total wave leak cost x LEAK_BUDGET         (act 3 ships ~21%)
    wave mass        shipped act mean x the lane-length ratio
    capacity_decay   capacity x the shipped act's decay fraction

`--budget shipped` freezes all of them at a shipped act-3 anchor's absolute values
instead, which is the control: it is what "carry decision 074's numbers forward to 48
squared" actually produces, and it is meant to lose.

**Determinism.** No RNG, no clock, no set iteration, no dict-order dependence: every
quantity is integer arithmetic or a sorted traversal, and the JSON is emitted with an
explicit key order. Two runs with the same arguments produce byte-identical files, which
`--selftest` asserts by generating twice and comparing.

    .venv/bin/python tools/genboard.py --preview
    .venv/bin/python tools/genboard.py --lanes 4 --sweeps 3 --preview
    .venv/bin/python tools/genboard.py --out /tmp/anchor-25.json --id anchor-25
    .venv/bin/python tools/genboard.py --budget shipped --preview
    .venv/bin/python tools/genboard.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import (Anchor, Enemy, anchor_from_doc, all_anchor_ids,  # noqa: E402
                         load_anchor, load_enemies, load_towers)

# ── shipped-content ratios ─────────────────────────────────────────────────────
# Measured off data/anchors/ rather than chosen, and recomputed at run time by
# `shipped_ratios()` so they cannot rot the way a number written into prose does. The
# comments record what they were when this file was written, as a tripwire.

# Union lane tiles per authored slot. Act 3 ships ~3.5 (42 tiles / 12 slots).
TILES_PER_SLOT = 3.5
# capacity_mw as a fraction of "every emplacement at max draw including upgrades" --
# validate_data.py's board-saturation invariant, which errors at 1.0 and warns above 0.80.
# Act 1 sits at 21-49%, act 3 at exactly 50%. Decision 048.
SATURATION_FRAC = 0.50
# Perpendicular offsets, in tiles, tried in order when siting a slot beside a lane. 1 is
# the closest an integer position can sit and still clear the legality predicate
# (lane_half_width 0.5 + FOOTPRINT_RADIUS 0.45 = 0.95), and 3 is inside every weapon's
# range, so no generated slot can trip validate_data.py's dead-slot check.
SLOT_OFFSETS = (1, 2, 3)
# Wave ramp, first wave to last, as a multiple of the anchor's mean wave. Shipped act 3
# runs 0.34 -> 1.35; this is the same shape with the endpoints rounded.
RAMP_LO, RAMP_HI = 0.40, 1.50
# Seconds between spawns of one entry, by unit speed class. Escorts lead (densify.py's
# ESCORT_INTERVAL), heavies trail.
FAST_INTERVAL, SLOW_INTERVAL = 0.8, 3.2
LEAD_IN = 22.0


def shipped_ratios(act: int) -> dict:
    """The ratios above, re-measured against the tracked anchors of `act`.

    Measured rather than constant for the reason CLAUDE.md gives about prose: a number
    written down here rots the first time somebody sweeps a capacity. `funds_per_slot` and
    `leak_budget` have no defensible closed form at all -- they are simply what the act
    ships -- so they are only ever read from the content.
    """
    enemies = load_enemies()
    ids = [a for a in all_anchor_ids() if load_anchor(a).act == act]
    if not ids:
        raise SystemExit(f"no tracked anchors in act {act} to derive ratios from")
    funds, slots, lives, leak, units, waves, decay, cap = 0, 0, 0, 0, 0, 0, 0.0, 0.0
    lane_len = 0.0
    for aid in ids:
        a = load_anchor(aid)
        funds += a.starting_funds
        slots += len(a.slots)
        lives += a.lives
        decay += a.capacity_decay_mw
        cap += a.capacity_mw
        lane_len += sum(ln.path_length for ln in a.lanes)
        for w in a.waves:
            waves += 1
            for s in w.spawns:
                leak += s.count * enemies[s.enemy].leak_cost
                units += s.count
    # Composition: the act's own unit mix, as a fraction of its total unit count, in a
    # sorted order so the emitted wave table cannot depend on dict insertion order.
    mix: dict[str, int] = {}
    for aid in ids:
        for w in load_anchor(aid).waves:
            for s in w.spawns:
                mix[s.enemy] = mix.get(s.enemy, 0) + s.count
    total = sum(mix.values())
    return {
        "anchors": ids,
        "funds_per_slot": funds / slots,
        "leak_budget": lives / leak,
        "units_per_wave": units / waves,
        "lane_length": lane_len / len(ids),
        "decay_frac": (decay / len(ids)) / (cap / len(ids)),
        "mix": tuple(sorted((eid, n / total) for eid, n in mix.items())),
    }


def towers_rows() -> list[dict]:
    return json.loads((ROOT / "data" / "towers.json").read_text())["towers"]


def max_draw_at(rows: list[dict], anchor_id: str) -> float:
    """Worst-case draw of the hungriest emplacement unlocked at `anchor_id`, upgrade
    included. Mirrors `validate_data.py`'s `_tower_max_draw` term for term -- the
    board-saturation invariant is computed against this exact quantity (LF-103), and a
    generator that derived capacity from the base draw alone would author boards that
    trip the invariant it was trying to satisfy."""
    avail = [t for t in rows if t.get("unlocked_at", "anchor-01") <= anchor_id]

    def one(t: dict) -> float:
        up = (t.get("upgrade") or {}).get("draw_mw")
        return max(t["draw_mw"], up) if up is not None else t["draw_mw"]

    return max(one(t) for t in avail)


# ── geometry ───────────────────────────────────────────────────────────────────

def dedupe(pts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop repeated points and merge collinear runs.

    Both are validation errors rather than cosmetic: `validate_data.py` rejects a
    zero-length segment outright, and a collinear pair inflates the waypoint count without
    changing the path, which makes `build_candidate_lattice()` do strictly more segment
    tests for the same board.
    """
    out: list[tuple[int, int]] = []
    for p in pts:
        if out and p == out[-1]:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (a[0] == b[0] == p[0]) or (a[1] == b[1] == p[1]):
                out[-1] = p
                continue
        out.append(p)
    return out


def lane_waypoints(i: int, n: int, w: int, h: int, sweeps: int) -> list[tuple[int, int]]:
    """Lane `i` of `n` on a `w` x `h` board, as an axis-aligned integer polyline.

    THE SHAPE, and why it is not a straight run. A 48-square board is 8.5x the AREA of an
    18x15 one but only ~3x its DIAMETER, so a lane that crosses it edge to edge is 44 tiles
    against today's 41 -- the board grows and the lane does not. Length at scale therefore
    has to come from meander, not from the board, and that is a design choice a generator
    must make explicitly rather than inherit. Each lane boustrophedons within its own
    horizontal BAND, `sweeps` vertical passes while it progresses east; length is
    approximately `(w - 8) + sweeps * band_height + |band - keep_row|`.

    THE BANDS are what make the board multi-lane in the sense decision 073 means: n
    disjoint horizontal strips, each with its own spawn, separated by a gutter of
    buildable rows that no lane enters. A gun sited in a gutter covers the two lanes either
    side of it, which is the whole tactical content of a front line and is unavailable on a
    single-lane board at any size.

    THE FUNNEL. Every lane leaves its band at `x = w - 8`, runs to the keep row, and enters
    the keep at `(w - 4, h // 2)`. That shared final stretch is deliberate -- an anchor is
    one site, and the lanes have to arrive at it -- and it is also the board's obvious
    strong point, so it is the first thing a grade should be read against.
    """
    band_h = h // n
    top = i * band_h + 2
    bot = (i + 1) * band_h - 3
    if bot <= top:                       # a band too thin to serpentine in
        top = bot = i * band_h + band_h // 2
    keep_x, keep_y = w - 4, h // 2
    funnel_x = w - 8

    # Spawn edge: north for the first lane, south for the last, west for the rest, so a
    # 3+ lane board has spawns on three edges rather than a stack on one.
    entry_x = 2 + 2 * i
    pts: list[tuple[int, int]] = []
    if n >= 3 and i == 0:
        pts.append((entry_x, 0))
        pts.append((entry_x, top))
    elif n >= 3 and i == n - 1:
        pts.append((entry_x, h - 1))
        pts.append((entry_x, bot))
    else:
        pts.append((0, top))
        pts.append((entry_x, top))

    # Serpentine: `sweeps` vertical passes, evenly spaced across the band's x-extent.
    span = funnel_x - entry_x
    y_here = pts[-1][1]
    for k in range(1, sweeps + 1):
        x = entry_x + span * k // (sweeps + 1)
        pts.append((x, y_here))
        y_here = bot if y_here == top else top
        pts.append((x, y_here))

    pts.append((funnel_x, y_here))
    pts.append((funnel_x, keep_y))
    pts.append((keep_x, keep_y))
    return dedupe(pts)


def lane_tiles(pts: list[tuple[int, int]]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for a, b in zip(pts, pts[1:]):
        if a[0] == b[0]:
            lo, hi = sorted((a[1], b[1]))
            out |= {(a[0], y) for y in range(lo, hi + 1)}
        else:
            lo, hi = sorted((a[0], b[0]))
            out |= {(x, a[1]) for x in range(lo, hi + 1)}
    return out


def seg_d2(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Squared point-to-segment distance. Copied term for term from
    `sim.engine.build_candidate_lattice()` so a slot this file calls legal is one the
    rules call legal -- see decision 078 on why this is `+ - * /` and comparisons only."""
    abx, aby = bx - ax, by - ay
    ab2 = abx * abx + aby * aby
    t = 0.0 if ab2 <= 0.0 else min(1.0, max(0.0, ((px - ax) * abx + (py - ay) * aby) / ab2))
    dx, dy = px - (ax + abx * t), py - (ay + aby * t)
    return dx * dx + dy * dy


def min_d2_to_lanes(px: float, py: float, lanes: list[list[tuple[int, int]]]) -> float:
    best = 1e18
    for pts in lanes:
        for a, b in zip(pts, pts[1:]):
            d2 = seg_d2(px, py, a[0], a[1], b[0], b[1])
            if d2 < best:
                best = d2
    return best


def point_along(pts: list[tuple[int, int]], s: float) -> tuple[float, float, float, float]:
    """Position and unit tangent at arclength `s` along an axis-aligned polyline."""
    run = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if seg <= 0:
            continue
        if s <= run + seg:
            t = (s - run) / seg
            tx = (b[0] - a[0]) / seg
            ty = (b[1] - a[1]) / seg
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, tx, ty
        run += seg
    a, b = pts[-2], pts[-1]
    seg = abs(b[0] - a[0]) + abs(b[1] - a[1]) or 1
    return float(b[0]), float(b[1]), (b[0] - a[0]) / seg, (b[1] - a[1]) / seg


def place_slots(lanes: list[list[tuple[int, int]]], w: int, h: int,
                n_slots: int) -> list[tuple[int, int]]:
    """`n_slots` integer build points beside the lanes, spread by arclength.

    Still integer, but no longer because it has to be: LF-219 widened
    `data/schema/anchor.schema.json` from `integer` to `number`, so the schema now admits
    the sub-tile positions both rule engines have accepted since PLC-01. What kept this
    integer is a different argument -- `render()` below indexes an ASCII grid by slot
    (`cell[y][x]`), the count rather than the positions is what this generator actually
    binds (`_effective_cap()`'s fallback; see LF-222), and moving the positions moves every
    generated board's numbers including `--selftest`'s. Emitting a half-tile lattice here is
    real work with its own evidence, tracked separately, not a side effect of the schema
    widening.

    The walk is per-lane and interleaved by arclength so the sites are spread along every
    lane rather than packed on the longest one, and each candidate tries the offsets in
    `SLOT_OFFSETS` on alternating sides. A candidate is kept only if it is in bounds, off
    every lane's tiles, at least `0.95` from every lane polyline (the rules' own legality
    predicate -- lane_half_width 0.5 plus FOOTPRINT_RADIUS 0.45), and not already taken.

    **Since PLC-04 the POSITIONS here are close to vestigial for the grader and the COUNT
    is not.** `Sim._try_build()` sources candidates from the board-wide lattice, not from
    this list; what this list still does is set `_effective_cap()`'s fallback, which is the
    emplacement budget, and give `sim/coverage.py` the per-slot sites it reports presence
    for. Sited carefully anyway, because the coverage instrument is the reason to generate
    a board at all.
    """
    taken: set[tuple[int, int]] = set()
    on_lane: set[tuple[int, int]] = set()
    for pts in lanes:
        on_lane |= lane_tiles(pts)
    lengths = [sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(p, p[1:]))
               for p in lanes]
    total = sum(lengths) or 1.0

    # Per-lane share of the budget, proportional to that lane's length.
    quota = [max(1, int(round(n_slots * L / total))) for L in lengths]
    out: list[tuple[int, int]] = []
    # Interleaved across lanes: index k of every lane before index k+1 of any of them, so
    # truncating at n_slots never strips one lane bare.
    for k in range(max(quota)):
        for li, pts in enumerate(lanes):
            if k >= quota[li] or len(out) >= n_slots:
                continue
            step = lengths[li] / quota[li]
            s = step * (k + 0.5)
            px, py, tx, ty = point_along(pts, s)
            # perpendicular, alternating side by (lane + index) so neighbouring sites do
            # not stack on one flank
            side = 1 if (k + li) % 2 == 0 else -1
            for off in SLOT_OFFSETS:
                for sgn in (side, -side):
                    cx = int(round(px - ty * off * sgn))
                    cy = int(round(py + tx * off * sgn))
                    if not (0 <= cx < w and 0 <= cy < h):
                        continue
                    if (cx, cy) in on_lane or (cx, cy) in taken:
                        continue
                    if min_d2_to_lanes(cx, cy, lanes) < 0.95 * 0.95:
                        continue
                    taken.add((cx, cy))
                    out.append((cx, cy))
                    break
                else:
                    continue
                break
    return sorted(out, key=lambda p: (p[1], p[0]))


# ── waves ──────────────────────────────────────────────────────────────────────

def apportion(total: int, weights: list[float]) -> list[int]:
    """Split `total` whole units across `weights`, largest remainder, ties to lower index.

    Not `round(total * w)`, which was the first cut and was measurably wrong: at
    `--budget shipped` an act-3 wave of 13.7 units divided by four lanes and then by five
    unit types rounds every share below 0.5 to zero, and the generated wave came out as
    "one hollow-shard per lane" -- the act's composition destroyed by rounding, and a
    control that tests nothing. Largest remainder is exact by construction (the parts sum
    to `total`) and keeps the largest fractional shares, so the dominant unit of the act
    survives a small wave instead of being the only survivor.

    Deterministic: the sort key carries the index, so equal remainders always break the
    same way in both directions of a re-run.
    """
    if total <= 0 or not weights:
        return [0] * len(weights)
    s = sum(weights)
    if s <= 0:
        return [0] * len(weights)
    exact = [total * w / s for w in weights]
    out = [int(v) for v in exact]
    rem = total - sum(out)
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - out[i]), i))
    for i in order[:rem]:
        out[i] += 1
    return out


def build_waves(n_waves: int, n_lanes: int, units_per_wave: float,
                mix: tuple[tuple[str, float], ...],
                enemies: dict[str, Enemy]) -> list[dict]:
    """A wave table with the act's own composition, ramped, spread across every lane.

    Composition is the shipped act's measured unit mix (`shipped_ratios()`), not a roster
    written into this file: an act's identity travels with which units it fields, and that
    is content. What the generator supplies is the SCALE -- how many, over how many lanes,
    on what ramp.

    Every lane is spawned into on every wave: `validate_data.py` errors on a lane no wave
    ever uses, and a lane that only carries units on wave 7 is a lane the player has no
    reason to defend for six waves.
    """
    order = [eid for eid, _ in mix]                  # already sorted by shipped_ratios()
    weights = [f for _, f in mix]
    waves: list[dict] = []
    for wi in range(n_waves):
        t = wi / (n_waves - 1) if n_waves > 1 else 1.0
        target = int(round(units_per_wave * (RAMP_LO + (RAMP_HI - RAMP_LO) * t)))
        per_lane = apportion(target, [1.0] * n_lanes)
        spawns: list[dict] = []
        for lane in range(n_lanes):
            counts = apportion(per_lane[lane], weights)
            for eid, n in zip(order, counts):
                if n <= 0:
                    continue
                e = enemies[eid]
                fast = e.speed >= 1.2
                spawns.append({
                    "enemy": eid,
                    "count": n,
                    "interval": FAST_INTERVAL if fast else SLOW_INTERVAL,
                    "delay": 0.0 if fast else 6.0,
                    "lane": lane,
                })
        if not spawns:                                # a ramp floor that rounded to nothing
            spawns.append({"enemy": order[0], "count": 1, "interval": FAST_INTERVAL,
                           "delay": 0.0, "lane": 0})
        waves.append({"lead_in": LEAD_IN, "spawns": spawns})
    return waves


# ── assembly ───────────────────────────────────────────────────────────────────

def generate(anchor_id: str, act: int, w: int, h: int, n_lanes: int, sweeps: int,
             n_waves: int, budget: str) -> dict:
    """The whole board, as an anchor document. Pure function of its arguments plus the
    tracked content `shipped_ratios()` reads -- no clock, no RNG, no set iteration."""
    ratios = shipped_ratios(act)
    enemies = load_enemies()
    lanes = [lane_waypoints(i, n_lanes, w, h, sweeps) for i in range(n_lanes)]
    lengths = [sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(p, p[1:]))
               for p in lanes]
    union = set()
    for pts in lanes:
        union |= lane_tiles(pts)
    scale = sum(lengths) / ratios["lane_length"]

    if budget == "derived":
        n_slots = max(3, int(round(len(union) / TILES_PER_SLOT)))
        units_per_wave = ratios["units_per_wave"] * scale
    else:
        # The control: a shipped act-3 anchor's absolute numbers, unchanged, on a board
        # 8.5x the area. This is what "carry decision 074 forward" means (LF-187).
        ref = load_anchor(ratios["anchors"][-1])
        n_slots = len(ref.slots)
        units_per_wave = ratios["units_per_wave"]

    slots = place_slots(lanes, w, h, n_slots)
    n_slots = len(slots)                              # what the board actually admitted
    max_draw = max_draw_at(towers_rows(), anchor_id)
    if budget == "derived":
        capacity = round(n_slots * max_draw * SATURATION_FRAC)
        funds = int(round(n_slots * ratios["funds_per_slot"]))
    else:
        ref = load_anchor(ratios["anchors"][-1])
        capacity = ref.capacity_mw
        funds = ref.starting_funds

    waves = build_waves(n_waves, n_lanes, units_per_wave, ratios["mix"], enemies)
    leak = sum(s["count"] * enemies[s["enemy"]].leak_cost for wv in waves
               for s in wv["spawns"])
    lives = max(1, int(round(leak * ratios["leak_budget"])))
    decay = round(capacity * ratios["decay_frac"], 1) if act == 3 else 0.0

    doc: dict = {
        "schema": "anchor",
        "id": anchor_id,
        "act": act,
        "title": f"Generated {w}x{h} theatre, {n_lanes} lanes",
        "capacity_mw": capacity,
        "starting_funds": funds,
        "lives": lives,
        "grid": {"w": w, "h": h},
        "paths": [{"id": f"lane-{i}", "waypoints": [[x, y] for x, y in pts]}
                  for i, pts in enumerate(lanes)],
        "slots": [[x, y] for x, y in slots],
        "max_emplacements": n_slots,
        "waves": waves,
    }
    if decay:
        doc["capacity_decay_mw"] = decay
    return doc


# ── preview ────────────────────────────────────────────────────────────────────

def render(doc: dict) -> str:
    """The board as ASCII: lane index digit, `#` where two lanes share a tile, `o` for a
    slot, `S` for a spawn, `K` for the keep.

    This is the whole reason the tool is preview-first. A wrong lane is instantly visible
    here and completely invisible in the JSON -- the same argument `densify.py` makes for
    printing a wave table, applied to a geometry, where it is stronger."""
    w, h = doc["grid"]["w"], doc["grid"]["h"]
    cell = [["." for _ in range(w)] for _ in range(h)]
    for li, lane in enumerate(doc["paths"]):
        pts = [(p[0], p[1]) for p in lane["waypoints"]]
        for (x, y) in sorted(lane_tiles(pts)):
            cell[y][x] = str(li % 10) if cell[y][x] == "." else "#"
    for x, y in doc["slots"]:
        cell[y][x] = "o"
    for lane in doc["paths"]:
        x, y = lane["waypoints"][0][0], lane["waypoints"][0][1]
        cell[y][x] = "S"
    kx, ky = doc["paths"][0]["waypoints"][-1][:2]
    cell[ky][kx] = "K"
    ruler = "    " + "".join(str(x // 10 % 10) if x % 5 == 0 else " " for x in range(w))
    rows = [f"{y:3d} " + "".join(r) for y, r in enumerate(cell)]
    return "\n".join([ruler] + rows)


def preview(doc: dict) -> None:
    enemies = load_enemies()
    a: Anchor = anchor_from_doc(doc)
    lengths = [ln.path_length for ln in a.lanes]
    union = set()
    for lane in doc["paths"]:
        union |= lane_tiles([(p[0], p[1]) for p in lane["waypoints"]])
    towers = load_towers()
    max_draw = max_draw_at(towers_rows(), a.id)
    sat = len(a.slots) * max_draw
    rng = [t.range for t in towers.values() if t.is_weapon and t.unlocked_at <= a.id]

    print(render(doc))
    print(f"\n{a.id}  {a.title}  ·  act {a.act}")
    print(f"  grid            {a.grid[0]}x{a.grid[1]} = {a.grid[0] * a.grid[1]} tiles")
    print(f"  lanes           {len(a.lanes)}  lengths "
          f"{' '.join(f'{L:.0f}' for L in lengths)}  total {sum(lengths):.0f}  "
          f"union {len(union)} tiles")
    print(f"  spawn points    {len(a.lanes)}  "
          f"{' '.join(str(tuple(p['waypoints'][0])) for p in doc['paths'])}")
    print(f"  slots           {len(a.slots)}  "
          f"({len(union) / max(1, len(a.slots)):.1f} lane tiles each)")
    print(f"  capacity        {a.capacity_mw:.0f} MW  "
          f"= {a.capacity_mw / sat:.0%} of board saturation "
          f"({len(a.slots)} x {max_draw:.0f} MW)")
    print(f"  funds / lives   {a.starting_funds}  /  {a.lives}")
    if a.capacity_decay_mw:
        print(f"  capacity decay  {a.capacity_decay_mw} MW per wave")
    # Unlocks are keyed on the anchor ID STRING, never on `act` (`unlocked_for()` in
    # sim/content.py is `t.unlocked_at <= anchor_id`), so `--act 1 --id anchor-25` produces
    # act-1 WAVES with every act-3 emplacement available. That is not a bug to hide inside
    # the generator -- the id is what the rules read -- so the count is printed next to the
    # act, where the mismatch is visible.
    unlocked = [t for t in towers.values() if t.unlocked_at <= a.id]
    print(f"  unlocked        {len(unlocked)} emplacements at id '{a.id}' "
          f"(unlocks follow the ID, not --act)")
    print(f"  weapon range    {min(rng):.1f}-{max(rng):.1f} tiles  → one emplacement at "
          f"max range covers {2 * max(rng) / sum(lengths):.1%} of the lane set, "
          f"{2 * max(rng) / max(lengths):.1%} of its longest lane")

    leak = sum(s["count"] * enemies[s["enemy"]].leak_cost
               for wv in doc["waves"] for s in wv["spawns"])
    print(f"\n  {'wave':>4s} {'units':>6s} {'leak':>5s} {'hp':>8s} {'drain':>7s}  per-lane")
    for wi, wv in enumerate(doc["waves"], 1):
        units = sum(s["count"] for s in wv["spawns"])
        hp = sum(s["count"] * enemies[s["enemy"]].hp for s in wv["spawns"])
        drain = sum(s["count"] * enemies[s["enemy"]].drains_mw for s in wv["spawns"])
        lk = sum(s["count"] * enemies[s["enemy"]].leak_cost for s in wv["spawns"])
        per = [sum(s["count"] for s in wv["spawns"] if s["lane"] == li)
               for li in range(len(a.lanes))]
        print(f"  {wi:>4d} {units:>6d} {lk:>5d} {hp:>8.0f} {drain:>7.0f}  "
              f"{' '.join(str(p) for p in per)}")
    print(f"  {'tot':>4s} {sum(sum(s['count'] for s in wv['spawns']) for wv in doc['waves']):>6d} "
          f"{leak:>5d}   lives {a.lives} = {a.lives / leak:.1%} leak budget")


# ── CLI ────────────────────────────────────────────────────────────────────────

def emit(doc: dict) -> str:
    return json.dumps(doc, indent=2) + "\n"


def selftest(args: argparse.Namespace) -> int:
    """Two generations of the same arguments must be byte-identical (verification bar 6),
    and the result must load through the real `anchor_from_doc()`."""
    ok = True
    a = emit(generate(args.id, args.act, args.width, args.height, args.lanes,
                      args.sweeps, args.waves, args.budget))
    b = emit(generate(args.id, args.act, args.width, args.height, args.lanes,
                      args.sweeps, args.waves, args.budget))
    if a != b:
        print("FAIL determinism: two generations differ")
        ok = False
    else:
        print(f"ok   determinism: two generations byte-identical ({len(a)} bytes)")
    anchor = anchor_from_doc(json.loads(a))
    if len(anchor.lanes) != args.lanes:
        print(f"FAIL lanes: {len(anchor.lanes)} != {args.lanes}")
        ok = False
    else:
        print(f"ok   loads: {len(anchor.lanes)} lanes, {len(anchor.slots)} slots, "
              f"{len(anchor.waves)} waves")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a large-board multi-lane anchor.")
    ap.add_argument("--id", default="anchor-25", help="anchor id (schema: anchor-NN)")
    ap.add_argument("--act", type=int, default=3, choices=(1, 2, 3))
    ap.add_argument("--width", type=int, default=48)
    ap.add_argument("--height", type=int, default=48)
    ap.add_argument("--lanes", type=int, default=4)
    ap.add_argument("--sweeps", type=int, default=3,
                    help="vertical passes each lane makes inside its band; this, not the "
                         "board size, is what makes a lane long at 48 squared")
    ap.add_argument("--waves", type=int, default=10)
    ap.add_argument("--budget", choices=("derived", "shipped"), default="derived",
                    help="derived: every budget scaled from the generated board. "
                         "shipped: a shipped act anchor's absolute numbers, unchanged — "
                         "the LF-187 control.")
    ap.add_argument("--preview", action="store_true", help="render the board, write nothing")
    ap.add_argument("--out", help="write the anchor document here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest(args)

    doc = generate(args.id, args.act, args.width, args.height, args.lanes,
                   args.sweeps, args.waves, args.budget)
    if args.preview or not args.out:
        preview(doc)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(emit(doc))
        print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
