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
##
## Headless does not work for this: GL Compatibility reads back nothing without a real
## GPU-backed window (probed on this machine — `await RenderingServer.frame_post_draw`
## never resolves under `--headless --rendering-driver opengl3`; LF-061). So `--shot` still
## needs a real window, which by default is the one the owner is looking at. `-- --quiet-window`
## (read by the `Display` autoload in display_settings.gd, since it already owns window
## mode/flags/position and runs before this scene is even reached) sets WINDOW_FLAG_NO_FOCUS
## and WINDOW_FLAG_MOUSE_PASSTHROUGH and skips re-centring, so pairing it with the engine's
## builtin `--position <X>,<Y>` (applied at window creation, before any script runs) parks a
## fully-rendered window off every monitor without it ever taking focus or eating a click.
## `tools/shot.py` is the supported way to drive this. Default OFF: without the flag,
## `--shot` behaves exactly as it always has.
var _shot_path: String = ""
var _shot_at: int = 240
## `-- --a11y <path>` writes the text inventory for the same frame `--shot` captures.
## Same frame is the point: the analyser samples the background behind each label out of
## the PNG, so a report taken a frame later describes a screen that was never measured.
var _a11y_path: String = ""
## `-- --facings` prints the yaw every drawable was drawn at, on the frame `--shot` took.
var _dump_facings: bool = false
var _frame: int = 0
var _shot_taken: bool = false
var _autoplay: bool = false
var _recorded: bool = false
var _open_pause: bool = false
var _select_nth: int = 0
var _pick_tower: String = ""
var _cursor_steps: int = 0
var _scroll_steps: int = 0
## `-- --build <tower-id>` (repeatable) puts a specific emplacement on the board.
##
## `--autoplay` cannot reach most of the library: `autobuild()` fills every free slot
## greedily from a total preference order, so a run builds all-of-one-thing and the flak
## array and mortar emplacement are never placed at all (the same limitation the grading
## policies have — LF-053). That made their projectiles unscreenshottable, so the combat FX
## for two of the six weapon classes shipped code-reviewed but never looked at. This is the
## hook that closes that hole, in the spirit of `--select`, `--pick` and `--cursor`: reach a
## state that otherwise needs a real player, rather than shipping something nobody has seen.
var _build_ids: Array[String] = []
## `-- --speed N` sets the game-speed multiplier at boot — reaching 2x/3x otherwise needs a
## real key press, same reasoning as every hook above.
var _speed_cli: float = 0.0
## `-- --ability <id>` (repeatable) skips its charge/cooldown and fires it immediately —
## overcharge active, shutter down, a surge with something in front of it to hit are all
## otherwise unreachable at --fixed-fps.
var _ability_ids: Array = []
## `-- --chain N` sets the kill-chain streak directly — a real N-kill streak needs N kills
## inside chain_window_s of each other, which nothing at --fixed-fps can produce on its own.
var _chain_cli: int = 0

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
    _place_requested()
    view.start()
    if _select_nth > 0 and view.sim != null and view.sim.placed.size() >= _select_nth:
        view.selected_slot = view.sim.placed[_select_nth - 1]["slot"]
    if _pick_tower != "":
        view.select(_pick_tower)      # applied after --select, so it can be seen to win
    if _scroll_steps != 0:
        hud.scroll_panels(_scroll_steps)
    for _i in range(_cursor_steps):
        var press := InputEventAction.new()
        press.action = "lf_right"
        press.pressed = true
        view._action_input(press)
    if _speed_cli > 0.0:
        view.speed = _speed_cli
    for aid in _ability_ids:
        # view.abilities is untyped Variant (see anchor_view.gd's own doc on why) — accessed
        # dynamically here for the same reason hud.gd reaches through `view` throughout.
        if view.abilities != null:
            view.abilities.force_ready(aid)
            view.activate_ability(aid)
    if _chain_cli > 0:
        view.debug_set_chain(_chain_cli)
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
            "--a11y":
                if i + 1 < argv.size():
                    _a11y_path = argv[i + 1]
            "--facings":
                # One line per drawable with the shot: sprite, chosen yaw and board
                # position. A facing is not legible from a 1440x810 PNG — the four yaws of
                # a turret differ by which side its muzzle sits on and by 40 px of height —
                # so the screenshot alone cannot say whether a sprite is pointing where the
                # thing it is tracking actually is. This makes that falsifiable. LF-050.
                _dump_facings = true
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
            "--scroll":
                # Scroll the instrument panels N steps, exactly as lf_panel_down does.
                # Above 125% interface scale both panels hold more than fits, and the
                # scrolled state is unreachable at --fixed-fps for the same reason the
                # pause overlay and the inspector are: it takes a real key press.
                if i + 1 < argv.size() and argv[i + 1].is_valid_int():
                    _scroll_steps = int(argv[i + 1])
            "--cursor":
                # Press lf_right N times at boot. Gamepad and keyboard board navigation
                # is otherwise unscreenshottable at --fixed-fps for the same reason the
                # pause overlay and the inspector are: it takes a real input to reach.
                if i + 1 < argv.size() and argv[i + 1].is_valid_int():
                    _cursor_steps = int(argv[i + 1])
            "--build":
                # Repeatable. `--build mortar-emplacement --build flak-array` puts one of
                # each on the next two free slots, which is the only way to photograph the
                # weapons autobuild never reaches. See _place_requested().
                if i + 1 < argv.size():
                    _build_ids.append(argv[i + 1])
            "--difficulty":
                if i + 1 < argv.size():
                    difficulty = argv[i + 1]
            "--speed":
                if i + 1 < argv.size() and argv[i + 1].is_valid_float():
                    _speed_cli = float(argv[i + 1])
            "--ability":
                if i + 1 < argv.size():
                    _ability_ids.append(argv[i + 1])
            "--chain":
                if i + 1 < argv.size() and argv[i + 1].is_valid_int():
                    _chain_cli = int(argv[i + 1])


