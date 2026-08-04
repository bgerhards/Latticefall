#!/usr/bin/env python3
"""
Derive emplacement range from measured lane length at 48 squared (BAL-04 / LF-187).

WHY THIS EXISTS. `range` in `data/towers.json` is an absolute distance in tiles, and every
one of its nine values was tuned against an 18x15 board whose lanes are 24-51 tiles long.
Decision 073 moved the board target to 48x48; `tools/genboard.py` generates one, and its
lanes measure 68-80 tiles. A constant absolute range therefore covers a shrinking fraction
of the wave's route as the board grows -- which is LF-187's whole argument, and it is the
one quantity BAL-04 names that decision 074 deliberately did not touch, because 074 was
tuning today's boards.

WHAT IS HELD FIXED, AND WHY IT IS THIS. **Own-lane coverage**: the share of ONE lane's
length that a slot can reach. Not `sim/coverage.py`'s `lane_coverage()`, which is
length-weighted across every lane on the anchor -- on a four-lane board that divides a
perfectly-sited gun's score by four and would read the lane count as a range problem. Not
`presence_coverage()` either, despite it being the better predictor of uptime (LF-217,
rank correlation +0.748 against +0.520): presence is measured from a live run on the
board's own authored slots, so it moves when the slots move and it cannot be evaluated on
a board that has not been graded yet. This solve needs a pure-geometry invariant that
exists before the first run, and own-lane coverage is that. `--grade` is what checks the
answer dynamically afterwards; the solve itself stays geometric on purpose.

THE SOLVE, per emplacement `w`:

    target[w]  = median own-lane coverage of w, at its AUTHORED range, over every
                 authored slot of every shipped anchor in the reference act
    derived[w] = the range at which the median own-lane coverage over the GENERATED
                 board's slots equals target[w]

Coverage is monotone non-decreasing in range, so the solve is a bisection. It is run per
emplacement rather than as one multiplier because the ladder does not scale uniformly --
measured, the top weapon needs ~1.86x and the shortest ~1.45x, because a short range on a
winding lane sits on the steep part of the curve and a long one does not. Decision 074
reached the same conclusion on the small board and this file keeps that shape.

WHICH ROWS ARE DERIVED. Every row whose range is read against a UNIT position: the five
weapons, plus `shield-wall` (slow), `scan-relay` (reveal) and `anchor-damper` (damp) --
`Sim._covered_by()` tests all three with the same squared-distance compare the firing loop
uses, so they are lane-coverage quantities exactly as much as a weapon's range is. Decision
074 left the support rows alone; that was right for a tuning pass and is wrong for a scale
change.

`restorer` is excluded, and the reason is a measurement rather than a judgement:
`Sim.capacity_now()` adds `effect_value` for every online restorer with no distance test
at all, so `restorer.range` is inert data. Scaling it would be scaling nothing. Filed as a
backlog item rather than fixed here.

The four `upgrade.range` overrides are solved the same way against their own authored
coverage, because an upgrade that does not scale with the base becomes a downgrade.

NOTHING HERE WRITES TO `data/`. The output is a table and, with `--patch`, the JSON body of
the change -- printed, never applied. These ranges belong to a 48-square campaign and the
flip lands with the content, not before it.

**And the reason is not the one it looks like.** The obvious argument -- a 13-tile mortar on
a 41-tile lane would break the shipped campaign -- is wrong, and `--shipped-impact` is here
because it was measured rather than assumed. Grading all 24 shipped anchors at the derived
ranges leaves them **24/24 `ok`, exactly as they are today**. What actually happens is that
the top difficulty dissolves: on brutal the share of tried builds that win goes **25% ->
43%**, the median anchor's distinct winning builds **3 -> 6**, and anchor-02, 09 and 11 go
from 2 winning builds to 8, 7 and 8. Not one anchor trips `sim/run.py`'s "difficulty is not
biting" rule, because that rule only fires when EVERY distinct build clears.

So the grade table cannot see this change, which is the second instance of decision 081's
finding that the grade table is not a valid selector of board quality. Do not use a green
24/24 as evidence that a range pass is safe.

    .venv/bin/python tools/range_derive.py                    # the derivation table
    .venv/bin/python tools/range_derive.py --patch            # + the towers.json body
    .venv/bin/python tools/range_derive.py --grade            # + grade the board at both
    .venv/bin/python tools/range_derive.py --shipped-impact   # + what it would do to 18x15
    .venv/bin/python tools/range_derive.py --json /tmp/r.json
    .venv/bin/python tools/range_derive.py --selftest

`--grade` is ~11 minutes and `--shipped-impact` ~2 at `--jobs 8`; pass an explicit Bash
timeout. Neither is on any gate tier -- this file is a derivation run when a board size
changes, not a check that runs on every commit.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import genboard  # noqa: E402
from sim.content import (Anchor, Tower, all_anchor_ids, anchor_from_doc,  # noqa: E402
                         load_anchor, load_enemies, load_towers)

# Samples per tile of lane when integrating coverage. `sim/coverage.py` uses 8 for the
# same integral and its --selftest pins the convergence; matched here so the two modules
# cannot disagree about what "fraction of a lane covered" means.
SAMPLES_PER_TILE = 8

# Bisection bounds, in tiles. The lower bound is below every authored range; the upper is
# above the 48-square board's diagonal (67.9), so a row that cannot be solved inside the
# bracket is a real failure rather than a clipped one.
RANGE_LO = 0.25
RANGE_HI = 72.0
# Solve tolerance in tiles, then round. `data/towers.json` authors range to one decimal,
# so solving finer than that is precision the format cannot carry.
RANGE_TOL = 0.005
RANGE_QUANTUM = 0.1

# The reference act. The generated board is act 3 by default and derives its own budgets
# from `genboard.shipped_ratios(3)`, so the coverage target is taken from the same act:
# every ratio in the comparison is then like-for-like.
REFERENCE_ACT = 3

# `restorer.range` is not read anywhere in either rules implementation -- see the module
# docstring. Excluded from the solve so the table does not imply a change that would have
# no effect.
INERT_RANGE_IDS = ("restorer",)


# ─────────────────────────────────────────────────────────────── geometry ──

def lane_samples(anchor: Anchor, samples_per_tile: int = SAMPLES_PER_TILE
                 ) -> list[np.ndarray]:
    """One (n, 2) array of evenly spaced points per lane, in tile space.

    Hoisted out of the coverage integral because the bisection below evaluates the same
    anchor at ~25 ranges per emplacement: sampling once turns each evaluation into a
    vectorised distance compare instead of several thousand `Lane.point_at()` calls.
    """
    out: list[np.ndarray] = []
    for lane in anchor.lanes:
        if lane.path_length <= 0.0:
            continue
        n = max(2, int(lane.path_length * samples_per_tile))
        pts = np.empty((n + 1, 2), dtype=float)
        for i in range(n + 1):
            pts[i] = lane.point_at(lane.path_length * i / n)
        out.append(pts)
    return out


def own_lane_coverage(samples: list[np.ndarray], slot: tuple[float, float],
                      rng: float) -> float:
    """Best single-lane coverage: the largest share of any ONE lane within `rng` of `slot`.

    The multi-lane generalisation of what a shipped anchor's single lane already measures,
    which is the property that makes a number from an 18x15 board comparable with one from
    a four-lane 48-square board at all. Squared compare, as every range test in both rules
    implementations (decision 030).
    """
    sx, sy = slot
    best = 0.0
    r2 = rng * rng
    for pts in samples:
        dx = pts[:, 0] - sx
        dy = pts[:, 1] - sy
        share = float(np.count_nonzero(dx * dx + dy * dy <= r2)) / pts.shape[0]
        if share > best:
            best = share
    return best


def median_coverage(samples: list[np.ndarray], slots: list[tuple[float, float]],
                    rng: float) -> float:
    """Median own-lane coverage over a board's authored slots, at one range."""
    if not slots:
        return 0.0
    return statistics.median(own_lane_coverage(samples, s, rng) for s in slots)


