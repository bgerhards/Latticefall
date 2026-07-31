"""
ART-01: head/base split for Latticefall's emplacements, additive and opt-in.

Runs inside Blender 5.2, same invocation shape as tools/blender/render.py:

    <blender> -b --python tools/blender/render_split.py -- --split pulse_turret
    <blender> -b --python tools/blender/render_split.py -- --split pulse_turret,arc_node
    <blender> -b --python tools/blender/render_split.py -- --list

Why this is a separate file rather than a new code path inside render.py
--------------------------------------------------------------------------
tools/blender/build.py's incremental skip logic (PRC-13) hashes each asset against
render.py's own `_shared_source()` — everything in that file except each ASSETS
builder's own function body. Any edit to render.py, even a brand-new function that no
existing asset calls, moves that shared hash and makes every one of the 26 already-
committed assets look "changed" to build.py, triggering a full, unwanted re-render
(and re-pack, and re-import) the next time anyone runs `build.py` for an unrelated
one-asset tweak. Confirmed empirically while prototyping this: adding ART-01's
constants and helper functions directly into render.py made a real `build.py --dry-run`
report all 26 assets as "source changed", even though not one of their builder
function bodies had. Keeping the split in its own file leaves render.py — and every
hash build.py has already stored — untouched, byte-for-byte, until ART-01 is actually
cut over (see "What the view layer needs" below for what that cutover is).

This imports render.py's shared rig (camera, materials, primitives, the FX palette)
rather than duplicating it, so both files render through literally the same camera,
projection and material code.

Manifest shape: deliberately FLAT, identical in shape to every entry render.py already
writes — a split asset gets two new top-level keys, "<name>_base" and "<name>_head",
each a plain {"b00": {"albedo": .., "glow": ..}, ...} dict. Nothing is nested under the
asset's existing "<name>" key, and that key is never touched by this file. This is what
lets tools/blender/pack_atlas.py pack the result with ZERO code changes (verified: real
pack_atlas.collect()/pack() against a synthetic post-cutover manifest built entirely
from this file's own real renders — see this issue's report) and what lets
tools/blender/gen_assets.py's coverage check (which reads render.py's ASSETS dict via
`ast`, never this file or its output) stay green without modification.

A base never tracks a target: it stays at the same coarse resolution the library
already ships at (YAW_BASE = 4, unchanged from today). A head swivels to face whatever
the emplacement is aiming at, so it gets the fine resolution a tracking turret needs
(YAW_HEAD = 16).

Slot keys are bucket-indexed ("b00".."b15"), not degree-indexed ("y045") — a 16-bucket
asset is 22.5deg apart, so half its buckets fall on fractional degrees "y%03d" can't
spell (see scripts/iso.gd's `Iso.yaw_for_heading()`, which now asserts its own bucket
width is integral for exactly this reason). `bucket_slot()` below mirrors
`scripts/iso.gd`'s `Iso.bucket_slot()` byte-for-byte.

See docs/issues/ART-01-sixteen-yaws-head-base-split.md for the acceptance criteria, and
this issue's own report (docs/STATE.md / the session that added this file) for the
measured page count, VRAM and render wall-clock this was shipped against.

What this file deliberately does NOT do
-----------------------------------------
It never touches assets/renders/sprites.json's existing "<name>" combined entries, and
it is never called by tools/blender/build.py's ordinary incremental path — so running
this is the only way any of its output reaches the committed manifest. Cutting the game
over to actually draw these two layers instead of the combined one is `anchor_view.gd`'s
`_build_drawables()` and (for the `--facings` verification hook) `main.gd` — both out of
this file's ownership; see the issue report for the precise, line-level change each
needs.
"""
from __future__ import annotations

import json
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render as R  # noqa: E402 — the shared rig this file renders through; render.py
                    # does `import bpy` at module scope so this only works inside Blender,
                    # same constraint render.py itself documents.

