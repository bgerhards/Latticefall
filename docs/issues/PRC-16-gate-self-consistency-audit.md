id: PRC-16
title: The gate's own tier documentation has drifted from what it runs, and STATE.md's snapshot is stale
labels: phase-1, tooling, process
milestone: E1 Process
---
## Problem

This is the first finding of a testing audit (five questions: is the testing valuable, how
redundant is it, is coverage right, what is the strategy, what can be optimised). The most
concrete result is that `tools/check.py` — the file whose entire job is to make claims
falsifiable — is not honest about itself.

**Measured, today, on this machine:**

```
$ .venv/bin/python tools/check.py --list | wc -l
29
```

The module docstring's own `## Tiers` table says tier 3 is "26 checks" and tier 4 (the
default) is "28 checks". Both are wrong by exactly one: tier ≤3 is actually **27** checks and
tier ≤4 is actually **29**. The cause is not arithmetic — it is that the tier-3 narrative
paragraph never mentions `scenarios pass` at all ("tier 3 (~66 s, PR), 26 checks: tier 2 +
`game renders` (6.5 s), `menu renders` (4.8 s), `accessibility` (41.2 s)."), even though
`CHECKS` has carried `Check("scenarios pass", 3, check_scenarios_pass)` since PRC-12 landed.
The docstring was hand-edited when `scenarios pass` was added to `CHECKS` and simply not
updated in the same change — the exact class of drift `dialog capacity` and `banned terms`
exist to catch mechanically in *game* content, but nobody built the equivalent for the gate's
description of itself.

**`docs/STATE.md`'s committed gate block is worse.** It is dated "Last updated: 2026-07-31" —
today — and its `### Gate` section prints `tier 4 — 21 passed · 0 failed · 0 skipped ·
664922ms`, listing 21 checks total. The live `CHECKS` array has 29. Eight checks — including
`scenarios pass`, `safe operations`, `rules autoloads`, `yaw hysteresis`, `asset coverage`,
`hooks configured`, `facing harness`, and `rules parity (windows)` — are simply absent from
the recorded snapshot, meaning `tools/session.py` was not re-run after they landed even though
other prose in the same file (the "What landed this session" narrative) clearly postdates all
of them. Anyone reading STATE.md's gate block to answer "is the gate green and what does it
cover" gets an answer that is both stale and undercounted, on the same day it claims to be
current.

This matters beyond cosmetics: the whole design intent of tiering (the `Check` dataclass's own
comment) is that a re-tier "is an edit to data, not an argument anyone has to win" — but the
*prose* describing that data is not derived from it, so the data can move while the prose
that everyone actually reads (this docstring, CLAUDE.md, STATE.md) stays put. That is a check
passing — or rather, a description going unchallenged — for the wrong reason.

## Tasks

- [ ] Add a small self-consistency check to `tools/check.py` (tier 1, alongside `banned terms`
      /`dialog capacity` — the same "prose must track data" idiom already used for spoken MW
      figures): parse the module docstring's `## Tiers` section for its stated per-tier check
      counts (a small, deliberately narrow regex — this is not a general doc-parser) and
      compare each against `len([c for c in CHECKS if c.tier <= n])`. Fail naming which tier's
      stated count is wrong and by how much.
- [ ] Fix the current docstring: add `scenarios pass` to the tier-3 narrative sentence, and
      correct "26 checks" → "27 checks" and "28 checks" → "29 checks" (both occurrences, tier-3
      and tier-4/default).
- [ ] Regenerate `docs/STATE.md` via `tools/session.py` so its committed `### Gate` block
      reflects all 29 checks at tier 4, with today's real timings.
- [ ] Check whether `tools/session.py`'s `gate()` call already forwards enough information to
      make this regeneration automatic on every session wrap, or whether — per `LF-115`,
      already on the backlog — it silently defaults in a way that would let this happen again;
      cross-reference rather than re-litigate that finding.
- [ ] Note in `.claude/skills/session-wrap/SKILL.md` (if it already prescribes a gate run) that
      the regeneration step is not optional cosmetic upkeep — it is the only thing standing
      between STATE.md and exactly the staleness this issue found.

## Acceptance criteria

- `tools/check.py --list | wc -l` and the docstring's stated tier-4 count are equal, verified
  by the new self-consistency check passing.
- Adding a 30th `Check(...)` to `CHECKS` with no matching docstring edit makes the new check
  fail, naming the stale tier and the expected vs. actual count.
- `docs/STATE.md`'s `### Gate` block lists 29 checks (or whatever the true count is at merge
  time) and its total-checks arithmetic (`passed + failed + skipped`) matches.

## Verification

```bash
.venv/bin/python tools/check.py --list | wc -l
.venv/bin/python tools/check.py --tier 1 2>&1 | grep -i "tier count\|gate self"
# negative case: add a throwaway check, confirm the self-consistency check reddens
python - <<'EOF'
import pathlib
p = pathlib.Path('tools/check.py')
s = p.read_text()
s = s.replace('    Check("facing harness",    2, check_facing_harness),',
              '    Check("facing harness",    2, check_facing_harness),\n'
              '    Check("_audit_probe", 1, lambda: Result(OK, "probe")),')
p.write_text(s)
EOF
.venv/bin/python tools/check.py --tier 1 2>&1 | tail -5   # expect the new check red
git checkout -- tools/check.py
.venv/bin/python -c "
import re
doc = open('docs/STATE.md').read()
m = re.search(r'tier 4 .. (\d+) passed', doc)
print('STATE.md tier-4 total claim:', m.group(0) if m else 'not found')
"
```

## Risks / gotchas

- Keep the self-consistency check's docstring parser narrow and literal (match the exact
  phrases `"N checks"` following `tier 3` / the default tier-4 sentence) — a clever general
  parser is itself a second thing to get wrong, and the point here is a tripwire, not a
  documentation engine.
- Do not fold this into `tools/gate_report.py` or CI reporting; it belongs in `check.py` itself
  so it runs locally at tier 1, the cheapest possible signal, before anything is pushed.
- This issue does not re-derive PRC-04's own tier assignment table — it only proves the
  *count* stays honest. A future re-tier that moves a check between tiers without changing the
  total would not be caught by this alone; that is a smaller, acceptable gap, not scope creep
  to close here.

## Files likely touched

- `tools/check.py` (new check, docstring fix)
- `docs/STATE.md` (regenerated)
- `.claude/skills/session-wrap/SKILL.md` (only if it already prescribes the gate/regen step)
