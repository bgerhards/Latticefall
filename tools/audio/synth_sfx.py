#!/usr/bin/env python3
"""
Latticefall procedural SFX synthesizer.

Deterministic: every sound is a pure function of its name + seed, so the entire
bank regenerates byte-identical on any machine. Sounds are *code*, not binaries —
tweak a parameter, re-run, hear the change. No licensing exposure, no attribution
tracking, and no hunting a sample library for "the right one".

Design language (shared with the score bible, deliberately):
  - Meridian / human / affirmative  -> open fifths, dry transients, tape warmth
  - Ordinal / alien / negative      -> minor seconds and tritones, metallic ring
  - Power system                    -> 50 Hz mains hum and its harmonics

Usage:
    .venv/bin/python tools/audio/synth_sfx.py                 # whole bank
    .venv/bin/python tools/audio/synth_sfx.py ui_click        # one sound
    .venv/bin/python tools/audio/synth_sfx.py --list
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT_DIR = Path(__file__).resolve().parents[2] / "assets" / "audio" / "sfx"


# ─────────────────────────────────────────────────────────── primitives ──

def t(dur: float) -> np.ndarray:
    return np.arange(int(round(SR * dur)), dtype=np.float64) / SR


def rng_for(name: str) -> np.random.Generator:
    """Seed derived from the sound's name -> stable across runs and machines."""
    h = hashlib.sha256(name.encode()).digest()
    return np.random.default_rng(struct.unpack("<Q", h[:8])[0])


def sine(freq, dur, phase=0.0):
    return np.sin(2 * np.pi * freq * t(dur) + phase)


def sweep(f0: float, f1: float, dur: float, curve: float = 3.0):
    """Exponential pitch sweep. Phase-accumulated so it doesn't click."""
    x = t(dur)
    k = (x / dur) ** curve if dur > 0 else x
    f = f0 * (f1 / f0) ** k
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def noise(dur, gen: np.random.Generator, pink=False):
    n = gen.standard_normal(int(round(SR * dur)))
    if pink:  # cheap 1/f via cascaded one-poles
        out, b = np.zeros_like(n), 0.0
        for i, v in enumerate(n):
            b = 0.997 * b + v * 0.03
            out[i] = b + v * 0.12
        return out
    return n


def env_exp(dur, decay=8.0, attack=0.002):
    x = t(dur)
    a = np.clip(x / max(attack, 1e-6), 0, 1)
    return a * np.exp(-decay * x / dur * (dur / max(dur, 1e-9)) * decay / decay) * np.exp(-decay * x)


def env_ad(dur, attack=0.005, decay_curve=6.0):
    x = t(dur)
    a = np.clip(x / max(attack, 1e-6), 0, 1)
    return a * np.exp(-decay_curve * x)


def lowpass(sig, cutoff, res=0.0):
    """State-variable filter — stable, and resonance gives us metallic ring."""
    f = 2.0 * np.sin(np.pi * min(cutoff, SR * 0.45) / SR)
    q = 1.0 - min(res, 0.96)
    low = band = 0.0
    out = np.empty_like(sig)
    for i, s in enumerate(sig):
        high = s - low - q * band
        band += f * high
        low += f * band
        out[i] = low
    return out


def bandpass(sig, cutoff, res=0.85):
    f = 2.0 * np.sin(np.pi * min(cutoff, SR * 0.45) / SR)
    q = 1.0 - min(res, 0.97)
    low = band = 0.0
    out = np.empty_like(sig)
    for i, s in enumerate(sig):
        high = s - low - q * band
        band += f * high
        low += f * band
        out[i] = band
    return out


def highpass(sig, cutoff):
    a = np.exp(-2.0 * np.pi * cutoff / SR)
    out = np.empty_like(sig)
    prev_x = prev_y = 0.0
    for i, s in enumerate(sig):
        prev_y = a * (prev_y + s - prev_x)
        prev_x = s
        out[i] = prev_y
    return out


def saturate(sig, drive=2.0):
    """Tape-style soft clip. Every sound gets a little; it glues the bank."""
    return np.tanh(sig * drive) / np.tanh(drive)


def metallic(dur, base, partials, gen, decay=9.0, spread=1.0):
    """Inharmonic partial stack — the difference between 'a beep' and 'metal'."""
    out = np.zeros(int(round(SR * dur)))
    for i, ratio in enumerate(partials):
        f = base * ratio * (1.0 + gen.uniform(-0.004, 0.004) * spread)
        out += sine(f, dur) * np.exp(-(decay + i * 1.6) * t(dur)) / (i + 1.4)
    return out


def tail(sig, amount=0.22, length=0.16, gen=None):
    """Cheap diffuse tail. Not a real reverb — just enough to sit in a space."""
    if gen is None:
        gen = np.random.default_rng(0)
    n = int(round(SR * length))
    ir = gen.standard_normal(n) * np.exp(-6.0 * np.linspace(0, 1, n))
    wet = np.convolve(sig, ir, mode="full")[: len(sig)] / (np.sqrt(n) * 0.9)
    return sig + wet * amount


def mix(*layers):
    n = max(len(x) for x in layers)
    out = np.zeros(n)
    for layer in layers:
        out[: len(layer)] += layer
    return out


def pad(sig, dur):
    n = int(round(SR * dur))
    return np.pad(sig, (0, max(0, n - len(sig))))[:n]


def loop_noise(sig, fade=0.05):
    """Make an *aperiodic* layer loop.

    Only ever apply this to noise. Tonal layers are built at durations where
    every partial completes a whole number of cycles, so they already loop
    exactly — crossfading them introduces error rather than removing it.

    Caller must supply `fade` seconds of EXTRA material; this returns
    len(sig) - fade so the tonal layers keep their exact loop length.
    """
    n = int(round(SR * fade))
    if n * 2 >= len(sig):
        return sig
    ramp = np.linspace(0, 1, n)
    out = sig[: len(sig) - n].copy()
    out[:n] = sig[-n:] * (1 - ramp) + sig[:n] * ramp
    return out


def limit(sig, ceiling=0.95):
    m = np.max(np.abs(sig))
    return sig * (ceiling / m) if m > ceiling else sig


def normalize(sig, rms=0.14, ceiling=0.95):
    """Loudness-match, then limit.

    Peak normalization is why programmer-made SFX banks sound flat: every
    sound arrives at the same peak but wildly different perceived loudness,
    so the mix has no dynamic range. Match RMS instead and let peaks vary —
    that is what preserves transient punch.
    """
    r = np.sqrt(np.mean(sig ** 2))
    if r > 0:
        sig = sig * (rms / r)
    return limit(sig, ceiling)


# ────────────────────────────────────────── primitives: the combat layer ──
# Added when the game grew a combat VFX layer and needed sound for it. The
# originals above are all per-sample Python loops, which is fine for a 20 ms
# grain and unaffordable for a 3 s swell, and none of them can say "noise
# between 1.4 and 4 kHz and nothing else" — which is most of what separates a
# flak burst from a mortar. These are the tools that gap needed, kept general
# so the next cue does not need a one-off.


def _band_mask(freqs: np.ndarray, lo: float, hi: float, edge: float = 0.5) -> np.ndarray:
    """Raised-cosine band mask over an rfft frequency axis.

    Skirts are specified in *octaves* rather than Hz because hearing is
    logarithmic: a 200 Hz transition is gentle at 6 kHz and a brick wall at
    300 Hz, so an octave-relative skirt is the only one that sounds the same
    wherever the band is placed.
    """
    f = np.maximum(freqs, 1e-6)
    lo_oct = np.log2(f / max(lo, 1e-6)) / max(edge, 1e-6)
    hi_oct = np.log2(max(hi, 1e-6) / f) / max(edge, 1e-6)
    rise = 0.5 - 0.5 * np.cos(np.pi * np.clip(lo_oct, 0.0, 1.0))
    fall = 0.5 - 0.5 * np.cos(np.pi * np.clip(hi_oct, 0.0, 1.0))
    return rise * fall


