#!/usr/bin/env python3
"""
Set `loop=true` on every music .import file.

Loops are baked into the audio (decision 011), so the file is meant to be played end to
end and restarted seamlessly. Godot's Ogg importer defaults to `loop=false`, and
`audio_director.gd` compensates by setting `stream.loop = true` after loading — which
works, but means the correct behaviour depends on a line of engine code rather than on
the asset, and anything that plays one of these files without going through the director
gets a bed that stops dead after one pass.

Idempotent: rewrites only the files that actually say `loop=false`.

    .venv/bin/python tools/audio/set_loop_flags.py
    .venv/bin/python tools/audio/set_loop_flags.py --check     # exit 1 if any are unset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MUSIC = ROOT / "assets" / "audio" / "music"


def main() -> int:
    ap = argparse.ArgumentParser(description="Set loop=true on music imports.")
    ap.add_argument("--check", action="store_true",
                    help="report unset files and exit non-zero instead of fixing them")
    args = ap.parse_args()

    imports = sorted(MUSIC.glob("*.ogg.import"))
    if not imports:
        print("no music .import files — has the project been imported?", file=sys.stderr)
        return 1

    unset = [p for p in imports if "loop=false" in p.read_text()]
    if args.check:
        for p in unset:
            print(f"loop unset: {p.name}")
        print(f"{len(imports) - len(unset)}/{len(imports)} music files loop")
        return 1 if unset else 0

    for p in unset:
        p.write_text(p.read_text().replace("loop=false", "loop=true"))
    print(f"{len(unset)} updated, {len(imports)} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
