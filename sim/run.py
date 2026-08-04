#!/usr/bin/env python3
"""
Grade Latticefall anchors headlessly.

    .venv/bin/python sim/run.py                              # every anchor, every difficulty
    .venv/bin/python sim/run.py --anchor anchor-01           # one anchor
    .venv/bin/python sim/run.py --anchor anchor-01 --json    # machine-readable
    .venv/bin/python sim/run.py --anchor anchor-01 --detail  # per-policy breakdown
    .venv/bin/python sim/run.py --jobs 8                     # one process per anchor

The verdict is not just win/loss. An anchor passes only if it is winnable by more
than one approach and the top difficulty is actually harder than the bottom one.
A level nobody can lose and a level with exactly one answer are both failures, and
neither shows up in a pass/fail number.

WIN SHARE, AND WHY THE OLD TEST COULD NOT SEE A DIFFICULTY DISSOLVE. Until LF-243 the
only guard on a difficulty tier was `distinct_winning_builds == distinct_builds_tried`
— a knife edge that fires when *every* build clears and is silent one build short of
that. Decision 082 walked straight through it: grading all 24 shipped anchors at the
48-square derived ranges left them 24/24 `ok` while brutal's share of tried builds that
win went 24% -> 43% and the median anchor's winning builds went 3 -> 6. The campaign's
own instrument reported no change to a change that roughly doubled how forgiving the
top difficulty is.

The replacement is `win_share` = `distinct_winning_builds / distinct_builds_tried`, and
the rule is that the **top** difficulty's win share must fall strictly below the
**bottom** one's, on every anchor. It has no threshold in it, which is the point —
decision 067 deleted `PRESSURE_FLOOR` for being a number with no argument behind it, and
a bound on "how forgiving is too forgiving" would be exactly that number again. "The
hardest tier is harder than the easiest tier" needs no constant and is the weakest
statement that is not vacuous. Measured: 0 of 24 shipped anchors fail it, 3 of 24 fail
it at the derived ranges. A fourth, anchor-01, trips the same comparison there and still
reports `ok`, because the tutorial relaxation reaches this rule too. Decision 086.

A share rather than a count, because BAL-04 adds grading policies and a count would
tighten every time the repertoire grows, for a reason that has nothing to do with the
content. `hard` is deliberately *reported* and not asserted — measured, it does not fall
below standard on anchor-08 or anchor-15 today, and brutal is the tier `DIFFICULTIES`
itself describes as the decisive one.

Grading is embarrassingly parallel and was serial for twenty-four anchors: --jobs
grades them in a pool, in the same order, with the same numbers. The sim has no RNG and
no shared state, so this changes wall-clock and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from sim.content import all_anchor_ids, load_anchor, load_enemies, load_towers  # noqa: E402
from sim.engine import DIFFICULTIES, Sim, standard_policies  # noqa: E402
import lease  # noqa: E402  — scopes the --jobs pool for tools/reap.py (PRC-07)

## Generous: a wide manual grading run (a whole act, or all 24 anchors at --jobs 0) is
## legitimately slower than the 3.5-minute serial baseline this file's docstring measures.
## The TTL is a crash backstop for the pool, not a performance budget.
POOL_LEASE_TTL_S = 1800.0

## The tiers are read off DIFFICULTIES' own order rather than spelled "standard"/"brutal"
## here, so a fourth tier is a data change and not a second place to remember. Decision 060
## made the same call for the yaw count.
BASE_DIFFICULTY = list(DIFFICULTIES)[0]
TOP_DIFFICULTY = list(DIFFICULTIES)[-1]

## PRESSURE_FLOOR used to live here, asserting that an anchor's peak load gets near
## capacity — "the player is pressed against the bus". It was measured DEAD twice: 0 of 72
## anchor-by-difficulty cells ever failed it, including under the capped-core policies and
## including restricted to winning builds only, which was its own proposed repair. Deleted
## rather than raised, because a threshold picked to make today's data fail is fitted to
## that data and has no argument behind it. Decision 067.
##
## The property is still worth testing and peak load ratio is simply the wrong instrument:
## peak is >= 100% everywhere, which says a greedy policy exists, not that a good board is
## squeezed. A real metric looks at time spent within a band of capacity across a WINNING
## run, or at the margin by which the best build clears. That is design work with its own
## evidence (LF-131), not a constant.


def grade(anchor_id: str, difficulties: list[str]) -> dict:
    return grade_anchor(load_anchor(anchor_id), difficulties)


def _grade_one(job: tuple[str, list[str]]) -> dict:
    """Pool worker. Top-level rather than a lambda because a pool pickles the callable."""
    return grade(*job)


def grade_all(anchor_ids: list[str], difficulties: list[str], jobs: int = 1) -> list[dict]:
    """Grade in anchor order. `jobs` only changes how long it takes."""
    work = [(i, difficulties) for i in anchor_ids]
    if jobs <= 1 or len(work) == 1:
        return [_grade_one(w) for w in work]
    # Leased so a sibling agent's `tools/reap.py --kill` spares this pool's workers
    # instead of orphaning them mid-grade (PRC-07) — the workers fork from this process,
    # so one lease here covers the whole pool via tools/reap.py's ancestor walk.
    with lease.acquire("sim-run", [f"jobs={jobs}", f"anchors={len(work)}"],
                       ttl_s=POOL_LEASE_TTL_S):
        with Pool(min(jobs, len(work))) as pool:
            return list(pool.imap(_grade_one, work))


def verdict(by_diff: dict, tutorial: bool = False) -> list[str]:
    """Turn a per-difficulty grade table into the anchor's list of problems.

    A pure function of the table, with no Sim and no content behind it, so `--selftest`
    can drive every branch in milliseconds. That is not tidiness: three of the four rules
    below can only fire on content that does not exist in `data/anchors/`, so a gate that
    grades the shipped 24 exercises the *green* path of each one and says nothing whatever
    about the red path. `firing arcs agree` is the same lesson (CLAUDE.md) — a check that
    runs the whole game proves nothing about a branch the shipped data never enters.
    """
    problems = []
    for diff, d in by_diff.items():
        if d["win_count"] == 0:
            problems.append(f"{diff}: unwinnable — no policy clears it")
        elif d["distinct_winning_builds"] == 1 and d["distinct_builds_tried"] > 1:
            problems.append(
                f"{diff}: only one distinct build clears it — single-solution level")
        if (d["distinct_builds_tried"] > 1
                and d["distinct_winning_builds"] == d["distinct_builds_tried"]
                and diff != BASE_DIFFICULTY):
            problems.append(f"{diff}: every distinct build clears it — difficulty is not biting")

    # LF-243 / decision 086. The rule above is a knife edge: it fires only when a tier's
    # win share is exactly 1.0, so it is silent one build short of that — which is how
    # decision 082's derived ranges took brutal from 24% to 43% campaign-wide without
    # moving a single verdict. This is the general form of the same statement, and it is
    # strictly stronger: a tier at share 1.0 cannot be below a tier that is at most 1.0,
    # so every case the knife edge catches this catches too.
    #
    # Only the top tier is asserted. The middle tiers are *reported* by main() instead,
    # because measured they do not fall below standard on anchor-08 or anchor-15 today and
    # a check that is red on arrival gets disabled rather than fixed (LF-224). `brutal` is
    # also the tier DIFFICULTIES' own comment describes as the decisive one.
    if (BASE_DIFFICULTY != TOP_DIFFICULTY
            and BASE_DIFFICULTY in by_diff and TOP_DIFFICULTY in by_diff
            and by_diff[BASE_DIFFICULTY]["distinct_builds_tried"] > 1):
        top, base = by_diff[TOP_DIFFICULTY], by_diff[BASE_DIFFICULTY]
        if top["win_share"] >= base["win_share"]:
            problems.append(
                f"{TOP_DIFFICULTY}: win share {top['win_share']:.0%} "
                f"({top['distinct_winning_builds']}/{top['distinct_builds_tried']}) is "
                f"not below {BASE_DIFFICULTY}'s {base['win_share']:.0%} "
                f"({base['distinct_winning_builds']}/{base['distinct_builds_tried']}) — "
                f"the top difficulty is not harder than the bottom one")

    # A tutorial has one emplacement unlocked, so it has one build by construction and
    # nothing for a difficulty tier to differentiate — five distinct builds tried on
    # anchor-01 against thirteen to sixteen everywhere else, which quantises its win share
    # into 20-point steps and makes a strict comparison between tiers meaningless. All
    # three build-count checks are relaxed, and the anchor declares this in data rather
    # than the grader inferring it.
    if tutorial:
        problems = [p for p in problems
                    if "single-solution" not in p and "not biting" not in p
                    and "not harder than" not in p]
    return problems


def grade_anchor(anchor, difficulties: list[str], towers=None, enemies=None) -> dict:
    """Grade an Anchor object. Split out from grade() so a sweep can grade an anchor
    that only exists in memory — tools/sweep.py varies capacity, funds and wave weight
    without writing sixteen candidate files to disk."""
    towers = towers if towers is not None else load_towers()
    enemies = enemies if enemies is not None else load_enemies()
    # sorted, so the preference tail matches the GDScript port's sorted id list
    available = sorted(t.id for t in towers.values() if t.unlocked_at <= anchor.id)
    policies = standard_policies(available)

    runs, by_diff = [], {}
    for diff in difficulties:
        outcomes = [Sim(anchor, towers, enemies, p, diff).run() for p in policies]
        runs.extend(outcomes)
        winners = [o for o in outcomes if o.won]
        # Policies that converge on the same board are one build, not several.
        distinct_wins = {tuple(sorted(o.built)) for o in winners}
        distinct_all = {tuple(sorted(o.built)) for o in outcomes}
        by_diff[diff] = {
            "winning_policies": [o.policy for o in winners],
            "win_count": len(winners),
            "policy_count": len(outcomes),
            "distinct_winning_builds": len(distinct_wins),
            "distinct_builds_tried": len(distinct_all),
            # LF-243. Stored rather than left for each consumer to divide, because three
            # of them already recompute `distinct_winning_builds` in their own way and a
            # fourth copy of the ratio is the drift this project keeps paying for.
            "win_share": (len(distinct_wins) / len(distinct_all)) if distinct_all else 0.0,
            "peak_load_mw": round(max(o.peak_load_mw for o in outcomes), 2),
            "peak_load_ratio": round(
                max(o.peak_load_mw for o in outcomes) / anchor.capacity_mw, 3),
            "earliest_death_wave": min(
                (o.died_on_wave for o in outcomes if o.died_on_wave), default=None),
            "brownout_fraction": round(
                max(o.brownout_fraction for o in outcomes), 3),
        }

    problems = verdict(by_diff, anchor.tutorial)

    return {
        "anchor": anchor.id,
        "tutorial": anchor.tutorial,
        "title": anchor.title,
        "act": anchor.act,
        "capacity_mw": anchor.capacity_mw,
        "waves": len(anchor.waves),
        "slots": len(anchor.slots),
        "unlocked": available,
        "by_difficulty": by_diff,
        "problems": problems,
        "ok": not problems,
        "runs": [o.as_dict() for o in runs],
    }


def campaign_win_share(reports: list[dict], diffs: list[str]) -> dict:
    """Pool win share across a set of graded anchors, per difficulty.

    Pooled (sum of winners over sum of tried), not the mean of per-anchor shares: an
    anchor with sixteen distinct builds tried is a stronger statement about the campaign
    than a tutorial with five, and averaging the ratios gives them equal weight. The two
    figures differ by under half a point on the shipped campaign either way; the pooled
    one is reported because it is the one whose denominator is a real count.

    Reported, never asserted. Measured on both the shipped campaign (40.6/30.0/24.2) and
    on the derived-range campaign decision 082 refused (54.5/46.8/42.6), this falls
    strictly in *both* — so it cannot be the test, and pretending otherwise would put a
    number in the gate that has already been shown not to discriminate. It is here as a
    trend line: `tools/session.py` writes it under `docs/STATE.md`'s grade table on every
    wrap, which is where a campaign-level drift becomes visible over sessions rather than
    within one. LF-243, decision 086.
    """
    out: dict = {"by_difficulty": {}, "anchors": len(reports)}
    for d in diffs:
        cells = [r["by_difficulty"][d] for r in reports if d in r["by_difficulty"]]
        won = sum(c["distinct_winning_builds"] for c in cells)
        tried = sum(c["distinct_builds_tried"] for c in cells)
        out["by_difficulty"][d] = {"won": won, "tried": tried,
                                   "win_share": (won / tried) if tried else 0.0}
    shares = [out["by_difficulty"][d]["win_share"] for d in diffs]
    out["falls_strictly"] = all(a > b for a, b in zip(shares, shares[1:]))
    # The tiers between bottom and top are not asserted per-anchor (see grade_anchor);
    # naming the anchors where they do not fall is what makes that omission visible
    # instead of silent. Measured today: hard does not fall on anchor-08 and anchor-15.
    out["not_falling"] = {}
    for d in diffs[1:]:
        out["not_falling"][d] = [
            r["anchor"] for r in reports
            if not r["tutorial"] and d in r["by_difficulty"] and diffs[0] in r["by_difficulty"]
            and r["by_difficulty"][d]["win_share"] >= r["by_difficulty"][diffs[0]]["win_share"]]
    return out


def campaign_discipline(reports: list[dict], diffs: list[str]) -> dict:
    """Is overdrawing the bus a judgement call, or an obvious yes? LF-253, decision 087.

    Decision 022 replaced a flat −40% brownout penalty with a priced one *specifically* so
    that overdrawing could sometimes pay — `LF-014` had measured that under the flat penalty
    no build ever benefited at any difficulty, which collapsed the whole power economy into
    a build constraint. The mirror question is whether 022 overshot, and the raw number says
    it did: runs that brown out win **53.2%** on standard against **31.4%** for runs that do
    not, a 21.8-point advantage for indiscipline.

    **That number is an artefact and this function is here to stop it being read again.**
    Whether a run browns out is decided by the *policy's* build rules, not by the penalty —
    the clean/browned split is the same 307/173 runs at every price the slope was swept to.
    So the raw comparison is mostly "the policies that overdraw are the strong ones": the
    lance is the game's best weapon and it draws the most, so a lance-led board overdraws.
    Compare each policy against **itself** — its own browned-out runs against its own clean
    ones — and three quarters of the effect goes, and the sign flips on brutal: +6.0%, +5.2%,
    **−3.7%**, helping on 7 of 18, 6 of 18 and **4 of 18** policies that split both ways.
    A thing that helps on under a third of approaches is a judgement call, which is exactly
    what decision 022 set out to build.

    Reported, never asserted, for decision 086's reason: bounding it needs a constant, and
    the honest statement — "helps on a minority" — has two policies of headroom on standard
    and would be a threshold in all but name. Both the raw and the adjusted figures are
    printed together, because printing either alone is how this was misread in the first
    place. Needs `runs`, so `--json` callers must pass `--detail`; returns {} without them.
    """
    out: dict = {}
    for d in diffs:
        runs = [o for r in reports for o in r.get("runs", []) if o["difficulty"] == d]
        if not runs or "brownout_fraction" not in runs[0]:
            return {}
        clean = [o for o in runs if o["brownout_fraction"] == 0.0]
        brown = [o for o in runs if o["brownout_fraction"] > 0.0]
        if not clean or not brown:
            continue
        raw = (sum(o["won"] for o in brown) / len(brown)
               - sum(o["won"] for o in clean) / len(clean))
        deltas, nc, nb = [], 0, 0
        for pol in {o["policy"] for o in runs}:
            c = [o for o in clean if o["policy"] == pol]
            b = [o for o in brown if o["policy"] == pol]
            if not c or not b:
                continue
            deltas.append(sum(o["won"] for o in b) / len(b)
                          - sum(o["won"] for o in c) / len(c))
            nc += len(c)
            nb += len(b)
        # Two different populations, named as two different things on purpose. `raw_*` is
        # every run at this difficulty; `paired_*` counts only the runs belonging to
        # policies that browned out on some anchors and not others, which is the only
        # subset the within-policy comparison can use. The first draft of this printed the
        # paired counts beside the raw delta under one label, which invites exactly the
        # conflation the whole function exists to prevent — caught in review.
        out[d] = {"raw_delta": raw, "raw_clean_runs": len(clean), "raw_brown_runs": len(brown),
                  "split_policies": len(deltas),
                  "helps_on": sum(1 for x in deltas if x > 0),
                  "within_policy_delta": (sum(deltas) / len(deltas)) if deltas else 0.0,
                  "paired_clean_runs": nc, "paired_brown_runs": nb}
    return out


def print_campaign_discipline(cd: dict) -> None:
    if not cd:
        return
    print("\noverdraw advantage — does browning out the bus help? "
          "(decision 087; reported, not asserted)")
    print(f"  {'':9s} {'raw':>8s} {'over all runs':>15s}   {'within policy':>14s} "
          f"{'over the policies that split both ways':>40s}")
    for d, x in cd.items():
        print(f"  {d:9s} {x['raw_delta']:>+8.1%} "
              f"{x['raw_clean_runs']}c/{x['raw_brown_runs']}b runs   "
              f"{x['within_policy_delta']:>+14.1%} "
              f"{x['helps_on']}/{x['split_policies']} policies, "
              f"{x['paired_clean_runs']}c/{x['paired_brown_runs']}b runs")
    print("  The raw column is confounded by WHICH policies overdraw and must not be read")
    print("  alone. The two run counts are DIFFERENT populations: the paired one covers only")
    print("  policies that browned out on some anchors and not others.")


def print_campaign_win_share(cws: dict, diffs: list[str]) -> None:
    print(f"\ncampaign win share over {cws['anchors']} anchors "
          f"(distinct winning builds / distinct builds tried, pooled)")
    cells = " · ".join(
        f"{d} {cws['by_difficulty'][d]['win_share']:.1%} "
        f"({cws['by_difficulty'][d]['won']}/{cws['by_difficulty'][d]['tried']})"
        for d in diffs)
    print(f"  {cells}")
    print(f"  falls strictly across the tiers: "
          f"{'yes' if cws['falls_strictly'] else 'NO'}   [reported, not asserted]")
    for d, names in cws["not_falling"].items():
        if names:
            print(f"  {d} does not fall below {diffs[0]} on {len(names)} anchor(s): "
                  f"{', '.join(names)}   [reported, not asserted — only "
                  f"{TOP_DIFFICULTY} is a problem]")


# ─────────────────────────────────────────────────────────────────── selftest ──

def _cell(won: int, tried: int, wins: int | None = None) -> dict:
    """One difficulty's grade cell, with only the keys `verdict()` reads."""
    return {"win_count": won if wins is None else wins,
            "distinct_winning_builds": won, "distinct_builds_tried": tried,
            "win_share": (won / tried) if tried else 0.0}


