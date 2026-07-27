---
name: sprite-smith
description: Produce or fix Latticefall sprite assets through the Blender 5.2 headless pipeline. Use for any work that renders, re-renders, or changes the look of a game asset — emplacements, enemies, anchors, tiles, VFX frames. Also use when a render looks wrong and the cause is unknown.
---

You produce sprites for an isometric game by rendering 3D models headlessly. You never
hand-paint. Consistency comes from the pipeline, not from discipline.

## Facts you must not re-derive

These were verified against the installed Blender 5.2.0 LTS. Assuming otherwise produces
code that fails at render time or, worse, renders subtly wrong assets.

- Camera: orthographic, elevation `atan(1/2)` = **26.5651°**, yaws 45/135/225/315.
- Only `BLENDER_EEVEE` is registered. Do not reach for Cycles.
- `scene.node_tree` **does not exist**. Use `scene.compositing_node_group` (a
  `CompositorNodeTree`) terminated by `NodeGroupOutput`. `CompositorNodeComposite` is gone.
- Glare settings are **input sockets**, title-case strings: `Type="Bloom"`, `Quality="High"`.
- Emission pass socket is `Emission`; enable `view_layer.use_pass_emit`.
- `CompositorNodeOutputFile` uses `directory`/`file_name`/`file_output_items`, and its
  node-level format only accepts `OPEN_EXR_MULTILAYER`. Prefer two renders over File Output.
- `view_settings.view_transform = 'Standard'`.
- **Always call the scene wipe before building.** Leaving Blender's default startup light in
  the scene silently washes every render out. This has already cost one debugging cycle.

## Two-pass rule

Every asset renders twice per yaw:
1. **albedo** — `render.use_compositing = False`
2. **glow** — `use_compositing = True`, Emission pass through Glare/Bloom

Glow is never baked into the albedo. Godot composites it additively and modulates it by
reactor bus load, so brownout dims every emissive element in the game.

## How you work

1. Read `docs/NOMENCLATURE.md` before naming an asset. Names are load-bearing and banned
   terms are a legal risk.
2. Write the model as a **script**, not a hand-built .blend. Scripts diff; .blend files do not.
3. Render at 320px, transparent RGBA, 64 samples.
4. **Look at the output.** Read the PNG back and inspect it. Alpha bbox, silhouette
   readability at 100%, whether the emissive actually reads. Do not report success from a
   zero exit code.
5. Report honestly: if it is a blockout, say blockout. Do not present programmer geometry
   as art.

Anything you notice but were not asked to fix goes to `tools/backlog.py add`, not into
the current change.
