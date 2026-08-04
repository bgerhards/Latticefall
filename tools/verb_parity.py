#!/usr/bin/env python3
"""
LF-244's scheduled-verb probe: drive sim/engine.py and scripts/anchor_sim.gd over the same
board of player actions and prove they agree, tick by tick, about what each verb did.

WHY THIS EXISTS, and it is the same hole PLC-03 found in a different wall.
`Sim._dispatch_one()` accepts eight verbs. Across all twenty distinct policies
`standard_policies()` ever returns, exactly two are ever scheduled: `call_wave` (one
policy) and `ability` (surge and overcharge, three policies). So every one of the 1,440
`rules parity` runs executes the **absent** branch for `target_mode`, `sell`, `upgrade`,
`set_online`, `build`, the shutter ability and `speed` — and says nothing whatever about
the present one. `scripts/test/parity.gd:317` has mirrored the upgrade dispatch since
BAL-01 and nothing has ever driven it.

These are not inert paths the way an unauthored firing arc is. **The player uses all of
them, every session** — the build button, the sell button, the upgrade button, the online
toggle, the target-mode selector and the speed control are the interface. An upgrade that
merges its stats differently in the two engines would mean every balance conclusion about
an upgraded board describes a game nobody plays, and the owner plays the Windows build,
which decision 078 exists because of.

FIVE CLAIMS, in increasing order of what they would catch:

1. **The two engines agree byte for byte** on the fire pattern, funds, spend, bus load,
   emplacement count, every unit's distance and every unit's hit points, on every tick.
2. **The funds and spend trajectory matches arithmetic done here**, from the fixture's own
   costs — 2000 starting, minus three builds, minus an upgrade, minus a mid-run build,
   plus `floor(400 * SELL_REFUND)`. Two engines wrong the same way still fail.
3. **`upgrade` is proved geometrically, not by reading a stat back.** `verb-probe-reach`
   stands 4.0 tiles off a straight lane with range 3.0 and therefore *cannot* fire. After
   the upgrade merges range 8.0 it can. So the check is: zero shots before the upgrade
   tick, and after it, every tick it fires on has some unit within 8.0 and none within
   3.0. An engine that merged the wrong key, or merged nothing, cannot satisfy that.
4. **`set_online` and the shutter are proved by the bus.** Load while emplacement 2 is off
   must be exactly 30.0 MW below the same board with it on — no other subset of the
   fixture's draws sums to 30 — and the shutter must add exactly its tuning `draw_mw`
   while holding a unit inside `hold_tiles` at a standstill.
5. **`speed` is proved to be the no-op the engine's own comment claims.** BAL-01's task
   list required that be demonstrated rather than asserted; `--no-speed` re-runs the whole
   fixture with the `speed` actions stripped and requires the output to be identical.

    .venv/bin/python tools/verb_parity.py
    .venv/bin/python tools/verb_parity.py --report    # the timeline it measured
    .venv/bin/python tools/verb_parity.py --corrupt upgrade   # prove it goes red
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import (anchor_from_doc, enemies_from_rows,  # noqa: E402
                         load_tuning, towers_from_rows)
from sim.engine import SELL_REFUND, Policy, Sim, Unit  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import toolpaths  # noqa: E402

FIXTURE = ROOT / "data" / "schema" / "fixtures" / "scheduled-verbs.json"

# The verbs this fixture is responsible for. `call_wave`, `ability:surge` and
# `ability:overcharge` are excluded because shipped policies already schedule them, so
# `rules parity` covers those three across 1,440 runs and this file would add nothing.
IN_SCOPE = ("speed", "target_mode", "sell", "upgrade", "set_online", "build",
            "ability:shutter")

# Float comparison tolerance: none. `load`, `dist` and `hp` are all reached by `+`, `-`
# and `*` on float64 in both runtimes — the operations decision 078 certifies as
# byte-identical — so a tolerance here would hide precisely the divergence this file is
# built to find.


def _dispatch_python(sim: Sim, verb: str, args: dict) -> None:
    """Route one fixture action into the real dispatcher.

    Deliberately `Sim._dispatch_one`, the production function in a rules file, and not a
    reimplementation: the point is to execute the branch that 1,440 parity runs never
    reach. `scripts/test/verb_parity.gd` mirrors `scripts/test/parity.gd`'s match block,
    which is where the GDScript side of the same dispatcher lives.
    """
    sim._dispatch_one(verb, args)


def run_python(fx: dict, actions: list[dict]) -> dict:
    anchor = anchor_from_doc(fx["anchor"])
    towers = towers_from_rows(fx["towers"])
    enemies = enemies_from_rows(fx["enemies"])
    # An empty preference and `_try_build()` is never called: the fixture states its own
    # board through `build()`, exactly as PLC-03's arc fixture does, so no policy search
    # can quietly place a fifth emplacement and move what is in range of what.
    sim = Sim(anchor, towers, enemies, Policy(name="verb-probe", preference=[]),
              fx.get("difficulty", "standard"))
    sim.tuning = load_tuning()

    for b in fx["builds"]:
        if not sim.build(b["tower"], float(b["x"]), float(b["y"])):
            raise SystemExit(f"verb_parity: build refused: {b['tower']} at "
                             f"({b['x']}, {b['y']}) — the fixture's own geometry must "
                             f"satisfy _is_placeable()")

    by_tick_actions: dict[int, list[dict]] = {}
    for a in actions:
        by_tick_actions.setdefault(int(a["tick"]), []).append(a)
    by_tick_spawns: dict[int, list[dict]] = {}
    for sp in fx["spawns"]:
        by_tick_spawns.setdefault(int(sp["tick"]), []).append(sp)

    n = int(fx["ticks"])
    fired: list[str] = []
    funds: list[int] = []
    spend: list[int] = []
    load: list[float] = []
    placed_count: list[int] = []
    dists: list[list[float]] = []
    hps: list[list[float]] = []

    for t in range(n):
        for sp in by_tick_spawns.get(t, []):
            # Mirrors sim/engine.py's own spawn site in run() and anchor_sim.gd's public
            # spawn(). Python's Sim has never had a public spawn verb — the wave queue is
            # its only producer — so this construction is copied from that line.
            e = enemies[sp["enemy"]]
            sim.units.append(Unit(kind=e, hp=e.hp * sim.hp_mult,
                                  lane=int(sp.get("lane", 0))))
        for a in by_tick_actions.get(t, []):
            _dispatch_python(sim, str(a["verb"]), dict(a.get("args", {})))

        before = [p.cooldown for p in sim.placed]
        sim._tick_once()

        while len(fired) < len(sim.placed):
            fired.append("-" * t)
        for i in range(len(fired)):
            if i < len(sim.placed) and i < len(before):
                fired[i] += "1" if sim.placed[i].cooldown > before[i] else "0"
            elif i < len(sim.placed):
                fired[i] += "0"     # built this tick; no `before` to compare against
            else:
                fired[i] += "-"     # sold; the column stays aligned

        funds.append(int(sim.funds))
        spend.append(int(sim.spend))
        load.append(float(sim.bus_load()))
        placed_count.append(len(sim.placed))
        dists.append([float(u.dist) for u in sim.units])
        hps.append([float(u.hp) for u in sim.units])

    return {"fired": fired, "funds": funds, "spend": spend, "load": load,
            "placed_count": placed_count, "dists": dists, "hps": hps,
            "lives": sim.lives, "leaks": sim.leaks}


def run_godot() -> dict:
    # `--headless` never opens a window on any build — this runs the rules, it draws
    # nothing — so `want_window=True` just means "don't bother wrapping in Xvfb".
    extra = ["--headless", "--script", "res://scripts/test/verb_parity.gd"]
    cmd = toolpaths.godot_argv(ROOT, extra, want_window=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    for line in r.stdout.splitlines():
        if line.startswith("VERB_PARITY_JSON "):
            return json.loads(line[len("VERB_PARITY_JSON "):])
    raise SystemExit(
        "godot produced no verb-parity output.\n"
        f"stdout tail:\n{r.stdout[-1500:]}\nstderr tail:\n{r.stderr[-1500:]}")


# ─────────────────────────────────────────────────────────────── the claims ──

def check_engines_agree(py: dict, gd: dict) -> list[str]:
    """Claim 1: byte for byte, every recorded series."""
    out: list[str] = []
    if len(py["fired"]) != len(gd["fired"]):
        out.append(f"emplacement count differs: python {len(py['fired'])}, "
                   f"godot {len(gd['fired'])}")
    else:
        for i, (pf, gf) in enumerate(zip(py["fired"], gd["fired"])):
            if pf != gf:
                bad = [t for t, (a, b) in enumerate(zip(pf, gf)) if a != b]
                out.append(f"emplacement {i}: fire pattern differs on {len(bad)} tick(s), "
                           f"first at tick {bad[0]} (python {pf[bad[0]]}, "
                           f"godot {gf[bad[0]]})")
    for key in ("funds", "spend", "load", "placed_count"):
        for t, (a, b) in enumerate(zip(py[key], gd[key])):
            if a != b:
                out.append(f"{key} differs at tick {t}: python {a!r}, godot {b!r}")
                break
    for key in ("dists", "hps"):
        for t, (a, b) in enumerate(zip(py[key], gd[key])):
            if a != b:
                out.append(f"{key} differs at tick {t}: python {a!r}, godot {b!r}")
                break
    for key in ("lives", "leaks"):
        if py[key] != gd[key]:
            out.append(f"{key} differs: python {py[key]}, godot {gd[key]}")
    return out


def check_funds(exp: dict, res: dict, src: str) -> list[str]:
    """Claim 2: the money trajectory, against arithmetic done here."""
    out: list[str] = []
    for row in exp["funds"]:
        t = int(row["after_tick"])
        want = int(row["funds"])
        got = res["funds"][t]
        if got != want:
            out.append(f"{src}: funds at tick {t} is {got}, expected {want} — "
                       f"{row['why']}")
    for row in exp["spend"]:
        t = int(row["after_tick"])
        want = int(row["spend"])
        got = res["spend"][t]
        if got != want:
            out.append(f"{src}: spend at tick {t} is {got}, expected {want} — "
                       f"{row['why']}")
    for row in exp["placed_count"]:
        t = int(row["at_tick"])
        want = int(row["count"])
        got = res["placed_count"][t]
        if got != want:
            out.append(f"{src}: {got} emplacement(s) standing at tick {t}, expected {want}")
    return out


def check_refund_arithmetic(fx: dict, exp: dict) -> list[str]:
    """Claim 2, second half: the expected refund is what SELL_REFUND actually says.

    A guard against the fixture and the engine drifting apart in the same direction — if
    someone retunes SELL_REFUND, this fails here with the number rather than showing up
    as an unexplained funds mismatch.
    """
    sold = next(t for t in fx["towers"] if t["id"] == "verb-probe-late")
    refund = int(sold["cost"] * SELL_REFUND)
    before = next(r["funds"] for r in exp["funds"] if r["after_tick"] == 180)
    after = next(r["funds"] for r in exp["funds"] if r["after_tick"] == 270)
    if after - before != refund:
        return [f"fixture expects a refund of {after - before} but SELL_REFUND "
                f"({SELL_REFUND}) on a cost of {sold['cost']} gives {refund}"]
    return []


def check_upgrade_geometry(fx: dict, exp: dict, res: dict, src: str) -> list[str]:
    """Claim 3: the upgrade is proved by reach, not by a stat read back."""
    out: list[str] = []
    i = int(exp["reach_build"])
    tick = int(exp["reach_upgrade_tick"])
    base = float(exp["reach_base_range"])
    upgraded = float(exp["reach_upgraded_range"])
    px, py_ = (float(v) for v in exp["reach_position"])
    lane_y = float(fx["anchor"]["paths"][0]["waypoints"][0][1])
    origin = float(fx["anchor"]["paths"][0]["waypoints"][0][0])

    pattern = res["fired"][i]
    early = pattern[:tick].count("1")
    if early:
        out.append(f"{src}: emplacement {i} fired {early} time(s) before its upgrade at "
                   f"tick {tick} — at range {base} and a {abs(py_ - lane_y)}-tile "
                   f"standoff it cannot reach the lane at all")
    late = pattern[tick:].count("1")
    if late < int(exp["reach_min_shots_after"]):
        out.append(f"{src}: emplacement {i} fired {late} time(s) after its upgrade, "
                   f"expected at least {exp['reach_min_shots_after']} — the merged "
                   f"range did not take effect")
    for t, c in enumerate(pattern):
        if c != "1" or t < tick:
            continue
        d2 = [((origin + d) - px) ** 2 + (lane_y - py_) ** 2 for d in res["dists"][t]]
        if not d2:
            out.append(f"{src}: emplacement {i} fired on tick {t} with no unit on the board")
            break
        nearest = min(d2)
        if nearest > upgraded * upgraded:
            out.append(f"{src}: emplacement {i} fired on tick {t} with its nearest unit "
                       f"{nearest ** 0.5:.4f} tiles away, beyond the upgraded range "
                       f"{upgraded}")
            break
        if nearest <= base * base:
            out.append(f"{src}: emplacement {i} fired on tick {t} with a unit only "
                       f"{nearest ** 0.5:.4f} tiles away — inside the BASE range "
                       f"{base}, so this shot does not prove the upgrade")
            break
    return out


def check_bus(fx: dict, exp: dict, res: dict, src: str) -> list[str]:
    """Claim 4: set_online and the shutter, priced on the bus."""
    out: list[str] = []
    drop = float(exp["offline_load_drop_mw"])
    lo, hi = int(exp["offline_from_tick"]), int(exp["offline_to_tick"])
    # The tick before the toggle and the tick after it: the only thing that changed
    # between them is the emplacement going offline.
    on_before = res["load"][lo - 1]
    off = res["load"][lo]
    if off != on_before - drop:
        out.append(f"{src}: bus load went {on_before} -> {off} when emplacement 2 was "
                   f"taken offline, a drop of {on_before - off}, expected exactly {drop}")
    back = res["load"][hi]
    if back != off + drop:
        out.append(f"{src}: bus load went {off} -> {back} when emplacement 2 came back "
                   f"online, a rise of {back - off}, expected exactly {drop}")

    sd = float(exp["shutter_draw_mw"])
    slo, shi = int(exp["shutter_from_tick"]), int(exp["shutter_to_tick"])
    if res["load"][slo] != res["load"][slo - 1] + sd:
        out.append(f"{src}: bus load went {res['load'][slo - 1]} -> {res['load'][slo]} "
                   f"when the shutter came down, expected a rise of exactly {sd}")
    if res["load"][shi] != res["load"][shi - 1] - sd:
        out.append(f"{src}: bus load went {res['load'][shi - 1]} -> {res['load'][shi]} "
                   f"when the shutter lifted, expected a fall of exactly {sd}")

    # The held unit. The fixture spawns one at tick 200, so by tick `slo` it is the last
    # entry in `dists` and inside hold_tiles; it must not move while the shutter is down.
    hold = float(exp["shutter_hold_tiles"])
    held = res["dists"][slo][-1]
    if held > hold:
        out.append(f"{src}: the unit meant to be held is {held} tiles in at tick {slo}, "
                   f"beyond hold_tiles {hold} — the fixture's timing is wrong, not the "
                   f"engine's")
    else:
        for t in range(slo, shi):
            if res["dists"][t][-1] != held:
                out.append(f"{src}: the held unit moved from {held} to "
                           f"{res['dists'][t][-1]} at tick {t}, while the shutter was "
                           f"down")
                break
        if res["dists"][shi][-1] <= held:
            out.append(f"{src}: the held unit did not resume moving after the shutter "
                       f"lifted at tick {shi}")
    return out


def check_target_mode(exp: dict, res: dict, src: str) -> list[str]:
    """Claim 1's sharpest edge: `last` must move the damage onto the trailing unit.

    Compared as hit points rather than as a target index, because neither engine exposes
    the selection and giving one an accessor would mean the rule existed twice in one
    file — the same reasoning arc_parity.gd records for using the fire pattern.
    """
    tick = int(exp["target_mode_tick"])
    # A few ticks after the switch, so the second unit has been spawned and both engines
    # have had time to fire. hps[t] is [leader, trailer] in spawn order.
    probe = tick + 60
    for t in (tick, tick + 1, probe):
        if t >= len(res["hps"]) or len(res["hps"][t]) < 2:
            return [f"{src}: expected two units alive at tick {t} for the target-mode "
                    f"check, found {len(res['hps'][t]) if t < len(res['hps']) else 0}"]
    leader_before = res["hps"][tick][0]
    leader_after = res["hps"][probe][0]
    trailer_after = res["hps"][probe][1]
    out: list[str] = []
    if trailer_after >= res["hps"][tick + 1][1]:
        out.append(f"{src}: the trailing unit took no damage between ticks {tick + 1} "
                   f"and {probe} — `target_mode: last` did not take effect")
    if leader_after != leader_before:
        out.append(f"{src}: the leading unit lost {leader_before - leader_after} hp "
                   f"after the switch to `last`; every emplacement in range should have "
                   f"moved onto the trailing unit")
    return out


# ─────────────────────────────────────────────────────────────────── main ──

def print_timeline(fx: dict, res: dict) -> None:
    marks = sorted({0, *(int(a["tick"]) for a in fx["actions"]), int(fx["ticks"]) - 1})
    by_tick = {}
    for a in fx["actions"]:
        by_tick.setdefault(int(a["tick"]), []).append(a["verb"])
    print(f"  {'tick':>5s} {'funds':>6s} {'spend':>6s} {'load':>7s} {'placed':>6s} "
          f"{'units':>5s}  verb(s)")
    for t in marks:
        print(f"  {t:>5d} {res['funds'][t]:>6d} {res['spend'][t]:>6d} "
              f"{res['load'][t]:>7.1f} {res['placed_count'][t]:>6d} "
              f"{len(res['dists'][t]):>5d}  {', '.join(by_tick.get(t, []))}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="sim/engine.py vs scripts/anchor_sim.gd scheduled-verb parity (LF-244).")
    ap.add_argument("--report", action="store_true",
                    help="print the measured timeline")
    ap.add_argument("--corrupt", choices=("upgrade", "refund", "bus", "target", "engine"),
                    help="break one thing on purpose, to prove this check fails red "
                         "instead of passing for the wrong reason. `target` and `engine` "
                         "break BEHAVIOUR (the mode switch becomes a no-op; one engine's "
                         "result is perturbed); the rest break an expectation. Note "
                         "`target` is ASYMMETRIC by construction — the mutation lands in "
                         "the in-memory document Python reads, while Godot re-reads the "
                         "file — so it fails both the cross-engine diff and the "
                         "target-mode check, and only the latter is what it is for")
    args = ap.parse_args()

    if toolpaths.godot() is None:
        print("godot not found on this machine — skipping scheduled-verb parity",
              file=sys.stderr)
        return 0

    fx = json.loads(FIXTURE.read_text())
    exp: dict[str, Any] = fx["expected"]

    # The fixture must actually exercise what this file claims to cover, or the check
    # quietly shrinks to whatever is left in it.
    scheduled = {a["verb"] if a["verb"] != "ability"
                 else f"ability:{a['args']['kind']}" for a in fx["actions"]}
    missing = [v for v in IN_SCOPE if v not in scheduled]
    if missing:
        print(f"fixture no longer schedules {', '.join(missing)} — the check would pass "
              f"while covering less than it claims", file=sys.stderr)
        return 1

    if args.corrupt == "upgrade":
        exp["reach_base_range"] = 20.0      # now every shot looks like a base-range shot
    elif args.corrupt == "refund":
        exp["funds"] = [dict(r, funds=r["funds"] + 1) if r["after_tick"] == 270 else r
                        for r in exp["funds"]]
    elif args.corrupt == "bus":
        exp["offline_load_drop_mw"] = 40.0
    elif args.corrupt == "target":
        # A behavioural break, not an arithmetic one: the switch is left in the schedule
        # but asks for the mode the emplacement is already in, so `target_mode` becomes a
        # no-op and the leading unit keeps taking every shot.
        for a in fx["actions"]:
            if a["verb"] == "target_mode":
                a["args"]["mode"] = "first"

    py = run_python(fx, fx["actions"])
    gd = run_godot()

    if args.corrupt == "engine":
        # Perturb one tick of one engine's result, to prove claim 1's comparator is not
        # vacuous. Every other mode leaves the two engines agreeing, so without this the
        # byte-for-byte diff would never have been shown to fail at all.
        i = next(i for i, p in enumerate(py["fired"]) if "1" in p)
        t = py["fired"][i].index("1")
        py["fired"][i] = py["fired"][i][:t] + "0" + py["fired"][i][t + 1:]

    problems = check_engines_agree(py, gd)
    problems += check_refund_arithmetic(fx, exp)
    for src, res in (("python", py), ("godot", gd)):
        problems += check_funds(exp, res, src)
        problems += check_upgrade_geometry(fx, exp, res, src)
        problems += check_bus(fx, exp, res, src)
        problems += check_target_mode(exp, res, src)

    # Claim 5: `speed` changes nothing. Python only — the verb is a bare `return` in one
    # engine and a bare `pass` in the other, and what is being proved is that the RULES
    # do not read it, which one engine can demonstrate as well as two.
    stripped = [a for a in fx["actions"] if a["verb"] != "speed"]
    control = run_python(fx, stripped)
    for key in ("fired", "funds", "spend", "load", "placed_count", "dists", "hps"):
        if py[key] != control[key]:
            problems.append(
                f"`speed` is not a no-op: {key} differs between a run with the "
                f"fixture's speed actions and one with them stripped")
            break

    if args.report or problems:
        print_timeline(fx, py)

    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        if args.corrupt:
            print(f"(--corrupt {args.corrupt} broke one expectation on purpose, to prove "
                  f"this fails red)", file=sys.stderr)
        return 1

    shots = sum(p.count("1") for p in py["fired"])
    print(f"ok — {len(IN_SCOPE)} verb(s) over {fx['ticks']} ticks, {shots} shots, "
          f"python and godot identical and matching the fixture's own arithmetic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