func _place_requested() -> void:
    ## Build each `--build <tower-id>` on the next free slot, funding it if the anchor's
    ## starting money will not stretch.
    ##
    ## Granting funds is deliberate and it is why this is a verification hook and not a
    ## cheat: the point is to photograph a weapon's projectile, and on anchor-06 the mortar
    ## costs more than the anchor starts with, so "can the player afford it on wave one" is
    ## a different question that would silently produce an empty board and a screenshot of
    ## nothing. Everything else goes through the normal `sim.build_at()`, so slot occupancy,
    ## bus load and the free-slot list all end up exactly as a real build leaves them. The
    ## granted amount is printed, because a shot is only evidence if the state it captured
    ## is known (LF-028).
    if _build_ids.is_empty() or view.sim == null:
        return
    for tid in _build_ids:
        if not Content.towers.has(tid):
            push_warning("main: --build %s is not a tower id" % tid)
            continue
        if view.sim.free_slots.is_empty():
            push_warning("main: --build %s has no free slot left" % tid)
            break
        var cost := int(Content.tower(tid)["cost"])
        if cost > int(view.sim.funds):
            # Annotated, not inferred: `view.sim` is an untyped var, so `view.sim.funds` is a
            # Variant and `:=` cannot infer at PARSE time — which fails the whole script, so
            # menu.gd cannot load main.tscn and the game hangs on the menu instead of
            # reporting anything. Same trap that took the playfield down via fx_additive.gd.
            var granted: int = cost - int(view.sim.funds)
            view.sim.funds += granted
            print("BUILD-GRANT %s +%d" % [tid, granted])
        var slot: Vector2i = view.sim.free_slots[0]
        if view.sim.build_at(tid, slot):
            print("BUILD %s at (%d,%d)" % [tid, slot.x, slot.y])
    view.queue_redraw()


func _bed_for(aid: String) -> String:
    var act := int(Content.anchor(aid).get("act", 1))
    match act:
        2:
            return "A2-BLD_contract_terms.ogg"
        3:
            return "A3-BLD_circulatory.ogg"
        _:
            return "A1-BLD_carrier_signal.ogg"


## Frames of real drawing kept before the captured one. The board is immediate-mode and
## carries no frame-to-frame render state, so one warm frame would do; three is cheap
## insurance for anything that eases toward a target over a few frames rather than being
## computed outright, and it keeps the captured frame from ever being the first one drawn.
const SHOT_WARMUP_FRAMES: int = 3


func _process(_delta: float) -> void:
    if _shot_path == "" or _shot_taken:
        return
    _frame += 1
    # Draw only the frames that end up in the PNG.
    #
    # A capture at frame 1800 used to *render* 1800 frames to keep the 1800th. Nothing reads
    # the other 1799 — they exist so the sim can reach the state being photographed, and the
    # sim advances in AnchorView._process, which runs whether or not the frame is drawn.
    # Turning the render loop off until the last few frames therefore captures exactly the
    # same image for a fraction of the work, and it matters because verification now renders
    # on a software rasteriser (decision 052): a frame costs real time there, where on a GPU
    # it was free enough not to notice.
    #
    # Deliberately not `--fixed-fps 0` or a bigger DT: the sim is stepped at a fixed
    # AnchorSimScript.DT and the capture must stay reproducible from _shot_at alone (LF-029).
    # This changes how many frames are *painted*, never how many are *simulated*.
    RenderingServer.render_loop_enabled = _frame >= _shot_at - SHOT_WARMUP_FRAMES
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
        if _a11y_path != "":
            A11yProbe.write(_a11y_path, A11yProbe.capture(self, get_viewport(), {
                "scene": "game", "anchor": anchor_id, "shot": _shot_path,
            }))
        var stats := _frame_stats(img)
        print("FRAME coverage=%.4f distinct=%d" % [stats["coverage"], stats["distinct"]])
        # What the sim actually reached by this frame. A screenshot is only evidence
        # if the state it captured is known and repeatable — see LF-028.
        print("STATE frame=%d sim_t=%.3f wave=%d phase=%s lives=%d leaks=%d"
            % [_frame, view.sim_time(), view.wave_number(), view.phase(),
               view.sim.lives, view.sim.leaks])
        print("AUDIO %s" % Audio.report())
        if _dump_facings:
            for d in view.drawables():
                print("FACE %s %s yaw=%d at=(%.0f,%.0f)"
                    % [d["kind"], d["sprite"], d["yaw"], d["at"].x, d["at"].y])
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
