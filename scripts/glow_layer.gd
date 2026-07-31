extends Node2D
## Additive emissive layer, drawn on top of the board and modulated by reactor load.
##
## This is the payoff for keeping glow out of the sprite (decision 007): a brownout
## dims every emissive element in the game by changing one number, and the albedo
## underneath is untouched.

## Authored as a child of AnchorView in scenes/main.tscn, so it takes its subject from
## its parent rather than being handed one — there is no arrangement in which this layer
## draws for a node other than the one it hangs under.
var view: Node2D

## The brownout dim, shared with fx_additive.gd's pooled combat FX (bolt/arc/flak/mortar/
## field) and its sustained-beam pass (LF-117) — both are additive layers subject to the
## same decision-007 rule, so this is the one place the number lives rather than two
## literals that could silently drift apart.
const BROWNOUT_FACTOR := 0.35


func _ready() -> void:
	view = get_parent() as Node2D
	var m := CanvasItemMaterial.new()
	m.blend_mode = CanvasItemMaterial.BLEND_MODE_ADD
	material = m
	z_index = 10
	# Changing the glow setting has to reach the canvas: this layer only redraws when asked,
	# so without this the option appears to do nothing until the next thing moves.
	Display.changed.connect(queue_redraw)


func _draw() -> void:
	if view == null or view.sim == null or not Sprites.ok:
		return
	# Brownout dimming is the mechanic (decision 007); the Display factor is the player's
	# own ceiling on top of it. The additive layer is the brightest thing on screen, so
	# turning it down is the accommodation for light sensitivity — and at 0 the layer costs
	# nothing to draw rather than drawing black.
	if Display.glow <= 0.0:
		return
	var energy: float = (BROWNOUT_FACTOR if view.sim.brownout else 1.0) * Display.glow
	var tint := Color(1, 1, 1, energy)
	for d in view.drawables():
		var tex: Texture2D = Sprites.get_tex(d["sprite"], d["yaw"], "glow")
		if tex == null:
			continue
		if d["kind"] == "tower" and not d["online"]:
			continue
		draw_texture(tex, d["at"] - Sprites.pivot, tint)
