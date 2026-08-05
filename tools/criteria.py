#!/usr/bin/env python3
"""
Print `BAL-04`'s acceptance-criteria table against the baselines it is judged by.

    .venv/bin/python tools/criteria.py --jobs 8                # grade, then judge
    .venv/bin/python tools/criteria.py --grade /tmp/grade.json # judge a saved artefact
    .venv/bin/python tools/criteria.py --json                  # machine-readable
    .venv/bin/python tools/criteria.py --rebaseline            # move the bar, deliberately
    .venv/bin/python tools/criteria.py --selftest              # drive every comparator, red
                                                               # and green, in ~0.2 s

## WHY THIS FILE EXISTS

The same five computations had been hand-rolled in a scratch script **five times** —
decisions 088, 089, 090, 091 and 093 each recomputed multi-weapon coverage and per-anchor
`standard + brutal` out of `sim/run.py`'s JSON by hand, and each one had to rediscover the
same three traps:

- **A `built` entry is `"<tower-id>@<x>,<y>"`.** A membership test against bare tower ids
  matches *nothing* and reports a confident **zero**. It does not raise, it lies. That is
  what `tools/density.py`'s `build_tower_ids()` and `weapon_ids_in_build()` exist for
  (LF-270), and until this file they had no caller — an uncalled helper is a claim nobody
  checks, which is LF-278.
- **`sim/run.py --json` drops every report's `runs` unless `--detail` is also passed**
  (LF-258). A per-run statistic computed off such an artefact gets nothing and says nothing.
  So the multi-weapon criterion below **fails loudly** on an artefact with no `runs` rather
  than reporting 0 of 22.
- **"Achievable" means at least two *weapon* ids unlocked** — `damage > 0`, `Tower.is_weapon`.
  anchor-01 and anchor-02 unlock exactly one weapon, so the multi-weapon criterion is
  impossible there by construction. That is the whole difference between **22 of 22
  achievable** and 22 of 24, and getting it wrong makes two anchors permanent failures — the
  exact error decision 088 had to correct.

## WHY IT IS A TOOL AND NOT MORE FIELDS ON `sim/run.py --json`

`sim/run.py` is an instrument several other workstreams read, and its report is *per anchor*.
Four of the six criteria here are not: three compare the whole campaign against a **stored
baseline artefact**, and one (`count: 1` share) is computed from `data/anchors/` and does not
touch the grade at all. Putting them in the grader would make every consumer of `--json`
carry BAL-04's specification and a baseline file it never asked for, and would give
`sim determinism` — which diffs two grade runs byte for byte — a second thing to be
sensitive to. The grader answers "is this anchor sound"; this answers "did the campaign get
worse than the day we wrote the bar down", and those are different questions with different
inputs. Consuming the grade rather than extending it also means this file can grade
in-process (`--jobs`), which is how it sidesteps the `--detail` trap above entirely.

## WHERE THE BAR LIVES, AND HOW IT MOVES

`tools/bal04_baseline.json` — a tracked artefact next to `tools/parity_costs.json`, which is
the same idea for a different measurement. Nothing in this file hardcodes a per-anchor
number, because decision 088's finding is precisely that the bar is **per anchor and
measured**, not a flat constant: read as a flat floor, `ROBUST_ENOUGH = 8` fails on 8 of 24
anchors today.

`--rebaseline` rewrites it. It writes improvements without ceremony and **refuses to write a
regression** unless `--accept-regressions` is passed and names each one it would bake in.
That is the deliberate part: a baseline you can move by editing a constant in a source file
is a baseline that moves when someone is tired.

## THE SIX CRITERIA, AND THE ONE THAT IS DELEGATED

1. every anchor grades `ok`, at every difficulty            — from the grade
2. per-anchor `standard + brutal` distinct winning builds   — from the grade, vs baseline
3. multi-weapon winning build where achievable              — from the grade's `runs`
4. `capacity_mw` within the PLC-05 saturation bound         — **delegated**, see below
5. per-act share of spawn entries at `count: 1`             — from `data/anchors/`
6. campaign win share falls strictly + decision 086's rule  — from the grade

**4 is delegated to `tools/validate/validate_data.py` on purpose.** The saturation
denominator already has three copies in this repository (`validate_data.py`,
`tools/sweep.py`, `tools/density.py`'s `_tower_max_draw`), each carrying a comment telling
the next person to move the other two. A fourth copy here would be a fourth thing to drift,
and the criterion as BAL-04 writes it *is* that tool's verdict: `ok — 0 warning(s)`. So this
runs it and reports what it said. The measured headroom (max `sat_frac` over the campaign,
via `density.saturation_stats`) is printed beside it as context and is **not** asserted.

Three of BAL-04's remaining acceptance bullets are deliberately **not** here: `wave density`,
`dialog capacity` and `rules parity` are gate checks in `tools/check.py` that already fail
loudly on their own, and a second, weaker copy of a check that exists is worse than no copy.
This file covers what nothing else does.

## WHAT `--selftest` COVERS AND WHAT IT CANNOT

Every comparator is a pure function of a grade table and a baseline dict, so `--selftest`
drives all of them red *and* green over synthetic reports in milliseconds, with no content
load and no Sim — the same argument that puts `sim/run.py --selftest` at tier 1. It also
pins the two `density.py` helpers against the `@` trap directly, because a green comparator
over a helper that silently returns the empty set is the failure this whole file is about.
Criterion 4 has no pure part to drive (it is a subprocess), and is the one thing here the
selftest says nothing about.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from sim.content import Tower, all_anchor_ids, load_anchor, load_towers  # noqa: E402
from sim.engine import DIFFICULTIES  # noqa: E402
from sim.run import campaign_win_share, grade_all  # noqa: E402

from density import saturation_stats, weapon_ids, weapon_ids_in_build  # noqa: E402

BASELINE_PATH = ROOT / "tools" / "bal04_baseline.json"
BASELINE_SCHEMA = "latticefall-bal04-baseline"
BASELINE_VERSION = 1

OK, FAIL = "ok", "FAIL"

## Read off DIFFICULTIES' own order rather than spelled out, exactly as sim/run.py does: a
## fourth tier must be a data change and not a second place to remember.
BASE_DIFFICULTY = list(DIFFICULTIES)[0]
TOP_DIFFICULTY = list(DIFFICULTIES)[-1]


@dataclass
class Criterion:
    """One acceptance bullet: its verdict, the line that summarises it, the detail lines
    under it, and the measurement in the shape `--rebaseline` stores."""

    key: str
    title: str
    status: str
    headline: str
    lines: list[str] = field(default_factory=list)
    measured: dict = field(default_factory=dict)
    ## Names of the things that got WORSE than the stored baseline — anchors, acts. Kept
    ## apart from `status`, because a criterion also fails when the baseline simply has no
    ## entry for an anchor (bootstrapping, or a new anchor), and that is not a regression.
    ## `--rebaseline` refuses on this list and not on the status, so a first run can write
    ## the file while a real regression still needs `--accept-regressions`.
    regressions: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == OK


# ────────────────────────────────────────────────────────────── measurement ──

def anchor_weapon_roster(report: dict, towers: dict[str, Tower]) -> set[str]:
    """The distinct weapon ids unlocked at or before this anchor.

    `report["unlocked"]` is the grader's own `available` list — bare ids, already filtered
    by `unlocked_at <= anchor.id` — so this is the criterion's *denominator* and nothing
    else. Routed through `density.weapon_ids()` rather than filtering here, because that
    predicate having exactly one definition is the point of LF-270/LF-278.
    """
    return weapon_ids(list(report["unlocked"]), towers)


def multi_weapon_wins(report: dict, towers: dict[str, Tower]) -> list[dict]:
    """Every winning run on this anchor whose build contains more than one weapon id.

    Any difficulty counts. BAL-04's bullet says "keeps at least one winning build
    containing more than one weapon id" and conditions on nothing else; measured at
    `a4702df` the standard-only reading gives the identical 22 of 22, so the looser wording
    is not currently hiding anything, and that equivalence is worth re-checking rather than
    assuming if this ever disagrees with the record.
    """
    out = []
    for o in report.get("runs", []):
        if not o.get("won"):
            continue
        ids = weapon_ids_in_build(o["built"], towers)
        if len(ids) > 1:
            out.append({"difficulty": o["difficulty"], "policy": o["policy"],
                        "weapons": sorted(ids)})
    return out


def count_one_shares(anchor_ids: list[str] | None = None) -> dict[int, dict[str, int]]:
    """Per act: how many spawn entries in `data/anchors/` carry `count: 1`, out of how many.

    Kept as the two integers rather than the ratio, so the comparison against baseline is
    exact rather than a float within epsilon. Act 1 at `a4702df` is 15 of 191 = **7.853%**,
    which is the 7.9% decision 091 records; a baseline storing that rounded figure would
    admit anything below 7.9%, and 16 of 203 — a real rise, at 7.882% — is below 7.9%. The
    selftest carries exactly that pair.
    """
    acts: dict[int, dict[str, int]] = {}
    for aid in (anchor_ids or all_anchor_ids()):
        a = load_anchor(aid)
        row = acts.setdefault(a.act, {"count_one": 0, "entries": 0})
        for w in a.waves:
            for s in w.spawns:
                row["entries"] += 1
                if s.count == 1:
                    row["count_one"] += 1
    return acts


def _share_rose(measured: dict[str, int], base: dict[str, int]) -> bool:
    """measured/entries > base/entries, by integer cross-multiplication — no float, no
    epsilon, no rounding that could hide a rise of a tenth of a point."""
    return measured["count_one"] * base["entries"] > base["count_one"] * measured["entries"]


# ────────────────────────────────────────────────────────────── comparators ──

def criterion_all_ok(reports: list[dict], baseline: dict, diffs: list[str]) -> Criterion:
    """All 24 anchors grade `ok`, at all three difficulties. Baseline 24/24.

    Also refuses a *partial* grade. A subset of the campaign cannot satisfy a criterion
    about the campaign, and a tool that greens on `--anchor anchor-01` is a tool that will
    eventually be run that way by accident and believed.
    """
    want_n = int(baseline["anchors"])
    want_diffs = list(baseline["difficulties"])
    bad = [r["anchor"] for r in reports if not r["ok"]]
    lines, problems = [], []
    if len(reports) < want_n:
        problems.append(f"graded {len(reports)} of {want_n} anchors — a subset cannot "
                        f"satisfy a campaign criterion")
    missing = [d for d in want_diffs if d not in diffs]
    if missing:
        problems.append(f"graded without {', '.join(missing)} — the verdict is weaker than "
                        f"the baseline's")
    for r in reports:
        if not r["ok"]:
            lines.append(f"  {r['anchor']}: {'; '.join(r['problems'])}")
    if bad:
        problems.append(f"{len(bad)} anchor(s) not ok: {', '.join(bad)}")
    head = (f"{len(reports) - len(bad)}/{len(reports)} anchors ok at "
            f"{len(diffs)} difficulties (baseline {want_n}/{want_n} at {len(want_diffs)})")
    return Criterion("all_ok", "All anchors grade ok", FAIL if problems else OK,
                     head, lines + [f"  {p}" for p in problems],
                     {"anchors": len(reports), "difficulties": diffs})


def criterion_standard_plus_brutal(reports: list[dict], baseline: dict) -> Criterion:
    """No anchor's `standard + brutal` distinct winning builds falls below its own baseline.

    Per anchor, never a flat number. Decision 088: read as the flat floor `ROBUST_ENOUGH =
    8`, this criterion fails on 8 of 24 anchors on a campaign that is otherwise 24/24 `ok`.
    """
    base = baseline["standard_plus_brutal"]
    measured, worse, better, unknown, fell = {}, [], [], [], []
    for r in reports:
        bd = r["by_difficulty"]
        if BASE_DIFFICULTY not in bd or TOP_DIFFICULTY not in bd:
            unknown.append(r["anchor"])
            continue
        v = (bd[BASE_DIFFICULTY]["distinct_winning_builds"]
             + bd[TOP_DIFFICULTY]["distinct_winning_builds"])
        measured[r["anchor"]] = v
        b = base.get(r["anchor"])
        if b is None:
            unknown.append(r["anchor"])
        elif v < b:
            fell.append(r["anchor"])
            worse.append(f"  {r['anchor']}: {v} against a baseline of {b}  (−{b - v})")
        elif v > b:
            better.append(f"  {r['anchor']}: {v} against a baseline of {b}  (+{v - b})")
    lines = list(worse)
    if unknown:
        lines.append(f"  no baseline for: {', '.join(unknown)}")
    lines.extend(better)
    total, base_total = sum(measured.values()), sum(base.get(a, 0) for a in measured)
    head = (f"{len(worse)} anchor(s) below baseline, {len(better)} above  ·  "
            f"campaign total {total} against {base_total}")
    return Criterion("standard_plus_brutal",
                     f"Per-anchor {BASE_DIFFICULTY} + {TOP_DIFFICULTY} winning builds",
                     FAIL if (worse or unknown) else OK, head, lines, measured, fell)


def criterion_multi_weapon(reports: list[dict], baseline: dict,
                           towers: dict[str, Tower]) -> Criterion:
    """Every anchor with two or more weapon ids unlocked keeps at least one winning build
    containing more than one weapon id. Baseline 22 of the 22 anchors where it is
    achievable — **not** 22 of 24, and the two denominators stay apart.
    """
    base_met = set(baseline["multi_weapon"]["met"])
    base_ach = set(baseline["multi_weapon"]["achievable"])
    if any("runs" not in r for r in reports):
        return Criterion(
            "multi_weapon", "Multi-weapon winning build where achievable", FAIL,
            "cannot be computed: the grade artefact carries no per-run `built` lists",
            ["  `sim/run.py --json` drops `runs` unless `--detail` is also passed (LF-258).",
             "  Re-grade with `--detail`, or let this tool grade in-process (drop --grade).",
             "  Reported as a failure rather than as 0 of 22, which is what a silent read",
             "  of a missing key would have produced."], {})

    achievable, met, misses, newly = [], [], [], []
    for r in reports:
        roster = anchor_weapon_roster(r, towers)
        if len(roster) < 2:
            continue
        achievable.append(r["anchor"])
        wins = multi_weapon_wins(r, towers)
        if wins:
            met.append(r["anchor"])
        else:
            misses.append(r["anchor"])
            if r["anchor"] not in base_met:
                newly.append(r["anchor"])
    regressions = [a for a in misses if a in base_met]
    lines = []
    for a in misses:
        tag = "REGRESSION" if a in base_met else ("not met at baseline either"
                                                  if a in base_ach else "newly achievable")
        lines.append(f"  {a}: no winning build with two weapon classes  [{tag}]")
    for a in achievable:
        if a not in base_ach:
            lines.append(f"  {a}: newly achievable (its weapon roster grew)")
    excluded = [r["anchor"] for r in reports
                if len(anchor_weapon_roster(r, towers)) < 2]
    lines.append(f"  excluded, fewer than two weapons unlocked: "
                 f"{', '.join(excluded) if excluded else 'none'}")
    head = (f"{len(met)}/{len(achievable)} achievable anchors "
            f"({len(met)}/{len(reports)} unconditioned)  ·  baseline "
            f"{len(base_met)}/{len(base_ach)}")
    return Criterion("multi_weapon", "Multi-weapon winning build where achievable",
                     FAIL if regressions else OK, head, lines,
                     {"achievable": achievable, "met": met}, regressions)


def criterion_saturation(anchor_ids: list[str] | None = None) -> Criterion:
    """`capacity_mw` within the PLC-05 saturation bound. Baseline `validate_data.py` →
    `ok — 0 warning(s)`. Delegated rather than recomputed; see the module docstring."""
    val = ROOT / "tools" / "validate" / "validate_data.py"
    if not val.exists():
        return Criterion("saturation", "capacity_mw within the saturation bound", FAIL,
                         f"cannot be computed: {val} is missing", [], {})
    r = subprocess.run([sys.executable, str(val)], cwd=ROOT,
                       capture_output=True, text=True, timeout=300)
    out = r.stdout.strip().splitlines() or ["(no output)"]
    tail = out[-1]
    lines = [f"  tools/validate/validate_data.py exit {r.returncode}: {tail}"]
    ## `validate_data.py` writes its errors and warnings to **stderr** and its tally to
    ## stdout, so on a failing run the last stdout line is "58 documents against 7 schemas"
    ## and quoting only that reports the tally as though it were the problem. Measured while
    ## proving this criterion red at 110% of anchor-24's saturation denominator, where the
    ## actual sentence — "no power decision exists on this anchor" — was nowhere in the
    ## output. Carry stderr whenever there is any; it is where both severities live.
    ## Warnings, though, go to *stdout* as "  warn  ..." — the two severities are split
    ## across the two streams, so both are carried or the 80–99% band reports "1 warning(s)"
    ## and never says which anchor.
    notes = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("warn")]
    notes += [ln.strip() for ln in r.stderr.strip().splitlines() if ln.strip()]
    lines.extend(f"  {ln}" for ln in notes[-8:])
    if r.returncode != 0:
        tail = f"validate_data.py exited {r.returncode} — see below"
    towers = load_towers()
    worst, worst_id = 0.0, ""
    for aid in (anchor_ids or all_anchor_ids()):
        frac = float(saturation_stats(load_anchor(aid), towers)["sat_frac"])
        if frac > worst:
            worst, worst_id = frac, aid
    lines.append(f"  headroom, reported not asserted: worst anchor is {worst_id} at "
                 f"{worst:.0%} of board saturation (warns at 80%, errors at 100%)")
    bad = r.returncode != 0 or "0 warning(s)" not in tail
    return Criterion("saturation", "capacity_mw within the PLC-05 saturation bound",
                     FAIL if bad else OK, tail, lines,
                     {"exit": r.returncode, "verdict": tail,
                      "worst_sat_frac": round(worst, 4), "worst_anchor": worst_id})


def criterion_count_one(baseline: dict, anchor_ids: list[str] | None = None) -> Criterion:
    """No act's share of spawn entries at `count: 1` rises above its baseline.

    A regression bound, not an absolute: decision 088 measured the old "no act above 10%"
    wording as failed by all three acts on the day it was written. The failure it guards is
    real — 251 of 252 Act III spawn entries once came out at `count: 1` and every wave
    collapsed to "N shards and one of each". Computed from `data/anchors/`, so it needs no
    grade at all.
    """
    base = {int(k): v for k, v in baseline["count_one"].items()}
    measured = count_one_shares(anchor_ids)
    lines, rose, unknown = [], [], []
    for act in sorted(measured):
        m = measured[act]
        share = m["count_one"] / m["entries"] if m["entries"] else 0.0
        b = base.get(act)
        if b is None:
            unknown.append(act)
            lines.append(f"  act {act}: {m['count_one']}/{m['entries']} = {share:.1%}  "
                         f"[no baseline]")
            continue
        bshare = b["count_one"] / b["entries"] if b["entries"] else 0.0
        up = _share_rose(m, b)
        if up:
            rose.append(act)
        lines.append(f"  act {act}: {m['count_one']}/{m['entries']} = {share:.1%}  "
                     f"against {b['count_one']}/{b['entries']} = {bshare:.1%}  "
                     f"{'ROSE' if up else 'ok'}")
    head = ("  ·  ".join(f"act {a} {measured[a]['count_one'] / measured[a]['entries']:.1%}"
                         for a in sorted(measured)))
    return Criterion("count_one", "Per-act share of spawn entries at count: 1",
                     FAIL if (rose or unknown) else OK, head, lines,
                     {str(a): measured[a] for a in sorted(measured)},
                     [f"act {a}" for a in rose])


def criterion_win_share(reports: list[dict], baseline: dict,
                        diffs: list[str]) -> Criterion:
    """Campaign win share falls strictly across the tiers, and decision 086's per-anchor
    rule holds: the top tier's share is strictly below the bottom tier's on every
    non-tutorial anchor.

    The per-anchor half duplicates nothing — `verdict()` already asserts it, so criterion 1
    covers it — but it is named here because BAL-04's bullet is compound, and a criterion
    whose two halves live in two places is a criterion someone will half-check. The
    campaign shares themselves are **reported against baseline, not asserted**: decision 086
    measured them falling strictly on both the shipped campaign and the derived-range one it
    refused, so a bound on the value would be a number that has already been shown not to
    discriminate.
    """
    cws = campaign_win_share(reports, diffs)
    base = baseline.get("campaign_win_share", {})
    lines = []
    for d in diffs:
        c = cws["by_difficulty"][d]
        b = base.get(d)
        drift = ""
        if b and b.get("tried"):
            drift = (f"  against baseline {b['won']}/{b['tried']} = "
                     f"{b['won'] / b['tried']:.1%}")
        lines.append(f"  {d:<9s} {c['won']:>3d}/{c['tried']:<3d} = {c['win_share']:.1%}"
                     f"{drift}")
    problems = []
    if not cws["falls_strictly"]:
        problems.append("campaign win share does not fall strictly across the tiers")
    offenders = cws["not_falling"].get(TOP_DIFFICULTY, [])
    if offenders:
        problems.append(f"{TOP_DIFFICULTY} does not fall below {BASE_DIFFICULTY} on "
                        f"{len(offenders)} non-tutorial anchor(s): {', '.join(offenders)}")
    for d, names in cws["not_falling"].items():
        if names and d != TOP_DIFFICULTY:
            lines.append(f"  {d} does not fall below {BASE_DIFFICULTY} on "
                         f"{len(names)} anchor(s): {', '.join(names)}   "
                         f"[reported, not asserted]")
    lines.extend(f"  {p}" for p in problems)
    head = ("falls strictly: " + ("yes" if cws["falls_strictly"] else "NO")
            + "  ·  " + " / ".join(f"{cws['by_difficulty'][d]['win_share']:.1%}"
                                   for d in diffs))
    return Criterion("win_share", "Campaign win share falls, and decision 086's rule holds",
                     FAIL if problems else OK, head, lines,
                     {d: {"won": cws["by_difficulty"][d]["won"],
                          "tried": cws["by_difficulty"][d]["tried"]} for d in diffs})


def evaluate(reports: list[dict], baseline: dict, diffs: list[str],
             towers: dict[str, Tower] | None = None) -> list[Criterion]:
    """Every criterion, in BAL-04's own order."""
    towers = towers if towers is not None else load_towers()
    return [criterion_all_ok(reports, baseline, diffs),
            criterion_standard_plus_brutal(reports, baseline),
            criterion_multi_weapon(reports, baseline, towers),
            criterion_saturation(),
            criterion_count_one(baseline),
            criterion_win_share(reports, baseline, diffs)]


