#!/usr/bin/env python3
"""
Per-emplacement siting instrument (LF-176).

The owner's complaint, verbatim: "the placement of most turrets are barely able to hit
the enemies on the field." Nothing in this project measured that before now. `sim/run.py`
only asks whether a build wins; `tools/density.py` reports what is on screen, not who can
reach it. A board where every gun fires at the extreme edge of its range for a sliver of
each pass grades identically, on both instruments, to a board sited well. This file is the
missing measurement, not a fix — it must not change any Outcome the grader already
produces, and it changes no value in `data/`.

Two independent halves, because the owner's three candidate causes need different
evidence:

  - `lane_coverage()` is pure geometry: what fraction of the lane's length falls inside a
    tower's range from a given slot, computed by sampling the lane — no sim, no RNG, no
    build order. This is the ceiling: what the *best possible* siting of *any* available
    tower at *that* slot could achieve. It separates (a) "the slot is just far from the
    lane" and (b) "the range value is low for this lane" from (c) "a decent slot exists but
    the build policy didn't use it" — if the ceiling itself is low, the level's own
    slot-and-range budget is the problem regardless of who is placing towers; if the
    ceiling is high but the dynamic numbers below are low, the grading policies (not the
    level) are the more likely explanation. See `verdict()`.

  - `InstrumentedSim` drives the real, unmodified `sim.engine.Sim` and reconstructs
    per-emplacement telemetry from its public state after each tick: whether a live,
    targetable unit was in range (independent of cooldown — a gun that could have hit
    something is "covered" whether or not it happened to be reloading that instant), and
    how much damage each placed emplacement actually dealt. It does NOT reimplement
    targeting or damage resolution — see the class docstring for exactly what is
    overridden and why neither override can change an Outcome. `--selftest` proves this
    at runtime rather than by argument: it runs the same (anchor, policy, difficulty)
    through both `Sim` and `InstrumentedSim` and asserts the resulting `Outcome`s are
    field-for-field identical.

`sim/engine.py` is untouched by this file. Every class here is a subclass that wraps
existing methods; nothing here is imported back into engine.py, content.py, or run.py.

    .venv/bin/python -m sim.coverage --selftest              # sanity + determinism, no data
    .venv/bin/python -m sim.coverage                         # every anchor, summary table
    .venv/bin/python -m sim.coverage --anchor anchor-09       # one anchor, detail
    .venv/bin/python -m sim.coverage --jobs 8 --json out.json # parallel, machine-readable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field, replace as dc_replace
from multiprocessing import Pool
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np  # noqa: E402

from sim.content import (Anchor, Enemy, Tower, all_anchor_ids, load_anchor,  # noqa: E402
                         load_enemies, load_towers, unlocked_for)
from sim.engine import DIFFICULTIES, Placed, Policy, Sim, standard_policies  # noqa: E402
import lease  # noqa: E402 — scopes the --jobs pool for tools/reap.py (PRC-07)

COVERAGE_LEASE_TTL_S = 1800.0

# A tower's "geometric" ceiling is sampled, not solved in closed form — an anchor's lane
# is a short axis-aligned polyline (decision 030) and closed-form circle/segment
# intersection has to special-case tangency and full containment for no benefit here.
# This runs once per (anchor, slot, tower), not per tick, so 8 samples/tile is cheap and
# far finer than DT ever needs.
SAMPLES_PER_TILE = 8

# An emplacement below this combat-uptime fraction is counted as a "near-zero
# contributor" in the summary table — the shape of tail the owner's complaint predicts.
# Chosen as a round, pre-stated threshold rather than picked after seeing the data.
NEAR_ZERO_UPTIME = 0.10


# ─────────────────────────────────────────────────────────── geometric half ──

def lane_coverage(anchor: Anchor, slot: tuple[float, float], rng: float,
                  samples_per_tile: int = SAMPLES_PER_TILE) -> float:
    """Fraction of the anchor's total lane length within `rng` of `slot`.

    Length-weighted across every lane on a multi-lane anchor, matching how
    `Sim._slot_priority()` already treats "nearest the path" as "nearest any lane"
    (WAR-01). Euclidean distance, matching every range test in the engine (decision 030
    compares squares rather than calling sqrt; this does the same).
    """
    sx, sy = slot
    total_len = sum(l.path_length for l in anchor.lanes)
    if total_len <= 0.0:
        return 0.0
    covered = 0.0
    for lane in anchor.lanes:
        if lane.path_length <= 0.0:
            continue
        n = max(2, int(lane.path_length * samples_per_tile))
        in_range = 0
        for i in range(n + 1):
            d = lane.path_length * i / n
            x, y = lane.point_at(d)
            dx, dy = sx - x, sy - y
            if dx * dx + dy * dy <= rng * rng:
                in_range += 1
        covered += (in_range / (n + 1)) * lane.path_length
    return covered / total_len


@dataclass
class SlotGeometry:
    slot: tuple[int, int]
    best_tower: str
    best_coverage: float                # at the tower's authored range
    best_coverage_2x_range: float        # same tower, range doubled — separates (a) from (b)
    coverage_by_tower: dict[str, float]


def geometric_report(anchor: Anchor, towers: dict[str, Tower]) -> list[SlotGeometry]:
    """Ceiling coverage for every authored slot, independent of any build policy."""
    avail = [t for t in unlocked_for(towers, anchor.id) if t.is_weapon]
    out = []
    for slot in anchor.slots:
        cov = {t.id: lane_coverage(anchor, slot, t.range) for t in avail}
        if not cov:
            continue
        best_id = max(cov, key=cov.get)
        best_tower = next(t for t in avail if t.id == best_id)
        cov_2x = lane_coverage(anchor, slot, best_tower.range * 2.0)
        out.append(SlotGeometry(slot=slot, best_tower=best_id, best_coverage=cov[best_id],
                                 best_coverage_2x_range=cov_2x, coverage_by_tower=cov))
    return out


# ────────────────────────────────────────────────────────────── dynamic half ──

@dataclass
class EmplacementStats:
    anchor: str
    difficulty: str
    policy: str
    slot: tuple[int, int]
    tower_id: str
    ticks_total: int = 0
    ticks_combat: int = 0     # ticks with >=1 alive unit anywhere on the board
    ticks_in_range: int = 0   # cooldown-independent: a live, targetable unit was in range
    damage_dealt: float = 0.0
    kills: int = 0

    @property
    def uptime_combat(self) -> float:
        return self.ticks_in_range / self.ticks_combat if self.ticks_combat else 0.0

    @property
    def uptime_total(self) -> float:
        return self.ticks_in_range / self.ticks_total if self.ticks_total else 0.0


class InstrumentedSim(Sim):
    """Drives the real, unmodified `Sim` and reconstructs per-emplacement telemetry from
    its public state. See this module's docstring for the overview; this docstring is the
    exact accounting of what each override does and why it cannot move an Outcome.

    `_veteran_rank(p)` is wrapped, not replaced: it is the one point in the unmodified
    `_step()` fire loop that names *which* `Placed` record is about to act, called once
    per (online, weapon, off-cooldown) emplacement per tick, before target search. The
    override records `p` on `self._current_p`, then returns `super()._veteran_rank(p)`
    completely unchanged — same input, same output, every time. `_step()` cannot observe
    the difference.

    `_damage(u, tower, scale, dmg_mult)` is wrapped the same way. It reads `u.hp` before
    and after calling `super()._damage(...)` and attributes the *observed* delta to
    whichever `p` the hook above most recently recorded — not a recomputation of the
    armour/shield-tax formula, so this cannot silently drift from `_damage()`'s own rules
    even if that formula changes later. The return value is passed through unchanged.

    `_tick_once()` calls `super()._tick_once()` FIRST — the entire real tick, including
    every real `_damage()` call, has already happened and already updated `_current_p`
    correctly for each one — and only afterward walks `self.placed`/`self.units` to
    record whether some live, targetable unit was in range of each online weapon,
    independent of cooldown. That walk reuses the base engine's own `_covered_by()`/
    `_can_target()` (pure reads) for reveal/shield eligibility rather than reimplementing
    them, and the same squared-distance compare every range test in the engine already
    uses (decision 030) — the only formula duplicated here is that two-line compare,
    which is already duplicated at three other call sites in engine.py itself.

    One known imprecision, narrow enough to be worth stating rather than hiding: the
    post-tick range walk calls `_veteran_rank(p)` again to size `rng` for a
    veterancy-opted policy, and that call sees `p.kills` as of the END of this tick
    (after the real fire loop's own kill this tick, if any), while the real fire loop
    used the rank as of the START of its own turn. The two can disagree for exactly one
    tick, on exactly the emplacement that crossed a rank threshold that tick, under
    "veteran-crews" (the only policy that opts into veterancy at all). Every other policy
    and every other emplacement is unaffected.

    Neither override writes to any field `Outcome` is built from — `--selftest` proves
    this by running the same inputs through `Sim` and `InstrumentedSim` and asserting the
    two `Outcome`s are field-for-field identical, on more than one anchor (coverage is
    anchor-dependent — a spot check on anchor-01 alone has hidden a one-sided regression
    before).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.emplacement_stats: dict[int, EmplacementStats] = {}
        self._current_p: Placed | None = None

    def _stats_for(self, p: Placed) -> EmplacementStats:
        st = self.emplacement_stats.get(id(p))
        if st is None:
            st = EmplacementStats(anchor=self.a.id, difficulty=self.difficulty,
                                   policy=self.policy.name, slot=p.slot,
                                   tower_id=p.tower.id)
            self.emplacement_stats[id(p)] = st
        return st

    def _veteran_rank(self, p: Placed):
        self._current_p = p
        return super()._veteran_rank(p)

    def _damage(self, u, tower, scale: float = 1.0, dmg_mult: float = 1.0) -> bool:
        before = u.hp
        killed = super()._damage(u, tower, scale, dmg_mult)
        dealt = before - u.hp
        if dealt > 0.0 and self._current_p is not None:
            st = self._stats_for(self._current_p)
            st.damage_dealt += dealt
            if killed:
                st.kills += 1
        return killed

    def _tick_once(self) -> None:
        super()._tick_once()
        combat = any(u.alive for u in self.units)
        for p in self.placed:
            if not p.online or not p.tower.is_weapon:
                continue
            st = self._stats_for(p)
            st.ticks_total += 1
            if combat:
                st.ticks_combat += 1
            vet = self._veteran_rank(p)   # pure read; see class docstring's known imprecision
            rng = p.tower.range * (vet.range_mult if vet is not None else 1.0)
            has_target = False
            for u in self.units:
                if not u.alive:
                    continue
                x, y = self.a.point_at(u.lane, u.dist)
                dx, dy = p.slot[0] - x, p.slot[1] - y
                if dx * dx + dy * dy > rng * rng:
                    continue
                revealed = u.kind.kind != "air" or self._covered_by("reveal", x, y) > 0
                if self._can_target(p.tower, u, revealed):
                    has_target = True
                    break
            if has_target:
                st.ticks_in_range += 1


def run_instrumented(anchor: Anchor, towers: dict[str, Tower], enemies: dict[str, Enemy],
                     policy: Policy, difficulty: str) -> tuple:
    sim = InstrumentedSim(anchor, towers, enemies, policy, difficulty)
    outcome = sim.run()
    return outcome, list(sim.emplacement_stats.values())


# ───────────────────────────────────────────────────────────────── analysis ──

def analyze_anchor(anchor_id: str, difficulties: list[str] | None = None) -> dict:
    """Geometric ceiling for every slot, plus dynamic per-emplacement telemetry across
    every standard policy at every requested difficulty (default: all three)."""
    anchor = load_anchor(anchor_id)
    towers = load_towers()
    enemies = load_enemies()
    diffs = difficulties or list(DIFFICULTIES)
    available = sorted(t.id for t in towers.values() if t.unlocked_at <= anchor.id)
    policies = standard_policies(available)

    geometry = geometric_report(anchor, towers)

    samples: list[dict] = []
    for diff in diffs:
        for policy in policies:
            outcome, stats = run_instrumented(anchor, towers, enemies, policy, diff)
            total_dealt = sum(s.damage_dealt for s in stats) or 1.0
            for s in stats:
                samples.append({
                    "policy": policy.name, "difficulty": diff, "slot": s.slot,
                    "tower_id": s.tower_id, "uptime_combat": s.uptime_combat,
                    "uptime_total": s.uptime_total, "damage_dealt": s.damage_dealt,
                    "damage_share": s.damage_dealt / total_dealt, "kills": s.kills,
                    "ticks_combat": s.ticks_combat, "won": outcome.won,
                })

    return {"anchor": anchor.id, "act": anchor.act, "title": anchor.title,
            "range_basis": range_basis(towers),
            "geometry": [asdict(g) for g in geometry], "samples": samples}


def range_basis(towers: dict[str, Tower]) -> dict[str, Any]:
    """The weapon ranges this report was computed against, plus a short digest of them.

    `verdict()` below is defined RELATIVE to whatever is in `data/towers.json` at the
    moment it runs — its 2x comparison scales with the base — so two reports are only
    comparable if this matches. Recording it is what makes that checkable instead of
    assumed (LF-186; measured in decision 074, where a global range rise flipped four of
    five anchors' labels while their dynamic tails went flat or worse).
    """
    ranges = {t.id: t.range for t in sorted(towers.values(), key=lambda t: t.id)
              if t.is_weapon}
    payload = json.dumps(ranges, sort_keys=True).encode()
    return {"weapon_ranges": ranges,
            "digest": hashlib.sha256(payload).hexdigest()[:12]}


def _quantiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p10": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 0.0,
                "mean": 0.0, "max": 0.0, "n": 0}
    arr = np.array(values, dtype=float)
    return {"p10": float(np.percentile(arr, 10)), "p25": float(np.percentile(arr, 25)),
            "p50": float(np.percentile(arr, 50)), "p75": float(np.percentile(arr, 75)),
            "p90": float(np.percentile(arr, 90)), "mean": float(arr.mean()),
            "max": float(arr.max()), "n": len(values)}


