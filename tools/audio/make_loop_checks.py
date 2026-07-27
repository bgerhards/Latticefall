#!/usr/bin/env python3
"""Write short clips that cross each track's loop point, so a human can judge it.

Automated seam metrics do not describe loop quality for baked crossfades: the
join is sample-continuous by construction, and window energy varies by 10 dB for
ordinary musical reasons. The only honest test is listening to the wrap.

Each clip is the last SPAN seconds of the loop followed immediately by the first
SPAN seconds, so the loop point lands dead centre. Play it; if you cannot hear
where it wraps, the loop is good.

    .venv/bin/python tools/audio/make_loop_checks.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
MUSIC = ROOT / "assets" / "audio"
OUT = ROOT / "assets" / "audio" / "_loop_checks"
SPAN = 6.0

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    man = json.loads((MUSIC / "music_manifest.json").read_text())
    made = 0
    for t in man["tracks"]:
        if not t["loop"]:
            continue
        a, sr = sf.read(str(MUSIC / t["file"]), dtype="float32", always_2d=True)
        n = int(SPAN * sr)
        if len(a) < n * 2:
            continue
        clip = np.concatenate([a[-n:], a[:n]])
        dst = OUT / f"{t['id']}_loopcheck.ogg"
        with sf.SoundFile(str(dst), "w", samplerate=sr, channels=a.shape[1],
                          format="OGG", subtype="VORBIS") as f:
            for i in range(0, len(clip), 1 << 16):
                f.write(clip[i:i + (1 << 16)])
        print(f"{t['id']:9s} {2*SPAN:.0f}s  wrap at {SPAN:.0f}s  ->  {dst.name}")
        made += 1
    print(f"\n{made} clips in {OUT.relative_to(ROOT)}")
