id: PRC-12
title: Scenario and assertion harness — one --scenario file replacing fourteen CLI flags
labels: phase-1, tooling, engine
depends: PRC-09
blocks: BAL-05
milestone: E1 Process
---
## Problem

Verification in this project is a growing pile of one-shot CLI flags. `scripts/main.gd`'s
`_setup_cli()` (line 163) now parses **fourteen**: `--shot`, `--a11y`, `--facings`,
`--anchor`, `--autoplay`, `--paused`, `--select`, `--pick`, `--scroll`, `--cursor`,
`--build`, `--difficulty`, `--speed`, `--ability`, `--ability-at`, `--press-at`, `--chain`.
Each exists because `--fixed-fps` has nobody to press a key (`CLAUDE.md`), each was added when
a screen turned out to be unreachable, and `--ability-at` / `--press-at` exist only because
`--ability` fired at boot before anything had spawned, so a surge could only ever be aimed at
an empty lane (`docs/STATE.md`). {{CAM-01}} needs a fifteenth (`--camera x,y,zoom`) or every
`--shot`/`--a11y` report becomes camera-state dependent (LF-052).

Worse, **none of them assert anything**. They reach a state; a human then looks at a PNG.
`main.gd`'s parser is a positional `match` with no validation, so a mistyped flag or a missing
value is silently ignored and you get a screenshot of the wrong thing. That is how "verified"
and "looked plausible" became the same word.

And the parsing is duplicated **four times**, hand-rolled, in four dialects:
`scripts/main.gd:163` (a `match`), `scripts/menu.gd:67` (an `if/elif` chain),
`scripts/draft.gd:86` (a `match`), `scripts/display_settings.gd:124` (an `if/elif` chain).

## Tasks

- [ ] Design the scenario file and write `data/schema/scenario.schema.json` for it — it is
      content, so it validates like content ({{PRC-10}}). Shape:
      `{schema, anchor, difficulty, ui_scale, seed?, timeline: [...]}` where each entry is
      either `{frame, action, args}` or `{frame, assert, expr, expect, tolerance?}`.
- [ ] Define the action verbs from the existing flags — `build`, `select`, `pick`, `press`,
      `ability`, `speed`, `scroll`, `cursor`, `pause`, `camera`, `shot`, `a11y`, `facings` —
      so every current flag has an exact scenario equivalent, and record the mapping table in
      the schema description.
- [ ] Define the assertion expression language deliberately and **narrowly**: dotted paths into
      a small exported state dictionary (`sim.lives`, `sim.bus_load`, `sim.units_alive`,
      `view.camera.zoom`, `hud.selected`), plus `==`, `!=`, `<`, `<=`, `>`, `>=` and an
      optional numeric tolerance. No arbitrary GDScript `Expression` evaluation — a scenario
      file is content, and content that can execute is a different kind of object.
- [ ] Write `scripts/scenario.gd`: load the JSON, sort the timeline by `(frame, index)`, and
      drive it from `_process()`. Sorting must be stable and total — the same argument as
      `sim/engine.py:389`'s `queue.sort(key=lambda q: (q[0], q[1]))`.
- [ ] Add `--scenario <path>` to `main.gd` and have it **exit non-zero on the first failed
      assertion**, printing `ASSERT FAIL frame=<n> expr=<...> got=<...> want=<...>`.
- [ ] Emit a machine-readable summary line (`SCENARIO {json}`) with every assertion, its
      frame, and its result, for `tools/shot.py` to relay and CI to consume.
- [ ] Replace the four hand-rolled argv parsers with one shared `scripts/cli_args.gd`:
      a single tokeniser over `OS.get_cmdline_user_args()` returning a Dictionary, used by
      `main.gd`, `menu.gd`, `draft.gd` and `display_settings.gd`. Make an unknown flag a
      **printed warning**, not silence.
- [ ] Keep every existing flag working as a thin shim over the scenario runner, so no existing
      command in `CLAUDE.md`, `tools/check.py` or `tools/shot.py` breaks. Deprecate in the help
      text, do not delete.
- [ ] Add `tools/scenario.py`: run a scenario file through Godot (via
      `toolpaths.godot_argv()`), relay `SCENARIO`/`ASSERT`/`SHOT`/`FRAME` lines, and exit
      non-zero on failure. Same shape as `tools/shot.py`, and it should reuse `--extra`'s
      REMAINDER fix from {{PRC-09}}.
- [ ] **Make the same file drive `sim/engine.py`.** `Sim.run` already merges a pre-sorted
      `(time, item)` queue (`sim/engine.py:385-390`); a scenario's timeline is the same shape.
      Add a loader that converts `frame` to sim time (`frame / 60` against `DT = 1/30`) and
      dispatches the actions the reference sim can express. Actions it cannot express must
      raise, never be silently dropped — that is the {{BAL-01}} boundary and it must be
      explicit.
- [ ] Add frame-time instrumentation to the scenario runner — per-frame `_process` and
      `_physics_process` duration, min/mean/p95/max — emitted in the `SCENARIO` summary. There
      is no frame-time output from a `--fixed-fps` run today, and {{BAL-05}}'s performance
      budget cannot exist without it.
