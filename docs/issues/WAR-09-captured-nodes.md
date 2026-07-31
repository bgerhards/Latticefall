id: WAR-09
title: Captured relay nodes — supply the enemy can take away
labels: rules, design, content, phase-3
depends: WAR-08, WAR-01
milestone: E5 War
---
## Problem

{{WAR-08}} makes generation something the player builds. Pillar 5 says the enemy must be able
to *take it away*, and nothing in either engine models a board feature changing hands. Today
the only thing that removes capacity is `capacity_decay_mw` (decision 031), which is a timer
the player cannot fight — the act is about choosing what fails first, not about defending
anything. A node that a unit can capture turns a fixed schedule into a place on the map worth
holding, which is the first pillar ("choosing where to make a stand") expressed in the power
economy rather than in geometry.

**Unlike the pulse ({{WAR-10}}), this is a genuinely new mechanic in both engines** — new
board state, new per-tick evaluation, new outcome fields — so it is the largest rules item in
E5 after multi-lane, and it must be costed as such.

## Tasks

- [ ] Design pass into `docs/DECISIONS.md`. The cheapest honest shape: a node is a **tile with
      an owner and a contest value**, authored in the anchor as
      `"nodes": [{"id": ..., "at": [x, y], "capacity_mw": N, "capture_seconds": S,
      "radius": R}]`. Owned by the player at wave one; any live enemy unit within `radius`
      accumulates capture progress; progress decays when none is present; at 100% the node
      flips and its `capacity_mw` leaves the bus. Record the rejected alternatives: a
      destructible emplacement (rejected — it collides with `placed`, which both engines
      compare only by `slot`, and with sell/upgrade), and a node the player must re-take by
      building (rejected — it is a build decision, not a defence decision).
- [ ] Schema: add `nodes` to `data/schema/anchor.schema.json`, `additionalProperties: false`,
      integer tile coordinates in grid, `capacity_mw` `exclusiveMinimum: 0`,
      `capture_seconds` `exclusiveMinimum: 0`.
- [ ] `sim/content.py`: a frozen `Node` dataclass and parsing onto `Anchor`.
- [ ] `sim/engine.py`: node state on `Sim` (`owner: int`, `progress: float`) as a list
      **index-parallel with `anchor.nodes`**, evaluated in the movement phase of `_step()`,
      before the fire loop reads capacity. Progress is `± DT / capture_seconds`, clamped
      `[0, 1]` — `+ − × ÷ min max` only.
- [ ] `sim/engine.py`: `capacity_now()` adds only the nodes the player still owns. The
      expression order must be fixed and mirrored.
- [ ] `scripts/anchor_sim.gd`: the identical state, the identical phase ordering, the identical
      clamp expression. Signals `node_captured(index)` / `node_recovered(index)` as
      **presentation-only**, following the contract at `:48-53`.
- [ ] Decide and mirror the recapture rule: does the node come back when the enemy leaves? A
      one-way flip is simpler and is a harsher, clearer decision; a two-way contest is more
      interesting and doubles the state. Pick one and write down the rejection.
- [ ] Unit membership test: reuse the {{WAR-03}} index, and **sort candidate indices ascending**
      before accumulating, for the same reason the targeting loop must.
- [ ] `tools/validate/validate_data.py`: a node must not sit on a lane tile, must be inside the
      grid, must not duplicate a slot, and the saturation invariant must count node capacity —
      an anchor whose nodes alone saturate the board has no power decision.
- [ ] `scripts/anchor_view.gd`: draw a node, its owner and its capture progress. It is a place
      on the map the player must be told to defend or the mechanic is invisible.
- [ ] `scripts/hud.gd`: the capacity readout must show capacity **lost to captures** distinctly
      from Act III decay, or the player reads a hostile capture as a scripted loss.
- [ ] Add `--nodes` to the verification hooks in `scripts/main.gd`: on the captured frame,
      print each node's index, owner and progress. A screenshot cannot settle ownership.
- [ ] `Outcome`: add `nodes_lost: int`, and surface it in `sim/run.py` and `tools/sweep.py`
      output — "did the build hold its supply" is a different question from "did it win".
- [ ] Author one anchor with nodes; sweep it; grade it at all three difficulties.
- [ ] Re-run parity; anchors with no `nodes` key must be byte-identical.

## Acceptance criteria

- An anchor with no `nodes` grades byte-identically to before.
- On the node anchor, both engines report the same `nodes_lost` and the same wave on which
  each node flipped, across all 864 parity runs.
- `--nodes` prints owner and progress and matches what the board draws.
- `validate_data.py` rejects a node on a lane tile and fires the saturation error on a fixture
  whose nodes alone saturate the board.
- The HUD distinguishes decay-lost capacity from capture-lost capacity.

## Verification

```bash
.venv/bin/python tools/validate/validate_data.py
.venv/bin/python -m sim.run --jobs 8 > /tmp/after.txt && diff /tmp/before.txt /tmp/after.txt
.venv/bin/python tools/sweep.py anchor-XX --jobs 8
.venv/bin/python tools/test_parity.py
.venv/bin/python tools/shot.py anchor-XX --out /tmp/nodes.png --extra --nodes
```

Proof to paste: the empty diff for node-free anchors, parity 864/864, the `--nodes` block, and
the screenshot showing capture progress.

## Risks / gotchas

- **Phase ordering is the parity risk.** Capture must resolve in the movement phase, before
  the fire loop reads `capacity()` through `brownout_penalty()`. One tick of difference in
  where the flip lands is a different fire rate for one tick, which compounds.
- Progress is a float accumulated over hundreds of ticks. `progress += DT / capture_seconds`
  and `progress += DT * (1.0 / capture_seconds)` are **not** the same double. Fix the
  expression, character for character, in both files.
- Do not annotate units. Capture state lives on the node list, not on a unit dictionary
  (LF-055).
- `Outcome` is compared field-by-field by the parity harness; adding a field means the harness
  must compare it. Confirm `tools/test_parity.py` picks it up rather than silently ignoring an
  unknown key.
- Capacity is read at nine sites (see {{WAR-08}}'s risk list). A capture changes the same
  number a brief speaks aloud.

## Files likely touched

- `data/schema/anchor.schema.json`, `data/anchors/anchor-XX.json`
- `sim/content.py`, `sim/engine.py`, `sim/run.py`
- `scripts/anchor_sim.gd`, `scripts/anchor_view.gd`, `scripts/hud.gd`, `scripts/main.gd`
- `tools/validate/validate_data.py`, `tools/sweep.py`, `tools/test_parity.py`
- `docs/DECISIONS.md`
