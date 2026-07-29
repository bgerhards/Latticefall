#!/usr/bin/env python3
"""
Make each brief say the capacity its anchor actually runs at.

Control reads the bus figure aloud before every level — "A hundred and ninety megawatts." —
so the reactor tier is prose as well as a tuning knob. `sweep.py --apply` writes
`capacity_mw` and does not touch a word of dialog, which means every re-tune silently leaves
Control quoting a number that was true two sessions ago. It happened on sixteen anchors at
once here, and nothing in the project compared the two until `check.py` grew a
`dialog capacity` check.

This is the other half of that check: the check fails, this fixes it. It rewrites exactly
one figure per brief — the spoken megawatt number at or above CAPACITY_FLOOR, since every
other figure a character says (a 22 MW flak array, a 6 MW decay rate, a 44 MW restorer) is
far below any anchor's capacity. If a brief has none, or more than one, it says so and
changes nothing rather than guessing at prose.

    .venv/bin/python tools/say_capacity.py            # report
    .venv/bin/python tools/say_capacity.py --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from check import _spell, _spoken_numbers            # noqa: E402
from sim.content import DATA, all_anchor_ids, load_anchor  # noqa: E402

# No emplacement draws this much and no decay rate approaches it, so a spoken figure at or
# above this is the anchor's capacity and nothing else. The smallest anchor runs at 60.
CAPACITY_FLOOR = 50


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync spoken capacity to anchor data.")
    ap.add_argument("--apply", action="store_true", help="rewrite the dialog files")
    args = ap.parse_args()

    problems = 0
    for aid in all_anchor_ids():
        cap = int(load_anchor(aid).capacity_mw)
        p = DATA / "dialog" / f"{aid}.json"
        if not p.exists():
            continue
        text = p.read_text()

        # Every "<words> megawatts" phrase, with the span it occupies, so the replacement
        # is surgical: the line around it is authored prose and is not ours to reflow.
        hits = []
        for m in re.finditer(r"([A-Za-z][A-Za-z \-]*?)\s+megawatts", text):
            vals = _spoken_numbers(m.group(0))
            big = [v for v in vals if v >= CAPACITY_FLOOR]
            if len(big) == 1:
                hits.append((m, big[0]))

        if len(hits) != 1:
            print(f"{aid}: {len(hits)} capacity-sized figures spoken — leaving it alone")
            problems += 1
            continue

        m, spoken = hits[0]
        if spoken == cap:
            continue

        # Keep the original capitalisation: the figure opens a sentence in most briefs and
        # sits mid-sentence in a few.
        said = _spell(cap)
        said = said.capitalize() if m.group(0)[0].isupper() else said
        print(f"{aid}: {spoken} -> {cap} MW  \"{m.group(0)}\" -> \"{said} megawatts\"")
        if args.apply:
            p.write_text(text[:m.start()] + f"{said} megawatts" + text[m.end():])

    if args.apply:
        print("\nrewritten — re-run tools/check.py")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
