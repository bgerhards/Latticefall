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
    slots: tuple[tuple[int, int], ...]
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
    doc = json.loads((path or DATA / "towers.json").read_text())
    out = {}
    for t in doc["towers"]:
        eff = t.get("effect") or {}
        out[t["id"]] = Tower(
            id=t["id"], name=t["name"], cost=t["cost"], draw_mw=t["draw_mw"],
            damage=t["damage"], range=t["range"], fire_interval=t["fire_interval"],
            targets=frozenset(t["targets"]), splash=t.get("splash", 0.0),
            unlocked_at=t.get("unlocked_at", "anchor-01"),
            effect_type=eff.get("type"), effect_value=eff.get("value", 0.0),
        )
    return out


def load_enemies(path: Path | None = None) -> dict[str, Enemy]:
    doc = json.loads((path or DATA / "enemies.json").read_text())
    return {
        e["id"]: Enemy(
            id=e["id"], name=e["name"], faction=e["faction"], hp=e["hp"],
            speed=e["speed"], bounty=e["bounty"], kind=e["kind"],
            armour=e.get("armour", 0.0), shielded=e.get("shielded", False),
            drains_mw=e.get("drains_mw", 0.0),
            leak_cost=int(e.get("leak_cost", 1)),
        )
        for e in doc["enemies"]
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
    doc = json.loads((DATA / "anchors" / f"{anchor_id}.json").read_text())
    return Anchor(
        id=doc["id"], act=doc["act"], title=doc["title"],
        capacity_mw=doc["capacity_mw"], starting_funds=doc["starting_funds"],
        lives=doc.get("lives", 10),
        tutorial=doc.get("tutorial", False),
        capacity_decay_mw=doc.get("capacity_decay_mw", 0.0),
        levels=resolve_terrain(doc),
        grid=(doc["grid"]["w"], doc["grid"]["h"]),
        lanes=tuple(
            Lane.build(
                lane_id=lane["id"],
                waypoints=tuple(tuple(float(v) for v in wp) for wp in lane["waypoints"]),
            )
            for lane in doc["paths"]
        ),
        slots=tuple((int(x), int(y)) for x, y in doc["slots"]),
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
    return sorted(p.stem for p in (DATA / "anchors").glob("anchor-*.json"))


def unlocked_for(towers: dict[str, Tower], anchor_id: str) -> list[Tower]:
    """Emplacements available at this anchor. Ids sort chronologically by design."""
    return [t for t in towers.values() if t.unlocked_at <= anchor_id]