def band_noise(dur: float, gen: np.random.Generator, lo: float, hi: float,
               edge: float = 0.5, tilt_db: float = 0.0) -> np.ndarray:
    """Noise confined to [lo, hi] Hz, returned at unit PEAK.

    The frequency-domain route is not an optimisation, it is the point: the SVF
    above can only ever be a resonant peak, so every noise layer in the original
    bank has the same colour and the bank's textures all sound related. A precise
    band is what makes a hollow launch tube and a ceramic shard different objects
    rather than the same noise at two cutoffs.

    Peak rather than RMS, to match `sine`, `sweep` and `modal_ir` — so that a `*
    0.55` on a noise layer means the same thing as a `* 0.55` on a tone. The
    alternative was tried and is a trap: unit-RMS noise arrives with peaks near 4,
    every mix then hits `saturate()` at four times the level its drive was chosen
    for, and tanh stops being glue and becomes a brick wall. It cost the first cut
    of this layer its transients — `impact_light` measured a crest factor of
    3.9 dB, which is a click with the click removed.

    `tilt_db` is dB per octave about the band centre — negative darkens, which is
    how real material absorbs.
    """
    n = int(round(SR * dur))
    if n < 4:
        return np.zeros(max(n, 0))
    spec = np.fft.rfft(gen.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mask = _band_mask(f, lo, hi, edge)
    if tilt_db:
        ref = float(np.sqrt(max(lo, 20.0) * min(hi, SR * 0.45)))
        mask = mask * 10.0 ** (tilt_db * np.log2(np.maximum(f, 1e-6) / ref) / 20.0)
    out = np.fft.irfft(spec * mask, n)
    peak = float(np.max(np.abs(out)))
    return out / peak if peak > 0 else out


def click(dur: float, lo: float, hi: float, edge: float = 0.5) -> np.ndarray:
    """A band-limited impulse: compact, deterministic, and FLAT inside its band.

    The exciter for a *tuned* body must not have holes in it. A 1.5 ms noise burst
    has 660 Hz of frequency resolution and a random comb on top of that, so
    whether the body's fundamental gets struck at all is luck — `chain_up_8` came
    out an octave sharp because its exciter happened to null at 1046 Hz and peak
    at 2093, and rung 6 was heading the same way. Noise excites something meant to
    sound irregular; this excites something meant to sound like a note.
    """
    n = max(int(round(SR * dur)), 8)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    out = np.roll(np.fft.irfft(_band_mask(f, lo, hi, edge), n), n // 2) * np.hanning(n)
    peak = float(np.max(np.abs(out)))
    return out / peak if peak > 0 else out


def modal_ir(dur: float, base: float, partials, decays, gains=None,
             gen: np.random.Generator | None = None, jitter: float = 0.0) -> np.ndarray:
    """Impulse response of a resonant body — one decaying sinusoid per mode.

    `metallic()` above bakes its own excitation into the result, so the body it
    models can only ever be struck one way, at t=0, by a click. Separating the
    body from the strike is what lets the same alloy ring be hit by a pebble
    (`ward_engage`) and by an armoured plate (`shutter_down`) and still be
    recognisably the same object — which is the whole reason the anchor sounds
    like a place rather than a list of sounds.

    Peak-normalized, because it is a filter and not a sound; the caller sets level.
    """
    x = t(dur)
    out = np.zeros(len(x))
    if gains is None:
        gains = [1.0 / (i + 1.4) for i in range(len(partials))]
    for i, ratio in enumerate(partials):
        f = base * ratio
        if jitter and gen is not None:
            f *= 1.0 + float(gen.uniform(-jitter, jitter))
        out += np.sin(2 * np.pi * f * x) * np.exp(-decays[i] * x) * gains[i]
    peak = float(np.max(np.abs(out)))
    return out / peak if peak > 0 else out


def strike(exciter: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Hit a resonant body with something. Convolution is the entire model.

    Keep the exciter short (a few ms): its spectrum is what decides which modes
    of the body actually speak, which is the difference between a knock and a
    scrape on identical hardware. The exciter must also reach BELOW the body's
    fundamental, or the fundamental is never struck and the object rings an
    octave high — see `_chain`, where that happened.

    Peak-normalized on the way out, like `band_noise` and `modal_ir`, because the
    peak of a convolution is not predictable from its inputs: the first cut of
    this layer had `impact_heavy`'s sub at a nominal 0.8 against a body whose
    convolution happened to peak near 3, so the mass that cue exists to convey
    ended up at 1% of its energy. Level is the caller's decision, not an accident
    of the arithmetic.
    """
    out = np.convolve(exciter, ir)
    peak = float(np.max(np.abs(out)))
    return out / peak if peak > 0 else out


def scatter(dur: float, gen: np.random.Generator, count: int, grain,
            bias: float = 3.0, span: float = 1.0) -> np.ndarray:
    """A cloud of tiny events — debris, shrapnel, crackle.

    Generalized out of `debris_settle`, which discovered the useful shape: place
    `count` grains over `span * dur`, biased toward the start by `u = rand**bias`,
    so density falls off the way a real fall of material does. `grain(gen, u)`
    is handed its own normalized position so a grain can know it is late and be
    quieter, smaller and more isolated for it.
    """
    out = np.zeros(int(round(SR * dur)))
    for _ in range(count):
        u = float(gen.random()) ** bias
        gr = grain(gen, u)
        start = int(u * span * dur * SR)
        end = min(start + len(gr), len(out))
        if end > start:
            out[start:end] += gr[: end - start]
    return out


def env_ar(dur: float, attack: float = 0.01, hold: float = 0.0,
           curve: float = 3.0, attack_curve: float = 1.0) -> np.ndarray:
    """Attack / hold / exponential release filling the rest of `dur`.

    `env_ad` above always starts decaying immediately, which is right for a strike
    and wrong for anything that has to *sustain* — a lance discharge, a surge, a
    plate travelling. Ends at exp(-curve) rather than at zero, so always finish
    with `fade_edges`.
    """
    x = t(dur)
    a = np.clip(x / max(attack, 1e-6), 0.0, 1.0) ** attack_curve
    rel = np.clip((x - attack - hold) / max(dur - attack - hold, 1e-6), 0.0, 1.0)
    return a * np.exp(-curve * rel)


def fade_edges(sig: np.ndarray, ms: float = 2.0) -> np.ndarray:
    """Ramp the first and last few ms to zero.

    A cue that ends mid-decay leaves a step at the file boundary, and a step is a
    click on every single playback — a defect that is inaudible in isolation and
    obvious when the sound plays four times a second. Never apply to a loop; there
    the seam is the point.
    """
    n = int(round(SR * ms / 1000.0))
    if n < 1 or n * 2 >= len(sig):
        return sig
    out = sig.copy()
    ramp = np.linspace(0.0, 1.0, n)
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def space(sig: np.ndarray, amount: float = 0.25, length: float = 0.3,
          gen: np.random.Generator | None = None, decay: float = 6.0) -> np.ndarray:
    """`tail()` for long signals, via FFT convolution.

    Identical in intent, different in cost: `tail` is O(n·m) and a 3 s cue with a
    0.45 s tail is 2.8 billion multiply-adds. Deliberately a separate function
    rather than a faster `tail`, because FFT convolution differs from the direct
    method in the last bits and swapping it in would rewrite all 36 cues that were
    authored and listened to against the old one.
    """
    if gen is None:
        gen = np.random.default_rng(0)
    m = int(round(SR * length))
    ir = gen.standard_normal(m) * np.exp(-decay * np.linspace(0.0, 1.0, m))
    nfft = 1 << int(np.ceil(np.log2(len(sig) + m)))
    wet = np.fft.irfft(np.fft.rfft(sig, nfft) * np.fft.rfft(ir, nfft), nfft)[: len(sig)]
    return sig + wet * amount / (np.sqrt(m) * 0.9)


def glue(sig: np.ndarray, drive: float = 1.5, peak: float = 1.0) -> np.ndarray:
    """Set the level, then soft-clip. The finisher for every combat cue.

    `saturate()` is a fixed curve, so what it *does* to a signal depends entirely
    on how hot that signal happens to arrive — the same `drive=1.5` is gentle
    warmth at peak 0.5 and a brick wall at peak 4. Scaling to a known peak first
    is what makes the drive number mean the same thing in every cue, and it is
    the difference between glue and a limiter nobody asked for.
    """
    m = float(np.max(np.abs(sig)))
    if m > 0:
        sig = sig * (peak / m)
    return saturate(sig, drive)


def soft_ceiling(sig: np.ndarray, ceiling: float = 0.95, knee: float = 0.62) -> np.ndarray:
    """Transparent below `knee`, asymptotic at `ceiling`.

    `limit()` scales the *whole* signal down to fit its peak, which is exactly
    wrong for a transient cue: it trades all of the loudness for the one sample
    that was over. This only touches what is over.
    """
    a = np.abs(sig)
    over = a > knee
    if not np.any(over):
        return sig
    out = sig.copy()
    excess = (a[over] - knee) / max(ceiling - knee, 1e-9)
    out[over] = np.sign(sig[over]) * (knee + (ceiling - knee) * np.tanh(excess))
    return out


# K-weighting, approximated analytically rather than as BS.1770's two biquads:
# the standard publishes coefficients at 48 kHz and this bank runs at 44.1, and
# re-deriving the shelf through the bilinear transform to chase a tenth of a dB
# would be precision the measurement does not have. Called LUFS-*ish* for that
# reason, and only ever used to compare cues in this bank against each other.
K_HPF_HZ = 38.0
K_SHELF_HZ = 1500.0
K_SHELF_DB = 4.0


def k_weight(sig: np.ndarray) -> np.ndarray:
    """Apply the K-weighting curve: RLB high-pass plus a high shelf."""
    n = len(sig)
    if n < 8:
        return sig
    f = np.fft.rfftfreq(n, 1.0 / SR)
    w = f / K_HPF_HZ
    hp = w ** 2 / np.sqrt((1.0 - w ** 2) ** 2 + (w / 0.5) ** 2)
    g = 10.0 ** (K_SHELF_DB / 20.0)
    v = f / K_SHELF_HZ
    shelf = np.sqrt((1.0 + (g * v) ** 2) / (1.0 + v ** 2))
    return np.fft.irfft(np.fft.rfft(sig) * hp * shelf, n)


def loudness(sig: np.ndarray, window: float = 0.100) -> float:
    """LUFS-ish: the K-weighted level of the LOUDEST `window` seconds.

    Integrated loudness is the wrong tool for a sound effect. `surge_fire` is 3.2 s
    and two thirds of it is tail; `impact_light` is 110 ms of which 100 is decay.
    Integrated over the file those two would have to be matched by making the surge
    enormous. Matching the loudest 100 ms instead is what the ear actually judges a
    short sound by. Files shorter than the window are zero-padded to it, so a 5 ms
    tick is not flattered by its own brevity.
    """
    y = k_weight(sig)
    w = int(round(SR * window))
    if len(y) < w:
        y = np.pad(y, (0, w - len(y)))
    c = np.concatenate(([0.0], np.cumsum(y * y)))
    e = (c[w:] - c[:-w]) / w
    return -0.691 + 10.0 * float(np.log10(max(float(e.max()), 1e-20)))


def match_loudness(sig: np.ndarray, target: float, ceiling: float = 0.95,
                   knee: float = 0.62) -> np.ndarray:
    """Loudness-match to `target` LUFS-ish, then hold the peaks with a soft knee.

    The K-weighted sibling of `normalize()`, and the reason both exist: RMS calls a
    60 Hz swell and a 6 kHz crack equally loud, and the ear does not. The combat
    layer spans exactly that range in one mix — a mortar under five pulse turrets —
    so it is matched on a weighted curve. The 36 cues authored before this are left
    on `normalize()`, because they were balanced against each other by ear and
    re-matching them would silently move a mix nobody asked to change.

    Iterates, because soft-clipping a peaky cue costs a little loudness and the
    correct answer is to give it back rather than to leave the cue quiet.
    """
    for _ in range(4):
        d = target - loudness(sig)
        if abs(d) < 0.05:
            break
        sig = soft_ceiling(sig * 10.0 ** (d / 20.0), ceiling, knee)
    return sig


def write_wav(path: Path, sig, sr=SR):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(sig, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())
    return path


# ────────────────────────────────────────────────────────────── sounds ──
# Each returns a mono float array. Keep them short; games layer, not sprawl.

def ui_click(g):
    click = noise(0.012, g) * env_ad(0.012, 0.0004, 40)
    click = highpass(bandpass(click, 2400, 0.6), 700)
    body = sine(880, 0.03) * env_ad(0.03, 0.001, 30) * 0.25
    return normalize(saturate(mix(click, body), 1.4), rms=0.10)


def ui_confirm(g):
    """Open fifth — Meridian's interval. Affirmative without being cheerful."""
    a = sine(587.33, 0.16) * env_ad(0.16, 0.004, 9)
    b = sine(880.00, 0.16, phase=0.4) * env_ad(0.16, 0.010, 8) * 0.75
    air = bandpass(noise(0.05, g), 5200, 0.5) * env_ad(0.05, 0.001, 26) * 0.18
    return normalize(saturate(tail(mix(a, b, air), 0.16, 0.12, g), 1.3), rms=0.11)


def ui_deny(g):
    """Minor second — the Ordinal's interval. Wrong on purpose."""
    a = sine(415.30, 0.20) * env_ad(0.20, 0.003, 11)
    b = sine(440.00, 0.20) * env_ad(0.20, 0.003, 11) * 0.9
    grit = bandpass(noise(0.08, g), 1400, 0.8) * env_ad(0.08, 0.002, 18) * 0.22
    return normalize(saturate(mix(a, b, grit), 1.8), rms=0.11)


def place_emplacement(g):
    thunk = sweep(190, 62, 0.13, curve=1.6) * env_ad(0.13, 0.001, 10)
    grit = lowpass(noise(0.09, g), 900, 0.3) * env_ad(0.09, 0.001, 16) * 0.5
    lock = metallic(0.22, 620, [1.0, 2.41, 3.77], g, decay=13) * 0.35
    return normalize(saturate(tail(mix(thunk, grit, lock), 0.2, 0.14, g), 1.6), rms=0.15)


def power_online(g):
    """Capacitor spin-up. Rises into the reactor's own 50 Hz world."""
    dur = 0.85
    whine = sweep(140, 1180, dur, curve=0.7) * np.clip(t(dur) / 0.25, 0, 1) * 0.35
    whine = bandpass(whine, 1500, 0.55)
    hum = sine(50, dur) * np.clip(t(dur) / dur, 0, 1) * 0.5
    hum += sine(100, dur) * np.clip(t(dur) / dur, 0, 1) * 0.22
    air = lowpass(noise(dur, g, pink=True), 2600) * np.clip(t(dur) / 0.5, 0, 1) * 0.10
    clunk = pad(metallic(0.18, 340, [1.0, 2.2, 3.9], g, decay=16) * 0.4, dur)
    clunk = np.roll(clunk, int(SR * 0.62))
    return normalize(saturate(mix(whine, hum, air, clunk), 1.5), rms=0.11)


def power_offline(g):
    dur = 0.7
    whine = sweep(980, 90, dur, curve=2.2) * np.exp(-3.0 * t(dur)) * 0.4
    whine = bandpass(whine, 1100, 0.6)
    sag = sine(50, dur) * np.exp(-4.5 * t(dur)) * 0.45
    thud = pad(sweep(120, 44, 0.22, 1.4) * env_ad(0.22, 0.001, 9) * 0.5, dur)
    return normalize(saturate(mix(whine, sag, thud), 1.4), rms=0.11)


def brownout_alarm(g):
    """Two-tone fault buzzer sagging under load. The mechanic, audible."""
    dur, out = 1.6, []
    for i in range(4):
        f = 466.16 if i % 2 == 0 else 349.23
        seg = 0.2
        sag = 1.0 - 0.06 * np.linspace(0, 1, int(round(SR * seg)))  # pitch droops = power failing
        s = np.sin(2 * np.pi * np.cumsum(f * sag) / SR)
        s = lowpass(s, 2200, 0.4) * np.clip(np.linspace(0, 1, int(round(SR * seg))) * 24, 0, 1)
        s *= np.clip((1 - np.linspace(0, 1, int(round(SR * seg)))) * 20, 0, 1)
        out.append(s * 0.55)
        out.append(np.zeros(int(SR * 0.2)))
    buzz = np.concatenate(out)
    hum = sine(50, len(buzz) / SR) * 0.28 + sine(150, len(buzz) / SR) * 0.10
    wobble = 1.0 + 0.03 * np.sin(2 * np.pi * 3.1 * t(len(buzz) / SR))  # tape flutter
    return normalize(saturate(mix(buzz * wobble, hum), 1.7), rms=0.13)


def reactor_hum_loop(g):
    """2.0 s seamless loop. All partials integer-cycle at 0.5 Hz resolution."""
    dur = 2.0
    x = t(dur)
    s = sine(50, dur) * 0.5 + sine(100, dur) * 0.24 + sine(150, dur) * 0.12 + sine(200, dur) * 0.06
    breathe = 1.0 + 0.10 * np.sin(2 * np.pi * 1.5 * x)  # 3 cycles in 2 s
    floor = loop_noise(lowpass(noise(dur + 0.05, g, pink=True), 1400)) * 0.055
    return normalize(saturate(s + floor[: len(s)], 1.3), rms=0.07)


def anchor_ambient_loop(g):
    """4.0 s seamless loop. Detuned cluster beating against itself."""
    dur = 4.0
    base = 110.0
    s = np.zeros(int(round(SR * dur)))
    for r, amp in [(1.0, 0.5), (1.5, 0.28), (2.25, 0.16), (3.0, 0.10)]:
        s += sine(base * r, dur) * amp
        s += sine(base * r + 0.5, dur) * amp * 0.7   # 0.5 Hz beat = 2 cycles in 4 s
    shimmer = loop_noise(bandpass(noise(dur + 0.05, g, pink=True), 3400, 0.7)) * 0.09
    swell = 1.0 + 0.14 * np.sin(2 * np.pi * 0.25 * t(dur))
    return normalize(saturate(s * swell * 0.35 + shimmer[: len(s)], 1.4), rms=0.055)


def turret_pulse_fire(g):
    dur = 0.26
    body = sweep(1250, 165, 0.11, curve=2.6) * env_ad(0.11, 0.0008, 13)
    ring = sine(1250 * 1.5, 0.09) * env_ad(0.09, 0.0005, 22) * 0.3
    crack = highpass(noise(0.018, g), 1800) * env_ad(0.018, 0.0002, 34) * 0.55
    sub = sweep(150, 55, 0.16, 1.8) * env_ad(0.16, 0.001, 9) * 0.45
    s = mix(pad(body, dur), pad(ring, dur), pad(crack, dur), pad(sub, dur))
    return normalize(saturate(tail(s, 0.14, 0.10, g), 1.7), rms=0.13)


def arc_node_fire(g):
    """Electrical, not ballistic. Flutter is what sells 'arc'."""
    dur = 0.34
    x = t(dur)
    flutter = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 92 * x) + g.uniform(-0.6, 0.6, len(x)))
    zap = bandpass(noise(dur, g), 2600, 0.88) * flutter * np.exp(-9.0 * x)
    chirp = sweep(2400, 380, 0.13, 2.0) * env_ad(0.13, 0.0006, 15) * 0.35
    sub = sweep(120, 48, 0.18, 1.5) * env_ad(0.18, 0.002, 10) * 0.35
    return normalize(saturate(tail(mix(zap, pad(chirp, dur), pad(sub, dur)), 0.18, 0.13, g), 1.8), rms=0.13)


def impact_metal(g):
    dur = 0.34
    hit = highpass(noise(0.02, g), 1200) * env_ad(0.02, 0.0002, 30) * 0.7
    ring = metallic(dur, 430, [1.0, 2.76, 5.40, 8.93], g, decay=11) * 0.55
    thud = sweep(180, 62, 0.10, 1.5) * env_ad(0.10, 0.001, 12) * 0.4
    return normalize(saturate(tail(mix(pad(hit, dur), ring, pad(thud, dur)), 0.2, 0.15, g), 1.6), rms=0.12)


def warden_death(g):
    """Construct failing: ring collapses, sub drops out, debris."""
    dur = 0.9
    ring = metallic(0.5, 350, [1.0, 2.41, 4.18, 6.77, 9.2], g, decay=6) * 0.5
    collapse = sweep(420, 58, 0.55, curve=2.2) * env_ad(0.55, 0.004, 5) * 0.45
    debris = lowpass(noise(dur, g), 2000) * np.exp(-4.5 * t(dur)) * 0.28
    debris *= (0.5 + 0.5 * g.random(len(debris)))
    sub = sweep(90, 32, 0.7, 1.6) * env_ad(0.7, 0.01, 4) * 0.5
    s = mix(pad(ring, dur), pad(collapse, dur), debris, pad(sub, dur))
    return normalize(saturate(tail(s, 0.26, 0.22, g), 1.7), rms=0.17)


def debris_settle(g):
    """Small rubble and dust falling after a construct dies.

    Sourced rather than synthesized was the plan (decision 009 listed debris among the
    twelve organic cues), and the CC0 corpus turned out to hold nothing for it — the two
    nearest items were a shovel and a "sand spell". That is a better outcome than it
    sounds, because the premise was wrong: granular debris is a *cloud of tiny impacts*,
    which is stochastic in exactly the way synthesis is good at. A voice is not; a
    hundred pebbles landing at decaying density is.

    Grains are scattered by the name-seeded generator, so this stays a pure function of
    its own name like every other cue in the bank. Decision 041.
    """
    dur = 1.10
    out = np.zeros(int(round(SR * dur)))
    # Density falls off as the pile settles: early grains crowd, late ones are isolated.
    count = 190
    for i in range(count):
        # Cube-biased toward zero, so most grains land in the first third.
        at = float(g.random() ** 3.0) * dur * 0.92
        grain_dur = 0.006 + 0.010 * float(g.random())
        grain = bandpass(noise(grain_dur, g), 700.0 + 2600.0 * float(g.random()), 0.6)
        grain *= env_ad(grain_dur, 0.0003, 26) * (0.10 + 0.55 * float(g.random()))
        start = int(at * SR)
        end = min(start + len(grain), len(out))
        out[start:end] += grain[: end - start]
    # Dust under the grains — the body of the fall rather than its individual stones.
    dust = lowpass(noise(dur, g), 900) * np.exp(-3.2 * t(dur)) * 0.22
    dust *= (0.45 + 0.55 * g.random(len(dust)))
    return normalize(saturate(mix(out, dust), 1.25), rms=0.11)


def ui_hover(g):
    s = sine(1320, 0.022) * env_ad(0.022, 0.0008, 34) * 0.5
    air = bandpass(noise(0.014, g), 6200, 0.4) * env_ad(0.014, 0.0003, 40) * 0.3
    return normalize(saturate(mix(s, air), 1.2), rms=0.055)


def ui_panel_open(g):
    dur = 0.30
    swish = bandpass(noise(dur, g), 2200, 0.55) * np.clip(t(dur) / 0.05, 0, 1) * np.exp(-7 * t(dur)) * 0.5
    rise = sweep(320, 760, 0.18, curve=0.8) * env_ad(0.18, 0.008, 6) * 0.4
    return normalize(saturate(mix(swish, pad(rise, dur)), 1.3), rms=0.09)


def ui_panel_close(g):
    dur = 0.26
    swish = bandpass(noise(dur, g), 1500, 0.55) * np.exp(-10 * t(dur)) * 0.5
    fall = sweep(700, 280, 0.15, curve=1.4) * env_ad(0.15, 0.003, 9) * 0.4
    return normalize(saturate(mix(swish, pad(fall, dur)), 1.3), rms=0.09)


def ui_purchase(g):
    """Two-note open fifth up. Spending resources should feel decisive."""
    a = sine(523.25, 0.13) * env_ad(0.13, 0.003, 12)
    b = pad(sine(783.99, 0.16) * env_ad(0.16, 0.004, 10) * 0.85, 0.29)
    b = np.roll(b, int(SR * 0.075))
    tick = pad(noise(0.01, g) * env_ad(0.01, 0.0003, 40) * 0.4, 0.29)
    return normalize(saturate(tail(mix(pad(a, 0.29), b, tick), 0.14, 0.11, g), 1.3), rms=0.105)


def ui_upgrade(g):
    """Three rising steps — the only ascending figure in the whole bank."""
    dur, out = 0.42, []
    for i, f in enumerate([523.25, 659.25, 783.99]):
        seg = pad(sine(f, 0.13) * env_ad(0.13, 0.003, 13), dur)
        out.append(np.roll(seg, int(SR * 0.075 * i)))
    shine = pad(bandpass(noise(0.10, g), 6800, 0.5) * env_ad(0.10, 0.002, 16) * 0.22, dur)
    return normalize(saturate(tail(mix(*out, np.roll(shine, int(SR * 0.15))), 0.16, 0.13, g), 1.3), rms=0.11)


def ui_sell(g):
    a = sine(659.25, 0.14) * env_ad(0.14, 0.003, 12)
    b = pad(sine(440.00, 0.17) * env_ad(0.17, 0.004, 10) * 0.8, 0.30)
    b = np.roll(b, int(SR * 0.08))
    return normalize(saturate(tail(mix(pad(a, 0.30), b), 0.13, 0.10, g), 1.3), rms=0.10)


def brownout_recover(g):
    """Bus back within capacity. Answers the alarm — same tones, resolving up."""
    dur = 0.9
    hum = sine(50, dur) * np.clip(t(dur) / 0.3, 0, 1) * 0.45 + sine(100, dur) * 0.2
    a = pad(sine(349.23, 0.22) * env_ad(0.22, 0.006, 8), dur)
    b = np.roll(pad(sine(523.25, 0.30) * env_ad(0.30, 0.008, 6) * 0.9, dur), int(SR * 0.14))
    steady = 1.0 - 0.05 * np.exp(-3 * t(dur))
    return normalize(saturate(mix(hum * steady, a, b), 1.4), rms=0.115)


def capacity_up(g):
    """Reactor tier increase. Story beat — allowed to be the biggest UI sound."""
    dur = 1.5
    swell = sweep(60, 240, 0.9, curve=0.6) * np.clip(t(0.9) / 0.4, 0, 1) * 0.5
    hum = sine(50, dur) * 0.4 + sine(100, dur) * 0.24 + sine(150, dur) * 0.12
    hum *= np.clip(t(dur) / 0.5, 0, 1)
    bell = np.roll(pad(metallic(0.7, 261.63, [1.0, 2.0, 3.0, 4.02], g, decay=4) * 0.6, dur), int(SR * 0.55))
    air = lowpass(noise(dur, g, pink=True), 3000) * np.clip(t(dur) / 0.6, 0, 1) * 0.09
    return normalize(saturate(tail(mix(pad(swell, dur), hum, bell, air), 0.2, 0.25, g), 1.5), rms=0.125)


def breaker_trip(g):
    dur = 0.5
    snap = highpass(noise(0.014, g), 2600) * env_ad(0.014, 0.0002, 32) * 0.8
    clack = metallic(0.2, 780, [1.0, 2.9, 5.1], g, decay=17) * 0.45
    drop = sweep(180, 42, 0.3, curve=1.3) * env_ad(0.3, 0.001, 7) * 0.45
    hum_die = sine(50, dur) * np.exp(-7 * t(dur)) * 0.35
    return normalize(saturate(tail(mix(pad(snap, dur), pad(clack, dur), pad(drop, dur), hum_die), 0.18, 0.14, g), 1.7), rms=0.13)


def overload_warn(g):
    """Pre-brownout warning. Same fault buzzer, single pulse, no pitch sag yet."""
    dur = 0.34
    tone = lowpass(sine(466.16, 0.26), 2400, 0.4) * env_ad(0.26, 0.006, 5)
    hum = sine(50, dur) * 0.22
    return normalize(saturate(mix(pad(tone, dur), hum), 1.6), rms=0.115)


def pulse_charge(g):
    dur = 0.45
    rise = sweep(180, 900, 0.4, curve=0.55) * np.clip(t(0.4) / 0.12, 0, 1) * 0.45
    rise = bandpass(rise, 1300, 0.5)
    shimmer = bandpass(noise(dur, g), 4200, 0.6) * np.clip(t(dur) / 0.35, 0, 1) * 0.12
    return normalize(saturate(mix(pad(rise, dur), shimmer), 1.5), rms=0.10)


def lance_fire(g):
    """Ion Lance — longer, tighter, more energy than the pulse turret."""
    dur = 0.5
    beam = sweep(2100, 420, 0.3, curve=1.9) * env_ad(0.3, 0.002, 7) * 0.55
    beam = bandpass(beam, 1900, 0.55)
    crack = highpass(noise(0.022, g), 2400) * env_ad(0.022, 0.0002, 28) * 0.6
    sub = sweep(190, 46, 0.34, 1.6) * env_ad(0.34, 0.002, 7) * 0.5
    sizzle = bandpass(noise(dur, g), 5400, 0.7) * np.exp(-8 * t(dur)) * 0.16
    return normalize(saturate(tail(mix(pad(beam, dur), pad(crack, dur), pad(sub, dur), sizzle), 0.2, 0.18, g), 1.8), rms=0.14)


def mortar_fire(g):
    dur = 0.6
    thump = sweep(140, 38, 0.28, curve=1.2) * env_ad(0.28, 0.001, 8) * 0.75
    air = lowpass(noise(0.2, g), 1100) * env_ad(0.2, 0.001, 11) * 0.45
    clank = metallic(0.25, 290, [1.0, 2.6, 4.3], g, decay=14) * 0.3
    return normalize(saturate(tail(mix(pad(thump, dur), pad(air, dur), pad(clank, dur)), 0.24, 0.2, g), 1.7), rms=0.15)


def flak_burst(g):
    dur = 0.42
    crack = highpass(noise(0.03, g), 1600) * env_ad(0.03, 0.0002, 22) * 0.8
    body = lowpass(noise(0.22, g), 2400) * env_ad(0.22, 0.001, 10) * 0.5
    sub = sweep(160, 50, 0.24, 1.5) * env_ad(0.24, 0.001, 9) * 0.45
    return normalize(saturate(tail(mix(pad(crack, dur), pad(body, dur), pad(sub, dur)), 0.22, 0.17, g), 1.8), rms=0.14)


def beam_loop(g):
    """1.0 s seamless loop — sustained beam weapons hold this while firing."""
    dur = 1.0
    s = sine(220, dur) * 0.4 + sine(330, dur) * 0.22 + sine(447, dur) * 0.12
    am = 1.0 + 0.22 * np.sin(2 * np.pi * 19 * t(dur))     # 19 cycles in 1 s
    sizzle = loop_noise(bandpass(noise(dur + 0.05, g), 4600, 0.75)) * 0.14
    return normalize(saturate(s * am + sizzle[: len(s)], 1.6), rms=0.10)


def impact_shield(g):
    """Energy, not metal — no inharmonic ring, just a bright elastic slap."""
    dur = 0.3
    slap = sweep(900, 300, 0.09, curve=2.2) * env_ad(0.09, 0.0006, 16) * 0.55
    fizz = bandpass(noise(0.16, g), 3600, 0.7) * env_ad(0.16, 0.0006, 13) * 0.4
    return normalize(saturate(tail(mix(pad(slap, dur), pad(fizz, dur)), 0.2, 0.14, g), 1.6), rms=0.115)


def shield_break(g):
    dur = 0.75
    shatter = bandpass(noise(0.3, g), 4800, 0.8) * env_ad(0.3, 0.0004, 9) * 0.55
    collapse = sweep(1400, 210, 0.4, curve=2.0) * env_ad(0.4, 0.001, 6) * 0.45
    sub = sweep(120, 40, 0.5, 1.5) * env_ad(0.5, 0.004, 5) * 0.4
    return normalize(saturate(tail(mix(pad(shatter, dur), pad(collapse, dur), pad(sub, dur)), 0.26, 0.22, g), 1.8), rms=0.15)


def ricochet(g):
    dur = 0.3
    tick = highpass(noise(0.01, g), 3200) * env_ad(0.01, 0.0002, 40) * 0.7
    whine = sweep(2600, 900, 0.2, curve=1.1) * env_ad(0.2, 0.002, 12) * 0.3
    whine = bandpass(whine, 2200, 0.75)
    return normalize(saturate(tail(mix(pad(tick, dur), pad(whine, dur)), 0.18, 0.13, g), 1.6), rms=0.10)


def crit_hit(g):
    dur = 0.4
    hit = highpass(noise(0.018, g), 1400) * env_ad(0.018, 0.0002, 28) * 0.8
    ring = metallic(dur, 620, [1.0, 2.83, 5.62], g, decay=9) * 0.5
    stab = sine(1244.5, 0.1) * env_ad(0.1, 0.0008, 18) * 0.3
    sub = sweep(200, 58, 0.16, 1.4) * env_ad(0.16, 0.001, 10) * 0.45
    return normalize(saturate(tail(mix(pad(hit, dur), ring, pad(stab, dur), pad(sub, dur)), 0.22, 0.16, g), 1.8), rms=0.155)


def warden_spawn(g):
    """Ordinal construct waking. Tritone — they were built by something afraid."""
    dur = 1.1
    wake = sweep(40, 165, 0.7, curve=0.6) * np.clip(t(0.7) / 0.3, 0, 1) * 0.5
    tri = pad(sine(220, 0.55) * env_ad(0.55, 0.05, 4) * 0.35, dur)
    tri += pad(sine(311.13, 0.55) * env_ad(0.55, 0.06, 4) * 0.3, dur)   # tritone
    servo = np.roll(pad(metallic(0.4, 480, [1.0, 2.37, 4.11], g, decay=8) * 0.35, dur), int(SR * 0.55))
    return normalize(saturate(tail(mix(pad(wake, dur), tri, servo), 0.22, 0.2, g), 1.6), rms=0.125)


def drone_hover_loop(g):
    """1.5 s seamless loop. Beating rotors, deliberately slightly unpleasant."""
    dur = 1.5
    s = sine(96, dur) * 0.4 + sine(144, dur) * 0.2 + sine(193, dur) * 0.14
    am = 1.0 + 0.3 * np.sin(2 * np.pi * 12 * t(dur))      # 18 cycles in 1.5 s
    wob = 1.0 + 0.08 * np.sin(2 * np.pi * 2 * t(dur))
    air = loop_noise(lowpass(noise(dur + 0.05, g, pink=True), 2200)) * 0.07
    return normalize(saturate(s * am * wob + air[: len(s)], 1.5), rms=0.075)


def heavy_stomp(g):
    dur = 0.7
    boom = sweep(90, 30, 0.35, curve=1.1) * env_ad(0.35, 0.002, 7) * 0.8
    crunch = lowpass(noise(0.12, g), 900) * env_ad(0.12, 0.001, 13) * 0.45
    plate = metallic(0.35, 180, [1.0, 2.1, 3.6], g, decay=11) * 0.3
    return normalize(saturate(tail(mix(pad(boom, dur), pad(crunch, dur), pad(plate, dur)), 0.26, 0.22, g), 1.7), rms=0.16)


# ══════════════════════════════════════════════════ combat: weapon fire ══
# Five weapons can be firing at once, so the constraint is not "does this sound
# good alone" but "can a player name it blind while four others are talking".
# Each family therefore owns one dimension outright and gives up the others:
#
#   pulse   — a hard dry transient and nothing after it. Owns the attack.
#   arc     — no transient worth the name; owns irregular amplitude (flutter).
#   lance   — owns *duration*: it is the only weapon that sustains.
#   flak    — owns hollowness: an odd-harmonic tube, launch and burst as a pair.
#   mortar  — owns the bottom octave. Nothing else in the bank lives below 60 Hz.
#
# Levels are set against the frequency of fire, not against importance. A pulse
# turret fires every 0.75 s and a player will hold twelve of them; a lance is a
# once-in-a-while event. Loud rare cues are exciting and loud constant cues are
# why people turn game audio off.


def _pulse(g: np.random.Generator, base: float, bright: float, sub: float,
           dur: float = 0.15) -> np.ndarray:
    """Shared body of the three pulse-turret variants.

    Energy discharged through hardware, not a laser: the tonal core drops a fifth
    and a half in 55 ms, which is a capacitor dumping rather than a beam. Rolled
    off above 8 kHz on purpose — this cue plays several times a second for an
    entire level, and the difference between "punchy" and "fatiguing" over ten
    minutes is entirely up there.

    The variants are re-synthesized rather than resampled. Pitch-shifting one
    recording gives three sounds with an identical noise transient, which is
    exactly the machine-gun artefact the variants exist to break.
    """
    crack = band_noise(0.004, g, 1600.0, 7000.0) * env_ad(0.004, 0.00015, 30) * bright
    core = sweep(base, base * 0.26, 0.055, curve=2.4) * env_ad(0.055, 0.0004, 14)
    ring = sine(base * 1.5, 0.035) * env_ad(0.035, 0.0003, 20) * 0.22
    thump = sweep(120.0, 52.0, 0.075, 1.4) * env_ad(0.075, 0.0008, 11) * sub
    s = mix(pad(crack, dur), pad(core, dur), pad(ring, dur), pad(thump, dur))
    s = lowpass(glue(s, 1.5), 8200.0, 0.1)
    return fade_edges(s, 1.0)


def fire_pulse(g):
    return match_loudness(_pulse(g, 880.0, 0.55, 0.42, 0.150), -23.0)


def fire_pulse_b(g):
    return match_loudness(_pulse(g, 806.0, 0.48, 0.47, 0.156), -23.0)


def fire_pulse_c(g):
    return match_loudness(_pulse(g, 962.0, 0.60, 0.38, 0.144), -23.0)


def fire_arc(g):
    """Arc node. Ionised air, not a projectile.

    The flutter is the whole cue: amplitude gated by a fast square whose edges are
    randomised, so the sound is continuously breaking and re-striking. Smooth it
    and this becomes a noise burst indistinguishable from flak. There is
    deliberately no low end and no hard transient — that is what keeps it legible
    underneath a pulse turret, which owns both.
    """
    dur = 0.22
    x = t(dur)
    gate = 0.32 + 0.68 * (np.sign(np.sin(2 * np.pi * 128.0 * x)
                                  + g.uniform(-0.75, 0.75, len(x))) * 0.5 + 0.5)
    zap = band_noise(dur, g, 1800.0, 9000.0, tilt_db=-1.5) * gate * np.exp(-16.0 * x) * 0.9
    snap = band_noise(0.003, g, 3000.0, 12000.0) * env_ad(0.003, 0.0001, 32) * 1.1
    chirp = sweep(3200.0, 620.0, 0.09, 2.2) * env_ad(0.09, 0.0004, 16) * 0.32
    crackle = scatter(dur, g, 26, _spark_grain, bias=1.8, span=0.95)
    s = mix(pad(snap, dur), zap, pad(chirp, dur), crackle)
    return match_loudness(fade_edges(tail(glue(s, 1.9), 0.10, 0.07, g), 1.0), -22.5)


def lance_charge(g):
    """Ion lance spooling. Fires on its own so the charge can lead the shot.

    Ends *rising* and unresolved. A charge that lands on a nice note is a chime;
    this has to feel like something is being withheld, so the last thing it does is
    still climbing when it stops.
    """
    dur = 0.30
    x = t(dur)
    rise = bandpass(sweep(150.0, 780.0, dur, curve=0.75), 1200.0, 0.5)
    rise *= np.clip(x / 0.05, 0, 1) * 0.5
    hum = (sine(50.0, dur) * 0.35 + sine(100.0, dur) * 0.20) * (x / dur) ** 1.6
    air = band_noise(dur, g, 900.0, 6500.0, tilt_db=-2.0) * (x / dur) ** 2.2 * 0.22
    grind = band_noise(dur, g, 200.0, 900.0) * (x / dur) ** 3.0 * 0.15
    return match_loudness(fade_edges(glue(mix(rise, hum, air, grind), 1.4), 2.0), -24.0)


def fire_lance(g):
    """The big gun, and the one that has to make the player feel armed.

    Carries 0.12 s of its own charge so it stands up alone; trigger `lance_charge`
    early and this lands on top of it rather than restating it. What makes it read
    as authority is the *hold* — 0.5 s of sustained discharge where every other
    weapon in the bank is already gone — plus a sub that outlasts the beam, so the
    room is still moving after the light stops.
    """
    dur, lead = 0.95, 0.12
    charge = bandpass(sweep(220.0, 1500.0, lead, 0.8), 1500.0, 0.5)
    charge = pad(charge * np.clip(t(lead) / 0.02, 0, 1) * 0.45, dur)
    crack = band_noise(0.005, g, 900.0, 9000.0) * env_ad(0.005, 0.0002, 26) * 1.3
    core = sweep(1500.0, 260.0, 0.5, 1.7) * env_ar(0.5, 0.0015, 0.10, 4.0) * 0.70
    hold = (sine(120.0, 0.5) * 0.30 + sine(180.0, 0.5) * 0.18) * env_ar(0.5, 0.005, 0.12, 4.0)
    sub = sweep(190.0, 44.0, 0.55, 1.5) * env_ar(0.55, 0.002, 0.12, 4.0) * 0.85
    sizzle = band_noise(0.6, g, 2500.0, 9000.0, tilt_db=-2.0) * np.exp(-5.5 * t(0.6)) * 0.30
    disc = glue(mix(crack, core, hold, sub, sizzle), 2.2)
    s = mix(charge, pad(np.pad(disc, (int(SR * lead), 0)), dur))
    return match_loudness(fade_edges(space(s, 0.24, 0.30, g), 2.0), -15.0)


def fire_flak(g):
    """Flak launch: a hollow thump, a tube rather than a gun.

    Odd harmonics only (1, 3, 5, 7, 9) — that is a pipe closed at one end, and it
    is where 'hollow' comes from. Paired with `flak_burst`; the launch is
    deliberately dull so the airburst has somewhere to go.
    """
    dur = 0.30
    puff = band_noise(0.09, g, 60.0, 700.0, tilt_db=-3.0) * env_ar(0.09, 0.001, 0.005, 7.0) * 0.8
    ir = modal_ir(0.26, 172.0, [1.0, 3.0, 5.0, 7.02, 9.1], [7, 11, 16, 22, 30],
                  gains=[1.0, 0.5, 0.28, 0.16, 0.09], gen=g, jitter=0.003)
    tube = strike(band_noise(0.004, g, 80.0, 2400.0) * env_ad(0.004, 0.0002, 24), ir) * 0.9
    thud = sweep(150.0, 58.0, 0.12, 1.3) * env_ad(0.12, 0.001, 10) * 0.55
    s = mix(pad(puff, dur), pad(tube, dur), pad(thud, dur))
    return match_loudness(fade_edges(tail(glue(s, 1.5), 0.12, 0.10, g), 2.0), -20.0)


def flak_burst(g):
    """The airburst. A crack, then shrapnel actually going somewhere.

    Rewritten for the combat layer: the previous version was a noise burst with a
    sub, which is a grenade rather than an airburst. The scatter is what makes it
    flak — fifty fragments thrown out over 400 ms at falling density, high and
    small, none of them where the crack was.
    """
    dur = 0.55
    crack = band_noise(0.006, g, 800.0, 12000.0) * env_ad(0.006, 0.00012, 24) * 1.4
    body = band_noise(0.13, g, 300.0, 4200.0, tilt_db=-2.0) * env_ar(0.13, 0.0008, 0.0, 9.0) * 0.6
    sub = sweep(180.0, 52.0, 0.20, 1.5) * env_ad(0.20, 0.001, 9) * 0.5
    shrap = scatter(dur, g, 55, _shrapnel_grain, bias=2.2, span=0.78)
    s = mix(pad(crack, dur), pad(body, dur), pad(sub, dur), shrap)
    return match_loudness(fade_edges(tail(glue(s, 1.8), 0.16, 0.14, g), 2.0), -17.0)


def fire_mortar(g):
    """Mortar launch: a deep thud and the air it moves.

    The displacement layer is band-limited to 40–520 Hz and given a 12 ms attack,
    which is slow enough to read as *mass shifting* rather than as an impact. Under
    it, a 105 → 30 Hz drop that ends below anything else in the bank.
    """
    dur = 0.60
    thud = sweep(105.0, 30.0, 0.30, 1.15) * env_ar(0.30, 0.0015, 0.02, 7.0)
    disp = band_noise(0.28, g, 40.0, 520.0, tilt_db=-4.0) * env_ar(0.28, 0.012, 0.02, 6.0) * 0.75
    ir = modal_ir(0.22, 128.0, [1.0, 2.05, 3.4], [9, 14, 20],
                  gains=[1.0, 0.4, 0.2], gen=g, jitter=0.004)
    clank = strike(band_noise(0.003, g, 200.0, 3000.0) * env_ad(0.003, 0.0002, 26), ir) * 0.35
    # A breech snap, quiet and very short. Without it this measured -43 dB above
    # 2 kHz — correct for "deep" and inaudible on a laptop, which is where most of
    # this will be heard. Enough presence to place the launch, not enough to give
    # it a treble character that would collide with the flak tube.
    snap = band_noise(0.004, g, 1500.0, 6000.0) * env_ad(0.004, 0.0002, 26) * 0.14
    s = mix(pad(thud, dur), pad(disp, dur), pad(clank, dur), pad(snap, dur))
    return match_loudness(fade_edges(tail(glue(s, 1.6), 0.20, 0.18, g), 2.0), -18.0)


def mortar_impact(g):
    """Where the shell lands. Low-end body, then debris.

    Two distinct events on purpose: the burst is over in 250 ms and the ground it
    struck keeps ringing for another 400, through a 62 Hz body with almost no
    damping. The debris tail is what tells the player the round *landed* rather
    than merely fired — the launch and the impact must never be confusable.
    """
    dur = 1.25
    crack = band_noise(0.008, g, 500.0, 9000.0) * env_ad(0.008, 0.00015, 20)
    burst = band_noise(0.22, g, 120.0, 2600.0, tilt_db=-3.0) * env_ar(0.22, 0.001, 0.0, 8.0) * 0.8
    sub = sweep(78.0, 26.0, 0.55, 1.25) * env_ar(0.55, 0.002, 0.04, 5.5) * 1.15
    ground = strike(band_noise(0.006, g, 60.0, 900.0) * env_ad(0.006, 0.0003, 20),
                    modal_ir(0.45, 62.0, [1.0, 1.94, 3.1], [5, 8, 13],
                             gains=[1.0, 0.4, 0.2], gen=g, jitter=0.006)) * 0.7
    debris = scatter(dur, g, 95, _rubble_grain, bias=2.6, span=0.92)
    s = mix(pad(crack, dur), pad(burst, dur), pad(sub, dur), pad(ground, dur), debris)
    return match_loudness(fade_edges(space(glue(s, 1.7), 0.22, 0.30, g), 2.0), -14.5)


# ═══════════════════════════════════════════════════════ combat: impacts ══
# Impacts teach weapon choice, so they are separated by *spectrum* rather than by
# level: light has no body, heavy has a body and a sub, shielded has no bottom at
# all. A player learns which of their guns is working from that, without reading.


def impact_light(g):
    """A shot landing on something unarmoured. Small, dry, gone in a tenth of a second."""
    dur = 0.11
    tick = band_noise(0.003, g, 1400.0, 8000.0) * env_ad(0.003, 0.0001, 28)
    ping = strike(click(0.0018, 500.0, 7000.0),
                  modal_ir(0.08, 940.0, [1.0, 2.31, 3.9], [26, 34, 46],
                           gains=[1.0, 0.45, 0.22], gen=g, jitter=0.006)) * 0.7
    dust = band_noise(0.04, g, 200.0, 1600.0) * env_ad(0.04, 0.0004, 16) * 0.25
    s = mix(pad(tick, dur), pad(ping, dur), pad(dust, dur))
    return match_loudness(fade_edges(glue(s, 1.3), 1.0), -24.5)


def impact_heavy(g):
    """A shot landing on mass. Same event as `impact_light` with an object behind it.

    The 236 Hz body rings for a third of a second where the light hit rings for
    eighty milliseconds, and there is a real sub under it. Deliberately the same
    shape so the two read as the same *action* on different targets.
    """
    dur = 0.38
    hit = band_noise(0.005, g, 700.0, 6500.0) * env_ad(0.005, 0.00015, 24)
    body = strike(band_noise(0.004, g, 100.0, 3000.0) * env_ad(0.004, 0.0002, 22),
                  modal_ir(0.34, 236.0, [1.0, 2.42, 4.05, 6.6], [8, 12, 17, 24],
                           gains=[1.0, 0.5, 0.26, 0.13], gen=g, jitter=0.005)) * 0.9
    thud = sweep(150.0, 46.0, 0.18, 1.3) * env_ad(0.18, 0.0012, 9) * 0.8
    s = mix(pad(hit, dur), pad(body, dur), pad(thud, dur))
    return match_loudness(fade_edges(tail(glue(s, 1.6), 0.14, 0.12, g), 2.0), -20.0)


def impact_shielded(g):
    """A shot that mostly did not get in. The one impact the player must not mishear.

    A teaching cue, not a texture, so every choice here is made to be *wrong*:

      - No low end at all. High-passed at 420 Hz, because nothing reached the hull,
        and the absence of a sub is the single clearest signal that the round was
        refused. It is what separates this from `impact_heavy` at any volume.
      - A flat, hard slab instead of a punch. The transient is sign()-squared band
        noise: a click reads as contact, a flat wall reads as a wall.
      - The ring is a tritone against itself — the Ordinal interval the bank
        already uses for 'invalid' (see `ui_deny`) — and it bends *up* by a
        semitone across its decay, so it reads as repelled rather than absorbed.

    Louder than the other two impacts on purpose: the player is meant to hear the
    mistake over five other turrets firing, which is when they are making it.
    """
    dur = 0.45
    slab = lowpass(np.sign(band_noise(0.006, g, 1100.0, 4200.0))
                   * env_ad(0.006, 0.0003, 14) * 0.55, 5200.0, 0.2)
    x = t(0.34)
    ring = np.zeros(len(x))
    for ratio, gn, dk in [(1.0, 1.0, 8.0), (np.sqrt(2.0), 0.55, 10.0), (2.06, 0.30, 13.0)]:
        f = 1860.0 * ratio * (1.0 + 0.055 * x / 0.34)
        ring += np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-dk * x) * gn
    ring *= 0.42
    bounce = band_noise(0.12, g, 2400.0, 9000.0, tilt_db=-1.0) * env_ar(0.12, 0.006, 0.0, 6.0) * 0.30
    s = highpass(mix(pad(slab, dur), pad(ring, dur), pad(bounce, dur)), 420.0)
    return match_loudness(fade_edges(tail(glue(s, 1.5), 0.18, 0.14, g), 2.0), -18.0)


