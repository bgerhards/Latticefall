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

Three halves, which is one more than this file shipped with. `lane_coverage()` was the
original static metric and `verdict()` was defined on it; LF-217 measured that it does not
predict the very tail it was being used to explain, so `presence_coverage()` was added
beside it and `verdict()` was moved onto the new evidence. `lane_coverage()` keeps its
meaning exactly — it is a true statement about the level and other things read it.

  - `lane_coverage()` is pure geometry: what fraction of the lane's LENGTH falls inside a
    tower's range from a given slot, computed by sampling the lane — no sim, no RNG, no
    build order. Cheap, board-independent, and the right answer to "how much of this path
    can a gun here touch".

    It is **not** the right answer to "will a gun here do any work", and that is the whole
    of LF-217. Measured per (slot, tower) across all 24 anchors against mean uptime, it
    ranks at Spearman +0.52 whole-game and +0.44 mean within an anchor, and it goes
    *negative* on anchor-02, -10 and -13 — on those three it orders their own slots
    backwards. anchor-13 once held a slot at 43.9% lane coverage and 2.5% uptime while
    anchor-10 held one at 17.1% and 53.7%.

  - `presence_coverage()` is the predictor: the share of LIVE UNIT-TICKS inside a tower's
    range, weighted by a measured profile of where along the lane the wave is still alive.
    Same pairs, same runs: +0.75 whole-game and +0.77 mean within an anchor, positive on
    all 24. The mechanism is LF-218 — on an act 1–2 anchor half of all live unit-ticks fall
    in the first 20–25% of the lane and the last quarter holds 4–5%, so lane downstream of
    where the wave dies is lane no siting can convert into uptime.

    It costs no extra simulation: the profile is accumulated by the same post-tick walk
    that already records uptime, over the same (policy, difficulty) cells the dynamic half
    was already running. Pooling those 60 cells into one profile per anchor is measured
    rather than assumed — scoring each cell against its OWN profile instead of the pooled
    one moves the mean within-anchor rank correlation from +0.83 to +0.87, against +0.49
    for `lane_coverage()`, so the pooled profile captures ~96% of what a per-cell oracle
    would, and the policy-to-policy spread that pooling hides is reported per slot
    (`SlotPresence.per_run_min/max/std`) rather than hidden.

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

`verdict()` now reads presence, not lane length, and its (a)/(b)/(c) sentences changed with
it — the old "(a) geometry: slots are far from the lane" fired on anchor-01, which is the
best-sited anchor in the game. See that function for what each letter now claims and why.
Its branch ORDER is load-bearing and is argued there too: LF-217 put the near-zero tail test
first, and LF-229 measured that this let a 48x48 generated board whose own ceiling is 11.9%
report as healthy. The board-ceiling test now runs unconditionally, ahead of everything.

**One comparability rule the geometric half did not need.** A presence number is measured on
the anchor's own authored board, so it moves when the slots move; `lane_coverage()` does not.
A presence figure taken before a siting pass and one taken after are two measurements of two
boards, not a before/after of one — the same caution `range_basis()` already carries for
weapon ranges (LF-186), for the same reason.

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

# Resolution of the presence profile: the lane is divided into this many equal-length
# buckets and a live unit's tick is credited to the bucket it ends the tick in. 200 is
# 0.12–0.26 tiles per bucket on today's 24–51 tile lanes.
#
# Chosen by measured convergence, not by taste, because a bucket count that moved the
# answer would be a knob and the metric would be fitted rather than computed. Against a
# 1600-bucket reference on anchor-01 and anchor-13, the worst authored slot disagrees by
# 0.052 at 25 and 50 buckets, 0.028/0.017 at 100, and **0.012/0.004 at 200** — i.e. the
# metric is converged by 200 and the residual is an order of magnitude below any threshold
# `verdict()` compares against. `--selftest` asserts it, at 0.02.
PRESENCE_BUCKETS = 200

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


def bucket_points(anchor: Anchor, buckets: int = PRESENCE_BUCKETS) -> list[np.ndarray]:
    """World position of each presence bucket's midpoint, one (buckets, 2) array per lane.

    Hoisted out of `presence_coverage()` because it depends only on the anchor: scoring a
    whole candidate lattice re-uses one call instead of `Lane.point_at()`-ing 200 buckets
    per position. Midpoints, not edges, so a bucket is credited at its own centre of mass.
    """
    out = []
    for lane in anchor.lanes:
        pts = np.empty((buckets, 2), dtype=float)
        for b in range(buckets):
            pts[b] = lane.point_at(lane.path_length * (b + 0.5) / buckets)
        out.append(pts)
    return out


