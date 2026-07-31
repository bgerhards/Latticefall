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
## otherwise unreachable at --fixed-fps. Fires at boot, frame 0, before anything has spawned
## — see `--ability-at` below for firing into a wave that is actually on the board.
var _ability_ids: Array = []
## `-- --ability-at <frame> <id>` (repeatable) fires an ability at a chosen main.gd process
## frame instead of at boot, so it can land on a wave that is actually live — the frame count
## is the same counter `--shot <path> <frame>` captures against, so the fired frame is
## reproducible from the number alone (LF-028). Diagnostics print before/after state so a
## screenshot is never the only evidence: see `_fire_ability_at()`.
var _ability_at: Array = []      ## [{frame:int, id:String, fired:bool}]
## `-- --press-at <frame> <action>` (repeatable) dispatches one `lf_*` action press at a
## chosen frame — the general form of `--cursor`'s boot-only `lf_right` presses, for actions
## that need to land mid-run: `lf_target` to cycle targeting priority on the `--select`ed
## emplacement, `lf_call_wave` to skip prep once a wave is actually in prep. Repeat it at the
## same frame to press the same action more than once (e.g. three `lf_target` presses to
## reach "weakest" from the "first" default).
var _press_at: Array = []        ## [{frame:int, action:String, fired:bool}]
## `-- --chain N` sets the kill-chain streak directly — a real N-kill streak needs N kills
## inside chain_window_s of each other, which nothing at --fixed-fps can produce on its own.
var _chain_cli: int = 0

## `-- --profile <frames>` (CAM-06/CAM-07): print mean/p95 milliseconds per draw layer
## (AnchorView, GlowLayer, FxAdditive, CombatFx) over this many rendered frames, then exit.
## The performance claims in those two issues need a falsifiable number, not a feeling —
## see docs/issues/CAM-06-cull-and-cache-board-tiles.md's own task asking for this hook,
## following the existing `_setup_cli()` idiom rather than a bespoke tool. 0 means "not
## requested"; a real frame count is always >= 1 because `--profile 0` parses as falsy here
## exactly like every other unset numeric flag in this file.
var _profile_frames: int = 0

## `-- --camera <x> <y> <zoom>` (CAM-03): point the board camera at a tile coordinate at a
## chosen zoom, bypassing pan/zoom/edge-scroll/cursor-follow entirely so a `--shot` and its
## paired `--a11y` report depend on the flag alone rather than on default framing that CAM-01
## can keep changing. Applied after `view.boot()`, which is what seeds the default framing
## this is meant to override — see AnchorView.set_camera_override()'s own doc.
var _camera_cli: Dictionary = {}       ## {} means "not passed"; else {x, y, zoom}, all float
## `-- --mouse-at <x> <y>` (logical viewport pixels): places the pointer at boot, the way a
## `--cursor` press places the board cursor — edge-scroll and wheel-zoom-about-cursor are
## otherwise unscreenshottable at --fixed-fps, because both need to know where the mouse
## actually is, and nothing moves it on its own. Warps the real cursor (so later frames'
## `get_global_mouse_position()` reads keep reporting it) and also dispatches one synthetic
## motion event, so AnchorView's own "a pointer has been seen at all" gate opens the same way
## a real move would.
var _mouse_at: Vector2 = Vector2.ZERO
var _has_mouse_at: bool = false
## `-- --drag <dx> <dy>` (logical viewport pixels): a synthetic middle-button drag of that
## screen-space delta, starting at `--mouse-at` (or the viewport centre if that was not
## given) — the only way to reach CAM-01's pan path without a real mouse.
var _drag_delta: Vector2 = Vector2.ZERO
var _has_drag: bool = false
## `-- --wheel <n>` (repeatable notches, negative zooms out): synthetic wheel ticks at
## `--mouse-at` (or the viewport centre) — reaches CAM-01's zoom-about-the-cursor path, which
## a held key or a stick axis (both zoom about the strip centre instead) cannot exercise.
var _wheel_steps: int = 0
## `-- --hold <action>` (repeatable): `Input.action_press()`, not a synthetic InputEvent —
## lf_zoom_in/lf_zoom_out are read every frame via `Input.is_action_pressed()` (a held-key
## zoom, not an edge-triggered step like the board cursor's), which only the Input singleton's
## own tracked state satisfies. Dispatching an InputEventAction the way `--cursor`/`--press-at`
## do never reaches it, because that calls AnchorView's handler directly and never touches
## Input at all. Held for the rest of the run — a --shot process quits right after capturing,
## so there is no matching --release and none is needed.
var _hold_actions: Array[String] = []

