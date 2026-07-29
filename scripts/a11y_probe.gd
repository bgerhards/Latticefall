extends RefCounted
class_name A11yProbe
## Walks a live UI tree and writes down every piece of text it is actually drawing.
##
## The accessibility question — "can a player read this?" — cannot be answered from the
## source. Font sizes are literals scattered across four scripts, colours are constants in
## three, and what a label ends up *on top of* is decided by draw order at runtime. The
## same reason the project screenshots itself rather than trusting the code applies here:
## a UI nobody has measured is a UI nobody has checked.
##
## So this reports what the engine has, not what the source says: resolved font size after
## theme overrides, resolved colour, and the on-screen rect in logical viewport pixels.
## `tools/validate/a11y.py` pairs that with the matching screenshot — it samples the real
## composited background under each rect, which is the half of a contrast ratio that no
## amount of reading GDScript can tell you (C_PANEL is 94% opaque over the clear colour,
## so the background behind a label is a blend that exists only once it is drawn).
##
## Deliberately a pure walker with no opinions: it records, `a11y.py` judges. Thresholds
## are a policy that will be argued about, and policy does not belong in the probe.


static func capture(root: Node, viewport: Viewport, meta: Dictionary = {}) -> Dictionary:
	var items: Array = []
	_walk(root, items)
	var out := {
		"viewport": {
			"width": viewport.get_visible_rect().size.x,
			"height": viewport.get_visible_rect().size.y,
		},
		"window": {
			"width": DisplayServer.window_get_size().x,
			"height": DisplayServer.window_get_size().y,
		},
		"content_scale_factor": viewport.get_window().content_scale_factor if viewport.get_window() != null else 1.0,
		"items": items,
	}
	for k in meta:
		out[k] = meta[k]
	return out


static func _walk(n: Node, items: Array) -> void:
	if n is Label:
		_record_label(n as Label, items)
	elif n is Button:
		_record_button(n as Button, items)
	for c in n.get_children():
		_walk(c, items)


static func _record_label(l: Label, items: Array) -> void:
	## A Label with no text draws nothing, and a zero-size rect is not on screen. Both are
	## normal here — the HUD builds its labels empty and fills them on refresh — so they
	## are skipped rather than reported as unreadable text.
	if l.text.strip_edges() == "" or not l.is_visible_in_tree():
		return
	var r := l.get_global_rect()
	if r.size.x <= 0.0 or r.size.y <= 0.0:
		return
	items.append({
		"path": String(l.get_path()),
		"kind": "label",
		"text": l.text.replace("\n", " ⏎ ").substr(0, 160),
		"lines": l.text.split("\n").size(),
		"font_size": l.get_theme_font_size("font_size"),
		"color": _rgba(l.get_theme_color("font_color")),
		"rect": [r.position.x, r.position.y, r.size.x, r.size.y],
		"clip": _clip_of(l),
		"disabled": false,
	})


static func _record_button(b: Button, items: Array) -> void:
	## A disabled button uses `font_color_disabled`, which is a different colour and is
	## exactly the case most likely to fail — the menu greys out every locked anchor. The
	## probe resolves the colour the button will really draw with rather than the enabled
	## one, so those 16 locked buttons are measured as the player sees them.
	if b.text.strip_edges() == "" or not b.is_visible_in_tree():
		return
	var r := b.get_global_rect()
	if r.size.x <= 0.0 or r.size.y <= 0.0:
		return
	## `font_disabled_color`, not `font_color_disabled` — the latter is not a theme item at
	## all, so an override under that name is accepted silently and never drawn. menu.gd
	## carried exactly that typo, which is why every locked anchor rendered in the engine's
	## default grey instead of C_DIM. Same failure mode as a mistyped InputEvent: no error,
	## just a setting that does nothing.
	var key := "font_disabled_color" if b.disabled else "font_color"
	items.append({
		"path": String(b.get_path()),
		"kind": "button",
		"text": b.text.replace("\n", " ⏎ ").substr(0, 160),
		"lines": b.text.split("\n").size(),
		"font_size": b.get_theme_font_size("font_size"),
		"color": _rgba(b.get_theme_color(key)),
		"rect": [r.position.x, r.position.y, r.size.x, r.size.y],
		"clip": _clip_of(b),
		"disabled": b.disabled,
	})


static func _clip_of(c: Control) -> Dictionary:
	## The nearest ancestor that clips this item, and which way it scrolls.
	##
	## Without this the only clipping question that could be asked was "does the rect leave
	## the viewport", which is the right question for a fixed layout and the wrong one for a
	## panel that reflows: an item below the fold of a scroll region has a rect outside the
	## viewport and is not lost — it is one wheel notch away. The judgement of which of those
	## it is belongs in `tools/validate/a11y.py`; this only reports the geometry and the
	## scroll modes, so "reachable by scrolling" cannot be asserted by a script that never
	## looked at whether the region scrolls.
	var p := c.get_parent()
	while p != null:
		if p is Control and (p as Control).clip_contents:
			var box := p as Control
			var r := box.get_global_rect()
			var sv := false
			var sh := false
			if box is ScrollContainer:
				var sc := box as ScrollContainer
				sv = sc.vertical_scroll_mode != ScrollContainer.SCROLL_MODE_DISABLED
				sh = sc.horizontal_scroll_mode != ScrollContainer.SCROLL_MODE_DISABLED
			return {
				"path": String(box.get_path()),
				"rect": [r.position.x, r.position.y, r.size.x, r.size.y],
				"scroll_v": sv,
				"scroll_h": sh,
			}
		p = p.get_parent()
	return {}


static func _rgba(c: Color) -> Array:
	return [c.r, c.g, c.b, c.a]


static func write(path: String, doc: Dictionary) -> void:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		push_error("a11y: cannot write %s (%d)" % [path, FileAccess.get_open_error()])
		return
	f.store_string(JSON.stringify(doc, "  "))
	f.close()
	print("A11Y %s items=%d" % [path, Array(doc["items"]).size()])
