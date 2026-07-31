id: PRC-13
title: Incremental asset build — one command, content-hashed, skip unchanged
labels: phase-1, tooling, art, perf
blocks: ART-01
milestone: E1 Process
---
## Problem

The art pipeline is four commands that must be run in one order —
render → `mask_glow` → `pack_atlas` → `--import` — and `CLAUDE.md` says **ALWAYS** three times
because skipping any of them makes a correct art fix look like it did nothing. It is
documented as a hazard rather than automated as a build.

It is also not incremental in the one place that matters. `tools/blender/mask_glow.py:37`
saves **every** glow PNG in the manifest on every run, unconditionally. PIL's `save()` does not
reproduce identical bytes for identical pixel content, so re-running it after a one-asset
re-render touches every other sprite's LFS blob with a pixel-identical, byte-different file —
confirmed by git-lfs smudge plus numpy compare on `hollow_column_y045_glow.png`: **max abs
pixel diff 0, sha256 differs from HEAD** (LF-071). A single asset re-render becomes a
full-repo LFS diff, and `git status` stops being readable.

This gets much worse immediately. {{ART-01}} takes the library from 208 renders to about
**680** (heads at 16 yaws, bases at 4, units at 8 — PRD §3 E6). A pipeline that rewrites every
blob on every run is not viable at that size, and the atlas grows with it:
`tools/blender/pack_atlas.py:52` justifies `COLS = 12` for **width** only
(*"12 × 256 = 3072 px, comfortably inside any GL texture limit"*) — page **height** is
`rows × CELL` and is uncapped. At 680 cells that is 57 rows = 14,592 px, past the
`GL_MAX_TEXTURE_SIZE` of a great many GL Compatibility targets, with no assertion anywhere.

## Tasks

- [ ] Write `tools/blender/build.py` — one entry point running the whole pipeline in order,
      with `--only <asset>`, `--force`, and `--dry-run`. Module docstring explains *why* the
      order is load-bearing, quoting the two ways an art fix silently does nothing.
- [ ] Define the per-asset content hash: the asset's builder source (its function in
      `tools/blender/render.py`), the shared material/palette code, `CELL`, `YAWS`,
      `HEIGHT_BIAS`, `ORTHO_SCALE` and the Blender version string. Store it in
      `assets/renders/sprites.json` beside each sprite entry.
- [ ] Hash the *builder*, not the whole `render.py` — otherwise every asset re-renders whenever
      any asset changes, which is the current behaviour with extra steps. Extract each asset's
      source with `inspect.getsource()` over the `ASSETS` dict (`tools/blender/render.py:701`).
- [ ] Skip re-rendering an asset whose hash matches, and report what was skipped and why.
- [ ] **Fix LF-071 in `mask_glow.py`**: compute the masked output, compare it byte-for-byte (or
      by array equality) against what is already on disk, and skip the `save()` when identical.
      Keep the function idempotent, which is the property `CLAUDE.md` promises.
- [ ] Verify the fix the way the bug was found: git-lfs smudge plus a numpy compare, and
      `git status` clean after a `--only pulse_turret` round trip.
- [ ] **Add the `GL_MAX_TEXTURE_SIZE` assertion to `pack_atlas.py`.** Compute page height
      before allocating, assert both dimensions against a documented floor (4096 is the
      conservative GLES3/GL Compatibility guarantee; 8192 is safe on any desktop target the
      project cares about — pick one, state it, and cite where the number came from). Fail with
      a message naming the required page count, not a Pillow `MemoryError`.
- [ ] Make `pack_atlas.py` add pages when a pass overflows, rather than growing one page
      taller. `atlas["pages"]` is already a dict keyed by pass name
      (`tools/blender/pack_atlas.py:136-144`); it needs a `(pass, page_index)` key and
      `scripts/sprites.gd` needs to resolve it.
- [ ] Read `CELL` from the manifest in `pack_atlas.py` instead of the module constant
      (`pack_atlas.py:51` hardcodes 256 while `render.py:39` also defines it) — LF-102 records
      that two constants have to move in lockstep and nothing enforces it.
