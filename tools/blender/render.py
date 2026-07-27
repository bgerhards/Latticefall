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
    cube(0.62, (0, 0, 0.42), scale=(1.0, 0.85, 0.9), material=mat("hb", STEEL, 0.5, 0.55))
    cube(0.5, (0, 0, 0.78), scale=(0.8, 0.7, 0.35), material=mat("hh", STEEL_LT, 0.65, 0.4))
    for s in (-1, 1):
        cube(0.2, (s * 0.34, 0, 0.2), scale=(1, 1, 1.6),
             material=mat("hg%d" % s, STONE, 0.4, 0.7))
    sphere(0.085, (0, 0.28, 0.8), segments=12, rings=8,
           material=mat("he", (0.9, 0.3, 0.15), 0.0, 0.3,
                        emit=(1.0, 0.28, 0.12), emit_strength=1.3))


def a_warden_mote():
    sphere(0.16, (0, 0, 0.72), segments=16, rings=10,
           material=mat("mc", VERD_LIT, 0.3, 0.35,
                        emit=(0.4, 1.0, 0.85), emit_strength=1.2))
    torus(0.28, 0.028, (0, 0, 0.72), rot=(math.radians(70), 0, 0),
          material=mat("mr", BONE, 0.8, 0.3))


ASSETS = {
    "tile_ground": a_tile_ground,
    "tile_path": a_tile_path,
    "tile_slot": a_tile_slot,
    "anchor_ring": a_anchor_ring,
    "pulse_turret": a_pulse_turret,
    "arc_node": a_arc_node,
    "warden_drone": a_warden_drone,
    "warden_heavy": a_warden_heavy,
    "warden_mote": a_warden_mote,
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


def render_pair(sc, ng, name, yaw, out_dir):
    cam = place_camera(sc, yaw)
    paths = {}
    sc.render.use_compositing = False
    sc.render.filepath = os.path.join(out_dir, "%s_y%03d_albedo.png" % (name, yaw))
    bpy.ops.render.render(write_still=True)
    paths["albedo"] = sc.render.filepath
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
