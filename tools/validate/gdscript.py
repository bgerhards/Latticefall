#!/usr/bin/env python3
"""
Parse-check every tracked GDScript file, in isolation, headless.

A GDScript parse error in this project is never an error at the failure site — it is a
hang or a blank frame (`docs/STATE.md`, "Traps that have already cost time"). `var x :=
node.method()` on an untyped receiver cannot be inferred; that is a *parse* failure, the
whole script fails to load, Godot downgrades the node to a scriptless base class, and the
cascading errors name files that are not broken. This cost time five separate times in one
session, in five different files (LF-055, decision 055, LF-063); once `main.gd` failed to
parse and the game hung on the menu with no output at all. `godot boots` (in `tools/
check.py`) only greps stdout for `SCRIPT ERROR`/`Parse Error` from whatever the *main
scene* happens to load, so a script nothing loads at boot — `draft.gd`, `options_menu.gd`,
`scripts/test/*.gd` — is never parsed at all. This module parses every one of them.

`--headless --check-only --script <f>` loads exactly one script and reports parse/compile
errors on stderr with a non-zero exit — probed against the installed Godot 4.7.1 on this
machine (not assumed):

    clean file:                  exit 0, empty stderr
    real parse error:            exit 1, "SCRIPT ERROR: Parse Error: ..." + a
                                  "at: GDScript::reload (res://path:line)" line
    reference to an autoload:    exit 1, "SCRIPT ERROR: Compile Error: Identifier not
                                  found: <Name>" — because a script checked in isolation has
                                  no autoload singletons registered, so every legitimate
                                  reference to Content, Audio, Sprites, Display, Progress,
                                  Recoveries, Ui or Tuning looks exactly like a typo unless
                                  filtered by name.

The autoload names are parsed out of `project.godot`'s `[autoload]` section rather than
hardcoded, so a new autoload does not need a matching change here. Only a diagnostic whose
identifier is *exactly* an autoload name is dropped — a different unknown identifier in the
same file, or a real typo of an autoload name (which will not match by construction), is
kept. A blanket "ignore identifier errors" filter would swallow the exact class of typo this
check exists to catch.

Also checked, deliberately: `scripts/test/facing.gd`, `scripts/test/parity.gd` (its parse
failure would turn the nine-minute `rules parity` check into an opaque "godot produced no
parity output"), and `tools/godot/setup_input.gd`. Not checked: anything under `addons/` —
third-party code this project does not own or edit.

This is a parse check, not a lint, and it does not catch a `class_name` the editor has not
yet imported — that failure is also a hang, but for a different reason (the global class
cache has no entry, per CLAUDE.md), and `--check-only` in isolation cannot see it. A clean
run here is not proof of that.

    .venv/bin/python tools/validate/gdscript.py              # every tracked script
    .venv/bin/python tools/validate/gdscript.py scripts/hud.gd scripts/iso.gd
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import toolpaths                                              # noqa: E402

PROJECT_GODOT = ROOT / "project.godot"

# scripts/test/*.gd and tools/godot/setup_input.gd are not under a path git ls-files '*.gd'
# would ever miss — they're included by the plain glob below — this constant exists only to
# document that their inclusion is deliberate, not an oversight. See module docstring.
DELIBERATELY_INCLUDED = ("scripts/test/facing.gd", "scripts/test/parity.gd",
                         "tools/godot/setup_input.gd")

_AUTOLOAD_LINE = re.compile(r'^(\w+)\s*=\s*"\*?res://')
_LOC = re.compile(r'at: GDScript::reload \((res://[^:]+):(\d+)\)')
_IDENT_NOT_FOUND = re.compile(r'Identifier not found: (\w+)')
_IDENT_NOT_DECLARED = re.compile(r'Identifier "(\w+)" not (?:found|declared in the current scope)')


def parse_project_autoloads(project_godot: Path) -> set[str]:
    """The `[autoload]` section of `project.godot`, as a set of singleton names.

    A script checked with `--check-only --script <f>` has none of these registered, so every
    legitimate reference to one reports a spurious "Identifier not found" that `check_file`
    must filter against this set.
    """
    names: set[str] = set()
    in_section = False
    for line in project_godot.read_text().splitlines():
        s = line.strip()
        if s == "[autoload]":
            in_section = True
            continue
        if s.startswith("[") and s.endswith("]"):
            in_section = False
            continue
        if in_section:
            m = _AUTOLOAD_LINE.match(s)
            if m:
                names.add(m.group(1))
    return names


def tracked_gd_files() -> list[Path]:
    """Every `.gd` file this repo tracks, excluding `addons/` — third-party code this
    project does not own. Absolute paths under ROOT.
    """
    r = subprocess.run(["git", "ls-files", "-z", "--", "*.gd"],
                       capture_output=True, text=True, cwd=str(ROOT))
    rels = [p for p in r.stdout.split("\0") if p and not p.startswith("addons/")]
    return sorted(ROOT / p for p in rels)


def build_argv(rel: Path) -> list[str]:
    """The Godot argv that parse-checks one script in isolation.

    `--headless` never opens a window on any build, so `want_window=True` here just means
    "do not bother wrapping in Xvfb" — there is nothing here for it to hide, exactly like
    `check_godot_boots` and `test_parity.py`'s `run_godot`.
    """
    res_path = "res://" + str(rel).replace("\\", "/")
    return toolpaths.godot_argv(ROOT, ["--headless", "--check-only", "--script", res_path],
                                want_window=True)


def _script_errors(stderr: str) -> list[tuple[str, str | None, int | None]]:
    """Every `SCRIPT ERROR: ...` block in check-only's stderr, as (message, file, line)."""
    lines = stderr.splitlines()
    out: list[tuple[str, str | None, int | None]] = []
    for i, line in enumerate(lines):
        if not line.startswith("SCRIPT ERROR: "):
            continue
        message = line[len("SCRIPT ERROR: "):]
        file = lineno = None
        if i + 1 < len(lines):
            m = _LOC.search(lines[i + 1])
            if m:
                file, lineno = m.group(1), int(m.group(2))
        out.append((message, file, lineno))
    return out


