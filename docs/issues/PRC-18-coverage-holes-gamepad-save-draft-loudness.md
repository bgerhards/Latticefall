id: PRC-18
title: Coverage holes — gamepad input, save/load, the recovery draft, and audio loudness have zero automated verification
labels: phase-1, tooling, process
depends: PRC-12
milestone: E1 Process
---
## Problem

Testing-audit finding #3. Grepped every tracked `scripts/*.gd` filename against every
`tools/*.py`, `scripts/test/*.gd`, and `data/scenarios/*.json`, as a blunt but mechanical proxy
for "does anything in the verification surface ever touch this file's behaviour". Sixteen
scripts come back with **zero** references, several of them backing features `CLAUDE.md` or
the PRD name explicitly:

- `recoveries.gd`, `draft.gd` — the between-anchor recovery draft. Already known fragile
  (`LF-065`, `LF-070`: no call site wired the accessors to rules for several sessions and this
  was only ever discovered by a human reading code) — and still, today, zero automated
  coverage touches either file.
- `progress.gd` — save/load. `progress.json` is read at boot and referenced by
  `display_settings.gd`'s own defaults-reset path, but no tool and no scenario ever exercises a
  save, a quit, and a reload.
- `pause_menu.gd`, `audio_director.gd` (brownout music ducking — the specific behaviour
  `CLAUDE.md`'s glow-dimming analogy is modelled on), `ui_theme.gd` (the `Ui` autoload
  decisions 045/046 say is the *sole* source of accessible sizes and colours), `abilities.gd`,
  `dialog_view.gd`, `cli_args.gd`.

**Gamepad input has no test at all, in any form.** Grepped for Godot's own gamepad identifiers
(`Joypad`, `JoyButton`, `JoyMotion`) across every `.gd` file and every `tools/*.py` file: zero
hits, anywhere. `CLAUDE.md`'s invariant table and `docs/PRD-THEATRE-SCALE.md` §4 both state
full "keyboard, mouse and gamepad control" as a non-negotiable, shipped property. If gamepad
support regressed tomorrow, nothing in this project would turn red.

**Audio loudness has no test.** `CLAUDE.md`'s fifth non-negotiable: "Loudness-match audio,
never peak-normalize... peak normalization is why programmer audio sounds flat." The only
audio gate check, `check_sfx_reproducible`, verifies exactly one thing — that re-synthesizing
`ui_confirm.wav` reproduces the committed bytes exactly. That is a determinism check, not a
loudness check: a synthesis change that is perfectly deterministic but drifts the whole bank's
perceived loudness — the specific failure the non-negotiable names — would pass every check in
the project, including this one, because it never measures loudness at all.

**Verification assets exist and are not wired in.** `data/scenarios/abilities.json` and
`data/scenarios/a11y-worst.json` were built by PRC-12 specifically to turn the measured ability
falloff numbers and the worst-case 200%-scale accessibility screen into passing assertions
(`docs/DECISIONS.md` decision 064 describes both by name). `tools/check.py`'s
`check_scenarios_pass` only ever runs `data/scenarios/smoke.json` — confirmed by reading the
function body, which hardcodes that one path. `abilities.json` is, by its own note field, the
scenario that actually pins something falsifiable ("dealt = 130 * lerp(0.35, 1.0,
dist/path_length) = 47.1782638888889 ... checked below to four decimal places"); `smoke.json`
is explicitly "not a claim about anchor-01's balance" by its own note. The stronger test is the
one nobody runs automatically. A fourth scenario, `data/scenarios/lf161_edge_scroll_contained.json`,
exists on disk and is referenced nowhere in `tools/`, `scripts/test/`, or any gate check either.

## Tasks