## Verification-only bookkeeping for `--ability-at`: how long after a fire to keep printing
## `ABILITY-LIVE` samples (bus load, shots/sec, shutter queue) — long enough to cover
## overcharge's 7s duration and shutter's 5s at real time, with margin.
const ABILITY_LIVE_WINDOW_FRAMES: int = 600
var _last_ability_fire_frame: int = -ABILITY_LIVE_WINDOW_FRAMES - 1
## Rolling window for the shots/sec sample in ABILITY-LIVE, populated only when
## `--ability-at` is in play (see _ready()) — this is the only thing that reads sim.shot_fired
## in this file, and it is a pure counter, never a rule.
const RATE_WINDOW_S: float = 1.0
var _shot_times: Array[float] = []

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
    if _profile_frames > 0:
        # After boot(): view.glow_layer/combat_fx/fx_additive are set in each node's own
        # _ready(), which — per the existing note on child-vs-parent ready order elsewhere
        # in this file — has already run by the time this line does, but boot() is still the
        # natural "the level exists now" point to start sampling from.
        view.start_profiling()
        if view.glow_layer:
            view.glow_layer.start_profiling()
        if view.combat_fx:
            view.combat_fx.start_profiling()
        if view.fx_additive:
            view.fx_additive.start_profiling()
    if not _camera_cli.is_empty():
        # After boot(), which is what seeds the default framing this overrides — see
        # AnchorView.set_camera_override()'s own doc for why the order matters.
        view.set_camera_override(Vector2(_camera_cli["x"], _camera_cli["y"]), _camera_cli["zoom"])
    hud.bind(view)
    dialog.bind(view)
    # Verification-only: dialog_view.gd already connects this to show the line on screen, but
    # nothing prints that a trigger actually fired — several of them (first-leak, low-lives,
    # wards-half/full, wave-called, chain-high) had never been observed to fire at all. A
    # second listener on the same signal is exactly as safe as dialog_view.gd's own — the
    # signal is public and multi-listener by design — and costs one line per trigger, ever
    # (each fires once; see AnchorView._fire()).
    view.dialog_trigger.connect(_on_dialog_trigger)
    # Only when `--ability-at` is in play: feeds ABILITY-LIVE's shots-per-second sample.
    # Guarded so an ordinary run (the CLI array empty) never even connects the listener.
    if not _ability_at.is_empty() and view.sim != null:
        view.sim.shot_fired.connect(_on_shot_fired_debug)

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
    if _has_mouse_at:
        _synth_mouse_at(_mouse_at)
    if _has_drag:
        _synth_drag(_mouse_at if _has_mouse_at else get_viewport_rect().size * 0.5, _drag_delta)
    if _wheel_steps != 0:
        _synth_wheel(_mouse_at if _has_mouse_at else get_viewport_rect().size * 0.5, _wheel_steps)
    for a in _hold_actions:
        Input.action_press(a)
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
            "--ability-at":
                if i + 2 < argv.size() and argv[i + 1].is_valid_int():
                    _ability_at.append({"frame": int(argv[i + 1]), "id": argv[i + 2], "fired": false})
            "--press-at":
                if i + 2 < argv.size() and argv[i + 1].is_valid_int():
                    _press_at.append({"frame": int(argv[i + 1]), "action": argv[i + 2], "fired": false})
            "--chain":
                if i + 1 < argv.size() and argv[i + 1].is_valid_int():
                    _chain_cli = int(argv[i + 1])
            "--camera":
                # One shape, parsed with is_valid_float() exactly as --speed is above: three
                # positional values or reject, no omitted-zoom / omitted-target shorthand.
                if i + 3 < argv.size() and argv[i + 1].is_valid_float() \
                        and argv[i + 2].is_valid_float() and argv[i + 3].is_valid_float():
                    _camera_cli = {"x": float(argv[i + 1]), "y": float(argv[i + 2]),
                        "zoom": float(argv[i + 3])}
            "--mouse-at":
                if i + 2 < argv.size() and argv[i + 1].is_valid_float() and argv[i + 2].is_valid_float():
                    _mouse_at = Vector2(float(argv[i + 1]), float(argv[i + 2]))
                    _has_mouse_at = true
            "--drag":
                if i + 2 < argv.size() and argv[i + 1].is_valid_float() and argv[i + 2].is_valid_float():
                    _drag_delta = Vector2(float(argv[i + 1]), float(argv[i + 2]))
                    _has_drag = true
            "--wheel":
                if i + 1 < argv.size() and argv[i + 1].is_valid_int():
                    _wheel_steps = int(argv[i + 1])
            "--hold":
                if i + 1 < argv.size():
                    _hold_actions.append(argv[i + 1])
            "--profile":
                if i + 1 < argv.size() and argv[i + 1].is_valid_int():
                    _profile_frames = int(argv[i + 1])
    if _profile_frames > 0:
        # Profiling needs the run to still be going at frame _profile_frames — extending
        # _shot_at (rather than adding a second, separate quit condition) reuses the single
        # existing pause-and-quit path in _process() unchanged, whether or not --shot was
        # also passed. See _process()'s own note on why render_loop_enabled also has to stay
        # on for the whole window when profiling, not just the last few frames before a shot.
        _shot_at = maxi(_shot_at, _profile_frames)


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
    if _shot_taken:
        return
    _frame += 1
    # Scheduled verification hooks run every frame regardless of whether a shot was
    # requested — both are no-ops (empty arrays) unless `--ability-at` or `--press-at` was
    # passed on the command line, so this changes nothing about a normal run or an ordinary
    # `--shot`.
    _process_ability_schedule()
    _process_press_schedule()
    if _frame - _last_ability_fire_frame >= 0 \
            and _frame - _last_ability_fire_frame <= ABILITY_LIVE_WINDOW_FRAMES \
            and _frame % 15 == 0:
        _dump_ability_live()
    if _shot_path == "":
        if _profile_frames > 0 and _frame >= _profile_frames:
            # --profile with no --shot: nothing pauses the tree or wants a PNG, so this is
            # its own small quit path rather than routing through the --shot branch below.
            _shot_taken = true
            _print_profile_stats()
            get_tree().quit()
        return
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
    #
    # --profile is the one thing that needs every frame actually drawn, not just the last
    # few before the shot: it measures each draw layer's own _draw() cost, which is zero on
    # a frame the render loop skipped. _setup_cli() already folded _profile_frames into
    # _shot_at, so this only has to keep the render loop on for the whole window.
    RenderingServer.render_loop_enabled = _profile_frames > 0 or _frame >= _shot_at - SHOT_WARMUP_FRAMES
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
        # Alongside SHOT/FRAME/STATE/AUDIO/FACE, whether or not --camera was passed (CAM-03):
        # a report that does not say where the camera was is the problem this exists to fix.
        var cam: Dictionary = view.camera_state()
        print("CAMERA %.3f %.3f %.4f" % [cam["x"], cam["y"], cam["zoom"]])
        if _a11y_path != "":
            A11yProbe.write(_a11y_path, A11yProbe.capture(self, get_viewport(), {
                "scene": "game", "anchor": anchor_id, "shot": _shot_path,
                "camera": {"x": cam["x"], "y": cam["y"], "zoom": cam["zoom"]},
            }))
        var stats := _frame_stats(img)
        print("FRAME coverage=%.4f distinct=%d" % [stats["coverage"], stats["distinct"]])
        # What the sim actually reached by this frame. A screenshot is only evidence
        # if the state it captured is known and repeatable — see LF-028.
        print("STATE frame=%d sim_t=%.3f wave=%d phase=%s lives=%d leaks=%d funds=%d hover=(%d,%d)"
            % [_frame, view.sim_time(), view.wave_number(), view.phase(),
               view.sim.lives, view.sim.leaks, view.sim.funds,
               view.hovered_slot.x, view.hovered_slot.y])
        print("BUS load=%.1f cap=%.1f draw=%.1f penalty=%.3f overcharge=%s shutter=%s shutter_queue=%d"
            % [view.sim.bus_load(), view.sim.capacity(), view.sim.online_draw(),
               view.sim.penalty_now(), view.sim.overcharge_active, view.sim.shutter_active,
               view.shutter_queue_size()])
        _dump_veterancy()
        print("AUDIO %s" % Audio.report())
        if _dump_facings:
            for d in view.drawables():
                print("FACE %s %s yaw=%d at=(%.0f,%.0f)"
                    % [d["kind"], d["sprite"], d["yaw"], d["at"].x, d["at"].y])
        if _profile_frames > 0:
            _print_profile_stats()
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


