#!/usr/bin/env python3
"""
Sweep an anchor's tuning knobs against the grader.

Every anchor from 02 on failed its first cut, and decision 026 records why intuition
does not work here: the direction of the fix is usually wrong, not just the magnitude.
Raising capacity can make a level *harder* to grade clean, because it removes the power
decision the grader is looking for. So the method is to grade a grid rather than guess.

This exists because that grid was being rebuilt by hand as a throwaway script for every
anchor. It varies the three knobs that do not change the level's identity:

    capacity_mw       the power tier
    starting_funds    how much board the player gets before wave 1
    wave weight       a multiplier on every spawn count, rounded, floor 1

Layout is deliberately *not* swept — a level whose layout is wrong cannot be rescued by
numbers (anchor-06, decision 024), and the validator now catches the worst layout faults.

    .venv/bin/python tools/sweep.py anchor-09
    .venv/bin/python tools/sweep.py anchor-09 --cap 130,140,150 --funds 900,1050 --weight 0.9,1.0
    .venv/bin/python tools/sweep.py anchor-09 --apply          # write the best cell back
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import DATA, Spawn, Wave, load_anchor, load_enemies, load_towers  # noqa: E402
from sim.engine import DIFFICULTIES  # noqa: E402
from sim.run import grade_anchor  # noqa: E402


def scaled_waves(waves: tuple[Wave, ...], weight: float) -> tuple[Wave, ...]:
    """Scale every spawn count. Floor of 1 — a wave that scales to zero units is a
    different level, not a lighter one."""
    out = []
    for w in waves:
        out.append(Wave(
            lead_in=w.lead_in,
            spawns=tuple(replace(s, count=max(1, round(s.count * weight)))
                         for s in w.spawns),
        ))
    return tuple(out)


def parse_list(raw: str | None, default: list[float]) -> list[float]:
    if not raw:
        return default
    return [float(x) for x in raw.split(",")]


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep an anchor's knobs against the grader.")
    ap.add_argument("anchor")
    ap.add_argument("--cap", help="comma-separated capacities in MW")
    ap.add_argument("--funds", help="comma-separated starting funds")
    ap.add_argument("--weight", help="comma-separated spawn-count multipliers")
    ap.add_argument("--lives", help="comma-separated life counts")
    ap.add_argument("--apply", action="store_true",
                    help="write the best clean cell back into the anchor file")
    ap.add_argument("--quiet", action="store_true", help="only print clean cells")
    args = ap.parse_args()

    base = load_anchor(args.anchor)
    towers, enemies = load_towers(), load_enemies()
    diffs = list(DIFFICULTIES)

    caps = parse_list(args.cap, [base.capacity_mw * m for m in (0.9, 1.0, 1.1)])
    funds = parse_list(args.funds, [base.starting_funds * m for m in (0.85, 1.0, 1.15)])
    weights = parse_list(args.weight, [0.85, 1.0, 1.15])
    lives = [int(x) for x in parse_list(args.lives, [float(base.lives)])]
    total = len(caps) * len(funds) * len(weights) * len(lives)

    print(f"{args.anchor}  {base.title}  ·  sweeping "
          f"{len(caps)}x{len(funds)}x{len(weights)}x{len(lives)} = {total} cells\n")
    print(f"{'cap':>6s} {'funds':>7s} {'wt':>5s} {'liv':>4s}  "
          f"{'std':>7s} {'hard':>7s} {'brutal':>7s} {'peak':>6s}  verdict")

    clean: list[tuple] = []
    for cap in caps:
        for fu in funds:
            for wt in weights:
                for lv in lives:
                    cand = replace(base, capacity_mw=cap, starting_funds=int(fu),
                                   lives=lv, waves=scaled_waves(base.waves, wt))
                    r = grade_anchor(cand, diffs, towers, enemies)
                    cells = [r["by_difficulty"][d] for d in diffs]
                    peak = max(c["peak_load_ratio"] for c in cells)
                    line = (f"{cap:>6.0f} {int(fu):>7d} {wt:>5.2f} {lv:>4d}  "
                            + " ".join(f"{c['distinct_winning_builds']:>2d}/"
                                       f"{c['distinct_builds_tried']:<4d}" for c in cells)
                            + f" {peak:>5.0%}  ")
                    if r["ok"]:
                        # Prefer the cell winnable by the most builds at standard while
                        # still biting at brutal — a level that grades clean on a knife
                        # edge will not survive the next tower stat change.
                        score = (r["by_difficulty"]["standard"]["distinct_winning_builds"]
                                 + r["by_difficulty"]["brutal"]["distinct_winning_builds"])
                        clean.append((score, cap, int(fu), wt, lv, r))
                        print(line + "ok")
                    elif not args.quiet:
                        print(line + r["problems"][0][:60])

    print(f"\n{len(clean)} clean cell(s) of {total}")
    if not clean:
        return 1

    clean.sort(key=lambda c: (-c[0], c[1]))
    score, cap, fu, wt, lv, _ = clean[0]
    print(f"best: capacity {cap:.0f} MW · funds {fu} · weight {wt:.2f} · "
          f"lives {lv} (score {score})")

    if args.apply:
        p = DATA / "anchors" / f"{args.anchor}.json"
        doc = json.loads(p.read_text())
        doc["capacity_mw"] = int(cap) if float(cap).is_integer() else cap
        doc["starting_funds"] = fu
        doc["lives"] = lv
        for w in doc["waves"]:
            for s in w["spawns"]:
                s["count"] = max(1, round(s["count"] * wt))
        p.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"applied to {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
