import bpy, math, os, sys
from mathutils import Vector

OUT = os.path.abspath("props")
os.makedirs(OUT, exist_ok=True)
RES = 320
ELEV = math.degrees(math.atan(0.5))   # 26.5651 -> true 2:1 isometric

def wipe():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes): bpy.data.meshes.remove(m)

def mat(name, rgb, metal=0.0, rough=0.5, emit=None, emit_str=1.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Metallic"].default_value = metal
    b.inputs["Roughness"].default_value = rough
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = emit_str
    return m

def setmat(obj, m):
    obj.data.materials.clear(); obj.data.materials.append(m)

VERD  = (0.16, 0.42, 0.35)
VERD2 = (0.24, 0.58, 0.48)
AMBER = (0.91, 0.58, 0.16)
STEEL = (0.20, 0.23, 0.25)
BONE  = (0.72, 0.76, 0.74)

def build_anchor():
    parts=[]
    bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=1.5, depth=0.25, location=(0,0,0.12))
    base=bpy.context.active_object; setmat(base, mat("stone", STEEL, 0.1, 0.75)); parts.append(base)
    bpy.ops.mesh.primitive_torus_add(major_radius=1.05, minor_radius=0.13, location=(0,0,1.25), rotation=(math.radians(90),0,0), major_segments=48, minor_segments=12)
    ring=bpy.context.active_object; setmat(ring, mat("ring", VERD, 0.85, 0.28)); parts.append(ring)
    bpy.ops.mesh.primitive_torus_add(major_radius=0.78, minor_radius=0.05, location=(0,0,1.25), rotation=(math.radians(90),0,0), major_segments=40, minor_segments=8)
    inner=bpy.context.active_object; setmat(inner, mat("glow", VERD2, 0, 0.4, emit=(0.30,0.85,0.70), emit_str=6.0)); parts.append(inner)
    for i in range(6):
        a = math.radians(i*60+15)
        bpy.ops.mesh.primitive_cube_add(size=0.18, location=(math.cos(a)*1.18, math.sin(a)*1.18, 1.25))
        c=bpy.context.active_object; c.rotation_euler=(0,0,a); c.scale=(1.0,1.6,1.0)
        setmat(c, mat("chev", VERD2, 0.9, 0.2)); parts.append(c)
    for i in range(2):
        x = -1.0 if i==0 else 1.0
        bpy.ops.mesh.primitive_cube_add(size=0.3, location=(x*0.95, 0, 0.55))
        p=bpy.context.active_object; p.scale=(1.0,1.0,3.2); setmat(p, mat("pil", STEEL, 0.6, 0.4)); parts.append(p)
    return parts

def build_turret():
    parts=[]
    bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=1.15, depth=0.4, location=(0,0,0.2))
    b=bpy.context.active_object; setmat(b, mat("tb", STEEL, 0.5, 0.55)); parts.append(b)
    bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=0.72, depth=0.7, location=(0,0,0.72))
    h=bpy.context.active_object; setmat(h, mat("th", BONE, 0.35, 0.45)); parts.append(h)
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.16, depth=1.7, location=(0.0,0.55,1.0), rotation=(math.radians(74),0,0))
    br=bpy.context.active_object; setmat(br, mat("tbar", STEEL, 0.9, 0.25)); parts.append(br)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.19, location=(0.0,1.18,1.28), segments=20, ring_count=12)
    tip=bpy.context.active_object; setmat(tip, mat("ttip", AMBER, 0, 0.3, emit=(1.0,0.62,0.15), emit_str=8.0)); parts.append(tip)
    for s in (-1,1):
        bpy.ops.mesh.primitive_cube_add(size=0.22, location=(s*0.62,-0.2,0.95))
        f=bpy.context.active_object; f.scale=(0.5,1.5,1.9); setmat(f, mat("tf", STEEL, 0.7, 0.35)); parts.append(f)
    return parts

def build_reactor():
    parts=[]
    bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=1.25, depth=0.3, location=(0,0,0.15))
    b=bpy.context.active_object; setmat(b, mat("rb", STEEL, 0.4, 0.6)); parts.append(b)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.72, location=(0,0,1.1))
    core=bpy.context.active_object; setmat(core, mat("rc", AMBER, 0, 0.25, emit=(1.0,0.70,0.22), emit_str=9.0)); parts.append(core)
    for i in range(3):
        a=math.radians(i*120)
        bpy.ops.mesh.primitive_torus_add(major_radius=0.98, minor_radius=0.055, location=(0,0,1.1), rotation=(math.radians(90), 0, a), major_segments=40, minor_segments=8)
        r=bpy.context.active_object; setmat(r, mat("rr", BONE, 0.9, 0.22)); parts.append(r)
    for i in range(4):
        a=math.radians(i*90+45)
        bpy.ops.mesh.primitive_cube_add(size=0.2, location=(math.cos(a)*1.05, math.sin(a)*1.05, 0.55))
        s=bpy.context.active_object; s.rotation_euler=(0,0,a); s.scale=(1,1,2.6)
        setmat(s, mat("rs", STEEL, 0.65, 0.4)); parts.append(s)
    return parts

def setup_scene():
    sc=bpy.context.scene
    sc.render.engine='BLENDER_EEVEE'
    sc.eevee.taa_render_samples=64
    sc.render.film_transparent=True
    sc.render.image_settings.file_format='PNG'
    sc.render.image_settings.color_mode='RGBA'
    sc.render.resolution_x=RES; sc.render.resolution_y=RES; sc.render.resolution_percentage=100
    sc.view_settings.view_transform='Standard'
    # key
    k=bpy.data.objects.new("Key", bpy.data.lights.new("Key",'SUN')); sc.collection.objects.link(k)
    k.rotation_euler=(math.radians(52),0,math.radians(35)); k.data.energy=3.2
    k.data.color=(1.0,0.96,0.90)
    # fill
    f=bpy.data.objects.new("Fill", bpy.data.lights.new("Fill",'SUN')); sc.collection.objects.link(f)
    f.rotation_euler=(math.radians(64),0,math.radians(215)); f.data.energy=1.1
    f.data.color=(0.55,0.78,0.85)
    w=bpy.data.worlds.new("W"); w.use_nodes=True
    w.node_tree.nodes["Background"].inputs[0].default_value=(0.05,0.07,0.08,1)
    w.node_tree.nodes["Background"].inputs[1].default_value=0.6
    sc.world=w
    return sc, [k,f]

def render(sc, name, yaw, ortho=4.2):
    cam_data=bpy.data.cameras.new("C"); cam_data.type='ORTHO'; cam_data.ortho_scale=ortho
    cam=bpy.data.objects.new("C", cam_data); sc.collection.objects.link(cam); sc.camera=cam
    rx=math.radians(90-ELEV); rz=math.radians(yaw); d=14
    cam.rotation_euler=(rx,0,rz)
    cam.location=(d*math.sin(rx)*math.sin(rz), -d*math.sin(rx)*math.cos(rz), d*math.cos(rx)+0.9)
    sc.render.filepath=os.path.join(OUT, f"{name}_y{int(yaw)}.png")
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)
    print("RENDERED", sc.render.filepath)

BUILDERS={"anchor":build_anchor,"turret":build_turret,"reactor":build_reactor}
wipe()
sc, lights = setup_scene()
for name, fn in BUILDERS.items():
    for o in list(bpy.data.objects):
        if o not in lights: bpy.data.objects.remove(o, do_unlink=True)
    fn()
    for yaw in (45, 135):
        render(sc, name, yaw)
print("DONE")