- [ ] Chain `--import` **in place** at the end of `build.py`, via `toolpaths.godot_argv()`.
      Never move `.godot` aside (LF-075) and print a line saying an import is about to happen,
      because the owner may be playing.
- [ ] Refuse to run the import step if a Godot process is live, or print a prominent warning
      naming LF-075 — {{PRC-07}}'s lease data makes that detectable.
- [ ] Report a summary: assets considered, re-rendered, glow files rewritten, atlas pages
      packed, whether the import ran.
- [ ] Update `CLAUDE.md`'s Commands block to lead with `tools/blender/build.py` and keep the
      four individual commands documented as what it runs.
- [ ] Close LF-071; reference LF-102 as remaining open (it is `calibrate()`'s convergence, not
      this).

## Acceptance criteria

- `tools/blender/build.py --only pulse_turret` re-renders exactly one asset and leaves
  `git status` showing **only** that asset's four albedo PNGs, four glow PNGs, the two atlas
  pages, and `sprites.json`.
- Running `build.py` twice with no source change re-renders nothing, rewrites no PNG, and
  leaves `git status` clean.
- `tools/blender/mask_glow.py` run twice in a row produces zero modified files on the second
  run (the LF-071 test).
- `pack_atlas.py` fails with a clear message when the computed page exceeds the documented
  texture-size floor, verified by temporarily lowering the limit constant.
- `tools/check.py`'s `sprite atlas` check is green after a `build.py` run, and red if
  `build.py` is interrupted between render and pack.
- The board draws correctly after `build.py` — proven by a screenshot, not by the exit code.

## Verification

```bash
.venv/bin/python tools/blender/build.py --only pulse_turret
git status --porcelain assets/ | wc -l          # expect ~11, not ~200
.venv/bin/python tools/blender/build.py         # expect "0 re-rendered, 0 rewritten"
git status --porcelain assets/ | wc -l          # expect 0
.venv/bin/python tools/blender/mask_glow.py && git status --porcelain assets/ | wc -l   # expect 0
.venv/bin/python tools/check.py --tier 2 2>&1 | grep 'sprite atlas'
.venv/bin/python tools/shot.py anchor-06 --out /tmp/board.png
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **An art fix can look like it did nothing in two ways** — a skipped `--import` serves the
  cached `.ctex`, a stale atlas serves the old pixels (`CLAUDE.md`). The whole value of this
  tool is removing both; do not add a `--skip-import` convenience flag.
- **The pack is a fixed 256 px grid and never trims.** One measured pivot serves every sprite
  only because every cell is identical; trimming would reintroduce LF-027 (every sprite drawn
  above its own tile). Multi-page support must not become an excuse to trim.
- **Glow renders opaque and must be masked** — an unmasked glow drawn additively fills the
  board with bright rectangles. `mask_glow` cannot be skipped as an optimisation, only its
  redundant *writes*.
- **Blender here is a Windows `.exe` run through WSL interop.** `toolpaths.blender_host_path()`
  translates paths, and `render.py` once wrote manifest paths with `os.path.relpath` under
  Windows Python, producing backslashes Linux `pathlib` reads as one opaque filename — every
  downstream step then silently processed nothing (`docs/STATE.md`). Any new path written into
  the manifest goes through the same translation and must be checked from Linux.
- **`calibrate()` cannot converge at 384 or 1024 px cells** (LF-102) — it corrects
  `ORTHO_SCALE` from the width ratio only. Reading `CELL` from the manifest does not fix that;
  do not let this issue drift into raising the render base.
- `assets/renders/*.png` are LFS blobs. A build that rewrites them all costs quota as well as
  readability (the 1 GB limit is why music masters are out of git — decision 012).
- Reap after every Blender run: `tools/reap.py` classifies `blender -b` with this repo path.

## Files likely touched

- `tools/blender/build.py` (new)
- `tools/blender/mask_glow.py`, `tools/blender/pack_atlas.py`, `tools/blender/render.py`
- `assets/renders/sprites.json` (generated)
- `scripts/sprites.gd` (multi-page atlas resolution)
- `CLAUDE.md`, `backlog.json`, `docs/BACKLOG.md`
