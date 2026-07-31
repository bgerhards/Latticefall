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

# Fraction of damage a weapon without "shielded" in its targets still lands on a shielded
# unit. Shielding is a tax, not a gate — decision 029. At 0.0 the ion lance was the only
# answer in the game, so every anchor carrying breachers graded unwinnable or
# single-solution, which is the defect the grader exists to catch.
SHIELD_LEAK = 0.25

# Act III capacity decay never takes the bus below this fraction of its rated capacity.
CAPACITY_FLOOR = 0.45


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
                 caps: dict[str, int] | None = None, reserve: float = 0.0,
                 core: tuple[str, int] | None = None, closed: bool = False):
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
        self.caps = dict(caps or {})
        # LF-095/BAL-02. `core` names the tower a capped-core policy leads with and how
        # many of it to build; it is purely descriptive here (a comment that also
        # executes) — it only auto-fills `caps[core[0]]` if the caller has not already
        # set it, so a capped-core policy cannot be built with the cap forgotten.
        # Building the count into caps means `_try_build` needs no core-specific
        # branch at all: "N of the core tower" is just a cap on one entry, same as any
        # support cap already was.
        self.core = core
        if core is not None:
            self.caps.setdefault(core[0], core[1])
        # An *open* policy (the default, and every one of the original twelve) ranks
        # the whole unlocked catalog — `preference` just orders it, and anything it
        # does not name is still buildable, tried last (rank 99). That is why the
        # all-of-one-thing bug existed: once the top-ranked entry stopped fitting, the
        # loop fell through to *some* next-best entry from the full catalog, never to
        # "nothing else" — LF-095's "Three pulse turrets in front of an ion lance is
        # not in the search space" is really "there is no way to say *only* these two
        # towers." `closed=True` says exactly that: Sim restricts `buildable` to
        # `preference` itself, so once nothing in that short list fits, `_try_build`'s
        # existing `else: return` fires for real, rather than reaching past a
        # deliberately-narrow roster into whatever else the anchor has unlocked. See
        # Policy.capped_core() below and Sim.__init__'s `buildable` construction.
        self.closed = closed

    def rank(self, tower_id: str) -> int:
        return self.preference.index(tower_id) if tower_id in self.preference else 99

    @classmethod
    def capped_core(cls, name: str, core: tuple[str, int], fill: list[str],
                     allow_overdraw: bool = False, caps: dict[str, int] | None = None,
                     reserve: float = 0.0) -> "Policy":
        """A policy built from an explicit core tower plus a closed fill list.

        `core=("ion-lance", 2)` with `fill=["pulse-turret"]` reads as "two ion lances,
        then pulse turrets, then stop" — composition as a first-class shape, not an
        accident of one open policy running out of funds for its favourite tower
        before falling through to the rest of the catalog. Preference is built
        core-first so the core tower claims the nearest slots (`_slot_priority()`
        ranks by distance to the path, tried in preference order) and the fill towers
        take what is left, exactly the ordering LF-095 asks for.
        """
        core_id, _count = core
        preference = [core_id] + [t for t in fill if t != core_id]
        return cls(name, preference, allow_overdraw=allow_overdraw, caps=caps,
                   reserve=reserve, core=core, closed=True)


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
        #
        # A closed policy (Policy.capped_core(), LF-095) restricts this to towers it
        # actually names — everything else is never a candidate, not merely a
        # low-ranked one — so "core exhausted, then stop" is a real closed search
        # rather than a fallback into the wider catalog. An open policy (every one of
        # the original twelve; `closed` defaults to False) is unaffected: the filter
        # below is a no-op for them.
        catalog = towers.values()
        if policy.closed:
            catalog = (t for t in catalog if t.id in policy.preference)
        self.buildable = sorted(
            (t for t in catalog if t.unlocked_at <= anchor.id),
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
        # Waves begun. Act III capacity decay is priced off this, not off elapsed time,
        # so the loss lands on a beat the player can see coming and build against.
        self.wave_index = 0

        # Per-effect placed lists (LF-099). `_covered_by` walks one of these instead of
        # every placed emplacement, so a board with nothing carrying `effect` never pays
        # for the walk. Kept fresh two ways: unconditionally at the top of `_tick_once()`
        # (the primary point — see the comment there, named here so a reader of either
        # file finds the other), and eagerly, right after every write to `placed`/
        # `.online` outside of a tick (`_try_build()`, `_shed_load()`) — a tick-only
        # rebuild would leave `capacity_now()` blind to a restorer placed earlier in the
        # same prep-phase build loop, which is a real behaviour change, not a rounding
        # error (see the risk note on `capacity_now()`/`brownout_penalty()` below).
        self._eff_slow: list[Placed] = []
        self._eff_damp: list[Placed] = []
        self._eff_reveal: list[Placed] = []
        self._eff_restore: list[Placed] = []

    def capacity_now(self) -> float:
        """Capacity this wave. Fixed in Acts I and II; falls per wave in Act III.

        Floored at CAPACITY_FLOOR of the anchor's rated capacity — a bus that decays to
        nothing is not a decision, it is a timer, and the act is supposed to be about
        choosing what fails first."""
        base = self.a.capacity_mw
        if self.a.capacity_decay_mw > 0.0:
            lost = self.a.capacity_decay_mw * self.wave_index
            base = max(self.a.capacity_mw * CAPACITY_FLOOR, self.a.capacity_mw - lost)
        # Restorers add capacity back. They are the only thing in the game that does,
        # and they pay for it with a slot and a draw of their own — decision 031.
        # Reuses `_eff_restore` (LF-099) rather than re-scanning `placed` here; kept
        # fresh by the same two rebuild points documented on `_eff_slow` above.
        for p in self._eff_restore:
            base += p.tower.effect_value
        return base

    def _rebuild_effect_lists(self) -> None:
        """Rebuild the four per-effect placed lists from `placed`, preserving order.

        The primary call site is the top of `_tick_once()`, before `bus_load()` — its
        first consumer this tick — and that call is unconditional rather than gated by
        a dirty flag: it is O(towers) once, against the O(towers x units) that
        `_covered_by` used to cost by re-testing every emplacement's effect type for
        every unit, every tick (LF-099). `_try_build()` and `_shed_load()` additionally
        call this right after every write to `placed`/`.online`, because both run
        outside any tick and `capacity_now()` needs the *current* prep-phase board, not
        last tick's — see the comment on `_eff_slow`. Mirrors
        scripts/anchor_sim.gd's `_rebuild_effect_lists()`, called from the same set of
        points there.

        `max`/`maxf` over the values in play does not care what order this list is in,
        but the walk order is kept identical to `placed`'s anyway — a future change to
        "first wins" must not be able to silently diverge between the two engines.
        """
        self._eff_slow = []
        self._eff_damp = []
        self._eff_reveal = []
        self._eff_restore = []
        for p in self.placed:
            if not p.online:
                continue
            et = p.tower.effect_type
            if et == "slow":
                self._eff_slow.append(p)
            elif et == "damp":
                self._eff_damp.append(p)
            elif et == "reveal":
                self._eff_reveal.append(p)
            elif et == "restore":
                self._eff_restore.append(p)

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
        """Slots nearest the path first — a slot covering nothing is worth nothing.

        Ranked by *squared* distance. Every range test in both engines compares squares
        rather than calling a square root, so the two runtimes do identical IEEE-754
        double arithmetic instead of comparing a `hypot` against a `sqrt` and disagreeing
        in the last bit. See decision 030."""
        def d2(slot):
            best = 1e18
            steps = max(2, int(self.a.path_length))
            for i in range(steps + 1):
                px, py = self.a.point_at(self.a.path_length * i / steps)
                dx, dy = px - slot[0], py - slot[1]
                best = min(best, dx * dx + dy * dy)
            return best
        return sorted(self.free_slots, key=lambda s: (d2(s), s))

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
                budget = self.capacity_now() * (1.0 - self.policy.reserve)
                if not self.policy.allow_overdraw and projected > budget:
                    continue
                slot = slot_order[0]
                self.placed.append(Placed(tower=tower, slot=slot))
                # Eager, not tick-gated (LF-099): capacity_now(), called again below on
                # the very next candidate, must see a restorer placed this iteration —
                # see the comment on _eff_slow/_rebuild_effect_lists().
                self._rebuild_effect_lists()
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
        while self._online_draw() > self.capacity_now():
            live = [p for p in self.placed if p.online]
            if not live:
                return
            worst = max(live, key=lambda p: (self.policy.rank(p.tower.id), p.tower.draw_mw))
            worst.online = False
            # Eager, not tick-gated (LF-099): the next loop condition re-reads
            # capacity_now(), which must not still count a restorer just shed.
            self._rebuild_effect_lists()

    # ──────────────────────────────────────────────────────── coverage ──

    def _covered_by(self, effect: str, x: float, y: float) -> float:
        """Best effect value covering a point. 0.0 if uncovered.

        Walks the pre-filtered list for `effect` (built by `_rebuild_effect_lists()`,
        LF-099) instead of every placed emplacement, and returns 0.0 immediately when
        that list is empty — the point being that a board with nothing carrying
        `effect` never pays for the walk at all, even once, per unit, per tick."""
        if effect == "slow":
            towers = self._eff_slow
        elif effect == "damp":
            towers = self._eff_damp
        elif effect == "reveal":
            towers = self._eff_reveal
        elif effect == "restore":
            towers = self._eff_restore
        else:
            towers = []
        if not towers:
            return 0.0
        best = 0.0
        for p in towers:
            dx, dy = p.slot[0] - x, p.slot[1] - y
            if dx * dx + dy * dy <= p.tower.range * p.tower.range:
                best = max(best, p.tower.effect_value or 1.0)
        return best

    def _can_target(self, tower: Tower, u: Unit, revealed: bool) -> bool:
        if u.kind.kind == "air":
            return "air" in tower.targets and revealed
        return "ground" in tower.targets

    @staticmethod
    def _shield_scale(tower: Tower, u: Unit) -> float:
        """Multiplier for shielding. 1.0 unless the unit is shielded and the weapon is
        not rated for it, in which case it still lands SHIELD_LEAK of its damage."""
        if u.kind.shielded and "shielded" not in tower.targets:
            return SHIELD_LEAK
        return 1.0

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
                # Priced by the unit, not a flat 1. Decision 047: with a flat cost, three
                # 170 hp units in place of one 520 hp one tripled the tension along with
                # the count, so density could only be bought with lives. `leaks` still
                # counts bodies — it is a different question from what they cost.
                self.lives -= u.kind.leak_cost

        # fire — furthest-along reachable target, a total order, so no RNG needed
        for p in self.placed:
            if not p.online or not p.tower.is_weapon:
                continue
            p.cooldown -= DT * rate
            if p.cooldown > 0:
                continue
            # An emplacement with nothing in range rescans every unit, every tick,
            # forever — the empty-target path below never resets the cooldown, which has
            # already gone negative. That is LF-098, and it is real: measured 14.3x at
            # 60 towers / 400 units and 17.1x at 100/800 against the same board with
            # targets in range. It is NOT fixed here, and there is no cheap fix, because
            # the only retry interval that preserves "acquires the frame a unit enters
            # range" is one tick — which is the same tick it already scans on. Decision
            # 058 has the measurements and the two rejected repairs; WAR-03's spatial
            # hash is the actual answer, since it makes an idle scan cost the cells in
            # range rather than every unit on the board.
            target = None
            for u in self.units:
                if not u.alive:
                    continue
                x, y = self.a.point_at(u.dist)
                dx, dy = p.slot[0] - x, p.slot[1] - y
                if dx * dx + dy * dy > p.tower.range * p.tower.range:
                    continue
                revealed = u.kind.kind != "air" or self._covered_by("reveal", x, y) > 0
                if not self._can_target(p.tower, u, revealed):
                    continue
                if target is None or u.dist > target.dist:
                    target = u
            if target is None:
                # Retry next tick rather than scanning every tick forever (LF-098) — see
                # the gate above. Rejected a longer retry interval: it would cut more
                # cost, but delays "acquires the frame a unit enters range" by that same
                # interval, changing which tick an idle gun re-acquires on — a rules
                # change, not a performance one.
                continue
            self._damage(target, p.tower)
            if p.tower.splash > 0:
                tx, ty = self.a.point_at(target.dist)
                for u in self.units:
                    if u is target or not u.alive:
                        continue
                    ux, uy = self.a.point_at(u.dist)
                    dx, dy = ux - tx, uy - ty
                    if dx * dx + dy * dy <= p.tower.splash * p.tower.splash:
                        self._damage(u, p.tower, scale=0.5)
            p.cooldown = p.tower.fire_interval

    def _damage(self, u: Unit, tower: Tower, scale: float = 1.0) -> None:
        # Armour first, then the shield tax on what got through. The other order makes
        # a shielded armoured unit immune rather than expensive — 9 damage taxed to 3.15
        # never clears 5 armour, so the bulwark could only be killed by the lance and
        # anchor-14 graded unwinnable at every setting swept.
        after_armour = max(0.0, tower.damage * scale - u.kind.armour)
        u.hp -= after_armour * self._shield_scale(tower, u)
        if u.hp <= 0:
            u.alive = False
            self.funds += int(u.kind.bounty * self.bounty_mult)

    # ───────────────────────────────────────────────────────────── run ──

    def run(self) -> Outcome:
        waves_cleared = 0
        died_on: int | None = None

        for wi, wave in enumerate(self.a.waves, start=1):
            # The bus loses its Act III decay *before* the prep phase, so the player
            # builds against the capacity this wave will actually run at.
            self.wave_index = wi - 1
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
        # Primary rebuild point (LF-099) — see the comment on _eff_slow in __init__.
        # Unconditional every tick, not gated by a dirty flag: O(towers) once here is
        # cheap next to what it replaces, O(towers) inside _covered_by for every one of
        # `bus_load()`'s and `_step()`'s per-unit calls. Named here because
        # scripts/anchor_sim.gd's tick() rebuilds at the equivalent point, before its
        # own bus_load() call, for the same reason.
        self._rebuild_effect_lists()
        load = self.bus_load()
        penalty = brownout_penalty(load, self.capacity_now())
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
        # Shielded units are answered only by the lance and (from anchor-13) the mortar,
        # so a board facing them must lead with one. Without this policy every anchor
        # carrying breachers grades unwinnable — not because it is, but because the two
        # lance-leading policies in the set both spend the bus flat and brown out.
        Policy("anti-armour",
               rest(has("ion-lance") + has("mortar-emplacement") + has("pulse-turret")),
               caps={"scan-relay": 1, "anchor-damper": 1}, reserve=0.20),
        # Act III. Buys capacity back before building into it. Capped at two restorers:
        # uncapped, a board that only restores capacity has nothing to spend it on.
        Policy("restore-first", rest(has("restorer") + has("pulse-turret")),
               caps={"restorer": 2, "scan-relay": 1, "anchor-damper": 1}, reserve=0.10),

        # ── Capped-core policies (LF-095 / BAL-02) ──────────────────────────────
        # Closed: each of these can build *only* its core tower and its named fill,
        # never falling through to the rest of the catalog the way every policy above
        # does. That is what makes the claim in each comment falsifiable by the grader
        # rather than merely plausible — a win here cannot be explained away by some
        # third, unlisted tower the policy was never meant to reach for.

        # Claim: enemies.json says the Reach Picket exists "so the pulse turret earns
        # its slot again" against a lance-led board. Two lances answer the anchor-13+
        # breacher/bulwark shield tax; if the claim is right, adding a turret escort
        # for the Pickets should out-survive spending the same slots on more lances,
        # which "burst" (open, uncapped lance) already stands in for as the control.
        Policy.capped_core("lance-core", core=("ion-lance", 2), fill=["pulse-turret"],
                           reserve=0.20),

        # Claim: from anchor-13, the mortar is the second shielded answer (decision
        # 029's SHIELD_LEAK note) and flak-array is the cheaper air/ground mop-up —
        # a two-mortar core with a flak escort should be a genuinely different board
        # from "anti-armour" (open, lance-first) rather than the same one reached by a
        # different route.
        Policy.capped_core("mortar-core", core=("mortar-emplacement", 2),
                           fill=["flak-array"], reserve=0.20),

        # Claim: "suppression" caps the damper at two and still falls through to the
        # full catalog when both are exhausted. This tests whether *one* damper is
        # already enough drain suppression for a turret board to hold Act II — a
        # stricter, falsifiable version of the same design idea.
        Policy.capped_core("damper-core", core=("anchor-damper", 1),
                           fill=["pulse-turret"], reserve=0.15),

        # Claim: "restore-first" caps restorers at two. This tests whether *one*
        # restorer's worth of capacity back is enough to carry a turret board through
        # Act III's decay (decision 031) — if not, that is evidence the two-restorer
        # cap in "restore-first" is load-bearing, not just generous.
        Policy.capped_core("restorer-core", core=("restorer", 1),
                           fill=["pulse-turret"], reserve=0.10),
    ]
    return out
