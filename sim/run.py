#!/usr/bin/env python3
"""
Grade Latticefall anchors headlessly.

    .venv/bin/python sim/run.py                              # every anchor, every difficulty
    .venv/bin/python sim/run.py --anchor anchor-01           # one anchor
    .venv/bin/python sim/run.py --anchor anchor-01 --json    # machine-readable
    .venv/bin/python sim/run.py --anchor anchor-01 --detail  # per-policy breakdown

The verdict is not just win/loss. An anchor passes only if it is winnable by more
than one approach and the player is actually pressed against capacity at some point.
A level nobody can lose and a level with exactly one answer are both failures, and
neither shows up in a pass/fail number.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.content import all_anchor_ids, load_anchor, load_enemies, load_towers  # noqa: E402
from sim.engine import DIFFICULTIES, Sim, standard_policies  # noqa: E402

# An anchor whose peak load never gets near capacity is not using the game's hook.
PRESSURE_FLOOR = 0.75


def grade(anchor_id: str, difficulties: list[str]) -> dict:
    towers = load_towers()
    enemies = load_enemies()
    anchor = load_anchor(anchor_id)
    available = [t.id for t in towers.values() if t.unlocked_at <= anchor.id]
    policies = standard_policies(available)

    runs, by_diff = [], {}
    for diff in difficulties:
        outcomes = [Sim(anchor, towers, enemies, p, diff).run() for p in policies]
        runs.extend(outcomes)
        winners = [o for o in outcomes if o.won]
        # Policies that converge on the same board are one build, not several.
        distinct_wins = {tuple(sorted(o.built)) for o in winners}
        distinct_all = {tuple(sorted(o.built)) for o in outcomes}
        by_diff[diff] = {
            "winning_policies": [o.policy for o in winners],
            "win_count": len(winners),
            "policy_count": len(outcomes),
            "distinct_winning_builds": len(distinct_wins),
            "distinct_builds_tried": len(distinct_all),
            "peak_load_mw": round(max(o.peak_load_mw for o in outcomes), 2),
            "peak_load_ratio": round(
                max(o.peak_load_mw for o in outcomes) / anchor.capacity_mw, 3),
            "earliest_death_wave": min(
                (o.died_on_wave for o in outcomes if o.died_on_wave), default=None),
            "brownout_fraction": round(
                max(o.brownout_fraction for o in outcomes), 3),
        }

    problems = []
    for diff, d in by_diff.items():
        if d["win_count"] == 0:
            problems.append(f"{diff}: unwinnable — no policy clears it")
        elif d["distinct_winning_builds"] == 1 and d["distinct_builds_tried"] > 1:
            problems.append(
                f"{diff}: only one distinct build clears it — single-solution level")
        if (d["distinct_builds_tried"] > 1
                and d["distinct_winning_builds"] == d["distinct_builds_tried"]
                and diff != "standard"):
            problems.append(f"{diff}: every distinct build clears it — difficulty is not biting")
        if d["peak_load_ratio"] < PRESSURE_FLOOR:
            problems.append(
                f"{diff}: peak load only {d['peak_load_ratio']:.0%} of capacity — "
                f"no power decision is forced")

    # A level with one emplacement unlocked has exactly one build by construction.
    # That is correct for a tutorial, so the anchor declares it rather than the
    # grader guessing.
    # A tutorial has one emplacement unlocked, so it has one build by construction and
    # nothing for a difficulty tier to differentiate. Both checks are relaxed, and the
    # anchor declares this in data rather than the grader inferring it.
    if anchor.tutorial:
        problems = [p for p in problems
                    if "single-solution" not in p and "not biting" not in p]

    return {
        "anchor": anchor.id,
        "tutorial": anchor.tutorial,
        "title": anchor.title,
        "act": anchor.act,
        "capacity_mw": anchor.capacity_mw,
        "waves": len(anchor.waves),
        "slots": len(anchor.slots),
        "unlocked": available,
        "by_difficulty": by_diff,
        "problems": problems,
        "ok": not problems,
        "runs": [o.as_dict() for o in runs],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade Latticefall anchors headlessly.")
    ap.add_argument("--anchor", help="anchor id (default: all)")
    ap.add_argument("--difficulty", choices=list(DIFFICULTIES), action="append",
                    help="repeatable. default: all three")
    ap.add_argument("--seed", type=int, default=0,
                    help="accepted for interface stability; the sim has no RNG")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--detail", action="store_true", help="per-policy breakdown")
    args = ap.parse_args()

    ids = [args.anchor] if args.anchor else all_anchor_ids()
    diffs = args.difficulty or list(DIFFICULTIES)
    reports = [grade(i, diffs) for i in ids]

    if args.json:
        slim = [{k: v for k, v in r.items() if k != "runs" or args.detail}
                for r in reports]
        print(json.dumps(slim, indent=2, sort_keys=True))
        return 0 if all(r["ok"] for r in reports) else 1

    for r in reports:
        head = f"{r['anchor']}  {r['title']}  ·  act {r['act']}  ·  {r['capacity_mw']:.0f} MW  ·  {r['waves']} waves"
        print(f"\n{head}\n{'─' * len(head)}")
        print(f"{'difficulty':<11s} {'builds':>8s} {'peak':>12s} {'brownout':>9s}  died on")
        for diff in diffs:
            d = r["by_difficulty"][diff]
            died = f"wave {d['earliest_death_wave']}" if d["earliest_death_wave"] else "—"
            print(f"{diff:<11s} {d['distinct_winning_builds']:>2d} of {d['distinct_builds_tried']:<3d} "
                  f"{d['peak_load_mw']:>7.1f} MW {d['peak_load_ratio']:>4.0%} "
                  f"{d['brownout_fraction']:>8.0%}  {died}")
            if d["winning_policies"]:
                print(f"{'':11s} {', '.join(d['winning_policies'])}")

        if args.detail:
            print()
            for o in r["runs"]:
                print(f"  {o['difficulty']:<9s} {o['policy']:<17s} "
                      f"{'WON ' if o['won'] else 'lost'} "
                      f"w{o['waves_cleared']}/{o['waves_total']} "
                      f"lives {o['lives_left']:>2d}  peak {o['peak_load_mw']:>6.1f} MW  "
                      f"spend {o['spend']:>4d}  {len(o['built'])} built")

        if r["problems"]:
            print()
            for p in r["problems"]:
                print(f"  PROBLEM  {p}")
        else:
            print("\n  ok")

    bad = [r["anchor"] for r in reports if not r["ok"]]
    print(f"\n{len(reports) - len(bad)}/{len(reports)} anchors clean")
    if bad:
        print(f"problems: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
