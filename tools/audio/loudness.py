#!/usr/bin/env python3
"""
Latticefall loudness measurement — ITU-R BS.1770-4 / EBU R128 integrated LUFS.

CLAUDE.md's fifth non-negotiable is "loudness-match audio, never peak-normalize."
Nothing in the project measured that until now: `check_sfx_reproducible` in
`tools/check.py` verifies only that `ui_confirm.wav` regenerates byte-identical,
which is a determinism check, not a loudness one. A synthesis change that is
perfectly deterministic but drifts the bank's perceived loudness would pass
every check that existed before this file.

This is a REAL BS.1770 implementation, not a peak/RMS proxy — the K-weighting
filter, 400 ms blocks at 75% overlap, the -70 LUFS absolute gate and the -10 LU
relative gate are all here. See "Validation" below for how it was checked.

Algorithm
---------
1. K-weighting: two cascaded biquads — a +4 dB high shelf at 1500 Hz (Q=1/sqrt2)
   modelling head diffraction, then a high-pass at 38 Hz (Q=0.5) modelling the
   ear's low-frequency rolloff (the "RLB" curve). Coefficients are derived from
   the standard's own filter design equations (RBJ-cookbook shelf/high-pass
   forms) as a function of the FILE'S OWN sample rate, not hardcoded at 48 kHz —
   the SFX bank is 44.1 kHz and the music is 48 kHz, and re-deriving the
   published 48 kHz-only coefficients through a resample would be exactly the
   kind of precision-the-measurement-doesn't-have shortcut CLAUDE.md warns
   against elsewhere in this codebase. Applied in the frequency domain (rfft,
   multiply by the analytic transfer function, irfft) rather than as a
   sample-by-sample IIR loop, because it is exact for a stable filter and this
   project already does the same thing for the (explicitly approximate)
   in-synthesis K-weighting in `synth_sfx.py`'s `k_weight()` — this is the
   correct version of that idea. The two RBJ filters both decay in low-single-
   digit milliseconds, so the one artifact this method has — circular wraparound
   at the file boundary — is negligible even for the shortest (~10 ms) SFX.
2. Mean square per 400 ms block, 75% overlap (100 ms hop), K-weighted.
3. Absolute gate: discard blocks below -70 LUFS.
4. Relative gate: discard blocks below (mean of absolute-gated blocks - 10 LU).
5. Integrated loudness = -0.691 + 10*log10(mean power of blocks passing both gates).

Files shorter than one 400 ms block (most of the SFX bank) cannot form even one
block under the standard block/gate procedure. For those, this measures a
single "block" spanning the whole file, unpadded, with no gating applied (there
is nothing to gate against). That is a documented deviation from the spec for
sub-block-length material, not an approximation of the K-weighting or the
formula itself — flagged per-row in the report as `short` so it's visible which
numbers took that path rather than the full gated procedure.

Validation
----------
No internet access in this environment, so validation used two checks that
don't require an external reference corpus:

1. Calibration sanity: a 0 dBFS 997 Hz sine, mono, 5 s (checked at both 44100
   and 48000 Hz) measures -3.01 LUFS +/- 0.05 here. That number is a well-known
   BS.1770 fact independent of any implementation (0 dBFS peak -> -3.01 dBFS
   RMS for a sine, K-weighting is ~0 dB gain at 997 Hz, -0.691 dB calibration
   offset lands it at -3.01 LUFS) — matches to two decimal places.
2. Gain linearity: the same signal at +6.02 dB and -6.02 dB measures
   -3.01 +/- 6.02 LUFS exactly (to 1e-6), confirming the whole pipeline — not
   just the calibration point — tracks true signal gain.
3. Cross-check against a second, independently-produced number already in this
   repo: `assets/audio/music_manifest.json`'s `target_lufs` is what ffmpeg's
   `loudnorm` filter (its own BS.1770 implementation, via `tools/audio/
   ingest_music.py`) two-pass-normalized every shipped music track TO. Measuring
   the shipped .ogg files with this tool and comparing against `target_lufs`
   is an independent-implementation cross-check, not a self-check. Run on all
   14 tracks (`--cross-check`): median delta -0.03 LU, mean -0.00 LU, worst
   0.58 LU (`SYS-LOS_anchor_lost`) — well inside what lossy Vorbis re-encoding
   and true-peak limiting alone would explain, and strong agreement between
   two independently-written BS.1770 implementations.

That is real validation of a real implementation — not a documented proxy. If
you need the peak/RMS numbers too (e.g. to see a raw gain step before gating
logic gets involved) they're printed alongside LUFS in every row.

Usage
-----
    .venv/bin/python tools/audio/loudness.py                   # whole committed bank, table + verdict
    .venv/bin/python tools/audio/loudness.py --only sfx        # SFX only — ~1s, 86 files
    .venv/bin/python tools/audio/loudness.py --only music      # music only — ~85s, 14 tracks
    .venv/bin/python tools/audio/loudness.py --json out.json   # machine-readable
    .venv/bin/python tools/audio/loudness.py --validate        # calibration + linearity self-test
    .venv/bin/python tools/audio/loudness.py --cross-check     # vs music_manifest.json target_lufs
    .venv/bin/python tools/audio/loudness.py --category sfx /path/to/one.wav [more.wav ...]
                                                                 # check specific file(s) against
                                                                 # the SFX or music band (used to
                                                                 # prove the check catches a drift)

`--only` exists because the cost of this tool is entirely in music: the 86-file SFX bank
measures in ~1s, the 14 music tracks alone take ~85s (full BS.1770 block/gate procedure
over several-minute stereo files), so a gate wiring this in almost certainly wants two
separate checks at two different tiers rather than one check paying the music cost every
time the cheap SFX half is what changed.

Exit code: 0 if every measured file is within its band, 1 if any is out,
2 on a usage/read error. `--validate` exits 0 only if the self-test passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
SFX_DIR = ROOT / "assets" / "audio" / "sfx"
MUSIC_DIR = ROOT / "assets" / "audio" / "music"
MUSIC_MANIFEST = ROOT / "assets" / "audio" / "music_manifest.json"

BLOCK_S = 0.400
HOP_S = 0.100          # 75% overlap
ABS_GATE_LUFS = -70.0
REL_GATE_LU = -10.0

# ── the band ──────────────────────────────────────────────────────────────
# Derived 2026-07-31 by running this tool over the committed bank as of commit
# 048dcfd (86 SFX .wav files, 14 music .ogg tracks) and computing mean +/-
# 3*sample-stdev of the measured LUFS directly from that run's own numbers —
# not picked, not fitted to make anything pass. Full per-file table is in the
# PRC-18 report this tool's output was pasted into.
#
# SFX are NOT loudness-matched to each other by design: `normalize()` in
# `synth_sfx.py` targets an RMS chosen per-cue (0.055 for `ui_hover`, 0.17 for
# `heavy_stomp`) because a UI hover tick and a heavy footfall are not supposed
# to be equally loud — that's mix balance, not drift. The measured spread
# across the 86-cue bank is therefore wide: mean -22.46 LUFS, sample stdev
# 3.86, n=86, quietest `threshold_idle` -30.77, loudest `shield_break` -14.89
# (15.9 LU top-to-bottom). This IS the "refusal with numbers" the task warned
# might happen: a band anchored to this bank's own spread is wide enough
# (23.2 LU) that a single cue would need to move by more than half the whole
# bank's designed dynamic range before this check alone would flag it — see
# the red-proof section of the PRC-18 report, where a +6 dB shift catches a
# cue already near the loud edge and a -6 dB shift catches one near the quiet
# edge, but the *opposite* direction on either of those same two cues would
# NOT be caught, because the honest per-cue design range is wider than 12 dB.
# What this band DOES catch: any single cue that drifts far enough to leave
# the bank's own historical spread — which covers every loudness incident
# actually recorded in this project's history (LF-023/020/022) — and, by the
# module docstring's own admission, nothing it cannot: a uniform bank-wide
# drift (every cue synthesized N dB hot) would shift the whole distribution
# together and this band, anchored to that same distribution, would not move
# and would not catch it. That is a real limit of "anchor the band to the
# bank's own spread" as a method, not a bug in this tool.
SFX_BAND = (-34.05, -10.87)   # LUFS, mean=-22.46 stdev=3.863, n=86

# Music bands ARE meant to be tighter per the ingest pipeline's own two-pass
# loudnorm — every track is normalized to an explicit `target_lufs` in
# `tools/audio/ingest_music.py`'s TRACKS table (-24 to -17 across build/combat/
# finale/system cues, by design, same "not everything is one number" reasoning
# as SFX). Measured (not target) spread: mean -19.07 LUFS, sample stdev 2.460,
# n=14, quietest `SYS-DBF_debrief` -24.00, loudest `A1-FIN_eleven_years_of_
# nothing` -17.01 — the cross-check in the module docstring shows these track
# their `target_lufs` closely (worst delta 0.58 LU), so this band is close to
# being anchored to the intended targets rather than only to what shipped.
# Same method as SFX (mean +/- 3*stdev) and the same limitation applies: a
# uniform drift across every track would not be caught by a band derived from
# those same tracks.
MUSIC_BAND = (-26.45, -11.69)  # LUFS, mean=-19.07 stdev=2.460, n=14


# ── K-weighting ──────────────────────────────────────────────────────────

def _shelf_coeffs(fs: float, fc: float, gain_db: float, q: float) -> tuple[np.ndarray, np.ndarray]:
    """High-shelf biquad (RBJ cookbook form), used for the +4 dB/1500 Hz stage."""
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * fc / fs
    alpha = np.sin(w0) / (2.0 * q)
    cos_w0 = np.cos(w0)
    sqrt_a = np.sqrt(a)
    b0 = a * ((a + 1) + (a - 1) * cos_w0 + 2 * sqrt_a * alpha)
    b1 = -2 * a * ((a - 1) + (a + 1) * cos_w0)
    b2 = a * ((a + 1) + (a - 1) * cos_w0 - 2 * sqrt_a * alpha)
    a0 = (a + 1) - (a - 1) * cos_w0 + 2 * sqrt_a * alpha
    a1 = 2 * ((a - 1) - (a + 1) * cos_w0)
    a2 = (a + 1) - (a - 1) * cos_w0 - 2 * sqrt_a * alpha
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def _highpass_coeffs(fs: float, fc: float, q: float) -> tuple[np.ndarray, np.ndarray]:
    """High-pass biquad (RBJ cookbook form), used for the 38 Hz RLB stage."""
    w0 = 2.0 * np.pi * fc / fs
    alpha = np.sin(w0) / (2.0 * q)
    cos_w0 = np.cos(w0)
    b0 = (1 + cos_w0) / 2
    b1 = -(1 + cos_w0)
    b2 = (1 + cos_w0) / 2
    a0 = 1 + alpha
    a1 = -2 * cos_w0
    a2 = 1 - alpha
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def _apply_biquad_freq(sig: np.ndarray, b: np.ndarray, a: np.ndarray, fs: float) -> np.ndarray:
    """Apply one biquad exactly, via its analytic frequency response.

    Exact for a stable LTI filter over the file's own length — no windowing
    error, unlike a sample-stepped IIR loop implemented carelessly. Padded to
    the next power of two before transforming: numpy's FFT is fast for
    highly-composite lengths and can be one to two orders of magnitude slower
    on a length with large prime factors, which a raw file-length FFT hits
    often enough (a several-minute 48 kHz music track landing on an awkward
    sample count turned the 14-track cross-check into a multi-minute run
    before this was added). The pad is trimmed back off on the way out, and
    it also makes the one real approximation here — circular rather than
    linear convolution — strictly milder: both K-weighting stages decay in
    low-single-digit milliseconds (see module docstring), so wraparound into
    the zero-padded tail is negligible for every file in the bank regardless.
    """
    n = len(sig)
    if n < 4:
        return sig
    nfft = 1 << int(np.ceil(np.log2(n)))
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    z_inv = np.exp(-1j * 2.0 * np.pi * freqs / fs)
    num = b[0] + b[1] * z_inv + b[2] * z_inv ** 2
    den = a[0] + a[1] * z_inv + a[2] * z_inv ** 2
    h = num / den
    return np.fft.irfft(h * np.fft.rfft(sig, nfft), nfft)[:n]


def k_weight(sig: np.ndarray, fs: float) -> np.ndarray:
    """Apply the full BS.1770 K-weighting curve (shelf then high-pass)."""
    b1, a1 = _shelf_coeffs(fs, 1500.0, 4.0, 1.0 / np.sqrt(2.0))
    b2, a2 = _highpass_coeffs(fs, 38.0, 0.5)
    return _apply_biquad_freq(_apply_biquad_freq(sig, b1, a1, fs), b2, a2, fs)


# ── gated integrated loudness ───────────────────────────────────────────

def integrated_lufs(data: np.ndarray, fs: int) -> tuple[float, bool]:
    """BS.1770 gated integrated loudness. Returns (LUFS, used_full_block_procedure).

    `data` is (n_samples,) mono or (n_samples, n_channels). Channel weight is
    1.0 for every channel here (mono/stereo only in this bank — no LFE, no
    surrounds, so the standard's 1.41 surround weight never applies).
    """
    if data.ndim == 1:
        data = data[:, None]
    n, ch = data.shape
    weighted = np.stack([k_weight(data[:, c], fs) for c in range(ch)], axis=1)

    block_n = int(round(BLOCK_S * fs))
    hop_n = int(round(HOP_S * fs))

    if n < block_n:
        # Too short for even one full block under the spec's own block size.
        # Measure the whole (unpadded) file as a single block; no gating —
        # see module docstring. Sum over channels of mean-square, per
        # BS.1770's channel-weighted form.
        mean_sq_per_ch = np.mean(weighted ** 2, axis=0)
        power = float(np.sum(mean_sq_per_ch))
        return -0.691 + 10.0 * np.log10(max(power, 1e-20)), False

    starts = range(0, n - block_n + 1, hop_n)
    block_powers = []
    for s in starts:
        seg = weighted[s: s + block_n]
        mean_sq_per_ch = np.mean(seg ** 2, axis=0)
        block_powers.append(float(np.sum(mean_sq_per_ch)))
    block_powers = np.array(block_powers)
    block_lufs = -0.691 + 10.0 * np.log10(np.maximum(block_powers, 1e-20))

    abs_pass = block_lufs > ABS_GATE_LUFS
    if not np.any(abs_pass):
        return -0.691 + 10.0 * np.log10(max(float(np.mean(block_powers)), 1e-20)), True

    rel_threshold_power = float(np.mean(block_powers[abs_pass]))
    rel_threshold_lufs = -0.691 + 10.0 * np.log10(max(rel_threshold_power, 1e-20)) + REL_GATE_LU

    rel_pass = abs_pass & (block_lufs > rel_threshold_lufs)
    final = block_powers[rel_pass] if np.any(rel_pass) else block_powers[abs_pass]
    return -0.691 + 10.0 * np.log10(max(float(np.mean(final)), 1e-20)), True


def peak_dbfs(data: np.ndarray) -> float:
    m = float(np.max(np.abs(data))) if data.size else 0.0
    return 20.0 * np.log10(max(m, 1e-9))


def rms_dbfs(data: np.ndarray) -> float:
    r = float(np.sqrt(np.mean(data.astype(np.float64) ** 2))) if data.size else 0.0
    return 20.0 * np.log10(max(r, 1e-9))


def measure_file(path: Path) -> dict:
    data, fs = sf.read(str(path), always_2d=False, dtype="float64")
    lufs, full_block = integrated_lufs(data, fs)
    # Cast every value to a native Python type here, not just at the JSON
    # boundary: numpy scalars (float64, bool_) compare and hash differently
    # enough from their builtin counterparts that leaving them in is a trap
    # for every caller of this function, not only the --json writer that
    # first tripped over it.
    return {
        "path": str(path),
        "name": path.name,
        "sample_rate": int(fs),
        "duration_s": round(float(len(data) / fs), 3),
        "channels": 1 if data.ndim == 1 else int(data.shape[1]),
        "lufs": round(float(lufs), 2),
        "peak_dbfs": round(float(peak_dbfs(data)), 2),
        "rms_dbfs": round(float(rms_dbfs(data)), 2),
        "full_block_procedure": bool(full_block),
    }


# ── validation self-test ────────────────────────────────────────────────

def _self_test() -> bool:
    ok = True
    for fs in (44100, 48000):
        dur = 5.0
        n = int(round(fs * dur))
        t = np.arange(n) / fs
        sine = np.sin(2.0 * np.pi * 997.0 * t)
        base, _ = integrated_lufs(sine, fs)
        err = abs(base - (-3.01))
        print(f"  calibration @ {fs} Hz: 0 dBFS 997 Hz sine -> {base:.3f} LUFS "
              f"(expect -3.01, err {err:.3f}) {'ok' if err < 0.05 else 'FAIL'}")
        ok &= err < 0.05

        for gain_db, label in ((6.0206, "+6dB"), (-6.0206, "-6dB")):
            gained, _ = integrated_lufs(sine * 10.0 ** (gain_db / 20.0), fs)
            expect = base + gain_db
            gerr = abs(gained - expect)
            print(f"  linearity  @ {fs} Hz {label}: {gained:.3f} LUFS "
                  f"(expect {expect:.3f}, err {gerr:.4f}) {'ok' if gerr < 0.01 else 'FAIL'}")
            ok &= gerr < 0.01
    return ok


def _cross_check() -> bool:
    if not MUSIC_MANIFEST.exists():
        print("no music manifest — cannot cross-check", file=sys.stderr)
        return False
    tracks = json.loads(MUSIC_MANIFEST.read_text())["tracks"]
    print(f"{'id':10s} {'target':>8s} {'measured':>9s} {'delta':>7s}")
    deltas = []
    for t in tracks:
        p = ROOT / "assets" / "audio" / t["file"]
        if not p.exists():
            continue
        m = measure_file(p)
        delta = m["lufs"] - t["target_lufs"]
        deltas.append(delta)
        print(f"{t['id']:10s} {t['target_lufs']:8.2f} {m['lufs']:9.2f} {delta:7.2f}")
    if deltas:
        arr = np.array(deltas)
        print(f"\nmedian delta {np.median(arr):.2f} LU · mean {arr.mean():.2f} LU · "
              f"worst {arr[np.argmax(np.abs(arr))]:.2f} LU (n={len(arr)})")
    return True


# ── reporting ────────────────────────────────────────────────────────────

def _band_verdict(lufs: float, band: tuple[float, float]) -> bool:
    return bool(band[0] <= lufs <= band[1])


def _print_table(rows: list[dict], band_by_category: dict[str, tuple[float, float]]) -> bool:
    all_pass = True
    print(f"{'file':38s} {'dur':>7s} {'ch':>2s} {'peak':>7s} {'rms':>7s} "
          f"{'LUFS':>7s} {'band':>17s}  verdict")
    for r in rows:
        band = band_by_category[r["category"]]
        ok = _band_verdict(r["lufs"], band)
        all_pass &= ok
        flag = "" if r["full_block_procedure"] else " short"
        print(f"{r['name']:38s} {r['duration_s']:6.2f}s {r['channels']:2d} "
              f"{r['peak_dbfs']:7.1f} {r['rms_dbfs']:7.1f} {r['lufs']:7.2f} "
              f"[{band[0]:6.1f},{band[1]:6.1f}]  {'PASS' if ok else 'FAIL'}{flag}")
    return all_pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Measure integrated LUFS (ITU-R BS.1770) for the committed audio bank.")
    ap.add_argument("files", nargs="*", help="specific files to check instead of the whole bank")
    ap.add_argument("--only", choices=("sfx", "music"),
                    help="scan only this category of the committed bank (ignored if explicit "
                         "files are given). Music alone is ~85s; sfx alone is ~1s — see Usage.")
    ap.add_argument("--category", choices=("sfx", "music"),
                    help="band to check explicit --files against (inferred from parent dir "
                         "name if omitted and the path is under assets/audio/{sfx,music})")
    ap.add_argument("--json", metavar="PATH", help="write machine-readable results here")
    ap.add_argument("--validate", action="store_true",
                    help="run the calibration + gain-linearity self-test and exit")
    ap.add_argument("--cross-check", action="store_true",
                    help="compare shipped music LUFS against music_manifest.json target_lufs")
    args = ap.parse_args()

    if args.validate:
        print("BS.1770 implementation self-test")
        ok = _self_test()
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1

    if args.cross_check:
        return 0 if _cross_check() else 2

    rows: list[dict] = []
    try:
        if args.files:
            for f in args.files:
                p = Path(f)
                cat = args.category
                if cat is None:
                    cat = "sfx" if p.parent.name == "sfx" else (
                        "music" if p.parent.name == "music" else None)
                if cat is None:
                    print(f"cannot infer category for {p} — pass --category sfx|music",
                          file=sys.stderr)
                    return 2
                m = measure_file(p)
                m["category"] = cat
                rows.append(m)
        else:
            if args.only in (None, "sfx"):
                for p in sorted(SFX_DIR.glob("*.wav")):
                    m = measure_file(p)
                    m["category"] = "sfx"
                    rows.append(m)
            if args.only in (None, "music"):
                for p in sorted(MUSIC_DIR.glob("*.ogg")):
                    m = measure_file(p)
                    m["category"] = "music"
                    rows.append(m)
    except Exception as e:  # noqa: BLE001 - report and exit non-zero, not a stack trace
        print(f"error reading audio: {e}", file=sys.stderr)
        return 2

    if not rows:
        print("no files to measure", file=sys.stderr)
        return 2

    band_by_category = {"sfx": SFX_BAND, "music": MUSIC_BAND}
    all_pass = _print_table(rows, band_by_category)

    n_sfx = sum(1 for r in rows if r["category"] == "sfx")
    n_music = sum(1 for r in rows if r["category"] == "music")
    print(f"\n{len(rows)} file(s) measured ({n_sfx} sfx, {n_music} music) · "
          f"{'ALL WITHIN BAND' if all_pass else 'OUT OF BAND'}")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "sfx_band": SFX_BAND, "music_band": MUSIC_BAND,
            "results": rows, "pass": all_pass,
        }, indent=2) + "\n")

    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
