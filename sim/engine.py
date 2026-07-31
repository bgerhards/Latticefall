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
from dataclasses import dataclass, field, replace as dc_replace

from .content import Ability, Anchor, Enemy, Tower, Tuning, VeterancyRank, load_tuning

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

# BAL-01. Mirrors scripts/anchor_sim.gd:31 SELL_REFUND — fraction of what was paid an
# emplacement returns on the `sell` scheduled verb. Grading-only simplification: no
# `sell_refund_bonus` term (anchor_sim.gd:38) — that is a Recoveries-only input outside
# every grading path (decision 033), so omitting it changes nothing a policy can observe.
SELL_REFUND = 0.6


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
    # BAL-01: mirrors scripts/anchor_sim.gd's placed-record keys of the same name.
    # `target_mode` defaults to "first" — an untouched record (every one _try_build()/
    # build() places) reads exactly as it did before this verb existed. `kills` is
    # tracked unconditionally, independent of whether a policy opts into veterancy,
    # matching anchor_sim.gd's own comment on why annotating it here is safe (a Placed
    # is only ever compared by `slot`, never by value, so growing this field cannot
    # collide with the LF-055 identity trap). `upgraded`/`upgrade_paid` back the
    # `upgrade` verb and sell()'s refund-on-what-was-actually-paid.
    target_mode: str = "first"
    kills: int = 0
    upgraded: bool = False
    upgrade_paid: int = 0