func _on_dialog_trigger(trigger: String) -> void:
    ## Verification-only print, see the connection in _ready(). Every trigger the game fires
    ## goes through AnchorView._fire(), so this one line covers first-leak, low-lives,
    ## wards-half, wards-full, wave-called, chain-high, brownout, surge-ready and every
    ## *-first cue without a bespoke hook per trigger.
    print("DIALOG-TRIGGER %s frame=%d sim_t=%.3f" % [trigger, _frame, view.sim_time()])


func _process_ability_schedule() -> void:
    if _ability_at.is_empty() or view == null or view.sim == null:
        return
    for entry in _ability_at:
        if bool(entry.get("fired", false)) or int(entry["frame"]) != _frame:
            continue
        entry["fired"] = true
        _fire_ability_at(String(entry["id"]))


func _fire_ability_at(id: String) -> void:
    ## `--ability-at <frame> <id>`: force the ability ready (same skip `--ability` already
    ## uses — abilities.gd's force_ready(), the sanctioned way to reach a state that needs
    ## real charge/cooldown time nothing at --fixed-fps can produce) and fire it at a chosen,
    ## reproducible frame instead of at boot. Prints enough state before and after that the
    ## claims in data/tuning.json's own notes are checkable from the log alone: surge's
    ## falloff (full at the ring, falloff_min at the mouth) and its pushback, and the bus
    ## numbers overcharge/shutter actually move.
    if view.abilities == null:
        push_warning("main: --ability-at %s has no AbilityState to fire" % id)
        return
    var pre_load: float = view.sim.bus_load()
    var pre_cap: float = view.sim.capacity()
    var pre_draw: float = view.sim.online_draw()
    var pre_penalty: float = view.sim.penalty_now()
    print("ABILITY-AT id=%s frame=%d sim_t=%.3f load=%.1f cap=%.1f draw=%.1f penalty=%.3f"
        % [id, _frame, view.sim_time(), pre_load, pre_cap, pre_draw, pre_penalty])

    var before: Array = []
    if id == "surge":
        for i in range(view.sim.units.size()):
            var u: Dictionary = view.sim.units[i]
            if bool(u["alive"]):
                before.append({
                    "i": i, "kind": String(u["kind"]["id"]),
                    "dist": float(u["dist"]), "hp": float(u["hp"]),
                    "shielded": bool(u["kind"].get("shielded", false)),
                    "armour": float(u["kind"].get("armour", 0.0)),
                })
                var b: Dictionary = before[before.size() - 1]
                print("SURGE-BEFORE i=%d kind=%s dist=%.2f hp=%.1f shielded=%s armour=%.1f"
                    % [b["i"], b["kind"], b["dist"], b["hp"], b["shielded"], b["armour"]])

    view.abilities.force_ready(id)
    var result: Dictionary = view.activate_ability(id)
    print("ABILITY-FIRED id=%s result=%s" % [id, result])

    var post_load: float = view.sim.bus_load()
    var post_cap: float = view.sim.capacity()
    var post_draw: float = view.sim.online_draw()
    var post_penalty: float = view.sim.penalty_now()
    print(("ABILITY-POST id=%s load=%.1f cap=%.1f draw=%.1f penalty=%.3f " +
           "overcharge=%s shutter=%s shutter_queue=%d")
        % [id, post_load, post_cap, post_draw, post_penalty,
           view.sim.overcharge_active, view.sim.shutter_active, view.shutter_queue_size()])

    if id == "surge" and view.sim.path_length > 0.0:
        var pl: float = view.sim.path_length
        for b in before:
            var i: int = int(b["i"])
            if i >= view.sim.units.size():
                continue
            var u: Dictionary = view.sim.units[i]
            var frac: float = clampf(float(b["dist"]) / pl, 0.0, 1.0)
            print(("SURGE-AFTER i=%d kind=%s dist_before=%.2f dist_after=%.2f " +
                   "hp_before=%.1f hp_after=%.1f alive=%s frac_along=%.3f shielded=%s")
                % [i, b["kind"], b["dist"], float(u["dist"]), b["hp"], float(u["hp"]),
                   bool(u["alive"]), frac, b["shielded"]])
    _last_ability_fire_frame = _frame


