---
name: new-asset
description: Add a new Latticefall sprite asset through the Blender pipeline — emplacement, enemy, anchor prop, or tile. Use whenever the game needs art that does not exist yet.
---

# New asset

Assets are rendered from scripted 3D, at one fixed camera, with one lighting rig. That is
what makes forty assets look like one game.

## Order of work

1. **Name it against `docs/NOMENCLATURE.md`.** Banned terms are a legal risk and renaming
   later is expensive.
2. **Model it in a script**, not by hand in the GUI. Scripts diff and regenerate; `.blend`
   files do neither.
3. **Render both passes at all four yaws** — albedo (compositing off) and glow (compositing
   on, Emission through Glare/Bloom). Eight images.
4. **Look at every one.** Read the PNGs back. Check the silhouette reads at 100%, the alpha
   bbox is sane, and the emissive actually appears in the glow pass.
5. **Report honestly.** Blockout geometry is called a blockout.

Delegate to the `sprite-smith` agent — it holds the Blender 5.2 API facts that a fresh
context will otherwise get wrong.

## Silhouette test

At final in-game size, on the darkest and lightest board tiles, is the asset identifiable
in one glance without colour? If not, the geometry is wrong. Fix it before surfacing.
