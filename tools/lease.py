#!/usr/bin/env python3
"""
Give a launched process a claim tools/reap.py can check before it decides to kill it.

Why this exists (PRC-07)
-------------------------
`tools/reap.py` used to classify a process as "ours" purely by command-line shape — any
Godot with `--headless`/`--fixed-fps`, any `Xvfb` on the known screen spec, any Python
naming this repo and one of its own tools. That cannot distinguish a leaked process from
one that is legitimately mid-capture right now, and it stopped being theoretical: seven
agents sharing this tree produced 6-8 processes belonging to OTHER agents mid-capture at
once, every one of which `reap.py --kill` would have killed. Agents were told by hand not
to run it — exactly the remembered rule decision 051 exists to replace with a script.

Lease file format
-------------------
One JSON file per *launch* — not strictly one per pid: the pid that owns a gate run and
the pid of a capture slot it briefly holds inside that run are the same process, so a
single pid can carry more than one lease at once, and pid-only filenames would collide
and let one lease's `__exit__` delete the other's file. Filenames are therefore
`.cache/leases/<pid>-<8 hex chars>.json`; every lookup keys off the `pid` *field* inside
the document, never the filename, so this is invisible to every caller. Gitignored by the
existing broad `/.cache/` rule in `.gitignore` — nothing here needs a new entry.

    {
      "pid": int,          # os.getpid() of the process HOLDING the lease — the Python
                            # launcher itself (tools/shot.py, tools/check.py, ...), not
                            # necessarily the Godot/Blender/Xvfb pid it goes on to spawn.
                            # Every subprocess a leased tool launches is a descendant of
                            # this pid, so tools/reap.py finds the lease covering a given
                            # stray by walking that stray's ppid chain upward until it
                            # hits a pid with a lease file (or pid 1). One lease, acquired
                            # once at the top of a tool's own run, therefore covers both
                            # that tool's own driver process (which matches reap.py's
                            # OUR_TOOLS python pattern on its own command line — a fact
                            # the pre-lease reaper never had to account for, because it
                            # only ever looked at command-line shape) and everything it
                            # spawns underneath it — including the Xvfb grandchild, which
                            # carries no repo path on its own command line at all and so
                            # has no other handle back to an owning session.
      "ppid": int,          # for diagnostics only; not used for matching
      "session_id": str,    # see session_id() below
      "agent": str,         # best-effort, informational label of the caller
      "tool": str,          # short name: "shot", "check", "check-capture",
                            # "test-parity", "blender-build", "sim-run", "sweep",
                            # "audio-serve"
      "argv": list[str],    # what this lease covers, for a human reading the report
      "started_at": float,  # unix time
      "expires_at": float,  # started_at + ttl_s — the crash backstop, see below
      "repo": str,          # str(REPO) — informational; LEASE_DIR is already scoped to
                            # one checkout, so this never drives a matching decision
      "capture": bool,      # true only for a bounded Godot/Xvfb frame capture — see
                            # "Capture serialisation" below
    }

TTL, not just clean exit
-------------------------
`acquire()` removes its lease file on exit, including on exception — but `SessionEnd`
does not fire when the CLI is killed outright (LF-062), so a lease that only ever
disappeared on a clean `__exit__` would leave a permanent "sibling" ghost the first time a
session is hard-killed mid-capture, which would make `reap.py --kill` exactly as useless
as before, just with extra steps between the symptom and the cause. So every lease also
carries `expires_at`, and `reap.py` treats a lease past its TTL as no better than no lease
at all: killable by anyone, reported as "expired" rather than protected forever. Pick a
`ttl_s` generous enough to cover the slowest real run of the wrapped tool (see each call
site for its own reasoning) — the TTL is a backstop against a crash, not a performance
budget, so erring long costs nothing but a slightly later cleanup of an already-dead
process.

Capture serialisation (LF-116)
-------------------------------
Measured live during the theatre-scale audit: one invisible Godot capture (native Linux
Godot under `Xvfb`, Mesa llvmpipe software GL) spreads across roughly 8 of this machine's
16 cores — one process was observed at 788% CPU. Three concurrent captures drove load
average to 18.6 and turned a nine-second capture into minutes, which is what made two
agents hit a command timeout and stall mid-task. A lease is the natural place to fix this
too, since every capture already has to go through this module: `acquire_capture()` bounds
the number of *live* capture leases to `MAX_CONCURRENT_CAPTURES`, and a caller that would
exceed it waits (polling, not blocking on a kernel primitive it would have to hold across
the wait — see below) instead of piling on top of whatever is already running.

Bounded to 2, not made fully exclusive (1): 2 concurrent captures is roughly 16 of this
machine's 16 cores — at the edge, but not what was measured to fail. 3 is exactly what was
measured to fail. If 2 still proves too many once more machines are measured, tighten
`MAX_CONCURRENT_CAPTURES` — the mechanism does not change. This is deliberately scoped to
the two launch sites that actually go through `Xvfb` (`tools/shot.py`, and `tools/check.py`
`run()`'s auto-detected `xvfb-run` invocations for `game renders`/`menu renders`/
`accessibility`) — `tools/blender/build.py`'s Blender renders are a separate, unmeasured
cost and are not folded into this cap.

Coordination for the slot count is a single lock file (`fcntl.flock`, POSIX) guarding a
count-and-write over the lease directory. Held only for the instant it takes to count
existing capture leases and, if a slot is free, write the new one — never across the wait
itself, so a process that dies while waiting for a slot cannot wedge anyone else, and a
process that dies holding the flock releases it the moment the OS reaps it (a POSIX flock
is tied to the file descriptor's owning process, not to a value that could be left
"locked" by a corpse).

Stdlib only, deliberately — the same property `tools/reap.py` holds itself to, and for the
same reason: this must stay importable from a bare `.venv` with nothing installed.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEASE_DIR = REPO / ".cache" / "leases"
CAPTURE_LOCK = LEASE_DIR / ".capture.lock"

## Measured LF-116: one capture spreads across ~8 of 16 cores; 2 concurrent fills the
## machine without oversubscribing it, 3 is what was measured to drive load average to
## 18.6 and cause real command timeouts. See "Capture serialisation" above.
MAX_CONCURRENT_CAPTURES = 2
CAPTURE_POLL_S = 2.0
## Generous: every capture this project runs (a single `--shot`, one of the gate's five
## rendered-check launches) completes in low tens of seconds: a slot has always freed well
## inside this window in everything measured so far.
CAPTURE_WAIT_TIMEOUT_S = 900.0


def session_id() -> str:
    """This process's session identity, for lease ownership.

    Probed on this machine (2026-07-30), not assumed: Claude Code exports
    `CLAUDE_CODE_SESSION_ID`. `CLAUDE_SESSION_ID` — guessed at when this issue was filed —
    is not what is actually exported here. A plain shell with no Claude Code in the loop at
    all has neither, so the fallback is the parent shell's pid: stable for that shell's
    lifetime, different from any other shell's, so a lease acquired by hand always has
    *some* owner rather than this raising.

    KNOWN LIMITATION, found live while wiring this up (not theoretical, not fixed here):
    `CLAUDE_CODE_SESSION_ID` is one value per TOP-LEVEL CLI session, not one per agent
    instance running inside it. Confirmed on this machine: while this file's own launch
    sites were being wired up, `tools/reap.py --json` (report-only, never `--kill`) showed
    a live `tools/test_parity.py` process and a batch of `godot --check-only` processes
    neither of which this session had started — both carrying THIS session's own
    `CLAUDE_CODE_SESSION_ID`, and `git status` at the same moment showed uncommitted edits
    to `scripts/*.gd` this session never made. The only explanation that fits: a sibling
    subagent of the *same* top-level orchestrator was working the tree concurrently, and
    its Bash-tool subprocesses inherit the same top-level session's environment. So
    "own-session" here means "this top-level CLI session", which can still contain more
    than one concurrently-active agent — plain `--kill` (no `--all` needed) can therefore
    still end a live sibling AGENT's process if it shares your top-level session, silently,
    with no warning the way `--all` prints one for a cross-session sibling. Narrowing this
    further needs a per-agent identifier this environment does not currently expose (see
    the PRC-07 report this was found under); tightening it to "own-session is never
    auto-killed" instead would break the exact leftover-cleanup case `SessionEnd` depends
    on, so this is left as a documented gap rather than papered over with an unverified fix.
    """
    env = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if env:
        return env
    return f"shell-{os.getppid()}"


def _agent_label() -> str:
    """Best-effort and informational ONLY — never consulted for a kill/spare decision,
    only printed in a report. `AI_AGENT` is what this machine's harness exports; a bare
    shell with nothing setting it is reported as 'human'."""
    return os.environ.get("AI_AGENT", "human")


def _write(pid: int, tool: str, argv: list[str], ttl_s: float, capture: bool) -> Path:
    LEASE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    doc = {
        "pid": pid,
        "ppid": os.getppid(),
        "session_id": session_id(),
        "agent": _agent_label(),
        "tool": tool,
        "argv": [str(a) for a in argv],
        "started_at": now,
        "expires_at": now + ttl_s,
        "repo": str(REPO),
        "capture": bool(capture),
    }
    # Unique per launch, not per pid — see module docstring. `os.getpid()` alone would
    # collide the moment one process holds an ordinary lease and a nested capture lease
    # at once, and the inner one's __exit__ would delete the outer one's file early.
    path = LEASE_DIR / f"{pid}-{os.urandom(4).hex()}.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


@contextlib.contextmanager
def acquire(tool: str, argv: list[str], ttl_s: float):
    """Hold a lease for the lifetime of the `with` block, filed under the CALLING
    process's own pid. Written on entry, removed on exit — including on exception, so an
    ordinary Python traceback out of the wrapped block never leaves a lease behind (only a
    hard kill of this process does, which is exactly what `expires_at` exists for).

    Wrap the whole span that owns the subprocess tree you don't want reaped out from under
    you — not just one `subprocess.run()` call. A tool's own driver process matches
    `reap.py`'s command-line patterns too (it names this repo and one of `OUR_TOOLS`), so
    leasing only a child and not the parent still leaves the parent itself killable.
    """
    path = _write(os.getpid(), tool, argv, ttl_s, capture=False)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _live_captures() -> list[dict]:
    """Every currently valid (process alive, not expired) capture lease. Garbage-collects
    dead ones in passing, since this already has to read the whole directory under the
    lock — no reason to make a second pass do it again."""
    out = []
    for doc, path in _read_all():
        if not doc.get("capture"):
            continue
        if _pid_alive(doc.get("pid", -1)) and doc.get("expires_at", 0) > time.time():
            out.append(doc)
        else:
            path.unlink(missing_ok=True)
    return out


@contextlib.contextmanager
def acquire_capture(tool: str, argv: list[str], ttl_s: float,
                     max_concurrent: int = MAX_CONCURRENT_CAPTURES,
                     poll_s: float = CAPTURE_POLL_S,
                     wait_timeout_s: float = CAPTURE_WAIT_TIMEOUT_S):
    """Same as `acquire()`, plus: wait for one of `max_concurrent` capture slots to be
    free before writing the lease. See module docstring, "Capture serialisation (LF-116)".

    Raises `TimeoutError` if no slot frees within `wait_timeout_s` — a caller should
    report that as "waited for a capture slot and gave up", not let it read as an
    ordinary Godot-hung-and-timed-out failure; the two mean different things.
    """
    import fcntl  # POSIX only, deferred so importing this module never requires it

    LEASE_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_timeout_s
    path: Path | None = None
    while path is None:
        with open(CAPTURE_LOCK, "a+") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                if len(_live_captures()) < max_concurrent:
                    path = _write(os.getpid(), tool, argv, ttl_s, capture=True)
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)
        if path is not None:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"no capture slot freed within {wait_timeout_s:.0f}s "
                f"({max_concurrent} already active) — see tools/lease.py")
        time.sleep(poll_s)

    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    """True if `pid` names a live process. A `PermissionError` (a process that exists but
    is not ours to signal) counts as alive — gc must never delete a lease just because we
    cannot prove the process behind it is dead."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _read_all() -> list[tuple[dict, Path]]:
    if not LEASE_DIR.exists():
        return []
    out = []
    for p in sorted(LEASE_DIR.glob("*.json")):
        try:
            out.append((json.loads(p.read_text()), p))
        except (json.JSONDecodeError, OSError):
            # An unreadable lease is not a lease anyone can trust; drop it rather than let
            # a half-written file (a crash mid-`write_text`) wedge every future reader.
            p.unlink(missing_ok=True)
    return out


