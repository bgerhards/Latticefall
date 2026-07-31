extends RefCounted
## PRC-12: one tokeniser over `OS.get_cmdline_user_args()`, replacing four hand-rolled
## parsers that had drifted into four different dialects — `scripts/main.gd`'s `match`,
## `scripts/menu.gd`'s `if/elif` chain, `scripts/draft.gd`'s `match`, and
## `scripts/display_settings.gd`'s `if/elif` chain. All four now build a `spec` Dictionary
## (flag name -> how many positional values follow) and call `parse()` once.
##
## Not a `class_name` — preloaded instead, the same reasoning `anchor_view.gd` gives for
## `AbilityStateScript` and `hud.gd` gives for `MinimapScript`: a new `class_name` is
## invisible until the editor has imported once, and the symptom is a hang, not an error
## (CLAUDE.md). No instance state is needed, so every entry point here is a `static func`.
##
## The four callers keep their own field names and defaults; this only replaces the
## token-walking loop. `parse()` never raises and never drops a flag silently — an
## unrecognised `--foo` is a **printed warning** (PRC-12's own acceptance criterion), not
## silence, which is what made a mistyped flag on any of the four old parsers look exactly
## like "nothing was asked for".
##
## All four scripts run against the SAME `OS.get_cmdline_user_args()` independently — there
## is no shared argv object one consumes and passes on — so each one parsing only the flags
## *it* acts on would warn on every flag meant for one of the other three (`--anchor
## --ui-scale 2.0` would have `menu.gd` warn about `--ui-scale`, `display_settings.gd` warn
## about `--anchor`, and so on). `ALL_FLAGS` below is the union of every flag any of the four
## recognises, kept here rather than as four independently-drifting copies, and every
## `parse()` call in `main.gd`/`menu.gd`/`draft.gd`/`display_settings.gd` tokenises against
## it — so a flag is only ever warned about when it is unknown to the *whole* CLI surface,
## which is the only case that is actually a typo. Each file still only *reads* the handful
## of flags it cares about afterwards (its own smaller spec documents which those are).
const ALL_FLAGS := {
	"--shot": [1, 2], "--a11y": 1, "--facings": 0, "--lanes": 0, "--dump-placeholder": 0,
	"--anchor": 1, "--autoplay": 0, "--paused": 0, "--select": 1, "--pick": 1,
	"--scroll": 1, "--cursor": 1, "--build": 1, "--difficulty": 1, "--speed": 1,
	"--ability": 1, "--ability-at": 2, "--press-at": 2, "--chain": 1, "--debrief-at": 1,
	"--camera": 3, "--mouse-at": 2, "--drag": 2, "--wheel": 1, "--hold": 1, "--profile": 1,
	"--scenario": 1,
	"--draft": 0, "--shot-menu": [1, 2], "--options": 0,
	"--seed": 1, "--draft-lives": 2, "--focus-card": 1, "--auto-take": 0,
	"--ui-scale": 1, "--display-defaults": 0, "--quiet-window": 0,
	"--edge-scroll": 1,
}

## Returns `{flag: Array[Array]}` — one inner Array per *occurrence* of a repeatable flag
## (`--build a --build b` -> `{"--build": [["a"], ["b"]]}`), so a caller never has to
## special-case "did this repeat" versus "did this appear once". A 0-arity flag (a boolean
## switch, e.g. `--paused`) appears as `{"--paused": [[]]}` per occurrence — test with
## `has()` below, not by reading the array.
##
## `spec` maps a known flag to its arity, either:
## - a plain `int`: exactly that many positional tokens must follow (`{"--camera": 3}`).
## - a 2-element `[min, max]` Array: **at least** `min` and **at most** `max` following
##   tokens are consumed, stopping at the first one that looks like a flag (begins with
##   `--`) or at the end of argv — `{"--shot": [1, 2]}` is `--shot <path>` or
##   `--shot <path> <frame>`, the exact optional-trailing-value shape `main.gd`'s old
##   hand-rolled parser gave `--shot`/`--a11y`'s frame and `--select`'s count.
##
## A flag absent from `spec` is unknown. Values are not type-checked here (a non-numeric
## token where a caller expects an int is simply not a valid int later — see `int_val()`/
## `float_val()` below, which fall back to their own `default` exactly as the hand-rolled
## parsers' `is_valid_int()`/`is_valid_float()` guards did); this function only knows about
## *how many* tokens a flag takes, not their shape.
static func parse(argv: Array, spec: Dictionary) -> Dictionary:
	var out := {}
	var i := 0
	while i < argv.size():
		var tok := String(argv[i])
		if not tok.begins_with("--"):
			# A bare positional with no flag before it. None of the four callers this
			# replaces ever expected one; skipped rather than warned about, since warning
			# on it would fire for every value token consumed below if the walk ever got
			# out of step, which would bury the one warning that matters.
			i += 1
			continue
		if not spec.has(tok):
			push_warning("cli_args: unknown flag %s" % tok)
			print("CLI-WARN unknown flag %s" % tok)
			i += 1
			continue
		var raw_arity: Variant = spec[tok]
		var lo: int
		var hi: int
		if typeof(raw_arity) == TYPE_ARRAY:
			lo = int(raw_arity[0])
			hi = int(raw_arity[1])
		else:
			lo = int(raw_arity)
			hi = lo
		var vals := []
		var k := 0
		while k < hi and i + 1 + k < argv.size() and not String(argv[i + 1 + k]).begins_with("--"):
			vals.append(String(argv[i + 1 + k]))
			k += 1
		if vals.size() < lo:
			push_warning("cli_args: %s expects at least %d value(s), found %d" % [tok, lo, vals.size()])
			print("CLI-WARN %s missing value(s)" % tok)
			i += 1
			continue
		if not out.has(tok):
			out[tok] = []
		out[tok].append(vals)
		i += 1 + vals.size()
	return out


static func has(parsed: Dictionary, flag: String) -> bool:
	return parsed.has(flag) and not parsed[flag].is_empty()


## The `idx`'th positional value of the flag's *first* occurrence, or `default`. Covers the
## common non-repeatable case (`--anchor anchor-07` -> `str_val(parsed, "--anchor", 0)`).
static func str_val(parsed: Dictionary, flag: String, idx: int = 0, default: String = "") -> String:
	if not has(parsed, flag) or idx >= parsed[flag][0].size():
		return default
	return String(parsed[flag][0][idx])


static func int_val(parsed: Dictionary, flag: String, idx: int = 0, default: int = 0) -> int:
	var s := str_val(parsed, flag, idx, "")
	return int(s) if s.is_valid_int() else default


static func float_val(parsed: Dictionary, flag: String, idx: int = 0, default: float = 0.0) -> float:
	var s := str_val(parsed, flag, idx, "")
	return float(s) if s.is_valid_float() else default


## Every occurrence's `idx`'th value, in argv order — for a repeatable flag like `--build`
## or `--ability-at` (`all_str(parsed, "--build", 0)` -> `["mortar-emplacement", "flak-array"]`).
static func all_str(parsed: Dictionary, flag: String, idx: int = 0) -> Array[String]:
	var out: Array[String] = []
	if not parsed.has(flag):
		return out
	for occ in parsed[flag]:
		if idx < occ.size():
			out.append(String(occ[idx]))
	return out
