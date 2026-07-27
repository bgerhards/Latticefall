#!/usr/bin/env python3
"""
Compare the GDScript rules against the Python reference.

The game and the balance simulator implement the same rules twice, in two languages.
That is a standing risk: if they drift, the game is not playing the level that was
graded and signed off, and nothing would announce it.

This runs both over every anchor x policy x difficulty and diffs the outcomes.
Floats are compared with a tolerance because the two runtimes accumulate rounding
differently; discrete results (won, waves cleared, lives, build) must match exactly.

    .venv/bin/python tools/test_parity.py
    .venv/bin/python tools/test_parity.py --anchor anchor-01 --verbose
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import all_anchor_ids, load_anchor, load_enemies, load_towers  # noqa: E402
from sim.engine import DIFFICULTIES, Sim, standard_policies  # noqa: E402

GODOT = "/Applications/Godot.app/Contents/MacOS/Godot"
LOAD_TOLERANCE_MW = 0.01

EXACT = ["won", "waves_cleared", "died_on_wave", "lives_left", "leaks", "spend", "built"]


def run_godot(anchor: str | None) -> list[dict]:
    cmd = [GODOT, "--headless", "--path", str(ROOT),
           "--script", "res://scripts/test/parity.gd"]
    if anchor:
        cmd += ["--", "--anchor", anchor]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    for line in r.stdout.splitlines():
        if line.startswith("PARITY_JSON "):
            return json.loads(line[len("PARITY_JSON "):])
    raise SystemExit(
        "godot produced no parity output.\n"
        f"stdout tail:\n{r.stdout[-1500:]}\nstderr tail:\n{r.stderr[-1500:]}")


def run_python(anchor_ids: list[str]) -> list[dict]:
    towers, enemies = load_towers(), load_enemies()
    out = []
    for aid in anchor_ids:
        anchor = load_anchor(aid)
        available = sorted(t.id for t in towers.values() if t.unlocked_at <= aid)
        for policy in standard_policies(available):
            for diff in DIFFICULTIES:
                o = Sim(anchor, towers, enemies, policy, diff).run()
                out.append({
                    "anchor": aid, "difficulty": diff, "policy": policy.name,
                    "won": o.won, "waves_cleared": o.waves_cleared,
                    "died_on_wave": o.died_on_wave, "lives_left": o.lives_left,
                    "leaks": o.leaks, "peak_load_mw": round(o.peak_load_mw, 3),
                    "spend": o.spend, "built": o.built,
                })
    return out


def key(r: dict) -> tuple:
    return (r["anchor"], r["policy"], r["difficulty"])


def main() -> int:
    ap = argparse.ArgumentParser(description="GDScript vs Python rules parity.")
    ap.add_argument("--anchor")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not Path(GODOT).exists():
        print(f"godot not found at {GODOT} — skipping parity", file=sys.stderr)
        return 0

    ids = [args.anchor] if args.anchor else all_anchor_ids()
    gd = {key(r): r for r in run_godot(args.anchor)}
    py = {key(r): r for r in run_python(ids)}

    missing = sorted(set(py) - set(gd))
    extra = sorted(set(gd) - set(py))
    diffs: list[str] = []

    for k in sorted(set(py) & set(gd)):
        a, b = py[k], gd[k]
        label = f"{k[0]} {k[1]:<16s} {k[2]:<9s}"
        for f in EXACT:
            if a[f] != b[f]:
                diffs.append(f"{label}  {f}: python={a[f]!r} godot={b[f]!r}")
        if abs(a["peak_load_mw"] - b["peak_load_mw"]) > LOAD_TOLERANCE_MW:
            diffs.append(f"{label}  peak_load_mw: python={a['peak_load_mw']} "
                         f"godot={b['peak_load_mw']}")
        if args.verbose and not diffs:
            print(f"  ok  {label}  {'won' if a['won'] else 'lost'} "
                  f"w{a['waves_cleared']} lives {a['lives_left']}")

    for k in missing:
        diffs.append(f"{k}: present in python, absent from godot")
    for k in extra:
        diffs.append(f"{k}: present in godot, absent from python")

    n = len(set(py) & set(gd))
    if diffs:
        print(f"PARITY FAILED — {len(diffs)} difference(s) across {n} run(s)",
              file=sys.stderr)
        for d in diffs[:40]:
            print(f"  {d}", file=sys.stderr)
        if len(diffs) > 40:
            print(f"  ... and {len(diffs) - 40} more", file=sys.stderr)
        return 1

    print(f"parity ok — {n} runs identical (gdscript vs python)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