# Bring the primitives/materials/palette/FX table into this module's namespace so the
# nine builder functions below read exactly as they do in render.py — no `R.` prefix
# noise on every call. These are render.py's existing, already-reviewed names; nothing
# here redefines or shadows them.
cyl = R.cyl
cone = R.cone
cube = R.cube
sphere = R.sphere
torus = R.torus
mat = R.mat
glow_point = R.glow_point
FX = R.FX
STEEL = R.STEEL
STEEL_LT = R.STEEL_LT
BONE = R.BONE
STONE = R.STONE
STONE_WARM = R.STONE_WARM

ROOT = R.ROOT
OUT_DIR = R.OUT_DIR
MANIFEST = R.MANIFEST

# A base never tracks a target: it stays at the same coarse resolution the library
# already ships at. A head swivels to face whatever the emplacement is aiming at, so it
# gets the fine resolution a tracking turret needs. Measured cost (see this issue's own
# report): 9 emplacements split this way, plus units at their current 4 yaws and props
# unchanged, packs to 300 cells/pass — one page per pass at pack_atlas.py's existing
# COLS=12, comfortably under both its 8192px floor and this machine's measured
# GL_MAX_TEXTURE_SIZE=16384 — for ~157 MB of uncompressed atlas VRAM, under the 226 MB
# flat-16 ceiling PRD risk 9 names.
YAW_BASE = 4
YAW_HEAD = 16


def bucket_slot(i: int) -> str:
    """The one place the "bNN" slot-name format is written down on the Python side.
    Mirrors scripts/iso.gd's `Iso.bucket_slot()` — Python and GDScript can't share a
    constant across the Blender/Godot process boundary (the same problem render.py's
    `YAW_COUNT` solves differently), so this is kept a small, obviously-correct
    one-liner in both languages instead. Zero-padded to 2 digits: 16 buckets is the
    largest count anything here renders, and "b00".."b15" sorts lexicographically in
    the same order as numerically, which is what keeps
    `tools/blender/pack_atlas.py`'s `collect()`'s `sorted(by_yaw)` deterministic, the
    same as the old "y045" style slot names did.
    """
    return "b%02d" % i


# ── split builder functions ──────────────────────────────────────────────────
# One base/head pair per emplacement in data/towers.json. Each pair is a mechanical
# split of render.py's existing combined `a_<name>()` — same geometry, same materials,
# just partitioned by which half swivels to track a target ("head") and which doesn't
# ("base"). See the issue report for the per-asset split rationale.

def a_pulse_turret_base():
    cyl(6, 0.62, 0.26, (0, 0, 0.13), material=mat("tb", STEEL, 0.45, 0.55))


def a_pulse_turret_head():
    cyl(6, 0.40, 0.36, (0, 0, 0.42), material=mat("th", BONE, 0.30, 0.45))
    cyl(16, 0.085, 0.9, (0.0, 0.30, 0.56), rot=(math.radians(76), 0, 0),
        material=mat("tr", STEEL, 0.9, 0.25))
    fx = FX["pulse_turret"]
    glow_point("tt", (0.0, 0.63, 0.70), fx["colour"], core=fx["core"])
    torus(0.405, 0.014, (0, 0, 0.42),
          material=mat("tband", fx["colour"], 0.0, 0.3, emit=fx["colour"], emit_strength=0.30))
    for s in (-1, 1):
        cube(0.13, (s * 0.34, -0.1, 0.5), scale=(0.5, 1.5, 1.7),
             material=mat("tf%d" % s, STEEL_LT, 0.7, 0.35))


def a_arc_node_base():
    cyl(8, 0.55, 0.2, (0, 0, 0.1), material=mat("nb", STEEL, 0.5, 0.5))
    for i in range(3):
        a = math.radians(i * 120)
        cube(0.1, (math.cos(a) * 0.34, math.sin(a) * 0.34, 0.45), scale=(1, 1, 3.2),
             rot_z=a, material=mat("np%d" % i, STEEL_LT, 0.75, 0.3))


