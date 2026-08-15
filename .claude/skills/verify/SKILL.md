---
name: verify
description: Run the full Latticefall verification pass — mechanical gate plus adversarial quality review. Use before declaring a milestone done, before a release, or when asked whether something is actually finished.
---

# Verify

Two layers. Both are required. The first catches breakage; the second catches cheapness.

## 1. The gate

```
.venv/bin/python tools/check.py --tier 3 --json /tmp/verify-gate.json   # the default
.venv/bin/python tools/check.py --tier 4 --json /tmp/verify-gate.json   # for a release
```

Tier 3 is everything except `music loudness` and the two parity legs — the schema and
cross-reference checks, sim determinism, the sprite and audio manifests, `godot boots`, the
rendered checks, the scenarios and `anchor grades`. **If it fails, stop.** Report the failure
with its output rather than working around it.

**Why tier 3 and not the whole gate.** This command carried no `--tier` until now, which meant
it silently ran tier 4 — 22–36 minutes, because a fresh `rules parity` and `rules parity
(windows)` are ~15 minutes each. Decision 070 tiered the *wrap* and never revisited this skill.
Tier 3 is the largest tier that fits inside one 600 s foreground call, and it is the tier CI
already requires on every pull request, so a verify pass at tier 3 is not weaker than what
`main` is protected by. Reach for `--tier 4` when the pass is for an actual release — the
parity legs are what make a balance claim falsifiable, and a release should re-prove them.

**Say which tier ran.** A tier-excluded check reports `skip` with a reason and is not a pass;
the tally line names every check that did not run. Quote it.

## 2. Adversarial review

Hand to the `build-verifier` agent, **with the path to the gate JSON from step 1**. It reads
that artefact rather than running its own gate, and it will refuse to certify off anything
below tier 3 — so pass a tier-3 or tier-4 run, not a tier-2 one from a wrap.

It verifies by observation — launching, looking, listening, reading actual output files —
never by reading code and reasoning about intent.

It re-checks claims made in recent commits. A commit that says "verified" gets verified.

## Reporting

Rank defects by severity. For each: what is wrong, how to reproduce, why it matters to a
player. Do not list what works.

State plainly what was **not** exercised. A verification pass whose limits are unstated is
worth much less than one that says "I did not test X" — the reader cannot otherwise judge
how much the green result means.
