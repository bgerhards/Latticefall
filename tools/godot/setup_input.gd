extends SceneTree
## Define the input map in project.godot, keyboard and gamepad together.
##
## Run headlessly, not by hand:
##   /Applications/Godot.app/Contents/MacOS/Godot --headless --path . --script tools/godot/setup_input.gd
##
## Why a script rather than editing project.godot directly: an action's events are
## serialized `Object(InputEventKey, ...)` literals with a dozen fields each, and a typo in
## that block does not fail loudly — it produces an action that silently never fires. Godot
## writes the format it reads, so let it.
##
## Idempotent. Every action is erased and rebuilt from the table below, so re-running after
## changing a binding converges rather than accumulating duplicates.
##
## Actions are prefixed `lf_` to keep them clear of Godot's built-in `ui_*`, which the
## menu's Control focus navigation still uses.

const DEADZONE := 0.35

## action -> {keys, buttons, axis}
## `axis` is [JoyAxis, sign] for stick directions, so the board cursor works on the left
## stick as well as the d-pad without a second set of actions.
const ACTIONS := {
	"lf_pause":    {"keys": [KEY_ESCAPE], "buttons": [JOY_BUTTON_START]},
	"lf_confirm":  {"keys": [KEY_SPACE, KEY_ENTER], "buttons": [JOY_BUTTON_A]},
	"lf_cancel":   {"keys": [KEY_BACKSPACE], "buttons": [JOY_BUTTON_B]},
	"lf_build":    {"keys": [KEY_E], "buttons": [JOY_BUTTON_A]},
	"lf_sell":     {"keys": [KEY_Q], "buttons": [JOY_BUTTON_X]},
	"lf_upgrade":  {"keys": [KEY_R], "buttons": [JOY_BUTTON_Y]},
	"lf_power":    {"keys": [KEY_F], "buttons": [JOY_BUTTON_RIGHT_STICK]},
	"lf_next":     {"keys": [KEY_TAB], "buttons": [JOY_BUTTON_RIGHT_SHOULDER]},
	"lf_prev":     {"keys": [KEY_SHIFT], "buttons": [JOY_BUTTON_LEFT_SHOULDER]},
	"lf_up":       {"keys": [KEY_W, KEY_UP], "buttons": [JOY_BUTTON_DPAD_UP],
					"axis": [JOY_AXIS_LEFT_Y, -1.0]},
	"lf_down":     {"keys": [KEY_S, KEY_DOWN], "buttons": [JOY_BUTTON_DPAD_DOWN],
					"axis": [JOY_AXIS_LEFT_Y, 1.0]},
	"lf_left":     {"keys": [KEY_A, KEY_LEFT], "buttons": [JOY_BUTTON_DPAD_LEFT],
					"axis": [JOY_AXIS_LEFT_X, -1.0]},
	"lf_right":    {"keys": [KEY_D, KEY_RIGHT], "buttons": [JOY_BUTTON_DPAD_RIGHT],
					"axis": [JOY_AXIS_LEFT_X, 1.0]},
}


func _init() -> void:
	for action in ACTIONS:
		var path: String = "input/" + String(action)
		var spec: Dictionary = ACTIONS[action]
		var events: Array = []

		for key in spec.get("keys", []):
			var k := InputEventKey.new()
			k.physical_keycode = key      # physical, so WASD stays WASD on AZERTY
			events.append(k)

		for button in spec.get("buttons", []):
			var b := InputEventJoypadButton.new()
			b.button_index = button
			events.append(b)

		if spec.has("axis"):
			var m := InputEventJoypadMotion.new()
			m.axis = spec["axis"][0]
			m.axis_value = spec["axis"][1]
			events.append(m)

		ProjectSettings.set_setting(path, {"deadzone": DEADZONE, "events": events})
		print("%-12s %d event(s)" % [action, events.size()])

	var err := ProjectSettings.save()
	print("saved project.godot err=%d  %d actions" % [err, ACTIONS.size()])
	quit(0 if err == OK else 1)
