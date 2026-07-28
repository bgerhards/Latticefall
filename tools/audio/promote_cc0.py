#!/usr/bin/env python3
"""Turn auditioned CC0 candidates into bank-shaped cues in assets/audio/sfx/.

Why this exists
---------------
`fetch_cc0.py` proves a file is safe to ship and `audition_cc0.py` records whether a
human wants it. Neither makes it usable. What comes back from a CC0 library is raw:
`radio_noise_loop.wav` is 55 seconds of continuous 8 kHz noise standing in for a
150-millisecond squelch, `sand_footsteps_0.mp3` is thirty separate footsteps in one
file, `deep_rumble.ogg` is 85 seconds of bed where a one-shot is wanted. Shipping any
of those as-is means the engine plays the whole thing under the next dialog line.

**The cut is baked into the asset, never gated at runtime.** `Audio.sfx()` sets a stream
and plays it; it has no notion of length and must not grow one. This is the same call
decision 011 made for music: the loop lives in the file, the engine stays dumb. So a
one-shot cue is *made* short here rather than stopped early there.

Loudness is matched by **RMS, never by peak** (decision 010), reusing `synth_sfx.normalize`
so a sourced cue and a synthesized one sit at the same perceived level in the mix.
Loops are baked with `ingest_music.bake_loop` — the same tail-splice-and-crossfade the
music uses, rather than a second implementation that could disagree with it.

Only candidates marked `keep` are promoted. Every promoted file is appended to
`assets/audio/SOURCES.md` with its source URL, author, licence and SHA-256, which is
decision 038's requirement and the reason this step writes documentation at all.

    .venv/bin/python tools/audio/promote_cc0.py --dry-run
    .venv/bin/python tools/audio/promote_cc0.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from synth_sfx import SR, normalize                    # noqa: E402
from ingest_music import bake_loop                     # noqa: E402

STAGE = Path.home() / "Latticefall-masters" / "cc0-candidates"
MANIFEST = STAGE / "candidates.json"
OUT_DIR = ROOT / "assets" / "audio" / "sfx"
SOURCES = ROOT / "assets" / "audio" / "SOURCES.md"

## Per-cue recipe. `secs` is the finished length; for a one-shot that means the slice
## taken around the loudest transient, for a bed the window handed to the loop baker.
##
## `rms` mirrors the level its neighbours in the bank already sit at — synth_sfx uses
## 0.055-0.07 for beds, ~0.09-0.11 for UI and comms, 0.12-0.17 for impacts. A sourced cue
## that ignores those arrives twice as loud as everything around it.
RECIPES: dict[str, dict] = {
    "amb_facility_loop": {"kind": "bed",  "secs": 20.0, "rms": 0.055},
    "amb_wind_loop":     {"kind": "bed",  "secs": 5.9,  "rms": 0.055},
    "amb_hollow_loop":   {"kind": "bed",  "secs": 20.0, "rms": 0.055},
    "comms_squelch":     {"kind": "shot", "secs": 0.18, "rms": 0.10, "decay": 0.055},
    "comms_close":       {"kind": "shot", "secs": 0.13, "rms": 0.09, "decay": 0.04},
    "rubble_impact":     {"kind": "shot", "secs": 1.20, "rms": 0.13},
    "metal_stress":      {"kind": "shot", "secs": 1.40, "rms": 0.09},
    "footstep_grit":     {"kind": "shot", "secs": 0.40, "rms": 0.10, "decay": 0.08},
    "heavy_footfall":    {"kind": "shot", "secs": 0.60, "rms": 0.15},
    "distant_collapse":  {"kind": "layer", "secs": 3.20, "rms": 0.13},
    "electric_arc":      {"kind": "shot", "secs": 0.34, "rms": 0.12},
}

ATTACK = 0.004          # seconds. Long enough to kill a click, short enough to keep punch.
DEFAULT_DECAY = 0.10


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    """Mono float32 at the file's own rate. The bank is mono (`synth_sfx.write_wav` sets
    one channel), and these are played through a non-positional AudioStreamPlayer, so a
    stereo image would be discarded downstream anyway."""
    x, sr = sf.read(path, always_2d=True, dtype="float32")
    return x.mean(axis=1), sr


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Rate-convert through the frequency domain.

    Truncating the spectrum *is* an ideal low-pass, so downsampling 96 kHz stone impacts
    to the bank's 44.1 kHz does not alias. Naive index-stepping would, and the artefact
    lands right in the crunch of an impact where it is most audible. Slicing happens
    before this is called, so the transform stays small.
    """
    if sr_in == sr_out or len(x) == 0:
        return x
    n_out = int(round(len(x) * sr_out / sr_in))
    if n_out < 2:
        return x
    spec = np.fft.rfft(x)
    bins = n_out // 2 + 1
    out_spec = np.zeros(bins, dtype=complex)
    keep = min(len(spec), bins)
    out_spec[:keep] = spec[:keep]
    return np.fft.irfft(out_spec, n=n_out).astype(np.float32) * (n_out / len(x))