def presence_coverage(anchor: Anchor, slot: tuple[float, float], rng: float,
                      profile: np.ndarray,
                      points: list[np.ndarray] | None = None) -> float:
    """Share of the anchor's LIVE UNIT-TICKS that fall within `rng` of `slot`.

    The predictive sibling of `lane_coverage()`, and the reason this module has two
    coverage metrics instead of one. `lane_coverage()` weights every metre of lane
    equally; the wave does not. Measured across all 24 anchors, half of every live
    unit-tick on an act 1–2 anchor falls in the first 20–25% of the lane and the last
    quarter holds 4–5% (LF-218), because the wave is attrited before it gets there. A gun
    covering 44% of a lane whose covered arc is all downstream of where the wave dies is
    idle at any range — anchor-13's (6,0) measured 43.9% lane coverage against 2.5% mean
    uptime, while anchor-10's (1,1) measured 17.1% against 53.7%. `lane_coverage()` orders
    those two the wrong way round; this orders them correctly (LF-217).

    `profile` is a (lanes, buckets) array of live unit-ticks per bucket, produced by
    `InstrumentedSim.presence` and summed over whichever runs the caller wants the metric
    to describe. It is measured on the anchor's own authored board, so this metric is
    **self-referential in a way `lane_coverage()` is not**: move the slots and the profile
    moves with them, because killing the wave earlier shifts presence upstream. That is a
    property of the thing being measured, not a defect — "where is the wave alive, given
    how this level is currently played" is exactly the question `uptime_combat` answers —
    but it means a presence number is only comparable against another taken on the same
    board, the same way a `verdict()` label is only comparable at the same `range_basis`.

    Bucket count is not a free parameter — see `PRESENCE_BUCKETS` for the measured
    convergence, and `--selftest`, which re-measures one anchor at 200 and 800 buckets and
    asserts they agree to 0.02.
    """
    total = float(profile.sum())
    if total <= 0.0:
        return 0.0
    pts = points if points is not None else bucket_points(anchor, profile.shape[1])
    sx, sy = slot
    got = 0.0
    for li in range(profile.shape[0]):
        dx = pts[li][:, 0] - sx
        dy = pts[li][:, 1] - sy
        d2 = dx * dx + dy * dy          # squared compare, as every range test in the engine
        got += float(profile[li][d2 <= rng * rng].sum())
    return got / total


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


# ─────────────────────────────────────────────────────────── presence half ──

@dataclass
class SlotPresence:
    slot: tuple[int, int]
    best_tower: str
    best_presence: float                 # at the tower's authored range
    best_presence_2x_range: float        # same tower, range doubled — separates (a) from (b)
    presence_by_tower: dict[str, float]
    # Spread of `best_presence` when the profile is taken from ONE (policy, difficulty)
    # run instead of the pooled one. This is the honesty check on pooling: if a single
    # profile per anchor were not meaningful, these would be wide. See `presence_report()`.
    per_run_min: float = 0.0
    per_run_max: float = 0.0
    per_run_std: float = 0.0


def presence_report(anchor: Anchor, towers: dict[str, Tower], profile: np.ndarray,
                    per_run: list[np.ndarray] | None = None) -> list[SlotPresence]:
    """Presence-weighted coverage for every authored slot, given a measured profile.

    Structurally parallel to `geometric_report()` — same slots, same tower set, same
    best-of and 2x-range columns — so the two can be read side by side and the ordering
    disagreement that motivated this metric (LF-217) is visible in one table.

    Unlike `geometric_report()` this needs a profile, and therefore needs the sim to have
    run. `analyze_anchor()` pools the profiles of every (policy, difficulty) cell it was
    already running for the dynamic half, so the metric costs no extra simulation.
    """
    avail = [t for t in unlocked_for(towers, anchor.id) if t.is_weapon]
    pts = bucket_points(anchor, profile.shape[1])
    out = []
    for slot in anchor.slots:
        pres = {t.id: presence_coverage(anchor, slot, t.range, profile, pts) for t in avail}
        if not pres:
            continue
        best_id = max(pres, key=pres.get)
        best_tower = next(t for t in avail if t.id == best_id)
        pres_2x = presence_coverage(anchor, slot, best_tower.range * 2.0, profile, pts)
        rec = SlotPresence(slot=slot, best_tower=best_id, best_presence=pres[best_id],
                           best_presence_2x_range=pres_2x, presence_by_tower=pres)
        if per_run:
            vals = [presence_coverage(anchor, slot, best_tower.range, pr, pts)
                    for pr in per_run if pr.sum() > 0.0]
            if vals:
                arr = np.array(vals, dtype=float)
                rec.per_run_min = float(arr.min())
                rec.per_run_max = float(arr.max())
                rec.per_run_std = float(arr.std())
        out.append(rec)
    return out


