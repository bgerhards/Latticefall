id: BAL-07
title: Gate check banning the unsafe operation set from the rules
labels: phase-0, tooling, rules, risk
depends: PRC-02
blocks: PLC-01
milestone: E7 Balance
---
## Problem

PRD §4 invariant 2: *"The safe operation set. `+ − × ÷ sqrt fmod floor min max` and
comparisons. Never `atan2 sin cos tan pow log exp`, never `Vector2` in the rules. **Enforced by
a gate check, not by memory.**"* No such check exists.

The measurement behind it (PRD §2.1, LF-106): 100,000 float64 pairs across five value regimes,
24 operations, raw IEEE-754 bytes compared on CPython, Linux Godot 4.7.1 and Windows Godot
4.7.1. `+ − × ÷`, `sqrt`, `fmod`, `floor`, `min`/`max` and comparisons: **0 mismatches out of
100,000 on all three runtimes**. `atan2` 0.084%, `sin` 0.133%, `cos` 0.120%, `pow` 0.130%,
`log` 0.031%, `exp` 0.069%, **`tan` 4.32%** — Windows Godot's MSVC UCRT against glibc.
And `Vector2` remains banned unchanged: it is float32, and for 2,000,000 points on an exact
integer radius float32 and float64 disagree on `<= r` **10.2%** of the time.

Two things follow. First, **decision 030 is partly wrong and it is blocking work**: it bans
square roots, when `sqrt` is required to be correctly rounded by IEEE-754 §5.4.1, both runtimes
issue `SQRTSD`, and it matched 100,000/100,000. The real culprit was `Vector2.distance_to`, a
float32 helper. That mistaken ban is what stands between the project and off-grid geometry —
an end-to-end continuous-position loop with `sqrt`-normalised directions ran 4,000 ticks × 64
units **bit-identical on all three runtimes**.

Second, the rules use none of the divergent operations **by accident**. {{PLC-01}} (firing arcs
as `dot(normalise(to_target), facing) >= cos_half_angle`) and {{TER-01}} (visibility) are both
one careless `atan2` away from a parity failure that only shows up on the owner's machine and
only 0.084% of the time.

## Tasks

- [ ] Write `tools/validate/safe_ops.py` with the ban list and the allow list as data, each
      entry carrying its measured divergence rate as a comment. The numbers are the argument;
      a bare list will be re-litigated.
- [ ] Define the scope precisely: the **rules files only** — `sim/engine.py`,
      `scripts/anchor_sim.gd`, and whatever `sim/content.py` computes that feeds them. Do not
      scan `anchor_view.gd`, `iso.gd` or `fx_additive.gd`: facing and yaw are presentation-only
      by decision 049 and legitimately use trigonometry.
- [ ] Enumerate the scanned files with `git ls-files` ({{PRC-02}}), and make the scope list
      explicit in the check's detail line, so a new rules file that nobody added to the list is
      visible.
- [ ] Detect in Python with `ast`, not with grep: `ast.walk` for `Call` nodes resolving to
      `math.sin`, `math.atan2`, `**`, `pow()`, etc. A regex will miss `from math import sin as
      s` and will false-positive on the word "cos" in a comment.
- [ ] Detect in GDScript with a tokeniser or a careful line scanner that strips comments and
      strings. GDScript's `sin`, `cos`, `atan2`, `pow`, `log`, `exp`, `tan` are global
      functions; also ban `Vector2`, `Vector2i`, `.distance_to(`, `.length(`, `.normalized(`,
      `.angle(`, `.rotated(` and `**`.
- [ ] Handle `lerp` explicitly. It is linear (`a + (b - a) * t`) and therefore safe, and the
      ability falloff already uses it (`130 * lerp(0.35, 1, frac)`, `docs/STATE.md`) — allow it,
      and say why in the list.
- [ ] Provide a per-line escape hatch — a `# safe-ops-exempt: <reason>` marker — matching the
      project's existing idiom (`nomenclature-exempt`, `tools/check.py:377`), because an
      unescapable check gets deleted. Report exemptions in the detail line so they cannot
      accumulate quietly.
- [ ] Add `("safe operations", check_safe_ops)` to `tools/check.py`'s `CHECKS`, at **tier 1**
      ({{PRC-04}}) — it is a pure text scan and costs milliseconds.
