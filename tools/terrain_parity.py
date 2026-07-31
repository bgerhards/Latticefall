#!/usr/bin/env python3
"""
Compare sim/content.py's resolve_terrain() against scripts/content.gd's, on the shared
fixture data/schema/fixtures/terrain-resolution.json.

TER-02. PRD-THEATRE-SCALE.md risk #10 names "two independent anchor parsers disagreeing on
region -> height resolution" as a failure mode invisible to schema validation and to a
screenshot, and findable only by the 9-minute `rules parity` gate — after which the symptom
is "some unit leaked in one engine and not the other" with no pointer to terrain at all.
This is the cheap, targeted proof: one small board, both resolvers, diffed tile for tile,
meant to run in the gate's fast tier rather than waiting on the full parity run to notice a
one-tile drift.

    .venv/bin/python tools/terrain_parity.py
    .venv/bin/python tools/terrain_parity.py --corrupt   # flip one expected digit, prove red
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import resolve_terrain  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import toolpaths  # noqa: E402

FIXTURE = ROOT / "data" / "schema" / "fixtures" / "terrain-resolution.json"


def run_godot() -> dict[str, list[list[int]]]:
    # `--headless` never opens a window on any build — this resolves data, it draws
    # nothing — so `want_window=True` just means "don't bother wrapping in Xvfb".
    extra = ["--headless", "--script", "res://scripts/test/terrain_parity.gd"]
    cmd = toolpaths.godot_argv(ROOT, extra, want_window=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    for line in r.stdout.splitlines():
        if line.startswith("TERRAIN_PARITY_JSON "):
            cases = json.loads(line[len("TERRAIN_PARITY_JSON "):])
            return {c["name"]: c["grid"] for c in cases}
    raise SystemExit(
        "godot produced no terrain-parity output.\n"
        f"stdout tail:\n{r.stdout[-1500:]}\nstderr tail:\n{r.stderr[-1500:]}")


def run_python(doc_by_name: dict[str, dict]) -> dict[str, list[list[int]]]:
    return {name: [list(row) for row in resolve_terrain(doc)]
            for name, doc in doc_by_name.items()}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="sim/content.py vs scripts/content.gd terrain-resolution parity.")
    ap.add_argument("--corrupt", action="store_true",
                     help="flip one digit of the fixture's expected grid, to prove the "
                          "check fails red instead of passing for the wrong reason")
    args = ap.parse_args()

    if toolpaths.godot() is None:
        print("godot not found on this machine — skipping terrain parity", file=sys.stderr)
        return 0

    fixture = json.loads(FIXTURE.read_text())
    cases = fixture["cases"]
    doc_by_name = {c["name"]: c["doc"] for c in cases}
    expected_by_name = {c["name"]: c["expected"] for c in cases}

    corrupted_at = None
    if args.corrupt:
        name = cases[0]["name"]
        expected_by_name[name][0][0] ^= 1
        corrupted_at = (name, 0, 0)

    gd = run_godot()
    py = run_python(doc_by_name)

    problems: list[str] = []
    for name in doc_by_name:
        exp = expected_by_name[name]
        for src, grid in (("python", py[name]), ("godot", gd[name])):
            for y, row in enumerate(grid):
                for x, v in enumerate(row):
                    if v != exp[y][x]:
                        problems.append(f"{name}/{src}: tile ({x},{y}) expected "
                                         f"{exp[y][x]}, got {v}")
        # The two resolvers must also agree with EACH OTHER, not only with `expected` —
        # otherwise a fixture typo could hide a real disagreement behind two matching
        # wrong answers.
        for y, (prow, grow) in enumerate(zip(py[name], gd[name])):
            for x, (pv, gv) in enumerate(zip(prow, grow)):
                if pv != gv:
                    problems.append(f"{name}: tile ({x},{y}) python={pv} godot={gv}")

    if problems:
        print(f"{len(problems)} mismatch(es):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        if corrupted_at:
            print(f"(--corrupt flipped {corrupted_at[0]}/({corrupted_at[2]},{corrupted_at[1]}) "
                  f"on purpose, to prove this fails red)", file=sys.stderr)
        return 1

    print(f"ok — {len(cases)} case(s), python and godot agree with the fixture and "
          f"each other")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
