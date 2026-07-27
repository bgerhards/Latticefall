"""
Latticefall sprite pipeline. Runs inside Blender 5.2:

    /Applications/Blender.app/Contents/MacOS/Blender -b --python tools/blender/render.py -- [args]
    ... -- --list
    ... -- --only pulse_turret
    ... -- --calibrate

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

import json
import math
import os
import sys

import bpy

# ── projection (decision 017 — measured, not derived) ───────────────────────
ELEVATION_DEG = 30.0            # arcsin(0.5). A 1x1 tile lands on exactly 2:1.
YAWS = (45, 135, 225, 315)
CELL = 256                      # render canvas, px
TILE_W = 128                    # a 1x1 world tile must measure this wide
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
    sphere(0.10, (0.0, 0.63, 0.70), material=mat("tt", AMBER, 0.0, 0.3,
                                                 emit=(1.0, 0.62, 0.15), emit_strength=1.3))
    for s in (-1, 1):
        cube(0.13, (s * 0.34, -0.1, 0.5), scale=(0.5, 1.5, 1.7),
             material=mat("tf%d" % s, STEEL_LT, 0.7, 0.35))


def a_arc_node():
    cyl(8, 0.55, 0.2, (0, 0, 0.1), material=mat("nb", STEEL, 0.5, 0.5))
    for i in range(3):
        a = math.radians(i * 120)
        cube(0.1, (math.cos(a) * 0.34, math.sin(a) * 0.34, 0.45), scale=(1, 1, 3.2),
             rot_z=a, material=mat("np%d" % i, STEEL_LT, 0.75, 0.3))
    sphere(0.24, (0, 0, 0.86), material=mat("nc", VERD_LIT, 0.0, 0.25,
                                            emit=(0.35, 0.95, 0.80), emit_strength=1.5))


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
    cone(20, 0.29, 0.04, 0.04, (0, 0.155, 1.13), rot=(math.radians(58), 0, 0),
         material=mat("rf", VERD_LIT, 0.0, 0.35,
                      emit=(0.22, 0.66, 0.54), emit_strength=1.1))


def a_shield_wall():
    # Broad and low — the opposite silhouette to the relay's mast. It is the biggest
    # draw in Act I, so it should look like plant: two pylons and a screen, no optics.
    cube(1.0, (0, 0, 0.07), scale=(0.86, 0.34, 0.14), material=mat("wb", STONE, 0.2, 0.8))
    for s in (-1, 1):
        cube(0.26, (s * 0.36, 0, 0.40), scale=(1.0, 1.0, 2.6),
             material=mat("wp%d" % s, STEEL, 0.55, 0.45))
        cyl(6, 0.10, 0.14, (s * 0.36, 0, 0.75), material=mat("wc%d" % s, STEEL_LT, 0.8, 0.3))
    # the screen itself: thin, upright, and the only emissive surface
    cube(1.0, (0, 0, 0.46), scale=(0.62, 0.03, 0.52),
         material=mat("ws", VERD_LIT, 0.1, 0.25,
                      emit=(0.16, 0.52, 0.44), emit_strength=1.0))
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
    cyl(14, 0.16, 0.20, (0.0, 0.06, 0.44), rot=(math.radians(68), 0, 0),
        material=mat("lc", STEEL, 0.85, 0.3))
    for s in (-1, 1):                                   # recoil rails along the barrel
        cube(0.06, (s * 0.17, 0.24, 0.60), scale=(1.0, 1.0, 7.0),
             rot_z=0.0, material=mat("lg%d" % s, STEEL, 0.7, 0.35))
    sphere(0.115, (0.0, 0.80, 1.02), segments=16, rings=10,
           material=mat("lm", VERD_LIT, 0.0, 0.25,
                        emit=(0.30, 0.86, 0.78), emit_strength=1.4))


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
    for i, s in enumerate((-1, 1)):
        for j, z in enumerate((0.46, 0.62)):
            cyl(10, 0.055, 0.68, (s * 0.20, 0.26, z), rot=(math.radians(74), 0, 0),
                material=mat("kr%d%d" % (i, j), STEEL_LT, 0.9, 0.25))
    for s in (-1, 1):                                   # ammo cans, so it reads crewed
        cube(0.22, (s * 0.46, -0.18, 0.28), scale=(1.0, 1.4, 0.9),
             material=mat("kc%d" % s, STONE_WARM, 0.2, 0.75))
    sphere(0.075, (0, 0.10, 0.66), segments=12, rings=8,
           material=mat("kt", AMBER, 0.0, 0.3,
                        emit=(1.0, 0.66, 0.18), emit_strength=1.2))


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
    # Small and dim on purpose. At major 0.40 / strength 1.5 the field ring was brighter
    # and wider than the anchor ring's own wards, and a board with four dampers on it
    # read as four objectives.
    torus(0.28, 0.032, (0, 0, 0.94),
          material=mat("dr", VERD_LIT, 0.2, 0.3,
                       emit=(0.20, 0.60, 0.50), emit_strength=0.85))


def a_mortar_emplacement():
    # Short fat tube at a steep angle on a broad plate. The lance is a long shallow
    # diagonal; the mortar is a stubby steep one, so the two do not read alike at zoom.
    cube(1.0, (0, 0, 0.06), scale=(0.78, 0.78, 0.12), material=mat("mb", STONE, 0.15, 0.8))
    cyl(8, 0.40, 0.24, (0, -0.06, 0.24), material=mat("mm", STEEL, 0.5, 0.5))
    cyl(14, 0.20, 0.86, (0.0, 0.16, 0.62), rot=(math.radians(28), 0, 0),
        material=mat("mt", STEEL_LT, 0.85, 0.3))
    cyl(14, 0.24, 0.12, (0.0, 0.02, 0.30), rot=(math.radians(28), 0, 0),
        material=mat("mc", STEEL, 0.8, 0.35))
    for s in (-1, 1):                                   # recoil spades
        cube(0.10, (s * 0.40, -0.28, 0.16), scale=(1.0, 2.2, 1.2),
             material=mat("mp%d" % s, BONE, 0.4, 0.6))
    for s in (-1, 1):                                   # shell rack
        cube(0.12, (s * 0.30, -0.40, 0.24), scale=(1.0, 1.0, 2.0),
             material=mat("mr%d" % s, STONE_WARM, 0.2, 0.7))
    sphere(0.065, (0.0, 0.44, 0.86), segments=12, rings=8,
           material=mat("mg", AMBER, 0.0, 0.3,
                        emit=(1.0, 0.58, 0.14), emit_strength=1.1))


# Sable Reach units. Human contractors, so the language is plate, scaffolding and
# floodlight — cold blue-white lamps rather than the wardens' red eye or the Ordinal's
# verdigris. Faction should be readable from the emissive colour alone at 100% zoom.
REACH_LAMP = (0.62, 0.80, 0.98)
REACH_PLATE = (0.180, 0.165, 0.140)


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
    cyl(12, 0.17, 0.62, (0, 0.34, 0.66),
        material=mat("rk", AMBER, 0.0, 0.25,
                     emit=(1.0, 0.70, 0.24), emit_strength=1.6))
    cyl(12, 0.21, 0.06, (0, 0.34, 1.00), material=mat("rt", BONE, 0.7, 0.35))
    for s in (-1, 1):                                   # conduit down to the deck
        cyl(6, 0.045, 0.42, (s * 0.34, 0.02, 0.24),
            material=mat("rd%d" % s, STEEL_LT, 0.75, 0.35))


# The Hollow. Never described directly in dialog, so the art does not describe it either:
# no faces, no optics, no limbs. Geometry that is almost architecture, in a violet-white
# that appears nowhere else in the game — the fourth and last faction colour.
HOLLOW_LIT = (0.66, 0.60, 0.90)
HOLLOW_DARK = (0.075, 0.070, 0.095)


def a_hollow_echo():
    # A cluster of thin shards leaning the same way. Baseline unit, so it has to read
    # instantly at 100% zoom without having any features to read.
    # Thicker and wider than the first cut: at 0.10 radius the shards were hairlines and
    # the act's baseline unit was invisible on a dark deck.
    for i, (x, y, h, t) in enumerate([(0.0, 0.0, 1.05, 1.0), (-0.22, 0.16, 0.80, 0.85),
                                      (0.23, -0.14, 0.70, 0.8), (0.07, 0.26, 0.56, 0.7),
                                      (-0.10, -0.22, 0.50, 0.65)]):
        cone(6, 0.20 * t, 0.020, h, (x, y, h * 0.5),
             rot=(math.radians(7), math.radians(-5), 0),
             material=mat("he%d" % i, HOLLOW_DARK, 0.2, 0.55))
    cone(6, 0.09, 0.016, 0.50, (0.0, 0.0, 0.84), rot=(math.radians(7), math.radians(-5), 0),
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
    "warden_mote": a_warden_mote,
    "flak_array": a_flak_array,
    "anchor_damper": a_anchor_damper,
    "mortar_emplacement": a_mortar_emplacement,
    "reach_sapper": a_reach_sapper,
    "reach_breacher": a_reach_breacher,
    "reach_skiff": a_reach_skiff,
    "reach_bulwark": a_reach_bulwark,
    "restorer": a_restorer,
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
    # Size is the spread in the 256px cell.
    for k, v in (("Type", "Bloom"), ("Quality", "High"), ("Threshold", 0.08),
                 ("Strength", 1.0), ("Size", 7.0)):
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
            # any coverage, not >0.5: a shape spanning exactly 128 px puts 50%
            # coverage in each boundary pixel, so a half threshold measures 126.
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


def calibrate(sc):
    """Solve the orthographic scale so a 1x1 tile measures exactly TILE_W px, then
    assert it.

    The camera elevation in this project was wrong for six sessions because a constant
    was written down from memory and trusted (decision 017). The response is not a
    better-remembered constant: it is to measure on every run and refuse to render if
    the projection is off. One extra 256px render costs nothing next to re-rendering
    the entire sprite library.
    """
    global ORTHO_SCALE, PIVOT
    for _ in range(6):
        w, h, cx, cy = _measure_tile(sc)
        if w == 0:
            print("CALIBRATION FAIL: nothing rendered")
            return False
        if w == TILE_W and h == TILE_W // 2:
            PIVOT = (cx, cy)
            print("CALIBRATION ok tile=%dx%d ratio=%.4f elev=%.4f ortho=%.6f (nominal %.6f)"
                  % (w, h, w / h, ELEVATION_DEG, ORTHO_SCALE, ORTHO_SCALE_NOMINAL))
            print("CALIBRATION pivot=(%.1f,%.1f) canvas centre=(%d,%d) bias=%.2f"
                  % (cx, cy, CELL // 2, CELL // 2, HEIGHT_BIAS))
            return True
        ORTHO_SCALE = ORTHO_SCALE * (float(w) / float(TILE_W))
    w, h, _cx, _cy = _measure_tile(sc)
    print("CALIBRATION FAIL tile=%dx%d expected=%dx%d ortho=%.6f"
          % (w, h, TILE_W, TILE_W // 2, ORTHO_SCALE))
    return False


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--list" in argv:
        for k in ASSETS:
            print(k)
        return 0

    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]

    os.makedirs(OUT_DIR, exist_ok=True)
    wipe()
    sc, lights = setup_scene()
    ng = setup_compositor(sc)

    if not calibrate(sc):
        print("RENDER ABORTED — projection calibration failed")
        return 1
    if "--calibrate" in argv:
        return 0

    names = [only] if only else list(ASSETS)
    manifest = {
        "elevation_deg": ELEVATION_DEG,
        "yaws": list(YAWS),
        "cell": CELL,
        "tile_px": [TILE_W, TILE_W // 2],
        "ortho_scale": ORTHO_SCALE,
        # Every asset is built around world origin and rendered with one camera, so
        # they share one pivot: wherever world (0,0,0) lands. Godot places by this
        # point. Measured by calibrate(), not assumed to be the canvas centre — the
        # camera's HEIGHT_BIAS puts it well below that. See _measure_tile / LF-027.
        "pivot": [PIVOT[0], PIVOT[1]],
        "sprites": {},
    }
    for name in names:
        if name not in ASSETS:
            print("unknown asset: %s" % name)
            return 2
        for o in [o for o in bpy.data.objects if o.type == "MESH"]:
            bpy.data.objects.remove(o, do_unlink=True)
        ASSETS[name]()
        entry = {}
        for yaw in YAWS:
            paths = render_pair(sc, ng, name, yaw, OUT_DIR)
            entry["y%03d" % yaw] = {k: os.path.relpath(v, ROOT) for k, v in paths.items()}
        manifest["sprites"][name] = entry
        print("RENDERED %s (%d yaws x 2 passes)" % (name, len(YAWS)))

    if only and os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            prior = json.load(f)
        prior["sprites"].update(manifest["sprites"])
        manifest = prior
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    print("MANIFEST %s (%d sprites)" % (MANIFEST, len(manifest["sprites"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