func _on_shot_fired_debug(_placed: Dictionary, _from_tile: Vector2, _to_tile: Vector2, _target_kind: Dictionary) -> void:
    _shot_times.append(view.sim_time())


func _prune_shot_log() -> void:
    var cutoff: float = view.sim_time() - RATE_WINDOW_S
    while _shot_times.size() > 0 and float(_shot_times[0]) < cutoff:
        _shot_times.pop_front()


func _dump_ability_live() -> void:
    ## Verification-only: printed every 15 frames for ABILITY_LIVE_WINDOW_FRAMES after an
    ## `--ability-at` fire, so overcharge's fire-rate effect and shutter's hold both show up as
    ## a *change over time* in the log — a single before/after pair proves a toggle flipped,
    ## not that it did anything continuous. shots_last_1s is a real count of AnchorSim's own
    ## shot_fired signal, not a computed estimate.
    if view == null or view.sim == null:
        return
    _prune_shot_log()
    var nearest := -1.0
    for u in view.sim.units:
        if bool(u["alive"]) and (nearest < 0.0 or float(u["dist"]) < nearest):
            nearest = float(u["dist"])
    print(("ABILITY-LIVE frame=%d sim_t=%.3f shots_last_%.0fs=%d load=%.1f cap=%.1f " +
           "penalty=%.3f overcharge=%s shutter=%s shutter_queue=%d nearest_alive_dist=%.2f")
        % [_frame, view.sim_time(), RATE_WINDOW_S, _shot_times.size(), view.sim.bus_load(),
           view.sim.capacity(), view.sim.penalty_now(), view.sim.overcharge_active,
           view.sim.shutter_active, view.shutter_queue_size(), nearest])