@dataclass
class Unit:
    kind: Enemy
    hp: float
    dist: float = 0.0
    alive: bool = True
    # WAR-01: index into Anchor.lanes, set once at construction and never afterwards —
    # a unit dictionary/record must never grow or change a key post-construction (LF-055;
    # see the identical note in scripts/anchor_sim.gd's _step()).
    lane: int = 0


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
                 core: tuple[str, int] | None = None, closed: bool = False,
                 schedule: list[tuple[float, str, dict]] | None = None,
                 veterancy: bool = False, chain_bounty: bool = False):
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

        # ── BAL-01: scheduled actions ───────────────────────────────────────────
        # A deterministic stand-in for a player pressing something. Times are seconds
        # of `Sim.t` — sim time, never wall-clock and never wave-relative. Wave-relative
        # would read better in a policy's own source, but it is a second source of truth
        # about when a wave starts (the anchor's `lead_in` plus however long every prior
        # wave's combat actually took, which is only known by running the sim) — BAL-01
        # rejected it for exactly that reason: it would make a schedule's own meaning
        # depend on outcomes the schedule is supposed to be judged against.
        #
        # Sorted once, here, on a TOTAL order (time, original list index) — never on
        # time alone. Two actions sharing a timestamp must dispatch in the order they
        # were authored, on both runtimes, and relying on Python's sort being stable
        # would not prove that: GDScript's `sort_custom` is not documented as stable, so
        # scripts/test/parity.gd's mirror needs the identical explicit second key to
        # agree, not merely to happen to agree today.
        indexed = list(enumerate(schedule or []))
        indexed.sort(key=lambda pair: (pair[1][0], pair[0]))
        self.schedule: list[tuple[float, str, dict]] = [item for _, item in indexed]

        # ── BAL-01: unconditional rules, not scheduled actions ──────────────────
        # Veterancy is a real mechanism in scripts/anchor_sim.gd today (_veteran_rank()
        # runs on every shot, unconditionally — see Sim._veteran_rank() below) but its
        # effect is inert by default because the rank ladder itself defaults empty.
        # `veterancy=True` is this Policy opting into the ladder data/tuning.json
        # actually authors, standing in for "scripts/anchor_view.gd has called
        # set_veterancy_ranks() at boot" in real play — not a mid-run button press, so
        # it lives here rather than in `schedule`. Every one of the original twelve (plus
        # four capped-core) policies leaves this False, which is exactly what makes the
        # no-schedule byte-identical requirement possible: the mechanism has always been
        # there, only nothing had ever turned it on.
        self.veterancy = veterancy
        # `chain_bounty` is intentionally NOT implemented this pass — see the BAL-01
        # report. The flag is accepted (rather than raising) so a future policy can be
        # authored against it once the mechanic itself lands, but Sim never reads it yet.
        self.chain_bounty = chain_bounty

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
                 policy: Policy, difficulty: str = "standard", *,
                 tuning: Tuning | None = None):
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"unknown difficulty {difficulty!r}")
        self.a = anchor
        self.towers = towers
        self.enemies = enemies
        self.policy = policy
        self.difficulty = difficulty
        self.hp_mult, self.bounty_mult = DIFFICULTIES[difficulty]
        # BAL-01: keyword-only, defaulted — every existing caller (tools/test_parity.py,
        # tools/sweep.py via sim.run.grade_anchor(), tools/bench_tick.py) constructs a Sim
        # with the original five positional args and never passes this, so it always
        # falls back to loading data/tuning.json itself. Loading it here rather than
        # requiring every caller to thread it through keeps this file's own "pure
        # function of its data" contract intact from the caller's point of view — the
        # data just now includes tuning.json, the same way it already includes towers/
        # enemies/anchor.
        self.tuning = tuning if tuning is not None else load_tuning()

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

        # ── BAL-01: scheduled-action / GDScript-only-mirrored state ─────────────
        # Every field below defaults to the value that reproduces this file's
        # PRE-BAL-01 behaviour exactly — the same contract scripts/anchor_sim.gd's own
        # "GDScript-only state" block already documents for these same four mechanics.
        # Nothing here can move an Outcome unless `policy.schedule` or `policy.veterancy`
        # says so.
        self._schedule_i = 0                 # index into policy.schedule already drained
        self._call_wave_requested = False    # set by the `call_wave` verb; see _advance()
        self.overcharge_active = False
        self._overcharge_fire_rate_bonus = 0.0
        self._overcharge_draw_mult = 1.0
        self.shutter_active = False
        self._shutter_hold_tiles = 0.0
        self._shutter_draw_mw = 0.0
        # LF-163: Threshold Surge's charge, tracked against every kill this Sim causes
        # (both _damage() and _fire_surge()'s own), mirroring scripts/abilities.gd's
        # AbilityState.charge — see _charge_surge()'s docstring. 0.0 whenever
        # charge_max <= 0 (not the case for "surge" in data/tuning.json today, but
        # matching is_charge_gated()'s own guard rather than assuming).
        self._surge_charge = 0.0

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
        load = self._online_draw()
        # A drain is suppressed where a damper covers the unit doing it. The damper
        # spends a fixed draw to deny a variable one, so it pays only on waves that
        # actually carry drain — which is the whole Act II decision (decision 027).
        for u in self.units:
            if not u.alive or u.kind.drains_mw <= 0.0:
                continue
            x, y = self.a.point_at(u.lane, u.dist)
            damp = min(1.0, self._covered_by("damp", x, y))
            load += u.kind.drains_mw * (1.0 - damp)
        return load

    def _online_draw(self) -> float:
        """Mirrors scripts/anchor_sim.gd:245 online_draw(). BAL-01: bus_load() now calls
        this instead of re-summing `placed` itself, so Overcharge's draw multiplier and
        Shutter's flat draw apply in exactly one place, on both the load figure the
        brownout penalty is priced from and the capacity checks `_try_build()`/
        `_shed_load()` already run against `_online_draw()`.

        Deliberately branches rather than always multiplying by a `draw_mult` that
        defaults to 1.0: `towers.json`'s `draw_mw` values are unannotated JSON integers
        (e.g. `12`), so `Tower.draw_mw` holds a Python `int` at runtime despite its
        `float` type hint, and `sum()` over ints stays an int. `int * 1.0` is numerically
        exact but promotes the result to `float` — harmless as a number, but it moved
        `peak_load_mw`/`headroom_mw` from a bare `60` to `60.0` in this project's own
        JSON grade output for every anchor with no drain and no overdraw, which is
        exactly the "a single field moved" failure this file's whole parity contract
        exists to catch. Branching keeps the untouched path byte-for-byte the original
        expression; only a policy that actually schedules Overcharge ever reaches the
        multiply, which is new behaviour anyway."""
        if self.overcharge_active:
            v = sum(p.tower.draw_mw * self._overcharge_draw_mult
                     for p in self.placed if p.online)
        else:
            v = sum(p.tower.draw_mw for p in self.placed if p.online)
        if self.shutter_active:
            v += self._shutter_draw_mw
        return v

    # ────────────────────────────────────────────────────────────── build ──

    def _slot_priority(self) -> list[tuple[int, int]]:
        """Slots nearest the path first — a slot covering nothing is worth nothing.

        Ranked by *squared* distance. Every range test in both engines compares squares
        rather than calling a square root, so the two runtimes do identical IEEE-754
        double arithmetic instead of comparing a `hypot` against a `sqrt` and disagreeing
        in the last bit. See decision 030.

        WAR-01: multi-lane samples EVERY lane and keeps the minimum squared distance — a
        slot covering any one lane is worth something, so "nearest the path" has to mean
        "nearest the nearest lane". Runs once per build, not per tick, so multiplying the
        cost by lane count is fine here; the same pattern must not be copied into _step()."""
        def d2(slot):
            best = 1e18
            for lane in self.a.lanes:
                steps = max(2, int(lane.path_length))
                for i in range(steps + 1):
                    px, py = lane.point_at(lane.path_length * i / steps)
                    dx, dy = px - slot[0], py - slot[1]
                    best = min(best, dx * dx + dy * dy)
            return best
        return sorted(self.free_slots, key=lambda s: (d2(s), s))

    def _effective_cap(self) -> int:
        """LF-152/decision 063. The board-saturation denominator: `max_emplacements` if
        the anchor authors one, else `len(slots)` — the identical fallback
        `validate_data.py` already uses, so this reproduces every one of the 24 real
        anchors' numbers exactly (none of them set `max_emplacements`). Mirrors
        scripts/anchor_sim.gd's `effective_cap()`."""
        return (self.a.max_emplacements if self.a.max_emplacements is not None
                else len(self.a.slots))

    def _try_build(self) -> None:
        """Spend down in preference order while funds and capacity allow."""
        while self.free_slots:
            # LF-152: provably a no-op for every anchor that authors `slots` and no
            # `max_emplacements` (all 24 today) — `len(self.placed) + len(self.free_slots)`
            # is invariant at `len(self.a.slots)` through every build()/sell() this file
            # has (both move a slot between the two lists in lockstep, never create or
            # destroy one), so `len(self.placed) >= len(self.a.slots)` and
            # `not self.free_slots` are the same condition and this `while` would have
            # exited here anyway. Only bites once an anchor sets `max_emplacements` below
            # its slot count, or has no `slots` at all (free placement, PLC-01 — not yet
            # loadable end to end, see sim/content.py's load_anchor()).
            if len(self.placed) >= self._effective_cap():
                return
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

    # ─────────────────────────────────────────────── BAL-01: scheduled verbs ──
    #
    # Each method below mirrors a real, already-shipped scripts/anchor_sim.gd method that
    # nothing had ever driven from a grading run (decision 033's original scope: sell()
    # was GDScript-only because a policy never changes its mind mid-run; this is the
    # same argument applied to five more verbs, now that a *scheduled* policy can decide
    # things in advance without becoming reactive). `call_wave` is the one exception —
    # see its own docstring for why it mirrors scripts/anchor_view.gd instead.

    def sell(self, index: int) -> int:
        """Mirrors scripts/anchor_sim.gd:384 sell(). Refunds SELL_REFUND of what was
        paid, upgrade included."""
        if index < 0 or index >= len(self.placed):
            return 0
        p = self.placed[index]
        paid = p.tower.cost + p.upgrade_paid
        refund = int(math.floor(paid * SELL_REFUND))
        self.free_slots.append(p.slot)
        del self.placed[index]
        # Eager, not tick-gated (LF-099) — see the comment on _eff_slow in __init__.
        self._rebuild_effect_lists()
        self.funds += refund
        return refund

    def upgrade_cost(self, index: int) -> int:
        """Mirrors scripts/anchor_sim.gd:414 upgrade_cost()."""
        if index < 0 or index >= len(self.placed):
            return 0
        p = self.placed[index]
        if p.upgraded or not p.tower.upgrade:
            return 0
        return int(p.tower.upgrade.get("cost", 0))

    def upgrade(self, index: int) -> bool:
        """Mirrors scripts/anchor_sim.gd:427 upgrade(). Builds a *new* Tower via
        dataclasses.replace() rather than mutating one in place — Tower is frozen and,
        unlike anchor_sim.gd's per-board dict copy, is the exact same object shared by
        every Placed record of that type across every board this process ever grades;
        mutating it would upgrade that tower type everywhere, the same hazard
        anchor_sim.gd's own comment names for why it duplicates rather than mutates."""
        cost = self.upgrade_cost(index)
        if cost <= 0 or cost > self.funds:
            return False
        p = self.placed[index]
        overrides: dict = {}
        for k, v in p.tower.upgrade.items():
            if k == "cost":
                continue
            if k == "effect":
                overrides["effect_type"] = v.get("type")
                overrides["effect_value"] = v.get("value", 0.0)
            elif k == "targets":
                overrides["targets"] = frozenset(v)
            else:
                overrides[k] = v
        p.tower = dc_replace(p.tower, name=f"{p.tower.name} II", **overrides)
        p.upgraded = True
        p.upgrade_paid = cost
        # Eager, not tick-gated (LF-099): an upgrade can change a support tower's effect
        # value (or, in principle, its type) — see the comment on _eff_slow in __init__.
        self._rebuild_effect_lists()
        self.funds -= cost
        self.spend += cost
        return True

    def set_online(self, index: int, on: bool) -> None:
        """Mirrors scripts/anchor_sim.gd:378 set_online()."""
        if 0 <= index < len(self.placed):
            self.placed[index].online = on
            # Eager, not tick-gated (LF-099) — see the comment on _eff_slow in __init__.
            self._rebuild_effect_lists()

    def build(self, tower_id: str, slot: tuple[int, int]) -> bool:
        """Mirrors scripts/anchor_sim.gd:360 build_at() — an explicit build at a named
        slot, so a scenario can express a board `_try_build()`'s own policy search would
        never reach on its own (BAL-01's own task list).

        LF-152/decision 063: refuses at `_effective_cap()`, same as `_try_build()` above —
        a no-op for every anchor that omits `max_emplacements` (all 24 today)."""
        if len(self.placed) >= self._effective_cap():
            return False
        if slot not in self.free_slots or tower_id not in self.towers:
            return False
        tower = self.towers[tower_id]
        if tower.cost > self.funds:
            return False
        self.placed.append(Placed(tower=tower, slot=slot))
        self._rebuild_effect_lists()
        self.free_slots.remove(slot)
        self.funds -= tower.cost
        self.spend += tower.cost
        return True

    def call_wave(self, remaining_seconds: float, bonus_per_sec: float) -> int:
        """No scripts/anchor_sim.gd line to mirror — call_wave has never existed in the
        rules at all. Real play's version is scripts/anchor_view.gd:888 call_wave() /
        :903 call_wave_bonus(), which write `sim.funds` directly rather than through a
        rules method (funds is a bare public field with nothing in `_rebuild_effect_lists`
        keyed off it, so that direct write is safe — the LF-123 lesson is specifically
        about fields that DO participate in the per-effect lists). BAL-01 ports that same
        formula into the grader for the first time; `_advance()` below is this file's
        stand-in for anchor_view.gd's own `_phase == "prep"` driving loop, deciding how
        many lead-in seconds remain the same way anchor_view.gd's `_lead_left` does.

        Uses floor(x + 0.5) rather than a native round(): GDScript's `roundi()` is round-
        half-away-from-zero, CPython's builtin `round()` is round-half-to-even, and
        `bonus_per_sec * remaining_seconds` can land exactly on a half (DT-quantized
        seconds times an integer bonus) — floor(x + 0.5) reproduces round-half-away-from-
        zero for the non-negative values this always receives, safe-ops-clean, and
        provably identical to scripts/test/parity.gd's mirror rather than merely hoping
        two different native rounding rules happen to agree."""
        granted = int(math.floor(bonus_per_sec * max(0.0, remaining_seconds) + 0.5))
        self.funds += granted
        return granted

    def _charge_surge(self, u: Unit) -> None:
        """LF-163. Mirrors scripts/anchor_view.gd's `_charge_surge()` /
        scripts/abilities.gd's `AbilityState.add_charge()`: every kill, from ANY
        source, adds `leak_cost * charge_per_leak_cost` to Threshold Surge's charge,
        clamped at `charge_max`. Real play fires this off the `unit_killed` signal,
        which `fire_surge()` emits for its own kills exactly as `_damage()` does for a
        tower's — so a scheduled "surge" verb has to call this at both kill sites
        below, not just `_damage()`'s, to stay honest about how much charge a cast
        earns back from its own casualties.

        `Recoveries.surge_charge_mult()` (`ward-primer`, 1.25x) is a save-file input
        outside every grading path (decision 033) and is omitted here, the same as
        `sell_refund_bonus` already is in `sell()` — see that method's own comment."""
        cfg = self.tuning.abilities.get("surge")
        if cfg is None or cfg.charge_max <= 0.0 or cfg.charge_per_leak_cost <= 0.0:
            return
        self._surge_charge = min(
            cfg.charge_max,
            self._surge_charge + u.kind.leak_cost * cfg.charge_per_leak_cost)

    def _fire_surge(self, cfg: Ability) -> dict:
        """Mirrors scripts/anchor_sim.gd:731 fire_surge(). Ignores shielding entirely —
        no call to _shield_scale() below, matching the GDScript note that the discharge
        is the same energy the ring is made of. Every survivor, hit or not, is pushed
        back cfg.pushback_tiles."""
        kills = 0
        total_dealt = 0.0
        for u in self.units:
            if not u.alive:
                continue
            plen = self.a.path_length(u.lane)
            frac = (cfg.falloff_min if plen <= 0.0 else
                    cfg.falloff_min + (1.0 - cfg.falloff_min)
                    * min(max(u.dist / plen, 0.0), 1.0))
            dealt = max(0.0, cfg.damage * frac - u.kind.armour)
            if dealt > 0.0:
                u.hp -= dealt
                total_dealt += dealt
            if u.hp <= 0:
                u.alive = False
                self.funds += int(u.kind.bounty * self.bounty_mult)
                self._charge_surge(u)  # LF-163
                kills += 1
            else:
                u.dist = max(0.0, u.dist - cfg.pushback_tiles)
        return {"kills": kills, "damage": total_dealt}

    def _veteran_rank(self, p: Placed) -> VeterancyRank | None:
        """Mirrors scripts/anchor_sim.gd:301 _veteran_rank() — the highest rank `p`'s
        kill count has reached. Returns None (anchor_sim.gd's `{}`) whenever the policy
        has not opted into veterancy (`policy.veterancy`, BAL-01) OR the rank ladder
        itself is empty — either way an identity multiplier, so every policy that leaves
        `veterancy` at its default False sees no change at all, on every anchor, at
        every difficulty. `p.kills` is tracked unconditionally in `_step()` regardless of
        this flag, matching anchor_sim.gd's own comment on why that is safe."""
        if not self.policy.veterancy or not self.tuning.veterancy_ranks:
            return None
        best = None
        for r in self.tuning.veterancy_ranks:
            if p.kills >= r.kills:
                best = r
        return best

    # ─────────────────────────────────────────── BAL-01: schedule dispatch ──

    def _dispatch_schedule(self) -> None:
        """Drains every scheduled action whose time has passed. Called from
        `_tick_once()`, mirroring scripts/test/parity.gd's own dispatch call at the
        identical point in its driving loop. `policy.schedule` is already sorted on a
        total (time, index) order at Policy construction, so draining it in list order
        from `self._schedule_i` onward IS draining it in that total order."""
        sched = self.policy.schedule
        while (self._schedule_i < len(sched)
               and sched[self._schedule_i][0] <= self.t + 1e-9):
            _, verb, args = sched[self._schedule_i]
            self._schedule_i += 1
            self._dispatch_one(verb, args)

    def _dispatch_one(self, verb: str, args: dict) -> None:
        if verb == "speed":
            # No-op on outcomes, deliberately. The rules tick at a fixed DT regardless
            # of wall-clock pacing (see this module's own DT); "speed" multiplies ticks
            # PER WALL SECOND in scripts/anchor_view.gd's own _process(), a presentation
            # concept the headless sim has no equivalent of at all. BAL-01's own task
            # list requires this be PROVED a no-op rather than assumed — see the report
            # for the byte-identical run demonstrating it.
            return
        if verb == "call_wave":
            # Consumed by _advance() below, the only caller that knows how many lead-in
            # seconds remain — see call_wave()'s own docstring.
            self._call_wave_requested = True
            return
        if verb == "ability":
            kind = args.get("kind")
            if kind == "surge":
                # LF-163: charge-gated, mirroring scripts/abilities.gd's
                # `AbilityState.ready()` — a scheduled cast whose time has come but
                # which has not earned a full charge from kills yet is dispatched (the
                # schedule entry is still consumed) but does nothing, exactly as
                # scripts/anchor_view.gd's `activate_ability()` returns `{}` and plays
                # a deny cue rather than firing. Charge resets to 0 on an actual cast,
                # mirroring `AbilityState.began()`.
                cfg = self.tuning.abilities["surge"]
                if cfg.charge_max <= 0.0 or self._surge_charge >= cfg.charge_max:
                    self._fire_surge(cfg)
                    self._surge_charge = 0.0
            elif kind == "overcharge":
                active = bool(args.get("active", True))
                cfg = self.tuning.abilities["overcharge"]
                # Mirrors scripts/anchor_sim.gd:261 set_overcharge().
                self.overcharge_active = active
                self._overcharge_fire_rate_bonus = cfg.fire_rate_bonus if active else 0.0
                self._overcharge_draw_mult = cfg.draw_mult if active else 1.0
            elif kind == "shutter":
                active = bool(args.get("active", True))
                cfg = self.tuning.abilities["shutter"]
                # Mirrors scripts/anchor_sim.gd:270 set_shutter().
                self.shutter_active = active
                self._shutter_hold_tiles = cfg.hold_tiles if active else 0.0
                self._shutter_draw_mw = cfg.draw_mw if active else 0.0
            return
        if verb == "target_mode":
            # Mirrors scripts/anchor_sim.gd:620's `p.get("target_mode", "first")` match.
            idx = int(args["index"])
            if 0 <= idx < len(self.placed):
                self.placed[idx].target_mode = args.get("mode", "first")
            return
        if verb == "sell":
            self.sell(int(args["index"]))
            return
        if verb == "upgrade":
            self.upgrade(int(args["index"]))
            return
        if verb == "set_online":
            self.set_online(int(args["index"]), bool(args.get("on", True)))
            return
        if verb == "build":
            slot = args["slot"]
            self.build(str(args["tower"]), (int(slot[0]), int(slot[1])))
            return
        raise ValueError(f"unknown scheduled verb {verb!r}")

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

    def _progress(self, u: Unit) -> float:
        """Fraction of `u`'s own lane it has covered, in [0, 1+]. LF-145: the fire
        loop's targeting priority has to compare progress on a common 0..1 scale, not
        raw `dist` — raw distance is only directly comparable when every reachable
        unit shares one lane's length, and a multi-lane anchor with lanes of
        different length breaks that (anchor-09's 37-tile main lane against its
        14-tile flank meant a slot in range of both fired on main almost
        exclusively, since any main unit past dist 14 outscored every possible
        flank unit by raw dist alone). Mirrors scripts/anchor_sim.gd's _progress() —
        must stay identical. On a single-lane board every compared unit divides by
        the same constant (that lane's one path_length), an order-preserving
        positive scale, so this is a no-op there: it picks the identical unit raw
        `dist` did."""
        length = self.a.path_length(u.lane)
        return u.dist / length if length > 0.0 else 0.0

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
        # Overcharge's fire_rate_bonus, applied ON TOP of the brownout penalty rather
        # than instead of it — mirrors scripts/anchor_sim.gd:561's own comment: `rate`
        # can still fall below 1.0 with the bonus applied, which is what makes
        # overcharging a saturated bus a loss rather than a free lunch. Defaults to
        # `overcharge_active = False`, so this is a no-op for every policy that never
        # schedules the ability.
        if self.overcharge_active:
            rate *= (1.0 + self._overcharge_fire_rate_bonus)

        # move
        for u in self.units:
            if not u.alive:
                continue
            x, y = self.a.point_at(u.lane, u.dist)
            slow = self._covered_by("slow", x, y)
            speed = u.kind.speed * (slow if slow else 1.0)
            # Shutter holds anything already inside hold_tiles of the entrance at zero
            # speed while it is down. Mirrors scripts/anchor_sim.gd:574. Defaults to
            # `shutter_active = False`, so this changes nothing for a graded run unless
            # a policy schedules it.
            if self.shutter_active and u.dist <= self._shutter_hold_tiles:
                speed = 0.0
            u.dist += speed * DT
            if u.dist >= self.a.path_length(u.lane):
                u.alive = False
                self.leaks += 1
                # Priced by the unit, not a flat 1. Decision 047: with a flat cost, three
                # 170 hp units in place of one 520 hp one tripled the tension along with
                # the count, so density could only be bought with lives. `leaks` still
                # counts bodies — it is a different question from what they cost.
                self.lives -= u.kind.leak_cost

        # fire — furthest-along reachable target, a total order, so no RNG needed.
        # "Furthest along" means furthest along ITS OWN lane, as a fraction of that
        # lane's length (_progress(), LF-145) — not raw dist, which only ties across
        # lanes of equal length and otherwise structurally favours whichever lane is
        # longest. See _progress()'s docstring for the anchor-09 case that surfaced it.
        for p in self.placed:
            if not p.online or not p.tower.is_weapon:
                continue
            p.cooldown -= DT * rate
            if p.cooldown > 0:
                continue
            # Veterancy: identity (1.0, 1.0) whenever policy.veterancy is False (every
            # existing policy) or the rank ladder is empty — see _veteran_rank()'s own
            # docstring. Mirrors scripts/anchor_sim.gd:598.
            vet = self._veteran_rank(p)
            dmg_mult = vet.damage_mult if vet is not None else 1.0
            rng_mult = vet.range_mult if vet is not None else 1.0
            rng = p.tower.range * rng_mult
            # Per-emplacement targeting priority (BAL-01; data/tuning.json's
            # `targeting`). "first" is the default, so a placed record nobody has
            # touched (every one _try_build()/build() places) falls to the final `else`
            # below — the identical _progress() comparison this loop always ran before
            # this verb existed. Mirrors scripts/anchor_sim.gd:620's `match mode`.
            mode = p.target_mode
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
                x, y = self.a.point_at(u.lane, u.dist)
                dx, dy = p.slot[0] - x, p.slot[1] - y
                if dx * dx + dy * dy > rng * rng:
                    continue
                revealed = u.kind.kind != "air" or self._covered_by("reveal", x, y) > 0
                if not self._can_target(p.tower, u, revealed):
                    continue
                if mode == "last":
                    keep = target is None or u.dist < target.dist
                elif mode == "strongest":
                    keep = target is None or u.hp > target.hp
                elif mode == "weakest":
                    keep = target is None or u.hp < target.hp
                else:
                    keep = target is None or self._progress(u) > self._progress(target)
                if keep:
                    target = u
            if target is None:
                # Retry next tick rather than scanning every tick forever (LF-098) — see
                # the gate above. Rejected a longer retry interval: it would cut more
                # cost, but delays "acquires the frame a unit enters range" by that same
                # interval, changing which tick an idle gun re-acquires on — a rules
                # change, not a performance one.
                continue
            killed = self._damage(target, p.tower, dmg_mult=dmg_mult)
            # Kills are counted on `p`, never on a unit — a Placed record is only ever
            # compared by `slot` (LF-055), so growing this field is safe the same way
            # `aim`/`kills` already are on scripts/anchor_sim.gd's own placed records.
            # Tracked unconditionally, independent of policy.veterancy, matching
            # anchor_sim.gd's own comment on why that is safe.
            if killed:
                p.kills += 1
            if p.tower.splash > 0:
                tx, ty = self.a.point_at(target.lane, target.dist)
                for u in self.units:
                    if u is target or not u.alive:
                        continue
                    ux, uy = self.a.point_at(u.lane, u.dist)
                    dx, dy = ux - tx, uy - ty
                    if dx * dx + dy * dy <= p.tower.splash * p.tower.splash:
                        if self._damage(u, p.tower, scale=0.5, dmg_mult=dmg_mult):
                            p.kills += 1
            p.cooldown = p.tower.fire_interval

    def _damage(self, u: Unit, tower: Tower, scale: float = 1.0,
                dmg_mult: float = 1.0) -> bool:
        # Armour first, then the shield tax on what got through. The other order makes
        # a shielded armoured unit immune rather than expensive — 9 damage taxed to 3.15
        # never clears 5 armour, so the bulwark could only be killed by the lance and
        # anchor-14 graded unwinnable at every setting swept.
        #
        # `dmg_mult` defaults to 1.0 (identity, no rounding introduced — IEEE-754 exact)
        # — it is veterancy's damage_mult (_veteran_rank()), applied here rather than by
        # scaling `tower.damage` itself because `tower` may be the *shared* Tower every
        # Placed record of that type points at; scaling it in place would buff that
        # tower type for every board this process ever grades. Mirrors
        # scripts/anchor_sim.gd:703's identical reasoning (there: a shared Content
        # dictionary; here: a shared frozen Tower).
        after_armour = max(0.0, tower.damage * scale * dmg_mult - u.kind.armour)
        u.hp -= after_armour * self._shield_scale(tower, u)
        if u.hp <= 0:
            u.alive = False
            self.funds += int(u.kind.bounty * self.bounty_mult)
            self._charge_surge(u)  # LF-163
            return True
        return False

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

            # (time, lane, enemy_id): a two-lane anchor makes simultaneous spawns the
            # normal case, not the rare one, so the tie-break has to be a total order in
            # both languages, not merely "usually agrees" (WAR-01's risk note). The
            # GDScript port's wave_queue() sorts the identical three-key tuple.
            queue: list[tuple[float, int, str]] = []
            for sp in wave.spawns:
                for n in range(sp.count):
                    queue.append((sp.delay + n * sp.interval, sp.lane, sp.enemy))
            queue.sort(key=lambda q: (q[0], q[1], q[2]))

            wave_t, qi = 0.0, 0
            while True:
                while qi < len(queue) and queue[qi][0] <= wave_t + 1e-9:
                    _, lane, enemy_id = queue[qi]
                    e = self.enemies[enemy_id]
                    self.units.append(Unit(kind=e, hp=e.hp * self.hp_mult, lane=lane))
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
        # BAL-01: dispatch every scheduled action whose time has passed, AFTER `t` has
        # advanced to this tick's own value but BEFORE `_step()` — so an action taken at
        # time t affects the tick at t, on both implementations. `policy.schedule` is
        # empty for every one of the original policies, so this is a single length-0
        # while-loop check for them: no float touched, no state written.
        self._dispatch_schedule()
        self._step(penalty)

    def _advance(self, seconds: float) -> None:
        # BAL-01: `call_wave` can only meaningfully end THIS lead-in — _advance() is the
        # only caller that knows both "how many ticks are left in it" and "is this a
        # lead-in at all" (the main wave-combat loop in run() below never calls
        # _advance()). Reset at the top so a flag that (mis-authored) fired during the
        # PREVIOUS wave's combat can never bleed into skipping this one's lead-in.
        self._call_wave_requested = False
        n = int(seconds / DT)
        for i in range(n):
            self._tick_once()
            if self._call_wave_requested:
                remaining = (n - i - 1) * DT
                self.call_wave(remaining, self.tuning.call_bonus_per_sec)
                self._call_wave_requested = False
                break


