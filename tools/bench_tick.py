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
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import Anchor, Enemy, Lane, load_enemies, load_towers  # noqa: E402
from sim.engine import DT, Placed, Sim, Unit, standard_policies  # noqa: E402

# Absurdly high so nothing dies over a bench run of any realistic length — a unit
# leaving the board mid-run would change the live unit count tick to tick, which
# would make ms/tick incomparable across runs of different duration.
BENCH_HP = 1.0e12


def _bench_anchor() -> Anchor:
    """A single long straight lane. Units are motionless, so its exact shape does not
    matter except that it must be long enough that _slot_priority-style geometry
    never comes up — this harness never calls _try_build(), so it does not."""
    return Anchor(
        id="anchor-01", act=1, title="bench rig", capacity_mw=1.0e9,
        starting_funds=0, lives=10 ** 9, grid=(256, 256),
        lanes=(Lane.build("main", ((0.0, 0.0), (1000.0, 0.0))),), slots=(), waves=(),
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
        sim.placed.append(Placed(tower=tw, slot=slot))
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
    args = ap.parse_args()

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