def legal_positions(anchor: Anchor, towers: dict[str, Tower],
                    enemies: dict[str, Enemy]) -> list[tuple[float, float]]:
    """Every integer board position an emplacement could legally occupy on an empty board.

    Asks the engine's own `Sim._placement_reason()` rather than reimplementing bounds,
    lane standoff and footprint overlap — PLC-02's predicate is the definition of legal and
    a second copy of it here would be the drift this project keeps paying for. The lattice
    is integer because `data/schema/anchor.schema.json` still types a slot coordinate as
    `"integer"` (LF-219) — the rules accept floats, the schema does not, so an integer
    lattice is what an author could actually write today.
    """
    avail = sorted(t.id for t in towers.values() if t.unlocked_at <= anchor.id)
    probe = Sim(anchor, towers, enemies, standard_policies(avail)[0], "standard")
    probe.placed = []
    w, h = anchor.grid
    return [(float(x), float(y)) for x in range(w) for y in range(h)
            if probe._placement_reason(float(x), float(y)) == Sim.REASON_OK]


def board_presence_ceiling(anchor: Anchor, towers: dict[str, Tower],
                           enemies: dict[str, Enemy], profile: np.ndarray) -> dict[str, Any]:
    """What presence-weighted coverage this board ADMITS, against which the authored slots
    are then judged.

    `verdict()`'s reference point, and the reason its (a) branch needs no absolute
    threshold on presence. "The authored slots score 25%" means nothing on its own —
    presence share is not calibrated to uptime and a lane that spreads its wave thin may
    admit no better number anywhere. "25% where the board's own slot budget could have
    reached 60%" is a siting statement; "25% where the board tops out at 27%" is a range or
    wave-table statement. Same evidence, opposite diagnosis, and the old label — which had
    only one number, and the wrong one — could not tell them apart.

    Two figures, and `top_n_median` is the one to compare against:

      - `best`: the single strongest legal position. An upper bound, and an outlier by
        construction.
      - `top_n_median`: the median of the N strongest legal positions, where N is how many
        slots this anchor actually authors. Like for like — a median of N against a median
        of N — which `best` is not. Overlap is not a confound: the lattice is integer, so
        every pair of candidates is at least 1.0 tiles apart and `FOOTPRINT_RADIUS` is 0.45,
        meaning the top N are always mutually placeable.

    Also the scoring function PLC-04's candidate lattice needs — it is exactly this argmax,
    which is why it lives here rather than in the grader.
    """
    avail = [t for t in unlocked_for(towers, anchor.id) if t.is_weapon]
    if not avail:
        return {"best": 0.0, "top_n_median": 0.0, "position": None, "tower": None,
                "n_legal": 0, "n_slots": len(anchor.slots)}
    pts = bucket_points(anchor, profile.shape[1])
    best_rng = max(t.range for t in avail)
    best_tower = next(t.id for t in avail if t.range == best_rng)
    lattice = legal_positions(anchor, towers, enemies)
    scored = sorted(((presence_coverage(anchor, pos, best_rng, profile, pts), pos)
                     for pos in lattice), key=lambda sp: -sp[0])
    if not scored:
        return {"best": 0.0, "top_n_median": 0.0, "position": None, "tower": best_tower,
                "n_legal": 0, "n_slots": len(anchor.slots)}
    n = max(1, min(len(scored), len(anchor.slots)))
    return {"best": scored[0][0],
            "top_n_median": float(np.median([v for v, _ in scored[:n]])),
            "position": scored[0][1], "tower": best_tower,
            "n_legal": len(lattice), "n_slots": len(anchor.slots)}


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
        # Popped before `super()`, which is `Sim` and knows nothing about it. Exists so
        # `--selftest` can measure the same run at 50, 200 and 800 buckets and assert the
        # metric does not depend on the resolution — a bucket count that changed the answer
        # would be a tuning knob, and `presence_coverage()` claims it is not one.
        buckets = int(kwargs.pop("presence_buckets", PRESENCE_BUCKETS))
        super().__init__(*args, **kwargs)
        self.emplacement_stats: dict[int, EmplacementStats] = {}
        self._current_p: Placed | None = None
        self.presence_buckets = buckets
        # (lanes, buckets) live unit-ticks. Filled by the same post-tick walk that records
        # uptime, from the same `u.alive`/`u.dist`/`u.lane` reads — so the presence profile
        # and the uptime it is meant to predict are measured on exactly the same ticks of
        # exactly the same run, and cannot drift apart.
        self.presence = np.zeros((len(self.a.lanes), buckets), dtype=float)

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
        for u in self.units:
            if not u.alive:
                continue
            length = self.a.lanes[u.lane].path_length
            if length <= 0.0:
                continue
            b = int(u.dist / length * self.presence_buckets)
            self.presence[u.lane][min(self.presence_buckets - 1, max(0, b))] += 1.0
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
                     policy: Policy, difficulty: str,
                     presence_buckets: int = PRESENCE_BUCKETS) -> tuple:
    """One instrumented run: its `Outcome`, its per-emplacement stats, its presence profile."""
    sim = InstrumentedSim(anchor, towers, enemies, policy, difficulty,
                          presence_buckets=presence_buckets)
    outcome = sim.run()
    return outcome, list(sim.emplacement_stats.values()), sim.presence


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
    per_run: list[np.ndarray] = []
    for diff in diffs:
        for policy in policies:
            outcome, stats, presence = run_instrumented(anchor, towers, enemies, policy, diff)
            per_run.append(presence)
            total_dealt = sum(s.damage_dealt for s in stats) or 1.0
            for s in stats:
                samples.append({
                    "policy": policy.name, "difficulty": diff, "slot": s.slot,
                    "tower_id": s.tower_id, "uptime_combat": s.uptime_combat,
                    "uptime_total": s.uptime_total, "damage_dealt": s.damage_dealt,
                    "damage_share": s.damage_dealt / total_dealt, "kills": s.kills,
                    "ticks_combat": s.ticks_combat, "won": outcome.won,
                })

    # Pooled over every cell that was going to run anyway, so the presence half adds no
    # simulation — only the post-hoc scoring below. Pooling is not an assumption: the
    # per-run spread it hides is measured and reported (`SlotPresence.per_run_*`, and the
    # `presence_spread` summary column), so a claim that one profile per anchor is
    # meaningful is falsifiable from the output rather than asserted here.
    profile = sum(per_run) if per_run else np.zeros((len(anchor.lanes), PRESENCE_BUCKETS))
    presence = presence_report(anchor, towers, profile, per_run)
    ceiling = board_presence_ceiling(anchor, towers, enemies, profile)

    return {"anchor": anchor.id, "act": anchor.act, "title": anchor.title,
            "range_basis": range_basis(towers),
            "geometry": [asdict(g) for g in geometry],
            "presence": [asdict(p) for p in presence],
            "board_presence_ceiling": ceiling,
            "presence_profile": profile.tolist(),
            "samples": samples}


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


