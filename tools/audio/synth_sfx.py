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
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the Latticefall SFX bank.")
    ap.add_argument("names", nargs="*", help="sounds to render (default: all)")
    ap.add_argument("--list", action="store_true", help="list the bank and exit")
    ap.add_argument("--out", default=str(OUT_DIR), help="output directory")
    args = ap.parse_args()

    if args.list:
        for name, (_, desc) in BANK.items():
            print(f"{name:22s} {desc}")
        return 0

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
