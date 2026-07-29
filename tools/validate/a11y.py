"""Accessibility audit of the rendered interface: contrast, text size, and clipping.

Why this exists
---------------
There is no off-the-shelf accessibility scanner for a game frame. The web tools (axe,
Lighthouse, WAVE) all walk a DOM and read computed CSS; a Godot viewport has neither. So
the measurement is assembled from the two things this project can produce deterministically
— the self-screenshot, and a probe of the live UI tree (`scripts/a11y_probe.gd`) — and the
judging is done here against published criteria rather than against taste.

Pairing the two matters. The probe knows the authoritative foreground colour and the
on-screen rect; only the PNG knows what is *behind* the text. `C_PANEL` is 94% opaque over
the clear colour, so the background under every HUD label is a blend that exists nowhere in
the source. Sampling it out of the composited frame is the only way to get a true ratio.

What is checked
---------------
`contrast`  WCAG 2.1 SC 1.4.3 Contrast (Minimum), level AA. 4.5:1 for normal text, 3:1 for
            large text (>= 24 logical px, per the 18pt threshold). Relative luminance uses
            the WCAG sRGB transfer function.

`size`      A project policy, not a WCAG criterion — WCAG deliberately sets no minimum font
            size, because it addresses legibility through SC 1.4.4 (resize) instead. The
            floor here is 16 logical px in the 1920x1080 design space, which is the long-
            standing default body size for readable text; below 14 is treated as a failure
            rather than a warning. Stated as policy so it can be argued with.

`clipping`  SC 1.4.4 Resize Text, level AA, requires text to survive 200% enlargement
            without loss of content. Run the probe at a raised UI scale and this check
            proves it: any label pushed outside the viewport is loss of content — unless it
            sits inside a region that scrolls, in which case it is one wheel notch away and
            the criterion is satisfied. Scrolling in *two* axes is a failure in its own
            right (SC 1.4.10 Reflow), as is text cut off along an axis its region does not
            scroll. See `clipping()`.

Reported severities are `fail` (violates a criterion) and `warn` (inside the margin).
Exit code is non-zero only when `--strict` is passed, so the gate can adopt this
incrementally rather than turning the whole board red on day one.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# ── policy ──────────────────────────────────────────────────────────────────
# Logical pixels in the 1920x1080 design space. See the module docstring: the size
# thresholds are this project's policy, the contrast thresholds are WCAG 2.1 AA.
SIZE_FAIL = 14.0
SIZE_WARN = 16.0
# WCAG 2.1 calls text "large" at 18pt / 24px (or 14pt bold, which the probe cannot
# distinguish, so the stricter normal-text ratio is applied below 24px).
LARGE_TEXT_PX = 24.0
CONTRAST_NORMAL = 4.5
CONTRAST_LARGE = 3.0
# Inside this margin of the threshold, report `warn` rather than `fail`.
CONTRAST_WARN_MARGIN = 1.0


# ── png ─────────────────────────────────────────────────────────────────────

def read_png(path: Path) -> tuple[int, int, bytes]:
    """Decode an RGBA8 or RGB8 PNG to (width, height, rgb-triples).

    Hand-rolled rather than pulled from Pillow because the project's dependency rule is
    stdlib plus numpy/soundfile, and the renders here are always 8-bit non-interlaced.
    """
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat, width, height, depth, ctype = 8, bytearray(), 0, 0, 0, 0
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        ctag = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        if ctag == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", body[:10])
        elif ctag == b"IDAT":
            idat += body
        elif ctag == b"IEND":
            break
        pos += 12 + length
    if depth != 8 or ctype not in (2, 6):
        raise ValueError(f"{path}: expected 8-bit RGB/RGBA, got depth={depth} type={ctype}")

    nch = 3 if ctype == 2 else 4
    data = zlib.decompress(bytes(idat))
    stride = width * nch
    out = bytearray(width * height * 3)
    prev = bytearray(stride)
    src = 0
    for y in range(height):
        filt = data[src]
        src += 1
        line = bytearray(data[src:src + stride])
        src += stride
        # PNG per-scanline filters, undone in place.
        if filt == 1:
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = line[i - nch] if i >= nch else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                b = prev[i]
                c = prev[i - nch] if i >= nch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        dst = y * width * 3
        for x in range(width):
            out[dst + x * 3:dst + x * 3 + 3] = line[x * nch:x * nch + 3]
        prev = line
    return width, height, bytes(out)


# ── wcag ────────────────────────────────────────────────────────────────────

def _channel(c: float) -> float:
    ## WCAG 2.1 relative-luminance transfer function, on sRGB in 0..1.
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def composite(fg: tuple[float, float, float, float],
              bg: tuple[float, float, float]) -> tuple[float, float, float]:
    """Flatten a translucent text colour onto its background.

    Not a detail: Godot's default `font_disabled_color` is 50% alpha, so a disabled button
    draws at half strength over whatever is behind it. Measuring its declared RGB would
    score a light grey as high-contrast when what the player actually sees is a mid grey on
    a dark panel. The alpha has to be resolved before the ratio means anything.
    """
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1.0 - a) for i in range(3))


# ── sampling ────────────────────────────────────────────────────────────────

@dataclass
class Sample:
    background: tuple[float, float, float]
    coverage: float          # fraction of pixels close to the foreground colour


def sample_background(px: bytes, w: int, h: int, rect: tuple[int, int, int, int],
                      fg: tuple[float, float, float]) -> Sample:
    """The dominant colour behind a run of text.

    Glyphs cover a minority of any text rect, so the mode of the non-glyph pixels is the
    background. Pixels near the foreground colour are excluded first, which drops both the
    glyph cores and most of their antialiased edges; what remains is what the text sits on.
    Quantising to 5 bits per channel groups a gradient or a dithered panel into one bucket,
    then the exact mean of that bucket is returned so the ratio is not computed against a
    rounded colour.
    """
    x0, y0, rw, rh = rect
    x1, y1 = min(w, x0 + rw), min(h, y0 + rh)
    x0, y0 = max(0, x0), max(0, y0)
    if x1 <= x0 or y1 <= y0:
        return Sample((0.0, 0.0, 0.0), 0.0)

    buckets: Counter = Counter()
    sums: dict[tuple[int, int, int], list[float]] = {}
    near_fg = 0
    total = 0
    for y in range(y0, y1):
        row = y * w * 3
        for x in range(x0, x1):
            i = row + x * 3
            r, g, b = px[i] / 255.0, px[i + 1] / 255.0, px[i + 2] / 255.0
            total += 1
            if abs(r - fg[0]) + abs(g - fg[1]) + abs(b - fg[2]) < 0.30:
                near_fg += 1
                continue
            key = (int(r * 31), int(g * 31), int(b * 31))
            buckets[key] += 1
            acc = sums.setdefault(key, [0.0, 0.0, 0.0])
            acc[0] += r
            acc[1] += g
            acc[2] += b
    if not buckets:
        return Sample(fg, 1.0)
    key, n = buckets.most_common(1)[0]
    acc = sums[key]
    return Sample((acc[0] / n, acc[1] / n, acc[2] / n), near_fg / max(total, 1))


# ── audit ───────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    severity: str
    check: str
    path: str
    text: str
    detail: str


def clipping(item: dict, label: str, rect: tuple[float, float, float, float],
             vw: float, vh: float) -> list[Finding]:
    """Is this text lost, or merely below the fold?

    SC 1.4.4 asks for 200% enlargement without loss of *content*. Text that has been pushed
    off the viewport by a layout that cannot reflow is lost. Text below the fold of a region
    that scrolls is not — it is one wheel notch, PageDown or focus step away, and scrolling
    in a single axis is the reflow answer the criterion is written to permit.

    The distinction cannot be made from a rect alone, which is why `scripts/a11y_probe.gd`
    reports the nearest clipping ancestor and its scroll modes. Three things are still
    failures inside a scroll region, and the first two are the ways a scroller is usually
    got wrong:

    * scrolling in **both** axes — SC 1.4.10 Reflow exists to forbid exactly that, because
      reading a line then means scrolling back and forth for every line of it;
    * text cut off along an axis the region does **not** scroll — a 490 px monospaced row in
      a 488 px region is not reachable by any amount of scrolling;
    * a scroll region that is itself outside the viewport, which is loss of content with an
      extra step in front of it.
    """
    rx, ry, rw, rh = rect
    clip = item.get("clip") or None
    if not clip:
        if rx < -1.0 or ry < -1.0 or rx + rw > vw + 1.0 or ry + rh > vh + 1.0:
            return [Finding("fail", "clipping", item["path"], label,
                            f"rect {rx:.0f},{ry:.0f} {rw:.0f}x{rh:.0f} leaves the "
                            f"{vw:.0f}x{vh:.0f} viewport")]
        return []

    cx, cy, cw, ch = (float(v) for v in clip["rect"])
    out: list[Finding] = []
    if cx < -1.0 or cy < -1.0 or cx + cw > vw + 1.0 or cy + ch > vh + 1.0:
        out.append(Finding("fail", "clipping", item["path"], label,
                           f"its scroll region {clip['path']} "
                           f"({cx:.0f},{cy:.0f} {cw:.0f}x{ch:.0f}) leaves the "
                           f"{vw:.0f}x{vh:.0f} viewport"))
    if clip["scroll_v"] and clip["scroll_h"]:
        out.append(Finding("fail", "clipping", item["path"], label,
                           f"{clip['path']} scrolls in both axes (SC 1.4.10 Reflow "
                           f"allows one)"))
    if not clip["scroll_h"] and (rx < cx - 1.0 or rx + rw > cx + cw + 1.0):
        out.append(Finding("fail", "clipping", item["path"], label,
                           f"rect {rx:.0f}..{rx + rw:.0f} is cut off by {clip['path']} "
                           f"({cx:.0f}..{cx + cw:.0f}), which does not scroll sideways"))
    if not clip["scroll_v"] and (ry < cy - 1.0 or ry + rh > cy + ch + 1.0):
        out.append(Finding("fail", "clipping", item["path"], label,
                           f"rect {ry:.0f}..{ry + rh:.0f} is cut off by {clip['path']} "
                           f"({cy:.0f}..{cy + ch:.0f}), which does not scroll vertically"))
    return out


def audit(report: Path, shot: Path | None) -> tuple[list[Finding], dict]:
    doc = json.loads(report.read_text())
    vw = float(doc["viewport"]["width"])
    vh = float(doc["viewport"]["height"])
    items = doc["items"]

    px = w = h = None
    if shot is not None and shot.exists():
        w, h, px = read_png(shot)
    # The screenshot is the window, the rects are the logical viewport. At the project's
    # default that is 1440x810 against 1920x1080 — the 0.75 noted in CLAUDE.md.
    sx = (w / vw) if w else 1.0
    sy = (h / vh) if h else 1.0

    findings: list[Finding] = []
    measured = []

    for it in items:
        size = float(it["font_size"])
        rgba = tuple(float(v) for v in it["color"])
        fg = rgba[:3]
        rx, ry, rw, rh = (float(v) for v in it["rect"])
        label = it["text"][:56]

        # ── size ──
        if size < SIZE_FAIL:
            findings.append(Finding(
                "fail", "size", it["path"], label,
                f"{size:.0f} logical px (floor {SIZE_FAIL:.0f}, target {SIZE_WARN:.0f}); "
                f"{size * sy:.1f} px at the captured {w}x{h} window"))
        elif size < SIZE_WARN:
            findings.append(Finding(
                "warn", "size", it["path"], label,
                f"{size:.0f} logical px (target {SIZE_WARN:.0f})"))

        # ── contrast ──
        ratio = None
        if px is not None:
            s = sample_background(
                px, w, h,
                (int(rx * sx), int(ry * sy), max(1, int(rw * sx)), max(1, int(rh * sy))),
                fg)
            # A rect that is almost entirely glyph gives no reliable background reading;
            # reporting a ratio against the foreground itself would be a fabricated number.
            if s.coverage < 0.90:
                drawn = composite(rgba, s.background)
                ratio = contrast(drawn, s.background)
                need = CONTRAST_LARGE if size >= LARGE_TEXT_PX else CONTRAST_NORMAL
                kind = "large" if size >= LARGE_TEXT_PX else "normal"
                alpha_note = "" if rgba[3] >= 0.999 else f" at {rgba[3]:.2f} alpha"
                if ratio < need:
                    findings.append(Finding(
                        "fail", "contrast", it["path"], label,
                        f"{ratio:.2f}:1 — {_hex(drawn)}{alpha_note} on "
                        f"{_hex(s.background)} (WCAG AA needs {need}:1 for {kind} text)"))
                elif ratio < need + CONTRAST_WARN_MARGIN:
                    findings.append(Finding(
                        "warn", "contrast", it["path"], label,
                        f"{ratio:.2f}:1 — {_hex(drawn)}{alpha_note} on "
                        f"{_hex(s.background)} (AA {need}:1, AAA 7:1)"))

        # ── clipping (SC 1.4.4 / 1.4.10) ──
        findings.extend(clipping(it, label, (rx, ry, rw, rh), vw, vh))

        measured.append({"path": it["path"], "text": label, "size": size,
                         "contrast": ratio, "disabled": bool(it.get("disabled"))})

    summary = {
        "scene": doc.get("scene", "?"),
        "anchor": doc.get("anchor"),
        "viewport": [vw, vh],
        "window": [doc["window"]["width"], doc["window"]["height"]],
        "items": len(items),
        "fail": sum(1 for f in findings if f.severity == "fail"),
        "warn": sum(1 for f in findings if f.severity == "warn"),
        "min_size": min((m["size"] for m in measured), default=0),
        "min_contrast": min((m["contrast"] for m in measured
                             if m["contrast"] is not None), default=None),
        "measured": measured,
    }
    return findings, summary


def _hex(c: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in c)


# ── cli ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("report", type=Path, help="JSON written by --a11y")
    ap.add_argument("--shot", type=Path, help="the PNG captured on the same frame")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero when any check fails")
    ap.add_argument("--quiet", action="store_true", help="summary only")
    ap.add_argument("--all", action="store_true",
                    help="list every measured item, not just the ones that fail")
    args = ap.parse_args()

    findings, summary = audit(args.report, args.shot)

    head = (f"{summary['scene']}"
            + (f" {summary['anchor']}" if summary["anchor"] else "")
            + f"  ·  {summary['items']} text items"
            + f"  ·  viewport {summary['viewport'][0]:.0f}x{summary['viewport'][1]:.0f}"
            + f"  ·  window {summary['window'][0]}x{summary['window'][1]}")
    print(head)
    print("-" * len(head))

    if args.all:
        print(f"{'size':>5}  {'ratio':>7}  text")
        for m in sorted(summary["measured"], key=lambda m: (m["size"], m["text"])):
            r = f"{m['contrast']:.2f}:1" if m["contrast"] is not None else "     —"
            flag = " (disabled)" if m["disabled"] else ""
            print(f"{m['size']:>5.0f}  {r:>7}  {m['text']!r}{flag}")
        print()

    if not args.quiet:
        order = {"fail": 0, "warn": 1}
        for f in sorted(findings, key=lambda f: (order[f.severity], f.check, f.path)):
            tag = "FAIL" if f.severity == "fail" else "warn"
            print(f"[{tag}] {f.check:<9} {f.text!r}")
            print(f"         {f.detail}")

    mc = summary["min_contrast"]
    tail = f" · worst contrast {mc:.2f}:1" if mc is not None else ""
    print()
    print(f"{summary['fail']} failing · {summary['warn']} warnings · "
          f"smallest text {summary['min_size']:.0f}px{tail}")

    if args.strict and summary["fail"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