def summarize(report: dict) -> dict:
    uptimes = [s["uptime_combat"] for s in report["samples"] if s["ticks_combat"] > 0]
    shares = [s["damage_share"] for s in report["samples"] if s["ticks_combat"] > 0]
    near_zero = sum(1 for u in uptimes if u < NEAR_ZERO_UPTIME)
    ceiling = [g["best_coverage"] for g in report["geometry"]]
    ceiling_2x = [g["best_coverage_2x_range"] for g in report["geometry"]]
    return {
        "anchor": report["anchor"], "act": report["act"], "title": report["title"],
        "uptime_combat": _quantiles(uptimes),
        "damage_share": _quantiles(shares),
        "near_zero_frac": near_zero / len(uptimes) if uptimes else 0.0,
        "near_zero_n": near_zero, "sample_n": len(uptimes),
        "geometric_ceiling": _quantiles(ceiling),
        "geometric_ceiling_2x_range": _quantiles(ceiling_2x),
        # Carried through so a summary is never separated from the ranges it describes.
        "range_basis": report.get("range_basis"),
    }


def verdict(summary: dict) -> str:
    """(a)/(b)/(c) call for one anchor, from the numbers alone.

    If the geometric ceiling itself is low, no build policy can fix it — that is (a) or
    (b), the level's own slot-and-range budget. Whether doubling every tower's range
    closes most of the gap distinguishes them: it does for (b) (range too short for an
    otherwise-reasonable slot) and does not for (a) (the slot is simply far from the
    lane, and no plausible range value reaches it). If the ceiling is comfortably high
    but the dynamic uptime distribution is still low, the gap is the build policy — (c).

    **This label is RELATIVE to whatever ranges are in `data/towers.json` when it runs, and
    it is therefore not comparable across a tuning pass.** LF-186, measured during decision
    074's range pass rather than reasoned about: raising every weapon's base range raises
    `ceil_p50` *and* the `ceil2x_p50` it is compared against, so the classification moves on
    its own. Four of the five anchors LF-181 had declared "(a) geometry" — anchor-03, 04, 05
    and 07 — **flipped** to "(b) range" or to "sited adequately" while their dynamic p10 went
    flat or *worse* (8→5%, 4→0%, 5→3%, 1→0%). The `ceil_p50 >= 0.5` early return is the
    sharpest edge: cross that threshold and the (a)/(b) question is never even asked.

    None of that makes the ceiling wrong — it remains a true statement about the level given
    today's ranges. It makes the *label* read as a claim about slot geometry when it is a
    claim about geometry-and-range together. So `range_basis()` records the ranges and a
    digest of them alongside every report: **two labels are comparable only if their
    `range_basis.digest` matches**, and if it does not, the dynamic distribution is the thing
    to compare, not the letter.
    """
    ceil_p50 = summary["geometric_ceiling"]["p50"]
    ceil2x_p50 = summary["geometric_ceiling_2x_range"]["p50"]
    dyn_p50 = summary["uptime_combat"]["p50"]
    if ceil_p50 >= 0.5:
        if dyn_p50 < 0.5 * ceil_p50:
            return "(c) policy: good slots exist, dynamic play under-uses them"
        return "boards are sited adequately at this anchor"
    # ceiling is low — is it a range problem or a geometry problem?
    if ceil2x_p50 >= 2.0 * max(ceil_p50, 1e-6) and ceil2x_p50 >= 0.5:
        return "(b) range: coverage recovers sharply at 2x range — values are short"
    return "(a) geometry: even 2x range does not recover coverage — slots are far from the lane"