def _near_zero_slot_rank(report: dict) -> float:
    """Where the near-zero tail SITS, as a presence rank among this anchor's own slots.

    0.0 means every near-zero emplacement was built on the worst-covered slot the anchor
    authors; 1.0 means the tail is on its best. This is what separates (a) from (c) without
    an absolute threshold: a tail on the anchor's worst slots is a siting problem, and the
    same-sized tail on its best slots is not — the slot reaches the live wave and the
    emplacement still did nothing, which points at the build policy or the wave table.
    Returns NaN when there is no tail to place.
    """
    pres = {tuple(p["slot"]): p["best_presence"] for p in report.get("presence", [])}
    if not pres:
        return float("nan")
    order = sorted(pres.values())
    ranks = []
    for s in report["samples"]:
        if s["ticks_combat"] <= 0 or s["uptime_combat"] >= NEAR_ZERO_UPTIME:
            continue
        v = pres.get(tuple(s["slot"]))
        if v is None:
            continue
        # fraction of this anchor's slots this one covers at least as much live wave as
        below = sum(1 for o in order if o < v)
        ranks.append(below / max(1, len(order) - 1))
    return float(np.median(ranks)) if ranks else float("nan")


def summarize(report: dict) -> dict:
    uptimes = [s["uptime_combat"] for s in report["samples"] if s["ticks_combat"] > 0]
    shares = [s["damage_share"] for s in report["samples"] if s["ticks_combat"] > 0]
    near_zero = sum(1 for u in uptimes if u < NEAR_ZERO_UPTIME)
    ceiling = [g["best_coverage"] for g in report["geometry"]]
    ceiling_2x = [g["best_coverage_2x_range"] for g in report["geometry"]]
    pres = [p["best_presence"] for p in report.get("presence", [])]
    pres_2x = [p["best_presence_2x_range"] for p in report.get("presence", [])]
    spread = [p["per_run_max"] - p["per_run_min"] for p in report.get("presence", [])]
    board = report.get("board_presence_ceiling") or {"best": 0.0}
    return {
        "anchor": report["anchor"], "act": report["act"], "title": report["title"],
        "uptime_combat": _quantiles(uptimes),
        "damage_share": _quantiles(shares),
        "near_zero_frac": near_zero / len(uptimes) if uptimes else 0.0,
        "near_zero_n": near_zero, "sample_n": len(uptimes),
        "geometric_ceiling": _quantiles(ceiling),
        "geometric_ceiling_2x_range": _quantiles(ceiling_2x),
        "presence_ceiling": _quantiles(pres),
        "presence_ceiling_2x_range": _quantiles(pres_2x),
        "presence_spread": _quantiles(spread),
        "board_presence_ceiling": board,
        # Median authored-slot presence over the median of the N best legal positions,
        # N = this anchor's slot count. This ratio, never an absolute presence number, is
        # what `verdict()` calls (a) on — see its docstring and `board_presence_ceiling()`.
        "siting_efficiency": (_quantiles(pres)["p50"] / board["top_n_median"]
                              if board.get("top_n_median") else 0.0),
        "near_zero_slot_rank": _near_zero_slot_rank(report),
        # Carried through so a summary is never separated from the ranges it describes.
        "range_basis": report.get("range_basis"),
    }


