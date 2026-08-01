#!/usr/bin/env python3
"""Work the issue backlog unattended, one issue per session, until there is nothing left.

WHY THIS SHAPE, because the obvious alternative was tried first and is worse.

The problem this solves is not "the context window fills up". It is "the agent is sometimes
mid-task when the context window fills up". Those are different problems and only the second
one actually hurts. Every high-severity failure a compaction can cause — an uncommitted tree
nobody can explain, a subagent whose results land in a context that has forgotten why it was
dispatched, a pushed branch with no PR — is a *mid-task* failure. Losing prose is survivable;
this project's durable trail is 171 commits with real why-bodies, 41 append-only journal
entries, 76 decisions carrying their rejected alternatives, and 50 closed issues with evidence
in the close note. An entire investigation (LF-172) was reconstructed from `git log` alone this
week, and the reconstruction was more reliable than the backlog item describing it.

So: make the unit of work small enough that a session never fills, and the boundary is always
*between* tasks by construction. One issue, one session, ship, exit. Compaction then never
fires, and the wrap collapses into the ship it was always duplicating — PR, journal, issue
close. The `PreCompact`/`PostCompact` hooks stay wired as insurance for the rare long issue;
they are no longer the plan.

WHAT AN ITERATION MUST PRODUCE. A merged pull request, or an explicit reason it could not.
Anything else is a failure, including "ran to completion and did nothing" — silence is the
failure mode a loop is most likely to hide, so it is checked for directly (`merged_since`).

WHAT STOPS THE LOOP, in priority order. A dirty tree after an iteration stops it immediately
rather than compounding into the next one — a shared working tree is the single most expensive
thing to get wrong here. Then: an owner-gated decision, an empty queue, the iteration cap, the
wall-clock cap, or too many consecutive failures.

NOT A SCHEDULER. This runs in the foreground and holds a lockfile. Run it under `systemd --user`
or `nohup` if you want to walk away; do not run two.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"
ISSUES = ROOT / "docs" / "issues"
MAP = ISSUES / ".map.json"
LOCK = ROOT / ".cache" / "autoloop.lock"
STATEFILE = ROOT / ".cache" / "autoloop-state.json"

# ntfy: set LF_NTFY_URL to a full topic URL, e.g. https://ntfy.example.lan/latticefall
NTFY = os.environ.get("LF_NTFY_URL", "").strip()

# GAME WORK FIRST, PROCESS LAST — and this ordering is deliberate rather than the PRD's.
#
# PRD §5's dependency order is E1 -> E2 -> E3/E4 -> E5 -> E6 -> E7, and ranking by it put
# `PRC-14` (asset<->data coupling, pure tooling) at the head of the queue. That is precisely
# the failure the owner stopped a session to name: most of a day's pull requests changed the
# tooling and only one changed the game. E1 and E2 are effectively finished, so what survives
# in them is chores; sequencing chores ahead of the thing the owner is waiting to play is how
# an autonomous loop quietly spends a night on infrastructure.
#
# So: E3 first — it is in flight, and it is what makes the owner's own complaint (LF-176,
# "most turrets can barely reach anything") fixable in play. Then the rest of the game, then
# balance, then process. Dependencies still gate everything: an issue whose `depends:` are
# open is skipped regardless of milestone, so this only decides order among READY work.
MILESTONE_RANK = {
    "E3 Placement": 0,   # in flight; free placement is the owner's complaint made fixable
    "E4 Terrain": 1,     # visible ground
    "E5 War": 2,         # the army
    "E6 Fidelity": 3,    # how it looks
    "E7 Balance": 4,     # needs the verbs to exist first
    "E2 Camera": 5,      # complete; anything left is a straggler
    "E1 Process": 6,     # tooling — last, on purpose
}


# ───────────────────────────────────────────────────────────────── plumbing ──

def sh(*args: str, timeout: int = 120, cwd: Path = ROOT) -> tuple[int, str]:
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except Exception as exc:                                   # noqa: BLE001
        return 1, f"{exc.__class__.__name__}: {exc}"


def notify(title: str, body: str, priority: str = "default", tags: str = "") -> None:
    """Best-effort push. A failed notification must never stop the loop — the whole point is
    that nobody is watching, so the work continuing matters more than the message landing."""
    line = f"[autoloop] {title}: {body.splitlines()[0] if body else ''}"
    print(line, flush=True)
    if not NTFY:
        return
    req = urllib.request.Request(
        NTFY, data=body.encode("utf-8"), method="POST",
        headers={"Title": title, "Priority": priority, **({"Tags": tags} if tags else {})})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except (urllib.error.URLError, OSError) as exc:
        print(f"[autoloop] ntfy failed ({exc}) — continuing", flush=True)


def load_state() -> dict:
    try:
        return json.loads(STATEFILE.read_text())
    except Exception:                                          # noqa: BLE001
        return {"failures": {}}


def save_state(state: dict) -> None:
    STATEFILE.parent.mkdir(parents=True, exist_ok=True)
    STATEFILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# ───────────────────────────────────────────────────────────── issue picking ──

@dataclass
class Issue:
    number: int
    spec_id: str
    title: str
    milestone: str
    depends: list[str]

    @property
    def rank(self) -> tuple[int, int]:
        return (MILESTONE_RANK.get(self.milestone, 99), self.number)


def spec_depends(spec_id: str) -> list[str]:
    """`depends:` from the issue spec's header. The specs are the source of truth; GitHub is a
    projection of them (`tools/issues.py`), so dependency order is read from disk."""
    for path in ISSUES.glob(f"{spec_id}-*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("depends:"):
                return [s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()]
            if line.strip() == "---":
                break
    return []


def open_issues() -> list[Issue]:
    code, out = sh("gh", "issue", "list", "--state", "open", "--limit", "200",
                   "--json", "number,title,milestone", timeout=90)
    if code != 0:
        return []
    try:
        raw = json.loads(out)
    except Exception:                                          # noqa: BLE001
        return []
    out_list: list[Issue] = []
    for it in raw:
        m = re.match(r"\[([A-Z]+-\d+)\]\s*(.*)", it.get("title", ""))
        if not m:
            continue
        sid = m.group(1)
        out_list.append(Issue(
            number=it["number"], spec_id=sid, title=m.group(2),
            milestone=((it.get("milestone") or {}) or {}).get("title", "") or "",
            depends=spec_depends(sid)))
    return out_list


def pick(issues: list[Issue], state: dict, max_failures: int,
         prefer: list[str] | None = None) -> Issue | None:
    """Lowest-ranked ready issue: dependencies closed, not failed too often.

    Skipping an issue whose `depends:` are still open is the difference between a loop that
    makes progress and one that repeatedly picks the hardest blocked thing and fails on it.

    `prefer` seeds the front of the queue by spec id, for the case where a human knows the
    next thing that matters and the milestone heuristic does not. It still respects
    dependencies and the failure count — it reorders ready work, it does not force anything.
    """
    open_ids = {i.spec_id for i in issues}
    ready = [i for i in issues
             if not (set(i.depends) & open_ids)
             and state["failures"].get(i.spec_id, 0) < max_failures]
    if not ready:
        return None
    if prefer:
        for sid in prefer:
            for i in ready:
                if i.spec_id == sid:
                    return i
    return min(ready, key=lambda i: i.rank)


# ─────────────────────────────────────────────────────────────── the session ──

PROMPT = """\
Run /session-start first.

