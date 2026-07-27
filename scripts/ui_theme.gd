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

const SANS := preload("res://assets/fonts/IBMPlexSansCondensed-Regular.ttf")
const SANS_BOLD := preload("res://assets/fonts/IBMPlexSansCondensed-SemiBold.ttf")
const MONO := preload("res://assets/fonts/IBMPlexMono-Regular.ttf")
const MONO_BOLD := preload("res://assets/fonts/IBMPlexMono-SemiBold.ttf")


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


func _face(mono: bool, bold: bool) -> Font:
	if mono:
		return MONO_BOLD if bold else MONO
	return SANS_BOLD if bold else SANS


func report() -> String:
	return "fonts %s" % ("ok" if SANS != null and MONO != null else "MISSING")
