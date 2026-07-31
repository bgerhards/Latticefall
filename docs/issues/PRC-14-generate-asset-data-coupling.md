id: PRC-14
title: Generate the asset↔data coupling instead of hand-keeping four lists
labels: phase-1, tooling, art, content
depends: PRC-13
blocks: ART-01
milestone: E1 Process
---
## Problem

The link between "a thing in the data" and "a picture of it" is maintained by hand in **four
independent places**, and `tools/check.py`'s own docstring says so:
*"The manifest is hand-kept in step with `render.py`'s ASSETS dict and with `enemies.json`, by
three separate people-shaped processes"* (`tools/check.py:250-251`). The four are:

1. `tools/blender/render.py:701` — the `ASSETS` dict of builder functions.
2. `data/towers.json` / `data/enemies.json` — the ids the rules use.
3. `scripts/anchor_view.gd` — which does not *look up* a sprite, it **derives** one:
   `id.replace("-", "_")`. A miss returns null, falls through to `_draw_unit`, and the game
   draws a coloured circle with no warning (`tools/check.py:241-249`).
4. `assets/renders/sprites.json` — the generated manifest.

`sprite coverage` catches one failure direction (a data id with no sprite). Nothing catches
the others: a sprite with no data id renders, packs and ships as dead atlas cells; a rename in
`towers.json` silently changes which file the game asks for; and `anchor_view.gd`'s derivation
means the naming convention itself is load-bearing but written down nowhere executable.