# ════════════════════════════════════════════════════════ combat: deaths ══


def unit_shatter(g):
    """A construct coming apart. Brittle, never organic — nothing here bleeds.

    The body's second mode is louder than its fundamental and outlives it, so what
    collapses first is the thing's *shape*; then seventy ceramic fragments at
    falling density. There is no sub, which is what reserves that for the heavy.
    """
    dur = 0.65
    crack = band_noise(0.005, g, 900.0, 11000.0) * env_ad(0.005, 0.00012, 22)
    ir = modal_ir(0.40, 520.0, [1.0, 2.37, 3.81, 5.9, 8.4], [9, 7, 6, 5.5, 5],
                  gains=[0.9, 1.0, 0.8, 0.55, 0.35], gen=g, jitter=0.008)
    body = strike(band_noise(0.004, g, 300.0, 6000.0) * env_ad(0.004, 0.0002, 22), ir) * 0.8
    collapse = sweep(760.0, 150.0, 0.30, 2.0) * env_ad(0.30, 0.001, 7) * 0.30
    shards = scatter(dur, g, 70, _shard_grain, bias=2.4, span=0.90)
    s = mix(pad(crack, dur), pad(body, dur), pad(collapse, dur), shards)
    return match_loudness(fade_edges(tail(glue(s, 1.6), 0.18, 0.16, g), 2.0), -19.0)


