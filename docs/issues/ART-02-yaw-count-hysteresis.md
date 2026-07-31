id: ART-02
title: LF-108 — one source of truth for the yaw count, and a hysteresis band re-measured for it
labels: engine, art, phase-3
blocks: ART-01
milestone: E6 Fidelity
---
## Problem

`scripts/iso.gd:55` sets `YAW_HYSTERESIS_DEG = 12.0`. Half a bucket at 16 yaws is **11.25°**,
so at sixteen yaws the hysteresis band is wider than the bucket and `yaw_for_heading()`'s
early return at `:73-76` (`if off <= 45.0 + hysteresis_deg: return previous`) never lets a
facing re-bucket — **every emplacement's facing locks permanently**. LF-108, PRD risk 8. The
symptom is not an error; it is a turret that visibly stops tracking, which reads as the new
feature not working at all.

Worse, the yaw count is encoded independently in three places: `tools/blender/render.py:38`
(`YAWS`), `scripts/iso.gd:78` (`wrapi(YAW_FOR_PLUS_X + 90 * roundi(deg / 90.0), 0, 360)`, with
`YAW_FOR_PLUS_X` at `:50`), and `scripts/iso.gd:75` (the literal `45.0` — half a 90° bucket).
Changing one and not the others produces a library that renders yaws nothing ever asks for, or
a game that asks for yaws nothing rendered.

Decision 049 measured the band empirically at four yaws — 116 facing changes with 40 reversals
became 59 changes with 3 reversals — so the fix is not a new constant chosen by argument. It is
the same measurement redone at the new bucket width.

## Tasks

- [ ] `scripts/iso.gd`: introduce `YAW_COUNT` as the single constant, with `BUCKET_DEG =
      360.0 / YAW_COUNT` derived from it. Rewrite `yaw_for_heading()`'s bucketing
      (`:77-78`) and the hysteresis test (`:75`) in terms of `BUCKET_DEG * 0.5`, not literals.
- [ ] `scripts/iso.gd`: express the hysteresis as a **fraction of a bucket**
      (`YAW_HYSTERESIS_FRAC`), not as degrees. A fraction survives a yaw-count change; a
      degree value is the bug in this issue.
- [ ] `tools/blender/render.py:38`: derive `YAWS` from the same count, and have the manifest
      record the count so the engine can assert against it at load rather than trusting that
      two files were edited together.
- [ ] `scripts/sprites.gd`: fail loudly at `load_library()` if the manifest's yaw count differs
      from `Iso.YAW_COUNT`. A mismatch today is a silent texture miss.
- [ ] Build the measurement harness decision 049 used, as a committed tool rather than a
      throwaway: replay a real anchor, count facing **changes** and **reversals** (a change
      back to the immediately previous bucket within N frames) per emplacement, and print the
      totals. Reproduce decision 049's 4-yaw numbers first to prove the harness measures the
      same thing.
- [ ] Sweep `YAW_HYSTERESIS_FRAC` across a range at `YAW_COUNT = 4`, 8 and 16 and choose the
      value that minimises reversals without visibly lagging the turret. Paste the table.
- [ ] Guard the constant: add a check that `YAW_HYSTERESIS_FRAC < 0.5`, because at ≥ 0.5 the
      band exceeds half a bucket and the freeze returns. Make it a gate check in
      `tools/check.py`, not a comment — CLAUDE.md's own record is that a rule written in prose
      does not hold (decision 051).
- [ ] Verify `Iso.heading_for_yaw()` (`:58-61`) still produces the right handedness at 16
      buckets. It uses `cos`/`sin`, which are **banned in the rules** but fine here — facing is
      presentation-only (decision 049) — so add a comment saying exactly that, because the next
      reader will see `sin` and reach for the ban list.
- [ ] Screenshot before and after at `YAW_COUNT = 4` to prove nothing regressed while the
      library is still four yaws.
- [ ] Close LF-108 in `docs/BACKLOG.md` with the measured band.

## Acceptance criteria

- `YAW_COUNT` exists in exactly one place per language and `render.py` derives its yaw list
  from the same number; changing it changes all three former sites.
- The harness reproduces decision 049's four-yaw figures (116/40 unhysteresed, 59/3 at the
  chosen band) within measurement noise, proving it measures the same quantity.
- At `YAW_COUNT = 16`, the chosen band produces **zero permanently frozen facings** over a full
  anchor and a reversal count comparable to the four-yaw result.
- `tools/check.py` fails if `YAW_HYSTERESIS_FRAC >= 0.5`.
- `sprites.gd` refuses to load a library whose yaw count disagrees with `Iso.YAW_COUNT`, with a
  message naming both numbers.
- With `YAW_COUNT` still 4, the game screenshots identically to before.

## Verification

```bash
.venv/bin/python tools/yaw_band.py --anchor anchor-12 --yaws 4  --frac 0,0.05,0.1,0.15,0.2,0.25
.venv/bin/python tools/yaw_band.py --anchor anchor-12 --yaws 16 --frac 0,0.05,0.1,0.15,0.2,0.25
.venv/bin/python tools/shot.py anchor-12 --out /tmp/yaw4.png --extra --facings
.venv/bin/python tools/check.py --no-window
```

Proof to paste: both band tables with changes/reversals per row, the chosen fraction, the
`--facings` block, and the gate's line for the new check.

## Risks / gotchas

- **The failure mode is silence.** A frozen facing throws nothing, logs nothing and looks like
  a sprite that simply does not track. Only the harness or `--facings` finds it.
- `wrapi(YAW_FOR_PLUS_X + 90 * roundi(deg / 90.0), 0, 360)` at `:78` produces integer degrees;
  16 buckets are 22.5° apart. This issue should hand back a **bucket index**, with degrees
  derived only where Blender needs them — {{ART-01}} depends on that.
- `heading_for_yaw()` uses `angle_to` on a `Vector2` (`:74`). `Vector2` is float32 and banned in
  the *rules*; this is presentation, so it is allowed — but do not let the pattern migrate into
  `anchor_sim.gd` (decision 030).
- Facing state lives on the `placed` record as `view_yaw` (`anchor_view.gd:983-994`) and that is
  only safe because placed records are compared by `slot`. Do not move it onto a unit (LF-055).
- Units call `yaw_for_heading()` with **no** hysteresis today (`anchor_view.gd:1019`). Decide
  whether they should get it at 8 buckets and say so; a unit changing facing every frame is a
  different artefact from a turret doing it.

## Files likely touched

- `scripts/iso.gd`, `scripts/sprites.gd`, `scripts/anchor_view.gd`
- `tools/blender/render.py`, `assets/renders/sprites.json`
- `tools/yaw_band.py` (new), `tools/check.py`
- `docs/BACKLOG.md`, `docs/DECISIONS.md`
