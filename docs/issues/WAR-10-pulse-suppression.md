id: WAR-10
title: The pulse — an enemy that suppresses emplacements instead of damaging them
labels: rules, engine, content, phase-2
depends: WAR-01
milestone: E5 War
---
## Problem

Every enemy in `data/enemies.json` answers the same question: how much damage does it take to
stop it. The one interesting exception, `drains_mw`, is a *tax* rather than an interruption.
Nothing in the game takes an emplacement off the bus against the player's will, which means
the player's whole relationship with their own board is "build it and it stays built". The
pulse is the antagonist mechanic that makes holding ground an active thing: it suppresses.

**It cannot be GDScript-only.** Decision 033 put sell, upgrade, the three abilities, targeting
modes and veterancy in `scripts/anchor_sim.gd` only, on the argument that they are inert unless
a player acts and therefore cannot move a graded number. The pulse is driven by the core loop
whether or not anyone presses anything, so a GDScript-only pulse means every anchor is
**graded against weaker rules than it is played against** — the exact failure the parity
harness exists to catch. The upside is symmetric: implemented in both files, the existing
864-run sweep exercises it for free.

## Tasks

- [ ] `docs/DECISIONS.md` entry. State the shape: suppression is `emp_until: float` on the
      **placed record**, compared against `t`. This is safe under the standing invariant that
      `placed` records are only ever compared by `slot` — the same argument `aim`, `kills`,
      `view_yaw` and `target_mode` already rely on (`anchor_sim.gd:232-246`, `:499`, `:537`).
      Record explicitly that a **unit** may never grow a key (LF-055) and that this is why the
      state lives on the emplacement rather than on the pulsing unit.
- [ ] Schema: add to `data/schema/enemies.schema.json` a `pulse` block —
      `{"radius": float, "interval": float, "duration": float}`, `additionalProperties: false`,
      all `exclusiveMinimum: 0`, optional. A unit with no `pulse` behaves exactly as today.
- [ ] `sim/content.py`: parse `pulse` onto the frozen `Enemy` dataclass as three fields with
      zero defaults (`pulse_radius`, `pulse_interval`, `pulse_duration`), matching the flat
      style already used for `drains_mw`.
- [ ] `sim/engine.py`: add `emp_until: float = 0.0` to `Placed` and a per-unit pulse clock.
      The clock is state on the unit — so it must be a **field on the `Unit` dataclass set at
      construction**, never a dictionary key added later, and the GDScript side must write the
      same key at `spawn()` for the same reason `lane` is safe in {{WAR-01}}.
- [ ] **Apply suppression in the movement phase, before the fire loop reads it**, in both
      files, at the same point relative to the leak test. A pulse that lands after the fire
      loop suppresses a gun that has already fired this tick, and the two implementations will
      not agree about which.
- [ ] Four read sites gain the check beside `online`, in both files:
      `online_draw()` (`anchor_sim.gd:176-189` / `sim/engine.py:220-221`) — a suppressed gun
      draws nothing;
      `_covered_by()` (`anchor_sim.gd:373-388` / `:279-288`) — a suppressed field stops
      covering;
      the `_step()` fire loop (`anchor_sim.gd:467-469` / `:326-328`) — a suppressed gun does
      not fire and **does not tick its cooldown**;
      `capacity()` (`anchor_sim.gd:274-279` / `:200-202`) — a suppressed restorer stops
      supplying, which is the whole point of pulsing one.
- [ ] Decide and mirror the cooldown behaviour under suppression: freeze (`continue` before
      the decrement) or keep draining. Freeze is the defensible choice — a suppressed gun is
      off, not reloading — and it is one line, in the same place, in both files.
- [ ] **Do not reuse `set_online()`.** `scripts/anchor_sim.gd:309-311` is an unconditional
      write and it is reachable from a player right-click via
      `scripts/anchor_view.gd:882-892`, so a suppressed gun could simply be switched back on.
      Suppression must be a separate field the player cannot write.
- [ ] `scripts/anchor_view.gd`: `toggle_at()` must refuse a suppressed emplacement and play
      `ui_deny`, and `drawables()` must mark it — otherwise the player right-clicks a dead gun
      and concludes the input is broken.