Then work exactly ONE issue to completion and ship it: **#{number} — {spec_id}: {title}**.

Read `docs/issues/{spec_id}-*.md` — it is the source of truth and it is detailed.

Ship it with /ship: branch `lf/<epic>-<slug>`, gate it, invoke the `chronicler` agent for the
journal entry (every PR updates the journal — copy any images into `docs/chronicle/assets/`,
never link a scratch path), push, wait for CI, squash-merge when green, and close the issue
with `tools/issues.py close {spec_id} --note "<what landed and how it was proved>"`.

Rules for this run:

- **Do not start any other issue.** Anything you discover goes to the backlog with
  `tools/backlog.py add` and, if it deserves one, a new spec in `docs/issues/` — not into this
  change.
- **A measured "no" is a successful outcome.** If the issue's premise turns out to be wrong,
  say so with the numbers and do not ship something you do not believe in.
- **If you hit a decision only the owner can make**, stop, and say `BLOCKED:` followed by the
  question. Do not guess. `docs/DECISIONS.md` and PRD §6 are where those live.
- **Leave the tree clean.** Everything committed and merged, or explicitly explained.
- Delegate with /dispatch; at most one or two Godot-launching workstreams at once.

When you are done, end your final message with exactly one of:
  `RESULT: SHIPPED #<pr>` · `RESULT: BLOCKED <why>` · `RESULT: REFUSED <why>`