# `verdict()`'s three thresholds. All round fractions with a stated meaning, not fitted
# constants: the classification is deliberately coarse and the table's own columns, not the
# letter, are what a reader should act on. See `verdict()`.
NEAR_ZERO_TAIL_OK = 0.10       # under one emplacement in ten below NEAR_ZERO_UPTIME
BOARD_CEILING_LOW = 0.75       # the board's own N best positions median under three quarters
SITING_EFFICIENT = 0.75        # authored slots within a quarter of that same budget


def verdict(summary: dict) -> str:
    """(a)/(b)/(c) call for one anchor, from the numbers alone.

    Rewritten for LF-217. The old version asked one question — "is `lane_coverage()` high?"
    — and it asked it of the wrong quantity. `lane_coverage()` measures lane LENGTH covered,
    which is a true statement about the level and a poor predictor of whether an emplacement
    does any work: measured across all 24 anchors on the current board, per-(slot, tower)
    lane coverage ranks against measured mean uptime at Spearman **+0.52** whole-game and
    **+0.44 mean within an anchor**, going *negative* on anchor-02, -10 and -13 — i.e. on
    three anchors it ordered their own slots backwards. Presence-weighted coverage ranks at
    **+0.75** and **+0.77** on the same pairs, and is positive on all 24. So the sentence
    "(a) geometry: slots are far from the lane" was being printed off a number that did not
    support it, and printed it for anchor-01 — the best-sited anchor in the game, 83% median
    uptime and a 5% tail.

    The four questions now asked, in this order, each against evidence that supports it:

      (b) **range / wave table** — `board_presence_ceiling.top_n_median` under
          `BOARD_CEILING_LOW`. The N best legal positions on this board, N = the anchor's own
          slot count, still cannot median above three quarters of the live wave. No siting
          fixes that; the lever is weapon range or the wave table. Independently corroborated:
          the four anchors this selects are the four with the lowest ceiling in the game, and
          LF-187 already says range must be re-derived from lane length.

      (0) **Is there a tail at all?** `near_zero_frac` under `NEAR_ZERO_TAIL_OK`. Nothing to
          diagnose; say so rather than manufacturing a cause. Thirteen anchors land here —
          06, 07, 12, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, at range basis `8189779cd568`.
          LF-217 wrote "twelve" and it had already rotted by LF-229; a count in prose here is
          only true at one range basis and one slot layout, so read the table, not this line.

      (a) **siting** — `siting_efficiency` under `SITING_EFFICIENT`. The board admits much
          better positions than the authored slots use, so moving slots is the lever. Note
          this is a claim about the slots relative to *this* board, which is why it is
          stated as a ratio and never as an absolute presence number.

      (c) **not geometry** — a tail survives on slots that are near the board's own ceiling.
          Build order, timing or the wave table, not where the slots are. This is exactly
          LF-218's finding on the re-sited anchors: re-siting moves guns into the live zone
          but cannot lengthen it, so the tail migrates instead of vanishing.

    **Why (b) is tested FIRST, and why (a) is deliberately NOT — LF-229.**

    LF-217 wrote these four branches with the tail test in front, and that ordering had a
    hole big enough to hide the worst board this project can currently generate. Measured on
    a `tools/genboard.py` 48x48 four-lane board (scratchpad, never `data/`): presence p50
    **6.7%**, board `top_n_median` **11.9%**, median uptime **18%**, and a near-zero fraction
    of **8.7%** — just under `NEAR_ZERO_TAIL_OK`. So `(0)` fired and the summary read *"no
    material near-zero tail (9% of emplacements)"* for a board on which the best 78 legal
    positions in existence reach an eighth of the live wave. `(b)` would have caught it by a
    factor of six and never ran.

    The mechanism is that `NEAR_ZERO_UPTIME` is an **absolute** 0.10, so the tail measure
    only sees a *bimodal* board — some emplacements idle, others working. A board that is
    uniformly mediocre has no tail to find (p10 uptime 11%, barely above the threshold) and
    is nonetheless broken everywhere. "The board admits nothing better anywhere" is a
    strictly stronger statement than "some emplacements idle" and does not need a tail to be
    true, so it is tested unconditionally.

    `(a)` stays **after** `(0)`, and that is a decision rather than an oversight. It is a
    *ratio* against the board's own ceiling — headroom, not outcome — and headroom is only
    worth naming when something is actually going wrong. `(b)` is an absolute claim that no
    outcome is reachable at all; `(0)` is a claim about outcomes; `(a)` is a claim about what
    could have been better. Outcomes rank above headroom. Measured consequence of the
    alternative, on the 24 shipped anchors at range basis `8189779cd568`: promoting `(a)`
    above `(0)` relabels **twelve** of them — 06, 07, 12, 15, 17, 18, 19, 20, 21, 22, 23, 24
    — from "no material tail" to "(a) siting", on boards whose emplacements are all doing
    work. Promoting `(b)` alone relabels **none**: the four anchors under `BOARD_CEILING_LOW`
    (02 at 68%, 03 at 65%, 04 at 50%, 05 at 42% — the backlog's "all 24 are 75-95%" is wrong,
    and this is the number) already carry tails of 19-31% and were already reaching `(b)`.

    The general shape is the one decision 078 records for the firing-arc branch and `LF-226`
    for the autobuild fallback: **a check that only ever runs over content which passes says
    nothing about content that does not.** All 24 shipped anchors are far above the ceiling
    threshold, so no shipped anchor could ever have exercised this ordering.

    **Two comparability warnings, both measured rather than reasoned about.**

    The label is RELATIVE to whatever ranges are in `data/towers.json` when it runs (LF-186,
    measured during decision 074's range pass): raising every weapon's base range moves both
    sides of every comparison here, so a label only means the same thing at the same
    `range_basis.digest` — recorded alongside every report for exactly that reason.

    It is also relative to the SLOT LAYOUT, which the old label was not. Presence is measured
    on the anchor's own authored board (see `presence_coverage()`), so re-siting an anchor
    changes both the metric and the reference it is compared against. That is the correct
    behaviour for the question being asked — "given how this level is played now, where is
    the wave alive" — but it means a presence number from before a siting pass and one from
    after are two different measurements, not a before/after of one.

    The thresholds are round fractions, and the classification is coarse near each of them:
    anchor-11 at 0.72 siting efficiency and anchor-08 at 0.78 straddle `SITING_EFFICIENT`
    and read as different letters while being two points apart on every other column. Read
    the table; the letter is a pointer to which column to read, not a result.
    """
    tail = summary["near_zero_frac"]
    board = summary.get("board_presence_ceiling") or {}
    top_n = board.get("top_n_median", 0.0)
    eff = summary.get("siting_efficiency", 0.0)
    # (b) first, unconditionally: a low ceiling is a fact about the board that no other
    # column can contradict, and it is the one branch a near-zero tail can hide (LF-229).
    if top_n < BOARD_CEILING_LOW:
        return (f"(b) range/waves: the board's own best {board.get('n_slots', '?')} positions "
                f"reach only {top_n:.0%} of the live wave")
    if tail < NEAR_ZERO_TAIL_OK:
        return f"no material near-zero tail ({tail:.0%} of emplacements)"
    if eff < SITING_EFFICIENT:
        return (f"(a) siting: authored slots reach {eff:.0%} of what this board's "
                f"slot budget admits")
    return (f"(c) not geometry: a {tail:.0%} tail survives on slots at {eff:.0%} of the "
            f"board's ceiling")


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
    #
    # The probe anchor's GRID is widened along with its slot list, and that is not
    # cosmetic. This check was written before PLC-02 and was silently RED on `main` when
    # LF-217 came to it: `Sim._placement_reason()` now answers `out_of_bounds` for a
    # position off the authored grid, so the scheduled build never landed and the check
    # failed with "the scheduled build never landed" rather than with anything about
    # coverage. Nothing caught it because `tools/check.py` does not run this selftest at
    # all — no tier does, which is why a rules change in a different workstream could turn
    # this file's own proof-of-no-side-effects red and nobody find out for two merges.
    # A synthetic 40x40 grid keeps the probe legal while leaving it 25 tiles
    # from the nearest lane point — still exactly zero coverage at the longest range in the
    # game, which is the property the check is about. `levels` is deliberately left at the
    # authored size: no rule reads it (TER-02), and growing it would be inventing terrain.
    far_slot = (30.0, 30.0)
    towers = load_towers()
    enemies = load_enemies()
    far_anchor = dc_replace(anchor01, grid=(40, 40), slots=anchor01.slots + (far_slot,))
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
    outcome, stats, _ = run_instrumented(far_anchor, towers, enemies, far_policy, "standard")
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

    # 2b. The presence metric's own hard equalities and its resolution independence.
    #
    # `presence_coverage()` claims two things that would be assumptions if they were not
    # checked here. First, that it is a SHARE: a slot no unit can ever be within range of
    # covers exactly 0.0 of the live unit-ticks, and a range large enough to contain the
    # whole board covers exactly 1.0 — both exact, not "near", because the sum is over the
    # same array in both the numerator and the denominator. Second, that PRESENCE_BUCKETS
    # is not a tuning knob: the same run measured at 200 and at 800 buckets must agree, or
    # the metric could be moved by choosing a resolution, which is the fitting move decision
    # 067 rejected. 0.02 is the tolerance because the measured worst-slot disagreement at
    # 200 buckets against a 1600-bucket reference is 0.012 (anchor-01) and 0.004
    # (anchor-13) — see PRESENCE_BUCKETS. Deliberately NOT run at 50: 50 is measurably
    # unconverged (0.052) and asserting it would be asserting something false.
    a01_pol = next(pol for pol in standard_policies(sorted(
        t.id for t in towers.values() if t.unlocked_at <= "anchor-01")) if pol.name == "cheap-mass")
    profs = {n: run_instrumented(anchor01, towers, enemies, a01_pol, "standard",
                                 presence_buckets=n)[2] for n in (200, 800)}
    p200 = profs[200]
    if p200.sum() <= 0.0:
        print("FAIL presence profile: no live unit-ticks recorded — test is vacuous")
        ok = False
    else:
        pres_far = presence_coverage(anchor01, far_slot, 6.5, p200)
        pres_all = presence_coverage(anchor01, (0.0, 0.0), 1.0e6, p200)
        if pres_far != 0.0 or pres_all != 1.0:
            print(f"FAIL presence share bounds: far slot -> {pres_far} (want exactly 0.0), "
                  f"whole board -> {pres_all} (want exactly 1.0)")
            ok = False
        else:
            print(f"ok   presence share bounds: far slot 0.0, whole board 1.0 over "
                  f"{int(p200.sum())} live unit-ticks")
        worst = max(abs(presence_coverage(anchor01, slot, 4.0, profs[200])
                        - presence_coverage(anchor01, slot, 4.0, profs[800]))
                    for slot in anchor01.slots)
        if worst > 0.02:
            print(f"FAIL presence bucket independence: worst |200 buckets - 800 buckets| is "
                  f"{worst:.4f} over {len(anchor01.slots)} slots (tolerance 0.0200)")
            ok = False
        else:
            print(f"ok   presence bucket independence: worst |200 - 800 buckets| {worst:.4f} "
                  f"over {len(anchor01.slots)} slots (tolerance 0.0200)")

    # 3. Determinism: run the same (anchor, policy, difficulty) twice, compare byte-for-byte.
    policies = standard_policies(sorted(t.id for t in towers.values()
                                        if t.unlocked_at <= "anchor-09"))
    a09 = load_anchor("anchor-09")
    p = next(pol for pol in policies if pol.name == "burst")
    o1, s1, pr1 = run_instrumented(a09, towers, enemies, p, "hard")
    o2, s2, pr2 = run_instrumented(a09, towers, enemies, p, "hard")
    d1 = sorted((s.slot, s.tower_id, s.ticks_total, s.ticks_combat, s.ticks_in_range,
                s.damage_dealt, s.kills) for s in s1)
    d2 = sorted((s.slot, s.tower_id, s.ticks_total, s.ticks_combat, s.ticks_in_range,
                s.damage_dealt, s.kills) for s in s2)
    if o1.as_dict() != o2.as_dict() or d1 != d2 or not np.array_equal(pr1, pr2):
        print("FAIL determinism: two runs of the same inputs disagreed")
        ok = False
    else:
        print(f"ok   determinism: anchor-09/burst/hard identical across two runs "
              f"({len(d1)} emplacements, {sum(s.damage_dealt for s in s1):.1f} total damage, "
              f"{int(pr1.sum())} live unit-ticks)")

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

    # 5. `verdict()` branch ORDER, on synthetic summaries. Free — no simulation at all — and
    # it is the only thing standing between this file and a repeat of LF-229, where the
    # near-zero tail test sat in front of the board-ceiling test and reported a 48x48
    # generated board with an 11.9% ceiling as healthy. It cannot be caught by running the
    # instrument over shipped content: no shipped anchor combines a sub-0.75 ceiling with a
    # sub-10% tail, which is exactly the "a gate that runs the whole game proves nothing
    # about a branch the shipped data never enters" lesson decision 078 already paid for.
    # The numbers in the first case are the measured ones from that generated board.
    def summ(tail: float, top_n: float, eff: float) -> dict:
        return {"near_zero_frac": tail, "siting_efficiency": eff,
                "board_presence_ceiling": {"top_n_median": top_n, "n_slots": 78}}

    verdict_cases = [
        # (name, summary, required substring)
        ("low ceiling under a silent tail (LF-229)", summ(0.087, 0.119, 0.57), "(b) range/waves"),
        ("low ceiling AND efficient siting",         summ(0.30,  0.119, 0.95), "(b) range/waves"),
        ("healthy ceiling, no tail",                 summ(0.05,  0.90,  0.52), "no material"),
        ("healthy ceiling, tail, poor siting",       summ(0.30,  0.90,  0.52), "(a) siting"),
        ("healthy ceiling, tail, good siting",       summ(0.30,  0.90,  0.95), "(c) not geometry"),
    ]
    for name, s, want in verdict_cases:
        got = verdict(s)
        if want not in got:
            print(f"FAIL verdict ordering [{name}]: want {want!r}, got {got!r}")
            ok = False
        else:
            print(f"ok   verdict ordering [{name}] -> {got}")

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
        board = r.get("board_presence_ceiling") or {}
        # Sorted by PRESENCE, not by lane coverage: the two disagree, that disagreement is
        # the whole of LF-217, and printing them side by side in presence order is what
        # makes it visible per slot rather than only in the whole-game correlation.
        print(f"\n{r['anchor']}  {r['title']}  ·  act {r['act']}  ·  per-slot coverage\n")
        print(f"  {'slot':>12s}  {'best tower':<20s} {'presence':>8s} {'@2x':>6s} "
              f"{'lane':>6s} {'@2x':>6s}  {'per-run lo..hi':>16s}")
        for p in sorted(r.get("presence", []), key=lambda p: -p["best_presence"]):
            g = next((g for g in r["geometry"] if tuple(g["slot"]) == tuple(p["slot"])), None)
            print(f"  {tuple(p['slot'])!s:>12s}  {p['best_tower']:<20s} "
                  f"{p['best_presence']:>8.1%} {p['best_presence_2x_range']:>6.1%} "
                  f"{(g['best_coverage'] if g else 0.0):>6.1%} "
                  f"{(g['best_coverage_2x_range'] if g else 0.0):>6.1%}  "
                  f"{p['per_run_min']:>7.1%}..{p['per_run_max']:<7.1%}")
        if board.get("position"):
            print(f"\n  board's own ceiling: best legal position {tuple(board['position'])} "
                  f"reaches {board['best']:.1%} of the live wave with {board['tower']}; "
                  f"median of its best {board['n_slots']} positions "
                  f"{board['top_n_median']:.1%} ({board['n_legal']} legal positions)")

    # Printed, not just stored: the (a)/(b) verdict below is defined relative to these
    # ranges, so a reader comparing this table against an older one needs to see whether
    # the basis moved. LF-186 — a range pass silently reclassified four anchors.
    if summaries and summaries[0].get("range_basis"):
        rb = summaries[0]["range_basis"]
        print(f"\nrange basis {rb['digest']} — "
              + ", ".join(f"{k} {v}" for k, v in rb["weapon_ranges"].items())
              + "\n  (verdicts below are RELATIVE to these; only comparable to a run with "
                "the same digest)")

    # `presence p50` sits immediately left of `lane p50` on purpose: the two columns
    # disagree, the presence one is what the uptime column tracks, and putting them
    # adjacent is what makes an inversion legible without re-deriving it (LF-217).
    print(f"\n{'anchor':<10s} {'act':>3s} {'presence':>8s} {'lane':>6s} {'board':>6s} "
          f"{'site-eff':>8s} {'uptime p10/p50/p90':>19s} {'dmg-share p10/p50/p90':>22s} "
          f"{'near-zero':>9s}  verdict")
    for s in summaries:
        u = s["uptime_combat"]
        d = s["damage_share"]
        nz = f"{s['near_zero_n']}/{s['sample_n']}"
        board = s.get("board_presence_ceiling") or {}
        print(f"{s['anchor']:<10s} {s['act']:>3d} {s['presence_ceiling']['p50']:>8.1%} "
              f"{s['geometric_ceiling']['p50']:>6.1%} "
              f"{board.get('top_n_median', 0.0):>6.1%} {s['siting_efficiency']:>8.2f} "
              f"{u['p10']:>5.0%}/{u['p50']:>5.0%}/{u['p90']:>5.0%}  "
              f"{d['p10']:>6.1%}/{d['p50']:>6.1%}/{d['p90']:>6.1%}  {nz:>9s}  "
              f"{verdict(s)}")
    print("  presence = median authored-slot share of LIVE UNIT-TICKS covered; "
          "lane = median share of lane LENGTH covered (`lane_coverage()`);")
    print("  board = median of the N best legal positions, N = slot count; "
          "site-eff = presence / board. Presence is measured on THIS slot layout.")

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