# ──────────────────────────────────────────────────────────────── baselines ──

## What a baseline looks like before there is one. `--rebaseline` uses it to bootstrap the
## artefact; every comparator then reports "no baseline for ..." and FAILS, which is right —
## a campaign cannot be said to meet a bar nobody has recorded — while leaving `regressions`
## empty, which is what lets the first write through without --accept-regressions.
EMPTY_BASELINE: dict = {
    "schema": BASELINE_SCHEMA, "version": BASELINE_VERSION, "commit": "(none yet)",
    "recorded_at": "", "anchors": 0, "difficulties": [], "standard_plus_brutal": {},
    "multi_weapon": {"achievable": [], "met": []}, "count_one": {},
    "campaign_win_share": {},
}


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    doc = json.loads(path.read_text())
    if doc.get("schema") != BASELINE_SCHEMA:
        raise SystemExit(f"{path}: schema is {doc.get('schema')!r}, "
                         f"expected {BASELINE_SCHEMA!r}")
    return doc


def baseline_from(criteria: list[Criterion], commit: str) -> dict:
    by = {c.key: c.measured for c in criteria}
    return {
        "schema": BASELINE_SCHEMA,
        "version": BASELINE_VERSION,
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "commit": commit,
        "source": ("tools/criteria.py --rebaseline; grade from sim/run.py at the commit "
                   "above. Every figure here is BAL-04's measured bar, per decision 088: "
                   "an acceptance criterion carries the baseline it is judged against."),
        "anchors": by.get("all_ok", {}).get("anchors", 0),
        "difficulties": by.get("all_ok", {}).get("difficulties", []),
        "standard_plus_brutal": by.get("standard_plus_brutal", {}),
        "multi_weapon": by.get("multi_weapon", {}),
        "count_one": by.get("count_one", {}),
        "campaign_win_share": by.get("win_share", {}),
    }