def unit_shatter_heavy(g):
    """The same failure with real mass under it.

    An octave and a bit down on the body, a 70 → 24 Hz thud the light one does not
    have at all, and rubble instead of ceramic. Same event, heavier object — the
    player should hear the *class* of thing that died, not a different sound.
    """
    dur = 1.05
    crack = band_noise(0.007, g, 600.0, 9000.0) * env_ad(0.007, 0.00015, 20)
    ir = modal_ir(0.6, 232.0, [1.0, 2.37, 3.81, 5.9], [6, 5, 4.5, 4],
                  gains=[0.9, 1.0, 0.7, 0.4], gen=g, jitter=0.008)
    body = strike(band_noise(0.005, g, 150.0, 4000.0) * env_ad(0.005, 0.00025, 20), ir) * 0.85
    collapse = sweep(420.0, 74.0, 0.5, 2.0) * env_ad(0.5, 0.002, 5) * 0.35
    thud = sweep(70.0, 24.0, 0.6, 1.2) * env_ar(0.6, 0.003, 0.05, 5.0) * 0.95
    rubble = scatter(dur, g, 110, _rubble_grain, bias=2.4, span=0.94)
    s = mix(pad(crack, dur), pad(body, dur), pad(collapse, dur), pad(thud, dur), rubble)
    return match_loudness(fade_edges(space(glue(s, 1.7), 0.22, 0.26, g), 2.0), -16.5)


