id: PRC-01
title: GDScript parse check in the gate and as a PostToolUse hook
labels: phase-0, tooling, process
blocks: PRC-06
milestone: E1 Process
---
## Problem

A GDScript parse error in this project is **a hang or a blank frame, never an error at the
failure site** (`docs/STATE.md`, "Traps that have already cost time"). `var x := node.method()`
on an untyped receiver cannot be inferred, that is a *parse* failure, the whole script fails
to load, Godot downgrades the node to a scriptless base class, and the cascading errors name
files that are not broken. This cost time **five separate times in one session, in five
different files**; once `main.gd` failed to parse and the game simply hung on the menu with
no output at all (LF-055 write-up, decision 055, LF-063). The gate has no check for it —
`godot boots` greps stdout for `SCRIPT ERROR`/`Parse Error`, which only fires for scripts the
*main scene* happens to load, so `draft.gd`, `options_menu.gd` and `scripts/test/*.gd` are
never parsed at all.

Measured: `--headless --path . --check-only --script res://scripts/<f>.gd` over all 27 game
scripts takes **1.59 s** — cheaper than `sim determinism` (2.9 s) and 0.25% of the current
648 s gate. The catch is that **exit code alone is unusable**: `--check-only` reports spurious
`Identifier not found: <autoload>` for every autoload reference, because a script checked in
isolation has no autoload singletons registered. Those must be filtered by parsing the
`[autoload]` section out of `project.godot`, or the check is red on `main` from day one.

## Tasks

- [ ] Probe `--check-only --script` against the installed Godot 4.7.1 on this machine and
      record the exact stdout/stderr shape for (a) a clean file, (b) a real parse error,
      (c) an autoload-only "error". Do not derive the format from memory — CLAUDE.md's
      "verify against the installed tool" rule.
- [ ] Write `tools/validate/gdscript.py`: a module docstring explaining *why* (the hang, not
      the lint), a `parse_project_autoloads(project_godot: Path) -> set[str]` that reads the
      `[autoload]` section, and a `check_file(path) -> list[str]` returning real diagnostics
      only.
- [ ] Build the ignore predicate from the autoload set: drop any diagnostic matching
      `Identifier "<name>" not found` / `Identifier not found: <name>` where `<name>` is an
      autoload. Keep every other diagnostic, including a *different* unknown identifier —
      a blanket "ignore identifier errors" filter would swallow the exact class of typo this
      check exists for.
- [ ] Enumerate the files with `git ls-files '*.gd'` (see {{PRC-02}}), excluding `addons/`.
      Confirm the count is 30 tracked, 27 under `scripts/` excluding `scripts/test/` — assert
      the count in the check's detail line so a file that stops being scanned is visible.
- [ ] Decide and document whether `scripts/test/parity.gd`, `scripts/test/facing.gd` and
      `tools/godot/setup_input.gd` are scanned. They should be: `parity.gd` failing to parse
      turns the 9-minute parity check into an opaque "godot produced no parity output".
- [ ] Run the per-file checks concurrently (`ThreadPoolExecutor`, as `tools/test_parity.py`
      already does) and measure. If a single `--check-only` invocation can take a list of
      scripts, prefer that; probe rather than assume.
- [ ] Add `("gdscript parses", check_gdscript_parses)` to `CHECKS` in `tools/check.py`,
      placed **before** `godot boots` so the specific failure is reported before the vague one.
- [ ] Return `SKIP` when `toolpaths.godot()` is `None`, matching the existing convention that
      a missing subsystem skips and never silently passes.
- [ ] Route the subprocess through `tools/check.py`'s bounded `run()` helper so a wedged
      Godot is a red run, not a silent wait (LF-061 precedent).
- [ ] Add a `PostToolUse` hook in `.claude/settings.json` matching `Edit|Write` on `*.gd`
      that runs the same module on the single edited file and prints diagnostics. Scope it to
      one file — a 1.59 s whole-project check on every keystroke-sized edit is not free.
      Coordinate with {{PRC-06}}, which owns the rest of that file.
- [ ] Verify the hook by deliberately writing an untyped-receiver line into a scratch `.gd`
      and confirming the hook output appears in the transcript.
- [ ] Record the trap and the check in `CLAUDE.md` (Conventions) and refresh
      `docs/STATE.md`'s gate block via `.venv/bin/python tools/session.py`.
- [ ] Add a `docs/DECISIONS.md` entry only if the autoload-filtering approach is contentious;
      otherwise a `CLAUDE.md` line is enough.

## Acceptance criteria

- `tools/check.py --list` includes `gdscript parses`.
- On unmodified `main`, `gdscript parses` reports `ok` with a detail line naming the file
  count, and the reported time is under 5 s.
- Introducing `var x := $Node.some_method()` on an untyped receiver in any one of the 27
  scripts makes the check `FAIL`, and the failure detail names **that file and line**.
- Reverting the deliberate break makes it pass again.
- A file that references only autoloads (e.g. `Content`, `Progress`, `Audio`, `Ui`) parses
  clean — no `Identifier not found` noise reaches the report.
- Editing a `.gd` file through the `Edit` tool surfaces parse diagnostics in the same turn.

## Verification

```bash
.venv/bin/python tools/check.py --list | grep 'gdscript parses'
.venv/bin/python -u tools/check.py 2>&1 | grep -E 'gdscript parses|godot boots'
# deliberate break, then restore
cp scripts/hud.gd /tmp/hud.gd.bak
printf '\nfunc _lf_probe() -> void:\n\tvar x := $Nope.thing()\n\tprint(x)\n' >> scripts/hud.gd
.venv/bin/python tools/validate/gdscript.py ; echo "exit=$?"   # must be non-zero, naming hud.gd
cp /tmp/hud.gd.bak scripts/hud.gd
```

Proof is the pair: non-zero exit naming `scripts/hud.gd` while broken, zero after restore.

## Risks / gotchas

- **`--check-only` reports spurious `Identifier not found` for autoloads** (`docs/STATE.md`).
  Filtering it by autoload *name* is the whole design; a regex that drops all identifier
  errors makes the check green on real typos.
- **A new `class_name` is invisible until the editor imports**, and the symptom is a hang, not
  an error (`CLAUDE.md`). A parse check will not catch that — say so in the check's docstring
  so nobody assumes it did.
- **Do not rebuild `.godot/`** to make the check work. The owner plays out of this same tree
  and a cold import blanks their running level (LF-075). Import in place if an import is
  needed at all.
- **Python block-buffers a redirected stdout** — use `python -u` when piping the gate.
- Godot capture goes through `toolpaths.godot_argv()`; `--headless` never opens a window, so
  pass `want_window=True` here exactly as `check_godot_boots` does (there is nothing for Xvfb
  to hide) — but do not shell out to a raw path, or {{PRC-06}}'s deny hook will refuse it.
- Kill what you start: `.venv/bin/python tools/reap.py` after any probing.

## Files likely touched

- `tools/validate/gdscript.py` (new)
- `tools/check.py` (`CHECKS`, one new check function)
- `.claude/settings.json` (`PostToolUse` entry — coordinate with {{PRC-06}})
- `CLAUDE.md`, `docs/STATE.md`