def loudest_onset(x: np.ndarray, sr: int) -> int:
    """Index of the transient worth keeping.

    Finds the loudest 20 ms frame, then walks *backwards* to where the energy first rises
    above a tenth of that. Slicing at the peak itself decapitates the attack, which is the
    part of an impact the ear uses to identify it.
    """
    frame = max(int(sr * 0.02), 1)
    n = max(len(x) // frame, 1)
    env = np.array([np.sqrt((x[i * frame:(i + 1) * frame] ** 2).mean() + 1e-12)
                    for i in range(n)])
    peak = int(env.argmax())
    floor = env[peak] * 0.1
    start = peak
    while start > 0 and env[start - 1] > floor:
        start -= 1
    return max(start - 1, 0) * frame


def shape(x: np.ndarray, sr: int, secs: float, decay: float) -> np.ndarray:
    """Slice to length and fade both ends. The fade-out is what makes a slice out of
    continuous noise read as a gate closing rather than as a tape cut."""
    want = int(sr * secs)
    x = x[:want] if len(x) >= want else np.pad(x, (0, want - len(x)))
    a = min(int(sr * ATTACK), len(x) // 2)
    d = min(int(sr * decay), len(x) - a)
    if a > 0:
        x[:a] *= np.linspace(0.0, 1.0, a, dtype=np.float32)
    if d > 0:
        x[-d:] *= np.linspace(1.0, 0.0, d, dtype=np.float32) ** 1.5
    return x


def write_wav(path: Path, sig: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(sig, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def build(cue: str, recipe: dict, keeps: list[dict]) -> tuple[np.ndarray, int, str, list[dict]]:
    """Return (audio, sample rate, how it was made, the sources actually used).

    The last element exists because a composite cue is built from more than one file and
    every one of them has to be credited. Logging only the first would put a file in the
    repo whose provenance record is wrong, which is the one thing SOURCES.md exists to
    prevent."""
    src, sr = read_mono(STAGE / keeps[0]["file"])
    # Headroom first: sand_footsteps_0.mp3 decodes to a peak of 1.06, and every later
    # stage would be operating on already-clipped samples.
    if (peak := float(np.abs(src).max())) > 1.0:
        src = src / peak

    if recipe["kind"] == "bed":
        want = int(sr * recipe["secs"])
        start = 0 if len(src) <= want else (len(src) - want) // 2
        window = src[start:start + want]
        looped, seam = bake_loop(window[:, None].astype(np.float32), sr)
        out = resample(looped[:, 0], sr, SR)
        return (normalize(out, rms=recipe["rms"]), SR,
                f"loop baked, seam {seam:.4f}", keeps[:1])

    if recipe["kind"] == "layer":
        # Far-off structural failure is a crack over a rumble; the audition kept one of
        # each, so build the composite rather than discard a file the owner marked keep.
        fore = shape(src[loudest_onset(src, sr):].copy(), sr, recipe["secs"],
                     recipe.get("decay", DEFAULT_DECAY))
        fore = resample(fore, sr, SR)
        if len(keeps) > 1:
            back_raw, bsr = read_mono(STAGE / keeps[1]["file"])
            back = shape(back_raw[loudest_onset(back_raw, bsr):].copy(), bsr,
                         recipe["secs"], 0.4)
            back = resample(back, bsr, SR)
            n = min(len(fore), len(back))
            fore = fore[:n] + back[:n] * 0.6
            return (normalize(fore, rms=recipe["rms"]), SR,
                    "explosion over rumble", keeps[:2])
        return normalize(fore, rms=recipe["rms"]), SR, "explosion only", keeps[:1]

    cut = shape(src[loudest_onset(src, sr):].copy(), sr, recipe["secs"],
                recipe.get("decay", DEFAULT_DECAY))
    return (normalize(resample(cut, sr, SR), rms=recipe["rms"]), SR,
            "sliced at onset", keeps[:1])


HEADER = ("| File | Source URL | Author | Licence | SHA-256 | Added |\n"
          "|---|---|---|---|---|---|\n")


def write_sources(rows: list[dict]) -> None:
    """Rewrite the whole SFX table from the rows just built.

    Rewritten rather than appended so the script is idempotent: appending meant a second
    run silently doubled every entry, and a provenance table that lists a file twice is
    evidence of nothing.
    """
    text = SOURCES.read_text()
    start = text.index(HEADER) + len(HEADER)
    end = text.index("\n## ", start)
    body = "\n".join(
        f"| `{r['out']}` | [{r['title'][:44]}]({r['page']}) | {r['author']} | "
        f"{r['licence']} | `{r['sha256'][:16]}…` | {r['added']} |" for r in rows)
    SOURCES.write_text(text[:start] + body + "\n" + text[end:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--date", default="2026-07-27", help="date recorded in SOURCES.md")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"no manifest at {MANIFEST}", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text())

    rows: list[dict] = []
    for cue, spec in manifest.items():
        keeps = [c for c in spec.get("candidates", []) if c.get("verdict") == "keep"]
        if not keeps:
            print(f"{cue:19s} — no keeps, skipped")
            continue
        recipe = RECIPES.get(cue)
        if recipe is None:
            print(f"{cue:19s} — no recipe, skipped", file=sys.stderr)
            continue

        audio, sr, how, used = build(cue, recipe, keeps)
        out = OUT_DIR / f"{cue}.wav"
        secs = len(audio) / sr
        src_secs = sf.info(STAGE / keeps[0]["file"]).duration
        print(f"{cue:19s} {src_secs:7.2f}s -> {secs:5.2f}s  {recipe['kind']:5s} "
              f"rms {recipe['rms']:.3f}  {how}")
        if args.dry_run:
            continue
        write_wav(out, audio, sr)
        digest = sha256_of(out)
        for src_row in used:
            rows.append({
                "out": out.name, "title": src_row.get("title", src_row["file"]),
                "page": src_row.get("page", ""), "author": src_row.get("author", "?"),
                "licence": src_row.get("licence", "CC0"), "sha256": digest,
                "added": args.date,
            })

    if not args.dry_run and rows:
        write_sources(rows)
        cues = len({r["out"] for r in rows})
        print(f"\nwrote {cues} cues to {OUT_DIR}")
        print(f"logged {len(rows)} source rows in {SOURCES.relative_to(ROOT)}")
        print("\nNow re-import, or the game will not see them:")
        print("  /Applications/Godot.app/Contents/MacOS/Godot --headless --path . --import")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
