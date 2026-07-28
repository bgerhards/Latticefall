#!/usr/bin/env python3
"""Pack the rendered sprite library into one atlas page per pass.

Why this file exists
--------------------
The library is 192 loose 256x256 PNGs — 24 assets, four yaws, two passes. Every one is
a separate `CompressedTexture2D` that Godot loads, tracks and binds independently, and
the board draws from most of them every frame. LF-004.

**The pack is a fixed grid, not a rectangle packer, and nothing is trimmed.** That is
the whole safety argument. `render.py` raises the camera by `HEIGHT_BIAS` so tall assets
clear the top of their cell, and `calibrate()` *measures* where world (0,0,0) actually
lands — 127.5, 171.5 — and writes it to the manifest. One pivot serves every sprite only
because every sprite occupies an identical 256x256 cell. Trimming transparent margins
would give each sprite its own origin and put the pivot back to being per-asset, which
is exactly the bug LF-027 was: a hardcoded `CELL//2` drew every sprite above its own
tile. A grid keeps the pivot true by construction.

Albedo and glow get separate pages because the engine draws them in separate passes and
modulates the glow layer by bus load (decision 007). One mixed page would bind the same
texture twice per frame for two different purposes and gain nothing.

What it writes
--------------
`assets/renders/atlas/{albedo,glow}.png`, plus an `atlas` section added to
`assets/renders/sprites.json`. The per-file paths in that manifest are left alone, so
`render.py` can rewrite it without knowing this step exists and `sprites.gd` can still
fall back to loading individual files when no atlas has been packed.

Order matters: **render -> mask_glow -> pack_atlas -> --import**. Packing before masking
would bake the unmasked opaque-alpha glow into the page, and skipping the import leaves
Godot serving the previous `.ctex` from its cache.

    .venv/bin/python tools/blender/pack_atlas.py
    .venv/bin/python tools/blender/pack_atlas.py --check    # verify, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "assets" / "renders" / "sprites.json"
ATLAS_DIR = ROOT / "assets" / "renders" / "atlas"

CELL = 256          # every render is 256x256; asserted below rather than assumed
COLS = 12           # 12 * 256 = 3072 px wide, comfortably inside any GL texture limit
PASSES = ("albedo", "glow")


def source_digest(groups: dict) -> str:
    """One hash over every render that went into the pages.

    An atlas is derived output with no visible link to its inputs: re-render a sprite,
    forget to re-pack, and the game happily draws the old pixels out of the stale page.
    That is the same class of failure as skipping `--import`, which has already cost this
    project a full round of misdiagnosis. `tools/check.py` compares this against the
    renders on disk so the mistake is a red gate rather than a puzzle.
    """
    h = hashlib.sha256()
    for pass_name in PASSES:
        for name, slot, path in groups.get(pass_name, []):
            h.update(f"{pass_name}|{name}|{slot}|".encode())
            h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def collect(doc: dict) -> dict[str, list[tuple[str, str, Path]]]:
    """(key, yaw slot, path) per pass, in a fixed order.

    Sorted by name then yaw so the same library always packs to the same grid. A packer
    whose output moves between runs would make every atlas a spurious diff and every
    cached region wrong after an unrelated re-render.
    """
    out: dict[str, list[tuple[str, str, Path]]] = {p: [] for p in PASSES}
    for name in sorted(doc.get("sprites", {})):
        by_yaw = doc["sprites"][name]
        for slot in sorted(by_yaw):
            for pass_name in PASSES:
                rel = by_yaw[slot].get(pass_name)
                if rel:
                    out[pass_name].append((name, slot, ROOT / rel))
    return out


def pack(entries: list[tuple[str, str, Path]], dest: Path,
         write: bool) -> tuple[dict[str, list[int]], tuple[int, int]]:
    rows = (len(entries) + COLS - 1) // COLS
    size = (COLS * CELL, max(rows, 1) * CELL)
    page = Image.new("RGBA", size, (0, 0, 0, 0)) if write else None
    index: dict[str, list[int]] = {}

    for i, (name, slot, path) in enumerate(entries):
        col, row = i % COLS, i // COLS
        index[f"{name}|{slot}"] = [col, row]
        if not write:
            continue
        with Image.open(path) as im:
            im = im.convert("RGBA")
            if im.size != (CELL, CELL):
                raise SystemExit(f"{path.name} is {im.size}, expected {CELL}x{CELL} — "
                                 "the grid pack assumes a uniform cell and the pivot "
                                 "depends on it")
            page.paste(im, (col * CELL, row * CELL))

    if write:
        dest.parent.mkdir(parents=True, exist_ok=True)
        page.save(dest, optimize=True)
    return index, size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report what would be packed, write nothing")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"no manifest at {MANIFEST}", file=sys.stderr)
        return 1
    doc = json.loads(MANIFEST.read_text())
    groups = collect(doc)

    missing = [p for g in groups.values() for (_, _, p) in g if not p.exists()]
    if missing:
        for p in missing[:5]:
            print(f"missing render: {p.relative_to(ROOT)}", file=sys.stderr)
        print(f"{len(missing)} renders missing — run render.py first", file=sys.stderr)
        return 1

    atlas = {"cell": CELL, "cols": COLS, "pages": {}, "index": {},
             "source_digest": source_digest(groups)}
    for pass_name in PASSES:
        entries = groups[pass_name]
        if not entries:
            continue
        dest = ATLAS_DIR / f"{pass_name}.png"
        index, size = pack(entries, dest, write=not args.check)
        atlas["pages"][pass_name] = str(dest.relative_to(ROOT))
        atlas["index"][pass_name] = index
        kb = dest.stat().st_size / 1024 if dest.exists() and not args.check else 0
        print(f"{pass_name:7s} {len(entries):3d} cells -> {size[0]}x{size[1]}"
              + (f"  {kb:.0f} KB" if kb else ""))

    if args.check:
        print("\n--check: nothing written")
        return 0

    doc["atlas"] = atlas
    MANIFEST.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    loose = sum(len(g) for g in groups.values())
    print(f"\n{loose} loose textures -> {len(atlas['pages'])} atlas pages")
    print(f"manifest updated: {MANIFEST.relative_to(ROOT)}")
    print("\nRe-import or the game keeps serving the cached .ctex:")
    print("  /Applications/Godot.app/Contents/MacOS/Godot --headless --path . --import")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
