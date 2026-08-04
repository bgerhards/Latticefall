extends Node
## Autoload `Ui`. One place that decides what the interface is made of.
##
## Latticefall's UI is meant to read as instrumentation: condensed sans for labels and
## chrome, monospace anywhere a *number* appears, because a proportional digit set makes
## a live megawatt readout jitter as it counts. Both are IBM Plex, vendored under the
## SIL Open Font Licence in assets/fonts/ (LF-008) rather than depending on a system font
## that will not exist on the next machine.
##
## Everything here is a preload, so a missing or unimported font is a load error at boot
## rather than a silent fallback to the engine's default face — which is exactly how this
## went unnoticed for as long as it did.
##
## Sizes and colours are named, not literal, because both are accessibility policy and
## policy scattered across five scripts cannot be audited. `tools/validate/a11y.py`
## measures the rendered result against WCAG 2.1 AA; the constants below are what it is
## measuring, and every one of them was chosen by solving for a contrast ratio against the
## real composited panel colour rather than by eye.

const SANS := preload("res://assets/fonts/IBMPlexSansCondensed-Regular.ttf")
const SANS_BOLD := preload("res://assets/fonts/IBMPlexSansCondensed-SemiBold.ttf")
const MONO := preload("res://assets/fonts/IBMPlexMono-Regular.ttf")
const MONO_BOLD := preload("res://assets/fonts/IBMPlexMono-SemiBold.ttf")

# ── type ladder ─────────────────────────────────────────────────────────────
# Logical pixels in the 1920x1080 design space. The floor is 16: the interface used to run
# on an 11-13 px ladder, which is 8-10 physical px in the project's default 1440x810
# window, and 15 of the game screen's 27 text items failed the size floor outright.
#
# Hierarchy is carried by weight, colour and case rather than by size, which is why the
# caption and body sizes are equal. An instrument panel distinguishes a field label from
# its value by treatment, not by shrinking the label until it cannot be read.
const SIZE_CAPTION := 16     ## uppercase field labels — "REACTOR BUS", "INCOMING"
const SIZE_BODY := 16        ## datasheet rows, notes, threat list, buttons
const SIZE_STAT := 18        ## live status lines the player reads at a glance
const SIZE_LEAD := 22        ## panel titles
const SIZE_READOUT := 28     ## the bus readout
const SIZE_BANNER := 44      ## end-of-anchor verdict
const SIZE_DISPLAY := 64     ## the title screen

# ── palette ─────────────────────────────────────────────────────────────────
# Contrast against the three backgrounds text actually lands on here — the 94%-opaque
# panel (#152024 composited), the bare board (#0e1417) and a default button face
# (#151718). Ratios quoted are against the panel, the worst of the three.
#
# C_MUTED, C_ALERT and C_DIM were all raised: at their old values they measured 4.90:1,
# 4.00:1 and 2.04:1, so secondary text sat on the AA boundary, every alert failed it, and
# a locked anchor was barely visible at all.
const C_VERD := Color(0.42, 0.74, 0.65)      ## 7.46:1 — held, online, friendly
const C_AMBER := Color(0.91, 0.64, 0.24)     ## 7.72:1 — armed, attention, cost
const C_ALERT := Color(0.95, 0.44, 0.36)     ## 5.75:1 — brownout, losing, incoming
const C_MUTED := Color(0.591, 0.675, 0.687)  ## 7.00:1 — captions and secondary text
const C_DIM := Color(0.458, 0.543, 0.560)    ## 4.60:1 — locked and unavailable
const C_BONE := Color(0.86, 0.89, 0.88)      ## 12.73:1 — primary text
const C_PANEL := Color(0.086, 0.13, 0.145, 0.94)
const C_OVERLAY := Color(0.07, 0.10, 0.115, 0.96)
const C_RULE := Color(0.591, 0.675, 0.687, 0.28)

# ── layout ──────────────────────────────────────────────────────────────────
# The instrument column's geometry, here rather than in hud.gd because the dialog panel
# has to know where the column ends. The dialog used to span the full width at the bottom
# of the screen, which stole 127 px from the bottom of a column that needs every pixel at
# the 16 px ladder — the emplacement note was clipped to two lines mid-sentence. Starting
# the dialog beside the column instead costs the dialog nothing and gives the column the
# full height of the viewport.
const COL_X := 16.0
const COL_W := 420.0
const PAD := 12.0
## Every instrument panel reserves this much of its own width for a vertical scrollbar,
## whether or not one is showing. `ScrollContainer.get_v_scroll_bar()` reports a minimum
## width of 8 on this build — measured, not assumed. Reserving it unconditionally is what
## keeps a panel's inner geometry identical at every interface scale: the alternative is a
## layout that shifts by 8 px the moment the content grows past the fold.
const SCROLL_GUTTER := 8.0
const INNER_W := COL_W - SCROLL_GUTTER - PAD * 2.0
## The threat panel mirrors the column on the right edge. 490 px is the widest row the data
## can produce — the longest trait row `enemies.json` can generate is 51 monospaced
## characters at 16 px — plus padding on both sides and the scrollbar gutter.
const THREAT_W := 528.0
const THREAT_INNER_W := THREAT_W - SCROLL_GUTTER - PAD * 2.0
## A floor, not a target: below this the dialog is not a line of prose, it is a word ladder.
const DIALOG_MIN_W := 200.0