func _process_press_schedule() -> void:
    if _press_at.is_empty() or view == null or view.sim == null:
        return
    for entry in _press_at:
        if bool(entry.get("fired", false)) or int(entry["frame"]) != _frame:
            continue
        entry["fired"] = true
        var action := String(entry["action"])
        if action == "lf_target":
            var idx: int = view.placed_index_at(view.selected_slot)
            if idx >= 0:
                _dump_target_state("TARGET-BEFORE", idx)
        elif action == "lf_call_wave":
            print("CALL-WAVE-BEFORE frame=%d funds=%d lead_left=%.2f phase=%s"
                % [_frame, view.sim.funds, view.lead_left(), view.phase()])
        var press := InputEventAction.new()
        press.action = action
        press.pressed = true
        view._action_input(press)
        print("PRESS-AT frame=%d action=%s" % [_frame, action])
        if action == "lf_target":
            var idx2: int = view.placed_index_at(view.selected_slot)
            if idx2 >= 0:
                _dump_target_state("TARGET-AFTER", idx2)
        elif action == "lf_call_wave":
            print("CALL-WAVE-AFTER frame=%d funds=%d lead_left=%.2f phase=%s"
                % [_frame, view.sim.funds, view.lead_left(), view.phase()])


func _dump_target_state(tag: String, idx: int) -> void:
    ## Verification-only: what the selected emplacement's targeting priority (data/tuning.json
    ## `targeting`) actually resolves to right now — its mode, what it is aiming at, and every
    ## alive unit within its range, so the aim can be checked against the mode's own rule
    ## (furthest along for "first", nearest hp for "weakest", ...) rather than trusted.
    var p: Dictionary = view.sim.placed[idx]
    var tw: Dictionary = p["tower"]
    var sx: float = float(p["slot"].x)
    var sy: float = float(p["slot"].y)
    var rng: float = float(tw["range"])
    var aim: Variant = p.get("aim", null)
    var mode: String = String(p.get("target_mode", Tuning.targeting_default()))
    print("%s frame=%d slot=(%d,%d) mode=%s aim=%s" % [tag, _frame, int(sx), int(sy), mode, str(aim)])
    for u in view.sim.units:
        if not bool(u["alive"]):
            continue
        var at: Vector2 = view.sim.point_at(float(u["dist"]))
        var dx: float = sx - at.x
        var dy: float = sy - at.y
        if dx * dx + dy * dy <= rng * rng:
            print("  %s-CANDIDATE kind=%s dist=%.2f hp=%.1f" % [tag, String(u["kind"]["id"]), float(u["dist"]), float(u["hp"])])


func _dump_veterancy() -> void:
    ## Verification-only, printed alongside the STATE line at the captured shot frame: every
    ## placed emplacement's kill count, the rank it resolves to under data/tuning.json
    ## `veterancy`'s thresholds, and the multipliers that rank actually carries — so the amber
    ## pip on a turret's base (visible in the screenshot) has printed state behind it rather
    ## than being taken on faith.
    if view.sim == null:
        return
    var ranks: Array = view.sim.veterancy_ranks()
    if ranks.is_empty():
        return
    for p in view.sim.placed:
        var kills: int = int(p.get("kills", 0))
        var best: Dictionary = {}
        for r in ranks:
            if kills >= int(r.get("kills", 0)):
                best = r
        var slot: Vector2i = p["slot"]
        var base_range: float = float(p["tower"]["range"])
        print("VET slot=(%d,%d) tower=%s kills=%d rank=%s damage_mult=%.2f range_mult=%.2f base_range=%.2f"
            % [slot.x, slot.y, String(p["tower"]["id"]), kills, String(best.get("name", "")),
               float(best.get("damage_mult", 1.0)), float(best.get("range_mult", 1.0)), base_range])