# ══════════════════════════════════════════════ pacing, chain and reward ══


# Pentatonic on G — no semitones, so any two rungs heard together are consonant
# and a streak that stutters never produces a clash. Rising a ninth over eight
# kills is enough to hear as a climb without ending up shrill.
CHAIN_STEPS = [392.00, 440.00, 523.25, 587.33, 659.25, 783.99, 880.00, 1046.50]


def _chain(g: np.random.Generator, i: int) -> np.ndarray:
    """One rung of the kill-chain ladder.

    A struck bar, not a coin: near-harmonic partials at 1 : 2 : 3 : 4 with the
    upper ones damped hard, so it has pitch but no shimmer. A fifth sits under it
    at -18 dB — audible as *width* rather than as a second note, which is the
    'instrument cluster' rather than 'reward jingle' the register asks for.

    Each rung is shorter, brighter and 0.35 dB QUIETER than the one below. Eight
    ticks inside 2.5 s is a lot of ticks, and a ladder that also crescendos is
    where a satisfying mechanic turns into a sound the player mutes.
    """
    f = CHAIN_STEPS[i]
    dur = 0.16
    # Every mode's decay scales with the rung, so the fundamental always outlives
    # its own partials. Damping only the fundamental as the ladder rose left the
    # octave ringing longest at the top, and rungs 6 and 8 measured an octave
    # sharp — a ladder that stops being a ladder exactly where it should pay off.
    dk = 16.0 + i * 1.2
    ir = modal_ir(0.14, f, [1.0, 2.0, 3.01, 4.02], [dk, dk * 1.35, dk * 1.9, dk * 2.4],
                  gains=[1.0, 0.34, 0.15, 0.07], gen=g, jitter=0.001)
    # The exciter has to reach BELOW the lowest rung. Banded at 600 Hz it left
    # chain_up_1's 392 Hz fundamental unstruck and the second partial louder than
    # the note, so the bottom of the ladder read a fifth sharp — the modal model
    # working exactly as specified, against a mistuned strike.
    hit = strike(click(0.0022, 250.0, 6000.0), ir)
    fifth = sine(f * 1.5, 0.09) * env_ad(0.09, 0.0015, 18) * 0.12
    s = mix(pad(hit, dur), pad(fifth, dur))
    return match_loudness(fade_edges(glue(s, 1.2), 1.0), -23.5 - 0.35 * i)