def solve_range(samples: list[np.ndarray], slots: list[tuple[float, float]],
                target: float, lo: float = RANGE_LO, hi: float = RANGE_HI) -> float:
    """Smallest range whose median own-lane coverage reaches `target`, by bisection.

    Coverage is monotone non-decreasing in range -- a larger disc contains a smaller one --
    so bisection is exact up to the tolerance and needs no derivative. Returns `hi` when
    the target is out of reach inside the bracket; the caller reports that as a failure
    rather than silently accepting a clipped value.
    """
    if median_coverage(samples, slots, hi) < target:
        return hi
    while hi - lo > RANGE_TOL:
        mid = (lo + hi) / 2.0
        if median_coverage(samples, slots, mid) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def quantise(rng: float) -> float:
    """Round to the precision `data/towers.json` actually authors."""
    return round(round(rng / RANGE_QUANTUM) * RANGE_QUANTUM, 1)


# ──────────────────────────────────────────────────────────── derivation ──

def scalable_ids(towers: dict[str, Tower]) -> list[str]:
    """Emplacement ids whose `range` is tested against a unit position. Sorted."""
    return sorted(t.id for t in towers.values() if t.id not in INERT_RANGE_IDS)


def reference_anchors(act: int) -> list[Anchor]:
    """Every shipped anchor in the reference act, in id order."""
    out = [load_anchor(aid) for aid in all_anchor_ids()]
    return [a for a in out if a.act == act]


