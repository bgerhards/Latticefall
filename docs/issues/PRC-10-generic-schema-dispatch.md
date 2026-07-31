id: PRC-10
title: Generic schema dispatch in validate_data.py — data/tuning.json is validated by nothing
labels: phase-0, tooling, content
blocks: TER-01
milestone: E1 Process
---
## Problem

`tools/validate/validate_data.py`'s `main()` hardcodes four `validate_schema()` calls —
towers, enemies, anchor, dialog (lines 239-240, 253, 263). `data/tuning.json` declares
`"schema": "tuning"` on its first key and `data/schema/tuning.schema.json` exists and passes
when run by hand through `jsonschema.Draft202012Validator` — **and the gate's validator never
calls it** (LF-064). So the file holding speeds, the call-wave bonus, the kill chain, the three
bindstone abilities, targeting priority, veterancy and the recovery draft — the numbers
`docs/STATE.md` describes as *"the most likely thing in the game to be accidentally
overpowered"* — is the one content file with no mechanical validation at all.

`CLAUDE.md`'s non-negotiable is *"Game content is data, not code… validated against a
schema"*. Every document already carries a `"schema"` key naming its schema. The dispatch
should be driven by that key, not by a list someone has to remember to extend — which is the
same failure LF-067 already recorded for `STATIC_TRIGGERS` at
`tools/validate/validate_data.py:38`.

## Tasks

- [ ] Add `schema_for(doc, path) -> str` that reads `doc["schema"]` and maps it to
      `data/schema/<name>.schema.json`, erroring (not warning) when the key is absent or the
      schema file does not exist.
- [ ] Add a discovery pass in `main()`: walk `data/**/*.json` via `git ls-files`
      ({{PRC-02}}), skip `data/schema/`, and validate every document against the schema it
      names.
- [ ] Keep the existing typed `check_anchor`/`check_dialog` cross-reference passes exactly as
      they are — this issue replaces *schema dispatch*, not the semantic checks.
- [ ] Add a **completeness assertion**: every `data/schema/*.schema.json` must be exercised by
      at least one document, and every document must be validated. Report the counts in the
      check detail (`N documents against M schemas`), so a new schema that nothing uses and a
      new document that nothing validates are both visible.
- [ ] Fold `STATIC_TRIGGERS` (`tools/validate/validate_data.py:38`) into the dialog schema's
      own regex, or derive the Python set *from* the schema file, so LF-067's duplicated list
      cannot drift again.
- [ ] Validate `data/tuning.json` and fix whatever the schema says is wrong — the file has
      never been checked, so treat a first-run failure as expected work, not as a blocker.
- [ ] Check `assets/renders/sprites.json` and `assets/audio/music_manifest.json`: they are
      generated manifests, not authored content. Decide explicitly whether they get schemas
      (recommended: yes, generated files are exactly what silently changes shape) and say so
      in the docstring.
- [ ] Make the failure message name the JSON pointer and the offending value, not just
      "does not validate" — a schema error that does not say where costs more time than no
      check.
- [ ] Update `tools/check.py`'s `game data` detail line to report the document/schema counts.
- [ ] Close LF-064 with `tools/backlog.py`.
- [ ] Note in `CLAUDE.md`'s Conventions that a new content type is a schema file plus a
      `"schema"` key, and nothing else — no validator edit.

## Acceptance criteria

- `tools/validate/validate_data.py` contains no per-type hardcoded `validate_schema()` call
  list.
- The check detail reports at least 51 documents (24 anchors + 24 dialog + towers + enemies +
  tuning) against 5 schemas, and the numbers appear in `tools/check.py`'s `game data` line.
- Setting `"speeds": "fast"` in `data/tuning.json` makes `game data` red, naming
  `data/tuning.json` and the JSON pointer `/pacing/speeds`.
- Adding `data/schema/terrain.schema.json` with no document that names it makes the
  completeness assertion red.
- Adding a new `data/foo.json` with `"schema": "nope"` is an error, not a silent pass.
- Removing a trigger name from the dialog schema makes the corresponding dialog file fail —
  proving `STATIC_TRIGGERS` is no longer a second source of truth.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -c "
import json,pathlib
p=pathlib.Path('data/tuning.json'); d=json.loads(p.read_text())
d['pacing']['speeds']='fast'; p.write_text(json.dumps(d,indent=2))"
.venv/bin/python tools/validate/validate_data.py ; echo "expect non-zero: $?"
git checkout -- data/tuning.json
.venv/bin/python tools/check.py --tier 1 2>&1 | grep 'game data'
```

## Risks / gotchas

- **`data/tuning.json` is untracked at time of writing** (`git status` shows it as `??`).
  Confirm it is committed before assuming the discovery walk sees it — `git ls-files` will not
  list an untracked file, which is exactly the correctness property {{PRC-02}} wants and would
  here produce a silent zero-coverage pass. Add the completeness assertion for that reason.
- Everything in `data/tuning.json` is **GDScript-only by design** (decision 033, and the file's
  own `note` key): the Python reference sim never presses a button, so these values must be
  inert unless the player acts, or the two rule implementations stop matching. A schema is the
  right guard; do not let this issue become "wire tuning into `sim/engine.py`" — that is
  {{BAL-01}}.
- `validate_data.py` is imported by `tools/check.py` as a subprocess with `--quiet`; keep the
  exit-code and warn/ERROR line prefixes stable, since `check_game_data`
  (`tools/check.py:105`) counts lines starting with `warn`.
- The saturation guard's blind spot to `upgrade.draw_mw` (LF-103) lives in the same file but
  belongs to {{PLC-05}}. Do not fix it here; the two changes would collide.

## Files likely touched

- `tools/validate/validate_data.py`
- `data/schema/*.schema.json` (dialog trigger regex; possibly new manifest schemas)
- `tools/check.py` (`check_game_data` detail only)
- `CLAUDE.md`, `backlog.json`, `docs/BACKLOG.md`
