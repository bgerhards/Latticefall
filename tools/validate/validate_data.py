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

Schema dispatch is generic, not a hardcoded per-type list (PRC-10 / LF-064): every
document under data/ names its own schema via a top-level `"schema"` key, and
`schema_for()` resolves that name to `data/schema/<name>.schema.json`. A new content
type is therefore a schema file plus a `"schema"` key on the documents that use it —
no edit to this file. Documents are discovered with `git ls-files` rather than a
directory walk, so an untracked file is invisible here exactly as it would be to
anyone who clones the repo, and a completeness assertion in `main()` catches both a
schema nothing exercises and a document whose named schema does not exist.

`assets/renders/sprites.json` and `assets/audio/music_manifest.json` are deliberately
out of scope here: they are generated build output (PRC-10 asked whether they should
get schemas — decided yes in principle, generated files are exactly what silently
changes shape without anyone editing them on purpose, but they live under `assets/`,
not `data/`, and this validator's discovery walk and completeness assertion are both
scoped to `data/`. Pulling them in belongs to a follow-up that also decides how a
schema under `data/schema/` reaches over to validate a document outside `data/`
without breaking the "every schema is exercised by a document `git ls-files data`
finds" invariant the completeness assertion relies on.

    .venv/bin/python tools/validate/validate_data.py
    .venv/bin/python tools/validate/validate_data.py --quiet
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SCHEMA = DATA / "schema"

# TER-08: terrain cross-reference checks (below) must resolve a `terrain` block the SAME
# way sim/content.py and scripts/content.gd do, or this becomes a third parser — exactly
# the disagreement TER-02 exists to prevent. Import the shared resolver rather than
# reimplementing paint order here.
sys.path.insert(0, str(ROOT))
from sim.content import resolve_terrain  # noqa: E402

## Capacity as a fraction of "every slot running the hungriest emplacement". Above this the
## power decision is thin; at 1.0 it does not exist. Act I sits at 29-38%.
SATURATION_WARN = 0.80

# PLC-05: the board-saturation invariant used to divide by len(slots); free placement
# (PLC-01) deletes `slots` entirely, so that denominator can vanish. `max_emplacements` is
# the replacement -- an authored integer cap, required by the schema whenever `slots` is
# absent (see anchor.schema.json's root `anyOf`), optional when `slots` is present (in
# which case it falls back to len(slots), reproducing every one of today's 24 anchors'
# numbers exactly -- the "provably neutral" requirement). See the DECISIONS.md entry this
# issue added, and CLAUDE.md's "Density must never be paid for with reactor capacity" trap.
#
# Second bound, WARNING only: max_emplacements (or len(slots)) against how many 1x1
# emplacements the board's buildable area could physically hold. This is deliberately a
# looser, upper-bound check -- hexagonal packing (0.9069) is the textbook density ceiling
# and a real layout loses more to the lane standoff already subtracted below -- so it must
# never become the only guard (a cap can be well inside this bound and still remove the
# power decision; that is what the primary check above is for). It exists to catch a cap
# authored far above what the board can physically hold, which is a content bug even when
# capacity itself is fine.
HEX_PACKING_EFFICIENCY = 0.9069
# Every emplacement in data/towers.json today occupies exactly one 1x1 slot, so a 1-tile
# footprint and a 1-tile lane standoff are exact for the whole current roster, not a guess.
# A future footprint-carrying tower (theatre-scale free placement) will need this pulled
# from data instead of assumed -- noted rather than solved here since no such tower exists.
EMPLACEMENT_FOOTPRINT_TILES = 1
LANE_STANDOFF_TILES = 1

# TER-08: `dir` names the direction of *ascent* (schema description on `terrain.ramps`);
# the tile one step in -dir must be at `from` and one step in +dir must be at `to`.
RAMP_DIRS = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}