"""


def merged_prs_since(iso: str) -> list[str]:
    code, out = sh("gh", "pr", "list", "--state", "merged", "--limit", "20",
                   "--json", "number,title,mergedAt", timeout=90)
    if code != 0:
        return []
    try:
        return [f"#{p['number']} {p['title']}"
                for p in json.loads(out) if (p.get("mergedAt") or "") > iso]
    except Exception:                                          # noqa: BLE001
        return []


def preflight() -> str | None:
    """Refuse to start on a tree that is not pristine. Everything downstream assumes it."""
    code, out = sh("git", "status", "--porcelain")
    if code != 0:
        return f"git status failed: {out}"
    if out:
        return f"working tree is dirty ({len(out.splitlines())} file(s)) — refusing to start"
    code, branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        return f"on branch {branch!r}, expected main"
    sh("git", "fetch", "origin", timeout=120)
    _, behind = sh("git", "rev-list", "--count", "HEAD..origin/main")
    if behind.strip() not in ("0", ""):
        return f"main is {behind} commit(s) behind origin — pull first"
    if PY.exists():
        code, out = sh(str(PY), "tools/reap.py", timeout=60)
        if "clean" not in out:
            return f"stray processes present: {out.splitlines()[0] if out else '?'}"
    return None


def run_one(issue: Issue, model: str, timeout_s: int,
            remote_control: bool = True) -> tuple[str, str]:
    """Spawn one session for this issue. Returns (verdict, detail).

    REMOTE CONTROL IS ON BY DEFAULT and each session is named after the issue, so the owner
    can watch a specific one from claude.ai or a phone without guessing which is which.

    UNVERIFIED COMBINATION, stated rather than assumed: `--help` describes `--remote-control`
    as starting an *interactive* session, and `-p` is non-interactive by definition. Whether
    they compose could not be tested from inside a session — spawning a nested
    permission-bypassing `claude` is denied by the auto-mode classifier, correctly. If the
    pair turns out to conflict, `--no-remote-control` drops the flag and everything else
    works unchanged. Find out with a single `--max-iterations 1` run before trusting a night
    to it.
    """
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prompt = PROMPT.format(number=issue.number, spec_id=issue.spec_id, title=issue.title)
    argv = ["claude", "-p", prompt, "--dangerously-skip-permissions",
            "--model", model, "--output-format", "text"]
    if remote_control:
        # Named per issue so a remote viewer can tell sessions apart at a glance.
        argv += ["--remote-control", f"latticefall-{issue.spec_id}"]
    code, out = sh(*argv, timeout=timeout_s)
    tail = out[-4000:] if out else ""

    if code == 124:
        return "TIMEOUT", f"no result after {timeout_s}s"

    m = re.search(r"RESULT:\s*(SHIPPED|BLOCKED|REFUSED)\s*(.*)", out or "")
    verdict = m.group(1) if m else ""
    reason = (m.group(2) or "").strip() if m else ""

    # Trust the ground truth over the self-report: a merged PR is the only proof of SHIPPED.
    merged = merged_prs_since(started)
    if verdict == "SHIPPED" and not merged:
        return "FAILED", f"claimed SHIPPED but no PR merged since {started}\n{tail}"
    if verdict in ("BLOCKED", "REFUSED"):
        return verdict, reason or tail
    if merged:
        return "SHIPPED", "; ".join(merged)
    return "FAILED", f"exit {code}, no verdict and no merged PR\n{tail}"


# ───────────────────────────────────────────────────────────────────── main ──

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-iterations", type=int, default=8)
    ap.add_argument("--issue-timeout", type=int, default=5400, help="seconds per issue")
    ap.add_argument("--max-wall-clock", type=int, default=28800, help="seconds, whole loop")
    ap.add_argument("--max-failures", type=int, default=2, help="per issue, before skipping")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--no-remote-control", action="store_true",
                    help="drop --remote-control from spawned sessions (use if it conflicts "
                         "with headless -p; see run_one's docstring)")
    ap.add_argument("--start-with", default="", help="comma-separated spec ids to try first, "
                                                     "e.g. PLC-07,PLC-04 (still respects depends)")
    ap.add_argument("--dry-run", action="store_true", help="show the queue and exit")
    args = ap.parse_args()

    if not MAP.exists():
        print("docs/issues/.map.json missing — run tools/issues.py first", file=sys.stderr)
        return 2

    issues = open_issues()
    state = load_state()
    prefer = [s.strip() for s in args.start_with.split(',') if s.strip()]

    if args.dry_run:
        print(f"{len(issues)} open issue(s). Queue order:")
        open_ids = {i.spec_id for i in issues}
        for i in sorted(issues, key=lambda i: i.rank)[:15]:
            blocked = sorted(set(i.depends) & open_ids)
            fails = state["failures"].get(i.spec_id, 0)
            flag = f"  BLOCKED by {', '.join(blocked)}" if blocked else ""
            flag += f"  ({fails} prior failure(s))" if fails else ""
            print(f"  {i.milestone:<14} #{i.number:<4} {i.spec_id:<8} {i.title[:56]}{flag}")
        nxt = pick(issues, state, args.max_failures, prefer)
        print(f"\nwould start: {nxt.spec_id} (#{nxt.number})" if nxt else "\nnothing ready")
        return 0

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        print(f"another autoloop holds {LOCK} — refusing to start", file=sys.stderr)
        return 2
    LOCK.write_text(f"{os.getpid()}\n{time.time()}\n")

    began = time.time()
    shipped: list[str] = []
    consecutive_failures = 0
    try:
        for n in range(1, args.max_iterations + 1):
            if time.time() - began > args.max_wall_clock:
                notify("autoloop stopped", f"wall-clock cap reached after {len(shipped)} shipped",
                       tags="hourglass")
                break

            err = preflight()
            if err:
                notify("autoloop STOPPED — preflight", err, priority="high", tags="warning")
                return 1

            issues = open_issues()
            issue = pick(issues, state, args.max_failures, prefer)
            if issue is None:
                notify("autoloop finished", "Nothing ready to work — the queue is empty or "
                       "everything left is blocked by an open dependency.\n\n"
                       f"Shipped this run: {len(shipped)}\n" + "\n".join(shipped),
                       tags="white_check_mark")
                break

            notify(f"autoloop {n}/{args.max_iterations} — starting",
                   f"{issue.spec_id} (#{issue.number}) {issue.title}", tags="hammer")
            verdict, detail = run_one(issue, args.model, args.issue_timeout,
                                      remote_control=not args.no_remote_control)

            if verdict == "SHIPPED":
                shipped.append(f"{issue.spec_id} — {detail}")
                state["failures"].pop(issue.spec_id, None)
                consecutive_failures = 0
                notify(f"shipped {issue.spec_id}", detail, tags="rocket")
            elif verdict in ("BLOCKED", "REFUSED"):
                state["failures"][issue.spec_id] = args.max_failures  # do not retry
                notify(f"autoloop {verdict} — needs you",
                       f"{issue.spec_id} (#{issue.number})\n\n{detail}",
                       priority="high", tags="raised_hand")
                save_state(state)
                if verdict == "BLOCKED":
                    return 0        # an owner decision halts the run; a refusal moves on
                continue
            else:
                state["failures"][issue.spec_id] = state["failures"].get(issue.spec_id, 0) + 1
                consecutive_failures += 1
                notify(f"autoloop {verdict} — {issue.spec_id}", detail,
                       priority="high", tags="x")
            save_state(state)

            # A dirty tree after an iteration is not recoverable by the next one. Stop.
            dirt = sh("git", "status", "--porcelain")[1]
            if dirt:
                notify("autoloop STOPPED — dirty tree",
                       f"After {issue.spec_id}:\n{dirt[:800]}", priority="urgent", tags="rotating_light")
                return 1
            if consecutive_failures >= 2:
                notify("autoloop STOPPED", "two consecutive failures", priority="high", tags="x")
                return 1

        notify("autoloop done", f"{len(shipped)} shipped\n" + "\n".join(shipped) if shipped
               else "autoloop done — nothing shipped", tags="checkered_flag")
        return 0
    finally:
        LOCK.unlink(missing_ok=True)
        if PY.exists():
            sh(str(PY), "tools/reap.py", "--kill", timeout=60)


if __name__ == "__main__":
    raise SystemExit(main())