- [ ] Make the failure message quote the measured divergence rate for the operation found, so
      the person who hit it learns why in the failure rather than in a document.
- [ ] Verify the check against a deliberate violation in **each** rules file, in both languages.
- [ ] Write the `docs/DECISIONS.md` entry that **supersedes decision 030** — append-only, never
      edit — naming the safe set, the banned set, the measured rates, the three runtimes, and
      the specific correction (the culprit was `Vector2.distance_to`, a float32 helper, not
      `sqrt`). Cross-reference {{BAL-06}}.
- [ ] Update `CLAUDE.md`'s non-negotiables table and `docs/STATE.md`'s trap list, which both
      currently state the old rule (*"the rules use `PackedFloat64Array` and squared range
      tests"* is still true; *"never sqrt"* is not).
- [ ] Audit the current rules files for existing violations before turning the check on, and fix
      or exempt each one explicitly.
- [ ] Close LF-106.

## Acceptance criteria

- `tools/check.py --tier 1` includes `safe operations` and it is green on `main` in under
  200 ms.
- Adding `math.sin(x)` anywhere in `sim/engine.py` makes it red, naming the file, the line, and
  the measured Windows divergence rate for `sin` (0.133%).
- Adding `var v := Vector2(x, y)` to `scripts/anchor_sim.gd` makes it red.
- Adding `sin(x)` to `scripts/anchor_view.gd` (presentation) leaves it **green** — scope is
  correct.
- `sqrt` and `lerp` in the rules are accepted with no exemption marker.
- A line carrying `# safe-ops-exempt: <reason>` is accepted and counted in the detail line.
- `docs/DECISIONS.md` contains a new entry superseding 030, and decision 030 itself is
  unedited.
- `CLAUDE.md` and `docs/STATE.md` no longer state that square roots are unsafe.

## Verification

```bash
.venv/bin/python tools/validate/safe_ops.py ; echo "exit=$?"
.venv/bin/python tools/check.py --tier 1 2>&1 | grep 'safe operations'
printf '\ndef _probe(x):\n    import math\n    return math.sin(x)\n' >> sim/engine.py
.venv/bin/python tools/validate/safe_ops.py ; echo "expect non-zero: $?"
git checkout -- sim/engine.py
printf '\nfunc _probe() -> Vector2:\n\treturn Vector2(1.0, 2.0)\n' >> scripts/anchor_view.gd
.venv/bin/python tools/validate/safe_ops.py ; echo "expect ZERO (presentation is out of scope): $?"
git checkout -- scripts/anchor_view.gd
```

## Risks / gotchas

- **Do not over-scope.** Facing and yaw are presentation-only and use trigonometry by design
  (decision 049); `iso.gd` buckets yaw with `90 * roundi(deg/90)` and a hysteresis test. A check
  that reddens on those gets scoped down by whoever is blocked, and the scoping-down is where
  the real rule gets lost.
- **`Vector2` is banned in the rules but is everywhere in the engine.** `anchor_sim.gd` exposes
  `point_at()` returning a `Vector2` (line 167) alongside `point_at_xy()` returning a
  `PackedFloat64Array` (line 150). The former is the presentation convenience and the latter is
  the rule. The check must distinguish them or it will fire on shipped, correct code — audit
  before enabling.
- **Decisions are append-only** (`CLAUDE.md`). Supersede 030 with a new entry that references
  it; do not edit it. The old entry's reasoning is the record of how the mistake was made.
- **This check cannot prove parity, only prevent a known class of break.** Say so in the
  docstring. {{BAL-06}} is what actually observes the Windows build.
- A comment or a string containing `pow` or `exp` must not trip the scanner. Strip both before
  matching, and add a test case for `"expansion"` and `"power"`.
- `**` in Python is `pow` and diverges the same way; do not forget the operator form.

## Files likely touched

- `tools/validate/safe_ops.py` (new)
- `tools/check.py` (one new check)
- `sim/engine.py`, `scripts/anchor_sim.gd` (only if the audit finds violations)
- `docs/DECISIONS.md`, `CLAUDE.md`, `docs/STATE.md`
- `backlog.json`, `docs/BACKLOG.md`