The cost is about to multiply. {{ART-01}} splits heads from bases and takes the library to
~680 renders across three yaw counts (16 / 4 / 8), so the "which asset, at which yaws, for
which id" mapping stops being memorisable — and LF-108 already records that the **yaw count is
independently encoded in three places** (`render.py`'s `YAWS`, `iso.gd`'s
`90 * roundi(deg/90)` bucketing, and `iso.gd`'s `45.0 + hysteresis` test).

## Tasks

- [ ] Define one source of truth: an `assets` block in `data/towers.json` / `data/enemies.json`
      (per id: sprite base name, yaw count, whether it has a separate head), **or** a single
      `data/assets.json` keyed by id. Pick one, state the rejected alternative in the
      docstring, and add a schema ({{PRC-10}}).
- [ ] Write `tools/blender/gen_assets.py` (or fold it into {{PRC-13}}'s `build.py`) which reads
      that source and emits: the render work list, the expected manifest keys, and the
      sprite-name derivation.
- [ ] Replace `render.py`'s hand-written `ASSETS` dict with a lookup from builder-function name
      to the data-declared id, so adding a tower is a data edit plus a builder function and
      nothing else. Keep the builder functions where they are — they are real Blender code, not
      configuration.
- [ ] Replace `anchor_view.gd`'s inline `id.replace("-", "_")` with a single function in
      `scripts/sprites.gd` (`Sprites.name_for(id)`), so the convention exists once and is
      greppable. Then verify no other file re-derives it.
- [ ] Centralise the yaw count: one constant, read by `render.py` and exported into the
      manifest, read back by `iso.gd`. Address LF-108's three-places problem while the file is
      open — but do **not** raise the count here; that is {{ART-01}}, and
      `YAW_HYSTERESIS_DEG = 12.0` is larger than half a 16-yaw bucket (11.25°) and would lock
      every facing permanently.
- [ ] Extend `tools/check.py`'s `sprite coverage` to check **both** directions: every data id
      has a sprite (today), and every manifest sprite is claimed by a data id or an explicit
      allowlist of props (the ring, bindstone, tiles and board furniture are legitimately
      id-less).
- [ ] Assert the per-id yaw count matches what the manifest actually contains — a tower
      declared at 16 yaws with 4 rendered is a silent visual regression, not an error.
- [ ] Regenerate the manifest and confirm it is byte-identical to the committed one before any
      behaviour change, so the refactor is provably a no-op first.
- [ ] Update `.claude/skills/new-asset/SKILL.md`: the "add a new asset" procedure changes from
      "edit four things" to "edit the data and write a builder".
- [ ] Note in `CLAUDE.md` that the derivation lives in `Sprites.name_for()` and nowhere else.

## Acceptance criteria

- Adding a new tower id to `data/towers.json` with no builder produces a **named error** from
  `gen_assets.py` and a red `sprite coverage`, not a coloured circle in game.
- Adding a builder with no data id produces a named error, not dead atlas cells.
- `grep -rn 'replace("-", "_")' scripts/` returns exactly one hit, in `scripts/sprites.gd`.
- The yaw count appears as a literal in exactly one place; `render.py` and `iso.gd` both read
  it.
- Regenerating `assets/renders/sprites.json` on an unchanged tree produces a byte-identical
  file (`git status` clean).
- `sprite coverage` reports both directions in its detail line
  (`N ids drawn from M sprites, 0 orphaned`).
- Renaming `pulse-turret` to `pulse-turret-mk2` in `towers.json` fails the gate with a message
  naming the missing sprite, rather than shipping a circle.

## Verification

```bash
.venv/bin/python tools/blender/gen_assets.py --check      # no-op verification, exit 0
git status --porcelain assets/renders/sprites.json        # expect empty
grep -rn 'replace("-", "_")' scripts/ | wc -l             # expect 1
# negative case
python - <<'EOF'
import json,pathlib
p=pathlib.Path('data/towers.json'); d=json.loads(p.read_text())
d['towers'][0]['id']='pulse-turret-mk2'; p.write_text(json.dumps(d,indent=2))
EOF
.venv/bin/python tools/check.py --tier 2 2>&1 | grep 'sprite coverage'   # expect FAIL
git checkout -- data/towers.json
```

## Verification note

`sprite coverage` is a data-only check and does not launch Godot, but the *symptom* it guards
against is visual. Confirm at least once with `tools/shot.py anchor-24 --out /tmp/s.png` that
the board still draws sprites and not placeholder polygons — LF-046 records that the
placeholder path collapses two factions to the same amber and scales its radius by Warden
Heavy's HP, so a regression there is quiet and ugly.

## Risks / gotchas

- **`sprite atlas` hashes every render** (`tools/check.py:630-664`) and fails if the packed
  page no longer matches. Any change to the render work list must be followed by
  {{PRC-13}}'s `build.py`, or the gate goes red for a reason that has nothing to do with this
  refactor.
- **The pack is a fixed 256 px grid and never trims** — do not use this refactor to introduce
  per-asset cell sizes. One measured pivot serves every sprite only because every cell is
  identical (LF-027).
- `scripts/sprites.gd` builds its own library instance when the `Sprites` autoload is absent
  (the editor case, LF-025). A new function must exist on both paths.
- Changing an id changes what `anchor_view.gd` asks for **and** what `sim/content.py` loads;
  ids are referenced from `data/anchors/*.json` waves and from `sim/engine.py`'s
  `standard_policies()` (`sim/engine.py:441-489`). A rename is not a one-file change.
- **A new `class_name` is invisible until the editor imports** (`CLAUDE.md`) — if
  `sprites.gd` gains one, import **in place** (LF-075).
- Do not touch `YAW_HYSTERESIS_DEG` here. Centralising the yaw count is in scope; changing it
  is {{ART-01}} and needs the empirical re-measurement decision 049 describes.

## Files likely touched

- `data/towers.json`, `data/enemies.json` (or `data/assets.json` new), `data/schema/*`
- `tools/blender/gen_assets.py` (new), `tools/blender/render.py`
- `scripts/sprites.gd`, `scripts/anchor_view.gd`, `scripts/iso.gd`
- `tools/check.py` (`check_sprite_coverage`)
- `.claude/skills/new-asset/SKILL.md`, `CLAUDE.md`