def _git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ───────────────────────────────────────────────────────────────── printing ──

def print_table(criteria: list[Criterion], baseline: dict, verbose: bool) -> None:
    print(f"BAL-04 acceptance criteria  ·  baseline {BASELINE_PATH.name} recorded at "
          f"{baseline.get('commit', '?')} on {baseline.get('recorded_at', '?')[:19]}")
    print("─" * 78)
    for c in criteria:
        print(f"{c.status:>4s}  {c.title}")
        print(f"      {c.headline}")
        if c.lines and (verbose or not c.ok):
            for line in c.lines:
                print(f"    {line}")
    bad = [c for c in criteria if not c.ok]
    print("─" * 78)
    print(f"{len(criteria) - len(bad)}/{len(criteria)} criteria met"
          + (f"  ·  FAILED: {', '.join(c.key for c in bad)}" if bad else ""))


# ───────────────────────────────────────────────────────────────── selftest ──

def _cell(won: int, tried: int) -> dict:
    return {"win_count": won, "distinct_winning_builds": won,
            "distinct_builds_tried": tried,
            "win_share": (won / tried) if tried else 0.0}


def _report(anchor: str, unlocked: list[str], builds: dict[str, list[list[str]]],
            cells: dict[str, tuple[int, int]], ok: bool = True,
            tutorial: bool = False, with_runs: bool = True) -> dict:
    """A grade report with only the keys the comparators read."""
    runs = [{"difficulty": d, "policy": f"p{i}", "won": True, "built": b}
            for d, bs in builds.items() for i, b in enumerate(bs)]
    r = {"anchor": anchor, "tutorial": tutorial, "ok": ok,
         "problems": [] if ok else ["synthetic problem"],
         "unlocked": unlocked,
         "by_difficulty": {d: _cell(*v) for d, v in cells.items()}}
    if with_runs:
        r["runs"] = runs
    return r


