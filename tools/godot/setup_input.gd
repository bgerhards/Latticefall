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
	## The instrument panels scroll once the interface scale shrinks the logical viewport
	## below what they need — 150% and above. Mouse wheel reaches them for free, but a
	## keyboard or gamepad player would otherwise be unable to read what is below the fold,
	## and unreadable-by-one-input-method is loss of content under SC 1.4.4. The right stick
	## is otherwise unused for anything but its click (lf_power).
	"lf_panel_up":   {"keys": [KEY_PAGEUP], "axis": [JOY_AXIS_RIGHT_Y, -1.0]},
	"lf_panel_down": {"keys": [KEY_PAGEDOWN], "axis": [JOY_AXIS_RIGHT_Y, 1.0]},

	## LF-057: hides the instrument column and threat panel so the board is visible under
	## them at high interface scale — at 200% they are 420 + 528 px of a 960 px design
	## space. Every action either panel exposes (build, sell, upgrade, power, tower select,
	## targeting, abilities) already has a keyboard/gamepad path elsewhere in this table
	## that does not touch a HUD widget, so hiding them costs only the readouts. PADDLE1
	## rather than a face/shoulder button because all of those are already spoken for; it
	## is the same trade already made for lf_target (MISC1) and lf_ability_3 (TOUCHPAD) —
	## present only on Xbox Elite/DualSense Edge-class pads, with the keyboard as the
	## binding every pad still has.
	"lf_hud_toggle": {"keys": [KEY_H], "buttons": [JOY_BUTTON_PADDLE1]},

	## Pacing and the bindstone abilities (data/tuning.json `pacing`/`abilities`) — the
	## answer to "slow" and to "nothing to press". Bound on keys the board actions above do
	## not already use, with a gamepad mapping on whatever is left over: BACK/select, the
	## left stick's own click, MISC1 (present on Xbox Series/DualSense-class pads; a keyboard
	## always still reaches everything here) and the analogue triggers, which are otherwise
	## unused by this game entirely.
	"lf_speed_cycle": {"keys": [KEY_QUOTELEFT], "buttons": [JOY_BUTTON_LEFT_STICK]},
	"lf_call_wave":   {"keys": [KEY_C], "buttons": [JOY_BUTTON_BACK]},
	"lf_target":      {"keys": [KEY_T], "buttons": [JOY_BUTTON_MISC1]},
	"lf_ability_1":   {"keys": [KEY_1], "axis": [JOY_AXIS_TRIGGER_LEFT, 1.0]},
	"lf_ability_2":   {"keys": [KEY_2], "axis": [JOY_AXIS_TRIGGER_RIGHT, 1.0]},
	"lf_ability_3":   {"keys": [KEY_3], "buttons": [JOY_BUTTON_TOUCHPAD]},

	## CAM-01: the board camera. lf_next/lf_prev already took both shoulders (tower cycling),
	## so zoom goes on the right stick's X axis instead — it is otherwise unused; Y already
	## drives lf_panel_up/down above. Held either direction zooms continuously, the same feel
	## as the mouse wheel a step at a time. lf_camera_reset gets PADDLE2 on the same trade
	## lf_hud_toggle already made for PADDLE1: no face or shoulder button is free, this one is
	## present only on Xbox Elite/DualSense Edge-class pads, and the keyboard binding is the
	## one every pad still has.
	"lf_zoom_in":      {"keys": [KEY_EQUAL], "axis": [JOY_AXIS_RIGHT_X, 1.0]},
	"lf_zoom_out":     {"keys": [KEY_MINUS], "axis": [JOY_AXIS_RIGHT_X, -1.0]},
	"lf_camera_reset": {"keys": [KEY_HOME], "buttons": [JOY_BUTTON_PADDLE2]},

	## CAM-04: focuses the minimap for keyboard/gamepad camera navigation. While active,
	## hud.gd claims lf_up/lf_down/lf_left/lf_right for region-stepping the camera instead of
	## letting them reach the board cursor — see hud.gd's toggle_minimap_focus() for why that
	## routing lives there rather than here. PADDLE3 on the same trade lf_hud_toggle
	## (PADDLE1) and lf_camera_reset (PADDLE2) already made: no face or shoulder button is
	## free, this one is present only on Xbox Elite/DualSense Edge-class pads, and the
	## keyboard binding is the one every pad still has.
	"lf_minimap_focus": {"keys": [KEY_M], "buttons": [JOY_BUTTON_PADDLE3]},
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