def shipped_targets(towers: dict[str, Tower], act: int) -> dict[str, float]:
    """Median own-lane coverage of each range value across the reference act's slots.

    Keyed by `<id>` for the base range and `<id>+upgrade` for an `upgrade.range` override,
    so the two solve through exactly the same path.
    """
    anchors = reference_anchors(act)
    per_anchor = [(a, lane_samples(a)) for a in anchors]
    targets: dict[str, float] = {}
    for tid in scalable_ids(towers):
        t = towers[tid]
        for key, rng in _range_values(t):
            vals = [own_lane_coverage(s, slot, rng)
                    for a, s in per_anchor for slot in a.slots]
            targets[key] = statistics.median(vals) if vals else 0.0
    return targets


def _range_values(t: Tower) -> list[tuple[str, float]]:
    """`[(key, authored range)]` for one emplacement: base, then upgrade if it has one."""
    out = [(t.id, float(t.range))]
    up = t.upgrade or {}
    if "range" in up:
        out.append((f"{t.id}+upgrade", float(up["range"])))
    return out


def derive(board: Anchor, towers: dict[str, Tower], act: int) -> list[dict[str, Any]]:
    """The derivation table: one row per range value that scales."""
    targets = shipped_targets(towers, act)
    samples = lane_samples(board)
    slots = [(float(x), float(y)) for x, y in board.slots]
    rows: list[dict[str, Any]] = []
    for tid in scalable_ids(towers):
        t = towers[tid]
        for key, authored in _range_values(t):
            target = targets[key]
            exact = solve_range(samples, slots, target)
            derived = quantise(exact)
            rows.append({
                "key": key,
                "id": tid,
                "kind": "upgrade" if key.endswith("+upgrade") else "base",
                "authored_range": authored,
                "shipped_median_coverage": round(target, 4),
                "derived_range": derived,
                "derived_median_coverage": round(median_coverage(samples, slots, derived), 4),
                "multiplier": round(derived / authored, 3) if authored else None,
                "unreachable": exact >= RANGE_HI,
            })
    return rows