def _static_triggers_from_schema() -> set[str]:
    """Derive the valid non-wave-numbered dialog triggers from the schema's own regex.

    This used to be a hand-maintained set that duplicated the `trigger` pattern in
    data/schema/dialog.schema.json, and the two drifted once: nine new triggers were
    added to the schema and every dialog file quoting one failed here with "unknown
    trigger" while the schema itself passed (LF-067). Reading the pattern back out of
    the schema means there is exactly one place these names are authored — removing a
    trigger from the schema now fails the dialog files that use it, instead of failing
    silently here.
    """
    pattern = load(SCHEMA / "dialog.schema.json")["properties"]["lines"]["items"] \
        ["properties"]["trigger"]["pattern"]
    body = pattern.removeprefix("^").removesuffix("$")
    assert body.startswith("(") and body.endswith(")"), \
        f"dialog schema trigger pattern has an unexpected shape: {pattern!r}"
    # wave-start:\d+ / wave-clear:\d+ are checked against the anchor's wave count
    # instead (see check_dialog); everything else is a literal, static trigger name.
    return {alt for alt in body[1:-1].split("|") if "\\d" not in alt}


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


STATIC_TRIGGERS = _static_triggers_from_schema()


class SchemaDispatchError(Exception):
    """A document cannot be routed to a schema at all — missing key or dangling name."""


def schema_for(doc: dict, path: Path) -> str:
    """Read the schema a document names for itself and resolve it to a schema file.

    Every document under data/ carries a top-level `"schema"` key (CLAUDE.md's "game
    content is data … validated against a schema"); this is the one place that key is
    read, so adding a content type never means editing this validator (PRC-10 / LF-064).
    Raises rather than returning a sentinel — the caller turns that into a Report error,
    never a warning, because a document nothing validates is a silent coverage gap.
    """
    name = doc.get("schema")
    if not isinstance(name, str) or not name:
        raise SchemaDispatchError(f'{path.relative_to(ROOT)}: no top-level "schema" key')
    sp = SCHEMA / f"{name}.schema.json"
    if not sp.exists():
        raise SchemaDispatchError(
            f'{path.relative_to(ROOT)}: "schema": "{name}" names {sp.name}, '
            f"which does not exist")
    return name