# ─────────────────────────────────────────────────────────────── self-test ──

def _selftest() -> int:
    ok = True

    # 1. Hand-checked geometric number. anchor-01, slot (2,3), pulse-turret (range 3.2).
    # Worked by hand against the anchor's own waypoints: the polyline segment
    # (0,5)-(3,5)-(3,2)-(7,2)-... — the slot's full circle covers all of the first two
    # segments and 2.04 of the 4-tile third segment, out of a 24-tile lane total, which
    # is 8.04/24 = 0.335.
    anchor01 = load_anchor("anchor-01")
    got = lane_coverage(anchor01, (2, 3), 3.2)
    want = 0.335
    if abs(got - want) > 0.01:
        print(f"FAIL geometric hand-check: slot (2,3) r=3.2 -> {got:.4f}, want ~{want}")
        ok = False
    else:
        print(f"ok   geometric hand-check: slot (2,3) r=3.2 -> {got:.4f} (hand calc {want})")

    # 2. A slot far from every lane must report EXACTLY zero coverage, and a tower forced
    # to build there (via an explicit "build" schedule verb, never touching data/) must
    # report exactly zero ticks-in-range and zero damage across a full, populated run —
    # not "near zero": since geometric coverage is provably 0.0, no unit can ever enter
    # range, so this is a hard equality, not a threshold.
    far_slot = (500, 500)
    towers = load_towers()
    enemies = load_enemies()
    far_anchor = dc_replace(anchor01, slots=anchor01.slots + (far_slot,))
    geo_far = lane_coverage(far_anchor, far_slot, 6.5)  # 6.5 = the longest range in the game
    if geo_far != 0.0:
        print(f"FAIL far-slot geometric check: expected exactly 0.0, got {geo_far}")
        ok = False
    else:
        print(f"ok   far-slot geometric check: slot {far_slot} -> {geo_far}")

    # closed=True with an EMPTY preference means `_try_build()`'s catalog filter leaves
    # nothing buildable, so the prep-phase greedy fill (which would otherwise spend the
    # anchor's whole 300 starting funds on its 3 nearest real slots before this schedule
    # ever runs — confirmed by hand, see the commit history for this file) never touches
    # any slot. Only the explicit scheduled `build` verb below places anything, and it
    # reads `self.towers` directly rather than the (empty) preference-filtered catalog.
    far_policy = Policy("far-probe", [], closed=True,
                        schedule=[(0.0, "build", {"tower": "pulse-turret", "slot": far_slot})])
    outcome, stats = run_instrumented(far_anchor, towers, enemies, far_policy, "standard")
    far_stats = [s for s in stats if s.slot == far_slot]
    if not far_stats:
        print("FAIL far-slot dynamic check: the scheduled build never landed")
        ok = False
    else:
        s = far_stats[0]
        if s.ticks_combat == 0:
            print("FAIL far-slot dynamic check: no combat occurred — test is vacuous")
            ok = False
        elif s.ticks_in_range != 0 or s.damage_dealt != 0.0:
            print(f"FAIL far-slot dynamic check: expected 0 ticks-in-range and 0 damage, "
                  f"got {s.ticks_in_range} ticks / {s.damage_dealt} damage over "
                  f"{s.ticks_combat} combat ticks")
            ok = False
        else:
            print(f"ok   far-slot dynamic check: 0/{s.ticks_combat} combat ticks in range, "
                  f"0.0 damage, across a real {outcome.waves_cleared}-wave run")

    # 3. Determinism: run the same (anchor, policy, difficulty) twice, compare byte-for-byte.
    policies = standard_policies(sorted(t.id for t in towers.values()
                                        if t.unlocked_at <= "anchor-09"))
    a09 = load_anchor("anchor-09")
    p = next(pol for pol in policies if pol.name == "burst")
    o1, s1 = run_instrumented(a09, towers, enemies, p, "hard")
    o2, s2 = run_instrumented(a09, towers, enemies, p, "hard")
    d1 = sorted((s.slot, s.tower_id, s.ticks_total, s.ticks_combat, s.ticks_in_range,
                s.damage_dealt, s.kills) for s in s1)
    d2 = sorted((s.slot, s.tower_id, s.ticks_total, s.ticks_combat, s.ticks_in_range,
                s.damage_dealt, s.kills) for s in s2)
    if o1.as_dict() != o2.as_dict() or d1 != d2:
        print("FAIL determinism: two runs of the same inputs disagreed")
        ok = False
    else:
        print(f"ok   determinism: anchor-09/burst/hard identical across two runs "
              f"({len(d1)} emplacements, {sum(s.damage_dealt for s in s1):.1f} total damage)")

    # 4. Outcome parity: InstrumentedSim must reproduce the plain Sim's Outcome exactly —
    # proof, not argument, that neither override can move a graded result. Anchor-dependent
    # per this project's own experience (a one-sided change once showed 30 differences on
    # anchor-24 and nothing on anchor-01), so this checks several anchors and several
    # policies, not just the tutorial.
    check_anchors = ["anchor-01", "anchor-09", "anchor-13", "anchor-24"]
    mismatches = []
    for aid in check_anchors:
        a = load_anchor(aid)
        avail = sorted(t.id for t in towers.values() if t.unlocked_at <= aid)
        for pol in standard_policies(avail):
            for diff in DIFFICULTIES:
                plain = Sim(a, towers, enemies, pol, diff).run().as_dict()
                inst = InstrumentedSim(a, towers, enemies, pol, diff).run().as_dict()
                if plain != inst:
                    mismatches.append((aid, pol.name, diff))
    if mismatches:
        print(f"FAIL outcome parity: {len(mismatches)} (anchor, policy, difficulty) "
              f"cells differ between Sim and InstrumentedSim: {mismatches[:10]}")
        ok = False
    else:
        n = len(check_anchors) * len(standard_policies(["pulse-turret"])) * len(DIFFICULTIES)
        print(f"ok   outcome parity: InstrumentedSim == Sim on every policy/difficulty "
              f"across {', '.join(check_anchors)}")

    return 0 if ok else 1