def a_arc_node_head():
    fx = FX["arc_node"]
    glow_point("nc", (0, 0, 0.66), fx["colour"], core=fx["core"], r_halo=0.065, r_core=0.028,
               s_halo=0.35, s_core=0.80)
    for i in range(3):
        a = math.radians(i * 120)
        sphere(0.028, (math.cos(a) * 0.34, math.sin(a) * 0.34, 0.62), segments=10, rings=6,
               material=mat("ncp%d" % i, fx["colour"], 0.0, 0.25,
                            emit=fx["colour"], emit_strength=0.30))


def a_scan_relay_base():
    cyl(6, 0.44, 0.16, (0, 0, 0.08), material=mat("rb", STEEL, 0.45, 0.55))
    cyl(8, 0.07, 1.00, (0, 0, 0.60), material=mat("rm", STEEL_LT, 0.80, 0.30))
    for s in (-1, 1):
        cube(0.05, (s * 0.22, 0, 0.34), scale=(1.0, 1.0, 5.2), rot_z=math.radians(28),
             material=mat("rs%d" % s, STEEL, 0.6, 0.5))


def a_scan_relay_head():
    cone(20, 0.34, 0.05, 0.22, (0, 0.10, 1.10), rot=(math.radians(58), 0, 0),
         material=mat("rd", STEEL_LT, 0.7, 0.35))
    fx = FX["scan_relay"]
    cone(20, 0.29, 0.04, 0.04, (0, 0.155, 1.13), rot=(math.radians(58), 0, 0),
         material=mat("rf", fx["colour"], 0.0, 0.35, emit=fx["colour"], emit_strength=0.55))
    sphere(0.032, (0, 0.02, 1.14), segments=10, rings=6,
           material=mat("rt", fx["colour"], 0.0, 0.3, emit=fx["colour"], emit_strength=0.35))


def a_shield_wall_base():
    cube(1.0, (0, 0, 0.07), scale=(0.86, 0.34, 0.14), material=mat("wb", STONE, 0.2, 0.8))
    for s in (-1, 1):
        cube(0.26, (s * 0.36, 0, 0.40), scale=(1.0, 1.0, 2.6),
             material=mat("wp%d" % s, STEEL, 0.55, 0.45))
        cyl(6, 0.10, 0.14, (s * 0.36, 0, 0.75), material=mat("wc%d" % s, STEEL_LT, 0.8, 0.3))


def a_shield_wall_head():
    fx = FX["shield_wall"]
    for i, z in enumerate((0.30, 0.46, 0.62)):
        cube(1.0, (0, 0, z), scale=(0.60 - i * 0.03, 0.025, 0.05),
             material=mat("ws%d" % i, fx["colour"], 0.1, 0.25,
                          emit=fx["colour"], emit_strength=0.42))
    for s in (-1, 1):
        cube(0.07, (s * 0.36, 0, 0.14), scale=(1.0, 2.4, 1.0),
             material=mat("wf%d" % s, STEEL_LT, 0.7, 0.4))


def a_ion_lance_base():
    cyl(8, 0.58, 0.20, (0, 0, 0.10), material=mat("lb", STEEL, 0.5, 0.5))
    cube(0.52, (0, -0.06, 0.34), scale=(1.0, 0.9, 0.8), material=mat("lh", STONE, 0.35, 0.6))


def a_ion_lance_head():
    cyl(14, 0.115, 1.34, (0.0, 0.34, 0.72), rot=(math.radians(68), 0, 0),
        material=mat("lr", STEEL_LT, 0.9, 0.22))
    fx = FX["ion_lance"]
    cyl(14, 0.16, 0.20, (0.0, 0.06, 0.44), rot=(math.radians(68), 0, 0),
        material=mat("lc", fx["colour"], 0.0, 0.3, emit=fx["colour"], emit_strength=0.30))
    for s in (-1, 1):                                   # recoil rails along the barrel
        cube(0.06, (s * 0.17, 0.24, 0.60), scale=(1.0, 1.0, 7.0),
             rot_z=0.0, material=mat("lg%d" % s, STEEL, 0.7, 0.35))
    glow_point("lm", (0.0, 0.80, 1.02), fx["colour"], core=fx["core"],
               r_halo=0.075, r_core=0.032, s_halo=0.40, s_core=0.85)


