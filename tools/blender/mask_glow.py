#!/usr/bin/env python3
"""
Turn glow renders into a proper additive layer.

The compositor writes an opaque frame: the Glare output has alpha 1 everywhere, so
drawing it with additive blending lifts the whole 256x256 cell and the board fills
with bright rectangles. An additive layer's alpha must be its own brightness.

    .venv/bin/python tools/blender/mask_glow.py

Idempotent at the *pixel* level — running it twice computes the same alpha from the
same colour both times, so the array `mask()` produces on pass two is identical to
pass one. That was never the problem (LF-071): what was not idempotent was the
*write*. PIL's `Image.save()` does not reproduce identical bytes for identical pixel
content across separate invocations (compression heuristics, library version, etc.),
so re-running this after touching one asset used to re-save all ~200 other glow PNGs
with pixel-identical, byte-different files — confirmed by git-lfs smudge plus a numpy
compare on `hollow_column_y045_glow.png`: max abs pixel diff 0, sha256 differs from
HEAD. A one-asset re-render turned into a full-repo LFS diff and `git status` stopped
being readable. The fix: decode what is already on disk as an array, compare it
against the freshly computed array, and skip `save()` when they match — comparing
*content*, never encoded bytes, is what makes the comparison itself immune to the same
non-determinism this is fixing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "assets" / "renders" / "sprites.json"
# Below this the pixel is bloom spill on empty background, not emission.
FLOOR = 0.06


def mask(path: Path) -> tuple[int, int, bool]:
    """Compute the masked RGBA for `path` and write it back only if it actually
    changed. Returns (opaque_px_before, emissive_px_after, written).

    `current` is read once, as uint8 — the exact bytes on disk — and reused for both
    the `before` count and the final equality check, rather than round-tripping through
    the [0,1] float space a second time: `float(x)/255.0*255.0` is not guaranteed to
    reproduce `x` exactly, and a rounding artefact there would masquerade as a real
    content change and defeat the whole point of this fix.
    """
    with Image.open(path) as im:
        current = np.asarray(im.convert("RGBA"), dtype=np.uint8)
    rgb = current[:, :, :3].astype(np.float32) / 255.0
    lum = rgb.max(axis=2)
    lum = np.clip((lum - FLOOR) / (1.0 - FLOOR), 0.0, 1.0)
    out = np.dstack([rgb, lum])
    out_u8 = (out * 255.0).astype(np.uint8)
    before = int((current[:, :, 3] > 127).sum())
    after = int((out_u8[:, :, 3] > 127).sum())
    written = not np.array_equal(out_u8, current)
    if written:
        Image.fromarray(out_u8, "RGBA").save(path)
    return before, after, written


def main() -> int:
    if not MANIFEST.exists():
        print("no sprite manifest — run the Blender render first", file=sys.stderr)
        return 1
    doc = json.loads(MANIFEST.read_text())
    n, n_written, tot_before, tot_after = 0, 0, 0, 0
    for name, yaws in doc["sprites"].items():
        for _yaw, passes in yaws.items():
            p = ROOT / passes["glow"]
            if not p.exists():
                continue
            b, a, written = mask(p)
            tot_before += b
            tot_after += a
            n += 1
            n_written += int(written)
    print(f"masked {n} glow images · {n_written} rewritten (LF-071: only ones that "
          f"actually changed) · opaque px {tot_before} -> emissive px {tot_after} "
          f"({100.0 * tot_after / max(tot_before, 1):.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
