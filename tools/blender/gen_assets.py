#!/usr/bin/env python3
"""
Cross-check the asset<->data coupling without launching Blender (PRC-14).

The problem (see `docs/issues/PRC-14-generate-asset-data-coupling.md`): "which data id has
which sprite" was hand-kept in step across four places at once — `render.py`'s `ASSETS`
dict, `data/towers.json`/`data/enemies.json`, `anchor_view.gd`'s inline
`id.replace("-", "_")`, and the generated manifest — and only one failure direction (a data
id with no sprite) had a check at all. A sprite with no data id renders, packs and ships as
a dead atlas cell with nothing to say so.

This file is the missing bidirectional check, and it runs under plain `.venv/bin/python`
rather than inside Blender: `render.py` does `import bpy` at module scope and cannot be
imported outside it, so its `ASSETS` dict is read here with `ast`, not `import render`.
That is also why this is fast enough to be a "no-op verification" — no Blender process, no
scene, no render.

Two ids of interest:
  - the DATA side: every tower id in `data/towers.json` and enemy id in
    `data/enemies.json` — read straight off the tracked JSON, never written to.
  - the RENDER side: every builder function name registered in `render.py`'s `ASSETS`
    dict.

The naming convention that ties them together is one function, `name_for()` below,
mirroring `scripts/sprites.gd`'s `Sprites.name_for()` byte-for-byte. GDScript and Python
cannot share one constant across that boundary — `render.py`'s `YAW_COUNT` solves the same
cross-language problem for the yaw count (ART-02) — so the fix here is the same shape: one
trivial one-liner in each language, each pointing at the other in its own doc comment,
instead of the transform being re-derived independently at every call site.

What this script does not do
-----------------------------
PRC-14's full spec also calls for: a data-declared `assets` block (schema-validated, in
`data/towers.json`/`data/enemies.json` or a new `data/assets.json`), the `anchor_view.gd`
call-site swap to `Sprites.name_for()`, `tools/check.py`'s `sprite coverage` check
reporting this same bidirectional result inside the gate, and two doc updates
(`.claude/skills/new-asset/SKILL.md`, `CLAUDE.md`). None of those paths live under
`tools/blender/**` or `scripts/sprites.gd`, and all of them were owned by other in-flight
agents the session this file was written (`data/**`, `scripts/anchor_view.gd`,
`tools/check.py`, `.claude/**`, `CLAUDE.md` were mid-edit elsewhere at the same time —
`git status` showed live changes to `scripts/anchor_view.gd` among others). This file is
the render-side half PRC-14 asks for: the coupling check, wired up so whoever next owns
`tools/check.py` can call `check()` (or shell out to `--check`'s exit code) directly
instead of re-deriving the cross-reference a second time.

One consequence of not having the data-declared block yet: there is no per-id yaw-count
override to assert against (PRC-14's task "assert the per-id yaw count matches what the
manifest actually contains"). `manifest_yaw_mismatches()` below asserts every manifest
entry against the manifest's own global `yaw_count` instead — the only source that exists
today, since every asset in the library renders at one yaw count uniformly until ART-01
lands. Its shape is written so that comparison point can move to a per-id data field
without the function changing shape once that field exists.

Usage:
    .venv/bin/python tools/blender/gen_assets.py            # human-readable report
    .venv/bin/python tools/blender/gen_assets.py --check     # exit 0 clean / 1 mismatch
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDER_PY = Path(__file__).with_name("render.py")
MANIFEST = ROOT / "assets" / "renders" / "sprites.json"

# Legitimately id-less props: the ring, tiles and slot decal are board furniture, never
# looked up by a tower or enemy id — PRC-14's own problem statement names this set ("the
# ring, bindstone, tiles and board furniture are legitimately id-less"). There is no
# bindstone asset in render.py's ASSETS dict today, so it is not listed below; add it here
# the day one exists. This allowlist belongs in data (schema-declared) per the full spec,
# but data/** was another agent's live edit this session — see the module docstring.
PROP_ALLOWLIST = frozenset({"tile_ground", "tile_path", "tile_slot", "anchor_ring"})


def name_for(id_: str) -> str:
    """Mirrors `scripts/sprites.gd`'s `Sprites.name_for()` exactly — see that function's
    own doc comment, which points back here. Kept as a one-line, obviously-correct
    transform in both languages rather than shared, because GDScript and Python cannot
    share a constant across the Blender/Godot boundary (the same problem `YAW_COUNT`
    solves for the yaw count, ART-02)."""
    return id_.replace("-", "_")


def render_asset_names() -> list[str]:
    """Every key in `render.py`'s `ASSETS` dict, read with `ast` rather than imported.

    `render.py` does `import bpy` at module scope, so it cannot load under plain
    `.venv/bin/python` — this is the same constraint `tools/blender/build.py` works
    around by shelling out to Blender for `--list`/`--print-hashes`. Reading the source
    as a syntax tree instead avoids needing Blender at all for a check this cheap.
    """
    tree = ast.parse(RENDER_PY.read_text(), filename=str(RENDER_PY))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ASSETS" for t in node.targets
        ):
            if not isinstance(node.value, ast.Dict):
                raise SystemExit("gen_assets: ASSETS is not a dict literal — cannot parse")
            names = []
            for k in node.value.keys:
                if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                    raise SystemExit("gen_assets: ASSETS has a non-string-literal key")
                names.append(k.value)
            return names
    raise SystemExit("gen_assets: no top-level ASSETS assignment found in render.py")


def data_ids() -> tuple[list[str], list[str]]:
    """(tower ids, enemy ids), read straight off the tracked JSON. Read-only — this script
    never writes to `data/**`, which is another workstream's live territory this session.
    """
    towers = json.loads((ROOT / "data" / "towers.json").read_text())["towers"]
    enemies = json.loads((ROOT / "data" / "enemies.json").read_text())["enemies"]
    return [t["id"] for t in towers], [e["id"] for e in enemies]


def check() -> tuple[list[str], list[str]]:
    """(missing, orphaned).

    missing — a data id (tower or enemy) with no render asset under its derived name.
    This is `tools/check.py`'s existing `sprite coverage` direction, reproduced here so
    it can be asked without a manifest on disk at all (render.py's ASSETS dict is the
    source of truth for what *can* be rendered; the manifest only records what *has*
    been).

    orphaned — a render asset claimed by no data id and not in `PROP_ALLOWLIST`. This is
    the direction PRC-14's problem statement says nothing catches today: a sprite with no
    data id renders, packs and ships as a dead atlas cell.
    """
    tower_ids, enemy_ids = data_ids()
    ids = tower_ids + enemy_ids
    assets = render_asset_names()
    expected = {name_for(i) for i in ids}
    missing = sorted(i for i in ids if name_for(i) not in assets)
    claimed = expected | PROP_ALLOWLIST
    orphaned = sorted(a for a in assets if a not in claimed)
    return missing, orphaned


def manifest_yaw_mismatches(manifest_path: Path = MANIFEST) -> list[str]:
    """Every asset whose manifest entry has a different number of yaw slots than the
    manifest's own declared `yaw_count` — e.g. a render interrupted partway through a
    yaw-count change, or (once ART-01 lands per-asset yaw counts) an asset silently
    rendered at the wrong one. Compared against the manifest's *global* `yaw_count` today
    because nothing in `data/**` yet declares a per-asset yaw count — see the module
    docstring. An empty manifest (no renders yet) is not a mismatch; it is reported
    separately by whatever asked for this list.
    """
    if not manifest_path.exists():
        return []
    doc = json.loads(manifest_path.read_text())
    expected = int(doc.get("yaw_count", len(doc.get("yaws", []))))
    bad = []
    for name, by_yaw in doc.get("sprites", {}).items():
        if len(by_yaw) != expected:
            bad.append(f"{name} has {len(by_yaw)} yaw(s) in the manifest, "
                       f"which declares yaw_count={expected}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                     help="exit 0/1 only; print nothing on success")
    args = ap.parse_args()

    missing, orphaned = check()
    yaw_bad = manifest_yaw_mismatches()
    tower_ids, enemy_ids = data_ids()
    n_ids = len(tower_ids) + len(enemy_ids)
    n_assets = len(render_asset_names())

    if not missing and not orphaned and not yaw_bad:
        if not args.check:
            print(f"gen_assets: ok — {n_ids} ids drawn from {n_assets} sprites, "
                  f"0 orphaned, yaw counts consistent")
        return 0

    if missing:
        print("gen_assets: missing sprite(s) for data id(s): " + ", ".join(missing),
              file=sys.stderr)
    if orphaned:
        print("gen_assets: orphaned render asset(s) — claimed by no data id and not in "
              "PROP_ALLOWLIST: " + ", ".join(orphaned), file=sys.stderr)
    if yaw_bad:
        print("gen_assets: yaw count mismatch(es): " + "; ".join(yaw_bad), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