def _overcharge_schedule() -> list[tuple[float, str, dict]]:
    """The `overcharge-greedy` policy's schedule (BAL-01) — engages Overcharge on
    tuning.json's own cadence (35s cooldown + 7s duration = 42s) for the whole run.
    Its first activation is a DELIBERATE same-timestamp pair: False authored before
    True at an identical time (5.0) must net ON if dispatch honours authored order —
    this is the acceptance criterion's same-timestamp-order proof. Mirrored 1:1 in
    scripts/test/parity.gd's `_overcharge_schedule()`, including the literal 42.0/7.0
    (data/tuning.json's overcharge cooldown_s/duration_s, hardcoded here rather than
    re-derived from `tuning` at construction time — a schedule is authored once, and a
    future edit to those tuning values does not automatically retime this schedule;
    that coupling is a known, accepted debt, not an oversight)."""
    sched: list[tuple[float, str, dict]] = [
        (5.0, "ability", {"kind": "overcharge", "active": False}),
        (5.0, "ability", {"kind": "overcharge", "active": True}),
    ]
    for i in range(1, 16):
        on_t = 5.0 + i * 42.0
        sched.append((on_t, "ability", {"kind": "overcharge", "active": True}))
        sched.append((on_t + 7.0, "ability", {"kind": "overcharge", "active": False}))
    sched.append((5.0 + 7.0, "ability", {"kind": "overcharge", "active": False}))
    return sched


