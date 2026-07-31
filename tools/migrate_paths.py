#!/usr/bin/env python3
"""
One-shot, idempotent migration: `path` -> `paths` in every data/anchors/anchor-NN.json.

WAR-01. The rules assumed exactly one lane, in both implementations, at ten call sites
each. `paths` is the multi-lane shape that replaces it: an array of lane objects
`{"id": <kebab-case>, "waypoints": [[x, y] | [x, y, z], ...]}`, addressed everywhere in
the rules by integer INDEX into `paths` -- stable, orderable, identical in both
languages. `id` is for authoring, dialog and the HUD only and is never compared by a
rule (see docs/DECISIONS.md's WAR-01 entry for the rejected alternative: lane-by-
string-id, which would put a locale/encoding question in the hot loop).

A single-lane anchor -- every anchor at the time this script was written -- migrates to
a `paths` array of exactly one lane, `id: "main"`, carrying the old `path` verbatim as
`waypoints`. No `lane` key is added to any spawn, because 0 is the schema default and a
migrated anchor must grade byte-identically to the pre-migration one (WAR-01's whole
safety argument: shape changed, numbers did not).

Idempotent: an anchor that already has `paths` (no top-level `path` key) is left
untouched, and a second run over already-migrated data reports "no changes" rather than
rewriting anything. Committed rather than run-once-and-deleted: the next person to
hand-author an anchor from an old single-`path` template will need this again.

    .venv/bin/python tools/migrate_paths.py
    .venv/bin/python tools/migrate_paths.py --check      # exit 1 if anything would change
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANCHORS = ROOT / "data" / "anchors"


def migrate_doc(doc: dict) -> tuple[dict, bool]:
    """Return (possibly-migrated doc, changed?). Never mutates the input.

    Key order is preserved -- `paths` lands exactly where `path` used to sit -- so a
    migrated file's diff against its pre-migration self is minimal rather than a full
    reshuffle of unrelated keys.
    """
    if "path" not in doc:
        return doc, False
    out: dict = {}
    for k, v in doc.items():
        if k == "path":
            out["paths"] = [{"id": "main", "waypoints": v}]
        else:
            out[k] = v
    return out, True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                     help="report what would change; exit 1 if anything would, write nothing")
    args = ap.parse_args()

    changed: list[str] = []
    for p in sorted(ANCHORS.glob("anchor-*.json")):
        doc = json.loads(p.read_text())
        new_doc, did_change = migrate_doc(doc)
        if not did_change:
            continue
        changed.append(p.name)
        if not args.check:
            p.write_text(json.dumps(new_doc, indent=2) + "\n")

    if not changed:
        print("no changes — every anchor already carries `paths`")
        return 0
    verb = "would migrate" if args.check else "migrated"
    print(f"{verb} {len(changed)} anchor(s): {', '.join(changed)}")
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
