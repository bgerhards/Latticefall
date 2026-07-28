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
@onready var pause_menu: CanvasLayer = $PauseMenu

## `-- --shot <path> [frame]` renders, saves a PNG and quits. Verification should not
## depend on capturing someone's desktop, and a screenshot the build can take itself
## is a screenshot CI can take too.
var _shot_path: String = ""
var _shot_at: int = 240
var _frame: int = 0
var _shot_taken: bool = false
var _autoplay: bool = false
var _recorded: bool = false
var _open_pause: bool = false
var _select_nth: int = 0
var _pick_tower: String = ""
var _cursor_steps: int = 0

const MENU_SCENE := "res://scenes/menu.tscn"


func _ready() -> void:
    RenderingServer.set_default_clear_color(Color(0.055, 0.078, 0.09))
    # The menu is the boot scene, so it has already resolved the CLI and the player's
    # choice into Progress. _setup_cli() still runs afterwards because a --shot run
    # reaches this scene directly and its arguments must still win.
    anchor_id = Progress.selected_anchor
    difficulty = Progress.difficulty
    _setup_cli()

    view.state_changed.connect(_on_state_changed)
    view.boot(anchor_id, difficulty)
    hud.bind(view)
    dialog.bind(view)

    Audio.music(_bed_for(anchor_id))
    if _autoplay:
        view.autobuild()
    view.start()
    if _select_nth > 0 and view.sim != null and view.sim.placed.size() >= _select_nth:
        view.selected_slot = view.sim.placed[_select_nth - 1]["slot"]
    if _pick_tower != "":
        view.select(_pick_tower)      # applied after --select, so it can be seen to win
    for _i in range(_cursor_steps):
        var press := InputEventAction.new()
        press.action = "lf_right"
        press.pressed = true
        view._action_input(press)
    if _open_pause:
        # The shot counter lives in this node's _process, and show_menu() pauses the
        # tree — so without this the screenshot never happens and the run hangs.
        process_mode = Node.PROCESS_MODE_ALWAYS
        pause_menu.show_menu()


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
            "--paused":
                # Opens the pause overlay at boot so a screenshot can show it. The
                # overlay is the one screen that cannot be reached by playing at
                # --fixed-fps, because reaching it requires a key press.
                _open_pause = true
            "--select":
                # Point the inspector at the Nth built emplacement. The inspector's
                # populated state is unreachable at --fixed-fps for the same reason the
                # pause overlay is: getting there needs a click, and a UI panel that is
                # never screenshotted is a UI panel nobody has looked at.
                if i + 1 < argv.size() and argv[i + 1].is_valid_int():
                    _select_nth = int(argv[i + 1])
            "--pick":
                # Arm an emplacement in the build bar, exactly as clicking its button
                # does. Paired with --select it proves the board selection is dropped
                # when the bar is used, which is the whole of that interaction.
                if i + 1 < argv.size():
                    _pick_tower = argv[i + 1]
            "--cursor":
                # Press lf_right N times at boot. Gamepad and keyboard board navigation
                # is otherwise unscreenshottable at --fixed-fps for the same reason the
                # pause overlay and the inspector are: it takes a real input to reach.
                if i + 1 < argv.size() and argv[i + 1].is_valid_int():
                    _cursor_steps = int(argv[i + 1])
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
    if _shot_path == "" or _shot_taken:
        return
    _frame += 1
    if _frame >= _shot_at:
        _shot_taken = true
        # Freeze before awaiting. --fixed-fps disables real-time sync, so the loop
        # spins as fast as it can and hundreds of frames elapse while this coroutine
        # is suspended — which advanced the sim past the frame that was asked for and
        # made the same command capture different states run to run. Pausing first
        # makes the captured content depend on _shot_at alone. This was LF-029.
        get_tree().paused = true
        await RenderingServer.frame_post_draw
        var img := get_viewport().get_texture().get_image()
        var err := img.save_png(_shot_path)
        print("SHOT %s err=%d %dx%d" % [_shot_path, err, img.get_width(), img.get_height()])
        var stats := _frame_stats(img)
        print("FRAME coverage=%.4f distinct=%d" % [stats["coverage"], stats["distinct"]])
        # What the sim actually reached by this frame. A screenshot is only evidence
        # if the state it captured is known and repeatable — see LF-028.
        print("STATE frame=%d sim_t=%.3f wave=%d phase=%s lives=%d leaks=%d"
            % [_frame, view.sim_time(), view.wave_number(), view.phase(),
               view.sim.lives, view.sim.leaks])
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


func _on_state_changed() -> void:
    ## Record the clear exactly once, the first time the view reports `done`. The signal
    ## fires on every wave boundary, so this has to be idempotent — a second call would
    ## be harmless to Progress but would re-emit `changed` for nothing.
    if _recorded or view.phase() != "done":
        return
    _recorded = true
    Progress.mark_cleared(anchor_id, difficulty, view.sim.lives)
    print("CLEARED %s %s lives=%d" % [anchor_id, difficulty, view.sim.lives])


func _unhandled_input(event: InputEvent) -> void:
    # Bound through the input map so a gamepad reaches the pause menu too (LF-010).
    if event.is_action_pressed("lf_pause"):
        # Pause, rather than leave. A --shot run has no one to pause for and must
        # still exit on its own, which is what _shot_path tests.
        if _shot_path != "":
            get_tree().quit()
        else:
            pause_menu.toggle()
