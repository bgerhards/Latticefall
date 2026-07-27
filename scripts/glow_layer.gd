extends Node2D
## Additive emissive layer, drawn on top of the board and modulated by reactor load.
##
## This is the payoff for keeping glow out of the sprite (decision 007): a brownout
## dims every emissive element in the game by changing one number, and the albedo
## underneath is untouched.

var view: Node2D


func _ready() -> void:
	var m := CanvasItemMaterial.new()
	m.blend_mode = CanvasItemMaterial.BLEND_MODE_ADD
	material = m
	z_index = 10


func _draw() -> void:
	if view == null or view.sim == null or not Sprites.ok:
		return
	var energy: float = 0.35 if view.sim.brownout else 1.0
	var tint := Color(1, 1, 1, energy)
	for d in view.drawables():
		var tex: Texture2D = Sprites.get_tex(d["sprite"], d["yaw"], "glow")
		if tex == null:
			continue
		if d["kind"] == "tower" and not d["online"]:
			continue
		draw_texture(tex, d["at"] - Sprites.pivot, tint)
