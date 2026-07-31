#!/usr/bin/env python3
"""
Compare the GDScript rules against the Python reference.

The game and the balance simulator implement the same rules twice, in two languages.
That is a standing risk: if they drift, the game is not playing the level that was
graded and signed off, and nothing would announce it.

This runs both over every anchor x policy x difficulty and diffs the outcomes.
Floats are compared with a tolerance because the two runtimes accumulate rounding
differently; discrete results (won, waves cleared, lives, build) must match exactly.

    .venv/bin/python tools/test_parity.py
    .venv/bin/python tools/test_parity.py --anchor anchor-01 --verbose
    .venv/bin/python tools/test_parity.py --shard 0/4       # 1 of 4 cost-balanced slices
    .venv/bin/python tools/test_parity.py --force           # ignore a cached-clean digest
    .venv/bin/python tools/test_parity.py --json            # outcome rows, for scripting

PRC-05 — content-hash gating and cost-balanced sharding
---------------------------------------------------------
This is 1152 simulations through both rule implementations (24 anchors x 16 policies x
3 difficulties), ~594s measured, and it is the single most expensive thing in the gate.
Two independent problems, both addressed here:

**Gating.** Parity is a pure function of a small, fixed set of inputs (see
`parity_inputs_digest()`) — if none of them moved since the last clean run, the answer
cannot have moved either. A *full* run (no `--anchor`, no `--shard` — the entire 1152)
that finds no cached hit runs in full and, if it comes back clean, records its digest in
`.cache/parity.json`. The next full run hashes those same inputs again and, on a match,
skips the whole comparison — this must NEVER read as an ordinary pass: it prints
`parity cached — ...` on a line `tools/check.py`'s `check_rules_parity()` recognises and
reports as `status: skip, skipped_reason: "cached"`, the same loud-skip contract every
other check in this project already honours (a tier-excluded check, a `--no-window`
skip, a missing subsystem — never a silent pass).

Only a full run participates in the cache, in either direction: `--anchor` and `--shard`
runs never consult it (a partial slice has nothing to say about whether the *whole*
suite is still clean) and never write it (recording "clean" off 1/4 of the work would be
exactly the unfalsifiable cache the issue warns against). `--json` also bypasses a
cache-hit read — asking for the actual outcome rows means the cache's "trust me, nothing
changed" answer is not what was asked for.

**Sharding.** `run_python()` times each anchor's own slice of the suite (48 runs: 16
policies x 3 difficulties) and that wall-clock is the caller's honest proxy for that
anchor's total cost — anchor-24 (10 waves, the widest roster) costs far more than
anchor-01 (6 waves) on both sides of the comparison, and the ratio holds because both
engines are ticking the same simulation. Recorded to `tools/parity_costs.json`,
refreshed on every full run regardless of `--no-cache`/`--force` (a perf number, not a
correctness record — see `_save_costs()`). `--shard I/N` bin-packs the 24 anchors across
`N` shards by longest-processing-time-first over that table (falls back to an even
round-robin split when the table does not exist yet), so a shard's wall-clock tracks the
mean rather than whichever unlucky third drew anchor-24.

Sharding never reorders anything *inside* a run — a shard is a set of whole anchors, and
every (anchor, policy, difficulty) triple that anchor owns moves with it. The sim has no
RNG and no shared state, so which shard an anchor lands in cannot change its own outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sim.content import all_anchor_ids, load_anchor, load_enemies, load_towers  # noqa: E402
from sim.engine import DIFFICULTIES, Sim, standard_policies  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import lease  # noqa: E402  — scopes this run for tools/reap.py (PRC-07)
import toolpaths  # noqa: E402

## Generous: this project's own `rules parity` gate check gives the whole comparison
## (python side included) up to 1800s (`tools/check.py`'s PARITY_TIMEOUT) and measures
## ~594s in practice.
PARITY_LEASE_TTL_S = 1800.0
## LF-134: `run_godot()`'s own `subprocess.run` used to have no timeout at all — the
## lease's TTL only bounds when ANOTHER session may reap a wedged Godot, it does nothing
## for the run stuck waiting on it, and this is the longest-running thing in the gate, so
## a wedge here was also the most expensive one to sit through unbounded. Set a little
## BELOW `tools/check.py`'s own `PARITY_TIMEOUT` (also 1800s) and the lease TTL above, so
## a real wedge is caught here first, with a message naming which anchor (or "full run")
## stalled — the outer, `tools/check.py`-level timeout would only ever report a generic,
## unattributed `TIMED_OUT`.
GODOT_SUBPROCESS_TIMEOUT_S = 1700.0

LOAD_TOLERANCE_MW = 0.01

EXACT = ["won", "waves_cleared", "died_on_wave", "lives_left", "leaks", "spend", "built"]

# ─────────────────────────────────────────────────────────── PRC-05: gating ──

## Every file that can move a parity OUTCOME — nothing else. Deliberately excludes
## anything under scripts/ that is presentation (anchor_view.gd, iso.gd, hud.gd, ...),
## everything under tools/ and docs/, and every asset: none of those are read by either
## rule engine or by parity.gd's own harness. Getting this list too broad means the cache
## never earns its keep (a dialog-line commit re-runs 1152 sims); too narrow means a rule
## change ships uncompared, which is the one failure mode this whole file exists to
## prevent. `scripts/test/parity.gd` is in scope even though it contains no rule of its
## own: it is the GDScript harness comparing against, and a change to how it drives
## AnchorSim (a new policy, a different build order) changes what "the outcome" means
## just as surely as a change inside anchor_sim.gd itself.
DIGEST_FIXED_FILES = [
    "scripts/anchor_sim.gd",
    "sim/engine.py",
    "sim/content.py",
    "scripts/test/parity.gd",
]

CACHE_PATH = ROOT / ".cache" / "parity.json"
## Not `.godot/` — rebuilding that blanks the level for whoever is playing out of this
## same tree (LF-075, CLAUDE.md). Not committed: `.gitignore`'s existing broad `/.cache/`
## rule already covers this path, and a per-machine cache is the right default here
## anyway — the digest is meaningless without the exact working tree and Godot binary
## that produced it, both of which are properties of this machine, not of the repo.
COST_TABLE_PATH = ROOT / "tools" / "parity_costs.json"


def _tracked_data_files() -> list[str]:
    """Every git-tracked path under `data/`, as repo-relative strings, sorted.

    `git ls-files` rather than a raw walk — PRC-02's whole point, and LF-132 is the
    concrete cost of skipping it: a scratch anchor dropped in `data/anchors/` for a
    benchmark is not content until it is tracked, and a digest built from a raw glob
    would invalidate the cache (or worse, silently NOT invalidate it, if the scratch
    file sorts in such a way that an unrelated hash collision never surfaces) over a
    file nobody asked to be compared.

    Falls back to a plain walk only when this is not a git checkout at all — an export
    tarball has no `git ls-files` to ask, and a digest that cannot be computed at all
    would force every run to treat parity as never-cached, which is safe but slow rather
    than silently wrong.
    """
    r = subprocess.run(["git", "ls-files", "-z", "--", "data"],
                       capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        return sorted(str(p.relative_to(ROOT)) for p in (ROOT / "data").rglob("*")
                      if p.is_file())
    return sorted(p for p in r.stdout.split("\0") if p)


def _godot_version() -> str:
    """The resolved Godot binary's own version string, or a sentinel.

    A Godot upgrade is exactly the event that can move float behaviour without moving a
    byte of this repo (LF-105 measured real divergence between runtimes on several
    trig/log/pow ops) — skipping the check across an upgrade on the strength of an
    unchanged repo digest would be the worst possible cache hit. `--version` needs no
    project path and opens no window on any build, so this is a direct call rather than
    going through `toolpaths.godot_argv()`.
    """
    exe = toolpaths.godot()
    if exe is None:
        return "no-godot"
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
        return (r.stdout or r.stderr).strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def parity_inputs_digest() -> str:
    """SHA-256 over everything that can change a parity outcome.

    Hashed as `<relative path>\\0<contents>\\0` per file, over `DIGEST_FIXED_FILES` (the
    two rule engines plus their GDScript harness) and every tracked file under `data/`
    (every anchor, `towers.json`, `enemies.json`, the schemas — anything either rule
    engine or `sim/content.py` reads), followed by the resolved Godot binary's own
    `--version` string. Content, never mtimes: a fresh `git checkout` resets mtimes and
    would force a spurious re-run, and a touch-free edit over a network mount (rare, but
    possible) would produce a spurious hit — `git ls-files` plus `sha256` of bytes is
    immune to both.

    What is deliberately OUT of scope: everything presentation (`anchor_view.gd`,
    `iso.gd`, `hud.gd`, ...), everything under `tools/` and `docs/`, every asset. None of
    those are read by either rule engine or by `parity.gd`'s harness, so a hash this wide
    would never earn a skip — the exact failure mode PRD risk #6 is about.
    """
    h = hashlib.sha256()
    for rel in [*DIGEST_FIXED_FILES, *_tracked_data_files()]:
        h.update(rel.encode())
        h.update(b"\0")
        try:
            h.update((ROOT / rel).read_bytes())
        except OSError:
            h.update(b"<missing>")
        h.update(b"\0")
    h.update(b"godot-version\0")
    h.update(_godot_version().encode())
    return h.hexdigest()


def _git_head() -> str | None:
    r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                      cwd=str(ROOT))
    return r.stdout.strip() or None


def _load_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(digest: str, godot_path: str | None, n_runs: int) -> None:
    """Record a full run that just came back clean. `godot_path` is stored alongside the
    digest as the stale-cache footgun guard: a digest match with a DIFFERENT recorded
    Godot path (e.g. `$LF_GODOT` pointed somewhere else, or the preferred binary changed)
    forces a run even though the repo contents did not move — see `_cache_hit()`. Content
    is already covered by the digest itself (`_godot_version()` folds the version string
    in), so the path is only needed for this one extra guard, not for the hash.
    """
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "latticefall-parity-cache",
        "version": 1,
        "digest": digest,
        "godot_path": godot_path,
        "commit": _git_head(),
        "passed_at": datetime.now(timezone.utc).isoformat(),
        "runs": n_runs,
    }
    CACHE_PATH.write_text(json.dumps(doc, indent=2) + "\n")


def _cache_hit(cache: dict | None, digest: str) -> bool:
    if not cache or cache.get("digest") != digest:
        return False
    # Guard against the stale-cache footgun (PRC-05 risk note): a digest match with a
    # DIFFERENT recorded Godot binary path is not trustworthy — the binary is exactly
    # the thing this project has already seen diverge silently (LF-105: Linux vs Windows
    # Godot disagree on several trig/log ops), so treat a path change as a forced miss
    # even though nothing in the repo moved.
    return cache.get("godot_path") == toolpaths.godot()


# ────────────────────────────────────────────────────────── PRC-05: sharding ──

def _load_costs() -> dict[str, float]:
    if not COST_TABLE_PATH.exists():
        return {}
    try:
        doc = json.loads(COST_TABLE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: float(v) for k, v in doc.get("anchor_ms", {}).items()}


def _save_costs(costs: dict[str, float]) -> None:
    """Refreshed on every FULL run (`--anchor`/`--shard` see only a slice and would
    corrupt the picture for every anchor they did not touch), regardless of
    `--no-cache`/`--force` — this is a perf number the sharder consults, not a
    correctness record, so it does not share the digest cache's "only write on a proven
    clean run" rule. Costs are the python side's own per-anchor wall clock (see
    `run_python()`): it has no Godot boot overhead to blur the comparison, and both
    engines tick the same simulation, so the ratio between anchors tracks the real cost
    the sharder needs to balance even though the absolute number is not the GDScript
    side's own time.
    """
    doc = {
        "schema": "latticefall-parity-costs",
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source": "python-side per-anchor wall clock (48 runs/anchor); a proxy for total "
                  "cost, not the GDScript side's own time — see module docstring",
        "anchor_ms": {k: round(v, 1) for k, v in sorted(costs.items())},
    }
    COST_TABLE_PATH.write_text(json.dumps(doc, indent=2) + "\n")


def _parse_shard(spec: str) -> tuple[int, int]:
    try:
        i_s, n_s = spec.split("/")
        i, n = int(i_s), int(n_s)
    except ValueError as exc:
        raise SystemExit(f"--shard must be I/N, e.g. 0/4 (got {spec!r})") from exc
    if n < 1 or not (0 <= i < n):
        raise SystemExit(f"--shard {spec!r}: need 0 <= I < N and N >= 1")
    return i, n


def shard_ids(all_ids: list[str], shard: tuple[int, int],
              costs: dict[str, float]) -> list[str]:
    """The subset of `all_ids` assigned to shard `shard[0]` of `shard[1]`.

    Longest-processing-time-first bin-packing: sort anchors by recorded cost descending,
    assign each to whichever shard currently has the smallest running total. This is the
    classic LPT heuristic (never worse than 4/3 of the optimal makespan) and it is the
    right tool here specifically because the imbalance is real and large — anchor-24 has
    been measured at a double-digit multiple of anchor-01's cost, so a count-based split
    can hand one shard the single most expensive anchor plus an unlucky share of the
    rest while another gets off easy.

    Falls back to an even round-robin split over `all_ids` (unsorted, so it is at least
    stable and deterministic) when `costs` is empty — the table does not exist until a
    full run has produced one, and a fresh checkout must still be shardable on its very
    first run rather than erroring.

    An anchor `costs` has no entry for (new since the table was last refreshed) is
    priced at the mean of the anchors that DO have one — cheaper than assuming the
    costliest anchor, safer than assuming free, and it only ever affects anchors this
    table has not caught up to yet.

    Deterministic: ties in cost break on anchor id, and `min()` over Python's list order
    breaks a tie between equally-light shards by lowest shard index — the same input
    always produces the same partition, which is what makes the shard-equivalence proof
    (serial run vs. the union of `--shard 0/N .. (N-1)/N`) meaningful at all.
    """
    i, n = shard
    if not costs:
        return sorted(a for idx, a in enumerate(sorted(all_ids)) if idx % n == i)

    known = [costs[a] for a in all_ids if a in costs]
    mean_cost = (sum(known) / len(known)) if known else 0.0
    order = sorted(all_ids, key=lambda a: (-costs.get(a, mean_cost), a))

    totals = [0.0] * n
    bins: list[list[str]] = [[] for _ in range(n)]
    for aid in order:
        j = min(range(n), key=lambda k: (totals[k], k))
        bins[j].append(aid)
        totals[j] += costs.get(aid, mean_cost)
    return sorted(bins[i])


# ──────────────────────────────────────────────────────────────── the run ──

def _run_godot_one(anchor: str | None) -> list[dict]:
    # `--headless` never opens a window on any build, so `want_window=True` here just
    # means "don't bother wrapping in Xvfb" — there is nothing for it to hide.
    extra = ["--headless", "--script", "res://scripts/test/parity.gd"]
    if anchor:
        extra += ["--", "--anchor", anchor]
    cmd = toolpaths.godot_argv(ROOT, extra, want_window=True)
    # `want_window=True` above means this never goes through `xvfb-run` — a true
    # `--headless` script run, not a rendered capture — so an ordinary lease is enough;
    # this does not compete for tools/lease.py's bounded capture slots (LF-116).
    with lease.acquire("test-parity", cmd, ttl_s=PARITY_LEASE_TTL_S):
        try:
            # LF-134: this subprocess.run had NO timeout of its own. The lease's TTL only
            # bounds when ANOTHER session may reap a wedged Godot; it did nothing for
            # THIS run, which just sat here forever waiting on its own child — and this
            # is the single longest-running thing in the gate, so a wedge here was also
            # the most expensive one to sit through unbounded. `cmd[0]` is the Godot
            # binary directly (never wrapped, see above), so it is the direct child
            # `subprocess.run(timeout=...)` kills on expiry — no reparented grandchild to
            # chase the way `tools/check.py`'s `run()` has to for a wrapped capture.
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT),
                              timeout=GODOT_SUBPROCESS_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise SystemExit(
                f"godot wedged: no output after {GODOT_SUBPROCESS_TIMEOUT_S:.0f}s "
                f"({'anchor ' + anchor if anchor else 'full run'}). stdout tail:\n"
                f"{(exc.stdout or '')[-1500:]}") from exc
    for line in r.stdout.splitlines():
        if line.startswith("PARITY_JSON "):
            return json.loads(line[len("PARITY_JSON "):])
    raise SystemExit(
        "godot produced no parity output.\n"
        f"stdout tail:\n{r.stdout[-1500:]}\nstderr tail:\n{r.stderr[-1500:]}")


def run_godot(anchor_ids: list[str] | None) -> list[dict]:
    """Run `scripts/test/parity.gd`, once per anchor in `anchor_ids`, or once with no
    filter at all when `anchor_ids` is None.

    `parity.gd` only accepts a single `--anchor` value at a time, and changing that is out
    of scope here — it is owned by `scripts/**`, not this ticket — so a `--shard`/
    `--anchor` request that names more than one anchor costs one Godot cold start per
    anchor rather than the single amortised launch a full run gets. That is the honest
    price of sharding at anchor granularity without touching the GDScript harness; it is
    still a net win when the point of sharding is spreading those cold starts across
    several CI jobs rather than paying them all on one machine.
    """
    if anchor_ids is None:
        return _run_godot_one(None)
    out: list[dict] = []
    for aid in anchor_ids:
        out += _run_godot_one(aid)
    return out


def run_python(anchor_ids: list[str]) -> tuple[list[dict], dict[str, float]]:
    """The Python side of the comparison, plus each anchor's own wall-clock (ms) — the
    cost signal `shard_ids()` bin-packs on (see `_save_costs()` for why this side's time
    is the honest proxy rather than the GDScript side's own, which this function never
    measures)."""
    towers, enemies = load_towers(), load_enemies()
    out: list[dict] = []
    costs: dict[str, float] = {}
    for aid in anchor_ids:
        t0 = time.perf_counter()
        anchor = load_anchor(aid)
        available = sorted(t.id for t in towers.values() if t.unlocked_at <= aid)
        for policy in standard_policies(available):
            for diff in DIFFICULTIES:
                o = Sim(anchor, towers, enemies, policy, diff).run()
                out.append({
                    "anchor": aid, "difficulty": diff, "policy": policy.name,
                    "won": o.won, "waves_cleared": o.waves_cleared,
                    "died_on_wave": o.died_on_wave, "lives_left": o.lives_left,
                    "leaks": o.leaks, "peak_load_mw": round(o.peak_load_mw, 3),
                    "spend": o.spend, "built": o.built,
                })
        costs[aid] = (time.perf_counter() - t0) * 1000.0
    return out, costs


def key(r: dict) -> tuple:
    return (r["anchor"], r["policy"], r["difficulty"])


def main() -> int:
    ap = argparse.ArgumentParser(description="GDScript vs Python rules parity.")
    ap.add_argument("--anchor")
    ap.add_argument("--shard", metavar="I/N",
                    help="run only this cost-balanced slice of the anchor list, e.g. "
                         "0/4 for the first of four (PRC-05). Never reads or writes the "
                         "digest cache — a slice has nothing to say about whether the "
                         "WHOLE suite is clean.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true",
                    help="print the compared outcome rows as a JSON array to stdout "
                         "instead of the human summary, and nothing else — used to prove "
                         "shard equivalence (a serial run's rows must equal the union of "
                         "every --shard I/N's rows). Bypasses a cache-hit read: asking "
                         "for the actual rows means the cache's skip answer is not what "
                         "was asked for.")
    ap.add_argument("--force", action="store_true",
                    help="ignore a cached-clean digest and run anyway. A full run that "
                         "comes back clean still updates the cache afterward, same as an "
                         "ordinary cache miss would.")
    ap.add_argument("--no-cache", action="store_true",
                    help="never read OR write the digest cache this run. For an "
                         "experimental run whose result should not be trusted by the "
                         "next invocation — --anchor and --shard already never touch the "
                         "cache regardless of this flag.")
    args = ap.parse_args()

    if toolpaths.godot() is None:
        print("godot not found on this machine — skipping parity", file=sys.stderr)
        return 0

    all_ids = all_anchor_ids()
    shard = _parse_shard(args.shard) if args.shard else None
    is_full_run = args.anchor is None and shard is None

    if args.anchor:
        ids = [args.anchor]
    elif shard:
        ids = shard_ids(all_ids, shard, _load_costs())
    else:
        ids = all_ids

    # Cache read: only a full run consults it, and --json/--force/--no-cache all mean
    # "run it anyway" for their own distinct reasons (see each flag's help text).
    digest = None
    if is_full_run and not args.json and not args.force and not args.no_cache:
        digest = parity_inputs_digest()
        cache = _load_cache()
        if _cache_hit(cache, digest):
            print(f"parity cached — skipping {cache.get('runs', '?')} runs (digest "
                  f"{digest[:12]}, last verified {cache.get('passed_at', '?')} at commit "
                  f"{(cache.get('commit') or '?')[:12]})")
            return 0

    # The two sides are independent, take roughly the same wall-clock, and neither
    # writes anything — so running them at once halves the gate's slowest check.
    with ThreadPoolExecutor(max_workers=2) as pool:
        gd_future = pool.submit(run_godot, None if is_full_run else ids)
        py_future = pool.submit(run_python, ids)
        gd = {key(r): r for r in gd_future.result()}
        py_rows, costs = py_future.result()
        py = {key(r): r for r in py_rows}

    if is_full_run:
        # Refreshed on every full run regardless of pass/fail — a perf number, not a
        # correctness record (see _save_costs()).
        _save_costs(costs)

    missing = sorted(set(py) - set(gd))
    extra = sorted(set(gd) - set(py))
    diffs: list[str] = []

    for k in sorted(set(py) & set(gd)):
        a, b = py[k], gd[k]
        label = f"{k[0]} {k[1]:<16s} {k[2]:<9s}"
        for f in EXACT:
            if a[f] != b[f]:
                diffs.append(f"{label}  {f}: python={a[f]!r} godot={b[f]!r}")
        if abs(a["peak_load_mw"] - b["peak_load_mw"]) > LOAD_TOLERANCE_MW:
            diffs.append(f"{label}  peak_load_mw: python={a['peak_load_mw']} "
                         f"godot={b['peak_load_mw']}")
        if args.verbose and not diffs:
            print(f"  ok  {label}  {'won' if a['won'] else 'lost'} "
                  f"w{a['waves_cleared']} lives {a['lives_left']}")

    for k in missing:
        diffs.append(f"{k}: present in python, absent from godot")
    for k in extra:
        diffs.append(f"{k}: present in godot, absent from python")

    n = len(set(py) & set(gd))

    if args.json:
        # Every other message this run could print goes to stderr instead — a caller
        # asking for --json wants stdout to be exactly one JSON array, nothing else
        # (this is what tools/test_parity.py --shard I/N's equivalence proof parses).
        print(json.dumps([gd[k] for k in sorted(gd)]))
        if diffs:
            print(f"PARITY FAILED — {len(diffs)} difference(s) across {n} run(s)",
                  file=sys.stderr)
            return 1
        return 0

    if diffs:
        print(f"PARITY FAILED — {len(diffs)} difference(s) across {n} run(s)",
              file=sys.stderr)
        for d in diffs[:40]:
            print(f"  {d}", file=sys.stderr)
        if len(diffs) > 40:
            print(f"  ... and {len(diffs) - 40} more", file=sys.stderr)
        return 1

    if is_full_run and not args.no_cache:
        _save_cache(digest or parity_inputs_digest(), toolpaths.godot(), n)

    scope = f" [shard {args.shard}]" if shard else (f" [{args.anchor}]" if args.anchor else "")
    print(f"parity ok — {n} runs identical (gdscript vs python){scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