- [ ] `scripts/hud.gd`: the inspector must say *suppressed*, distinctly from *offline*. Colours
      and sizes from `Ui`, never literals (decisions 045/046).
- [ ] Presentation: a `pulse_landed(at_tile, radius)` signal on `anchor_sim.gd`, following the
      presentation-only contract at `:48-53`, plus an FX response in `scripts/combat_fx.gd`.
      Nothing in the rules may read it.
- [ ] Add a pulsing enemy row to `data/enemies.json` (check `docs/NOMENCLATURE.md`'s banned
      list before naming it) and place it in two Act III waves.
- [ ] Add `--suppressed` to `scripts/main.gd`'s verification hooks: on the captured frame,
      print each placed record's slot, `online` and `emp_until` against `t`. A screenshot
      shows a dimmed sprite; it cannot show whether the rules agree.
- [ ] Re-run the 864-run parity set — this is the payoff, and it must be run **after** the
      pulsing enemy is in a graded wave, or nothing has been exercised.
- [ ] Re-grade all 24 anchors. Anchors with no pulsing enemy must be byte-identical; the two
      that gain one will move, and that movement needs a sweep, not an acceptance.

## Acceptance criteria

- An enemy with no `pulse` block produces byte-identical grades to before the change on all
  24 anchors.
- On an anchor carrying a pulsing enemy, both engines agree on every `Outcome` field across
  all 864 parity runs.
- A suppressed emplacement: draws 0 MW, provides no coverage, does not fire, does not tick its
  cooldown, and adds no capacity if it is a restorer. All five assertable from the sim, not
  from the screen.
- Right-clicking a suppressed emplacement does **not** bring it online; the input plays
  `ui_deny`.
- `--suppressed` output matches the drawn state on the same frame.
- Suppression is applied in the movement phase in both files, verifiable by reading the two
  `_step()` bodies side by side.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/sweep.py anchor-21 anchor-23 --jobs 8
.venv/bin/python tools/shot.py anchor-21 --out /tmp/emp.png --extra --suppressed
```

Proof to paste: the empty diff for pulse-free anchors, parity's 864/864 with a pulse anchor in
the set, the sweep grid for the two changed anchors, and the `--suppressed` block.

## Risks / gotchas

- **`set_online()` is a trap, not a shortcut.** It is one line and it does exactly the visible
  half of what is wanted, and it hands the player an undo for the enemy's mechanic.
- **Phase order.** Movement phase, before the fire loop, identically. This is stated twice on
  purpose.
- Sizing from the PRD: ~30–40 lines of Python, ~35–45 of GDScript, ~10–15 of schema. If the
  change is running well past that, the shape has drifted from `emp_until` and is probably
  becoming a status-effect system.
- `emp_until` is compared against `t`, which both engines advance identically
  (`anchor_sim.gd:431`, `sim/engine.py:433`). Compare `t < emp_until`, not
  `emp_until - t > 0.0` — same maths, but pick one and use it in both files.
- A suppressed emplacement drawing 0 MW **reduces** bus load, which can lift a brownout, which
  raises fire rate for everything else. That is a legitimate and interesting consequence, and
  it means a pulse can accidentally help. Sweep it; do not assume it is a bug.
- The `restorer` case can drop capacity below current load and trigger a brownout on the tick
  the pulse lands. Make sure `capacity()` cannot return a value that makes
  `brownout_penalty()` divide by zero (`cap_mw <= 0.0` is already guarded at
  `anchor_sim.gd:410` / `sim/engine.py:60`; confirm it still is).

## Files likely touched

- `data/schema/enemies.schema.json`, `data/enemies.json`, `data/anchors/anchor-2*.json`
- `sim/content.py`, `sim/engine.py`
- `scripts/anchor_sim.gd`, `scripts/anchor_view.gd`, `scripts/hud.gd`,
  `scripts/combat_fx.gd`, `scripts/main.gd`
- `docs/DECISIONS.md`, `docs/NOMENCLATURE.md`
