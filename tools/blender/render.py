"""
Latticefall sprite pipeline. Runs inside Blender 5.2:

    /Applications/Blender.app/Contents/MacOS/Blender -b --python tools/blender/render.py -- [args]
    ... -- --list
    ... -- --only pulse_turret
    ... -- --assets pulse_turret,arc_node    # batch re-render, one Blender launch (PRC-13)
    ... -- --calibrate
    ... -- --calibrate --cell 512      # override the 256px default (ART-03/LF-102);
    ... -- --only pulse_turret --cell 512   # TILE_W and ORTHO_SCALE re-derive from it
    ... -- --print-hashes                   # content hashes only, no scene/render (PRC-13)
    ... -- --print-hashes --only pulse_turret

`--print-hashes` is `tools/blender/build.py`'s fast path: it never calls wipe()/
setup_scene()/calibrate(), so it never touches the tracked `_calibration.png` and costs
only Blender's own startup. See `compute_hashes()` for what goes into a hash and why it
uses ORTHO_SCALE_NOMINAL rather than the calibrated ORTHO_SCALE.

Everything the game draws is rendered here, from scripted geometry, through one camera
rig and one lighting rig. Consistency across forty assets is a property of the pipeline,
not of anyone's discipline.

Two passes per yaw (decision 007):
  albedo — compositing OFF, the lit sprite
  glow   — compositing ON, Emission pass through Glare/Bloom
Godot draws glow additively and modulates it by reactor bus load, so a brownout dims
every emissive element in the game. A baked glow cannot dim and bleeds past the alpha.

Verified Blender 5.2 facts, do not re-derive:
  - only BLENDER_EEVEE is registered
  - scene.node_tree is gone; use scene.compositing_node_group, ending in NodeGroupOutput
  - Glare settings are input sockets taking title-case strings ("Bloom", "High")
  - the emission pass socket is called "Emission"; enable view_layer.use_pass_emit
  - always wipe the scene: Blender's default startup light silently blows out renders
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import sys
import tempfile

import bpy
import mathutils

# ── projection (decision 017 — measured, not derived) ───────────────────────
ELEVATION_DEG = 30.0            # arcsin(0.5). A 1x1 tile lands on exactly 2:1.

# LF-108/ART-02: the Python-side source of truth for the yaw count. scripts/iso.gd carries
# its own independent YAW_COUNT (the GDScript-side source — the two processes don't share a
# constant across the Blender/Godot boundary), and sprites.gd refuses to load a manifest
# whose "yaw_count" disagrees with it. Before this, YAWS below was a bare literal tuple and
# nothing tied its length to anything the game asserted against — a game asking for a yaw
# count this file did not render, or a render producing yaws the game never asks for, was
# silent. YAWS is derived from this rather than typed out a second time.
YAW_COUNT = 4
YAWS = tuple(45 + round(i * 360.0 / YAW_COUNT) for i in range(YAW_COUNT))
CELL = 256                      # render canvas, px. Overridable with --cell (ART-03);
                                 # TILE_W and ORTHO_SCALE_NOMINAL are re-derived from it
                                 # by set_cell() before setup_scene()/calibrate() run.
TILE_W = CELL // 2              # a 1x1 world tile must measure this wide
# Nominal value from the geometry: a 1x1 tile's diagonal is sqrt(2) world units, and
# we want that to land on TILE_W pixels. Blender's orthographic-to-pixel mapping is
# very slightly tighter than this in practice (128 nominal measured 126), so the real
# value is solved by measurement at startup rather than trusted. See calibrate().
ORTHO_SCALE_NOMINAL = CELL * math.sqrt(2.0) / TILE_W
ORTHO_SCALE = ORTHO_SCALE_NOMINAL
SAMPLES = 64

# The camera sits this far above the tile plane so tall assets — the anchor ring, a
# turret barrel — do not clip the top of the cell. It moves world (0,0,0) *down* in the
# frame, which is why the pivot has to be measured rather than assumed to be the middle
# of the canvas. See _measure_tile.
HEIGHT_BIAS = 0.55
# Solved by calibrate(): the pixel that world (0,0,0) projects to, top-left origin.
PIVOT = (CELL // 2, CELL // 2)


def set_cell(cell: int) -> None:
    """Override the render canvas size and re-derive everything that scales with it.

    `--cell` (ART-03/LF-102) is how a non-256 cell size gets exercised at all — without
    this, TILE_W, ORTHO_SCALE_NOMINAL and PIVOT stay locked to the module's 256px
    defaults no matter what `sc.render.resolution_x/y` is set to, and calibrate() would
    be solving a projection for a canvas it isn't actually rendering. Must run before
    setup_scene() and calibrate().
    """
    global CELL, TILE_W, ORTHO_SCALE_NOMINAL, ORTHO_SCALE, PIVOT
    CELL = cell
    TILE_W = CELL // 2
    ORTHO_SCALE_NOMINAL = CELL * math.sqrt(2.0) / TILE_W
    ORTHO_SCALE = ORTHO_SCALE_NOMINAL
    PIVOT = (CELL // 2, CELL // 2)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "assets", "renders")
MANIFEST = os.path.join(OUT_DIR, "sprites.json")

# ── palette ────────────────────────────────────────────────────────────────
# Written in **sRGB**, the same space as the game's UI constants, and linearised by
# mat(). Do not put linear values here — see srgb() for why that was the bug.
VERDIGRIS = (0.16, 0.42, 0.35)
VERD_LIT = (0.24, 0.58, 0.48)
AMBER = (0.91, 0.58, 0.16)
STEEL = (0.115, 0.135, 0.150)
STEEL_LT = (0.215, 0.240, 0.255)
BONE = (0.395, 0.425, 0.415)
STONE = (0.105, 0.130, 0.145)
STONE_WARM = (0.185, 0.150, 0.095)
RUST = (0.235, 0.130, 0.070)


def _hex_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _load_fx() -> dict:
    """Per-emplacement glow colour, sourced from `data/towers.json`'s `fx` block rather
    than duplicated here. Every tower already carries a distinct `colour` (its projectile
    or field tint) and, for the five weapons, a `core` (a near-white hot tone for the
    muzzle) — the same authored palette the combat-FX layer draws tracers and beams in.
    Reusing it means an emplacement's own light and the shots it fires are always the
    same hue by construction, and it is what breaks the defect this pass exists to fix:
    five of the nine emplacements previously emitted the *identical* teal (VERD_LIT), so
    even once the geometry read as distinct shapes, colour gave the player nothing to
    tell them apart by. Keyed by asset name (`pulse_turret`), not tower id
    (`pulse-turret`), to match this file's ASSETS dict.
    """
    path = os.path.join(ROOT, "data", "towers.json")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    out = {}
    for t in doc["towers"]:
        fx = t.get("fx", {})
        out[t["id"].replace("-", "_")] = {
            "colour": _hex_rgb(fx["colour"]) if "colour" in fx else None,
            "core": _hex_rgb(fx["core"]) if "core" in fx else None,
        }
    return out


FX = _load_fx()


def glow_point(prefix, loc, colour, core=None, r_halo=0.075, r_core=0.032,
               s_halo=0.40, s_core=0.85, segs=14, rings=8):
    """One small emitter with a genuine hot centre and a dim surrounding halo, built into
    the geometry rather than left entirely to Bloom.

    This is the fix for LF's "lightbulb on a post" defect: the old rig was one large,
    uniformly-bright sphere (up to 0.24 world-radius — ~40px, the single biggest emitter
    in the library) fed into a Glare Bloom with `Size=7.0`, a blur radius on the order of
    half the 256px cell. Structure and falloff both came out flat because there was
    neither in the source. `glow_point` guarantees the falloff exists before Bloom ever
    touches it: a tiny bright `core` sphere (near-white on weapons, via `core=`) nested
    inside a larger, much dimmer `colour` halo. Bloom (now `Size=3.0`, see
    `setup_compositor`) then adds a soft spread on top of a shape that already has a hot
    centre and a real gradient, instead of being the only thing providing either.
    `core=None` (used by every instrument emplacement) collapses this to a single-tone
    point — steady light, not a muzzle flash, which is the visual difference between
    "shoots something" and "shoots nothing" this pass is also trying to restore.
    """
    sphere(r_halo, loc, segments=segs, rings=rings,
           material=mat(prefix + "h", colour, 0.0, 0.3, emit=colour, emit_strength=s_halo))
    sphere(r_core, loc, segments=max(8, segs - 4), rings=max(6, rings - 2),
           material=mat(prefix + "c", core or colour, 0.0, 0.2,
                        emit=core or colour, emit_strength=s_core))


def wipe() -> None:
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)
    for m in list(bpy.data.materials):
        bpy.data.materials.remove(m)


def srgb(*c):
    """sRGB -> scene-linear. Every colour in this file is authored in sRGB.

    Blender's colour inputs are scene-linear, and view_transform='Standard' encodes
    back to sRGB when the PNG is written. Probed on this machine: an emission of 0.5
    is stored as 188/255, i.e. sRGB 0.733 — so a value written as though it were a
    display colour renders roughly three times too light.

    That is what made the board read as a light grey slab against a dark UI (LF-023):
    STONE was written as 0.105 to sit alongside the game's own 0.09 tile constant, but
    landed on #444c4e instead of #162126. The same mistake desaturated every emitter —
    an eye of (1.0, 0.22, 0.12) at strength 1.3 clipped red and published as pale
    white-orange rather than red (LF-020, LF-022).
    """
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


def mat(name, rgb, metal=0.0, rough=0.55, emit=None, emit_strength=1.2):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*srgb(*rgb), 1.0)
    b.inputs["Metallic"].default_value = metal
    b.inputs["Roughness"].default_value = rough
    if emit:
        b.inputs["Emission Color"].default_value = (*srgb(*emit), 1.0)
        b.inputs["Emission Strength"].default_value = emit_strength
    return m


def put(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)
    return obj


def cube(size, loc, scale=(1, 1, 1), rot_z=0.0, material=None):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    o = bpy.context.active_object
    o.scale = scale
    o.rotation_euler = (0, 0, rot_z)
    return put(o, material) if material else o


def cyl(verts, radius, depth, loc, rot=(0, 0, 0), material=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius, depth=depth,
                                        location=loc, rotation=rot)
    o = bpy.context.active_object
    return put(o, material) if material else o


def cone(verts, radius1, radius2, depth, loc, rot=(0, 0, 0), material=None):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=radius1, radius2=radius2,
                                    depth=depth, location=loc, rotation=rot)
    o = bpy.context.active_object
    return put(o, material) if material else o


def axis_offset(rot, dz, base=(0.0, 0.0, 0.0)):
    """World-space point at signed distance `dz` along local +Z after Euler `rot`,
    starting from `base` — for stacking two primitives on one shared, rotated
    centerline (LF-049) instead of each rotating independently about its own origin.

    `primitive_cone_add(location=loc, rotation=rot)` bakes `rot` onto the new object
    about its OWN origin at `loc` — it never rotates `loc` itself. Two cones built with
    the same `rot` but `loc=(0, 0, z1)` and `loc=(0, 0, z2)` therefore sit on PARALLEL
    tilted axes offset from each other by roughly `(z2 - z1) * tan(angle)`, not on one
    shared axis — the lit cone pokes out to one side and hides behind the dark cone from
    the opposite camera yaw. Passing `axis_offset(rot, z)` as `loc` instead makes both
    centers scalar multiples of the same rotated direction vector from `base`, which is
    colinear by construction.

    Confirmed against the installed Blender 5.2: `primitive_cone_add`'s `rotation=`
    argument sets `object.rotation_euler` directly (mode 'XYZ', Blender's default), which
    is exactly what `mathutils.Euler(rot, 'XYZ').to_matrix()` reproduces — this is not
    assumed from memory.
    """
    return tuple(mathutils.Vector(base) + mathutils.Euler(rot, 'XYZ').to_matrix()
                 @ mathutils.Vector((0.0, 0.0, dz)))


def sphere(radius, loc, segments=20, rings=12, material=None):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=loc,
                                         segments=segments, ring_count=rings)
    o = bpy.context.active_object
    return put(o, material) if material else o


def torus(major, minor, loc, rot=(0, 0, 0), material=None):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor,
                                     location=loc, rotation=rot,
                                     major_segments=48, minor_segments=12)
    o = bpy.context.active_object
    return put(o, material) if material else o


# ── assets ─────────────────────────────────────────────────────────────────
# Each returns nothing; it just builds geometry around the origin. The tile centre
# is world (0,0,0), which is why every sprite shares one pivot in the manifest.

def a_tile_ground():
    cube(1.0, (0, 0, -0.06), scale=(1, 1, 0.12), material=mat("g", STONE, 0.0, 0.85))
    for i, (x, y) in enumerate([(-0.3, 0.24), (0.28, -0.18), (0.1, 0.34)]):
        cube(0.1, (x, y, 0.01), scale=(1, 1, 0.2 + i * 0.1),
             material=mat("gd%d" % i, (0.088, 0.108, 0.120), 0.0, 0.9))


def a_tile_path():
    cube(1.0, (0, 0, -0.06), scale=(1, 1, 0.12), material=mat("p", STONE_WARM, 0.0, 0.9))
    for i, (x, y) in enumerate([(-0.22, -0.22), (0.24, 0.12), (0.0, 0.3)]):
        cube(0.13, (x, y, 0.0), scale=(1, 1, 0.14),
             material=mat("pd%d" % i, RUST, 0.05, 0.95))


def a_tile_slot():
    cube(1.0, (0, 0, -0.06), scale=(1, 1, 0.12), material=mat("s", STONE, 0.0, 0.8))
    torus(0.34, 0.035, (0, 0, 0.02), material=mat("sr", VERD_LIT, 0.4, 0.35,
                                                  emit=(0.20, 0.62, 0.50), emit_strength=1.4))
    for i in range(4):
        a = math.radians(i * 90 + 45)
        cube(0.1, (math.cos(a) * 0.42, math.sin(a) * 0.42, 0.02), scale=(1, 1, 0.5),
             rot_z=a, material=mat("sc%d" % i, STEEL_LT, 0.6, 0.4))


def a_anchor_ring():
    # deliberately larger than one tile: it is the objective, and it should read
    # as architecture rather than as another emplacement.
    cyl(8, 0.95, 0.20, (0, 0, 0.1), material=mat("ab", STONE, 0.05, 0.8))
    torus(0.70, 0.095, (0, 0, 0.86), rot=(math.radians(90), 0, 0),
          material=mat("ar", VERDIGRIS, 0.85, 0.28))
    torus(0.53, 0.035, (0, 0, 0.86), rot=(math.radians(90), 0, 0),
          material=mat("ag", VERD_LIT, 0.0, 0.4, emit=(0.30, 0.90, 0.74), emit_strength=1.3))
    for i in range(6):                      # six wards, per the nomenclature bible
        a = math.radians(i * 60 + 15)
        cube(0.12, (math.cos(a) * 0.78, math.sin(a) * 0.78, 0.86),
             scale=(1.0, 1.5, 1.0), rot_z=a,
             material=mat("aw%d" % i, VERD_LIT, 0.9, 0.22,
                          emit=(0.16, 0.46, 0.38), emit_strength=1.2))
    for s in (-1, 1):
        cube(0.20, (s * 0.66, 0, 0.42), scale=(1, 1, 2.4),
             material=mat("ap%d" % s, STEEL, 0.6, 0.45))


def a_pulse_turret():
    cyl(6, 0.62, 0.26, (0, 0, 0.13), material=mat("tb", STEEL, 0.45, 0.55))
    cyl(6, 0.40, 0.36, (0, 0, 0.42), material=mat("th", BONE, 0.30, 0.45))
    cyl(16, 0.085, 0.9, (0.0, 0.30, 0.56), rot=(math.radians(76), 0, 0),
        material=mat("tr", STEEL, 0.9, 0.25))
    fx = FX["pulse_turret"]
    glow_point("tt", (0.0, 0.63, 0.70), fx["colour"], core=fx["core"])
    # A thin armed-status band around the head — the turret should read as machinery
    # with a light on it, not as a light with a barrel attached.
    torus(0.405, 0.014, (0, 0, 0.42),
          material=mat("tband", fx["colour"], 0.0, 0.3, emit=fx["colour"], emit_strength=0.30))
    for s in (-1, 1):
        cube(0.13, (s * 0.34, -0.1, 0.5), scale=(0.5, 1.5, 1.7),
             material=mat("tf%d" % s, STEEL_LT, 0.7, 0.35))


def a_arc_node():
    cyl(8, 0.55, 0.2, (0, 0, 0.1), material=mat("nb", STEEL, 0.5, 0.5))
    posts = []
    for i in range(3):
        a = math.radians(i * 120)
        posts.append(a)
        cube(0.1, (math.cos(a) * 0.34, math.sin(a) * 0.34, 0.45), scale=(1, 1, 3.2),
             rot_z=a, material=mat("np%d" % i, STEEL_LT, 0.75, 0.3))
    fx = FX["arc_node"]
    # A small hub spark, not a ball on a stick. The old single 0.24-radius sphere here
    # was the single biggest emitter in the whole library — the exact source of the
    # "lightbulb on a post" defect this pass exists to fix. Replaced with a small hub
    # plus a tiny contact light at each post tip: "three prongs feeding a spark cluster",
    # the shape the unit's own name describes, rather than a dot floating above
    # unrelated machinery.
    glow_point("nc", (0, 0, 0.66), fx["colour"], core=fx["core"], r_halo=0.065, r_core=0.028,
               s_halo=0.35, s_core=0.80)
    for i, a in enumerate(posts):
        sphere(0.028, (math.cos(a) * 0.34, math.sin(a) * 0.34, 0.62), segments=10, rings=6,
               material=mat("ncp%d" % i, fx["colour"], 0.0, 0.25,
                            emit=fx["colour"], emit_strength=0.30))


def a_scan_relay():
    # Instrumentation, not a weapon: no barrel anywhere on it, and the only lit element
    # is the dish face. Tall and thin so it cannot be read as a turret at 100% zoom —
    # the player has to know at a glance which of their emplacements is the one that
    # sees rather than shoots, because that is the whole decision on anchor-02.
    cyl(6, 0.44, 0.16, (0, 0, 0.08), material=mat("rb", STEEL, 0.45, 0.55))
    cyl(8, 0.07, 1.00, (0, 0, 0.60), material=mat("rm", STEEL_LT, 0.80, 0.30))
    for s in (-1, 1):                                   # guy struts, so it reads braced
        cube(0.05, (s * 0.22, 0, 0.34), scale=(1.0, 1.0, 5.2), rot_z=math.radians(28),
             material=mat("rs%d" % s, STEEL, 0.6, 0.5))
    cone(20, 0.34, 0.05, 0.22, (0, 0.10, 1.10), rot=(math.radians(58), 0, 0),
         material=mat("rd", STEEL_LT, 0.7, 0.35))
    fx = FX["scan_relay"]
    cone(20, 0.29, 0.04, 0.04, (0, 0.155, 1.13), rot=(math.radians(58), 0, 0),
         material=mat("rf", fx["colour"], 0.0, 0.35, emit=fx["colour"], emit_strength=0.55))
    # A small steady tip light, the kind a real mast carries. Instrument, not weapon, so
    # it stays single-tone rather than getting a hot core the way the five guns do.
    sphere(0.032, (0, 0.02, 1.14), segments=10, rings=6,
           material=mat("rt", fx["colour"], 0.0, 0.3, emit=fx["colour"], emit_strength=0.35))


def a_shield_wall():
    # Broad and low — the opposite silhouette to the relay's mast. It is the biggest
    # draw in Act I, so it should look like plant: two pylons and a screen, no optics.
    cube(1.0, (0, 0, 0.07), scale=(0.86, 0.34, 0.14), material=mat("wb", STONE, 0.2, 0.8))
    for s in (-1, 1):
        cube(0.26, (s * 0.36, 0, 0.40), scale=(1.0, 1.0, 2.6),
             material=mat("wp%d" % s, STEEL, 0.55, 0.45))
        cyl(6, 0.10, 0.14, (s * 0.36, 0, 0.75), material=mat("wc%d" % s, STEEL_LT, 0.8, 0.3))
    # The field itself: three thin bands rather than one solid slab, so it reads as an
    # energy screen with structure — a grid, not a lit rectangle — and dim enough that
    # the wall, the biggest draw in Act I, does not out-glow the units it slows.
    fx = FX["shield_wall"]
    for i, z in enumerate((0.30, 0.46, 0.62)):
        cube(1.0, (0, 0, z), scale=(0.60 - i * 0.03, 0.025, 0.05),
             material=mat("ws%d" % i, fx["colour"], 0.1, 0.25,
                          emit=fx["colour"], emit_strength=0.42))
    for s in (-1, 1):
        cube(0.07, (s * 0.36, 0, 0.14), scale=(1.0, 2.4, 1.0),
             material=mat("wf%d" % s, STEEL_LT, 0.7, 0.4))


def a_ion_lance():
    # One long barrel on a heavy carriage. The silhouette is the barrel — it is the
    # only emplacement in the game whose outline is mostly diagonal.
    cyl(8, 0.58, 0.20, (0, 0, 0.10), material=mat("lb", STEEL, 0.5, 0.5))
    cube(0.52, (0, -0.06, 0.34), scale=(1.0, 0.9, 0.8), material=mat("lh", STONE, 0.35, 0.6))
    cyl(14, 0.115, 1.34, (0.0, 0.34, 0.72), rot=(math.radians(68), 0, 0),
        material=mat("lr", STEEL_LT, 0.9, 0.22))
    fx = FX["ion_lance"]
    # The breech collar carries a dim charge glow of its own — a tube reads as ordnance
    # rather than plumbing once it has two lit points along its length instead of one at
    # the tip.
    cyl(14, 0.16, 0.20, (0.0, 0.06, 0.44), rot=(math.radians(68), 0, 0),
        material=mat("lc", fx["colour"], 0.0, 0.3, emit=fx["colour"], emit_strength=0.30))
    for s in (-1, 1):                                   # recoil rails along the barrel
        cube(0.06, (s * 0.17, 0.24, 0.60), scale=(1.0, 1.0, 7.0),
             rot_z=0.0, material=mat("lg%d" % s, STEEL, 0.7, 0.35))
    glow_point("lm", (0.0, 0.80, 1.02), fx["colour"], core=fx["core"],
               r_halo=0.075, r_core=0.032, s_halo=0.40, s_core=0.85)


def a_warden_drone():
    sphere(0.26, (0, 0, 0.42), material=mat("dc", STEEL_LT, 0.55, 0.4))
    for i in range(3):
        a = math.radians(i * 120 + 30)
        cube(0.12, (math.cos(a) * 0.3, math.sin(a) * 0.3, 0.26), scale=(1.6, 0.5, 0.9),
             rot_z=a, material=mat("dl%d" % i, STEEL, 0.6, 0.45))
    # The eye must clear the hull. At y=0.20 its centre sat 0.204 from the body centre
    # against a body radius of 0.26, so all but a sliver was inside the sphere and it
    # rendered at yaw 135/225 only — and the game draws every sprite at yaw 45, so
    # in-game drones had no eye and an entirely black glow pass. Seated at 0.245 it
    # protrudes about half its radius and reads from all four yaws.
    sphere(0.075, (0, 0.245, 0.455), segments=12, rings=8,
           material=mat("de", (0.85, 0.25, 0.18), 0.0, 0.3,
                        emit=(1.0, 0.22, 0.12), emit_strength=1.3))


def a_warden_heavy():
    # Silhouette pass (LF-021): at 100% zoom the old shape was a dark box with a red dot
    # and could not be told from a drone at a glance. It is the armoured unit, so it now
    # reads as *wide and low* — tracks that stand proud of the hull, shoulder plates in
    # lit steel, and the hull itself stepped rather than a single cube.
    cube(0.66, (0, 0, 0.34), scale=(1.15, 0.9, 0.7), material=mat("hb", STEEL, 0.5, 0.55))
    cube(0.54, (0, -0.02, 0.62), scale=(1.0, 0.8, 0.45), material=mat("hb2", STEEL, 0.55, 0.5))
    cube(0.46, (0, 0, 0.84), scale=(0.85, 0.7, 0.30), material=mat("hh", STEEL_LT, 0.65, 0.4))
    for s in (-1, 1):
        # track units, wider than the hull so the outline is unmistakably broad
        cube(0.22, (s * 0.44, 0, 0.17), scale=(0.9, 2.6, 1.3),
             material=mat("hg%d" % s, STONE, 0.4, 0.7))
        for i in range(3):                              # road wheels, for a read at zoom
            cyl(8, 0.075, 0.09, (s * 0.50, -0.22 + i * 0.22, 0.13),
                rot=(0, math.radians(90), 0),
                material=mat("hw%d%d" % (s, i), STEEL_LT, 0.7, 0.4))
        cube(0.20, (s * 0.30, -0.04, 0.72), scale=(1.0, 1.5, 0.5),
             material=mat("hs%d" % s, STEEL_LT, 0.7, 0.35))
    sphere(0.095, (0, 0.30, 0.86), segments=12, rings=8,
           material=mat("he", (0.9, 0.3, 0.15), 0.0, 0.3,
                        emit=(1.0, 0.28, 0.12), emit_strength=1.3))


def a_warden_hauler():
    # Act I's mid-weight, and the entire reason it exists is that it is NOT armoured —
    # 110 HP against the heavy's 220, with no armour rating at all. So it must never read
    # as plated. The heavy already owns "plated", and a player who mistakes the two brings
    # the wrong weapon to it, which in this game is a real and expensive mistake. The
    # difference has to be carried by silhouette and mass, never by colour: it is the same
    # family of machine and wears the same steel and the same red eye as its siblings.
    #
    # Three things do that work, and none of them exists on the heavy.
    #   * The frame is OPEN — two sills and five cross ties with daylight between them,
    #     where the heavy's hull is a closed stepped box. Armour is a solid outline; a
    #     freight chassis is a broken one.
    #   * The load rides on top, exposed, in STONE_WARM — the crate/pallet tone the
    #     mortar's shell rack and the bulwark's tanks already use, deliberately NOT the
    #     steel the machine itself is built from. At zoom the mass on its back therefore
    #     reads as something carried rather than something worn.
    #   * It stands on eight short stub legs with pads, not tracks. Tracks are the heavy's
    #     and they draw a continuous skirt; the legs draw a comb of gaps.
    #
    # Mass sits between the siblings by construction — long and low rather than tall and
    # solid — so the three Act I ground units separate on outline before anything else.
    # Measured off the committed renders at yaw 45 (bbox / lit px):
    #   drone 65x56 / 2311   ·   hauler 128x90 / 4551   ·   heavy 110x113 / 7883
    for s in (-1, 1):                                   # sills: the spine, and the reason
        cube(1.0, (s * 0.185, 0.02, 0.25), scale=(0.09, 1.32, 0.09),   # it is long, not tall
             material=mat("uhs%d" % s, STEEL, 0.5, 0.55))
    for i, y in enumerate((-0.56, -0.28, 0.0, 0.28, 0.56)):
        cube(1.0, (0, y, 0.25), scale=(0.44, 0.06, 0.065),             # open deck, not a hull
             material=mat("uht%d" % i, STEEL_LT, 0.6, 0.45))
    for s in (-1, 1):
        for i, y in enumerate((-0.52, -0.18, 0.18, 0.52)):
            cube(0.13, (s * 0.235, y, 0.115), scale=(0.55, 0.55, 1.75),
                 material=mat("uhl%d%d" % (s, i), STEEL, 0.55, 0.5))
            cyl(6, 0.05, 0.045, (s * 0.235, y, 0.025),
                material=mat("uhp%d%d" % (s, i), STONE, 0.3, 0.75))
    # Crated korrite, stepping down toward the front so the sprite says which end leads.
    # Narrower in X than the sills carry it and spaced ~0.12 apart in Y — both on purpose.
    # The first cut had the crates flush to the rails and butted end to end, and it read
    # as one solid slab with strap lines on it, i.e. exactly the plated hull this unit
    # must not be. Daylight outboard of the load and between its pieces is what makes the
    # frame underneath visible at all, and the frame is the argument.
    for i, (y, sy, sz, z) in enumerate(((-0.42, 0.26, 0.28, 0.42),
                                        (-0.05, 0.24, 0.24, 0.40),
                                        (0.30, 0.20, 0.20, 0.38))):
        cube(1.0, (0, y, z), scale=(0.31 - i * 0.01, sy, sz),
             material=mat("uhc%d" % i, STONE_WARM, 0.2, 0.8))
        cube(1.0, (0, y, z + sz * 0.5 + 0.006), scale=(0.34 - i * 0.01, sy * 0.22, 0.020),
             material=mat("uhb%d" % i, BONE, 0.6, 0.4))                # strap over the load
    cube(0.16, (0, -0.69, 0.27), scale=(0.9, 0.85, 0.75),              # rear hitch
         material=mat("uhr", STONE, 0.35, 0.7))
    # Forward cowl, low and squat — the machine looks down its own route rather than over
    # a turret ring. Nothing sits above or ahead of the eye: at (0.72, 0.54) it clears the
    # cowl's top by 0.10 and its nose by 0.07, which puts the whole sphere outside the
    # cowl's silhouette at yaw 45 — the yaw that looks at this unit's *back*, and so the
    # only one where the eye is the far side of anything. That is the check the drone's
    # buried eye cost this project a render cycle to learn to do (see a_warden_drone).
    cube(0.26, (0, 0.53, 0.32), scale=(1.35, 0.95, 0.90),
         material=mat("uhh", STEEL_LT, 0.6, 0.45))
    cube(0.08, (0, 0.66, 0.46), scale=(1.0, 1.0, 0.9),                 # neck under the eye
         material=mat("uhn", STEEL, 0.6, 0.4))
    sphere(0.085, (0, 0.72, 0.54), segments=12, rings=8,
           material=mat("uhe", (0.9, 0.3, 0.15), 0.0, 0.3,
                        emit=(1.0, 0.28, 0.12), emit_strength=1.3))
    # Running lights along the rail, same hue as the eye and far dimmer. They are what
    # makes the *glow* pass carry the silhouette too: at gameplay distance the heavy is
    # one red dot and the hauler is a short row of them, so the two stay apart even when
    # the albedo is a dark shape on a dark deck.
    for s in (-1, 1):
        for i, y in enumerate((-0.33, 0.33)):
            sphere(0.030, (s * 0.25, y, 0.31), segments=8, rings=6,
                   material=mat("uhm%d%d" % (s, i), (0.9, 0.3, 0.15), 0.0, 0.3,
                                emit=(1.0, 0.28, 0.12), emit_strength=0.40))


def a_warden_mote():
    # Silhouette pass (LF-021). A lone glowing sphere reads as a light source rather than
    # a unit, and at 100% zoom it was indistinguishable from a slot ring. Two crossed
    # gimbals give it an outline that survives being small and bright.
    sphere(0.15, (0, 0, 0.72), segments=16, rings=10,
           material=mat("mc", VERD_LIT, 0.3, 0.35,
                        emit=(0.4, 1.0, 0.85), emit_strength=1.1))
    torus(0.29, 0.030, (0, 0, 0.72), rot=(math.radians(70), 0, 0),
          material=mat("mr", BONE, 0.8, 0.3))
    torus(0.24, 0.026, (0, 0, 0.72), rot=(math.radians(70), math.radians(74), 0),
          material=mat("mr2", STEEL_LT, 0.85, 0.3))
    for s in (-1, 1):                                   # stub vanes, so it has corners
        cube(0.07, (s * 0.30, 0, 0.72), scale=(1.4, 0.5, 0.5), rot_z=math.radians(s * 20),
             material=mat("mv%d" % s, BONE, 0.75, 0.35))


def a_flak_array():
    # Four short barrels on a wide yoke. Read against the pulse turret's single long
    # barrel and the lance's diagonal: the flak array is the *widest* weapon silhouette
    # in the game, because it is the one that covers a lane rather than a point.
    cyl(8, 0.60, 0.22, (0, 0, 0.11), material=mat("kb", STEEL, 0.5, 0.5))
    cube(0.46, (0, -0.04, 0.36), scale=(1.5, 0.9, 0.7), material=mat("kh", STEEL_LT, 0.6, 0.4))
    fx = FX["flak_array"]
    for i, s in enumerate((-1, 1)):
        for j, z in enumerate((0.46, 0.62)):
            cyl(10, 0.055, 0.68, (s * 0.20, 0.26, z), rot=(math.radians(74), 0, 0),
                material=mat("kr%d%d" % (i, j), STEEL_LT, 0.9, 0.25))
            # A tiny live tip on every barrel instead of one ball balanced between all
            # four — this is the widest weapon silhouette in the game and should read as
            # four points of light across an arc, not one point in the middle of them.
            tip = (s * 0.20, 0.26 + 0.34 * math.sin(math.radians(74)),
                   z + 0.34 * math.cos(math.radians(74)))
            sphere(0.024, tip, segments=8, rings=6,
                   material=mat("kt%d%d" % (i, j), fx["core"] or fx["colour"], 0.0, 0.2,
                                emit=fx["core"] or fx["colour"], emit_strength=0.62))
    for s in (-1, 1):                                   # ammo cans, so it reads crewed
        cube(0.22, (s * 0.46, -0.18, 0.28), scale=(1.0, 1.4, 0.9),
             material=mat("kc%d" % s, STONE_WARM, 0.2, 0.75))


def a_anchor_damper():
    # Plant, not a gun: a squat drum wrapped in coils with a field ring floating over it.
    # No barrel anywhere, and the only bright element is the ring — the player has to be
    # able to tell at a glance which emplacement is spending power to deny power.
    cyl(12, 0.52, 0.30, (0, 0, 0.15), material=mat("db", STONE, 0.1, 0.8))
    cyl(12, 0.44, 0.34, (0, 0, 0.46), material=mat("dd", STEEL, 0.55, 0.45))
    for i in range(3):                                  # coil bands
        cyl(24, 0.47, 0.045, (0, 0, 0.34 + i * 0.14),
            material=mat("dc%d" % i, BONE, 0.75, 0.3))
    for i in range(3):                                  # standoffs holding the ring up
        a = math.radians(i * 120)
        cube(0.06, (math.cos(a) * 0.30, math.sin(a) * 0.30, 0.74), scale=(1, 1, 3.4),
             rot_z=a, material=mat("ds%d" % i, STEEL_LT, 0.7, 0.35))
    # Violet, not the teal the arc node/lance/relay/wall all shared before this pass —
    # the damper suppresses drain rather than doing anything electrical or optical, and
    # needed a hue nothing else in the library uses. Small and dim on purpose: at major
    # 0.40 / strength 1.5 the field ring was brighter and wider than the anchor ring's
    # own wards, and a board with four dampers on it read as four objectives.
    fx = FX["anchor_damper"]
    torus(0.28, 0.032, (0, 0, 0.94),
          material=mat("dr", fx["colour"], 0.2, 0.3, emit=fx["colour"], emit_strength=0.45))


def a_mortar_emplacement():
    # Short fat tube at a steep angle on a broad plate. The lance is a long shallow
    # diagonal; the mortar is a stubby steep one, so the two do not read alike at zoom.
    cube(1.0, (0, 0, 0.06), scale=(0.78, 0.78, 0.12), material=mat("mb", STONE, 0.15, 0.8))
    cyl(8, 0.40, 0.24, (0, -0.06, 0.24), material=mat("mm", STEEL, 0.5, 0.5))
    cyl(14, 0.20, 0.86, (0.0, 0.16, 0.62), rot=(math.radians(28), 0, 0),
        material=mat("mt", STEEL_LT, 0.85, 0.3))
    fx = FX["mortar_emplacement"]
    # Same pattern as the ion lance's collar: a dim charge glow at the breech, so the
    # tube reads as ordnance with two lit points rather than plumbing with one.
    cyl(14, 0.24, 0.12, (0.0, 0.02, 0.30), rot=(math.radians(28), 0, 0),
        material=mat("mc", fx["colour"], 0.0, 0.3, emit=fx["colour"], emit_strength=0.28))
    for s in (-1, 1):                                   # recoil spades
        cube(0.10, (s * 0.40, -0.28, 0.16), scale=(1.0, 2.2, 1.2),
             material=mat("mp%d" % s, BONE, 0.4, 0.6))
    for s in (-1, 1):                                   # shell rack
        cube(0.12, (s * 0.30, -0.40, 0.24), scale=(1.0, 1.0, 2.0),
             material=mat("mr%d" % s, STONE_WARM, 0.2, 0.7))
    glow_point("mg", (0.0, 0.44, 0.86), fx["colour"], core=fx["core"],
               r_halo=0.062, r_core=0.026, s_halo=0.35, s_core=0.78)


# Sable Reach units. Human contractors, so the language is plate, scaffolding and
# floodlight — cold blue-white lamps rather than the wardens' red eye or the Ordinal's
# verdigris. Faction should be readable from the emissive colour alone at 100% zoom.
REACH_LAMP = (0.62, 0.80, 0.98)
REACH_PLATE = (0.180, 0.165, 0.140)


def a_reach_picket():
    # The unit with no tap. Everything else Sable Reach sends carries a module on its back
    # and the module is the silhouette; this one is the same contractor with nothing on it,
    # so it reads as the cheap version of the sapper rather than as a different thing.
    # Narrow, upright and a head shorter, with one lamp instead of three.
    cube(0.34, (0, 0, 0.48), scale=(0.75, 0.6, 1.25),
         material=mat("rpb", REACH_PLATE, 0.35, 0.6))
    cube(0.22, (0, 0.02, 0.72), scale=(0.8, 0.7, 0.6),           # hood
         material=mat("rph", STEEL_LT, 0.55, 0.45))
    for s in (-1, 1):
        cube(0.09, (s * 0.15, 0.0, 0.18), scale=(1.0, 1.0, 2.6),
             material=mat("rpl%d" % s, STEEL, 0.5, 0.55))
    cyl(6, 0.035, 0.34, (0.20, -0.06, 0.52), rot=(0, math.radians(14), 0),
        material=mat("rpr", BONE, 0.7, 0.35))                    # slung bar, not a weapon
    sphere(0.055, (0, 0.20, 0.76), segments=12, rings=8,
           material=mat("rpm", REACH_LAMP, 0.0, 0.3,
                        emit=(0.55, 0.78, 1.0), emit_strength=1.2))


def a_reach_sapper():
    # Light, hunched, and carrying the tap on its back — the drain is the silhouette.
    # Scaled up from the first cut: at 0.34 body it was a smudge next to a warden drone
    # and the tap module, which is the whole point of the unit, did not read at all.
    cube(0.44, (0, -0.02, 0.46), scale=(0.9, 0.7, 1.1),
         material=mat("ssb", REACH_PLATE, 0.35, 0.6))
    cube(0.36, (0, -0.28, 0.64), scale=(0.85, 0.6, 1.35),        # the tap module
         material=mat("sst", STEEL_LT, 0.6, 0.4))
    cyl(8, 0.055, 0.40, (0, -0.36, 0.92), material=mat("ssa", BONE, 0.7, 0.3))
    sphere(0.07, (0, -0.36, 1.14), segments=12, rings=8,
           material=mat("ssl", REACH_LAMP, 0.0, 0.3,
                        emit=(0.55, 0.78, 1.0), emit_strength=1.4))
    for s in (-1, 1):
        cube(0.12, (s * 0.20, 0.02, 0.16), scale=(1.0, 1.0, 2.4),
             material=mat("ssl%d" % s, STEEL, 0.5, 0.55))
    sphere(0.06, (0, 0.24, 0.60), segments=12, rings=8,
           material=mat("ssv", REACH_LAMP, 0.0, 0.35,
                        emit=(0.50, 0.72, 0.96), emit_strength=1.0))


def a_reach_breacher():
    # A shielded man: the screen is a flat panel carried in front, and it is the only
    # emissive surface. Wider than the sapper, half the height of the bulwark.
    cube(0.40, (0, -0.06, 0.44), scale=(1.0, 0.8, 1.15),
         material=mat("sbb", REACH_PLATE, 0.4, 0.55))
    cube(0.52, (0, 0.26, 0.50), scale=(1.0, 0.06, 1.25),          # the screen
         material=mat("sbs", REACH_LAMP, 0.1, 0.25,
                      emit=(0.42, 0.66, 0.94), emit_strength=1.2))
    for s in (-1, 1):
        cube(0.09, (s * 0.20, -0.06, 0.16), scale=(1.0, 1.0, 2.2),
             material=mat("sbl%d" % s, STEEL, 0.5, 0.55))
    cube(0.16, (0, -0.24, 0.66), scale=(1.0, 0.9, 0.8),
         material=mat("sbp", STEEL_LT, 0.65, 0.4))
    sphere(0.05, (0, -0.24, 0.82), segments=12, rings=8,
           material=mat("sbl", REACH_LAMP, 0.0, 0.3,
                        emit=(0.55, 0.78, 1.0), emit_strength=1.1))


def a_reach_skiff():
    # The only Reach unit off the ground: a flat hull on two ducted fans, lamps
    # underneath. Reads as a vehicle, not a drone — the wardens have the drones.
    # It flies at the warden mote's altitude, not just above the deck: at z 0.78 it sat
    # level with the ground units and nothing about it said "air".
    cube(0.70, (0, 0, 1.12), scale=(1.0, 0.55, 0.20),
         material=mat("skh", REACH_PLATE, 0.45, 0.5))
    cube(0.34, (0, 0.16, 1.24), scale=(0.9, 0.7, 0.5),
         material=mat("skc", STEEL_LT, 0.6, 0.4))
    for s in (-1, 1):
        cyl(14, 0.22, 0.11, (s * 0.36, -0.06, 1.02),
            material=mat("skd%d" % s, STEEL, 0.7, 0.35))
        torus(0.22, 0.030, (s * 0.36, -0.06, 1.02),
              material=mat("skr%d" % s, BONE, 0.75, 0.3))
        sphere(0.055, (s * 0.36, -0.06, 0.90), segments=12, rings=8,
               material=mat("skl%d" % s, REACH_LAMP, 0.0, 0.3,
                            emit=(0.52, 0.76, 1.0), emit_strength=1.3))
    cube(0.10, (0, 0.40, 1.14), scale=(1.0, 1.0, 0.6),
         material=mat("skn", REACH_LAMP, 0.0, 0.3,
                      emit=(0.46, 0.70, 0.98), emit_strength=1.0))


def a_reach_bulwark():
    # Plate on legs. Broad, low and front-heavy: the slab is most of the silhouette,
    # because on this unit the plate is the mechanic.
    cube(0.56, (0, -0.10, 0.46), scale=(1.1, 0.85, 1.05),
         material=mat("bwb", REACH_PLATE, 0.45, 0.5))
    # The slab is lit steel, not STEEL: at the dark value it was a black rectangle with
    # no readable edge against the deck, and the unit's defining feature disappeared.
    cube(0.86, (0, 0.30, 0.52), scale=(1.0, 0.10, 1.15),          # the slab
         material=mat("bwp", BONE, 0.65, 0.4))
    cube(0.70, (0, 0.36, 0.52), scale=(1.0, 0.03, 0.95),          # screen over the slab
         material=mat("bws", REACH_LAMP, 0.1, 0.25,
                      emit=(0.38, 0.60, 0.90), emit_strength=1.1))
    for s in (-1, 1):
        cube(0.16, (s * 0.30, -0.14, 0.18), scale=(1.0, 1.0, 2.0),
             material=mat("bwl%d" % s, STEEL, 0.55, 0.5))
        cube(0.12, (s * 0.34, -0.30, 0.62), scale=(1.0, 1.2, 1.4),
             material=mat("bwt%d" % s, STONE_WARM, 0.3, 0.7))
    sphere(0.06, (0, -0.34, 0.86), segments=12, rings=8,
           material=mat("bwe", REACH_LAMP, 0.0, 0.3,
                        emit=(0.55, 0.78, 1.0), emit_strength=1.2))


def a_restorer():
    # Meridian plant at its least elegant: a cabinet, a stack of fins, and a core that
    # is the brightest warm element on any board. It gives capacity back, so it should
    # look like the thing everything else is plugged into.
    cube(1.0, (0, 0, 0.07), scale=(0.66, 0.66, 0.14), material=mat("rb", STONE, 0.15, 0.8))
    cube(0.62, (0, 0, 0.48), scale=(0.9, 0.75, 1.15), material=mat("rc", STEEL, 0.5, 0.5))
    for i in range(5):                                  # cooling fins
        cube(0.60, (0, -0.24, 0.26 + i * 0.13), scale=(1.0, 0.30, 0.06),
             material=mat("rf%d" % i, STEEL_LT, 0.8, 0.3))
    # The core stands *proud of* the cabinet. At y=0.16 it was inside it and the
    # brightest element in the asset rendered as nothing at all from three yaws.
    # Mint, not amber — restoring capacity is the one unambiguously positive act in the
    # kit, and amber collided with every weapon's muzzle glow. Dimmer than before, per
    # this pass's brief that no emplacement should outshine a unit, but still a hot core
    # against a dim cabinet so it reads as the thing everything else is plugged into.
    fx = FX["restorer"]
    cyl(12, 0.17, 0.62, (0, 0.34, 0.66),
        material=mat("rk", fx["colour"], 0.0, 0.25, emit=fx["colour"], emit_strength=0.9))
    cyl(12, 0.21, 0.06, (0, 0.34, 1.00), material=mat("rt", BONE, 0.7, 0.35))
    for s in (-1, 1):                                   # conduit down to the deck
        cyl(6, 0.045, 0.42, (s * 0.34, 0.02, 0.24),
            material=mat("rd%d" % s, STEEL_LT, 0.75, 0.35))


# The Hollow. Never described directly in dialog, so the art does not describe it either:
# no faces, no optics, no limbs. Geometry that is almost architecture, in a violet-white
# that appears nowhere else in the game — the fourth and last faction colour.
HOLLOW_LIT = (0.66, 0.60, 0.90)
HOLLOW_DARK = (0.075, 0.070, 0.095)


def a_hollow_shard():
    # One piece of what the Echo is a cluster of, travelling alone. Half the Echo's height
    # and a single blade rather than five, so the two read as the same material at two
    # scales — which is the only thing the act ever says about what the Hollow is made of.
    # LF-049: the dark body and the lit tip share one rotation but must also share one
    # AXIS — both locations come from axis_offset() off the same origin so they're
    # colinear, rather than independently rotating about two different (0,0,z) origins
    # (which produced parallel, laterally offset axes and collapsed the glow at yaw 315).
    shard_rot = (math.radians(11), math.radians(-8), 0)
    cone(6, 0.17, 0.018, 0.62, axis_offset(shard_rot, 0.31), rot=shard_rot,
         material=mat("hsb", HOLLOW_DARK, 0.2, 0.55))
    cone(6, 0.075, 0.014, 0.30, axis_offset(shard_rot, 0.50), rot=shard_rot,
         material=mat("hsg", HOLLOW_LIT, 0.0, 0.3,
                      emit=(0.60, 0.52, 0.94), emit_strength=1.2))
    # A low sliver of debris travelling with it, so the sprite has a base to sit on and
    # does not read as floating the way a bare blade does at this size.
    cube(0.20, (0.11, -0.13, 0.05), scale=(1.0, 0.55, 0.32), rot_z=math.radians(24),
         material=mat("hsd", HOLLOW_DARK, 0.25, 0.6))


def a_hollow_echo():
    # A cluster of thin shards leaning the same way. Baseline unit, so it has to read
    # instantly at 100% zoom without having any features to read.
    # Thicker and wider than the first cut: at 0.10 radius the shards were hairlines and
    # the act's baseline unit was invisible on a dark deck.
    echo_rot = (math.radians(7), math.radians(-5), 0)
    for i, (x, y, h, t) in enumerate([(0.0, 0.0, 1.05, 1.0), (-0.22, 0.16, 0.80, 0.85),
                                      (0.23, -0.14, 0.70, 0.8), (0.07, 0.26, 0.56, 0.7),
                                      (-0.10, -0.22, 0.50, 0.65)]):
        if x == 0.0 and y == 0.0:
            # LF-049: this central shard (x=0,y=0) is the one the lit core below is
            # meant to sit on the same axis as — axis_offset() keeps them colinear. The
            # other four shards are standalone, at their own hand-placed (x,y) with no
            # companion cone, so they are left as independently-rotated placements.
            loc = axis_offset(echo_rot, h * 0.5)
        else:
            loc = (x, y, h * 0.5)
        cone(6, 0.20 * t, 0.020, h, loc, rot=echo_rot,
             material=mat("he%d" % i, HOLLOW_DARK, 0.2, 0.55))
    cone(6, 0.09, 0.016, 0.50, axis_offset(echo_rot, 0.84), rot=echo_rot,
         material=mat("hec", HOLLOW_LIT, 0.0, 0.3,
                      emit=(0.58, 0.50, 0.92), emit_strength=1.2))


def a_hollow_drift():
    # Air. A lens, edge-on, with nothing inside it. Flies at the warden mote's altitude.
    cyl(24, 0.30, 0.05, (0, 0, 0.88), rot=(math.radians(74), 0, 0),
        material=mat("hdl", HOLLOW_DARK, 0.3, 0.4))
    torus(0.31, 0.022, (0, 0, 0.88), rot=(math.radians(74), 0, 0),
          material=mat("hdr", HOLLOW_LIT, 0.0, 0.3,
                       emit=(0.60, 0.52, 0.94), emit_strength=1.3))
    sphere(0.06, (0, 0, 0.88), segments=12, rings=8,
           material=mat("hdc", HOLLOW_LIT, 0.0, 0.3,
                        emit=(0.72, 0.66, 1.0), emit_strength=1.1))


def a_hollow_vessel():
    # A shell with the inside missing: an open ring standing upright on a dark plinth.
    # The gap is the silhouette, which is the only way to draw the thing honestly.
    cyl(10, 0.34, 0.16, (0, 0, 0.08), material=mat("hvb", HOLLOW_DARK, 0.2, 0.6))
    torus(0.36, 0.075, (0, 0, 0.58), rot=(math.radians(90), 0, 0),
          material=mat("hvr", HOLLOW_DARK, 0.35, 0.45))
    torus(0.24, 0.028, (0, 0, 0.58), rot=(math.radians(90), 0, 0),
          material=mat("hvg", HOLLOW_LIT, 0.0, 0.3,
                       emit=(0.56, 0.48, 0.90), emit_strength=1.2))
    for s in (-1, 1):
        cube(0.10, (s * 0.30, 0, 0.26), scale=(1.0, 1.0, 2.2),
             material=mat("hvl%d" % s, HOLLOW_DARK, 0.3, 0.5))


def a_hollow_column():
    # The heavy: stacked slabs with lit seams, taller than anything else on the board
    # except the anchor ring itself. It is slow, so it is allowed to be architecture.
    for i in range(4):
        w = 0.62 - i * 0.09
        cube(1.0, (0, 0, 0.16 + i * 0.30), scale=(w, w * 0.8, 0.13),
             rot_z=math.radians(i * 12), material=mat("hcs%d" % i, HOLLOW_DARK, 0.3, 0.5))
        cube(1.0, (0, 0, 0.30 + i * 0.30), scale=(w * 0.92, w * 0.72, 0.022),
             rot_z=math.radians(i * 12),
             material=mat("hcg%d" % i, HOLLOW_LIT, 0.0, 0.3,
                          emit=(0.50, 0.43, 0.84), emit_strength=1.0))
    cone(8, 0.16, 0.02, 0.36, (0, 0, 1.42),
         material=mat("hct", HOLLOW_LIT, 0.0, 0.3,
                      emit=(0.64, 0.56, 0.96), emit_strength=1.3))


ASSETS = {
    "tile_ground": a_tile_ground,
    "tile_path": a_tile_path,
    "tile_slot": a_tile_slot,
    "anchor_ring": a_anchor_ring,
    "pulse_turret": a_pulse_turret,
    "arc_node": a_arc_node,
    "scan_relay": a_scan_relay,
    "shield_wall": a_shield_wall,
    "ion_lance": a_ion_lance,
    "warden_drone": a_warden_drone,
    "warden_heavy": a_warden_heavy,
    "warden_hauler": a_warden_hauler,
    "warden_mote": a_warden_mote,
    "flak_array": a_flak_array,
    "anchor_damper": a_anchor_damper,
    "mortar_emplacement": a_mortar_emplacement,
    "reach_picket": a_reach_picket,
    "reach_sapper": a_reach_sapper,
    "reach_breacher": a_reach_breacher,
    "reach_skiff": a_reach_skiff,
    "reach_bulwark": a_reach_bulwark,
    "restorer": a_restorer,
    "hollow_shard": a_hollow_shard,
    "hollow_echo": a_hollow_echo,
    "hollow_drift": a_hollow_drift,
    "hollow_vessel": a_hollow_vessel,
    "hollow_column": a_hollow_column,
}


# ── rig ────────────────────────────────────────────────────────────────────

def setup_scene():
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.eevee.taa_render_samples = SAMPLES
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.resolution_x = CELL
    sc.render.resolution_y = CELL
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "Standard"

    vl = bpy.context.view_layer
    vl.use_pass_emit = True

    key = bpy.data.objects.new("Key", bpy.data.lights.new("Key", "SUN"))
    sc.collection.objects.link(key)
    key.rotation_euler = (math.radians(48), 0, math.radians(35))
    key.data.energy = 2.1
    key.data.color = (1.0, 0.96, 0.90)

    fill = bpy.data.objects.new("Fill", bpy.data.lights.new("Fill", "SUN"))
    sc.collection.objects.link(fill)
    fill.rotation_euler = (math.radians(66), 0, math.radians(215))
    fill.data.energy = 0.75
    fill.data.color = (0.55, 0.78, 0.88)

    w = bpy.data.worlds.new("W")
    w.use_nodes = True
    w.node_tree.nodes["Background"].inputs[0].default_value = (0.05, 0.07, 0.08, 1)
    w.node_tree.nodes["Background"].inputs[1].default_value = 0.5
    sc.world = w
    return sc, [key, fill]


def setup_compositor(sc):
    ng = bpy.data.node_groups.new("SpriteComp", "CompositorNodeTree")
    sc.compositing_node_group = ng
    rl = ng.nodes.new("CompositorNodeRLayers")
    glare = ng.nodes.new("CompositorNodeGlare")
    # This runs on the Emission pass, which already excludes lit surfaces, so the
    # threshold only has to clear near-black. It was 0.55, chosen when the palette was
    # being fed display values and every emitter was ~3x too bright in linear terms
    # (see srgb()); at correct levels that muted the slot rings and anchor wards
    # entirely. Emitters now land at 0.2–1.3 linear, so 0.08 separates cleanly.
    # Size is the Bloom blur radius, roughly 2^Size px. It was 7.0 — a ~128px blur on a
    # 256px cell — which is the mechanical cause of LF's "lightbulb on a post" defect:
    # every emitter, regardless of its own shape, came out as one flat uniform disc with
    # no falloff, because the blur radius was half the canvas. 3.0 (~8px) lets Bloom add
    # a soft spread around a shape that already has structure (see glow_point) instead of
    # being the only source of any spread at all. Strength dropped with it, 1.0 -> 0.8,
    # so the bloom's own contribution sits under the emplacements' newly-lowered base
    # emission rather than re-inflating it back to unit brightness or past it.
    for k, v in (("Type", "Bloom"), ("Quality", "High"), ("Threshold", 0.08),
                 ("Strength", 0.8), ("Size", 3.0)):
        glare.inputs[k].default_value = v
    ng.links.new(rl.outputs["Emission"], glare.inputs["Image"])
    out = ng.nodes.new("NodeGroupOutput")
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    ng.links.new(glare.outputs["Image"], out.inputs[0])
    return ng


def place_camera(sc, yaw_deg, height_bias=HEIGHT_BIAS):
    cd = bpy.data.cameras.new("Cam")
    cd.type = "ORTHO"
    cd.ortho_scale = ORTHO_SCALE
    cam = bpy.data.objects.new("Cam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam
    rx = math.radians(90.0 - ELEVATION_DEG)
    rz = math.radians(yaw_deg)
    d = 14.0
    cam.rotation_euler = (rx, 0, rz)
    cam.location = (d * math.sin(rx) * math.sin(rz),
                    -d * math.sin(rx) * math.cos(rz),
                    d * math.cos(rx) + height_bias)
    return cam


def _emission_strengths(value=None):
    """Read or overwrite every material's Emission Strength. Returns the old values.

    Decision 007 says glow is never baked into a sprite. That was not actually true:
    Principled emission contributes to the beauty render, so the albedo pass carried a
    saturated emissive core *and* the glow pass carried it again, and the engine's
    additive layer summed the two. Every strong emitter — the arc node worst of all —
    resolved to a featureless white ball in game even though neither PNG clipped on its
    own. Zeroing emission for the albedo pass makes the albedo pure surface and leaves
    the glow layer supplying all of the emissive light, which is what lets a brownout
    dim it (LF-031).
    """
    old = {}
    for m in bpy.data.materials:
        if not m.use_nodes:
            continue
        b = m.node_tree.nodes.get("Principled BSDF")
        if b is None:
            continue
        s = b.inputs["Emission Strength"]
        old[m.name] = s.default_value
        if value is not None:
            s.default_value = value
    return old


def render_pair(sc, ng, name, yaw, out_dir):
    cam = place_camera(sc, yaw)
    paths = {}
    sc.render.use_compositing = False
    saved = _emission_strengths(0.0)          # albedo is surface only
    sc.render.filepath = os.path.join(out_dir, "%s_y%03d_albedo.png" % (name, yaw))
    bpy.ops.render.render(write_still=True)
    paths["albedo"] = sc.render.filepath
    for mname, v in saved.items():            # emission back on for the glow pass
        bpy.data.materials[mname].node_tree.nodes["Principled BSDF"] \
            .inputs["Emission Strength"].default_value = v
    sc.render.use_compositing = True
    sc.render.filepath = os.path.join(out_dir, "%s_y%03d_glow.png" % (name, yaw))
    bpy.ops.render.render(write_still=True)
    paths["glow"] = sc.render.filepath
    bpy.data.objects.remove(cam, do_unlink=True)
    return paths


def _measure_tile(sc):
    """Render a bare 1x1 plane and measure it.

    Returns (width_px, height_px, pivot_x, pivot_y). The pivot is where world (0,0,0)
    lands, in Godot's top-left-origin pixel coordinates.

    The pivot used to be hardcoded to the middle of the canvas. It is not: the
    production camera is raised by HEIGHT_BIAS so tall assets clear the top of the
    cell, which pushes world origin ~48px *below* the canvas centre. Every sprite
    therefore drew that far above its own tile — the build highlight sat off the slot
    it pointed at, and turrets floated over their slot rings (LF-027). This renders
    with the production camera and measures, so changing HEIGHT_BIAS re-derives the
    pivot instead of silently misaligning the entire library.
    """
    for o in [o for o in bpy.data.objects if o.type == "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 0, 0))
    put(bpy.context.active_object, mat("cal", BONE))
    prev = sc.eevee.taa_render_samples
    prev_filter = sc.render.filter_size
    sc.eevee.taa_render_samples = 1
    # Blender's pixel reconstruction filter softens the silhouette by roughly a pixel
    # each side, which shrinks an alpha-thresholded measurement. Off, so the
    # measurement is of the projection and not of the filter.
    sc.render.filter_size = 0.0
    sc.render.use_compositing = False
    cam = place_camera(sc, 45)          # production framing, bias included
    path = os.path.join(OUT_DIR, "_calibration.png")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)
    sc.eevee.taa_render_samples = prev
    sc.render.filter_size = prev_filter

    px = bpy.data.images.load(path)
    w, h = px.size
    buf = list(px.pixels)
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            # Probed on this machine (ART-03/LF-102): at taa_render_samples=1 and
            # filter_size=0.0 the alpha channel is exactly binary — 0.0 or 1.0, never
            # a fractional coverage value — because there is no accumulation to
            # produce a soft edge from a single unfiltered sample. The ">0.02, not
            # >0.5" threshold below is therefore not doing the partial-coverage job
            # the old comment here described; it is just "count this pixel or don't"
            # on an already-binary buffer. That binary in/out test is what makes this
            # measurement exact-integer *and* subject to phase noise: whichever side
            # of the true continuous edge a pixel's sample point falls on can differ
            # between the x and y axes even when the underlying scale is exactly
            # right, which is the root cause calibrate() corrects for using
            # _measure_tile_subpixel() rather than by looping this threshold.
            if buf[(y * w + x) * 4 + 3] > 0.02:
                xs.append(x)
                ys.append(y)
    bpy.data.images.remove(px)
    for o in [o for o in bpy.data.objects if o.type == "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)
    if not xs:
        return (0, 0, 0, 0)
    if min(xs) == 0 or max(xs) == w - 1 or min(ys) == 0 or max(ys) == h - 1:
        # The silhouette is clipped, so its centre is not the projection of world
        # origin and the pivot would be wrong. Fail loudly rather than measure a crop.
        print("CALIBRATION FAIL: tile silhouette touches the frame edge "
              "— HEIGHT_BIAS %.3f is too large for a %dpx cell" % (HEIGHT_BIAS, CELL))
        return (0, 0, 0, 0)
    # Blender's pixel buffer is bottom-up; Godot places sprites from the top-left.
    cx = (min(xs) + max(xs)) / 2.0
    cy_from_bottom = (min(ys) + max(ys)) / 2.0
    cy = (h - 1) - cy_from_bottom
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, cx, cy)


def _interp_crossing(lo_val, hi_val, level=0.5):
    """Fraction of the way from the low sample to the high sample that `level` falls
    at, assuming a linear ramp between them. Used to locate a sub-pixel edge."""
    if hi_val == lo_val:
        return 0.5
    return (level - lo_val) / (hi_val - lo_val)


def _measure_tile_subpixel(sc):
    """Render the same calibration plane with real antialiasing on, and return
    (width_sub, height_sub) as continuous, sub-pixel-accurate floats.

    `_measure_tile` deliberately renders at taa_render_samples=1 / filter_size=0.0 so
    its measurement is reproducible and exact-integer — but that setting produces a
    perfectly binary alpha channel (probed here: 0.0 or 1.0 only, confirmed by
    inspecting the raw buffer at a failing cell size), which is exactly why the
    integer bbox it returns can read one pixel short on one axis while the other axis
    already reads exact: a binary in/out test has no sub-pixel information to correct
    from, only a coin-flip on which side of the true edge a pixel's sample point
    lands, and that phase can differ between the x and y axes even at the *correct*
    scale.

    Restoring samples and the reconstruction filter (32 / 1.5, well short of the
    production 64 but enough to converge) gives a genuine analytic coverage gradient
    at each silhouette edge (also probed here) that a linear interpolation between
    neighbouring pixels can locate to a small fraction of a pixel. calibrate() uses
    that continuous width/height — not the binary one — to solve ORTHO_SCALE, because
    a 1x1 tile's width and height are tied by an exact 2:1 ratio for any correct
    scale (the projection is isotropic and the elevation is fixed), so getting either
    axis continuously exact makes the other exact too; only the binary threshold's
    independent per-axis rounding can make them disagree.
    """
    for o in [o for o in bpy.data.objects if o.type == "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 0, 0))
    put(bpy.context.active_object, mat("cals", BONE))
    prev = sc.eevee.taa_render_samples
    prev_filter = sc.render.filter_size
    sc.eevee.taa_render_samples = 32
    sc.render.filter_size = 1.5
    sc.render.use_compositing = False
    cam = place_camera(sc, 45)
    # Scratch only, unlike `_measure_tile`'s "_calibration.png": that one is an
    # existing tracked file in assets/renders/ (committed build output, overwritten
    # every calibrate() run); this one is new with ART-03 and has no reason to churn
    # the committed asset tree on every render, so it goes to the OS temp dir instead.
    path = os.path.join(tempfile.gettempdir(), "lf_calibration_sub.png")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)
    sc.eevee.taa_render_samples = prev
    sc.render.filter_size = prev_filter

    px = bpy.data.images.load(path)
    w, h = px.size
    buf = px.pixels[:]

    def alpha(x, y):
        return buf[(y * w + x) * 4 + 3]

    # Coarse bbox at the 0.5 level to locate the shape, then refine each edge with
    # a linear interpolation between the last sample outside 0.5 and the first
    # sample inside it (or vice versa).
    xs = [x for y in range(h) for x in range(w) if alpha(x, y) > 0.5]
    ys = [y for y in range(h) for x in range(w) if alpha(x, y) > 0.5]
    bpy.data.images.remove(px)
    for o in [o for o in bpy.data.objects if o.type == "MESH"]:
        bpy.data.objects.remove(o, do_unlink=True)
    if not xs or not ys:
        return (0.0, 0.0)

    cy_mid = (min(ys) + max(ys)) // 2
    cx_mid = (min(xs) + max(xs)) // 2

    # Left/right edges along the row through the shape's vertical middle.
    row = [alpha(x, cy_mid) for x in range(w)]
    li = next(i for i in range(1, w) if row[i] >= 0.5 > row[i - 1])
    left = (li - 1) + _interp_crossing(row[li - 1], row[li])
    ri = next(i for i in range(w - 2, -1, -1) if row[i] >= 0.5 > row[i + 1])
    right = ri + _interp_crossing(row[ri], row[ri + 1])

    # Top/bottom edges along the column through the shape's horizontal middle.
    col = [alpha(cx_mid, y) for y in range(h)]
    ti = next(i for i in range(1, h) if col[i] >= 0.5 > col[i - 1])
    top = (ti - 1) + _interp_crossing(col[ti - 1], col[ti])
    bi = next(i for i in range(h - 2, -1, -1) if col[i] >= 0.5 > col[i + 1])
    bottom = bi + _interp_crossing(col[bi], col[bi + 1])

    return (right - left, bottom - top)


def calibrate(sc):
    """Solve the orthographic scale so a 1x1 tile measures exactly TILE_W px, then
    assert it.

    The camera elevation in this project was wrong for six sessions because a constant
    was written down from memory and trusted (decision 017). The response is not a
    better-remembered constant: it is to measure on every run and refuse to render if
    the projection is off. One extra 256px render costs nothing next to re-rendering
    the entire sprite library.

    ART-03/LF-102: the width-ratio correction below (`ORTHO_SCALE *= w / TILE_W`) is
    the entire original algorithm, kept byte-for-byte for the case that actually needs
    it — `w` still off target — because it is what solves 256px today and the solved
    value there has to stay bit-identical. What it cannot do is fix a *height* error
    once `w` already reads exact: multiplying by `w / TILE_W` when `w == TILE_W` is a
    no-op by construction, so a one-pixel height shortfall (reproduced deterministically
    at 384px — 192x95 against 192x96 — and 1024px — 512x255 against 512x256) burned
    every remaining iteration doing nothing and always failed. 256 and 512 never hit
    this branch, which is the "works by luck" in the bug report: their nominal scale
    happens to round correctly on both axes after only ever correcting from `w`.

    The fix is not a looser tolerance (decision 017 exists specifically to keep this a
    real gate) and not a second integer-ratio correction on `h` — `_measure_tile`'s
    bbox is a binary in/out test per pixel (probed here: with taa_render_samples=1 and
    filter_size=0.0 the alpha channel is exactly 0.0 or 1.0, no partial coverage at
    all), so an "off by one" on that measurement can be pure sample-point phase noise
    rather than a scale error, and correcting from an integer count would be
    correcting from noise. Once stuck — `w` already exact, `h` is not — the fallback
    renders the same plane with real antialiasing (`_measure_tile_subpixel`, 32
    samples / filter 1.5) and reads a continuous, sub-pixel width and height off the
    resulting alpha gradient by linear interpolation. A 1x1 tile's width and height
    are tied by an exact 2:1 ratio at any correct scale — the projection is isotropic
    and the elevation is fixed — so nudging ORTHO_SCALE from whichever axis's
    *continuous* measurement is furthest from its target (not whichever axis's binary
    reading is wrong) moves both axes toward exact together, which is what a purely
    integer-driven correction cannot do once one axis has already latched onto its
    target pixel count.
    """
    global ORTHO_SCALE, PIVOT
    target_h = TILE_W // 2
    for _ in range(10):
        w, h, cx, cy = _measure_tile(sc)
        if w == 0:
            print("CALIBRATION FAIL: nothing rendered")
            return False
        if w == TILE_W and h == target_h:
            PIVOT = (cx, cy)
            print("CALIBRATION ok tile=%dx%d ratio=%.4f elev=%.4f ortho=%.6f (nominal %.6f)"
                  % (w, h, w / h, ELEVATION_DEG, ORTHO_SCALE, ORTHO_SCALE_NOMINAL))
            print("CALIBRATION pivot=(%.1f,%.1f) canvas centre=(%d,%d) bias=%.2f"
                  % (cx, cy, CELL // 2, CELL // 2, HEIGHT_BIAS))
            return True
        if w != TILE_W:
            # Original algorithm, unchanged: still making progress on the axis it
            # has always corrected from.
            ORTHO_SCALE = ORTHO_SCALE * (float(w) / float(TILE_W))
            continue
        # w is already exact and h is not — the width correction above is a no-op.
        # Fall back to the sub-pixel measurement and correct from whichever axis's
        # continuous value is furthest from its target.
        w_sub, h_sub = _measure_tile_subpixel(sc)
        if w_sub <= 0.0 or h_sub <= 0.0:
            print("CALIBRATION FAIL: sub-pixel measurement produced nothing")
            return False
        w_err = abs(w_sub - TILE_W) / TILE_W
        h_err = abs(h_sub - target_h) / target_h
        if h_err >= w_err:
            ORTHO_SCALE = ORTHO_SCALE * (h_sub / float(target_h))
        else:
            ORTHO_SCALE = ORTHO_SCALE * (w_sub / float(TILE_W))
    w, h, _cx, _cy = _measure_tile(sc)
    print("CALIBRATION FAIL tile=%dx%d expected=%dx%d ortho=%.6f"
          % (w, h, TILE_W, target_h, ORTHO_SCALE))
    return False


def _shared_source() -> str:
    """This file's source, minus every individual asset builder's body.

    What is left — materials, primitives, the palette, the FX loader, the render rig
    (`setup_scene`, `place_camera`, `calibrate`, ...) — is code every builder can call,
    so a change there really does change every asset's output and every asset's hash
    should move together. `compute_hashes()` combines a hash of this with one builder's
    own source, which is the PRC-13 fix for "hash the builder, not the whole file":
    editing `a_pulse_turret` no longer moves `a_arc_node`'s hash.

    Implemented as substring removal rather than a second AST pass: every ASSETS value
    is a plain top-level function, so `inspect.getsource()` returns exactly the text
    that appears once, verbatim, in the whole-file source — a `.replace(..., 1)` finds
    and removes it cleanly.
    """
    whole = inspect.getsource(sys.modules[__name__])
    for fn in ASSETS.values():
        whole = whole.replace(inspect.getsource(fn), "", 1)
    return whole


def compute_hashes(names: list) -> dict:
    """Content hash per asset in `names`.

    Inputs: this asset's builder source (`inspect.getsource()` over the ASSETS dict,
    PRC-13), `_shared_source()`, and CELL / YAWS / HEIGHT_BIAS / ORTHO_SCALE_NOMINAL /
    the Blender version string — the handful of things that change a render without
    touching any function body.

    Deliberately **ORTHO_SCALE_NOMINAL, not the calibrate()-solved ORTHO_SCALE**: this
    function has to be callable without calibrate() having run at all. calibrate()
    renders and overwrites the tracked `assets/renders/_calibration.png` on every call
    (see `_measure_tile`), so if the hash needed the solved value, `build.py`'s fast
    "has anything changed" pre-check would dirty git status on every *check*, not just
    every actual render — exactly the failure PRC-13 exists to remove. NOMINAL is a
    pure function of CELL, which is already a separate hash input, so nothing
    invalidation-relevant is lost; the Blender version string is what catches drift in
    calibrate()'s own tiny measured correction factor if that ever changes between
    Blender installs.
    """
    shared_hash = hashlib.sha256(_shared_source().encode()).hexdigest()
    constants = "CELL=%r YAWS=%r HEIGHT_BIAS=%r ORTHO_SCALE_NOMINAL=%.10f" % (
        CELL, YAWS, HEIGHT_BIAS, ORTHO_SCALE_NOMINAL)
    version = bpy.app.version_string
    out = {}
    for name in names:
        h = hashlib.sha256()
        h.update(shared_hash.encode())
        h.update(inspect.getsource(ASSETS[name]).encode())
        h.update(constants.encode())
        h.update(version.encode())
        out[name] = h.hexdigest()
    return out


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--list" in argv:
        for k in ASSETS:
            print(k)
        return 0

    if "--cell" in argv:
        set_cell(int(argv[argv.index("--cell") + 1]))

    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    assets_arg = None
    if "--assets" in argv:
        assets_arg = argv[argv.index("--assets") + 1]

    if assets_arg:
        names = [n.strip() for n in assets_arg.split(",") if n.strip()]
    elif only:
        names = [only]
    else:
        names = list(ASSETS)

    if "--print-hashes" in argv:
        # build.py's fast path (PRC-13): content hashes for `names`, computed with no
        # scene ever set up and no frame ever rendered — see compute_hashes()'s
        # docstring for why this can skip calibrate() entirely. One line of tagged JSON
        # so build.py can find it regardless of whatever else Blender prints around it
        # (same trick tools/shot.py uses for Godot's own stdout — RELAY_PREFIXES).
        bad = [n for n in names if n not in ASSETS]
        if bad:
            print("unknown asset(s): %s" % ", ".join(bad))
            return 2
        print("HASHES_JSON " + json.dumps({
            "blender_version": bpy.app.version_string,
            "hashes": compute_hashes(names),
        }))
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    wipe()
    sc, lights = setup_scene()
    ng = setup_compositor(sc)

    if not calibrate(sc):
        print("RENDER ABORTED — projection calibration failed")
        return 1
    if "--calibrate" in argv:
        return 0

    bad = [n for n in names if n not in ASSETS]
    if bad:
        print("unknown asset(s): %s" % ", ".join(bad))
        return 2

    manifest = {
        "elevation_deg": ELEVATION_DEG,
        "yaws": list(YAWS),
        # LF-108/ART-02: explicit yaw count, so sprites.gd can assert against it without
        # inferring one from len(yaws) — a 0-length "yaws" from a malformed manifest would
        # otherwise silently read as "0 yaws expected" instead of "no manifest".
        "yaw_count": YAW_COUNT,
        "cell": CELL,
        "tile_px": [TILE_W, TILE_W // 2],
        "ortho_scale": ORTHO_SCALE,
        # Every asset is built around world origin and rendered with one camera, so
        # they share one pivot: wherever world (0,0,0) lands. Godot places by this
        # point. Measured by calibrate(), not assumed to be the canvas centre — the
        # camera's HEIGHT_BIAS puts it well below that. See _measure_tile / LF-027.
        "pivot": [PIVOT[0], PIVOT[1]],
        "sprites": {},
        # PRC-13: per-asset content hash, keyed by asset name and stored as a top-level
        # sibling of "sprites" rather than nested inside each per-yaw entry — nesting it
        # there would put a bare string where pack_atlas.collect() and sprites.gd both
        # expect a {"y045": {...}, ...} dict of yaw slots, and break both.
        "hashes": compute_hashes(names),
        "blender_version": bpy.app.version_string,
    }
    for name in names:
        for o in [o for o in bpy.data.objects if o.type == "MESH"]:
            bpy.data.objects.remove(o, do_unlink=True)
        ASSETS[name]()
        entry = {}
        for yaw in YAWS:
            paths = render_pair(sc, ng, name, yaw, OUT_DIR)
            # Forward slashes always, regardless of the OS this process is running on.
            # On this project's Windows Blender install (driven through WSL interop —
            # see tools/toolpaths.py), os.path.relpath returns backslash-separated
            # paths, which is correct for *this* process but poisons the manifest for
            # every downstream *nix reader: mask_glow.py and pack_atlas.py join these
            # onto a POSIX ROOT with pathlib, which treats a whole backslash string as
            # one opaque path component rather than splitting it, so `ROOT / rel` names
            # a file that does not exist. The manifest is a cross-platform contract; the
            # process that writes it is not always on the platform that reads it.
            entry["y%03d" % yaw] = {
                k: os.path.relpath(v, ROOT).replace("\\", "/") for k, v in paths.items()
            }
        manifest["sprites"][name] = entry
        print("RENDERED %s (%d yaws x 2 passes)" % (name, len(YAWS)))

    # A partial run (--only / --assets) merges into whatever manifest is already on
    # disk instead of replacing it, so re-rendering one asset does not forget every
    # other one. A full run (no --only, no --assets — names is every key in ASSETS)
    # replaces wholesale, same as before this change.
    partial = names != list(ASSETS)
    if partial and os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            prior = json.load(f)
        prior["sprites"].update(manifest["sprites"])
        prior.setdefault("hashes", {}).update(manifest["hashes"])
        prior["blender_version"] = manifest["blender_version"]
        manifest = prior
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    print("MANIFEST %s (%d sprites)" % (MANIFEST, len(manifest["sprites"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