def selftest() -> int:
    """Drive every comparator red and green over synthetic grades. No content, no Sim.

    The red paths are the point. Run against the shipped campaign this tool prints six
    `ok`s, which is exactly the state in which a comparator that can only say `ok` is
    indistinguishable from one that works — LF-229 and decision 078's lesson, and the
    reason `sim/run.py` grew a selftest of its own.
    """
    base_d, top_d = BASE_DIFFICULTY, TOP_DIFFICULTY
    diffs = [base_d, top_d]
    tally = {"red": 0, "green": 0, "structural": 0}

    def expect(c: Criterion, want_ok: bool, needle: str = "") -> None:
        assert c.ok == want_ok, f"{c.key}: wanted ok={want_ok}, got {c.status} — {c.headline}"
        if needle:
            blob = c.headline + "\n" + "\n".join(c.lines)
            assert needle in blob, f"{c.key}: {needle!r} not in:\n{blob}"
        tally["green" if want_ok else "red"] += 1

    # 0. STRUCTURAL. The `@` trap itself, pinned against tools/density.py rather than
    #    trusted. A membership test against bare ids answers zero without raising, which is
    #    LF-270's whole reason for existing, so the helper is asserted here directly — if
    #    it ever regressed, every criterion below would still be green.
    towers = {"pulse-turret": Tower("pulse-turret", "Pulse", 100, 12, 9, 4, .75, frozenset()),
              "ion-lance": Tower("ion-lance", "Lance", 220, 30, 26, 6, 1.2, frozenset()),
              "scan-relay": Tower("scan-relay", "Relay", 80, 8, 0, 5, 1.0, frozenset())}
    built = ["pulse-turret@3,4", "scan-relay@5,5", "ion-lance@7,2"]
    assert weapon_ids_in_build(built, towers) == {"pulse-turret", "ion-lance"}, \
        "weapon_ids_in_build lost the @-suffix split, or stopped excluding support"
    assert not [b for b in built if b in towers], \
        "fixture is wrong: the naive membership test must match nothing here"
    assert weapon_ids(["pulse-turret", "scan-relay"], towers) == {"pulse-turret"}
    tally["structural"] += 3

    one_weapon = ["pulse-turret", "scan-relay"]
    two_weapon = ["pulse-turret", "ion-lance", "scan-relay"]
    mixed = [["pulse-turret@1,1", "ion-lance@2,2"]]
    single = [["pulse-turret@1,1", "scan-relay@2,2"]]

    # 1. all_ok, green then red — and the two ways a *partial* grade must not read as green.
    bl = {"anchors": 2, "difficulties": diffs}
    good = [_report("anchor-01", two_weapon, {base_d: mixed}, {base_d: (2, 5), top_d: (1, 5)}),
            _report("anchor-02", two_weapon, {base_d: mixed}, {base_d: (3, 6), top_d: (1, 6)})]
    expect(criterion_all_ok(good, bl, diffs), True)
    broken = [dict(good[0], ok=False, problems=["brutal: unwinnable"]), good[1]]
    expect(criterion_all_ok(broken, bl, diffs), False, "brutal: unwinnable")
    expect(criterion_all_ok(good[:1], bl, diffs), False, "a subset cannot")
    expect(criterion_all_ok(good, bl, [base_d]), False, "graded without")

    # 2. standard + brutal, per anchor. Equal passes, up passes, down by one fails — and an
    #    anchor with no baseline entry is a failure rather than an unremarked pass.
    bl2 = {"standard_plus_brutal": {"anchor-01": 3, "anchor-02": 4}}
    expect(criterion_standard_plus_brutal(good, bl2), True)
    expect(criterion_standard_plus_brutal(
        good, {"standard_plus_brutal": {"anchor-01": 2, "anchor-02": 4}}), True, "(+1)")
    expect(criterion_standard_plus_brutal(
        good, {"standard_plus_brutal": {"anchor-01": 4, "anchor-02": 4}}), False, "(−1)")
    expect(criterion_standard_plus_brutal(
        good, {"standard_plus_brutal": {"anchor-01": 3}}), False, "no baseline for")
    #    The flat-floor misreading decision 088 rejected: ROBUST_ENOUGH = 8 as a per-anchor
    #    floor fails a campaign that is 24/24 ok. Asserted so nobody reintroduces it.
    flat = criterion_standard_plus_brutal(good, {"standard_plus_brutal": {a["anchor"]: 8
                                                                          for a in good}})
    assert not flat.ok, "a flat floor of 8 must fail this fixture — decision 088"
    tally["structural"] += 1

    # 3. multi-weapon. The denominator is the whole criterion: an anchor with one weapon
    #    unlocked is EXCLUDED, not a miss (decision 088's correction, anchor-01/02).
    mw_bl = {"multi_weapon": {"achievable": ["anchor-02"], "met": ["anchor-02"]}}
    mixt = [_report("anchor-01", one_weapon, {base_d: single}, {base_d: (1, 5), top_d: (1, 5)}),
            _report("anchor-02", two_weapon, {base_d: mixed}, {base_d: (2, 5), top_d: (1, 5)})]
    c = criterion_multi_weapon(mixt, mw_bl, towers)
    expect(c, True, "1/1 achievable")
    assert "anchor-01" in c.headline or "1/2 unconditioned" in c.headline, c.headline
    tally["structural"] += 1
    #    A regression: the achievable anchor loses its mixed build.
    lost = [mixt[0], _report("anchor-02", two_weapon, {base_d: single},
                             {base_d: (2, 5), top_d: (1, 5)})]
    expect(criterion_multi_weapon(lost, mw_bl, towers), False, "REGRESSION")
    #    A newly achievable anchor that misses is reported, not counted as a regression.
    grew = [_report("anchor-01", two_weapon, {base_d: single}, {base_d: (1, 5), top_d: (1, 5)}),
            mixt[1]]
    expect(criterion_multi_weapon(grew, mw_bl, towers), True, "newly achievable")
    #    LF-258: no `runs` must fail loudly, never report 0 of 22.
    bare = [_report("anchor-02", two_weapon, {}, {base_d: (2, 5), top_d: (1, 5)},
                    with_runs=False)]
    expect(criterion_multi_weapon(bare, mw_bl, towers), False, "--detail")

    # 4. count: 1 share, as exact integers. A rise of 0.03 of a point — invisible to the
    #    stored percentage decision 091 records — still fails.
    c1 = {"count_one": {"1": {"count_one": 15, "entries": 191}}}
    assert not _share_rose({"count_one": 15, "entries": 191}, c1["count_one"]["1"])
    assert not _share_rose({"count_one": 14, "entries": 191}, c1["count_one"]["1"])
    assert _share_rose({"count_one": 16, "entries": 203}, c1["count_one"]["1"]), \
        "7.88% must count as a rise over 7.85% — that is why this is integers, not floats"
    assert _share_rose({"count_one": 16, "entries": 191}, c1["count_one"]["1"])
    tally["structural"] += 4

    # 5. win share. Falling passes; equal fails; the per-anchor top-tier rule fails on a
    #    non-tutorial anchor and is exempt on a tutorial, matching sim/run.py's verdict().
    ws_bl = {"campaign_win_share": {base_d: {"won": 5, "tried": 11},
                                    top_d: {"won": 2, "tried": 11}}}
    expect(criterion_win_share(good, ws_bl, diffs), True, "falls strictly: yes")
    flat_ws = [_report("anchor-01", two_weapon, {base_d: mixed},
                       {base_d: (2, 5), top_d: (2, 5)}),
               _report("anchor-02", two_weapon, {base_d: mixed},
                       {base_d: (3, 6), top_d: (3, 6)})]
    expect(criterion_win_share(flat_ws, ws_bl, diffs), False, "does not fall strictly")
    tut = [dict(flat_ws[0], tutorial=True), good[1]]
    c = criterion_win_share(tut, ws_bl, diffs)
    assert "anchor-01" not in "\n".join(c.lines), \
        "the tutorial exemption must reach the per-anchor rule here too"
    tally["structural"] += 1

    # 6. STRUCTURAL. The tiers come from DIFFICULTIES' order, not from literals.
    assert BASE_DIFFICULTY == list(DIFFICULTIES)[0] and TOP_DIFFICULTY == list(DIFFICULTIES)[-1]
    assert BASE_DIFFICULTY != TOP_DIFFICULTY, "one difficulty tier: criterion 6 is vacuous"
    tally["structural"] += 1

    # 7. STRUCTURAL. The stored baseline parses, carries every key the comparators read, and
    #    is self-consistent — its multi_weapon `met` must be a subset of `achievable`.
    b = load_baseline()
    for key in ("anchors", "difficulties", "standard_plus_brutal", "multi_weapon",
                "count_one", "campaign_win_share"):
        assert key in b, f"{BASELINE_PATH.name} has no {key!r}"
    assert set(b["multi_weapon"]["met"]) <= set(b["multi_weapon"]["achievable"])
    assert len(b["standard_plus_brutal"]) == b["anchors"], \
        "the per-anchor baseline does not cover every anchor it claims"
    tally["structural"] += 3

    print(f"tools/criteria.py selftest: {sum(tally.values())} case(s) ok — comparators fire "
          f"on {tally['red']}, stay silent on {tally['green']}, {tally['structural']} "
          f"structural; tiers {base_d!r} -> {top_d!r} read from DIFFICULTIES; "
          f"baseline {BASELINE_PATH.name} at {b.get('commit', '?')}")
    return 0


