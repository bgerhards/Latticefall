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

# COLS is independent of cell size: 12 columns kept 256px cells (3072px wide) comfortably
# inside any GL texture limit. At 1024px cells that becomes a 12288px-wide page, which is
# what GL_MAX_TEXTURE_SIZE_FLOOR below now catches before Pillow ever tries to allocate it.
# CELL itself is NOT a constant here (ART-03/LF-102) — render.py's `--cell` moves the
# render canvas size, and a hardcoded 256 here raised on the first render it opened at
# any other size (the size assertion in pack() below is the safety net that would have
# caught it; reading CELL from the manifest is what keeps that assertion from firing on a
# legitimately non-256 library instead of only on a genuinely mismatched one).
COLS = 12
PASSES = ("albedo", "glow")

# LF-114: the floor pack() asserts a page against before ever allocating it.
#
# The true Khronos-guaranteed minimum for OpenGL ES 3.0 (what Godot's GL Compatibility
# renderer targets) is GL_MAX_TEXTURE_SIZE >= 2048 — see the ES 3.0 spec, registry.khronos
# .org/OpenGL/specs/es/3.0/es_spec_3.0.pdf, table of required minimums. That number is not
# usable as this project's floor: the *current* 26-asset library already packs to a 3072px-
# wide page at COLS=12/cell=256 ("comfortably inside any GL texture limit" above predates
# this being measured), which would fail on day one against a literal 2048 floor.
#
# Measured on this machine instead of trusted from memory (CLAUDE.md's rule, and LF-114's
# whole point): a tiny EGL/desktop-GL probe against Mesa llvmpipe — the same software-GL
# stack `tools/toolpaths.godot()`'s preferred native Linux Godot build renders through —
# reports GL_MAX_TEXTURE_SIZE = 16384 here (EGL 1.5 surfaceless platform, no X/Wayland
# window needed; `glGetIntegerv(GL_MAX_TEXTURE_SIZE, ...)` after `eglBindAPI
# (EGL_OPENGL_API)`). That is this machine's real ceiling, not the floor to assert against
# — a page built here still has to upload correctly on whatever GPU the owner's Windows
# Godot editor runs on, which this probe cannot see.
#
# 4096 is picked as the working floor: comfortably above the current 3072px page (room to
# grow before ART-01's ~680-asset library needs true multi-page packing, not just a bigger
# single page), comfortably below both the 16384 measured here and every desktop/GLES3
# target this project has ever run on, and far enough above the 2048 spec minimum that no
# GL Compatibility implementation shipped in the GLES3 era is actually likely to be capped
# there in practice — the spec minimum has not been the practical minimum for a long time.
GL_MAX_TEXTURE_SIZE_FLOOR = 4096


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


def pack(entries: list[tuple[str, str, Path]], dest: Path, cell: int,
         write: bool) -> tuple[dict[str, list[int]], tuple[int, int]]:
    rows = (len(entries) + COLS - 1) // COLS
    size = (COLS * cell, max(rows, 1) * cell)

    # LF-114: assert before ever allocating. A page over this either fails to upload in
    # GL Compatibility or, on the Pillow side, blows up as an unexplained MemoryError for
    # an absurd size — this names the actual problem and the page count that would fix it.
    if size[0] > GL_MAX_TEXTURE_SIZE_FLOOR or size[1] > GL_MAX_TEXTURE_SIZE_FLOOR:
        pages_needed = max(
            -(-size[0] // GL_MAX_TEXTURE_SIZE_FLOOR),
            -(-size[1] // GL_MAX_TEXTURE_SIZE_FLOOR),
        )
        raise SystemExit(
            f"atlas page would be {size[0]}x{size[1]}px, over the "
            f"{GL_MAX_TEXTURE_SIZE_FLOOR}px GL_MAX_TEXTURE_SIZE floor (LF-114) — "
            f"{len(entries)} cells at {cell}px need at least {pages_needed} page(s) of "
            f"<= {GL_MAX_TEXTURE_SIZE_FLOOR}px each; multi-page packing is not "
            "implemented yet (backlog candidate, PRC-13 follow-up)")

    page = Image.new("RGBA", size, (0, 0, 0, 0)) if write else None
    index: dict[str, list[int]] = {}

    # LF-113: opens and checks every image whether or not this call is going to write
    # anything. It used to be `if not write: continue` right after the index line, which
    # meant `--check` short-circuited past Image.open() and this assertion never ran in
    # check mode — the one thing standing between a manifest that claims one cell size
    # and renders on disk that are actually another never fired where it mattered.
    for i, (name, slot, path) in enumerate(entries):
        col, row = i % COLS, i // COLS
        index[f"{name}|{slot}"] = [col, row]
        with Image.open(path) as im:
            im = im.convert("RGBA")
            if im.size != (cell, cell):
                raise SystemExit(f"{path.name} is {im.size}, expected {cell}x{cell} — "
                                 "the grid pack assumes a uniform cell and the pivot "
                                 "depends on it")
            if write:
                page.paste(im, (col * cell, row * cell))

    if write:
        dest.parent.mkdir(parents=True, exist_ok=True)
        page.save(dest, optimize=True)
    return index, size


def run(check: bool) -> int:
    """The actual work, split out from `main()` so `tools/blender/build.py` (PRC-13) can
    call this directly — one Python process, no subprocess, and a plain int back rather
    than having to scrape stdout — instead of shelling out to this file a second time
    right after importing `collect()`/`source_digest()` from it to decide whether packing
    is even needed. `main()` below is now just the CLI's argument parsing over this.
    """
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

    # Read from the manifest render.py just wrote, not a local constant (ART-03/LF-102):
    # render.py's --cell moves the render canvas size, and this packer has to follow it
    # or the size assertion in pack() above raises on the very first render it opens.
    if "cell" not in doc:
        print(f"manifest has no \"cell\" — re-run render.py to write one", file=sys.stderr)
        return 1
    cell = int(doc["cell"])

    atlas = {"cell": cell, "cols": COLS, "pages": {}, "index": {},
             "source_digest": source_digest(groups)}
    for pass_name in PASSES:
        entries = groups[pass_name]
        if not entries:
            continue
        dest = ATLAS_DIR / f"{pass_name}.png"
        index, size = pack(entries, dest, cell, write=not check)
        atlas["pages"][pass_name] = str(dest.relative_to(ROOT))
        atlas["index"][pass_name] = index
        kb = dest.stat().st_size / 1024 if dest.exists() and not check else 0
        print(f"{pass_name:7s} {len(entries):3d} cells -> {size[0]}x{size[1]}"
              + (f"  {kb:.0f} KB" if kb else ""))

    if check:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report what would be packed, write nothing")
    args = ap.parse_args()
    return run(args.check)


if __name__ == "__main__":
    raise SystemExit(main())