- [ ] Write three scenario files under `data/scenarios/` to prove the harness: `smoke.json`
      (anchor-01, build two turrets, assert `lives` unchanged at frame 1800),
      `abilities.json` (reproduce the measured surge falloff — `130 * lerp(0.35, 1, frac)`,
      pushback exactly 1.5 tiles — as assertions rather than as prose in `docs/STATE.md`), and
      `a11y-worst.json` (anchor-24 at 200% with `--scroll`).
- [ ] Convert `tools/check.py`'s `accessibility` cases (`tools/check.py:562-577`) to scenario
      files, so the five hardest-to-reach screens are described in data rather than in a Python
      list of argv fragments.
- [ ] Add a gate check `scenarios pass` (tier 2) running `data/scenarios/smoke.json`.
- [ ] Update `CLAUDE.md`'s "Verification hooks" paragraph — it currently lists the flags; it
      should lead with the scenario file and keep the flags as the shims they become.
- [ ] Add a `docs/DECISIONS.md` entry: verification is a data file with assertions, not a flag
      per screen. Rejected alternative: keep adding flags (the status quo, at fourteen and
      counting, with {{CAM-01}} about to make fifteen).

## Acceptance criteria

- `tools/scenario.py data/scenarios/smoke.json` exits 0 and prints a `SCENARIO` summary with
  every assertion listed and passing.
- Editing `smoke.json`'s expected value to something false makes it exit non-zero, printing
  `ASSERT FAIL` with the frame, the expression, the value seen and the value wanted.
- A mistyped action verb is a **load-time schema error**, not a silent no-op.
- A mistyped flag on `main.gd`'s legacy CLI prints a warning naming the flag.
- All fourteen legacy flags still behave identically: `tools/check.py --tier 3` is green
  without any change to its argv lists (before the accessibility conversion) and after.
- The `SCENARIO` summary carries frame-time statistics for the captured run.
- `data/scenarios/abilities.json` reproduces the measured surge numbers from `docs/STATE.md`
  as passing assertions.
- `sim/engine.py` can load `smoke.json` and either execute it or raise naming the unsupported
  verb.

## Verification

```bash
.venv/bin/python tools/scenario.py data/scenarios/smoke.json ; echo "exit=$?"
.venv/bin/python tools/scenario.py data/scenarios/abilities.json
# negative case
python - <<'EOF'
import json,pathlib
p=pathlib.Path('data/scenarios/smoke.json'); d=json.loads(p.read_text())
next(e for e in d['timeline'] if 'assert' in e)['expect']=-999
p.write_text(json.dumps(d,indent=2))
EOF
.venv/bin/python tools/scenario.py data/scenarios/smoke.json ; echo "expect non-zero: $?"
git checkout -- data/scenarios/smoke.json
.venv/bin/python tools/check.py --tier 2 2>&1 | grep 'scenarios pass'
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **A GDScript parse error is a hang, not an error** (`docs/STATE.md`, LF-063). A new
  `scripts/scenario.gd` reached through an untyped reference will take the whole playfield down
  and blame another file. Annotate every local; run {{PRC-01}}'s parse check before running the
  game.
- **A new `class_name` is invisible until the editor imports, and the symptom is a hang**
  (`CLAUDE.md`). If `scenario.gd` declares one, run `--headless --path . --import` **in
  place** (LF-075) and confirm the name lands in `.godot/global_script_class_cache.cfg`.
- **`--a11y` must be paired with the `--shot` on the same frame** (`CLAUDE.md`) — the analyser
  samples the background out of that PNG. The scenario runner must enforce that pairing rather
  than leaving it to the file's author.
- Frame numbers are not sim time: the game runs at `--fixed-fps 60` while the rules tick at
  `DT = 1/30` (`sim/engine.py:31`), and the speed control multiplies to 3×. Define the
  conversion once, in the schema description, or every scenario will be off by a factor
  somebody has to rediscover.
- Do not let the assertion language grow into an interpreter. `Expression` in GDScript will
  evaluate arbitrary code from a data file that `validate_data.py` treats as content.
- Input must go through the action map, never a raw keycode (`CLAUDE.md`, decision 042) — the
  `press` verb takes an `lf_*` action name and must reject anything else.
- Keep the legacy flags. `tools/check.py`, `tools/shot.py`, both session skills and
  `CLAUDE.md` all name them; a flag day here breaks verification everywhere at once.

## Files likely touched

- `scripts/scenario.gd`, `scripts/cli_args.gd` (new)
- `scripts/main.gd`, `scripts/menu.gd`, `scripts/draft.gd`, `scripts/display_settings.gd`
- `data/schema/scenario.schema.json`, `data/scenarios/*.json` (new)
- `tools/scenario.py` (new), `tools/shot.py`, `tools/check.py`
- `sim/engine.py` (scenario loader only — no rule change)
- `CLAUDE.md`, `docs/DECISIONS.md`
