#!/usr/bin/env python3
"""
Report what a wave actually contains, per anchor and per act.

LF-044 is the observation that Act III fields fewer units per wave than the tutorial.
That claim was made once from a number nobody could recompute, and the fix that followed
it was wrong (see decision 047 and the disproved premise recorded with it). So the
measurement lives in the repo rather than in a session transcript.

Four numbers describe a wave, and they are not interchangeable:

    units       how much is on screen — the thing LF-044 is about
    leak        the wave's total leak_cost, i.e. what it costs to ignore it entirely
    hp          how much work the board has to do
    drain       megawatts stolen if every unit in the wave is alive at once

`drain` is the one that limits density from Act II on: every Sable Reach and Hollow unit
draws on the bus while it walks, so doubling the unit count doubles the bus theft. A
density plan that ignores it prices the bus out of existence. `bus` shows that headroom —
wave drain against the capacity actually available on that wave, after Act III decay.

PLC-05 / LF-107 added a fifth: `sat%`, the fraction of board saturation (capacity_mw
against the emplacement-count cap -- `max_emplacements` when authored, else len(slots) --
times the hungriest unlocked emplacement's max draw). This is a distinct axis from `bus`
above: `bus` is wave drain against capacity (does the reactor cover what is walking right
now), `sat%` is capacity against the board's OWN ceiling (does the power decision still
exist at all). anchor-24 once reached 103% of it while every anchor still graded `ok` and
`bus` said nothing was wrong -- the grader cannot see this failure, only this measurement
can. Act I sits at 29-38%; `tools/validate/validate_data.py` warns above 80% and errors at
or above 100%.

    .venv/bin/python tools/density.py               # every anchor, act summary
    .venv/bin/python tools/density.py anchor-20     # per-wave detail

## WHAT "SCREEN PRESENCE" IS — BAL-04 asked and this is the answer

**Total units in flight, summed across every lane. The camera does not enter it.** Three
candidates were on the table and the other two were rejected on measurements, not taste.

*Units within the current view* is the reading BAL-04 was really asking about, and it is not
a property of the content at all. `scripts/anchor_view.gd` (CAM-01) gives the player a free
camera — middle-drag pan, WASD, edge-scroll, cursor-follow — and a zoom over
`[_min_zoom_for_board(), 1.0]` whose floor is **derived per board from the strip that happens
to be available right now**. So the count would depend on where the player last dragged, and
on a setting in the options menu: the board strip is 940 px at 100% interface scale and
**508 px at 200%** (the `playfield width` gate check prints the ladder), against a board
`(w + h) * 128 / 2` = 2112 px wide at zoom 1.0 for an 18x15 anchor. The same wave is
somewhere between 24% and 100% on screen depending on a scale the player chose for
legibility. A gate check reading that number would be asserting a fact about `Ui.COL_W`.

*Peak units in one lane* is the WAR-01 reading, it is measured, and it is deliberately not
the gated one. `peak_concurrent_per_lane()` below prints it for any anchor with more than one
lane, because "four lanes of eight" and "one lane of thirty-two" are a completely different
front line — but it answers "what must one defended lane hold", which is a difficulty
question the grader already owns. It is also untestable as a gate today: **23 of the 24
shipped anchors have exactly one lane**, so a bound over it would be a branch the content
never enters, which is LF-229's and decision 078's lesson. (anchor-09 is the one exception,
lanes of 37 and 14 tiles, peaking at 23 and 3.)

*Total units in flight* ships, and the argument for it is stronger than "the other two lose".
Decision 082 derives every emplacement's range by holding **own-lane coverage** fixed: a gun
reaches a constant share `c` of its lane's length, median 17.4% for the pulse turret. The
units inside one gun's envelope are therefore `(peak / lane_len) * c * lane_len = c * peak` —
**proportional to the raw peak and independent of how long the lane is**. Normalising by lane
length (occupancy, units per tile) would divide out exactly the term decision 082 holds
constant, and it is not a cosmetic difference: occupancy reads 0.763 / 0.625 / 0.507 per act
where the raw peak reads 26.2 / 27.1 / 21.1, which moves the busiest act from 2 back to 1.

## PER-ACT DENSITY, MEASURED ON `ce01b05`

    act   units/w   on screen   leak/w   hp/w   hp/unit   drain/w   peak bus   sat%
      1      16.3        26.2     17.7    950      58.2       0.0         0%    31%
      2      18.1        27.1     18.9   1113      61.6      39.4        29%    40%
      3      13.6        21.1     17.4   1668     122.7      57.8        39%    50%

Act 1's figures are decision 091's (`warden-hauler`, three drones fused into one body, so
`hp/w` is unchanged at 950 while units and presence fall); act 2 is now the busiest act and
act 3 sits at 78% of it. `tools/check.py`'s `DENSITY_FLOOR` is what holds that spread, and
the derivation for why the constant did **not** move is in the comment above it.

Presence is an axis nothing else in the gate can see, and the LF-044 defect is the proof.
Recomputed at `c048141`, the commit where Act III was complete and thin:

    act   on screen   units/w   hp/w   hp/unit          today: on screen   hp/w
      1        32.4      20.2    950      46.9                      26.2    950
      2        15.6       9.6    734      76.3                      27.1   1113
      3        11.1       7.7   1562     203.3                      21.1   1668

Act III showed **a third of Act I's units while carrying 64% more hit points per wave**.
Difficulty and presence did not merely vary independently there, they moved in *opposite*
directions — so a grader asking "is this winnable, and is it hard" reported Act III as the
hardest act in the game, which it was. That is why the measurement exists in this file
rather than as a line in a grade table.

## THE LEAK BUDGET HAS THREE READINGS, AND THEY DISAGREE

A leak costs `max(1, round(hp/130))` (decision 047), so `lives` is spent in a currency whose
exchange rate depends on which unit gets through. Two bounds are pure data and are printed:

    budget      lives / total leak_cost — the WORST case, every leak the dearest thing here.
                This is the authoring denominator, and the form `sweep.py --lives %12` takes.
    bodies      lives / total unit count — the BEST case, every leak at the 1-life minimum.

The truth is between them and was measured, on the `ce01b05` grade (24 anchors x 63 runs):

    act    budget   bodies      median lives left      leak-free
           (worst)  (best)      on a winning run       wins
      1     10.6%    11.5%              70%              34%
      2     11.3%    12.0%              42%              11%
      3     20.4%    26.1%              48%               0%

**The realised cost of a leak is close to 1 everywhere, nowhere near the roster maximum.**
Two estimators bracket it and they are both reported because they disagree in *direction*:
excluding runs that bottomed out (lives clamped at 0, which truncates the last leak) gives
1.50 / 1.06 / 1.06 per act; including them gives 1.10 / 1.15 / 1.41. Act III's dearest unit
costs 4. So decision 047's weighting is realised at a small fraction of its authored size,
and **no claim is made here about which act's leaks are dearer** — the estimators do not
support one.

The figure that needs no denominator at all is the margin, and it is the one to read: the
**median share of `lives` still standing when a winning run ends** — 70 / 42 / 48%. On that
reading Act III is not slack; it is tighter than Act I.

And the number that explains the whole spread is the last column. **No winning run in Act III
leaks nothing** — 0 of 123, against 34% of Act I's winners. Act III's larger `lives` pool is
not generosity, it is the price of admission: a budget phrased as "the share of the threat you
may ignore" assumes ignoring is a choice, and by Act III it has stopped being one. That is
why `budget` roughly doubles across the campaign without Act III getting easier.

**One anchor is out of band on every reading and is reported, not changed** (BAL-04's slice
discipline: an instrument change and a content change cannot be attributed together).
anchor-17 carries 18 lives against a total leak_cost of 112 — a 16.1% budget where the other
seven Act III anchors sit inside 20.7-21.3%, a band tight enough to have been authored to.
Act III's own band puts anchor-17 at 23 lives. It grades `ok` at 18 and nothing here changes
it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import (DATA, Anchor, Enemy, Wave, Tower, all_anchor_ids,  # noqa: E402
                         load_anchor, load_enemies, load_towers)


def _tower_max_draw(t: Tower) -> float:
    """Mirrors tools/validate/validate_data.py's `_tower_max_draw()` (LF-103) and
    tools/sweep.py's copy of the same: an upgrade REPLACES the base stat it names rather
    than adding to it, so a tower's true worst-case draw is max(base, upgrade). If any
    one of the three copies changes, move the other two with it."""
    up = t.upgrade.get("draw_mw") if t.upgrade else None
    return max(t.draw_mw, up) if up is not None else t.draw_mw


def build_tower_ids(built: list[str]) -> list[str]:
    """The tower ids in a graded run's `built` list, one per placement, order preserved.

    LF-270. A `built` entry is `"<tower-id>@<x>,<y>"`, so a membership test against bare
    tower ids matches **nothing** and reports a confident zero — which is the whole failure:
    it does not raise, it answers. That had been hand-rolled four times in four sessions by
    the time BAL-04 asked for it a fifth, so it lives here, next to the other "measure the
    shipped content" helpers and in the one tools module `tools/check.py` already imports.

    Split on the FIRST `@` only. Tower ids are kebab-case and coordinates are digits and a
    comma, so a second `@` cannot occur today — but `rsplit` would be the wrong choice the
    day a slot label carries one, and this is the kind of thing nobody re-reads.
    """
    return [b.split("@", 1)[0] for b in built]


def weapon_ids_in_build(built: list[str], towers: dict[str, Tower]) -> set[str]:
    """The distinct *weapon* ids in a graded run's build. `is_weapon` is `damage > 0`, so
    scan relays, dampers, shield walls and restorers are excluded — a build of a turret and
    three support emplacements is one weapon class, not four. Pairs with build_tower_ids();
    see LF-270 there for why this is not written inline at each call site."""
    return {t for t in build_tower_ids(built) if t in towers and towers[t].is_weapon}


def saturation_stats(a: Anchor, towers: dict[str, Tower]) -> dict[str, float | str]:
    """PLC-05 / LF-107's board-saturation invariant, measured rather than merely
    enforced. Same denominator tools/validate/validate_data.py checks capacity_mw
    against: `max_emplacements` when the anchor authors one, else len(slots) --
    data/schema/anchor.schema.json's root `anyOf` guarantees one of the two is present on
    every anchor. Reads the anchor's raw JSON directly for `max_emplacements`, since
    `sim.content`'s `Anchor` dataclass does not carry that field.

    This is the "measure first" table PLC-05's own task list calls for: printing
    len(slots)/max_emplacements, max draw, the saturated MW, and capacity_mw as a
    fraction of it for every anchor is what proves a denominator rewrite provably
    neutral, rather than merely arguing it.
    """
    raw = json.loads((DATA / "anchors" / f"{a.id}.json").read_text())
    avail = [t for t in towers.values() if t.unlocked_at <= a.id]
    max_draw = max(_tower_max_draw(t) for t in avail)
    explicit_cap = raw.get("max_emplacements")
    if explicit_cap is not None:
        cap_n, label = int(explicit_cap), "max_emplacements"
    else:
        cap_n, label = len(a.slots), "slots"
    saturated = cap_n * max_draw
    return {
        "cap_n": float(cap_n),
        "denom_label": label,
        "max_draw": max_draw,
        "saturated_mw": saturated,
        "sat_frac": a.capacity_mw / saturated if saturated else 0.0,
    }


def wave_stats(w: Wave, enemies: dict[str, Enemy]) -> dict[str, float]:
    units = leak = hp = drain = 0.0
    for s in w.spawns:
        e = enemies[s.enemy]
        units += s.count
        leak += s.count * e.leak_cost
        hp += s.count * e.hp
        drain += s.count * e.drains_mw
    return {"units": units, "leak": leak, "hp": hp, "drain": drain}


def peak_concurrent(a: Anchor, enemies: dict[str, Enemy]) -> int:
    """Most units in flight at once, over the anchor's busiest wave, if nothing died.

    This is the honest reading of "how much is on screen", and it is not units-per-wave:
    a wave of Columns at 0.5 tiles/sec occupies the board four times as long as the same
    count of Shards, so a table with fewer, slower units can be *busier* than a table with
    more. An upper bound by construction — it assumes the board kills nothing — which is
    what makes it comparable across acts whose emplacements differ.

    Totalled across every lane — screen presence does not care which lane a unit is
    walking. See peak_concurrent_per_lane() for the WAR-01 breakdown: "four lanes of
    eight" and "one lane of thirty-two" read identically here by design.
    """
    best = 0
    for w in a.waves:
        events: list[tuple[float, int]] = []
        for s in w.spawns:
            e = enemies[s.enemy]
            dwell = a.path_length(s.lane) / e.speed
            for k in range(s.count):
                t = s.delay + k * s.interval
                events.append((t, 1))
                events.append((t + dwell, -1))
        events.sort()
        live = 0
        for _, delta in events:
            live += delta
            best = max(best, live)
    return best


def peak_concurrent_per_lane(a: Anchor, enemies: dict[str, Enemy]) -> dict[int, int]:
    """Same measure as peak_concurrent(), split by lane (WAR-01).

    "Four lanes of eight" and "one lane of thirty-two" are the same total but a
    completely different front line; this is what tells the two apart. A single-lane
    anchor reports exactly one entry, equal to peak_concurrent()'s total.
    """
    out: dict[int, int] = {i: 0 for i in range(len(a.lanes))}
    for w in a.waves:
        events_by_lane: dict[int, list[tuple[float, int]]] = {
            i: [] for i in range(len(a.lanes))}
        for s in w.spawns:
            e = enemies[s.enemy]
            dwell = a.path_length(s.lane) / e.speed
            for k in range(s.count):
                t = s.delay + k * s.interval
                events_by_lane[s.lane].append((t, 1))
                events_by_lane[s.lane].append((t + dwell, -1))
        for lane, events in events_by_lane.items():
            events.sort()
            live = 0
            for _, delta in events:
                live += delta
                out[lane] = max(out[lane], live)
    return out


def capacity_on_wave(a: Anchor, index: int) -> float:
    """Rated capacity minus Act III decay, floored at 45% of rated. Mirrors engine.py."""
    if not a.capacity_decay_mw:
        return a.capacity_mw
    return max(a.capacity_mw * 0.45, a.capacity_mw - a.capacity_decay_mw * index)


def terrain_stats(a: Anchor) -> dict[str, float]:
    """Terrain presence: how much of the board actually has relief in it.

    TER-02 added the `terrain` schema and a shared resolve_terrain() but deliberately
    wired it into exactly one pilot anchor — "terrain data exists but nothing in the rules
    reads it yet" is the issue's own acceptance criterion. So this reports what is
    *measurable* (levels used, % of the board raised above 0) rather than what is merely
    claimed, the same reasoning LF-044 already forced onto unit density: a "terrain that
    means something" claim should be a number, not an assertion. `a.levels` is the dense
    grid resolve_terrain() produced at load time (sim/content.py); an anchor with no
    `terrain` key resolves to an all-zero grid, so this is exactly `0, 0.0%` for every
    anchor except the pilot until more anchors get a terrain pass.
    """
    tiles = [v for row in a.levels for v in row]
    total = len(tiles)
    raised = sum(1 for v in tiles if v > 0)
    return {
        "levels_used": float(len({v for v in tiles if v > 0})),
        "max_level": float(max(tiles) if tiles else 0),
        "raised_pct": raised / total if total else 0.0,
    }


def anchor_rows(a: Anchor, enemies: dict[str, Enemy]) -> list[dict[str, float]]:
    rows = []
    for i, w in enumerate(a.waves):
        st = wave_stats(w, enemies)
        st["wave"] = i + 1
        st["cap"] = capacity_on_wave(a, i)
        st["bus"] = st["drain"] / st["cap"] if st["cap"] else 0.0
        rows.append(st)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure wave density, leak cost and bus theft.")
    ap.add_argument("anchor", nargs="?", help="one anchor id; omit for the whole game")
    args = ap.parse_args()

    enemies = load_enemies()
    towers = load_towers()

    if args.anchor:
        a = load_anchor(args.anchor)
        t = terrain_stats(a)
        s = saturation_stats(a, towers)
        terrain_line = (f"flat (no terrain)" if t["raised_pct"] == 0.0 else
                         f"{t['raised_pct']:.1%} of board raised, "
                         f"{t['levels_used']:.0f} level(s) used (max {t['max_level']:.0f})")
        print(f"{a.id}  {a.title}  ·  act {a.act} · {a.capacity_mw:.0f} MW "
              f"· {a.lives} lives · decay {a.capacity_decay_mw:.0f}/wave · terrain: "
              f"{terrain_line} · {len(a.lanes)} lane(s): "
              f"{', '.join(l.id for l in a.lanes)}\n")
        print(f"board saturation: {s['cap_n']:.0f} [{s['denom_label']}] x "
              f"{s['max_draw']:.0f} MW max draw = {s['saturated_mw']:.0f} MW · "
              f"capacity {a.capacity_mw:.0f} MW is {s['sat_frac']:.0%} of it\n")
        print(f"{'wave':>4s} {'units':>6s} {'leak':>5s} {'hp':>6s} "
              f"{'drain':>6s} {'cap':>5s} {'bus':>5s}")
        for r in anchor_rows(a, enemies):
            print(f"{r['wave']:>4.0f} {r['units']:>6.0f} {r['leak']:>5.0f} {r['hp']:>6.0f} "
                  f"{r['drain']:>6.0f} {r['cap']:>5.0f} {r['bus']:>5.0%}")
        tot = sum(r["leak"] for r in anchor_rows(a, enemies))
        units = sum(r["units"] for r in anchor_rows(a, enemies))
        # Both bounds, never one: `budget` prices every leak at the dearest unit here and
        # `bodies` at the 1-life minimum, and the realised figure is between. See the
        # docstring — on Act III they differ by a fifth, so quoting either alone as "the"
        # leak budget is how a fifth of the margin goes missing.
        print(f"\nlives {a.lives} · {units:.0f} units · total leak_cost {tot:.0f} · "
              f"leak budget {a.lives / tot:.1%} (worst case) to "
              f"{a.lives / units:.1%} (every leak a 1)")
        # WAR-01: "four lanes of eight" and "one lane of thirty-two" are the same total
        # onscreen count and a completely different front line — this is what tells them
        # apart. A single-lane anchor prints exactly one row, equal to the total below.
        if len(a.lanes) > 1:
            per_lane = peak_concurrent_per_lane(a, enemies)
            print(f"\nper-lane peak onscreen:")
            for li, lane in enumerate(a.lanes):
                print(f"  lane {li} ({lane.id}): {per_lane[li]}")
        print(f"total peak onscreen: {peak_concurrent(a, enemies)}")
        return 0

    print(f"{'anchor':>9s} {'act':>3s} {'waves':>5s} {'units/w':>7s} {'onscreen':>8s} "
          f"{'leak/w':>6s} {'hp/w':>6s} {'drain/w':>7s} {'peak bus':>8s} {'lives':>5s} "
          f"{'budget':>6s} {'bodies':>6s} {'terrain':>7s} {'sat%':>5s}")
    acts: dict[int, list[dict]] = {}
    for aid in all_anchor_ids():
        a = load_anchor(aid)
        rows = anchor_rows(a, enemies)
        n = len(rows)
        agg = {k: sum(r[k] for r in rows) / n for k in ("units", "leak", "hp", "drain")}
        agg["peak_bus"] = max(r["bus"] for r in rows)
        agg["onscreen"] = float(peak_concurrent(a, enemies))
        agg["lives"] = a.lives
        agg["budget"] = a.lives / sum(r["leak"] for r in rows)
        # BAL-04. `budget`'s denominator assumes every leak costs the dearest thing in the
        # anchor; `bodies`' assumes every leak costs the 1-life minimum. They bracket the
        # realised figure, which the docstring records as measured (it needs a graded run
        # and this tool must stay pure data — `tools/check.py` imports peak_concurrent()
        # from here on every tier-1 gate). On Act III the two differ by a fifth.
        agg["bodies"] = a.lives / sum(r["units"] for r in rows)
        t = terrain_stats(a)
        agg["terrain_pct"] = t["raised_pct"]
        s = saturation_stats(a, towers)
        agg["sat_frac"] = s["sat_frac"]
        acts.setdefault(a.act, []).append(agg)
        print(f"{aid:>9s} {a.act:>3d} {n:>5d} {agg['units']:>7.1f} {agg['onscreen']:>8.0f} "
              f"{agg['leak']:>6.1f} {agg['hp']:>6.0f} {agg['drain']:>7.1f} "
              f"{agg['peak_bus']:>7.0%} {a.lives:>5d} {agg['budget']:>5.1%} "
              f"{agg['bodies']:>5.1%} {agg['terrain_pct']:>6.0%} {agg['sat_frac']:>4.0%}")

    print(f"\n{'act':>9s} {'units/w':>7s} {'onscreen':>8s} {'leak/w':>6s} {'hp/w':>6s} "
          f"{'hp/unit':>7s} {'drain/w':>7s} {'peak bus':>8s} {'budget':>6s} {'bodies':>6s} "
          f"{'sat%':>5s}")
    for act in sorted(acts):
        rs = acts[act]
        m = {k: sum(r[k] for r in rs) / len(rs)
             for k in ("units", "onscreen", "leak", "hp", "drain", "peak_bus", "budget",
                       "bodies", "sat_frac")}
        print(f"{'act ' + str(act):>9s} {m['units']:>7.1f} {m['onscreen']:>8.1f} "
              f"{m['leak']:>6.1f} {m['hp']:>6.0f} {m['hp'] / m['units']:>7.1f} "
              f"{m['drain']:>7.1f} {m['peak_bus']:>7.0%} "
              f"{m['budget']:>5.1%} {m['bodies']:>5.1%} {m['sat_frac']:>4.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
