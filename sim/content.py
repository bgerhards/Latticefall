"""
Load Latticefall game data into simulation structures.

Separate from the engine on purpose: the engine should be given plain values and
have no idea where they came from, so a balance experiment can construct an anchor
in memory without writing a file.

Path geometry lives here too. Anchor paths are stored as axis-aligned waypoints;
the simulation needs distance-along-path, so it is precomputed once per anchor
rather than recomputed every tick.
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


@dataclass(frozen=True)
class Wave:
    spawns: tuple[Spawn, ...]
    lead_in: float = 20.0

    @property
    def total_units(self) -> int:
        return sum(s.count for s in self.spawns)


@dataclass
class Anchor:
    id: str
    act: int
    title: str
    capacity_mw: float
    starting_funds: int
    lives: int
    grid: tuple[int, int]
    waypoints: tuple[tuple[float, float], ...]
    slots: tuple[tuple[int, int], ...]
    waves: tuple[Wave, ...]
    tutorial: bool = False
    # Act III: MW the bus loses at the start of every wave after the first. The reactor
    # is not failing — something else is drawing on it. A build that is exactly right on
    # wave one is over capacity by wave five, so the player's mastery of the power system
    # is what stops working. Decision 031.
    capacity_decay_mw: float = 0.0
    # derived
    seg_len: tuple[float, ...] = field(default=())
    cum_len: tuple[float, ...] = field(default=())

    def __post_init__(self) -> None:
        segs, cum, total = [], [0.0], 0.0
        for a, b in zip(self.waypoints, self.waypoints[1:]):
            d = abs(b[0] - a[0]) + abs(b[1] - a[1])   # axis-aligned, so manhattan == euclidean
            segs.append(d)
            total += d
            cum.append(total)
        self.seg_len = tuple(segs)
        self.cum_len = tuple(cum)

    @property
    def path_length(self) -> float:
        return self.cum_len[-1]

    def point_at(self, dist: float) -> tuple[float, float]:
        """World position of a unit `dist` tiles along the path."""
        if dist <= 0:
            return self.waypoints[0]
        if dist >= self.path_length:
            return self.waypoints[-1]
        # linear scan: paths are a handful of segments, so this beats bisect overhead
        for i, seg in enumerate(self.seg_len):
            if dist <= self.cum_len[i + 1]:
                t = (dist - self.cum_len[i]) / seg if seg else 0.0
                ax, ay = self.waypoints[i]
                bx, by = self.waypoints[i + 1]
                return (ax + (bx - ax) * t, ay + (by - ay) * t)
        return self.waypoints[-1]


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


def load_anchor(anchor_id: str) -> Anchor:
    doc = json.loads((DATA / "anchors" / f"{anchor_id}.json").read_text())
    return Anchor(
        id=doc["id"], act=doc["act"], title=doc["title"],
        capacity_mw=doc["capacity_mw"], starting_funds=doc["starting_funds"],
        lives=doc.get("lives", 10),
        tutorial=doc.get("tutorial", False),
        capacity_decay_mw=doc.get("capacity_decay_mw", 0.0),
        grid=(doc["grid"]["w"], doc["grid"]["h"]),
        waypoints=tuple((float(x), float(y)) for x, y in doc["path"]),
        slots=tuple((int(x), int(y)) for x, y in doc["slots"]),
        waves=tuple(
            Wave(
                lead_in=w.get("lead_in", 20.0),
                spawns=tuple(
                    Spawn(enemy=s["enemy"], count=s["count"],
                          interval=s.get("interval", 1.0), delay=s.get("delay", 0.0))
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
