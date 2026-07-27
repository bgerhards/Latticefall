#!/usr/bin/env python3
"""
Turn glow renders into a proper additive layer.

The compositor writes an opaque frame: the Glare output has alpha 1 everywhere, so
drawing it with additive blending lifts the whole 256x256 cell and the board fills
with bright rectangles. An additive layer's alpha must be its own brightness.

    .venv/bin/python tools/blender/mask_glow.py

Idempotent — running it twice changes nothing, because the second pass computes the
same alpha from the same colour.
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


def mask(path: Path) -> tuple[int, int]:
    a = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float32) / 255.0
    rgb = a[:, :, :3]
    lum = rgb.max(axis=2)
    lum = np.clip((lum - FLOOR) / (1.0 - FLOOR), 0.0, 1.0)
    out = np.dstack([rgb, lum])
    before = int((a[:, :, 3] > 0.5).sum())
    after = int((lum > 0.5).sum())
    Image.fromarray((out * 255.0).astype(np.uint8), "RGBA").save(path)
    return before, after


def main() -> int:
    if not MANIFEST.exists():
        print("no sprite manifest — run the Blender render first", file=sys.stderr)
        return 1
    doc = json.loads(MANIFEST.read_text())
    n, tot_before, tot_after = 0, 0, 0
    for name, yaws in doc["sprites"].items():
        for _yaw, passes in yaws.items():
            p = ROOT / passes["glow"]
            if not p.exists():
                continue
            b, a = mask(p)
            tot_before += b
            tot_after += a
            n += 1
    print(f"masked {n} glow images · opaque px {tot_before} -> emissive px {tot_after} "
          f"({100.0 * tot_after / max(tot_before, 1):.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
