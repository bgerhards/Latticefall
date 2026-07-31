"""CAM-05 evidence: how legible is a sprite once the board is zoomed out.

Why this exists: CAM-01 (the board camera) needs to know how small a sprite can get
before one enemy cannot be told from another. The sprite library is a fixed 256px
atlas cell rendered at one orthographic scale (decisions 002/017) — it does not zoom,
the *display* of it does. This script never touches Blender and never re-renders
anything; it reads only the already-committed albedo renders under assets/renders/
(named by assets/renders/sprites.json, the manifest tools/blender/render.py writes)
and asks, mechanically, how much shape survives when that same PNG is downsampled to
a small on-screen footprint.

Decision 056 settled CAM-05: the owner picked the zoom-floor option, so this script
no longer exists to cost three options against each other. What is left is durable
evidence for art work — which enemies/emplacements are shape-distinguishable at a
given size, and which pairs collapse into the same silhouette — so a future pass
knows where to spend a distinct outline without re-measuring from scratch.

Three independent modes, run separately (matches the issue's own verification block):

    tools/legibility.py --sizes 30,45,60,90 --out docs/shots/legibility.png
        Contact sheet: every tower and enemy id, downsampled to each candidate
        on-screen cell size, laid on the measured real board background colour.

    tools/legibility.py --matrix --sizes 30,45,60,90
        Pairwise silhouette distance at each size: downsample, threshold alpha to a
        binary mask, Jaccard-distance every pair. Writes the full matrix plus a
        sorted, named list of the closest pairs per size. This is a shape-identity
        measurement, not a claim about human perception — it says two silhouettes
        are mechanically near-identical, not that a player would confuse them.

    tools/legibility.py --zoom-ladder SHOT_100 SHOT_200 --out docs/shots/ladder.png
        Takes two already-captured tools/shot.py PNGs (one per interface scale) and
        downsamples each to a row of relative sizes. This is a proxy for a camera
        zoom that does not exist yet (CAM-01 is unbuilt, so there is no --camera
        hook to render the real thing) — see the honesty note the script prints and
        embeds in the output filename's neighbouring .md write-up.

Idempotent: every mode only reads committed PNGs/JSON and writes exactly the files
named by --out / --matrix-out, deterministically, given the same inputs. Stdlib +
numpy + PIL only, per this project's Python convention.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "assets" / "renders" / "sprites.json"
TOWERS_JSON = ROOT / "data" / "towers.json"
ENEMIES_JSON = ROOT / "data" / "enemies.json"

DEFAULT_SIZES = [30, 45, 60, 90]

# Measured, not assumed (CLAUDE.md's own rule): sampled from a real anchor-24 capture
# (tools/shot.py anchor-24 --frames 1800) at pixel (1150,600), an empty board tile
# away from the diagonal path strip, any sprite, and any UI panel. This is what the
# player's eye actually sits on behind a sprite — not backdrop.gd's authored sky
# gradient, which is a background *behind* the board, not the tile surface itself.
BOARD_BG = (14, 20, 23)

ALPHA_THRESHOLD = 128          # binary in/out for the silhouette mask
DEFAULT_COLLAPSE = 0.10        # Jaccard distance at/below which a pair is named as
                                # "collapsed" in the report. A shape-identity cutoff,
                                # not a measured human-legibility threshold.


def load_manifest() -> dict:
    if not MANIFEST.exists():
        sys.exit(f"no manifest at {MANIFEST} — render the sprite library first")
    return json.loads(MANIFEST.read_text())


def load_assets() -> list[dict]:
    """Every tower and every enemy id, as {'id', 'key', 'name', 'kind'}.

    'key' is the manifest/atlas naming (kebab-case id -> snake_case), matching what
    tools/blender/render.py and pack_atlas.py actually wrote to disk.
    """
    towers = json.loads(TOWERS_JSON.read_text())["towers"]
    enemies = json.loads(ENEMIES_JSON.read_text())["enemies"]
    out = []
    for t in towers:
        out.append({"id": t["id"], "key": t["id"].replace("-", "_"),
                    "name": t["name"], "kind": "tower"})
    for e in enemies:
        out.append({"id": e["id"], "key": e["id"].replace("-", "_"),
                    "name": e["name"], "kind": "enemy"})
    return out


def load_rgba(manifest: dict, key: str, yaw: str = "y045") -> Image.Image:
    entry = manifest["sprites"].get(key)
    if entry is None:
        sys.exit(f"'{key}' not in {MANIFEST} — sprite coverage gate should have caught this")
    rel = entry[yaw]["albedo"]
    path = ROOT / rel
    if not path.exists():
        sys.exit(f"missing committed render: {path}")
    im = Image.open(path).convert("RGBA")
    cell = manifest.get("cell", 256)
    if im.size != (cell, cell):
        sys.exit(f"{path.name} is {im.size}, manifest says cell={cell} — "
                  "atlas/manifest disagree, re-run pack_atlas.py")
    return im


def downsample(im: Image.Image, size: int) -> Image.Image:
    return im.resize((size, size), Image.LANCZOS)


def alpha_mask(im: Image.Image, threshold: int = ALPHA_THRESHOLD) -> np.ndarray:
    a = np.asarray(im)[:, :, 3]
    return a >= threshold


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


# --------------------------------------------------------------------------- contact sheet


def build_contact_sheet(sizes: list[int], out_path: Path) -> None:
    manifest = load_manifest()
    assets = load_assets()
    assets.sort(key=lambda a: (a["kind"], a["id"]))

    slot_w = max(sizes) + 24
    row_h = max(sizes) + 34
    label_h = 34
    margin = 20
    title_h = 40

    width = margin * 2 + slot_w * len(assets)
    height = title_h + margin + len(sizes) * (row_h + label_h)

    page = Image.new("RGB", (width, height), BOARD_BG)
    draw = ImageDraw.Draw(page)
    font_title = _font(16)
    font_label = _font(11)
    font_size = _font(13)

    draw.text((margin, 10),
              "CAM-05 legibility contact sheet — every tower/enemy, y045, on the measured "
              "board background %s" % (BOARD_BG,),
              fill=(200, 210, 215), font=font_title)

    y = title_h + margin
    for size in sizes:
        draw.text((2, y + row_h // 2 - 8), "%dpx" % size, fill=(150, 220, 255), font=font_title)
        for i, asset in enumerate(assets):
            im = load_rgba(manifest, asset["key"])
            thumb = downsample(im, size)
            x = margin + i * slot_w + (slot_w - size) // 2
            ty = y + (row_h - size) // 2
            page.paste(thumb, (x, ty), thumb)
            label = asset["id"]
            color = (120, 200, 255) if asset["kind"] == "tower" else (255, 150, 150)
            # rotate-free short label, centred under the thumbnail slot
            tw = draw.textlength(label, font=font_label)
            lx = margin + i * slot_w + (slot_w - tw) / 2
            draw.text((lx, y + row_h), label, fill=color, font=font_label)
        y += row_h + label_h

    out_path.parent.mkdir(parents=True, exist_ok=True)
    page.save(out_path)
    print("CONTACT SHEET %s (%dx%d, %d assets x %d sizes)"
          % (out_path, width, height, len(assets), len(sizes)))


# --------------------------------------------------------------------------- matrix


def jaccard_distance(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 0.0   # both fully transparent at this size — degenerate, flagged by caller
    return 1.0 - (inter / union)


def compute_matrix(sizes: list[int]) -> dict:
    manifest = load_manifest()
    assets = load_assets()
    assets.sort(key=lambda a: (a["kind"], a["id"]))
    ids = [a["id"] for a in assets]

    result = {"sizes": {}}
    for size in sizes:
        masks = {a["id"]: alpha_mask(downsample(load_rgba(manifest, a["key"]), size))
                  for a in assets}
        n = len(ids)
        mat = np.zeros((n, n))
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                d = jaccard_distance(masks[ids[i]], masks[ids[j]])
                mat[i, j] = mat[j, i] = d
                pairs.append((ids[i], ids[j], d))
        pairs.sort(key=lambda p: p[2])
        result["sizes"][str(size)] = {
            "ids": ids,
            "matrix": mat.tolist(),
            "pairs_by_distance": [{"a": p[0], "b": p[1], "distance": round(p[2], 4)}
                                    for p in pairs],
        }
    return result


def write_matrix_report(sizes: list[int], out_path: Path, collapse_threshold: float) -> dict:
    data = compute_matrix(sizes)
    data["collapse_threshold"] = collapse_threshold
    data["note"] = ("Jaccard distance between alpha-threshold (>=%d/255) silhouette masks, "
                     "downsampled from the native 256px albedo with LANCZOS. 0 = identical "
                     "silhouette, 1 = no overlap. 'collapsed' below is a shape-identity cutoff "
                     "(distance <= %.2f), not a measured human-legibility threshold."
                     % (ALPHA_THRESHOLD, collapse_threshold))

    lines = [
        "CAM-05 pairwise silhouette distance", "",
        data["note"], "",
    ]
    for size in sizes:
        block = data["sizes"][str(size)]
        collapsed = [p for p in block["pairs_by_distance"] if p["distance"] <= collapse_threshold]
        lines.append("== %dpx ==" % size)
        if collapsed:
            lines.append("  collapsed (distance <= %.2f):" % collapse_threshold)
            for p in collapsed:
                lines.append("    %-20s %-20s d=%.4f" % (p["a"], p["b"], p["distance"]))
        else:
            lines.append("  none collapsed at this size/threshold")
        lines.append("  closest 5 overall:")
        for p in block["pairs_by_distance"][:5]:
            lines.append("    %-20s %-20s d=%.4f" % (p["a"], p["b"], p["distance"]))
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(data, indent=2))
    print("MATRIX %s" % out_path)
    print("MATRIX %s (full data)" % json_path)
    print("\n".join(lines))
    return data


# --------------------------------------------------------------------------- zoom ladder


def build_zoom_ladder(shots: list[tuple[str, Path]], factors: list[float],
                       out_path: Path, base_w: int = 480) -> None:
    """One image: each captured PNG downsampled to a row of relative sizes.

    Honesty note (also printed and expected to be quoted in the write-up): this
    resizes the WHOLE captured frame — HUD, panels and board together — because
    CAM-01's board camera does not exist yet and there is no --camera hook to zoom
    only the board layer. The real camera (once built) will zoom the board only and
    leave the HUD at native resolution (STATE.md, LF-052 scoping), so this proxy is
    strictly more pessimistic about the HUD than the real thing will be, and is only
    a like-for-like proxy for how small board sprites themselves would get.
    """
    imgs = [(label, Image.open(p).convert("RGB")) for label, p in shots]
    src_w, src_h = imgs[0][1].size
    scale = base_w / src_w

    pad = 12
    label_h = 20
    row_label_w = 70
    col_widths = [max(1, round(src_w * f * scale)) for f in factors]
    row_h = max(1, round(src_h * factors[0] * scale)) + label_h

    label_margin = 90   # last column's "0.117x (WxH)" caption is wider than its thumbnail
    width = row_label_w + sum(col_widths) + pad * (len(factors) + 1) + label_margin
    height = 60 + len(imgs) * (row_h + pad)

    page = Image.new("RGB", (width, height), (18, 18, 20))
    draw = ImageDraw.Draw(page)
    font = _font(13)
    font_small = _font(11)

    draw.text((pad, 8),
              "CAM-05 zoom ladder — DOWNSAMPLE PROXY, not the real board camera "
              "(CAM-01 unbuilt, no --camera hook). Whole frame resized, HUD included.",
              fill=(255, 200, 120), font=font)
    draw.text((pad, 26),
              "Whole page shown at %.2fx for layout; labels are the TRUE downsample "
              "resolution (capture px x factor), not the on-page pixel count."
              % scale,
              fill=(150, 160, 165), font=font_small)

    y = 44
    for label, im in imgs:
        draw.text((pad, y + row_h // 2 - 6), label, fill=(200, 210, 215), font=font)
        x = row_label_w + pad
        for f, cw in zip(factors, col_widths):
            true_w = max(1, round(src_w * f))
            true_h = max(1, round(src_h * f))
            disp_w = max(1, round(true_w * scale))
            disp_h = max(1, round(true_h * scale))
            thumb = im.resize((disp_w, disp_h), Image.LANCZOS)
            page.paste(thumb, (x, y))
            draw.text((x, y + max(disp_h, 10) + 2), "%.3fx (%dx%d)" % (f, true_w, true_h),
                       fill=(150, 220, 255), font=font_small)
            x += cw + pad
        y += row_h + pad

    out_path.parent.mkdir(parents=True, exist_ok=True)
    page.save(out_path)
    print("ZOOM LADDER %s (%dx%d) — proxy for an unbuilt camera, see docstring" % (out_path, width, height))


# --------------------------------------------------------------------------- CLI


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default=",".join(str(s) for s in DEFAULT_SIZES),
                     help="comma-separated px sizes, e.g. 30,45,60,90")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "shots" / "legibility.png",
                     help="contact sheet output path (contact-sheet mode)")
    ap.add_argument("--matrix", action="store_true", help="run pairwise matrix mode instead")
    ap.add_argument("--matrix-out", type=Path,
                     default=ROOT / "docs" / "shots" / "legibility_matrix.txt")
    ap.add_argument("--collapse-threshold", type=float, default=DEFAULT_COLLAPSE)
    ap.add_argument("--zoom-ladder", nargs=2, metavar=("SHOT_100", "SHOT_200"), default=None,
                     help="two tools/shot.py PNGs (100%% and 200%% interface scale) to "
                          "downsample into one ladder image instead")
    ap.add_argument("--factors", default="1.0,0.5,0.35,0.234,0.117")
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s]

    if args.zoom_ladder:
        factors = [float(f) for f in args.factors.split(",") if f]
        shots = [("100% interface", Path(args.zoom_ladder[0])),
                 ("200% interface", Path(args.zoom_ladder[1]))]
        for _, p in shots:
            if not p.exists():
                sys.exit(f"no capture at {p}")
        build_zoom_ladder(shots, factors, args.out)
        return 0

    if args.matrix:
        write_matrix_report(sizes, args.matrix_out, args.collapse_threshold)
        return 0

    build_contact_sheet(sizes, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