def patch_body(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The `data/towers.json` change these rows describe, as a printable object.

    Printed, never written -- see the module docstring on why the flip lands with the
    48-square content rather than ahead of it.
    """
    out: dict[str, Any] = {}
    for r in rows:
        entry = out.setdefault(r["id"], {})
        if r["kind"] == "base":
            entry["range"] = r["derived_range"]
        else:
            entry.setdefault("upgrade", {})["range"] = r["derived_range"]
    return out


def apply_ranges(towers: dict[str, Tower], rows: list[dict[str, Any]]) -> dict[str, Tower]:
    """A copy of `towers` at the derived ranges. In memory only.

    `sim/run.py:grade_anchor()` takes a `towers` override for exactly this reason -- it is
    how `tools/sweep.py` already grades a candidate that was never written to disk.
    """
    import dataclasses
    by_key = {r["key"]: r["derived_range"] for r in rows}
    out: dict[str, Tower] = {}
    for tid, t in towers.items():
        rng = by_key.get(tid, t.range)
        up = dict(t.upgrade) if t.upgrade else None
        if up is not None and f"{tid}+upgrade" in by_key:
            up["range"] = by_key[f"{tid}+upgrade"]
        out[tid] = dataclasses.replace(t, range=rng, upgrade=up)
    return out


# ────────────────────────────────────────────────────────────── reporting ──

def print_table(rows: list[dict[str, Any]], board: Anchor, act: int) -> None:
    lanes = ", ".join(f"{l.path_length:.0f}" for l in board.lanes)
    ref = reference_anchors(act)
    ref_lanes = statistics.median(
        [l.path_length for a in ref for l in a.lanes]) if ref else 0.0
    print(f"reference: act {act}, {len(ref)} shipped anchors, median lane "
          f"{ref_lanes:.0f} tiles, {sum(len(a.slots) for a in ref)} slots")
    print(f"generated: {board.grid[0]}x{board.grid[1]}, {len(board.lanes)} lanes "
          f"[{lanes}] tiles, {len(board.slots)} slots\n")
    print(f"{'emplacement':24s} {'authored':>8s} {'target':>8s} "
          f"{'derived':>8s} {'at':>8s} {'x':>6s}")
    print("─" * 66)
    for r in rows:
        flag = "  UNREACHABLE" if r["unreachable"] else ""
        print(f"{r['key']:24s} {r['authored_range']:8.1f} "
              f"{r['shipped_median_coverage']:7.1%} {r['derived_range']:8.1f} "
              f"{r['derived_median_coverage']:7.1%} {r['multiplier']:6.2f}{flag}")


def grade_both(board: Anchor, towers: dict[str, Tower],
               rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Grade the generated board at authored ranges and at derived ranges.

    The geometric solve says the derived ranges restore a shipped board's coverage. Whether
    that produces a level worth playing is a different question and only the grader answers
    it -- issue #17 records the generated board's second defect (the winner set and the
    death wave identical across all three difficulties), which coverage cannot see.
    """
    from sim.run import grade_anchor
    enemies = load_enemies()
    derived_towers = apply_ranges(towers, rows)
    diffs = ["standard", "hard", "brutal"]
    return {
        "authored": grade_anchor(board, diffs, towers=towers, enemies=enemies),
        "derived": grade_anchor(board, diffs, towers=derived_towers, enemies=enemies),
    }


def _shipped_job(job: tuple[str, list[str], dict[str, float]]) -> dict[str, Any]:
    """Pool worker: grade one shipped anchor at the derived ranges.

    Top-level and taking a plain `{key: range}` dict rather than the `Tower` map, because
    a pool pickles both the callable and its arguments and the dataclass map is the larger
    thing to ship to sixteen workers.
    """
    aid, diffs, by_key = job
    towers = load_towers()
    rows = [{"key": k, "id": k.split("+")[0], "derived_range": v,
             "kind": "upgrade" if k.endswith("+upgrade") else "base"}
            for k, v in sorted(by_key.items())]
    return grade_anchor_at(aid, diffs, apply_ranges(towers, rows))


def grade_anchor_at(aid: str, diffs: list[str], towers: dict[str, Tower]) -> dict[str, Any]:
    from sim.run import grade_anchor
    r = grade_anchor(load_anchor(aid), diffs, towers=towers, enemies=load_enemies())
    return {k: v for k, v in r.items() if k != "runs"}


def shipped_impact(rows: list[dict[str, Any]], jobs: int = 8) -> list[dict[str, Any]]:
    """Grade all 24 shipped 18x15 anchors at the derived ranges.

    This is the evidence for the module docstring's refusal to write the patch. "A 13-tile
    mortar on a 41-tile lane would gut the campaign" is a prediction until somebody grades
    it, and this project's own method is that a measured refusal beats an argued one.
    """
    import os
    from multiprocessing import Pool
    import lease
    by_key = {r["key"]: r["derived_range"] for r in rows}
    diffs = ["standard", "hard", "brutal"]
    work = [(aid, diffs, by_key) for aid in all_anchor_ids()]
    n = min(jobs if jobs > 0 else (os.cpu_count() or 1), len(work))
    if n <= 1:
        return [_shipped_job(w) for w in work]
    with lease.acquire("range-derive", [f"jobs={n}", f"anchors={len(work)}"], ttl_s=1800.0):
        with Pool(n) as pool:
            return list(pool.imap(_shipped_job, work))


def print_shipped_impact(after: list[dict[str, Any]], before: list[dict[str, Any]]) -> None:
    idx = {r["anchor"]: r for r in before}
    print(f"\nshipped 18x15 campaign at the DERIVED ranges "
          f"(what applying this patch today would do):")
    print(f"{'anchor':11s} {'standard':>16s} {'hard':>16s} {'brutal':>16s}  verdict")
    broke = []
    for r in after:
        b = idx[r["anchor"]]
        cells = []
        for d in ("standard", "hard", "brutal"):
            cells.append(f"{b['by_difficulty'][d]['distinct_winning_builds']:>2d}"
                         f"->{r['by_difficulty'][d]['distinct_winning_builds']:<2d}"
                         f" of {r['by_difficulty'][d]['distinct_builds_tried']:<3d}")
        verdict = "ok" if r["ok"] else "; ".join(r["problems"])
        if not r["ok"]:
            broke.append(r["anchor"])
        print(f"{r['anchor']:11s} {cells[0]:>16s} {cells[1]:>16s} {cells[2]:>16s}  {verdict}")
    was_ok = sum(1 for r in before if r["ok"])
    print(f"\n{sum(1 for r in after if r['ok'])}/{len(after)} clean at the derived ranges, "
          f"against {was_ok}/{len(before)} at the authored ones")
    if broke:
        print(f"regressed or still broken: {', '.join(broke)}")


def print_grades(grades: dict[str, Any]) -> None:
    print(f"\n{'':10s} {'builds':>10s} {'peak':>13s} {'brownout':>9s}  died on")
    for label in ("authored", "derived"):
        r = grades[label]
        print(f"{label}")
        for diff, d in r["by_difficulty"].items():
            died = f"wave {d['earliest_death_wave']}" if d["earliest_death_wave"] else "—"
            print(f"  {diff:<8s} {d['distinct_winning_builds']:>2d} of "
                  f"{d['distinct_builds_tried']:<3d} {d['peak_load_mw']:>8.1f} MW "
                  f"{d['peak_load_ratio']:>4.0%} {d['brownout_fraction']:>8.0%}  {died}")
        for p in r["problems"]:
            print(f"    PROBLEM  {p}")
        if not r["problems"]:
            print("    ok")


# ─────────────────────────────────────────────────────────────── selftest ──

def selftest() -> int:
    """Sanity and determinism. Prints what it checked, fails loudly."""
    towers = load_towers()
    a01 = load_anchor("anchor-01")
    s01 = lane_samples(a01)
    slot = (float(a01.slots[0][0]), float(a01.slots[0][1]))

    # 1. Coverage is monotone non-decreasing in range — the property bisection rests on.
    prev = -1.0
    for r in [x / 4.0 for x in range(1, 200)]:
        cov = own_lane_coverage(s01, slot, r)
        assert cov >= prev - 1e-12, f"coverage fell at range {r}: {cov} < {prev}"
        prev = cov
    assert prev == 1.0, f"a 50-tile range covers the whole lane, got {prev}"

    # 2. Single-lane own-lane coverage equals sim/coverage.py's length-weighted metric,
    #    which is what makes a shipped-anchor number comparable at all.
    from sim.coverage import lane_coverage
    assert len(a01.lanes) == 1, "anchor-01 is single-lane; this equivalence assumes it"
    for r in (2.0, 4.0, 7.0):
        mine = own_lane_coverage(s01, slot, r)
        theirs = lane_coverage(a01, slot, r, samples_per_tile=SAMPLES_PER_TILE)
        assert abs(mine - theirs) < 1e-9, f"range {r}: {mine} vs {theirs}"

    # 3. The solve inverts the measurement: solving for a known range's own coverage
    #    returns that range.
    for r in (3.0, 5.0, 8.0):
        target = median_coverage(s01, [(float(x), float(y)) for x, y in a01.slots], r)
        got = solve_range(s01, [(float(x), float(y)) for x, y in a01.slots], target)
        assert abs(got - r) < 0.05, f"solve({target:.4f}) = {got}, expected ~{r}"

    # 4. Determinism: two derivations of the same board are identical.
    doc = genboard.generate("anchor-24", REFERENCE_ACT, 48, 48, 4, 3, 12, "derived")
    board = anchor_from_doc(doc)
    r1 = derive(board, towers, REFERENCE_ACT)
    r2 = derive(board, towers, REFERENCE_ACT)
    assert r1 == r2, "derivation is not deterministic"

    # 5. Every row solved inside the bracket, and every derived range is longer than the
    #    authored one — the direction LF-187 predicts on a board with longer lanes.
    for r in r1:
        assert not r["unreachable"], f"{r['key']} did not solve inside the bracket"
        assert r["derived_range"] > r["authored_range"], (
            f"{r['key']} derived {r['derived_range']} <= authored {r['authored_range']}")

    # 6. `restorer.range` really is inert: grading with it at 1.0 and at 40.0 is identical.
    import dataclasses
    from sim.run import grade_anchor
    a = load_anchor("anchor-20")
    wide = dict(towers)
    wide["restorer"] = dataclasses.replace(towers["restorer"], range=40.0)
    base = grade_anchor(a, ["standard"], towers=towers, enemies=load_enemies())
    alt = grade_anchor(a, ["standard"], towers=wide, enemies=load_enemies())
    assert base["by_difficulty"] == alt["by_difficulty"], (
        "restorer.range changed a grade — it is not inert, fix INERT_RANGE_IDS")

    print(f"selftest ok — monotone over 199 ranges, agrees with lane_coverage() on 3, "
          f"solve inverts 3, {len(r1)} rows derived twice identically, "
          f"restorer.range inert on anchor-20")
    return 0


# ─────────────────────────────────────────────────────────────────── main ──

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Derive emplacement range from measured lane length at 48 squared.")
    ap.add_argument("--act", type=int, default=REFERENCE_ACT, choices=(1, 2, 3),
                    help="reference act for both the coverage target and the board")
    ap.add_argument("--width", type=int, default=48)
    ap.add_argument("--height", type=int, default=48)
    ap.add_argument("--lanes", type=int, default=4)
    ap.add_argument("--sweeps", type=int, default=3)
    ap.add_argument("--waves", type=int, default=12)
    ap.add_argument("--patch", action="store_true",
                    help="also print the data/towers.json body (printed, never written)")
    ap.add_argument("--grade", action="store_true",
                    help="also grade the generated board at authored and derived ranges")
    ap.add_argument("--shipped-impact", action="store_true",
                    help="also grade all 24 shipped 18x15 anchors at the derived ranges")
    ap.add_argument("--jobs", type=int, default=8,
                    help="--shipped-impact anchors in parallel; 0 for one per core")
    ap.add_argument("--json", help="write the full table here")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    towers = load_towers()
    doc = genboard.generate("anchor-24", args.act, args.width, args.height,
                            args.lanes, args.sweeps, args.waves, "derived")
    board = anchor_from_doc(doc)
    rows = derive(board, towers, args.act)
    print_table(rows, board, args.act)

    payload: dict[str, Any] = {"act": args.act, "board": {
        "width": args.width, "height": args.height, "lanes": args.lanes,
        "sweeps": args.sweeps, "slots": len(board.slots),
        "lane_lengths": [l.path_length for l in board.lanes]}, "rows": rows}

    if args.patch:
        print("\ndata/towers.json (printed, not written):")
        print(json.dumps(patch_body(rows), indent=2, sort_keys=True))

    if args.grade:
        grades = grade_both(board, towers, rows)
        print_grades(grades)
        payload["grades"] = {k: {kk: vv for kk, vv in v.items() if kk != "runs"}
                             for k, v in grades.items()}

    if args.shipped_impact:
        # The "before" side is the ordinary campaign grade — sim/run.py's own parallel
        # path, not a reimplementation, so the baseline column is the same number the
        # gate's `anchor grades` check prints.
        from sim.run import grade_all
        diffs = ["standard", "hard", "brutal"]
        before = [{k: v for k, v in r.items() if k != "runs"}
                  for r in grade_all(all_anchor_ids(), diffs, args.jobs)]
        after = shipped_impact(rows, args.jobs)
        print_shipped_impact(after, before)
        payload["shipped_impact"] = {"before": before, "after": after}

    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2, sort_keys=True))
        print(f"\nwrote {args.json}")

    unreachable = [r["key"] for r in rows if r["unreachable"]]
    if unreachable:
        print(f"\nUNREACHABLE: {', '.join(unreachable)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