# ───────────────────────────────────────────────────────────────────── main ──

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Print BAL-04's acceptance-criteria table against its baselines.")
    ap.add_argument("--grade", help="a sim/run.py --json --detail artefact; omit to grade "
                                    "in-process, which cannot hit LF-258")
    ap.add_argument("--jobs", type=int, default=1,
                    help="passed to sim/run.py's pool when grading in-process")
    ap.add_argument("--baseline", default=str(BASELINE_PATH))
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--verbose", action="store_true",
                    help="print the detail lines of criteria that passed too")
    ap.add_argument("--rebaseline", action="store_true",
                    help="rewrite the baseline artefact from this measurement")
    ap.add_argument("--accept-regressions", action="store_true",
                    help="with --rebaseline, allow it to bake in a figure that got worse")
    ap.add_argument("--selftest", action="store_true",
                    help="drive every comparator red and green, without content")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    bpath = Path(args.baseline)
    if args.rebaseline and not bpath.exists():
        print(f"{bpath} does not exist — bootstrapping an empty baseline")
        baseline = dict(EMPTY_BASELINE)
    else:
        baseline = load_baseline(bpath)
    if args.grade:
        reports = json.loads(Path(args.grade).read_text())
        diffs = [d for d in DIFFICULTIES
                 if all(d in r["by_difficulty"] for r in reports)]
    else:
        diffs = list(DIFFICULTIES)
        reports = grade_all(all_anchor_ids(), diffs, args.jobs)

    criteria = evaluate(reports, baseline, diffs)
    bad = [c for c in criteria if not c.ok]

    if args.rebaseline:
        # A partial grade must never become the bar. `--grade` on a one-anchor artefact
        # would otherwise write `anchors: 1` and a `standard_plus_brutal` table with one
        # row, and every later run would compare the campaign against it and pass.
        full = list(DIFFICULTIES)
        if len(reports) < len(all_anchor_ids()) or diffs != full:
            print(f"refusing to rebaseline from a partial grade: {len(reports)} of "
                  f"{len(all_anchor_ids())} anchors at [{', '.join(diffs)}], "
                  f"need all at [{', '.join(full)}]")
            return 1
        regressed = [f"{c.key}: {', '.join(c.regressions)}"
                     for c in criteria if c.regressions]
        if regressed and not args.accept_regressions:
            print_table(criteria, baseline, args.verbose)
            print("\nrefusing to rebaseline — these got worse than the stored bar:")
            for line in regressed:
                print(f"  {line}")
            print("Pass --accept-regressions to bake that in deliberately.")
            return 1
        doc = baseline_from(criteria, _git_commit())
        bpath.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        print(f"wrote {bpath} at {doc['commit']}"
              + (f"  ·  BAKED IN REGRESSIONS: {'; '.join(regressed)}" if regressed else ""))
        return 0

    if args.json:
        print(json.dumps({
            "schema": "latticefall-criteria", "version": 1,
            "commit": _git_commit(), "ok": not bad,
            "baseline": {"path": str(bpath), "commit": baseline.get("commit")},
            "criteria": [{"key": c.key, "title": c.title, "status": c.status,
                          "headline": c.headline, "detail": c.lines,
                          "measured": c.measured} for c in criteria],
        }, indent=2, sort_keys=True))
        return 1 if bad else 0

    print_table(criteria, baseline, args.verbose)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