def discover_documents() -> list[Path]:
    """Every JSON document under data/, sourced from `git ls-files` rather than a
    directory walk (PRC-02).

    Tracked-only is deliberate: a file that exists on disk but was never `git add`ed is
    invisible here exactly as it would be to anyone who clones the repo. data/tuning.json
    was untracked for part of a session; a directory walk would have validated it anyway
    and hidden that the gate everyone else runs was seeing zero documents. The
    completeness assertion in main() is what turns "untracked" into a loud failure
    instead of a silent one.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "data"], cwd=ROOT, capture_output=True, check=True,
    ).stdout
    paths = [ROOT / rel for rel in out.decode().split("\0") if rel.endswith(".json")]
    return sorted(p for p in paths if SCHEMA not in p.parents)


def validate_schema(doc: dict, schema_name: str, where: str, rep: Report) -> bool:
    sp = SCHEMA / f"{schema_name}.schema.json"
    if not sp.exists():
        rep.err(where, f"no schema {sp.name}")
        return False
    v = Draft202012Validator(load(sp))
    ok = True
    for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path)):
        # A real JSON pointer, not just a breadcrumb: e.message already names the
        # offending value for most jsonschema failures ("'fast' is not of type
        # 'array'"), so pointer + message together say both where and what.
        loc = "/" + "/".join(str(x) for x in e.path) if e.path else "(root)"
        rep.err(where, f"{loc}: {e.message}")
        ok = False
    return ok


def check_terrain(doc: dict, rep: Report) -> None:
    """TER-08: invariants the schema cannot express about an anchor's optional `terrain`
    block. A schema can say "this is an integer"; only a resolver-aware check can say
    "this ramp does not connect the two heights it claims to".

    A no-op, by construction, on every anchor that carries no `terrain` — which today is
    23 of 24 (TER-02's pilot is the exception), so this must not move any of their warning
    counts. Called from the very top of check_anchor(), before its "no emplacement
    unlocked" early return — moving it after that return would make it silently never run
    on such an anchor (named as a risk in TER-08 itself).

    Uses sim/content.py's resolve_terrain() — the SAME resolver both engines are proved
    identical against by tools/terrain_parity.py — rather than a third implementation.

    Every check below is an ERROR, not a warning: each one is "geometry the engines would
    resolve differently" (a ramp whose claimed heights disagree with the resolved grid, a
    lane step no unit could climb) rather than "legal but suboptimal". There is currently
    no unreachable-but-legal terrain case to warn on — the pilot anchor's one ramp sits a
    tile away from its only lane by design, which is exactly the "declared for a future
    line-of-sight/movement consumer" case TER-02 calls out, not dead content, so this does
    not invent a warning for it.

    Bridges (deck support/clearance) and the LOS-aware dead-slot extension are deliberately
    NOT implemented here: TER-08's own task list says the bridge checks "ship with"
    TER-10, which defines the deck data, and the LOS extension is guarded on TER-12
    shipping — neither exists yet, so there is nothing to resolve against.
    """
    where = doc.get("id", "anchor?")
    terrain = doc.get("terrain")
    if not terrain:
        return
    levels = terrain["levels"]
    w, h = doc["grid"]["w"], doc["grid"]["h"]

    # ---- region bounds and level range ----------------------------------------
    # A rect that merely overhangs the edge is clipped, not an error (decision 057 / TER-02
    # acceptance: "a generator emitting a region against the edge is the normal case, not a
    # bug"). A rect with NO overlap at all paints nothing and is unambiguously dead data —
    # that is what is flagged here, distinct from a partial clip.
    for region in terrain.get("regions", []):
        rx, ry, rw, rh = region["rect"]
        z = region["z"]
        if not (0 <= z <= levels):
            rep.err(where, f"region rect {region['rect']} has z={z}, outside "
                           f"0..{levels} (terrain.levels)")
        ox0, ox1 = max(0, rx), min(w, rx + rw)
        oy0, oy1 = max(0, ry), min(h, ry + rh)
        if ox1 <= ox0 or oy1 <= oy0:
            rep.err(where, f"region rect {region['rect']} does not overlap the "
                           f"{w}x{h} grid at all")

    # ---- heightmap dimensions and level range ----------------------------------
    heightmap_ok = True
    hm = terrain.get("heightmap")
    if hm is not None:
        if len(hm) != h:
            rep.err(where, f"heightmap has {len(hm)} row(s), grid.h is {h} — row-major "
                           f"(heightmap[y][x]); a transposed heightmap is otherwise "
                           f"undetectable")
            heightmap_ok = False
        for y, row in enumerate(hm):
            if len(row) != w:
                rep.err(where, f"heightmap row {y} has {len(row)} column(s), "
                               f"grid.w is {w}")
                heightmap_ok = False
        if heightmap_ok:
            for y, row in enumerate(hm):
                for x, v in enumerate(row):
                    if not (0 <= v <= levels):
                        rep.err(where, f"heightmap tile ({x},{y})={v}, outside "
                                       f"0..{levels} (terrain.levels)")
        if not heightmap_ok:
            return  # cannot safely index a grid whose declared shape is wrong

    grid = resolve_terrain(doc)

    def height_at(tx: int, ty: int) -> int | None:
        if 0 <= ty < len(grid) and 0 <= tx < len(grid[ty]):
            return grid[ty][tx]
        return None

    # ---- ramp connectivity ------------------------------------------------------
    ramps = terrain.get("ramps", [])
    ramp_tiles: set[tuple[int, int]] = {
        tuple(r["tile"]) for r in ramps
        if 0 <= r["tile"][0] < w and 0 <= r["tile"][1] < h
    }
    for ramp in ramps:
        tx, ty = ramp["tile"]
        frm, to, d = ramp["from"], ramp["to"], ramp["dir"]
        tag = f"ramp ({tx},{ty})"
        if not (0 <= tx < w and 0 <= ty < h):
            rep.err(where, f"{tag} is outside the grid {w}x{h}")
            continue
        if abs(to - frm) != 1:
            rep.err(where, f"{tag} claims to connect level {frm} to {to}, which differ "
                           f"by {abs(to - frm)}, not 1")
        dx, dy = RAMP_DIRS[d]
        from_h, to_h = height_at(tx - dx, ty - dy), height_at(tx + dx, ty + dy)
        if from_h is None or to_h is None:
            rep.err(where, f"{tag} dir {d} has no tile on one side inside the grid — "
                           f"a ramp declared next to a wall")
        else:
            if from_h != frm:
                rep.err(where, f"{tag} claims level {frm} at ({tx - dx},{ty - dy}) "
                               f"(behind, opposite {d}) but the resolved grid has {from_h}")
            if to_h != to:
                rep.err(where, f"{tag} claims level {to} at ({tx + dx},{ty + dy}) "
                               f"(ahead, dir {d}) but the resolved grid has {to_h}")

    # ---- the lane never steps more than one level per tile ----------------------
    for li, lane in enumerate(doc["paths"]):
        pts = [tuple(p[:2]) for p in lane["waypoints"]]
        tiles: list[tuple[int, int]] = []
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if ax == bx:
                step = 1 if by >= ay else -1
                for y in range(ay, by + step, step):
                    tiles.append((ax, y))
            elif ay == by:
                step = 1 if bx >= ax else -1
                for x in range(ax, bx + step, step):
                    tiles.append((x, ay))
            # a diagonal segment is already an error from check_anchor's own lane
            # check; not walked here rather than double-reported.
        walk = [t for i, t in enumerate(tiles) if i == 0 or t != tiles[i - 1]]
        tag = f"lane {li} ('{lane['id']}')"
        for (tx0, ty0), (tx1, ty1) in zip(walk, walk[1:]):
            h0, h1 = height_at(tx0, ty0), height_at(tx1, ty1)
            if h0 is None or h1 is None:
                continue  # out-of-grid point already reported by check_anchor
            delta = h1 - h0
            if abs(delta) > 1:
                rep.err(where, f"{tag} steps {delta:+d} level(s) between ({tx0},{ty0})="
                               f"{h0} and ({tx1},{ty1})={h1} — units cannot climb more "
                               f"than one level per tile")
            elif delta != 0 and (tx0, ty0) not in ramp_tiles and (tx1, ty1) not in ramp_tiles:
                rep.err(where, f"{tag} steps from ({tx0},{ty0})={h0} to ({tx1},{ty1})="
                               f"{h1} with no ramp declared on either tile")

    # ---- no emplacement on a ramp or a cliff face --------------------------------
    # PLC-05: `slots` is optional now (free placement authors none at all), so this is a
    # no-op rather than a KeyError on such an anchor -- there is nothing fixed-coordinate
    # to check a ramp/cliff against when every build point is chosen at play time.
    for s in doc.get("slots", []):
        sx, sy = s[0], s[1]
        own = height_at(sx, sy)
        if own is None:
            continue  # out-of-grid slot already reported by check_anchor
        if (sx, sy) in ramp_tiles:
            rep.err(where, f"slot ({sx},{sy}) sits on a ramp")
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nh = height_at(sx + dx, sy + dy)
            if nh is not None and abs(nh - own) > 1:
                rep.err(where, f"slot ({sx},{sy})={own} sits on a cliff face — "
                               f"neighbour ({sx + dx},{sy + dy})={nh}")
                break


def check_anchor(doc: dict, towers: dict, enemies: dict, rep: Report) -> None:
    # TER-08: must run before the "no emplacement unlocked" early return below, or
    # terrain checks silently never run on such an anchor.
    check_terrain(doc, rep)
    where = doc.get("id", "anchor?")
    w, h = doc["grid"]["w"], doc["grid"]["h"]
    paths: list[dict] = doc["paths"]

    # WAR-01: duplicate lane ids are a data error even though the rules never compare by
    # id — a duplicate is still a authoring mistake (dialog/HUD address a lane by id) and
    # cheap to catch here.
    lane_ids = [lane["id"] for lane in paths]
    dupes = sorted({i for i in lane_ids if lane_ids.count(i) > 1})
    if dupes:
        rep.err(where, f"duplicate lane id(s): {dupes}")

    # each lane must be inside the grid, orthogonal, and contiguous; expand every lane
    # into occupied tiles so slots can be checked against the union of all of them
    all_tiles: set[tuple[int, int]] = set()
    lane_points: list[list[tuple[float, float]]] = []
    for li, lane in enumerate(paths):
        tag = f"lane {li} ('{lane['id']}')"
        pts = [tuple(p[:2]) for p in lane["waypoints"]]
        for x, y in pts:
            if not (0 <= x < w and 0 <= y < h):
                rep.err(where, f"{tag} point ({x},{y}) outside grid {w}x{h}")
        for a, b in zip(pts, pts[1:]):
            if a[0] != b[0] and a[1] != b[1]:
                rep.err(where, f"{tag} segment {a}->{b} is diagonal; segments must be "
                               f"axis-aligned")
            if a == b:
                rep.err(where, f"{tag} has a zero-length segment at {a}")
        for a, b in zip(pts, pts[1:]):
            if a[0] == b[0]:
                lo, hi = sorted((a[1], b[1]))
                all_tiles |= {(a[0], y) for y in range(lo, hi + 1)}
            else:
                lo, hi = sorted((a[0], b[0]))
                all_tiles |= {(x, a[1]) for x in range(lo, hi + 1)}
        lane_points.append([(float(x), float(y)) for x, y in pts])

    # PLC-05: optional now -- an anchor authored for free placement carries none at all.
    slots: list = doc.get("slots", [])
    seen: set[tuple[int, int]] = set()
    for s in slots:
        t = tuple(s)
        if not (0 <= t[0] < w and 0 <= t[1] < h):
            rep.err(where, f"slot {t} outside grid {w}x{h}")
        if t in all_tiles:
            rep.err(where, f"slot {t} sits on the enemy path")
        if t in seen:
            rep.err(where, f"duplicate slot {t}")
        seen.add(t)

    # every wave must reference a real enemy and a lane that exists; and every lane must
    # be spawned into by at least one wave, somewhere in the anchor — a lane nothing ever
    # spawns onto is dead data (WAR-01).
    used_lanes: set[int] = set()
    for i, wave in enumerate(doc["waves"], 1):
        for sp in wave["spawns"]:
            if sp["enemy"] not in enemies:
                rep.err(where, f"wave {i} spawns unknown enemy '{sp['enemy']}'")
            lane = sp.get("lane", 0)
            if not (0 <= lane < len(paths)):
                rep.err(where, f"wave {i} spawn references lane {lane}, but this anchor "
                               f"has {len(paths)} lane(s)")
            else:
                used_lanes.add(lane)
    for li, lane in enumerate(paths):
        if li not in used_lanes:
            rep.err(where, f"lane {li} ('{lane['id']}') is never spawned into by any wave")

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

    # If capacity can run the hungriest emplacement at every emplacement simultaneously,
    # the player never has to choose and the hook is inert. Compare against the
    # emplacement-count CAP x max draw, not the sum of distinct types — a board is filled
    # with instances, and with one type unlocked the type-sum is meaninglessly small.
    #
    # PLC-05 / LF-107: the cap is `max_emplacements` when authored, else len(slots) — the
    # schema's root `anyOf` guarantees at least one of the two is present. This fallback is
    # what makes the rewrite provably neutral: none of today's 24 anchors authors
    # max_emplacements, so every one takes the len(slots) branch and reproduces its
    # pre-PLC-05 number exactly. An anchor with no `slots` at all (free placement, PLC-01)
    # has no len() to fall back to and MUST carry `max_emplacements` — which is what makes
    # this invariant checkable with no slots at all, the entire point of this rewrite.
    explicit_cap = doc.get("max_emplacements")
    if explicit_cap is not None:
        cap_n, denom_label = int(explicit_cap), "max_emplacements"
    elif slots:
        cap_n, denom_label = len(slots), "slots"
    else:
        # Unreachable when the schema's `anyOf` passed (both call sites in main() gate
        # check_anchor on validate_schema succeeding first) — a named failure rather than
        # a crash, in case this is ever called on a doc nothing has schema-checked.
        rep.err(where, "anchor declares neither `slots` nor `max_emplacements` — there is "
                       "no denominator for the board-saturation invariant")
        cap_n = denom_label = None

    # LF-103: "max draw" must include `upgrade.draw_mw`, not just the base `draw_mw`.
    # An upgrade replaces the base stat it names (schema description on `towers[].upgrade`)
    # rather than adding to it, so a tower's true worst-case draw is max(base, upgrade) —
    # almost always the upgrade, since every upgrade in data/towers.json today draws more
    # than its base. Reading base alone made an upgraded slot's real draw invisible to
    # exactly the guard that caught anchor-24 reaching 103% of saturation once (decision
    # 048) — a steep-upgrade-draw weapon (WAR-13's Siege Battery) would have made this
    # guard pass while the anchor it graded quietly had no power decision left in it.
    def _tower_max_draw(t: dict) -> float:
        up_draw = t.get("upgrade", {}).get("draw_mw")
        return max(t["draw_mw"], up_draw) if up_draw is not None else t["draw_mw"]

    max_draw = max(_tower_max_draw(t) for t in avail)
    if cap_n is not None:
        saturated = cap_n * max_draw
        if cap >= saturated:
            rep.err(where, f"capacity {cap} MW covers every emplacement at max draw "
                           f"including upgrades ({cap_n} [{denom_label}] x {max_draw} = "
                           f"{saturated} MW) — no power decision exists on this anchor")
        # Warned well before that, because the hook does not switch off at the boundary, it
        # fades towards it — and the drift is invisible until it crosses. Act I runs at
        # 29-38% of saturation. Act III reached 103% once, by paying for heavier waves with
        # reactor capacity a sweep was free to raise: every anchor still graded clean, the
        # validator said nothing until the last one tipped over, and the game's core
        # decision had quietly stopped existing on five levels. Decision 048.
        elif cap > saturated * SATURATION_WARN:
            rep.warn(where, f"capacity {cap} MW is {cap / saturated:.0%} of what would run "
                            f"every emplacement at max draw ({saturated} MW) — the power "
                            f"decision is getting thin; Act I sits at 29-38%")

        if cap_n < 3:
            noun = "build slots" if denom_label == "slots" else "emplacements (max_emplacements)"
            rep.warn(where, f"{cap_n} {noun} is very few")

        # Second, looser bound — WARNING only (see the module-level comment on
        # HEX_PACKING_EFFICIENCY: it is an upper bound the player can never reach, so on
        # its own it is a weaker guard than the one above, and must never be the only one).
        # Catches a cap authored far above what the board can physically hold, which is a
        # content bug even when capacity itself is fine.
        standoff: set[tuple[int, int]] = set(all_tiles)
        for (tx, ty) in all_tiles:
            for ddx in range(-LANE_STANDOFF_TILES, LANE_STANDOFF_TILES + 1):
                for ddy in range(-LANE_STANDOFF_TILES, LANE_STANDOFF_TILES + 1):
                    standoff.add((tx + ddx, ty + ddy))
        buildable_tiles = w * h - len(
            {t for t in standoff if 0 <= t[0] < w and 0 <= t[1] < h})
        area_bound = int(buildable_tiles * HEX_PACKING_EFFICIENCY
                         // EMPLACEMENT_FOOTPRINT_TILES)
        if area_bound > 0 and cap_n > area_bound * 2:
            rep.warn(where, f"{denom_label} {cap_n} is more than 2x the area-derived bound "
                            f"({area_bound}, from {buildable_tiles} buildable tile(s) at "
                            f"{HEX_PACKING_EFFICIENCY} packing / "
                            f"{EMPLACEMENT_FOOTPRINT_TILES}-tile footprint) — the cap may "
                            f"be authored far above what the board can physically hold")

    # A slot further from the path than any weapon's range is dead: nothing built there
    # can ever fire. This is invisible in the data and expensive to find by grading —
    # anchor-06 was authored with two dead slots and several marginal ones, and read as
    # a wave-balance problem through several sweeps before the layout was measured.
    # PLC-05: meaningless (and skipped) on an anchor with no fixed `slots` at all — there
    # is no coordinate to test range against when every build point is chosen at play time.
    ranged = [t for t in avail if t.get("damage", 0) > 0]
    if ranged and slots:
        best_range = max(t["range"] for t in ranged)
        short_range = min(t["range"] for t in ranged)

        def dist_to_path(sx: float, sy: float) -> float:
            # WAR-01: minimum distance over ALL lanes — a slot dead to every lane is
            # dead, but a slot near just one of several lanes is still worth a slot.
            best = float("inf")
            for pts in lane_points:
                for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                    dx, dy = bx - ax, by - ay
                    span = dx * dx + dy * dy
                    t = 0.0 if span == 0 else max(0.0, min(1.0,
                            ((sx - ax) * dx + (sy - ay) * dy) / span))
                    best = min(best, math.hypot(sx - (ax + t * dx), sy - (ay + t * dy)))
            return best

        dead, marginal = [], []
        for s in slots:
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
    ap.add_argument("--fixture", type=Path, default=None,
                     help="validate one anchor document in isolation (e.g. a TER-08 "
                          "fixture under data/schema/fixtures/) against the real, "
                          "tracked towers.json/enemies.json, instead of the whole of "
                          "data/ — how a fixture built to trip exactly one rule is "
                          "proven to fail red")
    args = ap.parse_args()
    rep = Report()

    schema_paths = sorted(SCHEMA.glob("*.schema.json"))
    schema_names = {p.name[: -len(".schema.json")] for p in schema_paths}
    exercised: set[str] = set()

    towers: dict[str, dict] = {}
    enemies: dict[str, dict] = {}
    anchors: dict[str, dict] = {}
    dialogs: list[tuple[Path, dict]] = []

    docs = discover_documents()
    for path in docs:
        doc = load(path)
        where = str(path.relative_to(ROOT))
        try:
            name = schema_for(doc, path)
        except SchemaDispatchError as e:
            rep.err(where, str(e))
            continue
        exercised.add(name)
        if not validate_schema(doc, name, where, rep):
            continue

        # Type-specific handling, routed by the schema a document declared for itself
        # rather than by where on disk it lives. check_anchor/check_dialog below are
        # the semantic cross-reference layer this dispatch does not replace.
        if name == "towers":
            towers = {t["id"]: t for t in doc.get("towers", [])}
        elif name == "enemies":
            enemies = {e["id"]: e for e in doc.get("enemies", [])}
        elif name == "anchor":
            anchors[doc["id"]] = doc
            if doc["id"] != path.stem:
                rep.err(where, f"id '{doc['id']}' does not match filename")
        elif name == "dialog":
            dialogs.append((path, doc))
        # "tuning" (and any future data-only schema) needs no further handling: shape
        # validation above is the entire check — see the module docstring on why its
        # values are inert to the Python sim by design.

    if args.fixture:
        # TER-08: prove a validator rule fails red on a fixture built to trip exactly it,
        # in isolation — the real towers/enemies just loaded from tracked data are the
        # catalogue it is checked against; only the anchor is swapped for the fixture.
        fpath = args.fixture
        try:
            fwhere = str(fpath.resolve().relative_to(ROOT))
        except ValueError:
            fwhere = str(fpath)
        frep = Report()
        fdoc = load(fpath)
        try:
            fname = schema_for(fdoc, fpath)
        except SchemaDispatchError as e:
            frep.err(fwhere, str(e))
            fname = None
        if fname is not None and validate_schema(fdoc, fname, fwhere, frep):
            if fname == "anchor":
                check_anchor(fdoc, towers, enemies, frep)
            else:
                frep.err(fwhere, f"--fixture only supports anchor documents "
                                 f"(got schema '{fname}')")
        for w in frep.warnings:
            print(f"  warn  {w}")
        for e in frep.errors:
            print(f"  ERROR {e}", file=sys.stderr)
        if frep.errors:
            print(f"\n{len(frep.errors)} error(s)", file=sys.stderr)
            return 1
        if not args.quiet:
            print(f"ok — {len(frep.warnings)} warning(s)")
        return 0

    for coll, label in ((towers, "tower"), (enemies, "enemy")):
        if len(coll) != len({k for k in coll}):
            rep.err(f"{label}s", "duplicate ids")

    for doc in anchors.values():
        check_anchor(doc, towers, enemies, rep)

    for _, doc in sorted(dialogs, key=lambda pd: pd[0]):
        check_dialog(doc, anchors, rep)

    for aid in anchors:
        if not (DATA / "dialog" / f"{aid}.json").exists():
            rep.warn(aid, "no dialog file")

    # Completeness assertion (PRC-10): a schema nothing exercises, or a document whose
    # named schema does not exist (already an error out of schema_for above), are both
    # coverage gaps and both fail the gate rather than passing silently.
    for missing in sorted(schema_names - exercised):
        rep.err(f"data/schema/{missing}.schema.json", "schema is not exercised by any document")

    if not args.quiet:
        print(f"{len(towers)} emplacements · {len(enemies)} enemies · "
              f"{len(anchors)} anchors · {len(dialogs)} dialog files")
    # Always printed, even under --quiet: tools/check.py's game data check runs this
    # script with --quiet and needs these counts for its detail line.
    print(f"{len(docs)} documents against {len(schema_names)} schemas "
          f"({len(exercised)} exercised)")
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