func _print_profile_stats() -> void:
    ## `-- --profile <frames>` (CAM-06/CAM-07): mean/p95 milliseconds per draw layer over
    ## every frame actually rendered since boot(), plus how many times drawables() ran its
    ## build body — CAM-07's own acceptance criterion is that number reading 1-per-frame,
    ## not a feeling that sharing helped.
    _print_layer_stats("AnchorView._draw", view.profile_stats())
    if view.glow_layer:
        _print_layer_stats("GlowLayer._draw", view.glow_layer.profile_stats())
    if view.fx_additive:
        _print_layer_stats("FxAdditive._draw", view.fx_additive.profile_stats())
    if view.combat_fx:
        _print_layer_stats("CombatFx._draw", view.combat_fx.profile_stats())
    print("DRAWABLES rebuilds=%d" % view.drawables_rebuild_count())


func _print_layer_stats(label: String, stats: Dictionary) -> void:
    print("PROFILE layer=%s n=%d mean_ms=%.4f p95_ms=%.4f"
        % [label, int(stats["n"]), float(stats["mean"]), float(stats["p95"])])


func _unhandled_input(event: InputEvent) -> void:
    # Bound through the input map so a gamepad reaches the pause menu too (LF-010).
    if event.is_action_pressed("lf_pause"):
        # Pause, rather than leave. A --shot run has no one to pause for and must
        # still exit on its own, which is what _shot_path tests.
        if _shot_path != "":
            get_tree().quit()
        else:
            pause_menu.toggle()


# ────────────────────────────────────────────────────────── camera CLI ──
#
# `--mouse-at`/`--drag`/`--wheel`: synthetic mouse input for CAM-01's pointer-driven paths
# (edge-scroll, zoom-about-cursor, middle-drag pan), which --fixed-fps otherwise has nobody
# to produce. `Viewport.warp_mouse()` — not `Input.warp_mouse()`, which takes *window/screen*
# pixels — moves the real tracked pointer in this viewport's own (stretched, logical) space,
# the same space `get_global_mouse_position()` and every `InputEvent.position` here use, so
# later per-frame reads (edge-scroll's `get_global_mouse_position()`) keep seeing it exactly
# where this function put it. The synthetic events alongside it are what AnchorView's own
# input handlers react to, exactly as `--cursor`'s synthetic `lf_right` presses above call
# `view._action_input()` directly rather than going through the engine's input queue.

func _synth_mouse_at(p: Vector2) -> void:
    get_viewport().warp_mouse(p)
    var m := InputEventMouseMotion.new()
    m.position = p
    m.global_position = p
    m.relative = Vector2.ZERO
    view._unhandled_input(m)


func _synth_drag(start: Vector2, delta: Vector2) -> void:
    get_viewport().warp_mouse(start)
    var press := InputEventMouseButton.new()
    press.button_index = MOUSE_BUTTON_MIDDLE
    press.pressed = true
    press.position = start
    press.global_position = start
    view._unhandled_input(press)

    var move := InputEventMouseMotion.new()
    move.position = start + delta
    move.global_position = start + delta
    move.relative = delta
    view._unhandled_input(move)
    get_viewport().warp_mouse(start + delta)

    var release := InputEventMouseButton.new()
    release.button_index = MOUSE_BUTTON_MIDDLE
    release.pressed = false
    release.position = start + delta
    release.global_position = start + delta
    view._unhandled_input(release)
    print("DRAG from=(%.0f,%.0f) delta=(%.0f,%.0f)" % [start.x, start.y, delta.x, delta.y])


func _synth_wheel(p: Vector2, steps: int) -> void:
    get_viewport().warp_mouse(p)
    var dir := MOUSE_BUTTON_WHEEL_UP if steps > 0 else MOUSE_BUTTON_WHEEL_DOWN
    for _i in range(absi(steps)):
        var tick := InputEventMouseButton.new()
        tick.button_index = dir
        tick.pressed = true
        tick.position = p
        tick.global_position = p
        view._unhandled_input(tick)
    print("WHEEL at=(%.0f,%.0f) steps=%d" % [p.x, p.y, steps])