def _is_autoload_noise(message: str, autoloads: set[str]) -> bool:
    m = _IDENT_NOT_FOUND.search(message) or _IDENT_NOT_DECLARED.search(message)
    return bool(m and m.group(1) in autoloads)


def _diagnostics_from_result(r: subprocess.CompletedProcess, autoloads: set[str],
                             rel: Path) -> list[str]:
    """Turn one check-only run's result into real diagnostics, dropping autoload noise.

    Returns `[]` for a clean exit *and* for an exit whose only complaints were autoload
    references — those are indistinguishable from "clean" to everything downstream, which is
    the whole point: a file that references only autoloads must parse clean, no noise.
    """
    if r.returncode == 0:
        return []
    blocks = _script_errors(r.stderr)
    if not blocks:
        # Non-zero exit, but nothing recognised as a SCRIPT ERROR block — do not swallow it
        # silently just because it didn't match the shape this was written against. This is
        # distinct from "every block found was autoload noise", handled below: that case must
        # return clean, not fall through to here.
        tail = (r.stderr or r.stdout or "").strip()
        return [f"{rel}: check-only exited {r.returncode}: {tail[-400:]}"] if tail else []
    out = []
    for message, file, lineno in blocks:
        if _is_autoload_noise(message, autoloads):
            continue
        # `file` (when present) is the res:// path Godot itself named — usually the checked
        # script, but not always: a preload chain can attribute the error to a different
        # file. Strip the res:// prefix so the report reads as a repo-relative path like
        # everything else in the gate.
        named = (file or f"res://{rel}").removeprefix("res://")
        loc = f"{named}:{lineno}" if lineno else named
        out.append(f"{loc}: {message}")
    return out


def check_file(path: Path, autoloads: set[str] | None = None,
              runner: Callable[..., subprocess.CompletedProcess] | None = None,
              timeout: float = 30.0) -> list[str]:
    """Parse-check one `.gd` file in isolation. Returns real diagnostics only.

    `runner`, if given, replaces the subprocess call — `tools/check.py`'s gate check passes
    its own bounded `run()` here so a wedged Godot is a red run rather than a silent wait
    (LF-061 precedent), instead of this module's own plain-`subprocess.run` default, which
    this file's standalone CLI uses and which never touches `tools/reap.py`.
    """
    if autoloads is None:
        autoloads = parse_project_autoloads(PROJECT_GODOT)
    rel = path.relative_to(ROOT) if path.is_absolute() else path
    argv = build_argv(rel)
    if runner is not None:
        r = runner(*argv)
    else:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT),
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            return [f"{rel}: check-only timed out after {timeout:.0f}s"]
    return _diagnostics_from_result(r, autoloads, rel)


def check_all(files: list[Path] | None = None, jobs: int = 8) -> dict[str, list[str]]:
    """Check every tracked script concurrently, `ThreadPoolExecutor`-style (as
    `tools/test_parity.py` already does for its own Godot fan-out). Returns
    `{relpath: [diagnostics]}` — clean files are omitted entirely.
    """
    autoloads = parse_project_autoloads(PROJECT_GODOT)
    if files is None:
        files = tracked_gd_files()
    results: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(check_file, p, autoloads): p for p in files}
        for fut in futs:
            p = futs[fut]
            diags = fut.result()
            if diags:
                results[str(p.relative_to(ROOT))] = diags
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse-check GDScript in isolation.")
    ap.add_argument("files", nargs="*", type=Path,
                    help="specific .gd files to check; default is every tracked script "
                         "(excluding addons/)")
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()

    if toolpaths.godot() is None:
        print("godot not installed — cannot parse-check")
        return 0

    files = [f if f.is_absolute() else ROOT / f for f in args.files] \
        if args.files else tracked_gd_files()
    results = check_all(files, jobs=args.jobs)
    if not results:
        print(f"{len(files)} script(s) parse clean")
        return 0
    for relpath in sorted(results):
        for d in results[relpath]:
            print(d)
    print(f"\n{len(results)} of {len(files)} script(s) failed to parse")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
