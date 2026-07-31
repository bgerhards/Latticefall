#!/usr/bin/env python3
"""
The hook set. One entry point, dispatching on the Claude Code hook event read from stdin,
so every mechanically-enforced working-agreement rule lives in one auditable, testable file
instead of five prose paragraphs someone has to remember (PRC-06, decision 051).

The evidence this exists to act on, from one session of several parallel agents: five
backgrounded a long run and orphaned it; one blanked the owner's running game by rebuilding
the import cache (LF-075); a `git stash` swept eleven files across five workstreams and left
every other agent silently reading `HEAD` instead of its own edits; and a bypassed
`tools/toolpaths.py` once made the Blender pipeline process nothing at all, silently.
None of those needed a human to remember a rule — every one is visible at the tool-call
boundary, which is what this file reads.

Protocol (Claude Code's PreToolUse/PostToolUse "blocking error" exit-code contract):

    exit 0   allow  — nothing printed unless there is a warning worth surfacing on stderr.
    exit 1   warn   — the tool call proceeds; stderr is shown to Claude in the same turn.
    exit 2   deny   — PreToolUse: the tool call is blocked before it runs.
                       PostToolUse: the edit already happened (this cannot undo it) — exit 2
                       here means "surface this diagnostic prominently", not "blocked".

**Live firing of every hook below is UNPROVEN, not just untested.** LF-112 found the
PostToolUse parse-check hook already in `.claude/settings.json` was never observed firing
after being added mid-session, probably because settings.json is read once at session start.
This was re-confirmed directly while writing this file: a temporary debug `PreToolUse` hook
was added to `.claude/settings.json` mid-session, a plain `Bash` call was made, and the
hook's own stdout-capture file never appeared — then removed again before this file was
written. So every case below is verified by **direct invocation** (`--selftest`, or piping a
real JSON payload to this script by hand) — never by provoking the hook live in this
session. A fresh session is what would prove or disprove real firing; say so plainly rather
than claiming it works because the logic is right.

**Coordinator vs. subagent is not a distinction this file can make reliably, and it does not
try to.** `tools/lease.py`'s `session_id()` already established (LF-133) that
`CLAUDE_CODE_SESSION_ID` is one value per top-level CLI session, shared by every subagent it
fans out — there is no environment signal in this project that tells a subagent's tool call
apart from the coordinator's. Rather than guess at an unverified `agent_id` field, every rule
below is designed so the coordinator's own legitimate needs never collide with a blanket
rule in the first place:
  - `.godot/` writes, backgrounding a long run, and a raw engine invocation: neither role
    legitimately needs to break these, so both are held to the same rule, each with a
    documented, visible escape hatch for a genuine, deliberate exception.
  - git safety (stash/reset/checkout--/add): denied for anyone. The coordinator's sanctioned
    commit path is `git commit -- <paths>` (pathspec form), which needs none of the four.
  - The one place role actually matters — `tools/reap.py --kill` at subagent wrap-up vs. at
    session wrap — is handled by NOT denying anything at `SubagentStop` (see below), which is
    the "prefer warning over denying" instruction this issue was given when a distinction
    cannot be made reliably.

**`SubagentStop` is report-only, not `--kill`, and that is a deliberate deviation from the
issue's literal task text.** The issue calls for
`tools/reap.py --kill --quiet --lease <session>` at `SubagentStop`, scoped by PRC-07. But
`tools/reap.py` (verified by reading its current `argparse` setup — file is out of this
issue's ownership, so it was read, not touched) has no `--lease` flag; PRC-07 added the
lease *mechanism* (`tools/lease.py`, `find_owner()`) but never a CLI switch to scope a kill to
one subagent's descendants specifically. Without it, `--kill` at `SubagentStop` would kill
every `own-session` stray, which — per `tools/reap.py`'s own module docstring and LF-133 —
still includes every *sibling* subagent sharing this top-level session, exactly the failure
this issue exists to prevent. So this file runs `tools/reap.py --json` (report-only) at
`SubagentStop`, same interim shape `nightly.yml` already uses for the same reason (LF-120),
and warns rather than acts. The real scoped kill stays where CLAUDE.md already puts it: the
coordinator, at wrap, after every agent has reported.

    .venv/bin/python tools/hooks/guard.py --selftest      # the whole rule table, both ways
    echo '{"hook_event_name": "PreToolUse", ...}' | .venv/bin/python tools/hooks/guard.py
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Decision:
    level: str          # "allow" | "warn" | "deny"
    message: str = ""
    rule: str = "none"


_SEVERITY = {"allow": 0, "warn": 1, "deny": 2}
_EXIT_CODE = {"allow": 0, "warn": 1, "deny": 2}


# ───────────────────────────────────────────────────────────── payload helpers ──

def _tool_input(payload: dict) -> dict:
    ti = payload.get("tool_input")
    return ti if isinstance(ti, dict) else {}


def _command(payload: dict) -> str:
    return str(_tool_input(payload).get("command", ""))


def _file_path(payload: dict) -> str:
    return str(_tool_input(payload).get("file_path", ""))


def _segments(command: str) -> list[str]:
    """Split a shell command into pipeline stages on `;`, `&`, `|`, `&&`, `||`.

    Not a real shell parser — a command hidden inside `$(...)` or backticks is invisible to
    this, same as any regex-based approach. That is a real, documented limitation (see
    `rule_git_safety`'s docstring): it catches the ordinary, accidental case this issue is
    about, not a deliberately obfuscated one.
    """
    return [s.strip() for s in re.split(r"&&|\|\||[;|&]", command) if s.strip()]


def _leading_argv0(segment: str) -> str:
    """The executable a shell segment would run, skipping any `FOO=bar` env-assignment
    prefix (e.g. `LF_ALLOW_RAW_ENGINE=1 blender ...` -> `blender`)."""
    tokens = segment.split()
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    return tokens[i] if i < len(tokens) else ""


# ───────────────────────────────────────────────────────────── rule: .godot writes ──

MSG_GODOT_WRITE = (
    "Denied: write/move/delete under .godot/ (LF-075). The owner plays out of this same "
    "working tree — rebuilding or moving the import cache pulls every texture out from "
    "under a running game; the menu still draws (plain UI) but the level comes up blank, "
    "which reads exactly like a code regression and cost a full diagnosis pass last time. "
    "The supported path is an in-place reimport (`--headless --path . --import`, already "
    "wired into tools/shot.py and tools/check.py via toolpaths.godot_argv()) — import in "
    "place, and say so first."
)

# A ".godot" path component: preceded by start-of-string, whitespace, a quote or a slash;
# followed by a slash, end-of-string, whitespace or a quote. Matches both a file_path value
# (".godot/imported/x.ctex") and a Bash command ("rm -rf .godot", "mv .godot .godot.bak").
_GODOT_PATH_RE = re.compile(r"""(?:^|[\s"'/])\.godot(?:[\s"'/]|$)""")
_GODOT_WRITE_VERB_RE = re.compile(r"\b(rm|mv|cp|rsync|truncate|dd|tee)\b")


def rule_godot_write(payload: dict) -> Decision | None:
    tool = payload.get("tool_name")
    if tool in ("Write", "Edit"):
        if _GODOT_PATH_RE.search(_file_path(payload)):
            return Decision("deny", MSG_GODOT_WRITE, "godot-write")
        return None
    if tool == "Bash":
        cmd = _command(payload)
        if not _GODOT_PATH_RE.search(cmd):
            return None
        if _GODOT_WRITE_VERB_RE.search(cmd) or ">" in cmd:
            return Decision("deny", MSG_GODOT_WRITE, "godot-write")
        return None
    return None


# ───────────────────────────────────────────────────────────── rule: backgrounding ──

MSG_BACKGROUND_DENY = (
    "Denied: backgrounding tools/check.py, tools/test_parity.py, sim/run.py, "
    "tools/sweep.py, tools/audio/serve.py, tools/shot.py or blender (run_in_background, a "
    "trailing `&`, or `nohup`). A backgrounded process the harness still tracks re-invokes "
    "the model when it exits, billing a session everyone believed was over — five agents "
    "hit this in one session. Run it in the foreground with an explicit `timeout` (ms, up "
    "to 600000). If this genuinely needs to run concurrently, prefix the command with "
    "LF_ALLOW_BACKGROUND=1 to opt in deliberately."
)
MSG_BACKGROUND_WARN = (
    "Warning: this names a command measured to run long (tools/check.py, test_parity.py, "
    "sim/run.py, sweep.py, audio/serve.py, shot.py, blender) with no explicit `timeout` "
    "set. The Bash tool's 120s default does not fail a slow command, it backgrounds it — "
    "pass `timeout` (ms, up to 600000) explicitly."
)

_LONG_CMD_RE = re.compile(
    r"\b(tools/check\.py|tools/test_parity\.py|sim/run\.py|tools/sweep\.py|"
    r"tools/audio/serve\.py|tools/shot\.py|blender)\b"
)
_TRAILING_AMP_RE = re.compile(r"(?<!&)&(?!&)\s*$")
_NOHUP_RE = re.compile(r"\bnohup\b")
_ALLOW_BACKGROUND_MARK = "LF_ALLOW_BACKGROUND=1"


def rule_background(payload: dict) -> Decision | None:
    if payload.get("tool_name") != "Bash":
        return None
    ti = _tool_input(payload)
    cmd = str(ti.get("command", ""))
    if not _LONG_CMD_RE.search(cmd):
        return None
    if _ALLOW_BACKGROUND_MARK in cmd:
        return None
    backgrounded = (bool(ti.get("run_in_background"))
                    or bool(_TRAILING_AMP_RE.search(cmd.rstrip()))
                    or bool(_NOHUP_RE.search(cmd)))
    if backgrounded:
        return Decision("deny", MSG_BACKGROUND_DENY, "background")
    if not ti.get("timeout"):
        return Decision("warn", MSG_BACKGROUND_WARN, "background-no-timeout")
    return None


# ───────────────────────────────────────────────────────────── rule: raw engine argv ──

MSG_RAW_ENGINE = (
    "Denied: {hit}, bypassing tools/toolpaths.py. That skips Xvfb wrapping (decision 052) "
    "and the WSL path translation host_path_for() performs — the last bypass silently "
    "processed nothing (docs/STATE.md: 'the Blender pipeline had never been run on this "
    "machine and was broken two ways'). Go through tools/shot.py, tools/check.py, "
    "tools/test_parity.py, or tools/blender/*.py instead. A deliberate probe: prefix the "
    "command with LF_ALLOW_RAW_ENGINE=1."
)

_ALLOW_RAW_ENGINE_MARK = "LF_ALLOW_RAW_ENGINE=1"


def _raw_engine_hit(command: str) -> str | None:
    """What the command invokes directly, or None if nothing matches. Distinctive filename
    substrings are checked against the whole command (safe: they don't occur incidentally);
    the bare tokens "godot"/"godot4"/"blender" are checked only in the argv0 position of a
    shell segment, so a path reference like `tools/blender/render.py` (an argument, not an
    executable) is never a false hit."""
    if re.search(r"(?i)godot_v[\d.]", command):
        return "a Godot binary by its versioned filename"
    if "/Applications/Godot" in command or re.search(r"(?i)/godot\.app/", command):
        return "the macOS Godot.app bundle"
    if "/Applications/Blender" in command or re.search(r"(?i)/blender\.app/", command):
        return "the macOS Blender.app bundle"
    if re.search(r"(?i)blender\.exe\b", command):
        return "a Blender .exe"
    if re.search(r"(?i)blender foundation", command):
        return "a Blender install path"
    if re.search(r"/mnt/[^\s]*godot[^\s]*", command, re.I):
        return "a Godot install path under /mnt"
    for seg in _segments(command):
        base = _leading_argv0(seg).rsplit("/", 1)[-1].lower()
        if base in ("godot", "godot4", "blender"):
            return f"`{base}` invoked directly"
    return None


def rule_raw_engine(payload: dict) -> Decision | None:
    if payload.get("tool_name") != "Bash":
        return None
    cmd = _command(payload)
    if _ALLOW_RAW_ENGINE_MARK in cmd:
        return None
    hit = _raw_engine_hit(cmd)
    if hit is None:
        return None
    if "godot" in hit.lower() and "--headless" in cmd and "--import" in cmd:
        # CLAUDE.md's own sanctioned exception: an in-place reimport after a re-render is
        # not something tools/shot.py wraps, so a raw invocation is the documented path.
        return None
    return Decision("deny", MSG_RAW_ENGINE.format(hit=hit), "raw-engine")


# ───────────────────────────────────────────────────────────── rule: git safety ──

MSG_GIT_SAFETY = (
    "Denied: `git {verb}` in a tree several agents share. A stash once swept eleven files "
    "across five workstreams and left the tree at HEAD; every other agent then silently "
    "read HEAD instead of its own edits, which does not error — it just makes every "
    "measurement in that window describe code nobody wrote. Use `git show HEAD:<path>` to "
    "read committed content. A coordinator commits with `git commit -- <paths>` (pathspec "
    "form), which needs none of stash/reset/checkout--/add."
)

_GIT_DENY_SUBCOMMANDS = {"stash", "add", "reset"}


def _git_checkout_is_destructive(args: list[str]) -> bool:
    if not args:
        return False
    if args[0] in (".", "--"):
        return True
    return "--" in args


def rule_git_safety(payload: dict) -> Decision | None:
    """Deny `git stash`, `git add`, `git reset` (any form) and the file-discarding forms of
    `git checkout` (`checkout --`, `checkout .`) — exactly the four verbs CLAUDE.md already
    names as never-run-by-an-agent. `git checkout <branch>` (switching branches) is left
    alone; it is not one of the four and is an ordinary operation. Scoped deliberately to
    just these four, not the wider destructive-op list (`clean -f`, `branch -D`, `restore`)
    the top-level Bash tool guidance already covers by its own judgement — widening this
    hook past what the issue actually named would be scope creep in a safety-critical file.

    Not a full shell parser (see `_segments`): a git verb hidden inside `$(...)` or a
    backtick substitution is invisible to this, same limitation as every rule in this file
    that reasons about shell text rather than executing a real parser. That is an
    acceptable gap for what this guards against — an accidental `git stash`, not a
    deliberately obfuscated one.
    """
    if payload.get("tool_name") != "Bash":
        return None
    cmd = _command(payload)
    for seg in _segments(cmd):
        try:
            tokens = shlex.split(seg)
        except ValueError:
            tokens = seg.split()
        i = 0
        while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
            i += 1
        tokens = tokens[i:]
        if len(tokens) < 2:
            continue
        argv0 = tokens[0].rsplit("/", 1)[-1]
        if argv0 != "git":
            continue
        subcmd = tokens[1]
        if subcmd in _GIT_DENY_SUBCOMMANDS:
            return Decision("deny", MSG_GIT_SAFETY.format(verb=subcmd), "git-safety")
        if subcmd == "checkout" and _git_checkout_is_destructive(tokens[2:]):
            return Decision("deny", MSG_GIT_SAFETY.format(verb="checkout --"), "git-safety")
    return None


PRE_RULES = [rule_godot_write, rule_background, rule_raw_engine, rule_git_safety]


# ───────────────────────────────────────────────────────────── PostToolUse: lint ──

def _evaluate_post_tool_use(payload: dict) -> Decision:
    """On Edit|Write of a `.gd` file, run PRC-01's single-file parse check; on `.py`, run
    `python -m py_compile`. Both are meant to be sub-second per file — the parse check
    spawns one headless Godot `--check-only` run, which is the slow part.

    PostToolUse's exit-2 contract means "the edit already happened, surface this
    diagnostic prominently" — not "blocked" — see this module's docstring.
    """
    fp = _file_path(payload)
    if not fp:
        return Decision("allow", "", "post-lint-na")
    path = Path(fp)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return Decision("allow", "", "post-lint-missing")

    if fp.endswith(".gd"):
        gd_dir = str(ROOT / "tools" / "validate")
        if gd_dir not in sys.path:
            sys.path.insert(0, gd_dir)
        try:
            import gdscript  # type: ignore  # noqa: PLC0415
        except Exception as exc:  # defensive: never let a broken import block an edit
            return Decision("warn", f"could not run gdscript parse check: {exc}",
                            "post-lint-gd-error")
        diags = gdscript.check_file(path)
        if diags:
            return Decision("deny", "Parse error:\n" + "\n".join(diags), "post-lint-gd")
        return Decision("allow", "", "post-lint-gd-clean")

    if fp.endswith(".py"):
        r = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip()
            return Decision("deny", f"py_compile failed:\n{tail}", "post-lint-py")
        return Decision("allow", "", "post-lint-py-clean")

    return Decision("allow", "", "post-lint-other")


# ───────────────────────────────────────────────────────────── SubagentStop: reap ──

def _evaluate_subagent_stop(payload: dict) -> Decision:
    """Report-only. See this module's docstring for why this never runs `--kill`."""
    reap = ROOT / "tools" / "reap.py"
    if not reap.exists():
        return Decision("allow", "", "subagent-stop-no-reap")
    try:
        r = subprocess.run([sys.executable, str(reap), "--json"],
                           capture_output=True, text=True, cwd=str(ROOT), timeout=20)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return Decision("warn", f"tools/reap.py --json failed to run: {exc}",
                        "subagent-stop-error")
    try:
        doc = json.loads(r.stdout)
    except json.JSONDecodeError:
        return Decision("warn", "tools/reap.py --json produced no parseable report",
                        "subagent-stop-error")
    found = doc.get("found", [])
    if not found:
        return Decision("allow", "", "subagent-stop-clean")
    killable = [p for p in found
                if p.get("status") in ("orphan", "expired", "own-session", "unleased")]
    msg = (
        f"{len(found)} stray Latticefall process(es) at subagent stop "
        f"({len(killable)} killable). NOT auto-killed: tools/reap.py has no --lease flag to "
        "scope a kill to just this finishing subagent, and CLAUDE_CODE_SESSION_ID is shared "
        "across the whole top-level session (LF-133) — an unscoped --kill here could end a "
        "sibling still working. The coordinator runs the real, scoped kill at wrap, after "
        "every agent has reported (tools/reap.py --kill --quiet, already wired to "
        "SessionEnd)."
    )
    return Decision("warn", msg, "subagent-stop-report")


# ───────────────────────────────────────────────────────────── dispatch ──

def _worst(decisions: list[Decision | None]) -> Decision:
    real = [d for d in decisions if d is not None]
    if not real:
        return Decision("allow", "", "none")
    return max(real, key=lambda d: _SEVERITY[d.level])


def evaluate(payload: dict) -> Decision:
    event = payload.get("hook_event_name")
    if event == "PreToolUse":
        return _worst([r(payload) for r in PRE_RULES])
    if event == "PostToolUse":
        return _evaluate_post_tool_use(payload)
    if event == "SubagentStop":
        return _evaluate_subagent_stop(payload)
    return Decision("allow", "", f"unhandled-event:{event}")


# ───────────────────────────────────────────────────────────── --selftest ──

def _selftest() -> int:
    """(event, payload, expected) covering every rule in both directions, run directly
    against `evaluate()` — no harness involved. `expected` is "allow"/"warn"/"deny", or the
    sentinel "not-deny" for the one case (SubagentStop) whose exact level depends on
    whatever stray processes happen to exist on this machine right now.
    """
    cache = ROOT / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    broken_gd = cache / "hook_selftest_broken.gd"
    broken_py = cache / "hook_selftest_broken.py"
    broken_gd.write_text("func _ready():\n\tvar x := \n")
    broken_py.write_text("def f(:\n    pass\n")

    def bash(command: str, **extra) -> dict:
        ti = {"command": command, **extra}
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": ti}

    def write(file_path: str, tool: str = "Write") -> dict:
        return {"hook_event_name": "PreToolUse", "tool_name": tool,
                "tool_input": {"file_path": file_path}}

    def post(file_path: str, tool: str = "Edit") -> dict:
        return {"hook_event_name": "PostToolUse", "tool_name": tool,
                "tool_input": {"file_path": file_path}}

    cases: list[tuple[str, dict, str]] = [
        ("godot-write: Write to .godot/ denied", write(".godot/imported/foo.ctex"), "deny"),
        ("godot-write: Write to scripts/ allowed", write("scripts/hud.gd"), "allow"),
        ("godot-write: rm -rf .godot denied", bash("rm -rf .godot"), "deny"),
        ("godot-write: reading .godot cache allowed",
         bash("cat .godot/global_script_class_cache.cfg"), "allow"),
        ("godot-write: sanctioned reimport allowed (raw-engine exception)",
         bash("/Applications/Godot.app/Contents/MacOS/Godot --headless --path . --import"),
         "allow"),

        ("background: run_in_background on check.py denied",
         bash("tools/check.py", run_in_background=True), "deny"),
        ("background: foreground check.py with timeout allowed",
         bash("tools/check.py --tier 1", run_in_background=False, timeout=600000), "allow"),
        ("background: long command with no timeout warns",
         bash("tools/test_parity.py", run_in_background=False), "warn"),
        ("background: trailing & on shot.py denied",
         bash("tools/shot.py anchor-01 --out /tmp/x.png &"), "deny"),
        ("background: nohup on sweep.py denied",
         bash("nohup tools/sweep.py anchor-20 --jobs 8"), "deny"),
        ("background: escape hatch allows deliberate background",
         bash("LF_ALLOW_BACKGROUND=1 tools/audio/serve.py", run_in_background=True), "allow"),
        ("background: unrelated command with run_in_background is untouched",
         bash("sleep 300", run_in_background=True), "allow"),

        ("raw-engine: bare blender invocation denied",
         bash("blender -b --python tools/blender/render.py -- --only pulse_turret"), "deny"),
        ("raw-engine: through tools/blender/build.py allowed",
         bash(".venv/bin/python tools/blender/build.py", timeout=600000), "allow"),
        ("raw-engine: escape hatch allows a deliberate probe",
         bash("LF_ALLOW_RAW_ENGINE=1 blender -b --python probe.py", timeout=600000), "allow"),
        ("raw-engine: direct linux Godot binary denied",
         bash("/mnt/d/godot/linux/Godot_v4.7.1-stable_linux.x86_64 --headless --autoplay "
              "--anchor anchor-01"), "deny"),
        ("raw-engine: through tools/shot.py allowed",
         bash(".venv/bin/python tools/shot.py anchor-01 --out /tmp/s.png", timeout=600000),
         "allow"),

        ("git-safety: git stash denied", bash("git stash"), "deny"),
        ("git-safety: git add denied", bash("git add scripts/hud.gd"), "deny"),
        ("git-safety: git reset denied", bash("git reset --hard HEAD"), "deny"),
        ("git-safety: git checkout -- <path> denied",
         bash("git checkout -- scripts/hud.gd"), "deny"),
        ("git-safety: git checkout . denied", bash("git checkout ."), "deny"),
        ("git-safety: git checkout <branch> allowed", bash("git checkout main"), "allow"),
        ("git-safety: git status allowed", bash("git status"), "allow"),
        ("git-safety: coordinator commit via pathspec allowed",
         bash("git commit -m msg -- scripts/hud.gd"), "allow"),

        ("post-lint: clean .gd allowed", post("scripts/hud.gd"), "allow"),
        ("post-lint: broken .gd denied",
         post(".cache/hook_selftest_broken.gd", tool="Write"), "deny"),
        ("post-lint: clean .py allowed", post("tools/lease.py"), "allow"),
        ("post-lint: broken .py denied",
         post(".cache/hook_selftest_broken.py", tool="Write"), "deny"),
        ("post-lint: non-code file untouched", post("data/towers.json"), "allow"),

        ("subagent-stop: never denies",
         {"hook_event_name": "SubagentStop", "agent_id": "fake",
          "agent_type": "general-purpose"}, "not-deny"),
    ]

    passed = failed = 0
    for name, payload, expected in cases:
        decision = evaluate(payload)
        ok = (decision.level != "deny") if expected == "not-deny" else (decision.level == expected)
        print(f"[{'ok' if ok else 'FAIL':4}] {name}: got {decision.level!r}"
              + ("" if ok else f" — expected {expected!r}")
              + f"  (rule={decision.rule})")
        passed += ok
        failed += not ok

    broken_gd.unlink(missing_ok=True)
    broken_py.unlink(missing_ok=True)

    print(f"\n{passed} passed, {failed} failed, {len(cases)} total")
    return 1 if failed else 0


# ───────────────────────────────────────────────────────────── main ──

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in rule table against evaluate() directly and "
                         "report pass/fail, without the harness")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # A malformed hook payload is not evidence of anything a rule should act on — fail
        # open rather than block a tool call on a stdin parse failure that has nothing to do
        # with the tool call itself.
        return 0

    decision = evaluate(payload)
    if decision.message:
        print(decision.message, file=sys.stderr)
    return _EXIT_CODE[decision.level]


if __name__ == "__main__":
    sys.exit(main())
