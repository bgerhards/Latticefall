#!/usr/bin/env python3
"""
Re-author Act II and Act III wave composition: fewer bricks, more escorts (LF-044).

The finale had fewer things on screen than the tutorial — Act I fields 20.2 units a wave,
Act III 7.7 — and the reason was structural rather than timid authoring. From Act II every
unit in the game draws on the bus while it walks, so unit count and bus theft were the same
number: eighteen Hollow Echoes is 144 MW of theft against a bus that has decayed to 157.

`reach-picket` and `hollow-shard` are the cheap thing the roster did not have: 1 and 2 MW,
no armour, no screen, one life if they land.

**Escorts fill an allowance; they are not simply added.** The first cut only added escorts,
holding the authored spawns fixed. That put Act III at 2668 hp a wave against 1562, and all
eight anchors graded unwinnable — a level does not absorb a 70% rise in work because it has
more silhouettes on it. So every wave gets a budget and the escorts spend what is left:

    allowance   what the wave may carry afterwards, in the resource its act is limited by:
                its own mass times BUDGET, capped at SHARE_CEILING times the anchor's mean
    scale       authored spawn counts multiply by f, floor 1 per spawn
    escorts     whatever the allowance has left once the kept authored units are paid for
    f solved    so the resulting unit count lands on the act's target curve

The heavies stay — a Column still walks in the last wave of the game, because the act's
identity travels with its drain carriers, and at these allowances the authored counts are
mostly left alone. Something cheap now walks in front of them.

**Density is not paid for with reactor capacity.** The heavier tables grade clean if the bus
is allowed to grow and `sweep.py` will buy exactly that, which put anchor-24 at 103% of what
would run every slot at maximum draw — every anchor still clean, and the power decision the
game is about no longer present on the five heaviest levels in it. Escort hp and the
allowance are the knobs; capacity is bounded at 70% of board saturation. Decision 048.

**One-shot.** Unlike `sweep.py` this rewrites authored composition rather than a knob, and
running it twice would scale the same tables twice. It refuses to run against anchors that
already carry escorts; `--force` is for deliberately re-deriving from a re-authored base.

    .venv/bin/python tools/densify.py                 # report, change nothing
    .venv/bin/python tools/densify.py --preview       # the wave tables it would write
    .venv/bin/python tools/densify.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import DATA, all_anchor_ids, load_anchor, load_enemies  # noqa: E402

# Escort unit per act, and the mean units/wave the act should end up fielding, from its
# first anchor to its last. Act I is untouched and averages 20.2; Act III finishes above it
# because the last anchor of the game should be the busiest screen in it.
ESCORT = {2: "reach-picket", 3: "hollow-shard"}
ACT_TARGET = {2: (13.0, 19.0), 3: (14.0, 20.0)}
# What the trade holds roughly constant, and how much of it a wave may spend afterwards.
# **The two acts are limited by different resources, and using the wrong one produces a
# level that grades badly in a way the unit count cannot explain.**
#
# Act II is limited by *drain*. It fields less work than Act I — 740 hp a wave against
# 950 — and was thin only because a Sapper costs 8 MW of bus to put on the board. A Picket
# costs 1. So Act II is measured in hp and allowed to grow 50% into headroom it always
# had; its escorts are close to free in the currency that was actually binding.
#
# Act III is limited by *leak*. It already carries the most work in the game, and its units
# are worth 2-4 lives each since decision 047, so buying escorts with hp raises the wave's
# total leak_cost and the anchor needs proportionally more lives to stay clean — measured:
# anchor-20 re-authored against an hp budget wanted 38 lives at 250 MW, a 28% leak budget,
# no tighter than the 26% it started at. Density improved and tension did not, which is the
# exact trade decision 047 exists to prevent. Holding leak instead cuts the bricks, which
# is what LF-044 asked for in the first place.
BUDGET = {2: ("hp", 1.55), 3: ("hp", 1.10)}
# Two anchors get no headroom at all, and it is a property of those levels rather than of
# the rule. anchor-21 and anchor-24 carry the steepest capacity decay in the game — 17 and
# 16 MW a wave — so by their last waves the bus has fallen to its 45% floor while the wave
# is stealing 104 MW of what remains. They were authored within a few percent of the point
# where no build clears them: at the act's 1.10 allowance both went single-solution, and
# anchor-24 wanted 70 lives, a 34% leak budget against the 20% it shipped with. At 1.00 the
# escorts have to be bought out of the heavies instead of added to them, which is the trade
# the rest of the act does not need to make. Decision 048.
BUDGET_BY_ANCHOR = {"anchor-21": 1.00, "anchor-24": 1.00}
# How much steeper than the anchor's mean wave its heaviest wave may be, after the trade.
SHARE_CEILING = 1.6
# Least of an authored spawn that survives the trade. Half, because the wave table is
# content: the counts carry a shape somebody wrote — three Echoes screening one Column
# reads differently from one of each — and a transform that is free to cut to the floor of 1
# turns every Act III wave into the same wave. Where this floor binds, the wave misses the
# unit target instead. That is the intended trade: undershooting density is a tuning miss,
# flattening composition is a loss of authored content.
KEEP_FLOOR = 0.5
# Escorts arrive as a stream, ahead of the heavies they are screening: they are the
# fastest thing in the act, so they lead whether or not the table says so.
ESCORT_INTERVAL = {2: 0.7, 3: 0.8}


def anchor_target(act: int, index: int, count: int) -> float:
    lo, hi = ACT_TARGET[act]
    return hi if count <= 1 else lo + (hi - lo) * index / (count - 1)


def solve_scale(units: float, mass: float, escort_mass: float, target: float,
                allowance: float) -> float:
    """Fraction of the authored wave to keep, so the wave lands on `target` units once its
    `allowance` is spent on escorts. `mass` is whichever resource this act is measured in.

    From `f*units + (allowance - f*mass)/escort_mass = target`, so
    `f = (allowance/escort_mass - target) / (mass/escort_mass - units)`. Written once with
    the denominator as `cheap - mass/escort_mass`, which is a different quantity; the escort
    residual absorbed most of the error, so the tables looked plausible while `f` was not
    the number the function claimed and the target was quietly missed by a fifth.

    **Floored at KEEP_FLOOR, and the floor is the point.** Where the target asks for more
    units than the allowance can buy — which for Act III is most waves, since a leak budget
    of 1.45 cannot purchase 20 units of anything — `f` goes to zero or negative and every
    authored spawn lands on its floor of 1. That is not a thin wave, it is a *destroyed*
    one: 251 of 252 Act III spawn entries came out at count 1, every wave of the last three
    anchors reduced to "N shards and one of each", and the authored ramp gone. Undershooting
    the unit count is a tuning miss; flattening the composition is a loss of the content.
    """
    cheap = allowance / escort_mass          # unit count if the wave were all escorts
    if cheap <= units:                       # already lighter than escorts; leave it alone
        return 1.0
    f = (cheap - target) / (mass / escort_mass - units)
    return max(KEEP_FLOOR, min(1.0, f))


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-author Act II/III wave composition.")
    ap.add_argument("--apply", action="store_true", help="write the anchor files")
    ap.add_argument("--preview", action="store_true",
                    help="print the per-wave table this would produce, and write nothing")
    ap.add_argument("--force", action="store_true",
                    help="re-derive even if escorts are already present")
    ap.add_argument("--act", type=int, choices=sorted(ESCORT),
                    help="only this act; the acts are re-authored against different "
                         "resources and are tuned separately")
    ap.add_argument("--only", action="append", default=[],
                    help="re-derive just these anchors. Their position in the act still "
                         "sets their target, so one anchor can be redone without moving "
                         "the others off the curve.")
    args = ap.parse_args()

    enemies = load_enemies()
    ids_by_act: dict[int, list[str]] = {}
    for aid in all_anchor_ids():
        a = load_anchor(aid)
        if a.act in ESCORT and (args.act is None or a.act == args.act):
            ids_by_act.setdefault(a.act, []).append(aid)

    print(f"{'anchor':>9s} {'escort':>13s} {'units':>11s} {'budget':>15s}  per-wave escorts")
    for act, ids in sorted(ids_by_act.items()):
        escort = ESCORT[act]
        kind, budget = BUDGET[act]
        # What one unit costs in the resource this act is measured in.
        cost = ((lambda e: e.hp) if kind == "hp" else (lambda e: float(e.leak_cost)))
        ecost = cost(enemies[escort])
        for i, aid in enumerate(ids):
            if args.only and aid not in args.only:
                continue
            a = load_anchor(aid)
            has_escort = any(s.enemy == escort for w in a.waves for s in w.spawns)
            if has_escort and not args.force:
                print(f"{aid:>9s}  already carries {escort} — refusing; --force to re-derive")
                return 1
            target = anchor_target(act, i, len(ids))
            budget = BUDGET_BY_ANCHOR.get(aid, BUDGET[act][1])
            # Every measurement below is taken over the *authored* spawns — the escort is
            # excluded even when --force is re-deriving a table that already carries one.
            # Planning over the full list and applying to the filtered one handed each heavy
            # the count of the spawn before it and never updated the last, which is a silent
            # corruption of the wave table rather than a crash.
            authored = [[s for s in w.spawns if s.enemy != escort] for w in a.waves]
            mean_units = sum(sum(s.count for s in sp) for sp in authored) / len(authored)

            # The budget is spent against the anchor's *mean* wave, not against each wave's
            # own mass. Per-wave, the two multipliers compound on the last wave of an act —
            # a finale already carrying twice the anchor's mean got 1.5x of twice, and
            # anchor-16 came out at 3796 hp in one wave against a 1173 hp mean, then only
            # graded clean at 56 lives. Against the mean, the ramp is bounded by
            # SHARE_CEILING x budget however steep the authored table was.
            mean_mass = sum(sum(s.count * cost(enemies[s.enemy]) for s in sp)
                            for sp in authored) / len(authored)

            plan = []
            for spawns in authored:
                units = float(sum(s.count for s in spawns))
                mass = sum(s.count * cost(enemies[s.enemy]) for s in spawns)
                # This wave's share of the anchor's ramp: wave 1 stays lighter than wave 9.
                share = min(SHARE_CEILING, units / mean_units if mean_units else 1.0)
                # The allowance follows this wave's own mass, capped at SHARE_CEILING times
                # the anchor's mean. Deriving it from the *unit* share instead made Act II
                # alternate — a wave carrying two Bulwarks has average unit count and double
                # the hp, so it was handed an allowance below its own mass and came out with
                # almost no escorts, while the wave after it got a pile of them.
                allowance = budget * min(mass, mean_mass * SHARE_CEILING)
                f = solve_scale(units, mass, ecost, target * share, allowance)
                counts = [max(1, round(s.count * f)) for s in spawns]
                kept = sum(c * cost(enemies[s.enemy]) for c, s in zip(counts, spawns))
                n = max(1, round((allowance - kept) / ecost))
                plan.append((counts, n))

            old_mass = sum(sum(s.count * cost(enemies[s.enemy]) for s in sp)
                           for sp in authored) / len(authored)
            new_units = sum(sum(c) + n for c, n in plan) / len(plan)
            new_mass = sum(sum(c * cost(enemies[s.enemy]) for c, s in zip(cs, sp)) + n * ecost
                           for (cs, n), sp in zip(plan, authored)) / len(plan)
            # How much of the authored shape survived. A transform free to cut every spawn to
            # its floor of 1 produces "N escorts and one of each", the same wave on every
            # anchor — so the share of authored spawns left standing at 1 is reported next to
            # the density it bought. 100% here means the wave table stopped being content.
            entries = [c for cs, _ in plan for c in cs]
            flat = sum(1 for c in entries if c == 1) / len(entries)
            print(f"{aid:>9s} {escort:>13s} {mean_units:>5.1f}→{new_units:>5.1f} "
                  f"{kind:>4s} {old_mass:>4.0f}→{new_mass:>4.0f}  "
                  f"flat {flat:>4.0%}  {' '.join(str(n) for _, n in plan)}")

            if args.preview:
                # The table this rule *would* write, without writing or grading it. Every
                # rule in this file was wrong on its first cut, and finding that out cost a
                # full re-sweep each time — thirty minutes to learn that an act finale had
                # come out carrying the fewest escorts in the act, which is visible here in
                # a second. Look at the shape before spending the cores.
                print(f"{'':>9s} {'wave':>6s} {'units':>6s} {'leak':>5s} {'hp':>6s} "
                      f"{'drain':>6s}")
                for wi, ((counts, n), sp) in enumerate(zip(plan, authored), start=1):
                    kept = list(zip(counts, sp))
                    e = enemies[escort]
                    units_i = sum(counts) + n
                    leak_i = sum(c * enemies[s.enemy].leak_cost for c, s in kept) \
                        + n * e.leak_cost
                    hp_i = sum(c * enemies[s.enemy].hp for c, s in kept) + n * e.hp
                    drain_i = sum(c * enemies[s.enemy].drains_mw for c, s in kept) \
                        + n * e.drains_mw
                    print(f"{'':>9s} {wi:>6d} {units_i:>6d} {leak_i:>5.0f} {hp_i:>6.0f} "
                          f"{drain_i:>6.0f}")

            if not args.apply:
                continue
            p = DATA / "anchors" / f"{aid}.json"
            doc = json.loads(p.read_text())
            for w, (counts, n) in zip(doc["waves"], plan):
                spawns = [s for s in w["spawns"] if s["enemy"] != escort]
                for s, c in zip(spawns, counts):
                    s["count"] = c
                spawns.insert(0, {"enemy": escort, "count": n,
                                  "interval": ESCORT_INTERVAL[act]})
                w["spawns"] = spawns
            p.write_text(json.dumps(doc, indent=2) + "\n")

    if args.apply:
        print("\napplied — re-sweep every anchor touched, then run the gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