def standard_policies(tower_ids: list[str], tuning: Tuning | None = None) -> list[Policy]:
    """A small, fixed set of distinct playstyles. Deterministic and ordered.

    Not an exhaustive search — the point is to answer "does more than one sensible
    approach work", not to find the optimum. An optimiser would report that every
    anchor is winnable by some build and tell us nothing about whether it is fun.

    `tuning` is optional and keyword-compatible with every existing call site
    (tools/test_parity.py, sim/run.py, tools/bench_tick.py all call this with one
    positional arg) — BAL-01's four scheduled/opted-in policies at the end of `out`
    below need ability magnitudes to build their schedules, and default to loading
    data/tuning.json themselves rather than requiring every caller to change.
    """
    tn = tuning if tuning is not None else load_tuning()
    # Fail fast at policy-construction time rather than deep inside a Sim run: the
    # scheduled policies below reference these ids by name, and the dispatch code in
    # Sim._dispatch_one() would otherwise raise a bare KeyError with no anchor/wave
    # context attached.
    for aid in ("surge", "overcharge"):
        if aid not in tn.abilities:
            raise ValueError(
                f"data/tuning.json has no ability {aid!r}, needed by a BAL-01 "
                f"scheduled policy in standard_policies()")

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

        # ── BAL-01: scheduled / opted-in policies ───────────────────────────────
        # Every one above leaves `schedule`/`veterancy` at their defaults, which is
        # exactly what makes them byte-identical to the pre-BAL-01 grader (see the
        # report). These four are the first policies that press a button.

        # "call-early": converts wave 1's ENTIRE lead-in to funds on the very first
        # tick — proves pacing.call_bonus_per_sec reaches the grader (changing that
        # tuning value changes only this policy's spend/funds numbers). Only wave 1:
        # a schedule is authored in absolute sim-time, and every later wave's start
        # time depends on how long combat took, which is only known by running the
        # sim — BAL-01 rejected wave-relative scheduling for exactly that reason (see
        # Policy.__init__'s own comment), so "call early on every wave" is not
        # expressible without it.
        Policy("call-early", rest(has("pulse-turret")),
               schedule=[(0.0, "call_wave", {})]),

        # "surge-on-peak": fires Threshold Surge on a fixed cadence for the whole run,
        # standing in for "the player presses it the instant it is ready" without any
        # reactive/live-state read (surge has no charge/cooldown model in this file at
        # all — see _fire_surge()'s docstring — so this is the deliberately generous
        # instrument BAL-01 asks for to surface whether the ability is overpowered).
        Policy("surge-on-peak", rest(has("pulse-turret")),
               schedule=[(15.0 + i * 40.0, "ability", {"kind": "surge"})
                         for i in range(20)]),

        # "overcharge-greedy": engages Overcharge on tuning.json's own cooldown/
        # duration cadence (35s/7s) for the whole run. Its FIRST activation is a
        # deliberate same-timestamp pair — False authored before True at an identical
        # time (5.0) — which must net ON if dispatch honours authored order rather
        # than, say, reversing it or breaking the tie some other way. This is the
        # acceptance criterion's "two scheduled actions at the same timestamp dispatch
        # in authored order", mirrored identically in scripts/test/parity.gd.
        Policy("overcharge-greedy", rest(has("ion-lance") + has("pulse-turret")),
               schedule=_overcharge_schedule()),

        # "veteran-crews": opts into the real veterancy ladder from data/tuning.json
        # instead of grading policy.veterancy=False's inert default — every emplacement
        # this policy places earns damage_mult/range_mult as it racks up kills, exactly
        # as scripts/anchor_sim.gd's _veteran_rank() already computes for real play.
        # Not a schedule: veterancy is one of BAL-01's "unconditional rules", standing
        # in for "the save file already has ranks resolved at boot" rather than a
        # mid-run button press (see Policy.__init__'s own comment).
        Policy("veteran-crews", rest(has("pulse-turret") + has("ion-lance")),
               veterancy=True),
    ]
    return out