# ──────────────────────────────────────────────────────────────────── CLI ──

def _analyze_one(job: tuple) -> dict:
    anchor_id, diffs = job
    return analyze_anchor(anchor_id, diffs)


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-emplacement siting instrument (LF-176).")
    ap.add_argument("--anchor", help="one anchor id (default: all)")
    ap.add_argument("--difficulty", choices=list(DIFFICULTIES), action="append",
                    help="repeatable; default all three")
    ap.add_argument("--jobs", type=int, default=1, help="anchors in parallel; 0 for one/core")
    ap.add_argument("--json", help="write the full per-emplacement sample table here")
    ap.add_argument("--detail", action="store_true", help="print per-slot geometry too")
    ap.add_argument("--selftest", action="store_true", help="sanity + determinism, no report")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    ids = [args.anchor] if args.anchor else all_anchor_ids()
    diffs = args.difficulty or list(DIFFICULTIES)
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else args.jobs

    work = [(i, diffs) for i in ids]
    if jobs <= 1 or len(work) == 1:
        reports = [_analyze_one(w) for w in work]
    else:
        with lease.acquire("sim-coverage", [f"jobs={jobs}", f"anchors={len(work)}"],
                           ttl_s=COVERAGE_LEASE_TTL_S):
            with Pool(min(jobs, len(work))) as pool:
                reports = list(pool.map(_analyze_one, work))

    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=2, sort_keys=True))
        print(f"wrote {len(reports)} anchor report(s) to {args.json}")

    summaries = [summarize(r) for r in reports]

    if args.detail and args.anchor:
        r = reports[0]
        print(f"\n{r['anchor']}  {r['title']}  ·  act {r['act']}  ·  geometric ceiling per slot\n")
        for g in sorted(r["geometry"], key=lambda g: -g["best_coverage"]):
            print(f"  slot {g['slot']!s:>10s}  best {g['best_tower']:<18s} "
                  f"{g['best_coverage']:>6.1%}  (2x range {g['best_coverage_2x_range']:>6.1%})")

    # Printed, not just stored: the (a)/(b) verdict below is defined relative to these
    # ranges, so a reader comparing this table against an older one needs to see whether
    # the basis moved. LF-186 — a range pass silently reclassified four anchors.
    if summaries and summaries[0].get("range_basis"):
        rb = summaries[0]["range_basis"]
        print(f"\nrange basis {rb['digest']} — "
              + ", ".join(f"{k} {v}" for k, v in rb["weapon_ranges"].items())
              + "\n  (verdicts below are RELATIVE to these; only comparable to a run with "
                "the same digest)")

    print(f"\n{'anchor':<10s} {'act':>3s} {'ceiling p50':>11s} {'ceil@2x p50':>11s} "
          f"{'uptime p10/p50/p90':>19s} {'dmg-share p10/p50/p90':>22s} "
          f"{'near-zero':>9s}  verdict")
    for s in summaries:
        u = s["uptime_combat"]
        d = s["damage_share"]
        nz = f"{s['near_zero_n']}/{s['sample_n']}"
        print(f"{s['anchor']:<10s} {s['act']:>3d} {s['geometric_ceiling']['p50']:>10.1%} "
              f"{s['geometric_ceiling_2x_range']['p50']:>10.1%}  "
              f"{u['p10']:>5.0%}/{u['p50']:>5.0%}/{u['p90']:>5.0%}  "
              f"{d['p10']:>6.1%}/{d['p50']:>6.1%}/{d['p90']:>6.1%}  {nz:>9s}  "
              f"{verdict(s)}")

    # Per-act rollup — the distribution the owner's complaint is actually about.
    acts: dict[int, list[float]] = {}
    acts_share: dict[int, list[float]] = {}
    for r, s in zip(reports, summaries):
        acts.setdefault(s["act"], []).extend(
            samp["uptime_combat"] for samp in r["samples"] if samp["ticks_combat"] > 0)
        acts_share.setdefault(s["act"], []).extend(
            samp["damage_share"] for samp in r["samples"] if samp["ticks_combat"] > 0)
    print(f"\n{'act':>5s} {'n':>6s} {'uptime p10/p50/p90':>19s} {'dmg-share p10/p50/p90':>22s} "
          f"{'near-zero %':>11s}")
    for act in sorted(acts):
        q = _quantiles(acts[act])
        qs = _quantiles(acts_share[act])
        nz = sum(1 for v in acts[act] if v < NEAR_ZERO_UPTIME) / len(acts[act]) if acts[act] else 0.0
        print(f"{act:>5d} {q['n']:>6d} {q['p10']:>5.0%}/{q['p50']:>5.0%}/{q['p90']:>5.0%}  "
              f"{qs['p10']:>6.1%}/{qs['p50']:>6.1%}/{qs['p90']:>6.1%}  {nz:>10.0%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
