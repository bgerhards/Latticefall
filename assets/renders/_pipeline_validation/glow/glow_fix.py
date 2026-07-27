import bpy, math, os
exec(open("render_props.py").read().split("BUILDERS=")[0].replace('OUT = os.path.abspath("props")','OUT = os.path.abspath("glow2")'))
OUTDIR=os.path.abspath("glow2"); os.makedirs(OUTDIR, exist_ok=True)
wipe()
sc, lights = setup_scene()
vl=bpy.context.view_layer; vl.use_pass_emit=True
ng=bpy.data.node_groups.new("SpriteComp","CompositorNodeTree"); sc.compositing_node_group=ng
n=ng.nodes; rl=n.new("CompositorNodeRLayers"); emit=rl.outputs["Emission"]
gl=n.new("CompositorNodeGlare")
for k,v in [("Type","Bloom"),("Quality","High"),("Threshold",0.30),("Strength",1.0),("Size",6.0)]:
    gl.inputs[k].default_value=v
ng.links.new(emit, gl.inputs["Image"])
go=n.new("NodeGroupOutput"); ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
ng.links.new(gl.outputs["Image"], go.inputs[0])

build_anchor()
rx=math.radians(90-ELEV); rz=math.radians(45); d=14
cd=bpy.data.cameras.new("C"); cd.type='ORTHO'; cd.ortho_scale=4.2
cam=bpy.data.objects.new("C",cd); sc.collection.objects.link(cam); sc.camera=cam
cam.rotation_euler=(rx,0,rz)
cam.location=(d*math.sin(rx)*math.sin(rz), -d*math.sin(rx)*math.cos(rz), d*math.cos(rx)+0.9)

# PASS A: albedo, compositor OFF -> identical path to the known-good direct render
sc.render.use_compositing=False
sc.render.filepath=os.path.join(OUTDIR,"albedo.png"); bpy.ops.render.render(write_still=True)
# PASS B: glow, compositor ON, world black so the additive layer has a true zero floor
sc.world.node_tree.nodes["Background"].inputs[1].default_value=0.0
sc.render.use_compositing=True
sc.render.filepath=os.path.join(OUTDIR,"glow.png"); bpy.ops.render.render(write_still=True)
print("GF DONE", sorted(os.listdir(OUTDIR)))
