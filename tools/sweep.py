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
    .venv/bin/python tools/sweep.py anchor-09 --jobs 8         # grade cells in parallel
    .venv/bin/python tools/sweep.py --act 3 --jobs 8 \\
        --cap x0.92,x1.0,x1.12 --lives %9,%12,%16              # a whole act, one box

A grid is a few dozen independent grades of an anchor that only exists in memory, so
--jobs is pure wall-clock: same cells, same order, same verdict. It was worth adding the
first time a density question needed sixteen anchors re-swept and the box had to widen
twice before it answered.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import (DATA, Spawn, Wave, all_anchor_ids, load_anchor,  # noqa: E402
                         load_enemies, load_towers)
from sim.engine import DIFFICULTIES  # noqa: E402
from sim.run import grade_anchor  # noqa: E402
import lease  # noqa: E402  — scopes the --jobs pool for tools/reap.py (PRC-07); this file
              # already lives in tools/, so it is importable with no extra sys.path setup

## Generous: a wide box (a whole act, cap x funds x weight x lives) legitimately runs long
## — "a balance question usually needs the box widened two or three times before it
## answers" per this file's own docstring. The TTL is a crash backstop, not a budget.
SWEEP_LEASE_TTL_S = 3600.0


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


def parse_list(raw: str | None, default: list[float], base: float = 0.0,
               leak: float = 0.0) -> list[float]:
    """Absolute values, or relative ones so a grid can be written once for many anchors.

        220,246,275     absolute
        x0.9,x1.0,x1.12 multiples of the anchor's current value
        %9,%12,%16      percent of the anchor's total leak_cost — lives only, and the only
                        honest way to write a life count since decision 047, because 24
                        lives means something different on every anchor
    """
    if not raw:
        return default
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.startswith("x"):
            out.append(base * float(tok[1:]))
        elif tok.startswith("%"):
            out.append(leak * float(tok[1:]) / 100.0)
        else:
            out.append(float(tok))
    return out


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


_CONTENT: tuple = ()


def _init_worker() -> None:
    global _CONTENT
    _CONTENT = (load_towers(), load_enemies())


def _grade_cell(job: tuple) -> tuple:
    """Grade one cell. Returns the cell's coordinates alongside the report so results can
    be printed in grid order regardless of which worker finished first."""
    base, cap, fu, wt, lv, diffs = job
    towers, enemies = _CONTENT if _CONTENT else (load_towers(), load_enemies())
    cand = replace(base, capacity_mw=cap, starting_funds=int(fu), lives=lv,
                   waves=scaled_waves(base.waves, wt))
    return (cap, fu, wt, lv, grade_anchor(cand, diffs, towers, enemies))


def sweep_one(anchor_id: str, args) -> int:
    base = load_anchor(anchor_id)
    diffs = list(DIFFICULTIES)
    enemies = load_enemies()
    leak = float(sum(s.count * enemies[s.enemy].leak_cost
                     for w in base.waves for s in w.spawns))

    caps = parse_list(args.cap, [base.capacity_mw * m for m in (0.9, 1.0, 1.1)],
                      base.capacity_mw, leak)
    funds = parse_list(args.funds, [base.starting_funds * m for m in (0.85, 1.0, 1.15)],
                       base.starting_funds, leak)
    weights = parse_list(args.weight, [0.85, 1.0, 1.15], 1.0, leak)
    lives = sorted({max(1, int(round(x))) for x in
                    parse_list(args.lives, [float(base.lives)], base.lives, leak)})
    total = len(caps) * len(funds) * len(weights) * len(lives)

    print(f"{anchor_id}  {base.title}  ·  sweeping "
          f"{len(caps)}x{len(funds)}x{len(weights)}x{len(lives)} = {total} cells\n")
    print(f"{'cap':>6s} {'funds':>7s} {'wt':>5s} {'liv':>4s}  "
          f"{'std':>7s} {'hard':>7s} {'brutal':>7s} {'peak':>6s}  verdict")

    jobs = [(base, cap, fu, wt, lv, diffs)
            for cap in caps for fu in funds for wt in weights for lv in lives]
    n_jobs = (os.cpu_count() or 1) if args.jobs == 0 else args.jobs

    # Leased only for the parallel path — the workers fork from this process, so one
    # lease here covers the whole pool via tools/reap.py's ancestor walk, and a sibling
    # agent's `tools/reap.py --kill` spares it instead of orphaning it mid-sweep (PRC-07).
    # Held for the pool's full lifetime, including the `pool.join()` below, via ExitStack
    # rather than a `with Pool(...)` because which branch even creates a pool depends on
    # `n_jobs` and the existing serial/parallel split is not itself worth restructuring.
    with contextlib.ExitStack() as stack:
        if n_jobs <= 1:
            _init_worker()
            results = (_grade_cell(j) for j in jobs)
        else:
            stack.enter_context(lease.acquire(
                "sweep", [f"jobs={n_jobs}", f"cells={len(jobs)}"], ttl_s=SWEEP_LEASE_TTL_S))
            pool = Pool(min(n_jobs, len(jobs)), initializer=_init_worker)
            results = pool.imap(_grade_cell, jobs)

        clean: list[tuple] = []
        for cap, fu, wt, lv, r in results:
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
        if n_jobs > 1:
            pool.close()
            pool.join()

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
        p = DATA / "anchors" / f"{anchor_id}.json"
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep an anchor's knobs against the grader.")
    ap.add_argument("anchor", nargs="*",
                    help="anchor ids; omit and pass --act to sweep a whole act")
    ap.add_argument("--act", type=int, help="sweep every anchor in this act")
    ap.add_argument("--cap", help="capacities in MW, or xN multiples of the current one")
    ap.add_argument("--funds", help="starting funds, or xN multiples")
    ap.add_argument("--weight", help="spawn-count multipliers")
    ap.add_argument("--lives", help="life counts, xN multiples, or %%N of the anchor's "
                                    "total leak_cost — the only comparable form")
    ap.add_argument("--apply", action="store_true",
                    help="write the best clean cell back into the anchor file")
    ap.add_argument("--quiet", action="store_true", help="only print clean cells")
    ap.add_argument("--jobs", type=int, default=1,
                    help="grade this many cells at once; 0 for one per core")
    args = ap.parse_args()

    ids = list(args.anchor)
    if args.act is not None:
        ids += [i for i in all_anchor_ids() if load_anchor(i).act == args.act]
    if not ids:
        ap.error("name at least one anchor, or pass --act")

    # Anchors run one after another with their cells in parallel, rather than the other way
    # round: a grid is dozens of cells and an act is eight anchors, so this keeps every core
    # busy while the output stays in grid order and readable.
    failed = []
    for i, aid in enumerate(dict.fromkeys(ids)):
        if i:
            print()
        if sweep_one(aid, args) != 0:
            failed.append(aid)
    if len(ids) > 1:
        print(f"\n{len(ids) - len(failed)}/{len(ids)} anchors found a clean cell")
        if failed:
            print(f"no clean cell in the searched box: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