## LF-247. The playfield's own floor, and the reason the threat panel is allowed to undock.
##
## Derived, not picked: the board may never be narrower than the *narrowest instrument panel
## this project already treats as usable*. COL_W was solved for the 16 px type ladder and is
## the smallest width anything in this interface is permitted to be; a board given less than
## that is being told it matters less than the column beside it, which is backwards — the
## column is a readout, the board is the game.
##
## What it was before: `COL_W + THREAT_W` are 948 px that do NOT shrink when the interface
## scale shrinks the logical viewport, so the strip between them was 940 px at 100%, 300 px
## at 150%, 117 px at 175% and **4 px at 200%** — the panels covered the board completely and
## every accessibility check still passed, because a11y.py audits text items and a playfield
## is not a text item. `tools/check.py`'s `playfield width` check now asserts this bound at
## every scale in `Display.UI_SCALES`.
const PLAYFIELD_MIN_W := COL_W

# ── board readouts ──────────────────────────────────────────────────────────
# Presentation drawn directly on the board rather than inside an instrument panel, but
# still accessibility policy per decisions 045/046 — the geometry is named here rather
# than as a literal beside a `draw_arc()` call. ART-06's per-emplacement reload readout
# is the first user; its colour is `C_AMBER` above (unchanged — "armed, attention, cost"
# already describes a gun mid-reload) rather than a new one.
const BOARD_ARC_R := 14.0    ## px. Small enough to sit inside a placed emplacement's own
                              ## tile without reaching the 1.0-scale selection diamond's
                              ## edge (roughly 32px half-height) or the rank pip (LF-… at
                              ## (-26,-48) from the sprite's screen point).
const BOARD_ARC_W := 3.0     ## px stroke. Thinner than the 2px selection ring would read
                              ## as noise at zoom-out; this is thin enough not to compete
                              ## with it while still being visible at 100%.


func scroller() -> ScrollContainer:
	## The one way a panel is allowed to hold more than fits.
	##
	## Vertical only, and that is the whole point. WCAG 2.1 SC 1.4.4 asks for 200% without
	## loss of content, and at 200% the design space is 960x540 — the instrument column alone
	## wants 893 px of it plus 98 px of pinned controls. Content reached by scrolling in
	## *one* axis is not lost; content
	## that needs scrolling in two is what SC 1.4.10 exists to forbid, so the horizontal mode
	## is DISABLED rather than left to size itself out of trouble. `tools/validate/a11y.py`
	## checks both of those, which is why `scripts/a11y_probe.gd` reports the clip region and
	## its scroll axes for every label.
	##
	## `follow_focus` is what makes a scrolled panel reachable from the keyboard and gamepad
	## wherever the panel's own controls take focus. The HUD column has no focus chain — its
	## verbs are bound to `lf_sell`/`lf_upgrade`/`lf_power` on the board — so it is scrolled
	## by `lf_panel_up`/`lf_panel_down` instead. See hud.gd.
	var s := ScrollContainer.new()
	s.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	s.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_AUTO
	s.follow_focus = true
	s.clip_contents = true
	return s


func threat_docked(vp: Vector2) -> bool:
	## LF-247. Whether the threat panel *reserves* strip width (docked, at its right edge) or
	## floats over the board as a toggled overlay that reserves nothing (undocked).
	##
	## Derived from the bound, not from a scale number: dock it exactly while docking it still
	## leaves PLAYFIELD_MIN_W of board. That works out at about 137% today, but writing 1.37
	## here would be a constant that stops being true the moment COL_W or THREAT_W moves — the
	## same class of mistake as the hardcoded 1524 the threat panel's own x used to be.
	##
	## Uses COL_X rather than `gutter(vp)` to avoid mutual recursion, and it is not an
	## approximation: whenever this returns true, vp.x is at least COL_W + THREAT_W +
	## 2*COL_X + PLAYFIELD_MIN_W = 1400, at which width `gutter()` below is already clamped
	## to COL_X anyway. So the two agree by construction wherever the answer is `true`.
	return vp.x - 2.0 * COL_X - COL_W - THREAT_W >= PLAYFIELD_MIN_W


func reserved_w(vp: Vector2) -> float:
	## How much of the viewport's width the instrument panels take away from the board.
	## The column always does; the threat panel only while it is docked.
	return COL_W + (THREAT_W if threat_docked(vp) else 0.0)


