"""
Headless combat simulation.

Fixed timestep, no floating wall-clock, and **no RNG in the core loop** — targeting
and build order are total orders, so determinism is structural rather than something
a seed has to protect. That matters because the whole point of this file is that a
balance claim is reproducible: same data in, identical numbers out, on any machine.

The design decision that makes this possible is that power is a scalar over time
(see docs/DECISIONS.md 003). An anchor resolves without rendering a frame.

What is modelled:
  - continuous power draw, capacity, and brownout (a board-wide fire-rate penalty
    scaled by how far over capacity the bus is — decision 022)
  - enemies that drain the bus while alive (Act II onward)
  - slow fields and reveal coverage, so zero-damage emplacements have a real role
  - armour as flat reduction, shielding as a targeting gate

What is not modelled, deliberately: projectile travel time, unit collision, and
partial-tile pathing. None of them change whether a build survives a wave, and each
would add state that the engine cannot verify against anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .content import Anchor, Enemy, Tower

DT = 1.0 / 30.0
# Brownout is priced by how far over capacity the bus is, not as a flat cliff.
# Decision 022, superseding the flat 0.40 of decision 003.
#
# A flat penalty made the power budget a wall rather than a currency: 2 MW over cost
# exactly what 40 MW over cost, so "never exceed capacity" was always correct and the
# mechanic collapsed into a build constraint. LF-014 measured that and found no build,
# at any difficulty, where overdrawing paid — including a briefly-raised shield wall.
#
# The slope is set so that adding one emplacement past capacity lands near break-even:
# N+1 towers at (1 - k/N) effective output versus N at full rate is a coin-flip at
# k ~ N/(N+1), and boards in Act I run 5-8 emplacements. It is therefore a judgement
# call — worth it when the extra gun covers path the others cannot reach — rather than
# an obvious yes or an obvious no.
BROWNOUT_SLOPE = 1.5
BROWNOUT_MAX_PENALTY = 0.70


def brownout_penalty(load_mw: float, capacity_mw: float) -> float:
    """Fire-rate penalty in [0, BROWNOUT_MAX_PENALTY]. 0 when at or under capacity."""
    if capacity_mw <= 0.0 or load_mw <= capacity_mw:
        return 0.0
    over = load_mw / capacity_mw - 1.0
    return min(BROWNOUT_MAX_PENALTY, over * BROWNOUT_SLOPE)
MAX_SIM_SECONDS = 3600.0

# name -> (enemy hp multiplier, bounty multiplier)
DIFFICULTIES: dict[str, tuple[float, float]] = {
    "standard": (1.00, 1.00),
    "hard":     (1.35, 0.90),
    # 1.80 made anchor-01 unwinnable by any build. Swept against the sim: 1.55 is the
    # point where brutal kills the overdraw build but leaves the disciplined one alive,
    # which is the behaviour the brownout penalty is supposed to produce.
    "brutal":   (1.55, 0.80),
}


@dataclass
class Placed:
    tower: Tower
    slot: tuple[int, int]
    online: bool = True
    cooldown: float = 0.0


@dataclass
class Unit:
    kind: Enemy
    hp: float
    dist: float = 0.0
    alive: bool = True


@dataclass
class Outcome:
    anchor: str
    difficulty: str
    policy: str
    won: bool
    waves_cleared: int
    waves_total: int
    died_on_wave: int | None
    lives_left: int
    leaks: int
    peak_load_mw: float
    mean_load_mw: float
    capacity_mw: float
    brownout_fraction: float
    built: list[str]
    spend: int
    sim_seconds: float

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("peak_load_mw", "mean_load_mw", "brownout_fraction", "sim_seconds"):
            d[k] = round(d[k], 3)
        d["headroom_mw"] = round(self.capacity_mw - self.peak_load_mw, 3)
        return d


class Policy:
    """A deterministic stand-in for a player.

    A policy is an ordered preference over emplacements plus a power discipline.
    Different orders produce genuinely different boards, which is how the grader
    answers "how many distinct builds clear this anchor" — a level with exactly one
    winning policy is a puzzle with one answer, which the design treats as a defect.
    """

    def __init__(self, name: str, preference: list[str], allow_overdraw: bool = False,
                 caps: dict[str, int] | None = None, reserve: float = 0.0):
        self.name = name
        self.preference = preference
        self.allow_overdraw = allow_overdraw
        # Fraction of capacity left unbuilt. Act I policies spend the bus to the last
        # megawatt because nothing else draws on it; from Act II an enemy does, so a
        # policy with no reserve is browned out from the first sapper and every anchor
        # in the act grades unwinnable for a reason that is the harness, not the level.
        # A player leaves headroom; the grader has to be able to express that.
        self.reserve = reserve
        # Per-emplacement build limit. Without it a policy that leads with a support
        # emplacement fills every slot with it — "intel-first" built four scan relays
        # and no guns, and no policy could express the one sensible board for
        # anchor-02: a single relay plus turrets. Support towers need a count, not
        # just a rank, or every anchor from 02 on is ungradeable.
        self.caps = caps or {}

    def rank(self, tower_id: str) -> int:
        return self.preference.index(tower_id) if tower_id in self.preference else 99


class Sim:
    def __init__(self, anchor: Anchor, towers: dict[str, Tower], enemies: dict[str, Enemy],
                 policy: Policy, difficulty: str = "standard"):
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"unknown difficulty {difficulty!r}")
        self.a = anchor
        self.towers = towers
        self.enemies = enemies
        self.policy = policy
        self.difficulty = difficulty
        self.hp_mult, self.bounty_mult = DIFFICULTIES[difficulty]

        # Tie-broken by id, not by insertion order. Emplacements the policy does not
        # rank all share rank 99, and Python's stable sort would then hand back
        # towers.json's file order while the GDScript port hands back alphabetical —
        # a parity failure that stayed hidden until Act II added three towers whose
        # file order and alphabetical order finally disagreed.
        self.buildable = sorted(
            (t for t in towers.values() if t.unlocked_at <= anchor.id),
            key=lambda t: (policy.rank(t.id), t.id),
        )
        self.funds = anchor.starting_funds
        self.spend = 0
        self.lives = anchor.lives
        self.leaks = 0
        self.placed: list[Placed] = []
        self.free_slots = list(anchor.slots)
        self.units: list[Unit] = []
        self.t = 0.0

        self._load_integral = 0.0
        self._brownout_time = 0.0
        self.peak_load = 0.0

    # ───────────────────────────────────────────────────────────── power ──

    def bus_load(self) -> float:
        load = sum(p.tower.draw_mw for p in self.placed if p.online)
        # A drain is suppressed where a damper covers the unit doing it. The damper
        # spends a fixed draw to deny a variable one, so it pays only on waves that
        # actually carry drain — which is the whole Act II decision (decision 027).
        for u in self.units:
            if not u.alive or u.kind.drains_mw <= 0.0:
                continue
            x, y = self.a.point_at(u.dist)
            damp = min(1.0, self._covered_by("damp", x, y))
            load += u.kind.drains_mw * (1.0 - damp)
        return load

    def _online_draw(self) -> float:
        return sum(p.tower.draw_mw for p in self.placed if p.online)

    # ────────────────────────────────────────────────────────────── build ──

    def _slot_priority(self) -> list[tuple[int, int]]:
        """Slots nearest the path first — a slot covering nothing is worth nothing."""
        def d(slot):
            best = 1e9
            steps = max(2, int(self.a.path_length))
            for i in range(steps + 1):
                px, py = self.a.point_at(self.a.path_length * i / steps)
                best = min(best, math.hypot(px - slot[0], py - slot[1]))
            return best
        return sorted(self.free_slots, key=lambda s: (d(s), s))

    def _try_build(self) -> None:
        """Spend down in preference order while funds and capacity allow."""
        while self.free_slots:
            slot_order = self._slot_priority()
            for tower in self.buildable:
                if tower.cost > self.funds:
                    continue
                cap = self.policy.caps.get(tower.id)
                if cap is not None and sum(1 for p in self.placed
                                           if p.tower.id == tower.id) >= cap:
                    continue
                projected = self._online_draw() + tower.draw_mw
                budget = self.a.capacity_mw * (1.0 - self.policy.reserve)
                if not self.policy.allow_overdraw and projected > budget:
                    continue
                slot = slot_order[0]
                self.placed.append(Placed(tower=tower, slot=slot))
                self.free_slots.remove(slot)
                self.funds -= tower.cost
                self.spend += tower.cost
                break
            else:
                return   # nothing affordable fits

    def _shed_load(self) -> None:
        """Under a strict policy, take the least-preferred emplacement offline."""
        if self.policy.allow_overdraw:
            return
        while self._online_draw() > self.a.capacity_mw:
            live = [p for p in self.placed if p.online]
            if not live:
                return
            worst = max(live, key=lambda p: (self.policy.rank(p.tower.id), p.tower.draw_mw))
            worst.online = False

    # ──────────────────────────────────────────────────────── coverage ──

    def _covered_by(self, effect: str, x: float, y: float) -> float:
        """Best effect value covering a point. 0.0 if uncovered."""
        best = 0.0
        for p in self.placed:
            if not p.online or p.tower.effect_type != effect:
                continue
            if math.hypot(p.slot[0] - x, p.slot[1] - y) <= p.tower.range:
                best = max(best, p.tower.effect_value or 1.0)
        return best

    def _can_target(self, tower: Tower, u: Unit, revealed: bool) -> bool:
        if u.kind.kind == "air":
            return "air" in tower.targets and revealed
        if u.kind.shielded:
            return "shielded" in tower.targets
        return "ground" in tower.targets

    # ───────────────────────────────────────────────────────────── tick ──

    def _step(self, penalty: float) -> None:
        rate = 1.0 - penalty

        # move
        for u in self.units:
            if not u.alive:
                continue
            x, y = self.a.point_at(u.dist)
            slow = self._covered_by("slow", x, y)
            speed = u.kind.speed * (slow if slow else 1.0)
            u.dist += speed * DT
            if u.dist >= self.a.path_length:
                u.alive = False
                self.leaks += 1
                self.lives -= 1

        # fire — furthest-along reachable target, a total order, so no RNG needed
        for p in self.placed:
            if not p.online or not p.tower.is_weapon:
                continue
            p.cooldown -= DT * rate
            if p.cooldown > 0:
                continue
            target = None
            for u in self.units:
                if not u.alive:
                    continue
                x, y = self.a.point_at(u.dist)
                if math.hypot(p.slot[0] - x, p.slot[1] - y) > p.tower.range:
                    continue
                revealed = u.kind.kind != "air" or self._covered_by("reveal", x, y) > 0
                if not self._can_target(p.tower, u, revealed):
                    continue
                if target is None or u.dist > target.dist:
                    target = u
            if target is None:
                continue
            self._damage(target, p.tower)
            if p.tower.splash > 0:
                tx, ty = self.a.point_at(target.dist)
                for u in self.units:
                    if u is target or not u.alive:
                        continue
                    ux, uy = self.a.point_at(u.dist)
                    if math.hypot(ux - tx, uy - ty) <= p.tower.splash:
                        self._damage(u, p.tower, scale=0.5)
            p.cooldown = p.tower.fire_interval

    def _damage(self, u: Unit, tower: Tower, scale: float = 1.0) -> None:
        u.hp -= max(0.0, tower.damage * scale - u.kind.armour)
        if u.hp <= 0:
            u.alive = False
            self.funds += int(u.kind.bounty * self.bounty_mult)

    # ───────────────────────────────────────────────────────────── run ──

    def run(self) -> Outcome:
        waves_cleared = 0
        died_on: int | None = None

        for wi, wave in enumerate(self.a.waves, start=1):
            # prep phase: build, then shed anything that overdraws
            self._try_build()
            self._shed_load()
            self._advance(wave.lead_in)

            queue: list[tuple[float, str]] = []
            for sp in wave.spawns:
                for n in range(sp.count):
                    queue.append((sp.delay + n * sp.interval, sp.enemy))
            queue.sort(key=lambda q: (q[0], q[1]))

            wave_t, qi = 0.0, 0
            while True:
                while qi < len(queue) and queue[qi][0] <= wave_t + 1e-9:
                    e = self.enemies[queue[qi][1]]
                    self.units.append(Unit(kind=e, hp=e.hp * self.hp_mult))
                    qi += 1
                self._tick_once()
                wave_t += DT
                if self.lives <= 0:
                    died_on = wi
                    break
                if qi >= len(queue) and not any(u.alive for u in self.units):
                    break
                if self.t > MAX_SIM_SECONDS:
                    died_on = wi
                    break

            self.units = [u for u in self.units if u.alive]
            if died_on is not None:
                break
            waves_cleared += 1

        return Outcome(
            anchor=self.a.id, difficulty=self.difficulty, policy=self.policy.name,
            won=died_on is None and self.lives > 0,
            waves_cleared=waves_cleared, waves_total=len(self.a.waves),
            died_on_wave=died_on, lives_left=max(0, self.lives), leaks=self.leaks,
            peak_load_mw=self.peak_load,
            mean_load_mw=self._load_integral / self.t if self.t else 0.0,
            capacity_mw=self.a.capacity_mw,
            brownout_fraction=self._brownout_time / self.t if self.t else 0.0,
            built=[f"{p.tower.id}@{p.slot[0]},{p.slot[1]}" for p in self.placed],
            spend=self.spend, sim_seconds=self.t,
        )

    def _tick_once(self) -> None:
        load = self.bus_load()
        penalty = brownout_penalty(load, self.a.capacity_mw)
        self.peak_load = max(self.peak_load, load)
        self._load_integral += load * DT
        if penalty > 0.0:
            self._brownout_time += DT
        self.t += DT
        self._step(penalty)

    def _advance(self, seconds: float) -> None:
        for _ in range(int(seconds / DT)):
            self._tick_once()


def standard_policies(tower_ids: list[str]) -> list[Policy]:
    """A small, fixed set of distinct playstyles. Deterministic and ordered.

    Not an exhaustive search — the point is to answer "does more than one sensible
    approach work", not to find the optimum. An optimiser would report that every
    anchor is winnable by some build and tell us nothing about whether it is fun.
    """
    def has(i: str) -> list[str]:
        return [i] if i in tower_ids else []

    rest = lambda first: first + [t for t in tower_ids if t not in first]

    # Support emplacements are capped. They deal no damage, so an uncapped preference
    # for one produces a board with no guns on it — which grades a level as unwinnable
    # for a reason that has nothing to do with the level.
    out = [
        Policy("cheap-mass",   rest(has("pulse-turret"))),
        Policy("burst",        rest(has("ion-lance") + has("pulse-turret"))),
        Policy("rapid",        rest(has("arc-node") + has("pulse-turret"))),
        Policy("control",      rest(has("shield-wall") + has("pulse-turret")),
               caps={"shield-wall": 2, "scan-relay": 1}),
        Policy("intel-first",  rest(has("scan-relay") + has("pulse-turret")),
               caps={"scan-relay": 1, "shield-wall": 1}),
        Policy("screened",     rest(has("scan-relay") + has("pulse-turret")),
               caps={"scan-relay": 2, "shield-wall": 1}),
        Policy("greedy-overdraw", rest(has("ion-lance") + has("arc-node")), allow_overdraw=True),
        # Act II. The damper is support, so it needs a cap for the same reason the relay
        # does — uncapped it fills the board with emplacements that shoot nothing.
        Policy("suppression", rest(has("anchor-damper") + has("pulse-turret")),
               caps={"anchor-damper": 2, "scan-relay": 1, "shield-wall": 1},
               reserve=0.20),
        Policy("flak-screen", rest(has("flak-array") + has("scan-relay")),
               caps={"scan-relay": 1, "anchor-damper": 1}, reserve=0.15),
        Policy("reserved-mass", rest(has("pulse-turret") + has("scan-relay")),
               caps={"scan-relay": 1, "shield-wall": 1, "anchor-damper": 1},
               reserve=0.30),
    ]
    return out
