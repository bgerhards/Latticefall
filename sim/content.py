"""
Load Latticefall game data into simulation structures.

Separate from the engine on purpose: the engine should be given plain values and
have no idea where they came from, so a balance experiment can construct an anchor
in memory without writing a file.

Path geometry lives here too. An anchor carries 1-5 lanes (`Anchor.lanes`), each an
axis-aligned polyline; the simulation needs distance-along-lane, so it is precomputed
once per lane rather than recomputed every tick. WAR-01: a lane is addressed everywhere
in the rules by its integer INDEX into `Anchor.lanes` -- stable, orderable, identical in
both languages -- never by its `id`, which is authoring/dialog/HUD-only. See
docs/DECISIONS.md's WAR-01 entry.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@dataclass(frozen=True)
class Tower:
    id: str
    name: str
    cost: int
    draw_mw: float
    damage: float
    range: float
    fire_interval: float
    targets: frozenset[str]
    splash: float = 0.0
    unlocked_at: str = "anchor-01"
    effect_type: str | None = None
    effect_value: float = 0.0
    # BAL-01: the raw `upgrade` sub-document from towers.json (cost plus whichever stat
    # overrides that tower's upgrade carries — draw_mw/damage/range/fire_interval/splash/
    # effect, never targets today), or None for a tower with no upgrade block. Kept raw
    # rather than pre-split into typed fields because the override set genuinely varies by
    # tower (see the `upgrade()` verb in engine.py, which is the one and only consumer).
    upgrade: dict | None = None
    # PLC-03: an optional firing arc, authored as the COSINE of the half-angle and a
    # facing vector in tile space. `None` means omnidirectional, which is every one of
    # the nine shipped rows — the arc path is inert until data asks for it. Never an
    # angle: `atan2`/`sin`/`cos`/`tan`/`pow`/`log`/`exp` all diverge between Windows
    # Godot (MSVC UCRT) and CPython/Linux Godot on 0.03–4.32% of float64 samples
    # (LF-105), and the owner plays the Windows build. See Sim._arc_gate() for the test.
    # `facing` need not be normalised; the test folds |facing|² in rather than taking a
    # square root. Mirrors the raw keys of the same names in data/towers.json, which is
    # also what scripts/anchor_sim.gd reads (its tower defs are the JSON rows verbatim).
    cos_half_angle: float | None = None
    facing: tuple[float, float] = (0.0, 0.0)

    @property
    def is_weapon(self) -> bool:
        return self.damage > 0


@dataclass(frozen=True)
class Enemy:
    id: str
    name: str
    faction: str
    hp: float
    speed: float
    bounty: int
    kind: str
    armour: float = 0.0
    shielded: bool = False
    drains_mw: float = 0.0
    ## Lives lost when this unit reaches the anchor. See decision 047: a flat cost of 1
    ## made density and leak-tension the same axis, so Act III could only get more units on
    ## screen by becoming proportionally more forgiving.
    leak_cost: int = 1


@dataclass(frozen=True)
class Spawn:
    enemy: str
    count: int
    interval: float = 1.0
    delay: float = 0.0
    ## Index into Anchor.lanes. Defaults to 0, so a single-lane anchor's spawns need no
    ## `lane` key at all -- WAR-01's whole safety argument for the migration.
    lane: int = 0


@dataclass(frozen=True)
class Wave:
    spawns: tuple[Spawn, ...]
    lead_in: float = 20.0

    @property
    def total_units(self) -> int:
        return sum(s.count for s in self.spawns)


@dataclass(frozen=True)
class Lane:
    """One enemy path. WAR-01: an anchor carries 1-5 of these (`Anchor.lanes`), and every
    rule that used to read the single `path`/`path_length`/`point_at()` now takes a lane
    INDEX and reads through here instead.

    `seg_len`/`cum_len`/`path_length` are precomputed once, by `Lane.build()`, rather than
    recomputed every tick -- the same reasoning that used to live on `Anchor` itself before
    multi-lane split it out. Frozen, like every other content record: nothing downstream may
    mutate a lane in place.
    """
    id: str
    waypoints: tuple[tuple[float, ...], ...]
    seg_len: tuple[float, ...]
    cum_len: tuple[float, ...]
    path_length: float

    @staticmethod
    def build(lane_id: str, waypoints: tuple[tuple[float, ...], ...]) -> "Lane":
        segs, cum, total = [], [0.0], 0.0
        for a, b in zip(waypoints, waypoints[1:]):
            # axis-aligned, so manhattan == euclidean (decision 030) -- only x/y (indices
            # 0/1) ever enter this arithmetic; a waypoint's optional third element (z, the
            # elevation LEVEL — TER-01) is carried on the tuple and never read here.
            d = abs(b[0] - a[0]) + abs(b[1] - a[1])
            segs.append(d)
            total += d
            cum.append(total)
        return Lane(id=lane_id, waypoints=waypoints, seg_len=tuple(segs),
                    cum_len=tuple(cum), path_length=total)

    def point_at(self, dist: float) -> tuple[float, float]:
        """World position `dist` tiles along this lane."""
        if dist <= 0:
            return self.waypoints[0][0], self.waypoints[0][1]
        if dist >= self.path_length:
            return self.waypoints[-1][0], self.waypoints[-1][1]
        # linear scan: a lane is a handful of segments, so this beats bisect overhead
        for i, seg in enumerate(self.seg_len):
            if dist <= self.cum_len[i + 1]:
                t = (dist - self.cum_len[i]) / seg if seg else 0.0
                ax, ay = self.waypoints[i][0], self.waypoints[i][1]
                bx, by = self.waypoints[i + 1][0], self.waypoints[i + 1][1]
                return (ax + (bx - ax) * t, ay + (by - ay) * t)
        return self.waypoints[-1][0], self.waypoints[-1][1]


@dataclass
class Anchor:
    id: str
    act: int
    title: str
    capacity_mw: float
    starting_funds: int
    lives: int
    grid: tuple[int, int]
    lanes: tuple[Lane, ...]
    # PLC-01: float64, not int — Placed.x/y (sim/engine.py) are continuous positions.
    # PLC-02's `_is_placeable()` no longer reads this tuple at all (legality is now the
    # real bounds/lane/overlap predicate); `_slot_priority()` and `_effective_cap()`'s
    # fallback still do, so both sides of every comparison against them must stay the
    # same type. `float(x)` on an authored integer is exact (every one of today's 24
    # anchors), matching the loader's existing idiom for `waypoints` above.
    slots: tuple[tuple[float, float], ...]
    waves: tuple[Wave, ...]
    tutorial: bool = False
    # Act III: MW the bus loses at the start of every wave after the first. The reactor
    # is not failing — something else is drawing on it. A build that is exactly right on
    # wave one is over capacity by wave five, so the player's mastery of the power system
    # is what stops working. Decision 031.
    capacity_decay_mw: float = 0.0
    # TER-02: dense row-major level grid, levels[y][x], resolved once by resolve_terrain()
    # at load time. An anchor with no `terrain` key resolves to an all-zero grid at
    # grid.w x grid.h, so every downstream consumer (height_at() below; density.py) has one
    # code path regardless of whether the anchor declares any relief. Nothing in the rules
    # reads this yet by design — see resolve_terrain()'s docstring and TER-02's "risks"
    # section: letting terrain reach the engine is a later, owner-gated issue (TER-13).
    levels: tuple[tuple[int, ...], ...] = field(default=())
    # LF-152/decision 063: the board-saturation invariant's denominator once `slots` is
    # deleted by free placement (PLC-01/PLC-05). None means "not authored" — every one
    # of the 24 real anchors today, all of which still author `slots` — and the engine
    # falls back to `len(slots)`, reproducing today's numbers exactly (Sim._effective_cap()
    # in sim/engine.py; the schema's own root `anyOf` already requires this field whenever
    # `slots` is absent, so a None here with an empty `slots` only happens for content the
    # validator would already have rejected).
    max_emplacements: int | None = None
    # PLC-02: half the standoff width kept between the path polyline and an emplacement's
    # footprint — see Sim._placement_reason()'s own docstring for the full test. Defaults
    # to 0.5 (today's single-tile lane), matching the schema default, so every one of the
    # 24 anchors that omits this key resolves to the standoff the fixed-slot game always
    # implied.
    lane_half_width: float = 0.5
    # PLC-04: the grader's candidate lattice, memoised here rather than recomputed.
    # `None` means "not built yet"; `Sim.__init__()` fills it on first use via
    # `sim.engine.build_candidate_lattice()`, which is where it is DEFINED — the lattice
    # needs FOOTPRINT_RADIUS, a rules constant that lives in the engine, and importing
    # the engine from here would be a cycle. It is cached on the Anchor rather than on
    # the Sim because a grading pass builds up to 60 Sims per anchor (20 policies x 3
    # difficulties) off one loaded Anchor and the lattice is a pure function of board
    # geometry: grid, lane waypoints and lane_half_width, none of which any caller
    # mutates. `dataclasses.replace()` (tools/sweep.py) carries it across candidates
    # untouched, which is correct precisely because the sweep varies capacity, funds,
    # lives and waves and never the geometry — if a future caller ever does vary the
    # geometry through `replace()`, it must pass `lattice=None` with it.
    # Rows are `(x, y, lane, arclength)`: the position, the lane whose distance to it is
    # smallest, and the arclength of that lane's closest point. The last two are what
    # `Sim._try_build()`'s round-robin-across-lanes / maximin-within-a-lane consumption
    # rule walks; they come out of the same traversal that computes the distance, never a
    # second one.
    lattice: tuple[tuple[float, float, int, float], ...] | None = None

    def point_at(self, lane: int, dist: float) -> tuple[float, float]:
        return self.lanes[lane].point_at(dist)

    def path_length(self, lane: int) -> float:
        # A bare property here (as `path_length` used to be, pre-multi-lane) would have
        # to pick a lane silently — exactly the "defaulted lane reads lane 0 forever" trap
        # WAR-01 calls out for point_at(). Deleted rather than kept meaning lane 0.
        return self.lanes[lane].path_length

    def height_at(self, tx: int, ty: int) -> int:
        """Terrain level at tile (tx, ty), clamped to the board edge.

        Clamped rather than raising: a caller working from a world position derived by
        float math (point_at(), a projectile arc) can land a fraction outside the last
        tile by rounding, and treating the board edge as infinitely tall/flat there is
        the same choice `point_at` already makes for distance past the path's ends.
        """
        h = len(self.levels)
        if h == 0:
            return 0
        w = len(self.levels[0])
        if w == 0:
            return 0
        cx = min(max(int(tx), 0), w - 1)
        cy = min(max(int(ty), 0), h - 1)
        return self.levels[cy][cx]


def load_towers(path: Path | None = None) -> dict[str, Tower]:
    return towers_from_rows(json.loads((path or DATA / "towers.json").read_text())["towers"])


def towers_from_rows(rows: list[dict]) -> dict[str, Tower]:
    """Typed `Tower`s from already-parsed `towers.json` rows. Split out of
    `load_towers()` by PLC-03 for the same reason as `anchor_from_doc()`: the firing-arc
    fixture authors a probe row that must never ship in `data/towers.json`, and it has to
    reach the engine through this loader rather than a hand-built `Tower` that could
    drift from it."""
    out = {}
    for t in rows:
        eff = t.get("effect") or {}
        # PLC-03: absent stays absent — `cos_half_angle=None` is what makes the arc test
        # skip entirely, so a missing key must never become 1.0 or 0.0 here.
        face = t.get("facing") or (0.0, 0.0)
        out[t["id"]] = Tower(
            id=t["id"], name=t["name"], cost=t["cost"], draw_mw=t["draw_mw"],
            damage=t["damage"], range=t["range"], fire_interval=t["fire_interval"],
            targets=frozenset(t["targets"]), splash=t.get("splash", 0.0),
            unlocked_at=t.get("unlocked_at", "anchor-01"),
            effect_type=eff.get("type"), effect_value=eff.get("value", 0.0),
            upgrade=t.get("upgrade"),
            cos_half_angle=t.get("cos_half_angle"),
            facing=(float(face[0]), float(face[1])),
        )
    return out


@dataclass(frozen=True)
class Ability:
    """BAL-01/LF-163. One entry of data/tuning.json's `abilities` array, typed.
    `charge_max`/`charge_per_leak_cost` were added by LF-163: a scheduled `ability`
    verb of kind "surge" is charge-gated exactly like scripts/abilities.gd's
    `AbilityState.ready()`/`add_charge()` — a schedule entry whose time arrives before
    a full charge has accrued from kills is dispatched but has no effect, mirroring
    scripts/anchor_view.gd's `activate_ability()` returning `{}` when `not ready`.
    cooldown_s/duration_s/trauma remain HUD-timer concerns this file does not need:
    overcharge/shutter are still driven by a policy's own explicit on/off timestamps
    (standard_policies()'s scheduled-policy comments), and charge_max is 0 for both,
    so is_charge_gated()-equivalent logic below only ever engages for "surge"."""
    id: str
    damage: float = 0.0
    falloff_min: float = 1.0
    pushback_tiles: float = 0.0
    fire_rate_bonus: float = 0.0
    draw_mult: float = 1.0
    hold_tiles: float = 0.0
    draw_mw: float = 0.0
    charge_max: float = 0.0
    charge_per_leak_cost: float = 0.0


@dataclass(frozen=True)
class VeterancyRank:
    """One rank of data/tuning.json's `veterancy.ranks`, typed. See Sim._veteran_rank()."""
    kills: int
    damage_mult: float
    range_mult: float


@dataclass(frozen=True)
class Tuning:
    """BAL-01. Typed subset of data/tuning.json that sim/engine.py's rules actually
    consume: the pacing bonus a `call_wave` schedule action converts remaining lead-in
    seconds at, the three bindstone abilities, and the veterancy rank ladder. Deliberately
    NOT modelled here: `targeting.modes` (engine.py's target_mode verb takes the mode as a
    plain string in the schedule's own args, validated nowhere the rules need to trust it
    twice), `pacing.chain_*` (the kill chain bounty stays scripts/anchor_view.gd-only —
    see the report for why BAL-01 leaves it there), and `recoveries`/`grade` (neither is
    read by anything in sim/ or scripts/test/parity.gd)."""
    call_bonus_per_sec: float
    abilities: dict[str, Ability]
    veterancy_ranks: tuple[VeterancyRank, ...]


_TUNING_CACHE: "Tuning | None" = None


def load_tuning(path: Path | None = None) -> Tuning:
    """Load data/tuning.json into typed values (BAL-01's own task list). Cached when
    `path` is the default: a scheduled or veterancy-opted Policy constructs a fresh Sim
    per (anchor, difficulty) — up to 1152 times in one parity run — and the file does not
    change mid-run, so re-parsing it every time would be pure overhead."""
    global _TUNING_CACHE
    if path is None and _TUNING_CACHE is not None:
        return _TUNING_CACHE
    doc = json.loads((path or DATA / "tuning.json").read_text())
    abilities = {
        a["id"]: Ability(
            id=a["id"], damage=a.get("damage", 0.0), falloff_min=a.get("falloff_min", 1.0),
            pushback_tiles=a.get("pushback_tiles", 0.0),
            fire_rate_bonus=a.get("fire_rate_bonus", 0.0), draw_mult=a.get("draw_mult", 1.0),
            hold_tiles=a.get("hold_tiles", 0.0), draw_mw=a.get("draw_mw", 0.0),
            charge_max=a.get("charge_max", 0.0),
            charge_per_leak_cost=a.get("charge_per_leak_cost", 0.0),
        )
        for a in doc["abilities"]
    }
    ranks = tuple(
        VeterancyRank(kills=int(r["kills"]), damage_mult=float(r["damage_mult"]),
                      range_mult=float(r["range_mult"]))
        for r in doc["veterancy"]["ranks"]
    )
    tuning = Tuning(call_bonus_per_sec=float(doc["pacing"]["call_bonus_per_sec"]),
                     abilities=abilities, veterancy_ranks=ranks)
    if path is None:
        _TUNING_CACHE = tuning
    return tuning


def load_enemies(path: Path | None = None) -> dict[str, Enemy]:
    return enemies_from_rows(json.loads((path or DATA / "enemies.json").read_text())["enemies"])


def enemies_from_rows(rows: list[dict]) -> dict[str, Enemy]:
    """Typed `Enemy`s from already-parsed `enemies.json` rows — see
    `towers_from_rows()` for why this split exists (PLC-03)."""
    return {
        e["id"]: Enemy(
            id=e["id"], name=e["name"], faction=e["faction"], hp=e["hp"],
            speed=e["speed"], bounty=e["bounty"], kind=e["kind"],
            armour=e.get("armour", 0.0), shielded=e.get("shielded", False),
            drains_mw=e.get("drains_mw", 0.0),
            leak_cost=int(e.get("leak_cost", 1)),
        )
        for e in rows
    }


def resolve_terrain(doc: dict) -> tuple[tuple[int, ...], ...]:
    """Resolve an anchor doc's optional `terrain` block into a dense row-major level
    grid: `levels[y][x]`, `grid.h` rows of `grid.w` columns each.

    TER-02. This algorithm exists twice — here, and as `resolve_terrain()` in
    scripts/content.gd — implementing the same prose from data/schema/anchor.schema.json's
    `terrain` description, byte for byte. PRD-THEATRE-SCALE.md risk #10 is exactly these
    two parsers disagreeing on one tile; data/schema/fixtures/terrain-resolution.json is
    the fixture both are proved identical against, by tools/terrain_parity.py and the
    'terrain parsers agree' gate check. If either implementation changes, change the other
    the same way and re-run that check before touching anything else.

    Absent `terrain` (the case for all 24 anchors except the TER-02 pilot) resolves to an
    all-zero grid, so every downstream consumer — height_at() above, tools/density.py — has
    exactly one code path whether or not the anchor declares any relief.

    Resolution order, matching the schema:
      1. `heightmap` present -> return it verbatim (already `levels[y][x]`, row-major). It
         REPLACES `regions` entirely and is never composited with them — the schema makes
         declaring both a validation error rather than leaving a precedence rule for this
         function to invent.
      2. otherwise every tile starts at level 0, and `regions` are painted in array order:
         each region's `rect` ([x, y, w, h], half-open in w/h) completely overwrites the
         level of every tile it covers, including *lowering* it below what an earlier
         region wrote. Later region wins, full stop — this is the one sentence both
         parsers must implement identically.
    A rect that runs off the board is clipped to the grid, never wrapped and never an
    error — a generator emitting a region against the edge is the normal case, not a bug.
    `ramps` are declared metadata for a future line-of-sight / stepped-movement consumer
    (TER-06/TER-08/TER-12) and are not consulted here; they never affect the resolved grid.
    """
    w, h = doc["grid"]["w"], doc["grid"]["h"]
    terrain = doc.get("terrain")
    if not terrain:
        return tuple(tuple(0 for _ in range(w)) for _ in range(h))

    if "heightmap" in terrain:
        return tuple(tuple(int(v) for v in row) for row in terrain["heightmap"])

    grid = [[0] * w for _ in range(h)]
    for region in terrain.get("regions", []):
        rx, ry, rw, rh = region["rect"]
        z = int(region["z"])
        for y in range(max(0, ry), min(h, ry + rh)):
            row = grid[y]
            for x in range(max(0, rx), min(w, rx + rw)):
                row[x] = z
    return tuple(tuple(row) for row in grid)


def load_anchor(anchor_id: str) -> Anchor:
    return anchor_from_doc(json.loads((DATA / "anchors" / f"{anchor_id}.json").read_text()))


def anchor_from_doc(doc: dict) -> Anchor:
    """Build an `Anchor` from an already-parsed anchor document.

    Split out of `load_anchor()` by PLC-03 so a *fixture* anchor — one that lives under
    `data/schema/fixtures/` and is deliberately not tracked content — reaches the engine
    through the identical loader the 24 real anchors do, rather than through a
    second, drifting construction in a test harness. `load_anchor()` is now this plus
    the read.
    """
    return Anchor(
        id=doc["id"], act=doc["act"], title=doc["title"],
        capacity_mw=doc["capacity_mw"], starting_funds=doc["starting_funds"],
        lives=doc.get("lives", 10),
        tutorial=doc.get("tutorial", False),
        capacity_decay_mw=doc.get("capacity_decay_mw", 0.0),
        max_emplacements=doc.get("max_emplacements"),
        lane_half_width=doc.get("lane_half_width", 0.5),
        levels=resolve_terrain(doc),
        grid=(doc["grid"]["w"], doc["grid"]["h"]),
        lanes=tuple(
            Lane.build(
                lane_id=lane["id"],
                waypoints=tuple(tuple(float(v) for v in wp) for wp in lane["waypoints"]),
            )
            for lane in doc["paths"]
        ),
        # LF-152: `doc.get("slots", [])`, not `doc["slots"]` — the schema (PLC-05) already
        # allows an anchor to omit `slots` entirely once `max_emplacements` is its
        # denominator instead, and scripts/anchor_sim.gd's setup() already tolerates a
        # missing `slots` key the same way (`anchor.get("slots", [])`); this loader was the
        # one place still requiring it, which would crash rather than degrade the moment a
        # free-placement anchor existed to load.
        #
        # PLC-01: `float(x)`, not `int(x)` — see the field's own comment on Anchor.slots.
        slots=tuple((float(x), float(y)) for x, y in doc.get("slots", [])),
        waves=tuple(
            Wave(
                lead_in=w.get("lead_in", 20.0),
                spawns=tuple(
                    Spawn(enemy=s["enemy"], count=s["count"],
                          interval=s.get("interval", 1.0), delay=s.get("delay", 0.0),
                          lane=int(s.get("lane", 0)))
                    for s in w["spawns"]
                ),
            )
            for w in doc["waves"]
        ),
    )


def all_anchor_ids() -> list[str]:
    """Every anchor that is TRACKED CONTENT, sourced from `git ls-files` rather than a
    directory glob (LF-132).

    This used to be `glob("anchor-*.json")`, which meant any file dropped into
    `data/anchors/` was real content the moment it hit the disk. Measured consequence,
    paid once already: a scratch 64x64 synthetic anchor used for a draw-path benchmark
    was picked up by `tools/density.py` and broke the gate's `wave density` check
    outright -- act 1 suddenly had a 700-unit anchor as its busiest reference, and
    nothing in the failure pointed at an untracked file. `tools/genboard.py` (LF-080)
    makes that workflow -- generate a board, grade it, throw it away -- routine rather
    than accidental, so the glob had to go before the generator landed.

    Tracked-only is the same rule and the same reasoning `PRC-02` already applied to
    `tools/validate/validate_data.py`'s `discover_documents()`: a file that was never
    `git add`ed is invisible to anyone who clones the repo, so it must be invisible to
    the instruments too. `load_anchor()` is deliberately NOT changed -- it takes an id
    and reads the path, so an untracked generated board can still be loaded and graded
    by name. Discovery is the thing that must be conservative; loading by explicit
    request is not.

    Falls back to the glob if `git` is unavailable or this tree is not a repository --
    a sdist or a vendored copy still has to be able to enumerate its own content, and
    an exception here would take out every caller including the gate.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "data/anchors"], cwd=ROOT,
            capture_output=True, check=True,
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        return sorted(p.stem for p in (DATA / "anchors").glob("anchor-*.json"))
    stems = [Path(rel).stem for rel in out.split("\0")
             if rel.endswith(".json") and Path(rel).name.startswith("anchor-")]
    return sorted(stems)


def unlocked_for(towers: dict[str, Tower], anchor_id: str) -> list[Tower]:
    """Emplacements available at this anchor. Ids sort chronologically by design."""
    return [t for t in towers.values() if t.unlocked_at <= anchor_id]