def clean_sweep(g):
    """A wave cleared with nothing leaked. Warm, brief, and not a fanfare.

    An open fifth with the octave arriving late, low-passed and barely reverbed.
    These people do this for a living; the correct sound for a job done properly
    is a nod, not applause.
    """
    dur = 0.85
    a = pad(sine(174.61, 0.50) * env_ar(0.50, 0.012, 0.08, 4.0) * 0.55, dur)
    b = pad(sine(261.63, 0.55) * env_ar(0.55, 0.014, 0.08, 4.0) * 0.45, dur)
    c = np.roll(pad(sine(349.23, 0.45) * env_ar(0.45, 0.020, 0.05, 5.0) * 0.30, dur),
                int(SR * 0.10))
    warm = pad(lowpass(band_noise(0.35, g, 80.0, 1400.0), 1800.0, 0.1)
               * env_ar(0.35, 0.02, 0.0, 5.0) * 0.10, dur)
    # Three low sines and nothing else measured -50 dB above 2 kHz, which is not
    # warm, it is muffled — and inaudible over a board that is still ringing. The
    # upper octave and a breath of air give it presence without brightening it.
    top = np.roll(pad(sine(698.46, 0.30) * env_ar(0.30, 0.03, 0.02, 6.0) * 0.09, dur),
                  int(SR * 0.10))
    air = pad(band_noise(0.28, g, 2600.0, 8000.0, tilt_db=-3.0)
              * env_ar(0.28, 0.015, 0.0, 7.0) * 0.05, dur)
    return match_loudness(fade_edges(tail(glue(mix(a, b, c, warm, top, air), 1.3), 0.10, 0.10, g), 2.0), -18.0)


def wave_call(g):
    """The player pulling the next wave in early. A switch thrown, consequences taken.

    Three beats in 300 ms and the order is the meaning: travel, then the detent,
    then the bus answering underneath — 50 Hz swelling in *after* the switch, so
    the sound is cause and effect rather than a click. The low drop is what makes
    it committing; a detent alone would just be a button.
    """
    dur = 0.75
    travel = band_noise(0.035, g, 700.0, 4500.0) * env_ar(0.035, 0.004, 0.0, 5.0) * 0.35
    detent = strike(click(0.0020, 350.0, 7000.0),
                    modal_ir(0.16, 780.0, [1.0, 2.71, 4.9], [22, 30, 40],
                             gains=[1.0, 0.4, 0.2], gen=g, jitter=0.004)) * 0.8
    detent = np.pad(detent, (int(SR * 0.035), 0))
    drop = np.pad(sweep(96.0, 42.0, 0.32, 1.2) * env_ar(0.32, 0.002, 0.01, 6.0) * 0.7,
                  (int(SR * 0.04), 0))
    bus = np.pad((sine(50.0, 0.50) * 0.40 + sine(100.0, 0.50) * 0.18)
                 * env_ar(0.50, 0.06, 0.12, 4.0), (int(SR * 0.05), 0))
    s = mix(pad(travel, dur), pad(detent, dur), pad(drop, dur), pad(bus, dur))
    return match_loudness(fade_edges(tail(glue(s, 1.6), 0.14, 0.14, g), 2.0), -18.0)


def _detent(g: np.random.Generator, lock_hz: float, glide: float) -> np.ndarray:
    """A speed-control detent: a scrape of travel, then the stop.

    Terse by contract — this fires on a key the player will hold down. Direction
    lives in the 20 ms glide into the lock rather than in a pitch difference alone,
    because two clicks a fourth apart are a pair of clicks and a click that *slides
    into place* has a direction even heard in isolation.
    """
    dur = 0.11
    off = int(SR * 0.016)
    travel = band_noise(0.014, g, 900.0, 5200.0) * env_ar(0.014, 0.003, 0.0, 5.0) * 0.28
    tone = sweep(lock_hz * glide, lock_hz, 0.02, 1.0) * env_ad(0.02, 0.0008, 18) * 0.25
    lock = strike(click(0.0018, 400.0, 7000.0),
                  modal_ir(0.09, lock_hz, [1.0, 2.83, 5.2], [26, 34, 46],
                           gains=[1.0, 0.35, 0.16], gen=g, jitter=0.004)) * 0.85
    s = mix(pad(travel, dur), pad(np.pad(lock, (off, 0)), dur),
            pad(np.pad(tone, (off, 0)), dur))
    return fade_edges(glue(s, 1.4), 1.0)


def speed_up(g):
    return match_loudness(_detent(g, 1120.0, 0.82), -22.0)


def speed_down(g):
    return match_loudness(_detent(g, 760.0, 1.22), -22.0)


# ═══════════════════════════════════════════════════════════ abilities ══


def surge_ready(g):
    """Threshold Surge charged. Felt rather than heard.

    A 350 ms attack on a 43.5 Hz swell — slow enough that it arrives without an
    onset, which is what "felt" means in practice. Deliberately among the quietest
    good news in the game: it is announcing the means to end a fight that is
    currently happening, and it must not step on the fight.
    """
    dur = 0.90
    swell = (sine(43.5, dur) * 0.6 + sine(87.0, dur) * 0.3 + sine(130.5, dur) * 0.12)
    swell *= env_ar(dur, 0.35, 0.15, 3.0, attack_curve=2.0)
    bell = strike(click(0.0030, 150.0, 3500.0),
                  modal_ir(0.60, 220.0, [1.0, 1.5, 3.0], [4, 5, 7],
                           gains=[1.0, 0.5, 0.2], gen=g, jitter=0.002)) * 0.35
    bell = np.pad(bell, (int(SR * 0.30), 0))
    air = band_noise(dur, g, 1500.0, 7000.0, tilt_db=-3.0) * env_ar(dur, 0.40, 0.10, 4.0) * 0.06
    s = mix(swell, pad(bell, dur), air)
    return match_loudness(fade_edges(tail(glue(s, 1.3), 0.14, 0.20, g), 3.0), -23.0)


def surge_fire(g):
    """The ring discharging its threshold down the lane. The biggest sound in the game.

    Three movements, timed to the fiction rather than to a shape that sounded good:

      0.00 s  intake — a band of noise sweeping up under a rising 70 → 300 Hz tone,
              the ring gathering everything it has.
      0.42 s  release — broadband slam plus a 120 → 34 Hz hit. This is the moment,
              and everything before it exists to make it land.
      0.42 s+ the wall, LEAVING. Four fixed noise bands whose gains are Gaussian
              humps walking down the spectrum in sequence, which is a moving
              formant without a time-varying filter — and a moving formant is what
              makes something sound like it is travelling away. A fade sounds like
              the sound stopping; this sounds like the wall getting further off.
              A 5.5 Hz flutter slows exponentially under it, for the same reason.

    Under all of it, 41.2 / 82.4 / 123.6 Hz — the deepest sustained tone the bank
    has — resolving into an open fifth at 110/165 as the tail settles. It gets a
    2.8 s tail because this is the only cue in the game allowed to have one.
    """
    dur, rel = 3.20, 0.42
    inhale = band_noise(rel, g, 200.0, 5000.0, tilt_db=-2.0)
    inhale *= env_ar(rel, rel * 0.92, 0.0, 0.4, attack_curve=2.6) * 0.5
    up = sweep(70.0, 300.0, rel, curve=1.6) * (t(rel) / rel) ** 2.0 * 0.40

    slam = band_noise(0.012, g, 60.0, 11000.0, tilt_db=-3.0) * env_ad(0.012, 0.0002, 16) * 1.6
    hit = sweep(120.0, 34.0, 0.55, 1.2) * env_ar(0.55, 0.002, 0.03, 4.5) * 1.4

    wdur = dur - rel
    wx = t(wdur)
    prog = wx / wdur
    bands = [(3000.0, 9000.0), (1200.0, 3600.0), (450.0, 1500.0), (150.0, 560.0)]
    wall = np.zeros(len(wx))
    for i, (lo, hi) in enumerate(bands):
        centre = i / (len(bands) - 1)
        wall += band_noise(wdur, g, lo, hi, tilt_db=-1.0) * np.exp(-((prog - centre) ** 2) / (2 * 0.20 ** 2))
    wall *= np.exp(-1.15 * prog) * 0.55
    wall *= 1.0 + 0.18 * np.sin(2 * np.pi * 5.5 * wx * np.exp(-0.6 * prog))
    drone = (sine(41.2, wdur) * 0.70 + sine(82.4, wdur) * 0.34 + sine(123.6, wdur) * 0.14)
    drone *= env_ar(wdur, 0.02, 0.35, 3.4)
    resolve = (sine(110.0, wdur) * 0.30 + sine(165.0, wdur) * 0.20) * env_ar(wdur, 0.05, 0.10, 5.0) * 0.35
    body = glue(wall + drone + resolve, 1.7)

    s = mix(pad(inhale, dur), pad(up, dur),
            pad(np.pad(mix(slam, hit), (int(SR * rel), 0)), dur),
            pad(np.pad(body, (int(SR * rel), 0)), dur))
    return match_loudness(fade_edges(space(glue(s, 1.6), 0.30, 0.45, g), 4.0), -7.0)