def gc_stale() -> int:
    """Remove every lease file whose pid no longer exists. Returns the count removed.
    Safe (and meant) to call on every `tools/reap.py` invocation, not just a dedicated
    sweep — it is cheap, and it is what keeps `ls .cache/leases` empty after a clean
    session rather than accumulating one ghost file per exited process forever."""
    removed = 0
    for doc, path in _read_all():
        if not _pid_alive(doc.get("pid", -1)):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def leases_by_pid() -> dict[int, list[dict]]:
    """Every lease currently on disk, grouped by the pid field inside it (not the
    filename — see module docstring on why a pid can hold more than one lease)."""
    out: dict[int, list[dict]] = {}
    for doc, _path in _read_all():
        pid = doc.get("pid")
        if isinstance(pid, int):
            out.setdefault(pid, []).append(doc)
    return out


def find_owner(pid: int, ppid_of: dict[int, int], by_pid: dict[int, list[dict]],
               max_hops: int = 12) -> dict | None:
    """Walk `pid`'s ancestor chain (via `ppid_of`, a pid->ppid map built from one `ps`
    snapshot) until a pid holding a lease is found, or the chain runs out. This is how a
    lease acquired once, in a tool's own driver process, is found for every descendant it
    spawns — including a grandchild like `Xvfb` that carries no repo path on its own
    command line and so has no other handle back to an owning session (see module
    docstring's `pid` field entry).

    When the resolved pid holds more than one lease (an ordinary "ownership" lease plus a
    nested capture lease, say), the one with the furthest-out `expires_at` is returned —
    both come from the same process and therefore the same `session_id`, so this only
    changes the expiry judgement, and "covered as long as any of this pid's leases hasn't
    expired" is the right reading of overlapping leases on one process.
    """
    seen: set[int] = set()
    cur = pid
    for _ in range(max_hops):
        docs = by_pid.get(cur)
        if docs:
            return max(docs, key=lambda d: d.get("expires_at", 0.0))
        if cur in seen or cur <= 1:
            return None
        seen.add(cur)
        nxt = ppid_of.get(cur)
        if nxt is None:
            return None
        cur = nxt
    return None