def a_flak_array_base():
    cyl(8, 0.60, 0.22, (0, 0, 0.11), material=mat("kb", STEEL, 0.5, 0.5))
    cube(0.46, (0, -0.04, 0.36), scale=(1.5, 0.9, 0.7), material=mat("kh", STEEL_LT, 0.6, 0.4))
    for s in (-1, 1):
        cube(0.22, (s * 0.46, -0.18, 0.28), scale=(1.0, 1.4, 0.9),
             material=mat("kc%d" % s, STONE_WARM, 0.2, 0.75))


def a_flak_array_head():
    fx = FX["flak_array"]
    for i, s in enumerate((-1, 1)):
        for j, z in enumerate((0.46, 0.62)):
            cyl(10, 0.055, 0.68, (s * 0.20, 0.26, z), rot=(math.radians(74), 0, 0),
                material=mat("kr%d%d" % (i, j), STEEL_LT, 0.9, 0.25))
            tip = (s * 0.20, 0.26 + 0.34 * math.sin(math.radians(74)),
                   z + 0.34 * math.cos(math.radians(74)))
            sphere(0.024, tip, segments=8, rings=6,
                   material=mat("kt%d%d" % (i, j), fx["core"] or fx["colour"], 0.0, 0.2,
                                emit=fx["core"] or fx["colour"], emit_strength=0.62))


def a_anchor_damper_base():
    cyl(12, 0.52, 0.30, (0, 0, 0.15), material=mat("db", STONE, 0.1, 0.8))
    cyl(12, 0.44, 0.34, (0, 0, 0.46), material=mat("dd", STEEL, 0.55, 0.45))
    for i in range(3):                                  # coil bands
        cyl(24, 0.47, 0.045, (0, 0, 0.34 + i * 0.14),
            material=mat("dc%d" % i, BONE, 0.75, 0.3))
    for i in range(3):                                  # standoffs holding the ring up
        a = math.radians(i * 120)
        cube(0.06, (math.cos(a) * 0.30, math.sin(a) * 0.30, 0.74), scale=(1, 1, 3.4),
             rot_z=a, material=mat("ds%d" % i, STEEL_LT, 0.7, 0.35))


def a_anchor_damper_head():
    fx = FX["anchor_damper"]
    torus(0.28, 0.032, (0, 0, 0.94),
          material=mat("dr", fx["colour"], 0.2, 0.3, emit=fx["colour"], emit_strength=0.45))


def a_mortar_emplacement_base():
    cube(1.0, (0, 0, 0.06), scale=(0.78, 0.78, 0.12), material=mat("mb", STONE, 0.15, 0.8))
    cyl(8, 0.40, 0.24, (0, -0.06, 0.24), material=mat("mm", STEEL, 0.5, 0.5))
    for s in (-1, 1):                                   # recoil spades
        cube(0.10, (s * 0.40, -0.28, 0.16), scale=(1.0, 2.2, 1.2),
             material=mat("mp%d" % s, BONE, 0.4, 0.6))
    for s in (-1, 1):                                   # shell rack
        cube(0.12, (s * 0.30, -0.40, 0.24), scale=(1.0, 1.0, 2.0),
             material=mat("mr%d" % s, STONE_WARM, 0.2, 0.7))


def a_mortar_emplacement_head():
    cyl(14, 0.20, 0.86, (0.0, 0.16, 0.62), rot=(math.radians(28), 0, 0),
        material=mat("mt", STEEL_LT, 0.85, 0.3))
    fx = FX["mortar_emplacement"]
    cyl(14, 0.24, 0.12, (0.0, 0.02, 0.30), rot=(math.radians(28), 0, 0),
        material=mat("mc", fx["colour"], 0.0, 0.3, emit=fx["colour"], emit_strength=0.28))
    glow_point("mg", (0.0, 0.44, 0.86), fx["colour"], core=fx["core"],
               r_halo=0.062, r_core=0.026, s_halo=0.35, s_core=0.78)