def overcharge_on(g):
    """The bus deliberately pushed past its rating.

    The strain is not a layer, it is the drive: mains hum climbs 50 → 58 Hz, its
    harmonics pile on in sequence as the load rises, and the saturation goes from
    1.2 to 3.8 across the same span — so the distortion is *caused* by the thing
    the player did rather than pasted over it. Same 50 Hz world as `power_online`
    and `brownout_alarm`, because it is the same bus.
    """
    dur = 1.15
    x = t(dur)
    prog = np.clip(x / (dur * 0.8), 0.0, 1.0)
    ph = 2 * np.pi * np.cumsum(50.0 * (1.0 + 0.16 * prog)) / SR
    hum = (np.sin(ph) * 0.55 + np.sin(2 * ph) * 0.30 * prog
           + np.sin(3 * ph) * 0.18 * prog ** 2 + np.sin(4 * ph) * 0.10 * prog ** 3)
    whine = bandpass(sweep(190.0, 940.0, dur, 1.1), 1500.0, 0.55) * prog ** 1.4 * 0.35
    rattle = band_noise(dur, g, 700.0, 5200.0, tilt_db=-2.0) * prog ** 2.2
    rattle *= (0.6 + 0.4 * np.sin(2 * np.pi * 17.0 * x)) * 0.16
    # Drive rises with the load and stops well short of a square wave: pushed to
    # 3.8 this measured a 3.5 dB crest factor, which is not a strained bus, it is
    # a fuzz pedal. 2.4 keeps the harmonics piling on and the waveform intact.
    s = np.tanh((hum + whine + rattle) * (1.1 + 1.3 * prog)) * 0.5
    return match_loudness(fade_edges(space(s, 0.12, 0.25, g), 3.0), -18.5)


def overcharge_off(g):
    """Coming off overcharge. The relief is the point, so it is mostly a settle.

    Hum sags 58 → 50 Hz and then *steadies* — a 6 Hz wobble decaying to nothing —
    which is the audible half of "back within rating". Answers `overcharge_on`
    exactly the way `brownout_recover` answers `brownout_alarm`.
    """
    dur = 1.00
    x = t(dur)
    prog = np.clip(x / (dur * 0.45), 0.0, 1.0)
    ph = 2 * np.pi * np.cumsum(58.0 - 8.0 * prog) / SR
    hum = (np.sin(ph) * 0.50 + np.sin(2 * ph) * 0.22 * (1.0 - 0.6 * prog)
           + np.sin(3 * ph) * 0.10 * (1.0 - prog))
    hum *= 1.0 - 0.05 * np.exp(-4.0 * x) * np.sin(2 * np.pi * 6.0 * x)
    fall = pad(bandpass(sweep(900.0, 170.0, 0.45, 1.6), 1200.0, 0.5) * np.exp(-3.2 * t(0.45)) * 0.40, dur)
    clunk = strike(band_noise(0.0025, g, 300.0, 4000.0) * env_ad(0.0025, 0.00012, 26),
                   modal_ir(0.20, 300.0, [1.0, 2.4, 4.1], [16, 22, 30],
                            gains=[1.0, 0.4, 0.18], gen=g, jitter=0.004)) * 0.40
    clunk = pad(np.pad(clunk, (int(SR * 0.42), 0)), dur)
    breath = band_noise(dur, g, 200.0, 2600.0, tilt_db=-3.0) * np.exp(-3.5 * x) * 0.14
    s = mix(hum, fall, clunk, breath)
    return match_loudness(fade_edges(space(glue(s, 1.5), 0.12, 0.22, g), 3.0), -19.5)


def shutter_down(g):
    """An armoured plate driven over the ring.

    The lock is the whole cue. A 0.66 s grind that simply stops has no consequence,
    and the player has to know the plate is *down* rather than still moving — so
    the travel is chatter-modulated and rising in effort, and it terminates in a
    slam, a 118 Hz alloy body, and a lock pin 35 ms behind it. Two-stage, because
    real heavy machinery seats and then latches.
    """
    dur, lock_at = 1.15, 0.66
    tx = t(lock_at)
    tp = tx / lock_at
    grind = band_noise(lock_at, g, 60.0, 1500.0, tilt_db=-3.0) * (0.35 + 0.65 * np.clip(tp * 6, 0, 1))
    grind *= 0.65 + 0.35 * np.sin(2 * np.pi * 23.0 * tx) * np.sin(2 * np.pi * 7.0 * tx)
    grind *= 0.55 * (1.0 + 0.5 * tp)
    strain = (sine(78.0, lock_at) * 0.25 + sine(117.0, lock_at) * 0.12) * tp ** 1.5

    hit = band_noise(0.010, g, 80.0, 8000.0) * env_ad(0.010, 0.0002, 18) * 1.3
    slam = sweep(140.0, 38.0, 0.40, 1.2) * env_ar(0.40, 0.002, 0.02, 5.0) * 1.2
    plate = strike(band_noise(0.005, g, 100.0, 3000.0) * env_ad(0.005, 0.0002, 20),
                   modal_ir(0.45, 118.0, [1.0, 2.31, 3.9, 6.2], [7, 11, 16, 22],
                            gains=[1.0, 0.45, 0.22, 0.1], gen=g, jitter=0.005)) * 0.8
    pin = strike(band_noise(0.0015, g, 900.0, 7000.0) * env_ad(0.0015, 0.00008, 28),
                 modal_ir(0.10, 1420.0, [1.0, 2.7, 4.6], [28, 36, 48],
                          gains=[1.0, 0.3, 0.14], gen=g, jitter=0.004)) * 0.35
    lock = mix(hit, slam, plate, np.pad(pin, (int(SR * 0.035), 0)))
    s = mix(pad(grind + strain, dur), pad(np.pad(lock, (int(SR * lock_at), 0)), dur))
    return match_loudness(fade_edges(space(glue(s, 1.7), 0.16, 0.30, g), 3.0), -16.0)


def shutter_up(g):
    """The plate coming off. `shutter_down` run backwards as an *event order*.

    Latch releases first, then the travel — which is the reverse of the way down,
    and reversing the order is what makes it read as the same machine going the
    other way. It ends soft: nothing slams, the plate just finishes its run and
    settles, and the ring is exposed again with no ceremony because what happens
    next is the queued wave arriving all at once.
    """
    dur, travel_at = 0.95, 0.10
    release = strike(band_noise(0.0015, g, 800.0, 8000.0) * env_ad(0.0015, 0.00008, 28),
                     modal_ir(0.12, 1180.0, [1.0, 2.7, 4.6], [24, 32, 44],
                              gains=[1.0, 0.32, 0.15], gen=g, jitter=0.004)) * 0.55
    unseat = sweep(120.0, 62.0, 0.14, 1.4) * env_ad(0.14, 0.0015, 9) * 0.45

    tdur = dur - travel_at
    tx = t(tdur)
    tp = tx / tdur
    grind = band_noise(tdur, g, 70.0, 1700.0, tilt_db=-3.0) * env_ar(tdur, 0.06, 0.30, 3.2)
    grind *= 0.60 + 0.40 * np.sin(2 * np.pi * 21.0 * tx) * np.sin(2 * np.pi * 6.0 * tx)
    grind *= 0.5 * (1.4 - 0.5 * tp)
    lift = (sine(88.0, tdur) * 0.22 + sine(132.0, tdur) * 0.10) * env_ar(tdur, 0.08, 0.20, 3.6)
    settle = np.pad(strike(band_noise(0.003, g, 150.0, 2500.0) * env_ad(0.003, 0.0002, 22),
                           modal_ir(0.25, 132.0, [1.0, 2.31, 3.9], [10, 15, 21],
                                    gains=[1.0, 0.4, 0.18], gen=g, jitter=0.005)) * 0.35,
                    (int(SR * 0.62), 0))
    s = mix(pad(release, dur), pad(unseat, dur),
            pad(np.pad(grind + lift, (int(SR * travel_at), 0)), dur), pad(settle, dur))
    return match_loudness(fade_edges(space(glue(s, 1.6), 0.16, 0.26, g), 3.0), -17.5)


# ════════════════════════════════════════════════════ the anchor itself ══


# Six wards, six steps, G3 up to E4. Rising through a major pentatonic so the
# ring charging is a phrase rather than six unrelated knocks — and so the sixth
# is unmistakably the last one.
WARD_STEPS = [196.00, 220.00, 246.94, 261.63, 293.66, 329.63]


def _ward(g: np.random.Generator, i: int) -> np.ndarray:
    """One of the six wards locking. Stone on alloy: a dry knock into a body that rings.

    Everything except pitch is held identical across all six, and that is the
    design rather than laziness. A count you can hear is worth more than six
    sounds that are merely different: the player should be able to tell how far
    the ring has charged without looking, and that only works if the *only* thing
    changing is the step. Level rises 0.3 dB per ward for the same reason.

    The knock is band-limited under 1.8 kHz — stone, not metal — and the thing it
    strikes is inharmonic at 1 : 2.41 : 3.77, which is alloy. Precursor hardware:
    old, heavy, and not made of panels.
    """
    f = WARD_STEPS[i]
    dur = 0.55
    knock = band_noise(0.006, g, 90.0, 1800.0, tilt_db=-3.0) * env_ad(0.006, 0.0003, 20) * 0.9
    ir = modal_ir(0.45, f, [1.0, 2.41, 3.77, 5.9], [6.0, 9, 13, 19],
                  gains=[1.0, 0.42, 0.20, 0.09], gen=g, jitter=0.003)
    alloy = strike(click(0.0035, 120.0, 4500.0), ir) * 0.85
    seat = sweep(f * 0.42, f * 0.16, 0.20, 1.3) * env_ad(0.20, 0.0015, 8) * 0.55
    grit = band_noise(0.05, g, 300.0, 2600.0) * env_ad(0.05, 0.0008, 14) * 0.18
    s = mix(pad(knock, dur), pad(alloy, dur), pad(seat, dur), pad(grit, dur))
    return match_loudness(fade_edges(tail(glue(s, 1.6), 0.16, 0.16, g), 2.0), -18.5 + 0.30 * i)


