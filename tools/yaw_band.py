#!/usr/bin/env python3
"""
Measure the facing-hysteresis band, the way decision 049 first measured it (LF-108/ART-02).

`scripts/iso.gd`'s `YAW_HYSTERESIS_DEG` used to be a bare degree constant (12.0), picked
once at YAW_COUNT=4 by hand-counting facing changes on anchor-07. It was never re-measured,
so nothing caught it silently exceeding half a bucket's own width the moment a future change
raised YAW_COUNT — the exact freeze LF-108 is about. This is that measurement, rebuilt as a
committed tool instead of a throwaway: it drives `scripts/test/facing.gd`'s headless replay
of a real anchor across as many (yaw_count, hysteresis_frac) combinations as asked for, and
prints one row per combination — changes and reversals, the same two numbers decision 049
reported.

    .venv/bin/python tools/yaw_band.py --anchor anchor-07 --yaws 4  --frac 0,0.05,0.1,0.15,0.2,0.25
    .venv/bin/python tools/yaw_band.py --anchor anchor-07 --yaws 16 --frac 0,0.05,0.1,0.15,0.2,0.25
    .venv/bin/python tools/yaw_band.py --anchor anchor-07 --yaws 4,8,16 --frac 0.15

A *reversal* is a change back to the bucket a key had just left, inside the same
30-tick window `scripts/test/facing.gd` uses. Fewer reversals at a comparable change count
is a tighter band; zero changes at any frac would mean the band is so wide nothing ever
re-buckets (LF-108's freeze) and is flagged explicitly rather than read as "good."
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import toolpaths  # noqa: E402


def run(anchor: str, yaws: list[int], fracs: list[float], timeout: float) -> dict:
    """One Godot launch, `scripts/test/facing.gd`, covering the full (yaws x fracs) grid in
    a single combat replay — see that script's docstring for why one recorded run can be
    replayed at every combination instead of re-simulating per row."""
    extra = ["--headless", "--script", "res://scripts/test/facing.gd", "--",
             "--anchor", anchor,
             "--yaws", ",".join(str(y) for y in yaws),
             "--frac", ",".join(f"{f:g}" for f in fracs)]
    # `--headless` never opens a window on any build — this drives sim.tick() and prints
    # numbers, it draws nothing — so `want_window=True` just means "don't bother wrapping
    # in Xvfb" (mirrors tools/terrain_parity.py's run_godot()).
    cmd = toolpaths.godot_argv(ROOT, extra, want_window=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=timeout)
    band_rows, unit_rows = [], []
    for line in r.stdout.splitlines():
        if line.startswith("BAND_JSON "):
            band_rows.append(json.loads(line[len("BAND_JSON "):]))
        elif line.startswith("UNIT_JSON "):
            unit_rows.append(json.loads(line[len("UNIT_JSON "):]))
    if r.returncode != 0 or (not band_rows and not unit_rows):
        raise SystemExit(
            "yaw_band: facing.gd produced no usable output "
            f"(exit {r.returncode}).\nstdout tail:\n{r.stdout[-2500:]}\n"
            f"stderr tail:\n{r.stderr[-2500:]}")
    return {"band": band_rows, "units": unit_rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anchor", default="anchor-07",
                     help="anchor to replay (default anchor-07, decision 049's own anchor)")
    ap.add_argument("--yaws", default="4",
                     help="comma list of yaw counts to sweep, e.g. 4,8,16")
    ap.add_argument("--frac", default="0,0.05,0.1,0.15,0.2,0.25,0.3,0.35,0.4,0.45",
                     help="comma list of YAW_HYSTERESIS_FRAC candidates to sweep")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    if toolpaths.godot() is None:
        print("godot not found on this machine — cannot measure", file=sys.stderr)
        return 1

    yaws = [int(t) for t in args.yaws.split(",") if t.strip()]
    fracs = [float(t) for t in args.frac.split(",") if t.strip()]

    result = run(args.anchor, yaws, fracs, args.timeout)

    if result["units"]:
        print(f"UNITS  {'yaws':>5}  {'changes':>8}  {'strobes':>8}")
        for row in sorted(result["units"], key=lambda r: r["yaws"]):
            print(f"       {row['yaws']:>5}  {row['changes']:>8}  "
                  f"{row['strobes_within_1_tile']:>8}")
        print()

    if result["band"]:
        print(f"EMPLACEMENTS  {'yaws':>5}  {'frac':>7}  {'changes':>8}  {'reversals':>9}")
        rows = sorted(result["band"], key=lambda r: (r["yaws"], r["frac"]))
        for row in rows:
            flag = ""
            raw = next((r for r in rows if r["yaws"] == row["yaws"] and r["frac"] == 0.0),
                       None)
            if row["frac"] > 0.0 and row["changes"] == 0 and raw and raw["changes"] > 0:
                flag = "  <- froze: 0 changes where frac=0 had some (LF-108)"
            print(f"              {row['yaws']:>5}  {row['frac']:>7.4f}  "
                  f"{row['changes']:>8}  {row['reversals']:>9}{flag}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
