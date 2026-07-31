id: CAM-03
title: --camera x,y,zoom hook, so a screenshot and its a11y report are reproducible
labels: phase-1, tooling, ui
depends: CAM-01
milestone: E2 Camera
---
## Problem

Every verification hook in this project exists because `--fixed-fps` has nobody to press a key:
`--paused`, `--select`, `--pick`, `--cursor`, `--scroll`, `--options`, `--ui-scale`,
`--display-defaults`, `--facings`, `--build`, `--ability-at`, `--press-at` (`main.gd:163-241`).
A camera adds a *continuous* piece of state that every one of those hooks now depends on:
`--shot` frames a different picture at a different pan, and `--a11y` must be paired with the
`--shot` on the same frame because the analyser samples the background out of that PNG
(`CLAUDE.md`). Without a camera hook, `game renders`' coverage 0.95 and `accessibility`'s 182
text items / worst contrast 5.08:1 stop being comparable run to run — and a check that passes
for a changed reason is the failure mode this project keeps re-learning (LF-061, the 36-minute
`game renders` that reported `ok`).

There is a second, smaller blocker in the way of using the hook at all: **`tools/shot.py`'s
`--extra` cannot pass any flag-shaped token** (LF-073). `nargs='*'` makes argparse classify
`--camera` as option-like and abort before Godot launches, so the tool's own documented example
has apparently never been run.

## Tasks

- [ ] Add `--camera <x> <y> <zoom>` to `main.gd`'s `_setup_cli()` match block, next to
      `--cursor` and `--build`, with a comment in the same voice as its neighbours saying what
      it makes reachable that a real input otherwise would.
- [ ] Apply it *after* boot and after `--display-defaults`, and make `--display-defaults` reset
      the camera to the anchor's default framing. `--display-defaults` must keep coming first,
      because it resets `ui_scale` (`docs/STATE.md`).
- [ ] Accept an omitted zoom (`--camera x,y`) and an omitted target (`--camera zoom`)? No —
      take three positional values or reject. One shape, parsed with `is_valid_float()` exactly
      as `--speed` does at `main.gd:227`.
- [ ] Emit a `CAMERA <x> <y> <zoom>` line alongside the existing `SHOT` / `FRAME` / `STATE` /
      `AUDIO` / `FACE` lines on the shot frame, whether or not `--camera` was passed. A report
      that does not say where the camera was is the problem this issue exists to fix.
- [ ] Relay `CAMERA` in `tools/shot.py` with the other lines.
- [ ] Write the camera state into the `--a11y` JSON header so `tools/validate/a11y.py <report>`
      can print it and a stale report is identifiable.
- [ ] {{PRC-09}} owns the LF-073 fix (`tools/shot.py`'s `--extra` → `nargs=argparse.REMAINDER`).
      Take it as a dependency in practice: every verification command below is unrunnable until
      it lands, and do not change that argparse block here.
- [ ] Pin the camera in `tools/check.py`'s rendered checks. `game renders`, `menu renders` and
      `accessibility` must each pass an explicit `--camera` so their numbers stay comparable
      across the camera work; record the pinned values in the check's detail line.
- [ ] Re-baseline `game renders` coverage with the pinned camera and update `docs/STATE.md`'s
      gate block via `.venv/bin/python tools/session.py`.
- [ ] Document the pairing rule in `CLAUDE.md` next to the existing `--a11y`/`--shot`
      same-frame note: **`--a11y` and `--shot` and `--camera` are one triple.**

## Acceptance criteria

- Two runs of the same command with the same `--camera` produce byte-identical PNGs
  (SHA-256 match).
- Two runs differing only in `--camera` produce different PNGs, and each PNG's paired
  `--a11y` report carries the camera it was taken at.
- `--display-defaults` with no `--camera` reproduces the pre-CAM-01 framing for all 24 anchors.
- `tools/shot.py anchor-24 --out /tmp/s.png --extra --paused` launches Godot instead of
  aborting in argparse.
- `check.py`'s three rendered checks report the camera they pinned.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-24 --out /tmp/a.png --extra --camera 9 7 0.5
.venv/bin/python tools/shot.py anchor-24 --out /tmp/b.png --extra --camera 9 7 0.5
sha256sum /tmp/a.png /tmp/b.png                       # identical
.venv/bin/python tools/shot.py anchor-24 --out /tmp/c.png --extra --camera 2 2 1.0
sha256sum /tmp/c.png                                  # different from a/b
.venv/bin/python tools/validate/a11y.py /tmp/a.json --shot /tmp/a.png --all
.venv/bin/python tools/check.py --no-window && .venv/bin/python tools/reap.py
```

Proof is the identical/different SHA-256 pair plus the `CAMERA` line appearing in `shot.py`'s
relayed output.

## Risks / gotchas

- **`--a11y` must be paired with the `--shot` on the same frame** — the analyser samples the
  background out of that PNG, so a report taken a frame later describes a screen that was never
  measured (`CLAUDE.md`).
- **`--extra` cannot pass flags today** (LF-073, fixed by {{PRC-09}}). Every verification command
  in this issue is unrunnable until that lands; sequence it first, not last.
- **`argparse.REMAINDER` swallows everything after it**, including a later `--out`. Put `--extra`
  last in the documented example and say so in the epilog.
- A pinned camera makes the gate blind to a regression in *default* framing. Keep one unpinned
  rendered case, or assert the default separately.
- The scenario harness in {{PRC-12}} will want to set the camera too — expose the setter as a
  method, not only as CLI parsing.

## Files likely touched

- `scripts/main.gd` (`_setup_cli()`, the shot/report emit path)
- `tools/shot.py` (`--extra`, `CAMERA` relay)
- `tools/check.py` (three rendered checks)
- `tools/validate/a11y.py` (report header)
- `CLAUDE.md`, `docs/STATE.md`