def _table(**cells: tuple) -> dict:
    return {d: _cell(*v) for d, v in cells.items()}


def selftest() -> int:
    """Drive every branch of `verdict()`, red and green. Prints what it checked.

    The red paths matter more than the green ones here. Three of the four rules cannot
    fire on any anchor in `data/anchors/`, so the gate's `anchor grades` check — which
    grades exactly those anchors — only ever sees them pass. Decision 086 and LF-243.
    """
    base, top = BASE_DIFFICULTY, TOP_DIFFICULTY
    assert base != top, f"DIFFICULTIES has one tier ({base!r}); the top-tier rule is vacuous"
    # Counted, never written into the message as a literal: a hardcoded tally in a line
    # that claims to be evidence is how this project has twice shipped a count that was
    # wrong by the time anyone read it (CLAUDE.md, `tier counts`).
    tally = {"red": 0, "green": 0, "structural": 0}

    def case(name: str, table: dict, want: str | None, tutorial: bool = False) -> None:
        got = verdict(table, tutorial)
        if want is None:
            assert not got, f"{name}: expected no problem, got {got}"
            tally["green"] += 1
        else:
            assert any(want in p for p in got), f"{name}: expected {want!r}, got {got}"
            tally["red"] += 1

    # 1. GREEN. The shipped campaign's shape: the top tier's share is strictly lower.
    case("healthy anchor", _table(**{base: (6, 15), top: (3, 15)}), None)

    # 2. RED, equal shares. anchor-23 at the derived ranges is exactly this — 5 of 15 on
    #    both tiers — and it is the case the old knife-edge rule is blind to, because
    #    neither tier is anywhere near 1.0.
    case("top tier equal to base", _table(**{base: (5, 15), top: (5, 15)}),
         "not harder than the bottom one")

    # 3. RED, top tier strictly more forgiving. anchor-02 at the derived ranges: 6/10
    #    against 8/11. Also blind to the knife edge.
    case("top tier above base", _table(**{base: (6, 10), top: (8, 11)}),
         "not harder than the bottom one")

    # 4. RED, and the proof that the new rule SUBSUMES the old one rather than sitting
    #    beside it: every table the knife edge catches has the top tier at share 1.0,
    #    which cannot be below a base tier that is at most 1.0.
    knife = _table(**{base: (6, 15), top: (14, 14)})
    got = verdict(knife, False)
    assert any("not biting" in p for p in got), f"knife edge did not fire: {got}"
    assert any("not harder than" in p for p in got), f"new rule missed a knife-edge case: {got}"
    tally["red"] += 1

    # 5. Near miss, GREEN. anchor-21 as shipped: the same absolute count on both tiers,
    #    passing only because the top tier tried one more build. Recorded as a case
    #    because it is the campaign's thinnest margin (+1.7 points) and the anchor this
    #    rule will name first if BAL-04 loosens anything.
    case("shipped anchor-21 (+1.7pt margin)", _table(**{base: (4, 15), top: (4, 16)}), None)

    # 6. The tutorial relaxation reaches the new rule too, not just the two older ones.
    case("tutorial is exempt", _table(**{base: (2, 5), top: (3, 5)}), None, tutorial=True)
    case("non-tutorial is not", _table(**{base: (2, 5), top: (3, 5)}),
         "not harder than the bottom one")

    # 7. A single-tier grade (`--difficulty standard`) has nothing to compare and must not
    #    invent a problem — tools/sweep.py and tools/range_derive.py both grade subsets.
    case("one tier only", _table(**{base: (3, 15)}), None)

    # 8. The older rules still fire, so this refactor did not quietly drop one.
    case("unwinnable", {base: _cell(0, 15, wins=0)}, "unwinnable")
    case("single solution", _table(**{base: (1, 15)}), "single-solution level")

    # 9. The tiers come from DIFFICULTIES' order, not from the strings "standard"/"brutal".
    assert base == list(DIFFICULTIES)[0] and top == list(DIFFICULTIES)[-1]
    tally["structural"] += 1

    # 10. campaign_win_share pools rather than averages, and names the middle tiers it does
    #     not assert. Two anchors of very different size, so pooled != mean-of-shares.
    reports = [
        {"anchor": "a", "tutorial": False,
         "by_difficulty": _table(**{base: (1, 2), "mid": (0, 2), top: (0, 2)})},
        {"anchor": "b", "tutorial": False,
         "by_difficulty": _table(**{base: (2, 20), "mid": (9, 20), top: (1, 20)})},
    ]
    cws = campaign_win_share(reports, [base, "mid", top])
    assert cws["by_difficulty"][base]["win_share"] == 3 / 22, cws
    assert cws["not_falling"]["mid"] == ["b"], cws["not_falling"]
    assert not cws["falls_strictly"], cws          # mid 40.9% is above base 13.6%
    tally["structural"] += 1

    # 11. campaign_discipline separates the raw comparison from the within-policy one, which
    #     is the entire point of decision 087. Two policies, constructed so the RAW figure
    #     says browning out wins by 50 points while WITHIN each policy it does nothing:
    #     policy `a` always browns out and always wins, `b` never browns out and never wins,
    #     and the two policies that DO split both ways are flat. A function that pooled
    #     instead of pairing would report +50% in both columns.
    def _run(pol, brown, won):
        return {"difficulty": base, "policy": pol, "won": won,
                "brownout_fraction": 0.5 if brown else 0.0}
    disc = [{"anchor": "x", "tutorial": False, "by_difficulty": _table(**{base: (1, 2)}),
             "runs": [_run("a", True, True), _run("a", True, True),
                      _run("b", False, False), _run("b", False, False),
                      _run("split1", True, True), _run("split1", False, True),
                      _run("split2", True, False), _run("split2", False, False)]}]
    cd = campaign_discipline(disc, [base])[base]
    assert abs(cd["raw_delta"] - 0.5) < 1e-9, cd          # 3/4 brown win vs 1/4 clean
    assert cd["within_policy_delta"] == 0.0, cd           # both splitters are flat
    assert (cd["split_policies"], cd["helps_on"]) == (2, 0), cd
    # The two populations must NOT be the same number: raw covers all eight runs, paired
    # covers only the four belonging to `split1`/`split2`. Conflating them is the defect
    # this fixture exists to hold down.
    assert (cd["raw_clean_runs"], cd["raw_brown_runs"]) == (4, 4), cd
    assert (cd["paired_clean_runs"], cd["paired_brown_runs"]) == (2, 2), cd
    # Both halves of the "no detail" guard, because they catch different callers: `--json`
    # without `--detail` drops `runs` entirely, while a caller that trimmed the run records
    # to save memory keeps them and loses the key. The second half was NOT covered when this
    # selftest was first written, and deleting it left the selftest green — found by
    # breaking it on purpose, which is the only reason it is covered now.
    bare = {"anchor": "x", "tutorial": False, "by_difficulty": _table(**{base: (1, 2)})}
    assert campaign_discipline([bare], [base]) == {}, "no `runs` key at all"
    assert campaign_discipline(
        [dict(bare, runs=[{"difficulty": base, "policy": "a", "won": True}])],
        [base]) == {}, "`runs` present but trimmed of brownout_fraction"
    tally["structural"] += 2

    print(f"sim/run.py selftest: {sum(tally.values())} case(s) ok — verdict() fires on "
          f"{tally['red']}, stays silent on {tally['green']}, "
          f"{tally['structural']} structural; tiers {base!r} -> {top!r} "
          f"read from DIFFICULTIES")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade Latticefall anchors headlessly.")
    ap.add_argument("--anchor", help="anchor id (default: all)")
    ap.add_argument("--difficulty", choices=list(DIFFICULTIES), action="append",
                    help="repeatable. default: all three")
    ap.add_argument("--seed", type=int, default=0,
                    help="accepted for interface stability; the sim has no RNG")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--detail", action="store_true", help="per-policy breakdown")
    ap.add_argument("--jobs", type=int, default=1,
                    help="grade this many anchors at once; 0 for one per core")
    ap.add_argument("--selftest", action="store_true",
                    help="drive every branch of the verdict, red and green, without "
                         "loading content. See selftest() for why this is not optional")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    ids = [args.anchor] if args.anchor else all_anchor_ids()
    diffs = args.difficulty or list(DIFFICULTIES)
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else args.jobs
    reports = grade_all(ids, diffs, jobs)

    if args.json:
        slim = [{k: v for k, v in r.items() if k != "runs" or args.detail}
                for r in reports]
        print(json.dumps(slim, indent=2, sort_keys=True))
        return 0 if all(r["ok"] for r in reports) else 1

    for r in reports:
        head = f"{r['anchor']}  {r['title']}  ·  act {r['act']}  ·  {r['capacity_mw']:.0f} MW  ·  {r['waves']} waves"
        print(f"\n{head}\n{'─' * len(head)}")
        print(f"{'difficulty':<11s} {'builds':>8s} {'share':>6s} {'peak':>12s} "
              f"{'brownout':>9s}  died on")
        for diff in diffs:
            d = r["by_difficulty"][diff]
            died = f"wave {d['earliest_death_wave']}" if d["earliest_death_wave"] else "—"
            print(f"{diff:<11s} {d['distinct_winning_builds']:>2d} of {d['distinct_builds_tried']:<3d} "
                  f"{d['win_share']:>5.0%} {d['peak_load_mw']:>7.1f} MW "
                  f"{d['peak_load_ratio']:>4.0%} {d['brownout_fraction']:>8.0%}  {died}")
            if d["winning_policies"]:
                print(f"{'':11s} {', '.join(d['winning_policies'])}")

        if args.detail:
            print()
            for o in r["runs"]:
                print(f"  {o['difficulty']:<9s} {o['policy']:<17s} "
                      f"{'WON ' if o['won'] else 'lost'} "
                      f"w{o['waves_cleared']}/{o['waves_total']} "
                      f"lives {o['lives_left']:>2d}  peak {o['peak_load_mw']:>6.1f} MW  "
                      f"spend {o['spend']:>4d}  {len(o['built'])} built")

        if r["problems"]:
            print()
            for p in r["problems"]:
                print(f"  PROBLEM  {p}")
        else:
            print("\n  ok")

    if len(reports) > 1 and len(diffs) > 1:
        print_campaign_win_share(campaign_win_share(reports, diffs), diffs)
        print_campaign_discipline(campaign_discipline(reports, diffs))

    bad = [r["anchor"] for r in reports if not r["ok"]]
    print(f"\n{len(reports) - len(bad)}/{len(reports)} anchors clean")
    if bad:
        print(f"problems: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
