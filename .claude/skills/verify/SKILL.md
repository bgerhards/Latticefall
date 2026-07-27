---
name: verify
description: Run the full Latticefall verification pass — mechanical gate plus adversarial quality review. Use before declaring a milestone done, before a release, or when asked whether something is actually finished.
---

# Verify

Two layers. Both are required. The first catches breakage; the second catches cheapness.

## 1. The gate

```
.venv/bin/python tools/check.py
```

Schema validation, data cross-references, sim determinism, asset manifest integrity, Python
syntax. Mechanical, fast, binary. **If it fails, stop.** Report the failure with its output
rather than working around it.

## 2. Adversarial review

Hand to the `build-verifier` agent. It verifies by observation — launching, looking,
listening, reading actual output files — never by reading code and reasoning about intent.

It re-checks claims made in recent commits. A commit that says "verified" gets verified.

## Reporting

Rank defects by severity. For each: what is wrong, how to reproduce, why it matters to a
player. Do not list what works.

State plainly what was **not** exercised. A verification pass whose limits are unstated is
worth much less than one that says "I did not test X" — the reader cannot otherwise judge
how much the green result means.
