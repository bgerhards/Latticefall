extends Node2D
## Root scene. Wires the anchor, HUD and dialog together.

const AnchorViewScript := preload("res://scripts/anchor_view.gd")
const HudScript := preload("res://scripts/hud.gd")
const DialogScript := preload("res://scripts/dialog_view.gd")

@export var anchor_id: String = "anchor-01"
@export var difficulty: String = "standard"

var view: Node2D

## `-- --shot <path> [frame]` renders, saves a PNG and quits. Verification should not
## depend on capturing someone's desktop, and a screenshot the build can take itself
## is a screenshot CI can take too.
var _shot_path: String = ""
var _shot_at: int = 240
var _frame: int = 0
var _autoplay: bool = false


func _ready() -> void:
	RenderingServer.set_default_clear_color(Color(0.055, 0.078, 0.09))
	_setup_cli()

	view = AnchorViewScript.new()
	view.anchor_id = anchor_id
	view.difficulty = difficulty
	add_child(view)

	var hud := HudScript.new()
	hud.view = view
	add_child(hud)

	var dialog := DialogScript.new()
	dialog.view = view
	add_child(dialog)

	Audio.music(_bed_for(anchor_id))
	if _autoplay:
		view.autobuild()
	view.start()


func _setup_cli() -> void:
	var argv := OS.get_cmdline_user_args()
	for i in range(argv.size()):
		match argv[i]:
			"--shot":
				if i + 1 < argv.size():
					_shot_path = argv[i + 1]
				if i + 2 < argv.size() and argv[i + 2].is_valid_int():
					_shot_at = int(argv[i + 2])
			"--anchor":
				if i + 1 < argv.size():
					anchor_id = argv[i + 1]
			"--autoplay":
				_autoplay = true
			"--difficulty":
				if i + 1 < argv.size():
					difficulty = argv[i + 1]


func _bed_for(aid: String) -> String:
	var act := int(Content.anchor(aid).get("act", 1))
	match act:
		2:
			return "A2-BLD_contract_terms.ogg"
		3:
			return "A3-BLD_circulatory.ogg"
		_:
			return "A1-BLD_carrier_signal.ogg"


func _process(_delta: float) -> void:
	if _shot_path == "":
		return
	_frame += 1
	if _frame == _shot_at:
		await RenderingServer.frame_post_draw
		var img := get_viewport().get_texture().get_image()
		var err := img.save_png(_shot_path)
		print("SHOT %s err=%d %dx%d" % [_shot_path, err, img.get_width(), img.get_height()])
		print("AUDIO %s" % Audio.report())
		get_tree().quit()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		get_tree().quit()
