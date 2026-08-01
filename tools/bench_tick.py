#!/usr/bin/env python3
"""
Microbenchmark the per-tick cost of Sim._tick_once() (sim/engine.py) under board
shapes chosen to isolate specific costs, rather than under a real anchor + wave table.

Written for LF-098 (an idle emplacement re-scanning every unit every tick, forever —
WAR-04) and LF-099 (_covered_by() walking every placed emplacement even when nothing
on the board carries the effect it is looking for — WAR-05). Both are engine-cost
questions, not balance questions, so this constructs a synthetic Sim directly —
Placed and Unit records assigned by hand — rather than running sim.run's grader over
an anchor file. Units are motionless (speed forced to 0.0 on a bespoke Enemy) so a
run is a stable steady state for its whole duration: nothing leaks, nothing dies
outright (hp is set absurdly high), so ms/tick is comparable run to run and does not
drift as the board empties out.

    .venv/bin/python tools/bench_tick.py --units 400 --towers 60
        baseline: towers placed within weapon range of units, a realistic mix of
        weapon and support emplacements — most guns firing on their own cadence.

    .venv/bin/python tools/bench_tick.py --units 400 --towers 60 --all-idle
        WAR-04: every gun placed out of range of every unit, forever. Isolates the
        idle re-scan cost against the baseline above.

    .venv/bin/python tools/bench_tick.py --units 400 --towers 60 --no-support
        WAR-05: only weapon emplacements, none of them carrying `effect` at all —
        _covered_by("slow"/"damp"/"reveal"/"restore", ...) should cost nothing.

    .venv/bin/python tools/bench_tick.py --units 400 --towers 60 --half-dampers
        WAR-05: half the board is anchor-damper (support, `damp` effect) covering
        drain-carrying units, so bus_load()'s per-unit _covered_by("damp", ...) call
        is genuinely exercised, not merely present.

WAR-02/WAR-03 added a second, separate board builder and CLI mode, `--segments`,
rather than bending the LF-098/099 harness above to fit — that harness deliberately
clusters everything in the lane's first 200 tiles (a steady-state rig, not a
realistic board) and this needs the opposite: units and emplacements spread across
the WHOLE lane, and a lane with a *chosen* number of segments, because PRD §2.2's
cells are stated as N units / M emplacements / K segments precisely because segment
count is what `point_at_xy`'s linear scan pays for. Passing `--segments` switches to
this second board and this second reporting path; every flag/behaviour above is
unchanged when it is absent.

    .venv/bin/python tools/bench_tick.py --units 512 --towers 60 --segments 24
        WAR-02/WAR-03 acceptance cell #1 (PRD §2.2). Prints ms/tick.

    .venv/bin/python tools/bench_tick.py --units 1000 --towers 80 --segments 32
        WAR-02/WAR-03 acceptance cell #2 (PRD §2.2).

    .venv/bin/python tools/bench_tick.py --units 512 --towers 60 --segments 24 \\
        --crosscheck
        WAR-03: on that same board, run both the exhaustive scan and the spatial
        index every tick and diff the selected target — see _CrosscheckSim below.

    .venv/bin/python tools/bench_tick.py --crosscheck-anchors
        WAR-03: the same diff, but driving every real anchor through Sim.run() at
        every standard policy and all three difficulties, not just the synthetic
        board — the WAR-03 issue's own acceptance bar.

    .venv/bin/python tools/bench_tick.py --crosscheck-random 200
        WAR-03: the differential test the session brief asked for independently of
        the issue text — N randomised synthetic boards (random unit/tower counts,
        positions, segment counts) plus the one deliberately-adversarial case named
        in the WAR-03 issue itself: several structurally IDENTICAL units (same kind,
        hp, dist, lane) alive on the same tick, in range of the same guns — the
        LF-055 shape an insertion-order-dependent index would diverge on only
        sometimes.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import (Anchor, Enemy, Lane, all_anchor_ids, load_anchor,  # noqa: E402
                         load_enemies, load_towers)
from sim.engine import DIFFICULTIES, DT, Placed, Sim, Unit, standard_policies  # noqa: E402

# Absurdly high so nothing dies over a bench run of any realistic length — a unit
# leaving the board mid-run would change the live unit count tick to tick, which
# would make ms/tick incomparable across runs of different duration.
BENCH_HP = 1.0e12


## Total lane length for every synthetic board this file builds, both the LF-098/099
## harness above and the WAR-02/03 one below — fixed so `--segments 1` (the default)
## reproduces the original single-segment (0,0)-(1000,0) lane byte-for-byte, and every
## existing LF-098/099 invocation (none of which ever passes `--segments`) is
## unaffected by anything below this point.
_BENCH_LANE_LEN = 1000.0


def _bench_anchor(segments: int = 1) -> Anchor:
    """A `segments`-leg lane, zigzagging so every leg stays axis-aligned (decision
    030) while the path as a whole has real segment structure for `point_at_xy`'s
    linear scan to pay for. `segments=1` (every LF-098/099 call site, and this
    function's own old signature) is exactly the original single straight lane —
    units are motionless in every mode this file has, so a lane's exact shape past
    "long enough that _slot_priority-style geometry never comes up" never mattered;
    this harness never calls _try_build(), so it still does not."""
    seg_len = _BENCH_LANE_LEN / segments
    waypoints = [(0.0, 0.0)]
    x, y = 0.0, 0.0
    for i in range(segments):
        if i % 2 == 0:
            x += seg_len
        else:
            y += seg_len
        waypoints.append((x, y))
    return Anchor(
        id="anchor-01", act=1, title="bench rig", capacity_mw=1.0e9,
        starting_funds=0, lives=10 ** 9, grid=(256, 256),
        lanes=(Lane.build("main", tuple(waypoints)),), slots=(), waves=(),
    )


def _still_enemy(drains_mw: float = 0.0) -> Enemy:
    """A ground unit that never dies and never moves, so a bench run is a steady
    state — see the module docstring. `drains_mw` is the only knob that matters for
    --half-dampers; every other stat is inert for this harness's purposes."""
    return Enemy(id="bench-unit", name="Bench Unit", faction="bench", hp=BENCH_HP,
                 speed=0.0, bounty=0, kind="ground", drains_mw=drains_mw)


def _place_units(sim: Sim, n: int, drains_mw: float = 0.0) -> None:
    enemy = _still_enemy(drains_mw)
    # Spread along the first 200 tiles so in-range towers actually have to walk past
    # some out-of-range candidates too — a tower whose range covers the whole lane
    # would make the scan artificially cheap (early-exit shaped) relative to a real
    # board, where most units on a lane are not within any one gun's range.
    for i in range(n):
        sim.units.append(Unit(kind=enemy, hp=enemy.hp, dist=float(i % 200)))


def _place_towers(sim: Sim, towers: dict, n: int, mode: str) -> None:
    weapon = towers["pulse-turret"]
    weapon2 = towers["arc-node"]
    support_cycle = [towers["scan-relay"], towers["shield-wall"],
                      towers["anchor-damper"], towers["restorer"]]
    damper = towers["anchor-damper"]

    for i in range(n):
        if mode == "all-idle":
            # Off the lane entirely (y=500, lane runs y=0) and range is small next to
            # that offset, so no unit is ever a candidate — every scan this tick walks
            # every live unit and finds nothing, exactly LF-098's board shape.
            tw = weapon if i % 2 == 0 else weapon2
            slot = (i * 10, 500)
        elif mode == "no-support":
            # In range, weapon-only: every emplacement fires on its own cadence, and
            # none of them ever carries an `effect` at all.
            tw = weapon if i % 2 == 0 else weapon2
            slot = (i % 200, 1)
        elif mode == "half-dampers":
            # Half anchor-damper (in range, covering the drain-carrying units placed
            # by _place_units), half pulse-turret (also in range, so it still fires).
            tw = damper if i % 2 == 0 else weapon
            slot = (i % 200, 1)
        else:  # baseline: a realistic mix, all in range
            tw = weapon if i % 3 != 0 else support_cycle[i % len(support_cycle)]
            slot = (i % 200, 1)
        sim.placed.append(Placed(tower=tw, x=slot[0], y=slot[1]))  # PLC-01: x/y, not slot
    # Guarded: this script is also run against a pre-LF-099 engine.py copy (from
    # tools/reap.py-adjacent scratch comparisons) that has no rebuild method at all —
    # such an engine filters inline on every _covered_by() call instead, and needs no
    # help from this harness to see towers placed above.
    if hasattr(sim, "_rebuild_effect_lists"):
        sim._rebuild_effect_lists()


def build_sim(towers: dict, n_towers: int, n_units: int, mode: str) -> Sim:
    anchor = _bench_anchor()
    policy = standard_policies(list(towers))[0]
    sim = Sim(anchor, towers, {}, policy, "standard")
    drains = 8.0 if mode == "half-dampers" else 0.0
    _place_units(sim, n_units, drains_mw=drains)
    _place_towers(sim, towers, n_towers, mode)
    return sim


def run_bench(towers: dict, n_towers: int, n_units: int, mode: str, ticks: int) -> float:
    """Returns ms/tick, averaged over `ticks` calls to _tick_once()."""
    sim = build_sim(towers, n_towers, n_units, mode)
    # Warm up: a handful of ticks so every emplacement has gone through its first
    # cooldown check at least once (including, for --all-idle, setting next_scan_t)
    # before the clock starts, so the timed region is the steady state, not the
    # one-time cold-start branch.
    for _ in range(4):
        sim._tick_once()
    start = time.perf_counter()
    for _ in range(ticks):
        sim._tick_once()
    elapsed = time.perf_counter() - start
    return elapsed / ticks * 1000.0


def count_effect_iterations(towers: dict, n_towers: int, n_units: int, mode: str) -> int:
    """Sum of len() over the four pre-filtered effect lists after a tick — the number
    of per-emplacement iterations _covered_by() would do for a *single* effect lookup
    against this board. Zero for --no-support proves the WAR-05 acceptance criterion
    structurally: _covered_by() returns 0.0 immediately when its list is empty, so a
    list of length zero is exactly zero iterations, not merely a cheap one."""
    sim = build_sim(towers, n_towers, n_units, mode)
    sim._tick_once()
    return (len(sim._eff_slow) + len(sim._eff_damp)
            + len(sim._eff_reveal) + len(sim._eff_restore))


# ───────────────────────────────────────────────── WAR-02 / WAR-03: the war board ──
#
# A second board shape, separate from the LF-098/099 rig above: units and
# emplacements spread across the WHOLE lane (not clustered in its first 200 tiles),
# on a lane built with a *chosen* number of segments — PRD §2.2's cells are stated as
# N units / M emplacements / K segments precisely because segment count is what
# point_at_xy's linear scan pays for, and a board where every unit resolves inside
# the lane's first leg would make that scan artificially cheap.
#
# Real towers, cycled through a mixed roster of weapons (never support — a support
# emplacement never enters _step()'s fire loop at all, `tw["damage"] <= 0.0` in
# GDScript / `p.tower.is_weapon` in Python), each placed along the lane at the same
# spacing as the units — a tower's range (2.6-6.5 tiles today) then covers a real,
# bounded neighbourhood of units around its own position: not every unit on the
# board (which would make WAR-03's index no cheaper than the exhaustive scan it
# replaces) and not zero of them (LF-098's already-covered idle shape).

_WAR_ROSTER_IDS = ("pulse-turret", "arc-node", "ion-lance", "mortar-emplacement",
                   "flak-array")


def _build_war_sim(towers: dict, n_towers: int, n_units: int, segments: int,
                    sim_cls: type = Sim):
    """One (anchor, units, emplacements) board for the WAR-02/03 acceptance cells.
    `sim_cls` defaults to the real `Sim`; pass `_CrosscheckSim` to get the same board
    wired for target-selection diffing instead of timing."""
    anchor = _bench_anchor(segments)
    plen = anchor.lanes[0].path_length
    policy = standard_policies(list(towers))[0]
    sim = sim_cls(anchor, towers, {}, policy, "standard")
    enemy = _still_enemy()
    for i in range(n_units):
        sim.units.append(Unit(kind=enemy, hp=enemy.hp, dist=plen * i / max(1, n_units)))
    roster = [towers[t] for t in _WAR_ROSTER_IDS if t in towers] or list(towers.values())
    for i in range(n_towers):
        tw = roster[i % len(roster)]
        x = plen * i / max(1, n_towers)
        sim.placed.append(Placed(tower=tw, x=x, y=2.0))  # PLC-01: x/y, not slot
    if hasattr(sim, "_rebuild_effect_lists"):
        sim._rebuild_effect_lists()
    return sim


def run_war_bench(towers: dict, n_towers: int, n_units: int, segments: int,
                  ticks: int) -> float:
    """Returns ms/tick on the WAR board, averaged over `ticks` calls to
    _tick_once() — same warm-up rationale as run_bench() above."""
    sim = _build_war_sim(towers, n_towers, n_units, segments)
    for _ in range(4):
        sim._tick_once()
    start = time.perf_counter()
    for _ in range(ticks):
        sim._tick_once()
    elapsed = time.perf_counter() - start
    return elapsed / ticks * 1000.0


def _count_point_at_one_tick(towers: dict, n_towers: int, n_units: int, segments: int) -> int:
    """point_at calls in one steady-state tick of the WAR board, by wrapping
    `Anchor.point_at` with a counter — not by reading the code. One warm-up tick
    first so every emplacement is past its first-tick cooldown edge case; the
    counter is zeroed after that, so the return value is exactly one tick's cost."""
    sim = _build_war_sim(towers, n_towers, n_units, segments)
    calls = {"n": 0}
    real_point_at = sim.a.point_at

    def counting_point_at(lane: int, dist: float):
        calls["n"] += 1
        return real_point_at(lane, dist)

    sim.a.point_at = counting_point_at  # type: ignore[method-assign]
    sim._tick_once()
    calls["n"] = 0
    sim._tick_once()
    sim.a.point_at = real_point_at  # type: ignore[method-assign]
    return calls["n"]


def assert_point_at_budget(towers: dict, n_units: int, segments: int) -> tuple[int, int]:
    """WAR-02's acceptance criterion, proved by counting rather than by reading the
    code: "point_at is called at most units.size() times per tick IN THE FIRE
    PHASE". Movement calls it once per live unit regardless — that cost is real and
    is not what WAR-02 removes — so the criterion is proved by INVARIANCE, not by an
    absolute count: run the identical board (same units, same segments) at two very
    different tower counts and diff the per-tick call total. Before the hoist, the
    fire phase alone was ~O(towers x units), so the total scales hard with tower
    count; after it, movement (O(units)) plus one shared position pass (O(units)) is
    the whole cost and does not depend on how many emplacements are on the board at
    all — the two counts below come back EXACTLY equal, not merely close.

    Returns (calls at 10 towers, calls at 100 towers); the caller compares them."""
    return (_count_point_at_one_tick(towers, 10, n_units, segments),
            _count_point_at_one_tick(towers, 100, n_units, segments))


class _CrosscheckSim(Sim):
    """WAR-03's differential test. Wraps `_select_target()` — mirrors
    sim/coverage.py's `InstrumentedSim` pattern (wrap an existing method, call
    super() for the real answer, never reimplement the rule) rather than
    reimplementing `_step()`'s fire loop a second time, which would just be a new
    place for the two copies to drift.

    `_step()` calls `self._select_target(p, pos, grid, rng, mode)` exactly as `Sim`
    does; this override answers that call for real (the `super()` result, returned
    unchanged, so the actual game state this Sim produces is untouched) and ALSO
    calls `super()._select_target(...)` a second time with `grid` forced to `None` —
    the exhaustive path — purely to diff the two `target_i` values. When
    `USE_SPATIAL_INDEX` is True (the shipped default), the real call already runs
    indexed, so this comparison is indexed-vs-exhaustive on every live decision the
    Sim makes, not a synthetic one bolted on afterward."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.crosscheck_mismatches: list[tuple] = []

    def _select_target(self, p, pos, grid, rng, mode):
        real_target, real_i = super()._select_target(p, pos, grid, rng, mode)
        if grid is not None:
            _, linear_i = super()._select_target(p, pos, None, rng, mode)
            if linear_i != real_i:
                self.crosscheck_mismatches.append(
                    (self.a.id, self.difficulty, self.policy.name, p.slot,
                     round(self.t, 4), real_i, linear_i))
        return real_target, real_i


def crosscheck_all_anchors() -> list[tuple]:
    """WAR-03's own acceptance bar: every real anchor, every standard policy, all
    three difficulties, driven through the unmodified `Sim.run()` — build phase,
    wave spawns, sell/upgrade/ability schedules, all of it — diffing indexed against
    exhaustive target selection on every tick any emplacement fires. A superset of
    the issue's literal "24 anchors x 3 difficulties": every standard policy, not
    just one, because a policy that never triggers a particular targeting mode would
    make that mode's ordering path untested."""
    towers = load_towers()
    enemies = load_enemies()
    mismatches: list[tuple] = []
    for aid in all_anchor_ids():
        anchor = load_anchor(aid)
        available = sorted(t.id for t in towers.values() if t.unlocked_at <= aid)
        for pol in standard_policies(available):
            for diff in DIFFICULTIES:
                sim = _CrosscheckSim(anchor, towers, enemies, pol, diff)
                sim.run()
                mismatches.extend(sim.crosscheck_mismatches)
    return mismatches


def _random_war_sim(towers: dict, rng: random.Random, sim_cls: type = Sim):
    """One random synthetic board — random segment count, random unit/tower counts,
    random positions — still built on _bench_anchor()'s zigzag lane so point_at_xy
    has real segment structure to resolve. RNG chooses WHICH board to throw at both
    algorithms, the same board for both; it is not a rules input and nothing here
    touches sim/engine.py or scripts/anchor_sim.gd, whose own "no RNG in the core
    loop" contract this does not exercise or need to."""
    segments = rng.randint(1, 12)
    n_units = rng.randint(1, 120)
    n_towers = rng.randint(1, 40)
    anchor = _bench_anchor(segments)
    plen = anchor.lanes[0].path_length
    policy = standard_policies(list(towers))[0]
    sim = sim_cls(anchor, towers, {}, policy, "standard")
    enemy = _still_enemy()
    for _ in range(n_units):
        sim.units.append(Unit(kind=enemy, hp=enemy.hp, dist=rng.uniform(0.0, plen)))
    roster = list(towers.values())
    for _ in range(n_towers):
        tw = rng.choice(roster)
        slot = (rng.uniform(0.0, plen), rng.uniform(-5.0, 5.0))
        sim.placed.append(Placed(tower=tw, x=slot[0], y=slot[1]))  # PLC-01: x/y, not slot
    if hasattr(sim, "_rebuild_effect_lists"):
        sim._rebuild_effect_lists()
    return sim


def crosscheck_random_boards(n_boards: int = 200, ticks_each: int = 5,
                             seed: int = 1234) -> tuple[list[tuple], int]:
    """The differential test asked for independently of the WAR-03 issue text: N
    randomised board states, asserting indexed and exhaustive selection agree on
    every one — not merely that a grade matches, which can hide a selection
    difference that only bites later. Deterministic (fixed seed): a bug this finds
    must reproduce on the next run, or it is not a repeatable proof of anything."""
    towers = load_towers()
    rng = random.Random(seed)
    mismatches: list[tuple] = []
    ticks_run = 0
    for _ in range(n_boards):
        sim = _random_war_sim(towers, rng, sim_cls=_CrosscheckSim)
        for _ in range(ticks_each):
            sim._tick_once()
            ticks_run += 1
        mismatches.extend(sim.crosscheck_mismatches)
    return mismatches, ticks_run


def crosscheck_identical_units(ticks: int = 30) -> list[tuple]:
    """The specific case the WAR-03 issue names: several structurally IDENTICAL
    units (same kind, hp, dist, lane) alive on the same tick, in range of the same
    guns — the shape LF-055 says an insertion-order-dependent index diverges on only
    SOMETIMES, so a random board sweep could pass 1000 boards and still miss it.
    Built in memory (no anchor file, no `data/` change) the same way this file's own
    `--all-idle` etc. rigs already bypass wave spawning — `sim.units.append()`
    directly, several identical records at once."""
    towers = load_towers()
    anchor = _bench_anchor(segments=4)
    policy = standard_policies(list(towers))[0]
    sim = _CrosscheckSim(anchor, towers, {}, policy, "standard")
    enemy = _still_enemy()
    for _ in range(6):
        sim.units.append(Unit(kind=enemy, hp=enemy.hp, dist=10.0))
    for i in range(8):
        sim.placed.append(Placed(tower=towers["pulse-turret"], x=10.0 + i, y=1.0))  # PLC-01
    if hasattr(sim, "_rebuild_effect_lists"):
        sim._rebuild_effect_lists()
    for _ in range(ticks):
        sim._tick_once()
    return sim.crosscheck_mismatches


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--units", type=int, default=400)
    ap.add_argument("--towers", type=int, default=60)
    ap.add_argument("--ticks", type=int, default=900, help="sim ticks timed (30/sec)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--all-idle", action="store_const", dest="mode",
                       const="all-idle", help="LF-098: nothing ever in range")
    mode.add_argument("--no-support", action="store_const", dest="mode",
                       const="no-support", help="LF-099: no emplacement carries an effect")
    mode.add_argument("--half-dampers", action="store_const", dest="mode",
                       const="half-dampers", help="LF-099: half the board is anchor-damper")
    ap.set_defaults(mode="baseline")
    # ── WAR-02 / WAR-03 ──────────────────────────────────────────────────────────
    ap.add_argument("--segments", type=int,
                    help="WAR-02/03 acceptance cell: lane segment count. Switches to "
                         "the spread-across-the-whole-lane board; every flag above is "
                         "ignored when this is set.")
    ap.add_argument("--crosscheck", action="store_true",
                    help="WAR-03: with --segments, diff indexed vs exhaustive target "
                         "selection on that board instead of timing it")
    ap.add_argument("--crosscheck-anchors", action="store_true",
                    help="WAR-03: diff indexed vs exhaustive selection across every "
                         "real anchor x policy x difficulty")
    ap.add_argument("--crosscheck-random", type=int, metavar="N",
                    help="WAR-03: diff indexed vs exhaustive selection across N random "
                         "synthetic boards, plus the identical-units case")
    args = ap.parse_args()

    if args.crosscheck_anchors:
        mismatches = crosscheck_all_anchors()
        n_anchors = len(all_anchor_ids())
        print(f"crosscheck-anchors: {len(mismatches)} differing selections across "
              f"{n_anchors} anchors x 3 difficulties x every standard policy")
        for m in mismatches[:10]:
            print(f"  mismatch: anchor={m[0]} diff={m[1]} policy={m[2]} slot={m[3]} "
                  f"t={m[4]} indexed={m[5]} exhaustive={m[6]}")
        return 1 if mismatches else 0

    if args.crosscheck_random is not None:
        mismatches, ticks_run = crosscheck_random_boards(n_boards=args.crosscheck_random)
        id_mismatches = crosscheck_identical_units()
        total = len(mismatches) + len(id_mismatches)
        print(f"crosscheck-random: {len(mismatches)} differing selections over "
              f"{args.crosscheck_random} random boards ({ticks_run} ticks) + "
              f"{len(id_mismatches)} on the identical-units case = {total} total")
        for m in (mismatches + id_mismatches)[:10]:
            print(f"  mismatch: anchor={m[0]} diff={m[1]} policy={m[2]} slot={m[3]} "
                  f"t={m[4]} indexed={m[5]} exhaustive={m[6]}")
        return 1 if total else 0

    if args.segments is not None:
        towers = load_towers()
        if args.crosscheck:
            sim = _build_war_sim(towers, args.towers, args.units, args.segments,
                                 sim_cls=_CrosscheckSim)
            for _ in range(4):
                sim._tick_once()
            for _ in range(args.ticks):
                sim._tick_once()
            n = len(sim.crosscheck_mismatches)
            print(f"units={args.units:<5d} towers={args.towers:<4d} "
                  f"segments={args.segments:<3d} ticks={args.ticks:<5d}  "
                  f"{n} differing selections")
            for m in sim.crosscheck_mismatches[:10]:
                print(f"  mismatch: t={m[4]} slot={m[3]} indexed={m[5]} exhaustive={m[6]}")
            return 1 if n else 0

        ms = run_war_bench(towers, args.towers, args.units, args.segments, args.ticks)
        print(f"units={args.units:<5d} towers={args.towers:<4d} "
              f"segments={args.segments:<3d} ticks={args.ticks:<5d} {ms:8.4f} ms/tick")

        # WAR-02's acceptance criterion, always checked (not gated on WAR-03 having
        # landed yet — this is purely about the position hoist): fire-phase point_at
        # cost must not scale with tower count.
        c10, c100 = assert_point_at_budget(towers, args.units, args.segments)
        print(f"point_at budget: {c10} calls/tick @ 10 towers, {c100} calls/tick "
              f"@ 100 towers (must be equal — fire-phase cost must not scale with "
              f"tower count)")
        if c10 != c100:
            return 1
        return 0

    # ── LF-098 / LF-099 (unchanged) ─────────────────────────────────────────────
    towers = load_towers()

    ms = run_bench(towers, args.towers, args.units, args.mode, args.ticks)
    baseline_ms = ms if args.mode == "baseline" else run_bench(
        towers, args.towers, args.units, "baseline", args.ticks)

    print(f"mode={args.mode:<13s} towers={args.towers:<4d} units={args.units:<5d} "
          f"ticks={args.ticks:<5d} {ms:7.4f} ms/tick   "
          f"{ms / baseline_ms:5.2f}x vs baseline ({baseline_ms:.4f} ms/tick)")

    if args.mode == "no-support" and hasattr(Sim, "_rebuild_effect_lists"):
        n = count_effect_iterations(towers, args.towers, args.units, args.mode)
        print(f"no-support: per-emplacement iterations available to _covered_by() "
              f"after a tick = {n} (must be 0)")
        if n != 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
