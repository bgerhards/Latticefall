#!/usr/bin/env python3
"""
Install the git hooks under `tools/hooks/` into this checkout's hook directory.

PRC-08. Idempotent and re-runnable: running this twice produces the same `pre-commit` and
`pre-push` in `.git/hooks/` (or wherever `core.hooksPath`/a linked worktree resolves hooks
to — resolved with `git rev-parse --git-path hooks` rather than a hardcoded `.git/hooks`,
so this also does the right thing from a worktree).

**git-lfs already owns `pre-push`** in a freshly cloned copy of this repo (`git lfs install`
writes it) — `.git/hooks/pre-push` is a two-line shim that shells out to `git lfs pre-push`.
Overwriting it outright would silently break LFS pushes for a repo whose 224 sprite renders
live in LFS (decision 012). So installing `pre-push` here is a *chain*, not a replacement:
the first time this script runs, a pre-existing lfs shim is detected (it is not already our
own managed hook, and its body mentions `git lfs pre-push`) and moved aside to
`pre-push.lfs`; `tools/hooks/pre-push` itself calls that file first, before the gate, and
propagates its stdin and its failure. On the second and later runs the shim has already been
moved, `pre-push.lfs` already exists, and nothing about that step repeats — reinstalling
just re-copies the (possibly edited) hook bodies from `tools/hooks/`.

A hook this script did NOT write and that is not the lfs shim (a hand-written hook, or one
some other tool installed) is backed up to `<name>.pre-latticefall` rather than silently
discarded, and a warning is printed — this script does not know what that hook does and
must not eat it.

    .venv/bin/python tools/install_hooks.py            # install/refresh pre-commit, pre-push
    .venv/bin/python tools/install_hooks.py --dry-run   # say what would happen, touch nothing
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "tools" / "hooks"

# Every managed hook body starts with this exact first line — how this script tells "a hook
# we installed, possibly stale" apart from "a hook something else put here". Must match the
# first line of every file in tools/hooks/ exactly.
MANAGED_MARKER = "#!/bin/sh\n# latticefall-managed-hook v1"

HOOK_NAMES = ("pre-commit", "pre-push")


def _git_path_hooks() -> Path:
    """Resolve this checkout's real hook directory — respects `core.hooksPath` and, from a
    linked worktree, still resolves to the shared hooks directory git itself would use."""
    r = subprocess.run(["git", "rev-parse", "--git-path", "hooks"],
                       capture_output=True, text=True, cwd=str(ROOT), check=True)
    p = Path(r.stdout.strip())
    return p if p.is_absolute() else (ROOT / p)


def _is_managed(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return path.read_text(errors="ignore").startswith(MANAGED_MARKER)
    except OSError:
        return False


def _looks_like_lfs_shim(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return "git lfs pre-push" in path.read_text(errors="ignore")
    except OSError:
        return False


def install(dry_run: bool = False) -> list[str]:
    """Install every hook in `tools/hooks/`, returning a list of human-readable actions taken
    — printed by `main()` and returned so tests / callers can assert on it without scraping
    stdout."""
    hooks_dir = _git_path_hooks()
    actions: list[str] = []

    if not dry_run:
        hooks_dir.mkdir(parents=True, exist_ok=True)

    for name in HOOK_NAMES:
        source = SOURCE_DIR / name
        dest = hooks_dir / name
        if not source.exists():
            actions.append(f"SKIP {name}: no tools/hooks/{name} in this checkout")
            continue

        if name == "pre-push" and dest.exists() and not _is_managed(dest):
            lfs_shim = hooks_dir / "pre-push.lfs"
            if _looks_like_lfs_shim(dest):
                if lfs_shim.exists():
                    actions.append(
                        f"pre-push.lfs already present at {lfs_shim} — leaving the existing "
                        f"unmanaged {dest} alone (not overwriting a shim we didn't move)")
                    # Something is already at pre-push.lfs, and the current pre-push is a
                    # foreign lfs shim rather than ours — most likely a re-run of `git lfs
                    # install` re-wrote it after we chained it once. Move it aside under a
                    # timestamped-free backup name rather than clobbering the working chain,
                    # then fall through to installing ours on top.
                    backup = hooks_dir / "pre-push.lfs.reinstalled"
                    if not dry_run:
                        shutil.move(str(dest), str(backup))
                    actions.append(f"moved re-appeared lfs shim {dest} -> {backup}")
                else:
                    if not dry_run:
                        shutil.move(str(dest), str(lfs_shim))
                    actions.append(f"moved git-lfs's pre-push shim: {dest} -> {lfs_shim} "
                                    f"(tools/hooks/pre-push calls it first)")
            else:
                backup = hooks_dir / f"{name}.pre-latticefall"
                actions.append(f"WARNING: {dest} exists, is not ours, and is not the lfs "
                                f"shim — backed up to {backup} rather than discarded. "
                                f"Inspect it; it did not run as part of the chain.")
                if not dry_run:
                    shutil.copy2(str(dest), str(backup))
        elif dest.exists() and not _is_managed(dest):
            backup = hooks_dir / f"{name}.pre-latticefall"
            actions.append(f"WARNING: {dest} exists and is not ours — backed up to {backup} "
                            f"rather than discarded. Inspect it; it did not run.")
            if not dry_run:
                shutil.copy2(str(dest), str(backup))

        if dry_run:
            verb = "would refresh" if _is_managed(dest) else "would install"
            actions.append(f"{verb} {dest} from {source}")
            continue

        shutil.copy2(str(source), str(dest))
        dest.chmod(0o755)
        actions.append(f"installed {dest}")

    return actions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would happen; touch nothing")
    args = ap.parse_args()

    for line in install(dry_run=args.dry_run):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
