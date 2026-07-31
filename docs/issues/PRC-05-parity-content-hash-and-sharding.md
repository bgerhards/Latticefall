id: PRC-05
title: Content-hash gating and cost-balanced sharding for the parity check
labels: phase-1, tooling, perf, risk
depends: PRC-04
blocks: WAR-01
milestone: E1 Process
---
## Problem

`rules parity` is **542,066 ms of a 648,669 ms gate — 83.6%** (`docs/STATE.md` gate block).
It runs 864 simulations through both rule implementations on every commit, including the
commits that changed a dialog line, a colour, or this file. Parity is a **pure function of
four paths**: `sim/engine.py`, `scripts/anchor_sim.gd`, `sim/content.py` and `data/**`. If
none of them moved, the answer cannot have moved either.

The second half is throughput. `tools/test_parity.py` fans out over anchors with a
`ThreadPoolExecutor`, but a naive split by *count* is badly unbalanced: **anchor-24 costs
513 ms against anchor-01's 31 ms** — 16.5×. Balancing by measured cost instead of by anchor
count is what keeps the wall clock near the slowest single anchor rather than near the
slowest arbitrary third of them.

This matters beyond today's gate: PRD §7 risk 6 is *"parity wall-clock at 10× units:
9 minutes → potentially hours"*. {{WAR-01}} multiplies the unit budget, and nothing about the
current harness degrades gracefully when it does.

## Tasks

- [ ] Write `parity_inputs_digest()` — a SHA-256 over the *contents* of `sim/engine.py`,
      `scripts/anchor_sim.gd`, `sim/content.py`, `scripts/test/parity.gd`, and every file under
      `data/` (enumerated with `git ls-files`, {{PRC-02}}), plus the Godot binary's own
      version string from `godot --version`.
- [ ] Include `scripts/test/parity.gd` in the digest — it is the GDScript harness, and a change
      to it changes what is compared even when neither rule file moved.
- [ ] Include the Godot version. A Godot upgrade is exactly the event that can move float
      behaviour without moving a byte of this repo, and skipping the check across an upgrade
      would be the worst possible cache hit.
- [ ] Store the last-passing digest in a small cache file. Choose a path that is **not** in
      `.godot/` and is either gitignored or committed deliberately — a per-machine cache under
      `.cache/parity.json` is the safer default. Record the decision in the docstring.
- [ ] Skip with `status: skip, skipped_reason: "cached"` and a detail line naming the digest
      and the commit it last passed at ({{PRC-03}}'s JSON, {{PRC-04}}'s tiering).
- [ ] Add `--force` / `--no-cache` to both `tools/check.py` and `tools/test_parity.py`, and
      make tier 4 / nightly / release **always** force. A cache that can never be bypassed is a
      cache that will eventually be wrong and unfalsifiable.
- [ ] Instrument `tools/test_parity.py` to record per-anchor wall clock and write it to a
      cost table (`tools/parity_costs.json`), refreshed on every full run.
- [ ] Replace the current fan-out with a longest-processing-time-first bin-pack over that
      cost table: sort anchors by recorded cost descending, assign each to the currently
      lightest shard. Fall back to equal-count splitting when the cost table is absent.
- [ ] Add `--shard I/N` to `tools/test_parity.py` so CI ({{PRC-08}}) can run parity across
      several jobs and so a local run can be bisected.
- [ ] **Prove determinism is unaffected.** The sim has no RNG and no shared state, so shard
      order must not change any outcome: run the full set serially and sharded, and diff the
      collected outcome list, not just the pass/fail.
- [ ] Guard against the stale-cache footgun: if the cache file's recorded digest matches but
      the recorded Godot path differs, force a run.
- [ ] Update `CLAUDE.md`'s parity paragraph and `docs/STATE.md`, stating plainly that a cached
      skip is a skip.

## Acceptance criteria

- Running the gate twice on an unchanged tree: the first `rules parity` runs (~542 s), the
  second reports `skip (cached)` in under 1 s.
- Touching one byte of `sim/engine.py` (even a comment) makes the next run execute parity in
  full. Same for `scripts/anchor_sim.gd`, `sim/content.py`, `data/towers.json`, and
  `scripts/test/parity.gd`.
- Touching `docs/STATE.md` or `scripts/hud.gd` does **not** invalidate the cache.
- `tools/test_parity.py --shard 0/4 … --shard 3/4` together cover all 864 runs exactly once,
  and the union of their outcome lists is byte-identical to the serial run's.
- The slowest shard of a 4-way cost-balanced split is within 25% of the mean shard time;
  a count-based split on the same data is measurably worse (record both).
- `--force` runs parity even when the digest matches.

## Verification

```bash
.venv/bin/python -u tools/check.py --tier 4 2>&1 | grep 'rules parity'   # full run, records digest
.venv/bin/python -u tools/check.py --tier 4 2>&1 | grep 'rules parity'   # expect skip (cached), <1s
printf '\n# cache probe\n' >> sim/engine.py
.venv/bin/python -u tools/check.py --tier 4 2>&1 | grep 'rules parity'   # expect a full run
git checkout -- sim/engine.py
# shard equivalence
.venv/bin/python tools/test_parity.py --json > /tmp/serial.json
for i in 0 1 2 3; do .venv/bin/python tools/test_parity.py --shard $i/4 --json > /tmp/s$i.json; done
.venv/bin/python - <<'EOF'
import json,glob
merged=sorted(sum((json.load(open(f)) for f in sorted(glob.glob('/tmp/s?.json'))),[]),key=repr)
serial=sorted(json.load(open('/tmp/serial.json')),key=repr)
print("identical" if merged==serial else "DIVERGED")
EOF
.venv/bin/python tools/reap.py
```

## Risks / gotchas

- **A cached skip is a skip, not a pass.** This project's own gate docstring makes that
  distinction load-bearing, and LF-061 is the record of what happens when a check reports
  success for the wrong reason. The summary line must say it out loud.
- **Killing the parity check does not kill its Godot** (`CLAUDE.md`). It reparents to init and
  holds a core at 100%. Any sharding experiment must end with `tools/reap.py --kill`, and
  {{PRC-07}}'s leases must land before sharding runs concurrently with sibling agents.
- **Tie-break ordering is a live parity risk** — LF-055 is the precedent: Godot compares
  `Dictionary` by value while Python compares by identity, and two same-kind units at equal
  `dist` diverged. Sharding must not reorder anything *inside* a run; only whole
  (anchor, policy, difficulty) triples may move between shards.
- The digest must hash **file contents**, not mtimes. `git ls-files` plus `sha256` of bytes;
  a checkout resets mtimes and would produce spurious invalidation, and a touch-free edit
  (rare but possible over a network mount) would produce a spurious hit.
- Do not put the cache in `.godot/`. Rebuilding that directory blanks the level for whoever is
  playing (LF-075), and {{PRC-15}} records that two concurrent gate runs already delete each
  other's fixed-path artefacts there.

## Files likely touched

- `tools/test_parity.py`
- `tools/check.py` (`check_rules_parity`)
- `tools/parity_costs.json` (new, generated)
- `.cache/parity.json` (new, generated) and `.gitignore`
- `CLAUDE.md`, `docs/STATE.md`
