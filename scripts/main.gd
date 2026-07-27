extends Node2D
## Root scene. Wires the anchor, HUD and dialog together.
##
## Deliberately not a @tool script: only anchor_view.gd needs to run while editing, and
## a script's tool-ness is independent of its parent's, so the board still previews.
##
## The child nodes are authored in scenes/main.tscn, not constructed here. They used to
## be built in _ready(), which made the scene file a single childless Node2D: opening the
## project showed an empty viewport and a scene dock that revealed nothing about the game.
## Authoring them costs nothing at runtime and makes the structure inspectable.
##
## Because children now _ready() before this node does, the anchor cannot be chosen in
## their _ready(). Setup is therefore explicit: boot() the view once the CLI is parsed,
## bind() the listeners, then start().

@export var anchor_id: String = "anchor-01"
@export var difficulty: String = "standard"

@onready var view: Node2D = $AnchorView
@onready var hud: CanvasLayer = $Hud
@onready var dialog: CanvasLayer = $DialogView

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

    view.boot(anchor_id, difficulty)
    hud.bind(view)
    dialog.bind(view)

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
        var stats := _frame_stats(img)
        print("FRAME coverage=%.4f distinct=%d" % [stats["coverage"], stats["distinct"]])
        print("AUDIO %s" % Audio.report())
        get_tree().quit()


func _frame_stats(img: Image) -> Dictionary:
    ## How much of the frame is actually drawn on, and how varied it is.
    ##
    ## The boot check runs headless and only greps for script errors, so a scene that
    ## builds no nodes passes it perfectly — which is how main.tscn stayed a childless
    ## Node2D. A blank frame scores coverage ~0 and distinct ~1; the gate asserts on
    ## these numbers so "renders nothing" is a red run rather than a green one.
    const CLEAR := Color(0.055, 0.078, 0.09)
    const STEP := 4                      # ~72k samples at 1440x810; plenty, and quick
    var lit := 0
    var total := 0
    var buckets := {}
    for y in range(0, img.get_height(), STEP):
        for x in range(0, img.get_width(), STEP):
            var c := img.get_pixel(x, y)
            total += 1
            if absf(c.r - CLEAR.r) + absf(c.g - CLEAR.g) + absf(c.b - CLEAR.b) > 0.02:
                lit += 1
            buckets[Vector3i(int(c.r * 16.0), int(c.g * 16.0), int(c.b * 16.0))] = true
    return {
        "coverage": float(lit) / maxf(float(total), 1.0),
        "distinct": buckets.size(),
    }


func _unhandled_input(event: InputEvent) -> void:
    if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
        get_tree().quit()
