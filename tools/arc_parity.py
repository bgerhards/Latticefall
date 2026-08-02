#!/usr/bin/env python3
"""
PLC-03's firing-arc probe: drive sim/engine.py and scripts/anchor_sim.gd over the same
throwaway arc'd weapon and prove they agree on every tick about what is inside the cone.

The arc rule is one expression written twice — `dot*dot >= cos_half_angle^2 *
|facing|^2 * d2`, guarded by `dot >= 0` — and this project's entire method for a rule
that exists twice is to run both and diff them. `rules parity` already does that for the
whole game, but it cannot cover this one: no shipped tower row carries `cos_half_angle`
(deliberately — the arc path must stay provably inert until a weapon asks for it), so
all 1,440 parity runs execute the *absent* branch and say nothing about the present one.
This is the cheap, targeted proof that fills exactly that hole, in the shape
`tools/terrain_parity.py` already established for the two terrain resolvers.

Three claims, in increasing order of what they would catch:

1. **The two engines produce a byte-identical fire pattern.** One "0"/"1" per tick per
   emplacement over the fixture's whole run — an integer signal that compares exactly
   across two languages, unlike a float.
2. **Each emplacement only ever fires with the unit inside an ANALYTIC window** computed
   from the fixture's geometry, not read off either implementation. Two engines wrong the
   same way — a dropped sign guard, an un-squared compare, an assumed unit vector — fail
   this even though they agree with each other, which is the failure `terrain_parity.py`'s
   `expected` block exists to catch and the reason this file is not just a diff.
3. **The arc'd rows fire strictly less than the un-arc'd control**, so an arc test that
   silently never engaged at all cannot pass by looking omnidirectional.

    .venv/bin/python tools/arc_parity.py
    .venv/bin/python tools/arc_parity.py --report   # print the windows it measured
    .venv/bin/python tools/arc_parity.py --corrupt  # narrow a window, prove it goes red
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import anchor_from_doc, enemies_from_rows, towers_from_rows  # noqa: E402
from sim.engine import Policy, Sim, Unit  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import toolpaths  # noqa: E402

FIXTURE = ROOT / "data" / "schema" / "fixtures" / "firing-arc.json"

# The window check's slack, in tiles. A shot is attributed to the unit's position at the
# END of the tick it fired on, so it can legitimately sit up to one tick of travel past
# the true boundary: 2.0 tiles/sec * (1/30) sec = 0.0667. Anything wider than a tick of
# travel is a real disagreement with the geometry, not a sampling artefact.
WINDOW_SLACK = 0.07


def run_python(fx: dict) -> dict:
    """Drive `sim/engine.py` over the fixture, tick by tick."""
    anchor = anchor_from_doc(fx["anchor"])
    towers = towers_from_rows(fx["towers"])
    enemies = enemies_from_rows(fx["enemies"])
    # An empty preference list, and `_try_build()` is never called: the fixture states its
    # own board explicitly through `build()`, the same public verb scripts/test/
    # arc_parity.gd calls, so neither side's policy search can quietly place a fifth
    # emplacement and change what is in range of what.
    sim = Sim(anchor, towers, enemies, Policy(name="arc-probe", preference=[]),
              fx.get("difficulty", "standard"))

    for b in fx["builds"]:
        if not sim.build(b["tower"], float(b["x"]), float(b["y"])):
            raise SystemExit(f"arc_parity: build refused: {b['tower']} at "
                             f"({b['x']}, {b['y']}) — the fixture's own geometry must "
                             f"satisfy _is_placeable()")
    for sp in fx["spawns"]:
        # Mirrors sim/engine.py's own spawn site in run() (`Unit(kind=e, hp=e.hp *
        # self.hp_mult, lane=lane)`) and scripts/anchor_sim.gd's public spawn(). Python's
        # Sim has never had a public spawn verb — the wave queue is the only producer —
        # so this is the one construction the harness does by hand, and it is copied from
        # that line rather than invented.
        e = enemies[sp["enemy"]]
        sim.units.append(Unit(kind=e, hp=e.hp * sim.hp_mult, lane=int(sp.get("lane", 0))))

    fired = ["" for _ in sim.placed]
    dists: list[float] = []
    for _ in range(int(fx["ticks"])):
        # A shot is the only thing that RAISES `cooldown` — `_step()` sets it to
        # fire_interval on firing and otherwise decrements it — so "after > before" is
        # exactly "fired this tick", with no new accessor on either engine.
        before = [p.cooldown for p in sim.placed]
        sim._tick_once()
        for i, p in enumerate(sim.placed):
            fired[i] += "1" if p.cooldown > before[i] else "0"
        dists.append(sim.units[0].dist if sim.units else -1.0)

    return {"fired": fired, "dists": dists, "lives": sim.lives, "leaks": sim.leaks}


def run_godot() -> dict:
    # `--headless` never opens a window on any build — this runs the rules, it draws
    # nothing — so `want_window=True` just means "don't bother wrapping in Xvfb".
    extra = ["--headless", "--script", "res://scripts/test/arc_parity.gd"]
    cmd = toolpaths.godot_argv(ROOT, extra, want_window=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    for line in r.stdout.splitlines():
        if line.startswith("ARC_PARITY_JSON "):
            return json.loads(line[len("ARC_PARITY_JSON "):])
    raise SystemExit(
        "godot produced no arc-parity output.\n"
        f"stdout tail:\n{r.stdout[-1500:]}\nstderr tail:\n{r.stderr[-1500:]}")


def spans(pattern: str, dists: list[float], origin: float) -> tuple[float, float, int]:
    """(first, last, count) unit x-positions of the ticks `pattern` fired on.

    The fixture's lane runs along +x from x=0, so distance along the lane IS the unit's
    x coordinate; `origin` is the lane's first waypoint x, kept explicit so this does not
    quietly become wrong if the fixture ever gains a lane that starts elsewhere."""
    xs = [origin + dists[i] for i, c in enumerate(pattern) if c == "1"]
    if not xs:
        return (0.0, 0.0, 0)
    return (min(xs), max(xs), len(xs))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="sim/engine.py vs scripts/anchor_sim.gd firing-arc parity (PLC-03).")
    ap.add_argument("--report", action="store_true",
                    help="print the measured firing window of every emplacement")
    ap.add_argument("--corrupt", action="store_true",
                    help="shrink the first expected window to a point, to prove this "
                         "check fails red instead of passing for the wrong reason")
    args = ap.parse_args()

    if toolpaths.godot() is None:
        print("godot not found on this machine — skipping firing-arc parity",
              file=sys.stderr)
        return 0

    fx = json.loads(FIXTURE.read_text())
    expected = {int(e["build"]): e for e in fx["expected"]}
    if args.corrupt:
        lo, _hi = expected[0]["window"]
        expected[0]["window"] = [lo, lo]

    py = run_python(fx)
    gd = run_godot()

    problems: list[str] = []

    # (1) the two engines, character for character.
    if len(py["fired"]) != len(gd["fired"]):
        problems.append(f"emplacement count differs: python {len(py['fired'])}, "
                        f"godot {len(gd['fired'])}")
    else:
        for i, (pf, gf) in enumerate(zip(py["fired"], gd["fired"])):
            if pf != gf:
                bad = [t for t, (a, b) in enumerate(zip(pf, gf)) if a != b]
                problems.append(
                    f"build {i} ({fx['builds'][i]['tower']}): fire pattern differs on "
                    f"{len(bad)} tick(s), first at tick {bad[0]} "
                    f"(python {pf[bad[0]]}, godot {gf[bad[0]]})")
    for key in ("lives", "leaks"):
        if py[key] != gd[key]:
            problems.append(f"{key} differs: python {py[key]}, godot {gd[key]}")
    # `dists` is float64 on both sides and is only `dist += speed * DT` accumulated, so
    # it must agree exactly — a tolerance here would hide the one thing that could make
    # two matching fire patterns mean different geometry.
    for t, (pd, gd_) in enumerate(zip(py["dists"], gd["dists"])):
        if pd != gd_:
            problems.append(f"unit dist differs at tick {t}: python {pd!r}, godot {gd_!r}")
            break

    # (2) each window against the fixture's analytic bounds, on BOTH engines.
    origin = float(fx["anchor"]["paths"][0]["waypoints"][0][0])
    measured: list[tuple[str, float, float, int]] = []
    for i, b in enumerate(fx["builds"]):
        exp = expected.get(i)
        for src, res in (("python", py), ("godot", gd)):
            first, last, count = spans(res["fired"][i], res["dists"], origin)
            if src == "python":
                measured.append((b["tower"], first, last, count))
            if exp is None:
                continue
            lo, hi = float(exp["window"][0]), float(exp["window"][1])
            if count < int(exp["min_shots"]):
                problems.append(f"build {i} ({b['tower']}/{src}): fired {count} time(s), "
                                f"expected at least {exp['min_shots']}")
                continue
            if first < lo - WINDOW_SLACK or last > hi + WINDOW_SLACK:
                problems.append(
                    f"build {i} ({b['tower']}/{src}): fired over x "
                    f"[{first:.4f}, {last:.4f}], outside the analytic window "
                    f"[{lo:.4f}, {hi:.4f}] (+-{WINDOW_SLACK} tile of tick sampling)")

    # (3) the arc'd rows must be strictly narrower than the un-arc'd control, or an arc
    # test that never engaged would pass every check above by looking omnidirectional.
    omni = [i for i, b in enumerate(fx["builds"])
            if "cos_half_angle" not in next(t for t in fx["towers"]
                                            if t["id"] == b["tower"])]
    if not omni:
        problems.append("fixture has no un-arc'd control row — claim (3) cannot be made")
    else:
        ctrl = py["fired"][omni[0]].count("1")
        for i, b in enumerate(fx["builds"]):
            if i in omni:
                continue
            n = py["fired"][i].count("1")
            if n >= ctrl:
                problems.append(
                    f"build {i} ({b['tower']}) fired {n} time(s), not fewer than the "
                    f"un-arc'd control's {ctrl} — the arc test is not engaging")

    if args.report or problems:
        for tower, first, last, count in measured:
            print(f"  {tower:<18} {count:>3} shot(s) over x [{first:.4f}, {last:.4f}]",
                  file=sys.stderr if problems else sys.stdout)

    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        if args.corrupt:
            print("(--corrupt collapsed build 0's expected window on purpose, to prove "
                  "this fails red)", file=sys.stderr)
        return 1

    total = sum(p.count("1") for p in py["fired"])
    print(f"ok — {len(fx['builds'])} emplacement(s) over {fx['ticks']} ticks, "
          f"{total} shots, python and godot identical and inside the analytic windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