def a_restorer_base():
    cube(1.0, (0, 0, 0.07), scale=(0.66, 0.66, 0.14), material=mat("rb", STONE, 0.15, 0.8))
    cube(0.62, (0, 0, 0.48), scale=(0.9, 0.75, 1.15), material=mat("rc", STEEL, 0.5, 0.5))
    for i in range(5):                                  # cooling fins
        cube(0.60, (0, -0.24, 0.26 + i * 0.13), scale=(1.0, 0.30, 0.06),
             material=mat("rf%d" % i, STEEL_LT, 0.8, 0.3))
    for s in (-1, 1):                                   # conduit down to the deck
        cyl(6, 0.045, 0.42, (s * 0.34, 0.02, 0.24),
            material=mat("rd%d" % s, STEEL_LT, 0.75, 0.35))


def a_restorer_head():
    fx = FX["restorer"]
    cyl(12, 0.17, 0.62, (0, 0.34, 0.66),
        material=mat("rk", fx["colour"], 0.0, 0.25, emit=fx["colour"], emit_strength=0.9))
    cyl(12, 0.21, 0.06, (0, 0.34, 1.00), material=mat("rt", BONE, 0.7, 0.35))


SPLIT_ASSETS = {
    "pulse_turret": {"base": (a_pulse_turret_base, YAW_BASE),
                      "head": (a_pulse_turret_head, YAW_HEAD)},
    "arc_node": {"base": (a_arc_node_base, YAW_BASE),
                 "head": (a_arc_node_head, YAW_HEAD)},
    "scan_relay": {"base": (a_scan_relay_base, YAW_BASE),
                   "head": (a_scan_relay_head, YAW_HEAD)},
    "shield_wall": {"base": (a_shield_wall_base, YAW_BASE),
                    "head": (a_shield_wall_head, YAW_HEAD)},
    "ion_lance": {"base": (a_ion_lance_base, YAW_BASE),
                  "head": (a_ion_lance_head, YAW_HEAD)},
    "flak_array": {"base": (a_flak_array_base, YAW_BASE),
                   "head": (a_flak_array_head, YAW_HEAD)},
    "anchor_damper": {"base": (a_anchor_damper_base, YAW_BASE),
                      "head": (a_anchor_damper_head, YAW_HEAD)},
    "mortar_emplacement": {"base": (a_mortar_emplacement_base, YAW_BASE),
                           "head": (a_mortar_emplacement_head, YAW_HEAD)},
    "restorer": {"base": (a_restorer_base, YAW_BASE),
                 "head": (a_restorer_head, YAW_HEAD)},
}


def _render_pair(sc, ng, stem, yaw_deg, out_dir):
    """Same rig render.py's own `render_pair()` uses — same `place_camera()`, same
    albedo(compositing off)/glow(compositing on) two-pass shape, same
    `_emission_strengths()` zero-then-restore dance — just keyed by an
    already-built filename stem instead of formatting `%s_y%03d` from an integer
    degree, because a 16-bucket yaw (22.5deg apart) isn't always a whole number.
    Duplicated rather than imported from render.py's private `_render_pair_impl`
    (which does not exist — see the module docstring on why nothing in render.py
    was touched to add one).
    """
    cam = R.place_camera(sc, yaw_deg)
    paths = {}
    sc.render.use_compositing = False
    saved = R._emission_strengths(0.0)
    sc.render.filepath = os.path.join(out_dir, "%s_albedo.png" % stem)
    bpy.ops.render.render(write_still=True)
    paths["albedo"] = sc.render.filepath
    for mname, v in saved.items():
        bpy.data.materials[mname].node_tree.nodes["Principled BSDF"] \
            .inputs["Emission Strength"].default_value = v
    sc.render.use_compositing = True
    sc.render.filepath = os.path.join(out_dir, "%s_glow.png" % stem)
    bpy.ops.render.render(write_still=True)
    paths["glow"] = sc.render.filepath
    bpy.data.objects.remove(cam, do_unlink=True)
    return paths


