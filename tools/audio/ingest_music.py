#!/usr/bin/env python3
"""
Latticefall music ingest.

Takes the raw Suno WAVs in assets/audio/source/ and produces game-ready Ogg
Vorbis in assets/audio/music/, plus a manifest.

Per track:
  1. rename to  <ID>_<snake_name>.wav          (canonical, sorts by play order)
  2. trim leading/trailing near-silence
  3. for looping cues: find the tail splice that best matches the head, then
     bake a crossfade so the file loops with no seam and no engine-side logic
  4. two-pass EBU R128 loudness normalization to the track's target LUFS
  5. encode Ogg Vorbis
  6. record duration, loudness, seam error and SHA-256 in the manifest

Suno writes linear music with intros and endings. Games need loops. Step 3 is
where that gap gets closed, and it is the only interesting part of this file.

Usage:
    .venv/bin/python tools/audio/ingest_music.py            # all tracks
    .venv/bin/python tools/audio/ingest_music.py TTL-01     # one track
    .venv/bin/python tools/audio/ingest_music.py --rename-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "assets" / "audio" / "source"
OUT = ROOT / "assets" / "audio" / "music"
MANIFEST = ROOT / "assets" / "audio" / "music_manifest.json"

CROSSFADE = 3.0      # seconds baked into the loop seam
SEARCH = 6.0         # seconds of tail positions to search for the best splice
SILENCE_DB = -48.0   # trim threshold

# id -> (snake name, loops?, target LUFS)
# Beds sit under dialog and gameplay, so they are quieter than the title and
# the finales. Stingers are one-shots and stay hot so they cut through combat.
TRACKS = {
    "TTL-01":  ("latticefall",              True,  -18.0),
    "A1-BLD":  ("carrier_signal",           True,  -22.0),
    "A1-CMB":  ("warden_protocol",          True,  -18.0),
    "A1-FIN":  ("eleven_years_of_nothing",  True,  -17.0),
    "A2-BLD":  ("contract_terms",           True,  -22.0),
    "A2-CMB":  ("hostile_recovery",         True,  -18.0),
    "A2-FIN":  ("sable_reach",              True,  -17.0),
    "A3-BLD":  ("circulatory",              True,  -22.0),
    "A3-CMB":  ("the_door_was_held_open",   True,  -18.0),
    "A3-FIN":  ("close_it_from_this_side",  True,  -17.0),
    "SYS-BRN": ("brownout",                 True,  -20.0),
    "SYS-DBF": ("debrief",                  True,  -24.0),   # sits under speech
    "SYS-WIN": ("anchor_held",              False, -17.0),
    "SYS-LOS": ("anchor_lost",              False, -17.0),
}


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ────────────────────────────────────────────────────────────── rename ──

def canonical_name(p: Path) -> str | None:
    """'A1-BLD Carrier Signal.wav' -> 'A1-BLD_carrier_signal.wav'"""
    m = re.match(r"^((?:A[123]|SYS|TTL)-[A-Z0-9]{2,3})(?=[ _]|$)", p.stem)
    if not m or m.group(1) not in TRACKS:
        return None
    tid = m.group(1)
    return f"{tid}_{TRACKS[tid][0]}.wav"


def rename_sources(verbose: bool = True) -> dict[str, Path]:
    """Rename in place via `git mv` when tracked, so LFS pointers move cleanly."""
    found: dict[str, Path] = {}
    for p in sorted(SRC.glob("*.wav")):
        want = canonical_name(p)
        if want is None:
            print(f"  ? unrecognized, skipped: {p.name}", file=sys.stderr)
            continue
        tid = want.split("_")[0]
        dst = p.with_name(want)
        if p.name != want:
            tracked = sh("git", "-C", str(ROOT), "ls-files", "--error-unmatch",
                         str(p.relative_to(ROOT))).returncode == 0
            if tracked:
                sh("git", "-C", str(ROOT), "mv", str(p.relative_to(ROOT)),
                   str(dst.relative_to(ROOT)))
            if p.exists():           # git mv is a no-op if untracked
                shutil.move(str(p), str(dst))
            if verbose:
                print(f"  renamed  {p.name}  ->  {want}")
        found[tid] = dst
    return found


# ──────────────────────────────────────────────────────────── looping ──

def read_wav(p: Path):
    with wave.open(str(p)) as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"{p.name}: expected 16-bit PCM, got {sw*8}-bit")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return a.reshape(-1, ch), sr


def write_wav(p: Path, a: np.ndarray, sr: int):
    data = (np.clip(a, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(a.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def trim_silence(a: np.ndarray, sr: int) -> np.ndarray:
    mono = a.mean(axis=1)
    thresh = 10 ** (SILENCE_DB / 20)
    win = sr // 100
    frames = len(mono) // win
    energy = np.abs(mono[: frames * win].reshape(frames, win)).max(axis=1)
    loud = np.flatnonzero(energy > thresh)
    if len(loud) == 0:
        return a
    return a[loud[0] * win: min(len(a), (loud[-1] + 1) * win)]


def best_splice(a: np.ndarray, sr: int) -> int:
    """Pick the loop end that best matches the loop start.

    We compare a window just before each candidate end against the window just
    after the head. Lower distance means the crossfade has less to hide, so the
    seam survives repeated listening rather than only the first pass.
    """
    xf = int(CROSSFADE * sr)
    search = int(SEARCH * sr)
    if len(a) < xf * 3 + search:
        return len(a)

    # cheap mono downmix at ~4 kHz — plenty for matching musical phase
    dec = max(1, sr // 4000)
    m = a.mean(axis=1)[::dec]
    xf_d, search_d = xf // dec, search // dec

    head = m[xf_d: xf_d * 2]
    head_n = head / (np.linalg.norm(head) + 1e-9)

    best_i, best_score = len(m), -2.0
    for end_d in range(len(m) - search_d, len(m) - xf_d):
        tail = m[end_d - xf_d: end_d]
        if len(tail) != len(head):
            continue
        score = float(np.dot(tail / (np.linalg.norm(tail) + 1e-9), head_n))
        if score > best_score:
            best_score, best_i = score, end_d
    return min(len(a), best_i * dec)


def bake_loop(a: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """Crossfade the tail into the head. Returns (audio, seam error 0..1)."""
    end = best_splice(a, sr)
    a = a[:end]
    xf = int(CROSSFADE * sr)
    if len(a) < xf * 2:
        return a, float(np.abs(a[-1] - a[0]).mean())

    ramp = np.linspace(0.0, 1.0, xf, dtype=np.float32)[:, None]
    # equal-power so the crossfade doesn't dip in the middle
    fin, fout = np.sqrt(ramp), np.sqrt(1.0 - ramp)
    out = a[:-xf].copy()
    out[:xf] = a[-xf:] * fout + a[:xf] * fin
    seam = float(np.abs(out[-1] - out[0]).mean())
    return out, seam


# ─────────────────────────────────────────────────────── loudness/encode ──

def measure_loudness(p: Path, target: float) -> dict:
    r = sh("ffmpeg", "-nostdin", "-i", str(p), "-af",
           f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
           "-f", "null", "-")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        raise SystemExit(f"loudnorm measurement failed for {p.name}:\n{r.stderr[-800:]}")
    return json.loads(m.group(0))


def encode_ogg(src: Path, dst: Path, target: float, meas: dict, quality: int = 6):
    """Loudness-normalize with ffmpeg, then encode Vorbis with libsndfile.

    This Homebrew ffmpeg ships without libvorbis — only the native `vorbis`
    encoder, which is flagged experimental and sounds it. libsndfile links the
    reference encoder, so we hand off at the last step rather than asking the
    machine to rebuild ffmpeg.
    """
    af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:"
          f"measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
          f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
          f"offset={meas['target_offset']}:linear=true:print_format=summary")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        norm = Path(tf.name)
    try:
        r = sh("ffmpeg", "-nostdin", "-y", "-i", str(src), "-af", af,
               "-c:a", "pcm_s16le", "-ar", "48000", str(norm))
        if r.returncode != 0:
            raise SystemExit(f"loudnorm failed for {src.name}:\n{r.stderr[-800:]}")
        # Stream in blocks. libsndfile's Vorbis writer segfaults on a single
        # multi-million-frame write, so never hand it a whole track at once.
        with sf.SoundFile(str(norm)) as src_f, sf.SoundFile(
            str(dst), "w", samplerate=src_f.samplerate,
            channels=src_f.channels, format="OGG", subtype="VORBIS"
        ) as dst_f:
            for block in src_f.blocks(blocksize=1 << 16, dtype="float32", always_2d=True):
                dst_f.write(block)
    finally:
        norm.unlink(missing_ok=True)


def duration(p: Path) -> float:
    r = sh("ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=nw=1:nk=1", str(p))
    return float(r.stdout.strip() or 0.0)


# ───────────────────────────────────────────────────────────────── main ──

def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest Suno music into game-ready Ogg.")
    ap.add_argument("ids", nargs="*", help="track IDs (default: all)")
    ap.add_argument("--rename-only", action="store_true")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"no source directory: {SRC}", file=sys.stderr)
        return 2

    print("renaming sources to convention")
    found = rename_sources()
    missing = [t for t in TRACKS if t not in found]
    if missing:
        print(f"  ! not present: {', '.join(sorted(missing))}")
    if args.rename_only:
        return 0

    ids = args.ids or [t for t in TRACKS if t in found]
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []

    print(f"\nprocessing {len(ids)} track(s)")
    print(f"{'id':9s} {'src':>7s} {'out':>7s} {'loop':>5s} {'seam':>7s} {'LUFS':>7s} {'MB':>6s}")
    for tid in ids:
        if tid not in found:
            print(f"{tid:9s} ! source missing")
            continue
        name, loops, target = TRACKS[tid]
        src = found[tid]
        a, sr = read_wav(src)
        src_dur = len(a) / sr

        a = trim_silence(a, sr)
        seam = None
        if loops:
            a, seam = bake_loop(a, sr)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp = Path(tf.name)
        try:
            write_wav(tmp, a, sr)
            meas = measure_loudness(tmp, target)
            dst = OUT / f"{tid}_{name}.ogg"
            encode_ogg(tmp, dst, target, meas)
        finally:
            tmp.unlink(missing_ok=True)

        out_dur, mb = duration(dst), dst.stat().st_size / 1e6
        print(f"{tid:9s} {src_dur:6.1f}s {out_dur:6.1f}s {'yes' if loops else 'no':>5s} "
              f"{(f'{seam:.5f}' if seam is not None else '—'):>7s} {float(meas['input_i']):7.1f} {mb:6.2f}")

        manifest.append({
            "id": tid, "name": name, "file": f"music/{dst.name}",
            "loop": loops, "loop_offset": 0.0,
            "duration": round(out_dur, 3),
            "target_lufs": target,
            "measured_input_lufs": float(meas["input_i"]),
            "seam_error": round(seam, 6) if seam is not None else None,
            "source": src.name,
            "source_sha256": sha256(src),
        })

    MANIFEST.write_text(json.dumps({"crossfade_seconds": CROSSFADE,
                                    "tracks": manifest}, indent=2) + "\n")
    total = sum(t["duration"] for t in manifest)
    print(f"\n{len(manifest)} tracks · {total/60:.1f} min · "
          f"{sum((OUT / Path(t['file']).name).stat().st_size for t in manifest)/1e6:.1f} MB")
    print(f"manifest -> {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
