#!/usr/bin/env python3
"""
Validate all Latticefall game content.

Two layers, because schemas only catch half the problems:

  1. JSON Schema — shape, types, required fields, id patterns.
  2. Cross-reference and design invariants — a wave naming an enemy that does not
     exist, a path that teleports, a slot sitting on the path, an anchor whose
     cheapest viable build cannot fit inside its own reactor capacity.

The second layer is the one that earns its keep. A level can be perfectly well-formed
JSON and still be unplayable, and finding that out in the engine is expensive.

    .venv/bin/python tools/validate/validate_data.py
    .venv/bin/python tools/validate/validate_data.py --quiet
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SCHEMA = DATA / "schema"

## Capacity as a fraction of "every slot running the hungriest emplacement". Above this the
## power decision is thin; at 1.0 it does not exist. Act I sits at 29-38%.
SATURATION_WARN = 0.80

# valid dialog triggers that do not depend on wave count
STATIC_TRIGGERS = {"brief", "debrief", "brownout", "first-leak", "low-lives"}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


def load(p: Path) -> dict:
    return json.loads(p.read_text())


def validate_schema(doc: dict, schema_name: str, where: str, rep: Report) -> bool:
    sp = SCHEMA / f"{schema_name}.schema.json"
    if not sp.exists():
        rep.err(where, f"no schema {sp.name}")
        return False
    v = Draft202012Validator(load(sp))
    ok = True
    for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(x) for x in e.path) or "(root)"
        rep.err(where, f"{loc}: {e.message}")
        ok = False
    return ok


def check_anchor(doc: dict, towers: dict, enemies: dict, rep: Report) -> None:
    where = doc.get("id", "anchor?")
    w, h = doc["grid"]["w"], doc["grid"]["h"]

    # path must be inside the grid, orthogonal, and contiguous
    path = [tuple(p) for p in doc["path"]]
    for x, y in path:
        if not (0 <= x < w and 0 <= y < h):
            rep.err(where, f"path point ({x},{y}) outside grid {w}x{h}")
    for a, b in zip(path, path[1:]):
        if a[0] != b[0] and a[1] != b[1]:
            rep.err(where, f"path segment {a}->{b} is diagonal; segments must be axis-aligned")
        if a == b:
            rep.err(where, f"path has a zero-length segment at {a}")

    # expand the path into occupied tiles so slots can be checked against it
    tiles: set[tuple[int, int]] = set()
    for a, b in zip(path, path[1:]):
        if a[0] == b[0]:
            lo, hi = sorted((a[1], b[1]))
            tiles |= {(a[0], y) for y in range(lo, hi + 1)}
        else:
            lo, hi = sorted((a[0], b[0]))
            tiles |= {(x, a[1]) for x in range(lo, hi + 1)}

    seen: set[tuple[int, int]] = set()
    for s in doc["slots"]:
        t = tuple(s)
        if not (0 <= t[0] < w and 0 <= t[1] < h):
            rep.err(where, f"slot {t} outside grid {w}x{h}")
        if t in tiles:
            rep.err(where, f"slot {t} sits on the enemy path")
        if t in seen:
            rep.err(where, f"duplicate slot {t}")
        seen.add(t)

    # every wave must reference a real enemy
    for i, wave in enumerate(doc["waves"], 1):
        for sp in wave["spawns"]:
            if sp["enemy"] not in enemies:
                rep.err(where, f"wave {i} spawns unknown enemy '{sp['enemy']}'")

    # design invariant: the capacity must admit more than one build.
    # if only the cheapest emplacement fits, the anchor has no power decision in it.
    avail = [t for t in towers.values()
             if t.get("unlocked_at", "anchor-01") <= doc["id"]]
    if not avail:
        rep.err(where, "no emplacements unlocked at this anchor")
        return
    cheapest_draw = min(t["draw_mw"] for t in avail)
    cap = doc["capacity_mw"]
    if cap < cheapest_draw:
        rep.err(where, f"capacity {cap} MW cannot run even one {cheapest_draw} MW emplacement")
    elif cap < cheapest_draw * 3:
        rep.warn(where, f"capacity {cap} MW fits fewer than 3 of the cheapest "
                        f"emplacement ({cheapest_draw} MW) — likely unwinnable")

    # If capacity can run the hungriest emplacement in every slot simultaneously, the
    # player never has to choose and the hook is inert. Compare against slots x max
    # draw, not the sum of distinct types — a board is filled with instances, and with
    # one type unlocked the type-sum is meaninglessly small.
    max_draw = max(t["draw_mw"] for t in avail)
    saturated = len(doc["slots"]) * max_draw
    if cap >= saturated:
        rep.err(where, f"capacity {cap} MW covers every slot at max draw "
                       f"({len(doc['slots'])} x {max_draw} = {saturated} MW) "
                       f"— no power decision exists on this anchor")
    # Warned well before that, because the hook does not switch off at the boundary, it
    # fades towards it — and the drift is invisible until it crosses. Act I runs at 29-38%
    # of saturation. Act III reached 103% once, by paying for heavier waves with reactor
    # capacity a sweep was free to raise: every anchor still graded clean, the validator
    # said nothing until the last one tipped over, and the game's core decision had
    # quietly stopped existing on five levels. Decision 048.
    elif cap > saturated * SATURATION_WARN:
        rep.warn(where, f"capacity {cap} MW is {cap / saturated:.0%} of what would run "
                        f"every slot at max draw ({saturated} MW) — the power decision is "
                        f"getting thin; Act I sits at 29-38%")

    if len(doc["slots"]) < 3:
        rep.warn(where, f"{len(doc['slots'])} build slots is very few")

    # A slot further from the path than any weapon's range is dead: nothing built there
    # can ever fire. This is invisible in the data and expensive to find by grading —
    # anchor-06 was authored with two dead slots and several marginal ones, and read as
    # a wave-balance problem through several sweeps before the layout was measured.
    ranged = [t for t in avail if t.get("damage", 0) > 0]
    if ranged:
        best_range = max(t["range"] for t in ranged)
        short_range = min(t["range"] for t in ranged)
        pts = [(float(a[0]), float(a[1])) for a in doc["path"]]

        def dist_to_path(sx: float, sy: float) -> float:
            best = float("inf")
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                dx, dy = bx - ax, by - ay
                span = dx * dx + dy * dy
                t = 0.0 if span == 0 else max(0.0, min(1.0,
                        ((sx - ax) * dx + (sy - ay) * dy) / span))
                best = min(best, math.hypot(sx - (ax + t * dx), sy - (ay + t * dy)))
            return best

        dead, marginal = [], []
        for s in doc["slots"]:
            d = dist_to_path(float(s[0]), float(s[1]))
            if d > best_range:
                dead.append((list(s), round(d, 1)))
            elif d > short_range:
                marginal.append((list(s), round(d, 1)))
        if dead:
            rep.err(where, f"{len(dead)} slot(s) are further from the path than any "
                           f"weapon can reach (max range {best_range}): {dead}")
        if marginal:
            rep.warn(where, f"{len(marginal)} slot(s) are out of range of the "
                            f"shortest-ranged weapon ({short_range}): {marginal}")


def check_dialog(doc: dict, anchors: dict, rep: Report) -> None:
    where = f"dialog/{doc.get('anchor','?')}"
    aid = doc["anchor"]
    if aid not in anchors:
        rep.err(where, f"dialog for unknown anchor '{aid}'")
        return
    nwaves = len(anchors[aid]["waves"])

    triggers = set()
    for line in doc["lines"]:
        t = line["trigger"]
        triggers.add(t)
        if ":" in t:
            kind, n = t.split(":")
            if int(n) > nwaves:
                rep.err(where, f"trigger '{t}' but the anchor only has {nwaves} waves")
        elif t not in STATIC_TRIGGERS:
            rep.err(where, f"unknown trigger '{t}'")

        # mid-wave lines are interruptible, so none may be load-bearing
        if line.get("critical") is True:
            rep.err(where, f"line under '{t}' is marked critical; no line may be")

    for required in ("brief", "debrief"):
        if required not in triggers:
            rep.warn(where, f"no '{required}' line")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Latticefall game data.")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    args = ap.parse_args()
    rep = Report()

    tdoc = load(DATA / "towers.json")
    edoc = load(DATA / "enemies.json")
    validate_schema(tdoc, "towers", "towers.json", rep)
    validate_schema(edoc, "enemies", "enemies.json", rep)
    towers = {t["id"]: t for t in tdoc.get("towers", [])}
    enemies = {e["id"]: e for e in edoc.get("enemies", [])}

    for coll, name in ((towers, "tower"), (enemies, "enemy")):
        if len(coll) != len({k for k in coll}):
            rep.err(f"{name}s", "duplicate ids")

    anchors: dict[str, dict] = {}
    for p in sorted((DATA / "anchors").glob("anchor-*.json")):
        doc = load(p)
        if validate_schema(doc, "anchor", p.name, rep):
            anchors[doc["id"]] = doc
            if doc["id"] != p.stem:
                rep.err(p.name, f"id '{doc['id']}' does not match filename")

    for doc in anchors.values():
        check_anchor(doc, towers, enemies, rep)

    for p in sorted((DATA / "dialog").glob("anchor-*.json")):
        doc = load(p)
        if validate_schema(doc, "dialog", p.name, rep):
            check_dialog(doc, anchors, rep)

    for aid in anchors:
        if not (DATA / "dialog" / f"{aid}.json").exists():
            rep.warn(aid, "no dialog file")

    if not args.quiet:
        print(f"{len(towers)} emplacements · {len(enemies)} enemies · "
              f"{len(anchors)} anchors · {len(list((DATA/'dialog').glob('*.json')))} dialog files")
    for w in rep.warnings:
        print(f"  warn  {w}")
    for e in rep.errors:
        print(f"  ERROR {e}", file=sys.stderr)

    if rep.errors:
        print(f"\n{len(rep.errors)} error(s)", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"ok — {len(rep.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