def render_split_asset(sc, ng, name, out_dir):
    """Render every part of a head/base-split emplacement, bucket-indexed.

    Same camera rig as render.py's own renders — same `place_camera()`, same
    `ORTHO_SCALE`, same `HEIGHT_BIAS`, same cell size (whatever render.py's own
    `set_cell()` last left it at) — so a base and its head composite with zero pixel
    offset by construction. Verified empirically, not just argued: a real alpha-over
    composite of `pulse_turret_base_b00` + `pulse_turret_head_b00` reproduces
    render.py's combined `pulse_turret_y045` silhouette bounding box exactly
    ((72,183)x(88,199) on both), with only the small shading deltas expected from two
    independently-lit EEVEE passes at the base/head seam (see this issue's report).

    Returns {"<name>_base": {"b00": {...}, ...}, "<name>_head": {"b00": {...}, ...}} —
    see the module docstring for why this is deliberately flat.
    """
    parts = SPLIT_ASSETS[name]
    out = {}
    for part_name, (fn, yaw_count) in parts.items():
        for o in [o for o in bpy.data.objects if o.type == "MESH"]:
            bpy.data.objects.remove(o, do_unlink=True)
        fn()
        slots = {}
        for i in range(yaw_count):
            yaw_deg = 45.0 + i * (360.0 / yaw_count)
            slot = bucket_slot(i)
            stem = "%s_%s_%s" % (name, part_name, slot)
            paths = _render_pair(sc, ng, stem, yaw_deg, out_dir)
            slots[slot] = {
                k: os.path.relpath(v, ROOT).replace("\\", "/") for k, v in paths.items()
            }
        out["%s_%s" % (name, part_name)] = slots
    return out


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--list" in argv:
        for k in SPLIT_ASSETS:
            print(k)
        return 0

    if "--cell" in argv:
        R.set_cell(int(argv[argv.index("--cell") + 1]))

    if "--split" not in argv:
        print("usage: render_split.py -- --split name1,name2,... | --list")
        return 2
    split_arg = argv[argv.index("--split") + 1]
    split_names = [n.strip() for n in split_arg.split(",") if n.strip()]
    bad = [n for n in split_names if n not in SPLIT_ASSETS]
    if bad:
        print("unknown split asset(s) — not in SPLIT_ASSETS: %s" % ", ".join(bad))
        return 2

    os.makedirs(OUT_DIR, exist_ok=True)
    R.wipe()
    sc, lights = R.setup_scene()
    ng = R.setup_compositor(sc)
    if not R.calibrate(sc):
        print("RENDER ABORTED — projection calibration failed")
        return 1

    new_sprites = {}
    for name in split_names:
        new_sprites.update(render_split_asset(sc, ng, name, OUT_DIR))
        parts_desc = ", ".join("%s@%d" % (p, yc) for p, (_, yc) in SPLIT_ASSETS[name].items())
        print("RENDERED %s (split: %s)" % (name, parts_desc))

    # Additive merge only: every key render_split_asset() returns is new
    # ("<name>_base"/"<name>_head"), so this can never touch the existing combined
    # "<name>" entry render.py writes, or any other asset already in the manifest.
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            doc = json.load(f)
    else:
        doc = {
            "elevation_deg": R.ELEVATION_DEG, "yaws": list(R.YAWS),
            "yaw_count": R.YAW_COUNT, "cell": R.CELL,
            "tile_px": [R.TILE_W, R.TILE_W // 2], "ortho_scale": R.ORTHO_SCALE,
            "pivot": [R.PIVOT[0], R.PIVOT[1]], "sprites": {}, "hashes": {},
            "blender_version": bpy.app.version_string,
        }
    doc.setdefault("sprites", {}).update(new_sprites)
    doc["blender_version"] = bpy.app.version_string
    with open(MANIFEST, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    print("MANIFEST %s (%d sprites, %d new from --split)"
          % (MANIFEST, len(doc["sprites"]), len(new_sprites)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