def threshold_idle(g):
    """2.0 s seamless bed for an engaged ring. Restless rather than droning.

    Loops exactly by construction, the same way `reactor_hum_loop` does: every
    partial is a multiple of 0.5 Hz and every modulator completes a whole number
    of cycles in 2.0 s (0.5, 1.5, 2.5 Hz — one, three and five). Because those are
    coprime in phase offset, the unease never lands on the same beat twice inside
    a pass, which is what stops a short loop announcing its own length.

    The 55/55.5 Hz pair beats once per loop; that slow swell is the ring being
    *held* rather than idling. No `fade_edges` here — on a loop the seam is the
    point, and a ramp at the boundary is the one thing that would click.
    """
    dur = 2.00
    x = t(dur)
    s = np.zeros(len(x))
    for f, a in [(55.0, 0.55), (82.5, 0.26), (110.0, 0.16), (137.5, 0.07), (192.5, 0.04)]:
        s += sine(f, dur) * a
    s += sine(55.5, dur) * 0.30 + sine(83.0, dur) * 0.14
    restless = (1.0 + 0.13 * np.sin(2 * np.pi * 0.5 * x)
                + 0.07 * np.sin(2 * np.pi * 1.5 * x + 1.1)
                + 0.04 * np.sin(2 * np.pi * 2.5 * x + 2.2))
    floor = loop_noise(band_noise(dur + 0.05, g, 90.0, 1300.0, tilt_db=-2.0)) * 0.07
    return match_loudness(glue(s * restless * 0.5 + floor[: len(s)], 1.3), -27.5)


# ─────────────────────────────────────────────────────── grain recipes ──
# Handed to scatter(). Each takes the generator and the grain's own normalized
# position, so a late grain can be smaller and quieter than an early one — which
# is what a real cloud of fragments does and what a uniform scatter never does.


def _spark_grain(gen: np.random.Generator, u: float) -> np.ndarray:
    """A single re-strike inside an electrical arc. Tiny, high, and very short."""
    lo = 2500.0 + 4000.0 * float(gen.random())
    d = 0.0020 + 0.0025 * float(gen.random())
    return (band_noise(d, gen, lo, lo * 2.4, edge=0.6) * env_ad(d, 0.0001, 30)
            * (0.05 + 0.25 * (1.0 - u)))


def _shrapnel_grain(gen: np.random.Generator, u: float) -> np.ndarray:
    """One flak fragment leaving. Higher and shorter than debris; it is still moving."""
    lo = 2400.0 + 5200.0 * float(gen.random())
    d = 0.0020 + 0.0050 * float(gen.random())
    return (band_noise(d, gen, lo, min(lo * 2.2, 18000.0), edge=0.6) * env_ad(d, 0.00012, 30)
            * (0.05 + 0.28 * (1.0 - u) ** 2))


def _shard_grain(gen: np.random.Generator, u: float) -> np.ndarray:
    """A ceramic fragment landing. Bright and brittle — this is what 'not organic' sounds like."""
    lo = 1500.0 + 4500.0 * float(gen.random())
    d = 0.0030 + 0.0080 * float(gen.random())
    return (band_noise(d, gen, lo, lo * 2.6, edge=0.6) * env_ad(d, 0.0002, 26)
            * (0.05 + 0.30 * (1.0 - u) ** 1.5))


def _rubble_grain(gen: np.random.Generator, u: float) -> np.ndarray:
    """A piece of masonry landing. Lower, longer and duller than a shard."""
    lo = 180.0 + 1400.0 * float(gen.random())
    d = 0.0060 + 0.0160 * float(gen.random())
    return (band_noise(d, gen, lo, lo * 3.0, edge=0.6) * env_ad(d, 0.0004, 20)
            * (0.06 + 0.34 * (1.0 - u) ** 1.2))


def _bind(fn, i: int):
    """Bind an index into a bank entry. `lambda g: fn(g, i)` in a loop captures the
    LAST i for every entry — the classic late-binding bug, and it would silently
    render eight identical chain ticks."""
    return lambda g: fn(g, i)


BANK = {
    "ui_click":            (ui_click,            "UI tick — menus, hover, increment"),
    "ui_confirm":          (ui_confirm,          "Affirmative — open fifth (Meridian)"),
    "ui_deny":             (ui_deny,             "Invalid action — minor second (Ordinal)"),
    "place_emplacement":   (place_emplacement,   "Emplacement seated on a tile"),
    "power_online":        (power_online,        "System spin-up onto the bus"),
    "power_offline":       (power_offline,       "System cut from the bus"),
    "brownout_alarm":      (brownout_alarm,      "Bus overdraw — the core mechanic, audible"),
    "reactor_hum_loop":    (reactor_hum_loop,    "Reactor bed — 2.0 s seamless loop"),
    "anchor_ambient_loop": (anchor_ambient_loop, "Anchor ring bed — 4.0 s seamless loop"),
    "turret_pulse_fire":   (turret_pulse_fire,   "Pulse Turret discharge"),
    "arc_node_fire":       (arc_node_fire,       "Arc Node discharge"),
    "impact_metal":        (impact_metal,        "Projectile on armour"),
    "warden_death":        (warden_death,        "Ordinal warden destroyed"),
    "ui_hover":            (ui_hover,            "UI hover — the quietest sound in the game"),
    "ui_panel_open":       (ui_panel_open,       "Panel opens"),
    "ui_panel_close":      (ui_panel_close,      "Panel closes"),
    "ui_purchase":         (ui_purchase,         "Resources spent — rising fifth"),
    "ui_upgrade":          (ui_upgrade,          "Emplacement upgraded — three rising steps"),
    "ui_sell":             (ui_sell,             "Emplacement sold — falling fifth"),
    "overload_warn":       (overload_warn,       "Approaching capacity — one pulse, no sag yet"),
    "brownout_recover":    (brownout_recover,    "Bus back within capacity"),
    "capacity_up":         (capacity_up,         "Reactor tier increased — a story beat"),
    "breaker_trip":        (breaker_trip,        "Breaker throws, hum dies"),
    "pulse_charge":        (pulse_charge,        "Pulse Turret spooling"),
    "lance_fire":          (lance_fire,          "Ion Lance discharge"),
    "mortar_fire":         (mortar_fire,         "Mortar launch"),
    "flak_burst":          (flak_burst,          "Flak airburst"),
    "beam_loop":           (beam_loop,           "Sustained beam — 1.0 s seamless loop"),
    "impact_shield":       (impact_shield,       "Projectile on shielding"),
    "shield_break":        (shield_break,        "Shield collapses"),
    "ricochet":            (ricochet,            "Deflection"),
    "crit_hit":            (crit_hit,            "Critical strike"),
    "warden_spawn":        (warden_spawn,        "Ordinal construct wakes — tritone"),
    "drone_hover_loop":    (drone_hover_loop,    "Drone rotors — 1.5 s seamless loop"),
    "heavy_stomp":         (heavy_stomp,         "Heavy unit footfall"),
    "debris_settle":       (debris_settle,       "Rubble settling after a construct dies"),

    # ── combat layer ──
    "fire_pulse":          (fire_pulse,          "Pulse turret — baseline gun, variant A"),
    "fire_pulse_b":        (fire_pulse_b,        "Pulse turret — variant B, 1.5 st down"),
    "fire_pulse_c":        (fire_pulse_c,        "Pulse turret — variant C, 1.5 st up"),
    "fire_arc":            (fire_arc,            "Arc node — ionised snap with a crackle tail"),
    "lance_charge":        (lance_charge,        "Ion lance spooling — trigger ahead of the shot"),
    "fire_lance":          (fire_lance,          "Ion lance discharge — the authority sound"),
    "fire_flak":           (fire_flak,           "Flak launch — hollow tube thump"),
    "flak_burst":          (flak_burst,          "Flak airburst — crack and shrapnel scatter"),
    "fire_mortar":         (fire_mortar,         "Mortar launch — deep thud and air displacement"),
    "mortar_impact":       (mortar_impact,       "Mortar landing — ground burst and debris"),
    "impact_light":        (impact_light,        "Shot on an unarmoured target"),
    "impact_heavy":        (impact_heavy,        "Shot on something with mass"),
    "impact_shielded":     (impact_shielded,     "Shot repelled by screening — the teaching cue"),
    "unit_shatter":        (unit_shatter,        "Construct destroyed — brittle"),
    "unit_shatter_heavy":  (unit_shatter_heavy,  "Heavy construct destroyed — brittle with mass"),
    "clean_sweep":         (clean_sweep,         "Wave cleared with no leaks"),
    "wave_call":           (wave_call,           "Next wave called in early — a switch thrown"),
    "speed_up":            (speed_up,            "Speed control up — mechanical detent"),
    "speed_down":          (speed_down,          "Speed control down — mechanical detent"),
    "surge_ready":         (surge_ready,         "Threshold Surge charged — felt, not loud"),
    "surge_fire":          (surge_fire,          "Threshold Surge discharges — the biggest cue"),
    "overcharge_on":       (overcharge_on,       "Bus pushed past capacity — rising strain"),
    "overcharge_off":      (overcharge_off,      "Overcharge released — hum settles"),
    "shutter_down":        (shutter_down,        "Armoured plate driven over the ring"),
    "shutter_up":          (shutter_up,          "Plate withdrawn — latch, travel, settle"),
    "threshold_idle":      (threshold_idle,      "Engaged ring bed — 2.0 s seamless loop"),
}

# Indexed families. Written as loops so a ladder cannot drift out of order by
# hand-editing one rung, and so the level/pitch progression lives in exactly one
# place — the builder — rather than in eight near-identical dict entries.
for _i in range(8):
    BANK[f"chain_up_{_i + 1}"] = (_bind(_chain, _i),
                                  f"Kill chain rung {_i + 1}/8 — rises, and quietens as it rises")
for _i in range(6):
    BANK[f"ward_engage_{_i + 1}"] = (_bind(_ward, _i),
                                     f"Ward {_i + 1}/6 locking — stone on alloy, counted")
del _i


def read_wav(path: Path) -> np.ndarray:
    """Read a mono 16-bit wav back as floats. Only used by the level report."""
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0


def report_levels(out_dir: Path) -> int:
    """Print what every file in the bank actually measures.

    The loudness policy is only worth anything if it is falsifiable, and the way to
    falsify it is to look at the whole bank sorted by level and ask whether the
    order matches what should be loud. Includes the CC0-sourced cues, which are not
    in BANK — they share the mix, so they belong in the table.
    """
    files = sorted(out_dir.glob("*.wav"))
    if not files:
        print(f"no wavs in {out_dir}", file=sys.stderr)
        return 1
    rows = []
    for p in files:
        d = read_wav(p)
        peak = 20.0 * np.log10(max(float(np.max(np.abs(d))), 1e-9))
        rms = 20.0 * np.log10(max(float(np.sqrt(np.mean(d ** 2))), 1e-9))
        rows.append((p.stem, len(d) / SR, peak, rms, loudness(d), p.stem in BANK))
    print(f"{'name':24s} {'dur':>6s} {'peak':>8s} {'rms':>8s} {'LUFS-ish':>9s}  src")
    for name, dur, peak, rms, lu, synth in sorted(rows, key=lambda r: r[4]):
        print(f"{name:24s} {dur:5.2f}s {peak:7.1f} {rms:7.1f} {lu:8.1f}   "
              f"{'synth' if synth else 'cc0'}")
    print(f"\n{len(rows)} files · {sum(1 for r in rows if r[5])} synthesized · "
          f"{sum(1 for r in rows if not r[5])} sourced")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the Latticefall SFX bank.")
    ap.add_argument("names", nargs="*", help="sounds to render (default: all)")
    ap.add_argument("--list", action="store_true", help="list the bank and exit")
    ap.add_argument("--levels", action="store_true",
                    help="measure every .wav on disk (duration, peak, RMS, LUFS-ish) and exit")
    ap.add_argument("--out", default=str(OUT_DIR), help="output directory")
    args = ap.parse_args()

    if args.list:
        for name, (_, desc) in BANK.items():
            print(f"{name:22s} {desc}")
        return 0

    if args.levels:
        return report_levels(Path(args.out))

    names = args.names or list(BANK)
    unknown = [n for n in names if n not in BANK]
    if unknown:
        print(f"unknown sound(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    for name in names:
        fn, desc = BANK[name]
        sig = fn(rng_for(name))
        p = write_wav(out_dir / f"{name}.wav", sig)
        print(f"{name:22s} {len(sig)/SR:5.2f}s  {p.stat().st_size/1024:6.1f} KB  {desc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