func gutter(vp: Vector2) -> float:
	## The margin around the instrument panels, derived from the viewport rather than fixed
	## at COL_X. At 200% interface scale the design space is 960 px wide and the column plus a
	## docked threat panel are 420 + 528 of it, so a 16 px margin on each side does not exist
	## to be spent — the panels would overlap by 20 px and the build bar would be drawn under
	## the threat list. Squeezing the margin is the cheapest thing to give up; nothing in
	## either panel moves. Since LF-247 the threat panel undocks before it ever gets that
	## tight, so the 4 px floor is now reached only by a scale the options screen does not
	## offer (`-- --ui-scale` clamps at 3.0); it stays because a floor that only triggers off
	## the offered ladder is still cheaper than a crash there.
	return clampf((vp.x - reserved_w(vp)) / 3.0, 4.0, COL_X)


func strip_w(vp: Vector2) -> float:
	## The width of the playfield — the free region between the instrument column and
	## whatever is reserved on the right. The one definition; `anchor_view.gd`'s
	## `_strip_geometry()` reads it rather than re-deriving it, and `tools/check.py`'s
	## `playfield width` check mirrors this exact expression and fails if this line changes
	## without the mirror.
	return maxf(vp.x - 2.0 * gutter(vp) - reserved_w(vp), 1.0)


func dialog_h() -> float:
	## A speaker line and two wrapped lines of dialog, measured from the type it holds.
	return line_h(SIZE_CAPTION, false) + 2.0 * line_h(SIZE_STAT, false) + 26.0


func dialog_rect(vp: Vector2) -> Rect2:
	## Where the dialog panel sits: a band along the bottom, from the right edge of the
	## instrument column to the right edge of the viewport. Here rather than in
	## dialog_view.gd because the threat panel has to reserve the same band — the dialog is
	## on a later CanvasLayer and draws over anything it lands on, and at 960x540 what it
	## lands on is the threat panel's air warning. "Where the dialog is" and "how tall the
	## threat panel may be" are one fact, so they are one function.
	var g := gutter(vp)
	var h := dialog_h()
	var x := g + COL_W + 8.0
	var w := vp.x - x - g
	return Rect2(x, vp.y - h - g, maxf(w, DIALOG_MIN_W), h)


func label(text: String, size: int, col: Color, mono: bool = false,
		bold: bool = false) -> Label:
	## The one way a Label gets made. Callers pass intent (mono for numbers, bold for
	## headings) rather than font resources, so a face swap is a change to this file.
	var l := Label.new()
	l.text = text
	l.add_theme_font_override("font", _face(mono, bold))
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", col)
	return l


func style(c: Control, size: int, mono: bool = false, bold: bool = false) -> void:
	## Apply the face to something already built — buttons, mostly.
	c.add_theme_font_override("font", _face(mono, bold))
	c.add_theme_font_size_override("font_size", size)


func button(text: String, size: int = SIZE_BODY, bold: bool = false) -> Button:
	## Buttons get their disabled colour set explicitly. Godot's default
	## `font_disabled_color` is a light grey at **0.5 alpha**, which composites to 4.15:1
	## on the panel here and fails AA — a disabled control still has to be readable to
	## explain why it is disabled. Note the theme item is `font_disabled_color`; the
	## plausible-looking `font_color_disabled` is not a theme item at all, and an override
	## under that name is accepted in silence and never drawn. menu.gd carried exactly that
	## typo, so every locked anchor ignored its intended colour.
	var b := Button.new()
	b.text = text
	style(b, size, false, bold)
	b.add_theme_color_override("font_color", C_BONE)
	b.add_theme_color_override("font_disabled_color", C_DIM)
	return b


func line_h(size: int, mono: bool = true) -> float:
	## Measured from the font, not assumed. A Label stacks its `line_spacing` theme constant
	## (3 by default) on top of the face's own height, so an 11 px mono line is about 19 px,
	## not 15 — and a guessed 15 drew the threat footer on top of the last two rows of the
	## unit list.
	return (MONO if mono else SANS).get_height(size) + 3.0


func wrapped_lines(text: String, width: float, size: int, mono: bool = false) -> int:
	## How many lines a wrapped label will really take. Asked of the font rather than
	## estimated from character counts, for the same reason `line_h` is: the emplacement
	## note used to be given a fixed box and `clip_text`, and `clip_text` clips horizontally
	## only — a note that did not fit was silently cut mid-sentence and its last line was
	## drawn over the SELL button. Reserving the measured height means nothing is cut.
	if text.strip_edges() == "":
		return 0
	var f := _face(mono, false)
	var h := f.get_multiline_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, width, size).y
	return maxi(1, ceili(h / maxf(f.get_height(size), 1.0)))


func _face(mono: bool, bold: bool) -> Font:
	if mono:
		return MONO_BOLD if bold else MONO
	return SANS_BOLD if bold else SANS


func report() -> String:
	return "fonts %s" % ("ok" if SANS != null and MONO != null else "MISSING")