- [ ] Extend `check_scenarios_pass` (or split it into one check per file) to run every scenario
      under `data/scenarios/`, not only `smoke.json` — including `abilities.json`,
      `a11y-worst.json`, and `lf161_edge_scroll_contained.json`. Place at whichever tier the
      measured wall-clock earns (tier 3 already, next to the other frame-rendering checks, is
      the natural home unless the combined cost blows tier 3's implicit budget).
- [ ] Investigate whether a synthetic gamepad `InputEvent` (`InputEventJoypadButton`/
      `InputEventJoypadMotion`) can be injected through `Input.parse_input_event()` the same
      way LF-139 already proved for keyboard-shaped `lf_*` actions via the scenario `press`
      verb. If it can, add a `gamepad` scenario action and at least one scenario exercising
      build/select/cursor with it. If it cannot — say so explicitly in
      `data/schema/scenario.schema.json`'s description and in `CLAUDE.md`, and record whatever
      manual procedure currently substitutes, rather than leaving the gap silent.
- [ ] Write a save/load round-trip check. A `--scenario` run is one live Godot process, so a
      round trip (save, quit, relaunch, assert state matches) likely needs a small dedicated
      tool in `tools/terrain_parity.py`'s shape — call the save/load code directly where
      possible, or drive two short Godot launches if not.
- [ ] Write a minimal recovery-draft scenario reaching `draft.gd`/`recoveries.gd` — even a
      smoke-level "the draft screen offers N choices and taking one persists" proof is strictly
      better than the current zero, and directly closes the verification half of `LF-065`.
- [ ] Add a loudness measurement to `tools/audio/`: integrated LUFS (or, as a documented interim
      proxy, peak dBFS) for every committed SFX/music asset, asserted to cluster within a
      tolerance band derived from the *current, already-shipped* bank — not an arbitrary
      target. `numpy`/`soundfile` are already project dependencies.
- [ ] For anything the audit above finds genuinely infeasible to automate this pass, file a
      backlog item naming exactly what and why, rather than letting it quietly stay at zero
      with no record that anyone looked.

## Acceptance criteria

- `tools/check.py --tier 3` runs and passes `abilities.json`, `a11y-worst.json`, and
  `lf161_edge_scroll_contained.json`, verified by breaking one assertion in each and confirming
  the gate goes red naming the right file.
- Either a gamepad `InputEvent` is exercised by some automated scenario/test, or the schema and
  `CLAUDE.md` explicitly document why not and name the manual substitute.
- Either a save/load round trip is exercised by some automated tool, or the same explicit
  documentation exists.
- A loudness measurement (LUFS or peak dBFS) exists for the SFX bank with a defined
  passing/failing band, distinct from `check_sfx_reproducible`'s byte-identity check.

## Verification

```bash
.venv/bin/python tools/check.py --tier 3 2>&1 | grep -i scenario
python - <<'EOF'
import json, pathlib
p = pathlib.Path('data/scenarios/abilities.json')
d = json.loads(p.read_text())
d['timeline'][-1]['expect'] = -999
p.write_text(json.dumps(d, indent=2))
EOF
.venv/bin/python tools/scenario.py data/scenarios/abilities.json ; echo "expect non-zero: $?"
git checkout -- data/scenarios/abilities.json
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- Do not conclude "gamepad cannot be automated" without actually trying the
  `Input.parse_input_event()` route first — LF-139 already proved the general injection
  mechanism works; the open question is only whether device index/joypad-specific dispatch
  behaves the same way, not whether synthetic input works at all.
- A loudness check must not become a second metric tuned against itself the way the loop-splice
  scorer is explicitly forbidden from being (`CLAUDE.md`, decision 011) — anchor the tolerance
  band to a measurement of the current, accepted bank, and judge new material against that
  band, not by ear alone and not by re-deriving the band to fit whatever is submitted.
- `smoke.json` staying weak is fine — it is honestly labelled a proof-of-life test by its own
  note field. Do not "strengthen" it into duplicating `abilities.json`'s job; wire the existing
  stronger scenario in instead.

## Files likely touched

- `tools/check.py` (`check_scenarios_pass` or its replacement)
- `tools/scenario.py`, `scripts/scenario.gd`, `data/schema/scenario.schema.json`
- `tools/audio/` (new loudness tool)
- `tools/save_roundtrip.py` (new, if a dedicated tool is needed)
- `CLAUDE.md`, `docs/BACKLOG.md`
