#!/usr/bin/env python3
"""
Decide what tier a session wrap's gate should run at, and show the parity cache state,
before anything expensive starts. PRC-20.

    .venv/bin/python tools/wrap_gate.py            # decide from the session's own diff
    .venv/bin/python tools/wrap_gate.py --release   # force tier 4 (nightly / actual release)
    .venv/bin/python tools/wrap_gate.py --json       # machine-readable decision, no probe text

Why tier 2 is the wrap's default
---------------------------------
Every commit on `main` now arrives through a gated pull request — the `ship` skill runs
`check.py` at tier 2 or 4 before a PR opens, and CI runs tier 1 on top of that. By the time
a session wrap runs, whatever landed has already been verified once. The wrap's own edits
are `docs/STATE.md`, `docs/BACKLOG.md`, `docs/DECISIONS.md` and `docs/chronicle/**` — none
of which touch rules, sim, or assets — so re-proving the whole 28-check, ~9-minute gate on
every wrap is largely re-running work the PR flow just did. Tier 2 (~21-25s: syntax, data,
sim determinism, sprite/atlas/audio integrity) is honest coverage for what a wrap actually
changes. `--release` is the explicit escape hatch for when a wrap is not following a gated
PR — a hand-run wrap after a rebase, a pre-release check, or any session where the operator
wants the full nightly-grade proof.

Escalation
----------
A wrap defaulting to tier 2 is only honest if nothing in scope for tier 3/4 actually moved.
Two independent, automatic triggers force tier 4 even without `--release`:

1. **Path escalation.** If the session's own diff — everything uncommitted (staged,
   unstaged, untracked) plus every commit this branch carries that `main` does not have —
   touches `scripts/anchor_sim.gd`, anything under `sim/`, `data/`, or `assets/`, tier 2
   would silently under-verify: those are exactly the files that can move a parity outcome
   or a rendered frame, and none of the tier-2 checks re-run parity, scenarios, renders or
   accessibility.
2. **Digest escalation.** `tools/test_parity.py`'s own content-hash digest
   (`parity_inputs_digest()`) is deliberately wider than "the paths above" — it also folds
   in the resolved Godot binary's `--version` string, so an engine upgrade invalidates it
   with zero repo diff (a deliberate PRC-05 property, not a bug — see that file's module
   docstring). A digest MISS this script observes, for any reason, is treated the same as a
   path hit: something that can move a parity outcome changed, and the wrap must not take
   the cheap path on the strength of a path filter that cannot see an engine upgrade.

Either trigger prints which condition fired and why, so escalation is never silent — the
whole point of PRC-20 is that a fast wrap must be an HONEST wrap, never a quiet one.

This script only decides and reports; it does not run `tools/check.py` itself; and it does
not run `tools/test_parity.py`'s actual 1152-run comparison — the digest probe below calls
the same hashing function `test_parity.py` uses for its own cache check, not the comparison
itself, so the visibility this prints costs a fraction of a second even when the cache is
stale.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import test_parity  # noqa: E402
import toolpaths  # noqa: E402

# The same four locations CLAUDE.md and PRC-20 both name as "rules, sim, or asset content" —
# exactly what test_parity.py's own DIGEST_FIXED_FILES + data/** already cover for parity,
# plus assets/, which no parity digest touches but which PRC-20's own task list names
# explicitly as a path that must not go out on the cheap tier.
ESCALATE_EXACT = {"scripts/anchor_sim.gd"}
ESCALATE_PREFIXES = ("sim/", "data/", "assets/")


def _sh(*args: str) -> str:
    r = subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT))
    return r.stdout


def _matches(rel: str) -> bool:
    return rel in ESCALATE_EXACT or rel.startswith(ESCALATE_PREFIXES)


def changed_paths() -> list[str]:
    """Every path the session's own work has touched: uncommitted changes (staged,
    unstaged, untracked) unioned with every commit this branch carries that `main` does
    not. Either half alone is wrong — a wrap can run mid-session with nothing committed
    yet, or after several commits with a clean working tree (the `ship` skill already
    committed, wrap runs after) — so both are checked.

    Falls back to working-tree-only when there is no local `main` to diff against (a
    fresh clone with a differently named default branch, or `main` itself is checked
    out) — `git merge-base HEAD main` failing is treated as "nothing to compare", not as
    an error, since a wrap must still be able to decide a tier on a machine where that
    lookup does not resolve.
    """
    paths: set[str] = set()

    # Uncommitted: staged + unstaged.
    for line in _sh("git", "diff", "--name-only", "HEAD").splitlines():
        if line.strip():
            paths.add(line.strip())
    # Untracked, respecting .gitignore.
    for line in _sh("git", "ls-files", "--others", "--exclude-standard").splitlines():
        if line.strip():
            paths.add(line.strip())

    # Committed-but-not-on-main: this branch since it diverged.
    mb = _sh("git", "merge-base", "HEAD", "main").strip()
    if mb:
        for line in _sh("git", "diff", "--name-only", mb, "HEAD").splitlines():
            if line.strip():
                paths.add(line.strip())

    return sorted(paths)


def decide_tier(release: bool) -> tuple[int, list[str]]:
    """The tier to run, and the reason(s) — empty when `--release` or tier 2 needs no
    justification (the default case)."""
    if release:
        return 4, ["--release requested"]
    hits = [p for p in changed_paths() if _matches(p)]
    if hits:
        return 4, [f"session diff touches {p}" for p in hits[:8]] + (
            [f"...and {len(hits) - 8} more"] if len(hits) > 8 else [])
    return 2, []


def probe_digest() -> dict:
    """The same cache-hit question `tools/test_parity.py` asks itself before a full run,
    answered here without running the comparison. Returns a dict rather than printing
    directly, so `--json` and the human path share one source of truth."""
    exe = toolpaths.godot()
    if exe is None:
        return {"available": False, "reason": "godot not installed"}
    digest = test_parity.parity_inputs_digest(exe)
    cache_path = test_parity._cache_path_for(None)
    cache = test_parity._load_cache_at(cache_path)
    hit = test_parity._cache_hit(cache, digest, exe)
    n_data = len(test_parity._tracked_data_files())
    return {
        "available": True,
        "digest": digest,
        "digest_short": digest[:12],
        "cache_hit": hit,
        "cache_path": str(cache_path.relative_to(ROOT)),
        "covers": f"{len(test_parity.DIGEST_FIXED_FILES)} rule file(s) + {n_data} data "
                  f"file(s) + godot --version",
        "last_verified": (cache or {}).get("passed_at") if hit else None,
        "last_commit": (cache or {}).get("commit") if hit else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", action="store_true",
                    help="force tier 4 unconditionally (nightly / pre-release wrap)")
    ap.add_argument("--json", action="store_true",
                    help="print the decision as one JSON object instead of human text")
    args = ap.parse_args()

    tier, reasons = decide_tier(args.release)
    digest = probe_digest()

    # Digest escalation: a cache MISS is a second, independent trigger, checked after the
    # path-based decision so both reasons show when both fire — a reader should never have
    # to guess which one actually caused tier 4.
    if tier < 4 and digest.get("available") and not digest.get("cache_hit"):
        tier = 4
        reasons.append("parity digest cache miss (see digest line below) — something that "
                       "can move a parity outcome changed even though no watched path did")

    if args.json:
        print(json.dumps({"tier": tier, "reasons": reasons, "digest": digest}, indent=2))
        return 0

    if reasons:
        print(f"wrap gate: ESCALATING to tier 4")
        for r in reasons:
            print(f"  - {r}")
    else:
        print(f"wrap gate: tier 2 (default — no rules/sim/data/asset change in this "
              f"session's diff)")

    if digest.get("available"):
        if digest.get("cache_hit"):
            print(f"parity digest: {digest['digest_short']} — CACHED, last verified "
                  f"{digest.get('last_verified', '?')} at commit "
                  f"{(digest.get('last_commit') or '?')[:12]} (covers "
                  f"{digest['covers']})")
        else:
            print(f"parity digest: {digest['digest_short']} — NOT cached (covers "
                  f"{digest['covers']}); a tier-4 run will pay the full comparison")
    else:
        print(f"parity digest: unavailable ({digest.get('reason', 'unknown')})")

    print(f"WRAP_TIER={tier}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
