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
const INNER_W := COL_W - PAD * 2.0


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


func _face(mono: bool, bold: bool) -> Font:
	if mono:
		return MONO_BOLD if bold else MONO
	return SANS_BOLD if bold else SANS


func report() -> String:
	return "fonts %s" % ("ok" if SANS != null and MONO != null else "MISSING")
