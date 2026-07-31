id: PRC-04
title: Tiered gate — check.py --tier with measured budgets
labels: phase-1, tooling, perf
depends: PRC-01, PRC-02, PRC-03
blocks: PRC-05, PRC-08
milestone: E1 Process
---
## Problem

The gate is one all-or-nothing suite that takes **648,669 ms — nearly eleven minutes**
(`docs/STATE.md` gate block). `CLAUDE.md` says "run before every commit", and nobody runs an
eleven-minute check before every commit, so in practice the rule is either skipped or the
commit waits. The cost is not evenly spread: `rules parity` is **542,066 ms, 83.6% of the
total**, and `accessibility` another 41,234 ms — two checks are 90% of the wall clock, and
neither can tell you that a JSON file stopped parsing.

Measured tier budgets for this suite (with {{PRC-02}}'s enumeration fix applied):
**~6 s pre-commit**, **~14 s pre-push**, **~66 s PR**, **~9 min nightly**. Every check keeps
running; the question this issue answers is *how often*, not *whether*.

## Tasks

- [ ] Add a `tier` field to each entry in `CHECKS` (`tools/check.py:693-712`), turning the
      tuples into a small dataclass or a 3-tuple. Tier is a minimum: a check in tier 1 runs in
      tiers 1, 2 and 3.
- [ ] Assign tiers from the measured times, and write the assignment table into the module
      docstring with each check's measured cost so a future re-tier is an edit to data, not
      an argument:
      - **tier 1 (~6 s, pre-commit):** `python syntax`, `json parses`, `gdscript parses`
        ({{PRC-01}}), `game data`, `wave density`, `dialog capacity`, `backlog rendered`,
        `agent models`, `banned terms` (436 ms after {{PRC-02}}).
      - **tier 2 (~14 s, pre-push):** tier 1 + `sim determinism` (2.9 s), `sprite atlas`
        (1.1 s), `sprite coverage`, `music manifest`, `sfx determinism` (1.0 s),
        `godot boots` (5.1 s).
      - **tier 3 (~66 s, PR):** tier 2 + `game renders` (6.5 s), `menu renders` (4.8 s),
        `accessibility` (41.2 s).
      - **tier 4 (~9 min, nightly/release):** tier 3 + `rules parity` (542 s), and later
        {{BAL-06}}'s Windows parity run and {{BAL-05}}'s performance budget.
- [ ] Add `--tier N` (default 4, i.e. today's behaviour — the default must not become weaker
      than what `CLAUDE.md` currently promises).
- [ ] Make the summary line state the tier and **name every check the tier excluded**, in the
      same voice as the existing `--no-window` warning: they did not run and are not passes.
- [ ] Carry `tier` into {{PRC-03}}'s JSON, and mark excluded checks
      `status: skip, skipped_reason: "tier"` rather than omitting them — an absent key reads
      as a pass to anything consuming the file.
- [ ] Re-measure every check after {{PRC-02}} lands and update the docstring table with the
      real numbers; the figures above are the pre-change baseline for all but `banned terms`.
- [ ] Assert the budgets: add a `--budget` mode (or a nightly assertion) that fails if
      tier 1 exceeds 10 s or tier 2 exceeds 25 s. A tier whose budget silently doubles is a
      tier nobody will run, which is the failure mode this whole issue is about.
- [ ] Keep `--no-window` working and orthogonal to `--tier`; document the interaction (tier 3
      with `--no-window` is tier 2 plus nothing).
- [ ] Update `CLAUDE.md`'s Commands block and the "the gate" paragraph, and both session
      skills (`.claude/skills/session-*`) if they name the gate command.
- [ ] Regenerate `docs/STATE.md` with `tools/session.py` so the recorded gate block shows the
      tier it was produced at.

## Acceptance criteria

- `tools/check.py --tier 1` completes in under 10 s wall clock on this machine and runs
  exactly the nine tier-1 checks.
- `tools/check.py --tier 2` completes in under 25 s.
- `tools/check.py --tier 3` completes in under 120 s.
- `tools/check.py` with no flags runs all 18 checks, as it does today.
- Every tier's summary line names the excluded checks and states that they are not passes.
- Breaking `data/towers.json`'s JSON turns `--tier 1` red (proving the cheap tier still has
  teeth); breaking `sim/engine.py`'s brownout constant leaves `--tier 1` green and `--tier 4`
  red (proving the tier boundary is where it is claimed to be).

## Verification

```bash
for t in 1 2 3; do /usr/bin/time -f "tier $t %e s" .venv/bin/python -u tools/check.py --tier $t; done
.venv/bin/python tools/check.py --tier 1 --json /tmp/t1.json
.venv/bin/python -c "import json;d=json.load(open('/tmp/t1.json'));print(sum(1 for c in d['checks'] if c['status']!='skip'))"
# teeth test
python - <<'EOF'
import pathlib; p=pathlib.Path('data/towers.json'); s=p.read_text(); p.write_text(s[:-1])
EOF
.venv/bin/python tools/check.py --tier 1 ; echo "expect non-zero: $?"
git checkout -- data/towers.json
```

## Risks / gotchas

- **A skip is never a pass.** `tools/check.py`'s docstring says so and the summary already
  enforces it for two skip kinds. A third kind must be as loud, or "I ran the gate" becomes
  ambiguous — that ambiguity is precisely how a 36-minute `game renders` reported `ok`
  (LF-061).
- Do not put `rules parity` below tier 4 on the argument that it is "usually fine". It is the
  one thing making any balance claim in this project falsifiable (PRD §1) and 864 runs is what
  makes it so.
- `accessibility` launches Godot **five** times; it is tier 3 because of that, not because
  a11y is optional. Anything touching `Ui`, the HUD or a panel must run tier 3.
- The rendered checks capture invisibly under Xvfb on this machine (decision 052), but a
  machine without a native Linux Godot or `xvfb-run` falls back to a real window — on such a
  machine tier 3 opens seven windows. Say so in `--help`.
- Reap after every measurement run: `.venv/bin/python tools/reap.py`. The parity check's Godot
  survives its parent.

## Files likely touched

- `tools/check.py`
- `CLAUDE.md`, `docs/STATE.md`
- `.claude/skills/session-start/SKILL.md`, `.claude/skills/session-wrap/SKILL.md` (only where
  the gate command is quoted)
