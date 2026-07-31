id: PRC-09
title: tools/shot.py --extra must use argparse.REMAINDER
labels: phase-0, tooling
blocks: PRC-12
milestone: E1 Process
---
## Problem

`tools/shot.py` declares `--extra` as `nargs="*"` (`tools/shot.py:195`) and documents the
example `--extra --select 1 --pick pulse-turret` in its own epilog
(`tools/shot.py:196-199`) and module docstring (`tools/shot.py:18-19`). **That example has
never worked.** `argparse` classifies any argv token beginning with `-` as option-like and
refuses to fold it into `nargs="*"`, so the parser aborts with `unrecognized arguments`
before Godot is ever launched. Confirmed with a standalone repro, not anchor-specific
(LF-073).

`--extra` is the only route to eleven of `main.gd`'s verification hooks — `--paused`,
`--select`, `--pick`, `--scroll`, `--cursor`, `--build`, `--speed`, `--ability`,
`--ability-at`, `--press-at`, `--chain` (`scripts/main.gd:163-240`) — and `tools/shot.py` is
the documented, supported way to look at the game (`docs/STATE.md`). The workaround used last
session was to bypass `shot.py` entirely and invoke the Linux Godot binary under `xvfb-run` by
hand, which is exactly the raw-argv path {{PRC-06}} is going to deny.

This is one word. It is listed separately because it unblocks screenshot-driven verification
for everyone and because it is a two-hour-of-E1 pure win (PRD §5).

## Tasks

- [ ] Change `nargs="*"` to `nargs=argparse.REMAINDER` at `tools/shot.py:195`.
- [ ] Confirm the ordering constraint still holds: `build_extra_args()`
      (`tools/shot.py:65-84`) appends `args.extra` **before** `--shot <path> <frames>`
      precisely so a positional value carried by `--extra` cannot be mistaken for the frame
      count. `REMAINDER` swallows everything after `--extra`, so `--extra` must now be the
      **last** flag on `shot.py`'s own command line — document that in the help text and the
      epilog.
- [ ] Update the epilog/help to state the new rule explicitly: "`--extra` must come last;
      everything after it is passed to Godot verbatim, in order."
- [ ] Verify each of the documented forwarded flags actually reaches `main.gd`: `--paused`,
      `--select N`, `--pick <id>`, `--scroll N`, `--cursor N`, `--build <id>`, `--speed <f>`,
      `--ability-at <frame> <id>`, `--press-at <frame> <action>`, `--chain N`.
- [ ] Check the `--shot-menu` path too: `check_menu_renders` passes `--shot-menu` and
      `tools/shot.py` documents a menu shot via `--extra --shot-menu …` (`tools/shot.py:152`).
      Confirm the comment is still accurate after the change.
- [ ] Add one line to `CLAUDE.md`'s verification-hooks paragraph naming `tools/shot.py --extra`
      as the way to reach those hooks, since the paragraph currently lists the flags without
      saying how to pass them.
- [ ] Close LF-073 with `tools/backlog.py`, quoting the verified command.

## Acceptance criteria

- `tools/shot.py anchor-01 --out /tmp/s.png --no-autoplay --extra --paused` launches Godot and
  writes a PNG showing the pause overlay.
- `tools/shot.py anchor-24 --out /tmp/s.png --extra --select 1 --pick pulse-turret --scroll 3`
  runs, and the relayed `STATE`/`FRAME` lines show a non-blank frame.
- The tool's own epilog example runs verbatim.
- Passing a flag-shaped token no longer produces `unrecognized arguments`.
- `--a11y` and `--ui-scale` still work when combined with `--extra` placed last.

## Verification

```bash
.venv/bin/python tools/shot.py anchor-01 --out /tmp/paused.png --no-autoplay --extra --paused
.venv/bin/python tools/shot.py anchor-24 --out /tmp/sel.png --ui-scale 2.0 \
    --extra --select 1 --scroll 3
.venv/bin/python tools/reap.py
```

Proof: a `SHOT … err=0` line and a `FRAME coverage=…` above the 0.02 floor for each, plus the
PNGs themselves.

## Risks / gotchas

- **`REMAINDER` is greedy.** Any `shot.py` flag placed after `--extra` silently becomes a Godot
  argument. That is the trade and it is the right one, but it must be documented or the next
  person loses an hour to `--extra --paused --ui-scale 2.0` quietly not setting the UI scale.
- `main.gd`'s parser is a positional `match` over `OS.get_cmdline_user_args()`
  (`scripts/main.gd:163-240`) with no validation — an unrecognised flag is silently ignored,
  and a flag whose value is missing is silently ignored too. So a forwarded typo produces a
  screenshot of the wrong thing rather than an error. That is the argument for {{PRC-12}}'s
  scenario harness; note it here rather than fixing it here.
- Verify by **looking at the frame**, not by exit code: `docs/STATE.md`'s method section is
  explicit that every UI defect this project has had was invisible in code and obvious in a
  screenshot.
- Reap afterwards; `xvfb-run`'s cleanup trap does not reliably fire (`tools/reap.py:16-21`).

## Files likely touched

- `tools/shot.py`
- `CLAUDE.md`
- `backlog.json`, `docs/BACKLOG.md` (via `tools/backlog.py`)
