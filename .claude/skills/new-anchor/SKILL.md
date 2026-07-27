---
name: new-anchor
description: Scaffold a new Latticefall level (anchor) — layout, wave table, power tier, and dialog hooks — as validated data. Use when adding or substantially reworking any of the 24 anchors.
---

# New anchor

An anchor is **data**, never code. If building one requires an engine change, the schema is
wrong — stop and say so.

## Files

```
data/anchors/anchor-NN.json     layout, path, slots, reactor capacity, wave table
data/dialog/anchor-NN.json      brief, mid-wave interrupts, debrief — keyed by trigger
```

Both declare a `"schema"` and are validated by `tools/validate/validate_data.py`.

## Order of work

1. **Read the act's beat** in `docs/STORY.md`. The level serves the story beat, not the
   other way round.
2. **Pick the power tier.** Capacity is fixed per anchor and rises only at story beats.
   This is the primary difficulty lever — before wave counts, before enemy HP.
3. **Lay out path and slots.** Diamond grid. Single ingress unless the beat demands
   otherwise. Slots sit off-path.
4. **Write the wave table.** Each act introduces one mechanic that invalidates the previous
   act's dominant strategy — make sure this anchor participates in that.
5. **Grade it.** Hand to the `balance-analyst` agent. All three difficulties, one pass.
6. **Write dialog last**, once the level's actual shape is known. Hand to `narrative-writer`.

## Acceptance

- Validates against schema, and every referenced tower/enemy id exists.
- Winnable at all three difficulties, by **more than one build**. One viable build means
  the level is a puzzle with a single answer — that is a failure, not a difficulty setting.
- Peak bus load approaches capacity at least once. An anchor the player coasts through
  without a power decision is not using the game's hook.
- No dialog line carries information the player cannot afford to miss.
