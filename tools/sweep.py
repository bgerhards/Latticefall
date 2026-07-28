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


## Robustness is a threshold, not a quantity to maximise.
##
## The original score was `standard_wins + brutal_wins`, chosen so a level would not grade
## clean on a knife edge and then break on the next tower stat change. That intent is right
## and the implementation paid for it with lives: more lives means more boards survive,
## which means more distinct winning builds, which means the loosest cell always scored
## highest. Sixteen anchors were tuned that way and it shows — Act I runs 20 units a wave on
## 10 lives, Act III runs 8 on 32. The finale had fewer things on screen than the tutorial.
##
## So cap the benefit of extra builds at ROBUST_ENOUGH, and let density and tightness decide
## between cells that are all comfortably robust. Beyond the cap, another winning build buys
## nothing and a wave with more units in it buys everything.
ROBUST_ENOUGH = 8


def cell_score(result: dict, weight: float, lives: int) -> float:
    wins = (result["by_difficulty"]["standard"]["distinct_winning_builds"]
            + result["by_difficulty"]["brutal"]["distinct_winning_builds"])
    robust = min(wins, ROBUST_ENOUGH) * 10.0        # dominates while still below the cap
    # Lives are weighted above density on purpose: a player feels "a leak barely matters"
    # long before they feel "this wave is thin". Anchor-24 shipped 44 lives.
    return robust + weight * 6.0 - float(lives) * 0.8


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
                        score = cell_score(r, wt, lv)
                        clean.append((score, cap, int(fu), wt, lv, r))
                        print(line + "ok")
                    elif not args.quiet:
                        print(line + r["problems"][0][:60])

    print(f"\n{len(clean)} clean cell(s) of {total}")

    # State the box, always. "No cell grades clean" is a statement about the grid that was
    # searched and nothing else, and it has already been read once as a statement about the
    # game: LF-044 recorded "higher spawn weights do not grade clean at any life count" as a
    # property of the roster, when the sweep behind it spanned weights 0.85-1.15 and a single
    # life count. anchor-20 grades clean at weight 1.50 and 72 lives. Printing the bounds
    # next to the verdict makes the scope of the claim impossible to lose.
    print(f"searched: capacity {min(caps):.0f}-{max(caps):.0f} MW · "
          f"funds {int(min(funds))}-{int(max(funds))} · "
          f"weight {min(weights):.2f}-{max(weights):.2f} · "
          f"lives {min(lives)}-{max(lives)}")
    if not clean:
        print("a verdict of 'none' applies to that box only — widen --weight and --lives "
              "before concluding anything about the content")
        return 1

    # Ties broken toward the tighter level, then the denser one, then the cheaper bus —
    # never by list order, which is what "best" used to mean when two cells scored equal.
    clean.sort(key=lambda c: (-c[0], c[4], -c[3], c[1]))
    score, cap, fu, wt, lv, _ = clean[0]
    print(f"best: capacity {cap:.0f} MW · funds {fu} · weight {wt:.2f} · "
          f"lives {lv} (score {score:.1f})")

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
