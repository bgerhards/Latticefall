#!/usr/bin/env python3
"""
Turn this repository into a build a stranger can run, and prove the build actually plays.

Why this file exists (LF-206)
------------------------------
Until now nothing in this project produced a binary. The game was playable only from the
Godot editor, out of a working tree that happens to have three things a player will never
have: an `.godot/` import cache, a fetched Git LFS working copy, and Godot itself. Every
one of those is invisible on the dev box and fatal off it, which is exactly why "it runs
here" proves nothing about an export and why this tool ends in a *play*, not in a file
existing.

**The import-cache assumption inverts here, and that is the whole risk.** Everywhere else
in this project `.godot/` is disposable build output that the editor rebuilds on demand
(see CLAUDE.md). An export bakes it into the pack instead, so the export is the first
context in which a missing or stale import is unfixable by the player — and the failure
mode this project already knows well, a `class_name` that is absent from the global class
cache, is a *hang* rather than an error. A binary that starts is therefore not evidence of
anything. `--verify` runs the packaged build through a real anchor and reads back the
`FRAME`/`STATE` lines `main.gd` prints, which is the same evidence the gate's `game
renders` check uses and the only kind that distinguishes a working pack from a hung one.

Two export presets, and they are not the same kind of thing
------------------------------------------------------------
`Web` is the deliverable. The project renders with GL Compatibility already
(`project.godot`), which is the only rendering method Godot supports on the web — Forward+
and Mobile are designed around modern low-level graphics APIs and do not exist there — and
nothing in `scripts/` uses `Thread`, `WorkerThreadPool`, `Mutex` or `Semaphore`, so the
preset sets `variant/thread_support=false`. That is not a limitation being accepted, it is
the configuration Godot's own web documentation recommends for itch.io specifically:
threads require `SharedArrayBuffer`, which requires the `Cross-Origin-Opener-Policy` and
`Cross-Origin-Embedder-Policy` headers, which requires control of the server. A
single-threaded build is a plain static upload.

`Linux` is the *verification vehicle*, not a shipping target. `--verify` needs to launch
the packaged build on this machine, and this machine is Linux; a web bundle cannot be
played by a subprocess. Its `binary_format/embed_pck=true` makes it one self-contained
file, which is what lets `--verify` copy it outside the repository and run it there — the
only way to prove the pack does not silently depend on the working tree it was built in.

What is deliberately NOT here
-------------------------------
No upload. Publishing is `.github/workflows/release.yml`'s job, because a credential that
can push to a storefront belongs in CI secrets and not in a tool any agent can run. This
file's contract ends at "a verified build exists in `build/`".

    .venv/bin/python tools/export.py --ensure-templates   # one-time, ~1.2 GB download
    .venv/bin/python tools/export.py                      # web + linux, then verify
    .venv/bin/python tools/export.py --preset web         # deliverable only, no verify
    .venv/bin/python tools/export.py --preset linux --anchor anchor-13
    .venv/bin/python tools/export.py --json /tmp/export.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lease        # noqa: E402  — so tools/reap.py never kills an export mid-pack
import toolpaths    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The engine version every preset's templates must match. Godot refuses to export against a
# mismatched template set, and the error names the version it wanted — but it names it at
# the END of a multi-minute run, so this tool checks first. Kept as a literal rather than
# parsed out of the binary because the templates are fetched from a GitHub release tag,
# which is this exact string.
GODOT_VERSION = "4.7.1-stable"
# Godot's own on-disk layout: `<templates>/<version with . instead of ->/`.
TEMPLATE_DIR_NAME = "4.7.1.stable"
TEMPLATES_URL = (f"https://github.com/godotengine/godot/releases/download/{GODOT_VERSION}/"
                 f"Godot_v{GODOT_VERSION}_export_templates.tpz")

# Which members of the 1.2 GB .tpz are actually extracted. The archive carries every
# platform Godot supports (Android, iOS and macOS alone are 450 MB of it) and this project
# targets two. `--all-templates` takes the lot, for whoever adds a Windows or macOS preset.
TEMPLATE_MEMBERS = (
    "templates/version.txt",
    "templates/linux_debug.x86_64",
    "templates/linux_release.x86_64",
    "templates/web_debug.zip",
    "templates/web_release.zip",
    "templates/web_nothreads_debug.zip",
    "templates/web_nothreads_release.zip",
    "templates/web_dlink_debug.zip",
    "templates/web_dlink_release.zip",
    "templates/web_dlink_nothreads_debug.zip",
    "templates/web_dlink_nothreads_release.zip",
)

# Preset name in export_presets.cfg -> the path its `export_path` writes, relative to ROOT.
# Duplicated from the .cfg on purpose: Godot rewrites that file wholesale whenever the
# editor's export dialog is opened, and a tool that read `export_path` back out of it would
# follow the rewrite silently instead of failing. `check_export_presets` in tools/check.py
# asserts the two agree, so a drift is a red gate rather than a build in the wrong place.
PRESETS = {
    "web": ("Web", Path("build/web/index.html")),
    "linux": ("Linux", Path("build/linux/latticefall.x86_64")),
}

# The floors `check_game_renders` in tools/check.py uses, and for the same reason: they sit
# between a healthy frame and a board that failed to load. Coverage is the fraction of
# non-background pixels over the whole frame and is dominated by the two instrument panels,
# so it is evidence about the frame rather than about the board — which is why `--verify`
# also asserts against `--facings`, the one output that names what was actually drawn.
MIN_COVERAGE, MIN_DISTINCT = 0.15, 12

VERIFY_ANCHOR = "anchor-13"
VERIFY_FRAMES = 900


def templates_root() -> Path:
    """Where Godot looks for export templates on this machine.

    `$XDG_DATA_HOME` is honoured because the Linux backend honours it — the same lever
    `tools/save_roundtrip.py` uses to keep a test off the real save (LF-175). There is no
    `--user-data-dir` to reach for: this Godot build does not recognise that flag and
    ignores it *silently*, which is how an early version of that tool ran twice against the
    live save before an mtime check caught it.
    """
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "godot" / "export_templates" / TEMPLATE_DIR_NAME


def templates_installed() -> bool:
    """True when the version file is present AND says the version this project needs.

    Checking the marker file's *content* rather than the directory's existence is the
    point: an interrupted extraction leaves a directory full of the right names and the
    wrong bytes, and Godot's own complaint about that arrives minutes into an export.
    """
    marker = templates_root() / "version.txt"
    if not marker.is_file():
        return False
    return marker.read_text().strip() == TEMPLATE_DIR_NAME


def ensure_templates(all_platforms: bool = False, keep_archive: bool = False) -> bool:
    """Download and extract the export templates. Idempotent; returns True if it did work.

    The archive lands in `.cache/` (gitignored) and is deleted afterwards unless asked
    otherwise — it is 1.2 GB and the extracted subset this project needs is 225 MB, so
    keeping both by default would cost more disk than the templates themselves.
    """
    if templates_installed() and not all_platforms:
        return False
    dest = templates_root()
    dest.mkdir(parents=True, exist_ok=True)
    archive = ROOT / ".cache" / "export_templates" / "tpz.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        print(f"downloading {TEMPLATES_URL}", flush=True)
        with urllib.request.urlopen(TEMPLATES_URL) as src, archive.open("wb") as out:
            shutil.copyfileobj(src, out)
    with zipfile.ZipFile(archive) as z:
        members = z.namelist() if all_platforms else list(TEMPLATE_MEMBERS)
        for m in members:
            if m.endswith("/"):
                continue
            target = dest / Path(m).name
            with z.open(m) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            # The Linux templates ARE the exported binary's executable half; a template that
            # lost its mode bit produces a build nobody can run, with no error at export time.
            if Path(m).name.startswith("linux_"):
                target.chmod(0o755)
    if not keep_archive:
        archive.unlink()
    print(f"templates installed: {dest}", flush=True)
    return True


def export(preset_key: str, timeout_s: float = 900.0) -> tuple[bool, Path, str]:
    """Run one export. Returns (ok, output path, the engine's last words on failure)."""
    name, rel = PRESETS[preset_key]
    out = ROOT / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = toolpaths.godot_argv(
        ROOT, ["--headless", "--export-release", name, str(out)], want_window=False)
    with lease.acquire("export", argv, ttl_s=timeout_s + 60.0):
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s)
    blob = r.stdout + r.stderr
    if r.returncode != 0 or not out.is_file():
        bad = [ln for ln in blob.splitlines() if "ERROR" in ln or "error:" in ln.lower()]
        return False, out, "\n".join(bad[-8:]) or blob[-1200:]
    return True, out, ""


def _artifact_bytes(preset_key: str) -> int:
    """Total size of what this preset produced. A web export is a directory of siblings
    (`.wasm`, `.pck`, `.js`, the shell and its icons), not one file, so the number worth
    reporting is the whole bundle — that is what a player downloads."""
    _, rel = PRESETS[preset_key]
    out = ROOT / rel
    if preset_key == "web":
        return sum(p.stat().st_size for p in out.parent.iterdir() if p.is_file())
    return out.stat().st_size


def verify(binary: Path, anchor: str = VERIFY_ANCHOR, frames: int = VERIFY_FRAMES,
           timeout_s: float = 600.0) -> tuple[bool, dict]:
    """Play an anchor from the packaged build and read back what it drew.

    Run from a temporary directory OUTSIDE the repository, with `$XDG_DATA_HOME` pointed
    somewhere disposable. Both halves matter and neither is ceremony. Running elsewhere is
    what proves the pack is self-contained rather than quietly reading `res://` off the
    working tree it was built beside; redirecting the data home is what keeps a verification
    run from writing the owner's real `progress.json`, which is the same trap `--user-data-dir`
    silently walked into in LF-175.
    """
    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / f"lf-export-verify-{os.getpid()}"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        exe = scratch / binary.name
        shutil.copy2(binary, exe)
        exe.chmod(0o755)
        shot = scratch / "verify.png"
        argv = [str(exe), "--rendering-driver", "opengl3", "--fixed-fps", "60", "--",
                "--autoplay", "--anchor", anchor, "--facings",
                "--shot", str(shot), str(frames)]
        prefix = toolpaths.xvfb_prefix()
        if prefix:
            argv = prefix + argv
        env = dict(os.environ, XDG_DATA_HOME=str(scratch / "xdg"))
        with lease.acquire_capture("export-verify", argv, ttl_s=timeout_s + 60.0):
            r = subprocess.run(argv, capture_output=True, text=True, cwd=scratch,
                               timeout=timeout_s, env=env)
        blob = r.stdout + r.stderr
        info: dict = {"anchor": anchor, "frames": frames, "shot_bytes": 0}

        m = re.search(r"^FRAME coverage=([\d.]+) distinct=(\d+)", blob, re.M)
        if not m:
            return False, dict(info, error="the packaged build never reported a FRAME line",
                               tail=blob[-1500:])
        info["coverage"], info["distinct"] = float(m.group(1)), int(m.group(2))

        m = re.search(r"^SHOT \S+ err=(\d+)", blob, re.M)
        info["shot_err"] = int(m.group(1)) if m else -1
        info["shot_bytes"] = shot.stat().st_size if shot.is_file() else 0

        m = re.search(r"^STATE frame=(\d+) sim_t=([\d.]+)", blob, re.M)
        info["state_frame"] = int(m.group(1)) if m else -1

        # `--facings` names every drawable the frame actually drew, one `FACE <kind>
        # <sprite> ...` line each (main.gd). Coverage cannot tell a board of twelve
        # emplacements from a board of none — measured, LF-251 — so this is the half of the
        # assertion that is about the board rather than about the frame.
        drawn = set(re.findall(r"^FACE\s+\S+\s+(\S+)", blob, re.M))
        info["drawables"], info["sprites"] = len(drawn), sorted(drawn)[:12]

        errors = [ln for ln in blob.splitlines()
                  if "SCRIPT ERROR" in ln or "Failed to load script" in ln
                  or "Failed to instantiate" in ln or "Parse error" in ln]
        info["script_errors"] = errors[:5]

        why = []
        if info["shot_err"] != 0:
            why.append(f"the build could not write its own screenshot (err={info['shot_err']})")
        if info["coverage"] < MIN_COVERAGE:
            why.append(f"coverage {info['coverage']:.4f} < {MIN_COVERAGE}")
        if info["distinct"] < MIN_DISTINCT:
            why.append(f"{info['distinct']} distinct tones < {MIN_DISTINCT}")
        if info["state_frame"] < frames:
            why.append(f"the sim stopped at frame {info['state_frame']}, short of {frames}")
        if info["drawables"] == 0:
            why.append("nothing was drawn — no FACE line, so the board loaded empty")
        if errors:
            why.append(f"{len(errors)} script error(s), first: {errors[0][:160]}")
        if why:
            return False, dict(info, error="; ".join(why))
        return True, info
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=[*PRESETS, "all"], default="all")
    ap.add_argument("--ensure-templates", action="store_true",
                    help="download and extract the export templates first (~1.2 GB)")
    ap.add_argument("--all-templates", action="store_true",
                    help="with --ensure-templates, extract every platform, not just ours")
    ap.add_argument("--keep-archive", action="store_true",
                    help="with --ensure-templates, keep the .tpz in .cache/ afterwards")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip playing the packaged Linux build (it is the point; say why)")
    ap.add_argument("--anchor", default=VERIFY_ANCHOR)
    ap.add_argument("--frames", type=int, default=VERIFY_FRAMES)
    ap.add_argument("--json", metavar="PATH", help="write a machine-readable record here")
    args = ap.parse_args()

    if args.ensure_templates:
        ensure_templates(all_platforms=args.all_templates, keep_archive=args.keep_archive)

    if not templates_installed():
        print(f"FAIL  export templates for {GODOT_VERSION} are not installed.\n"
              f"      expected: {templates_root()}\n"
              f"      fix:      .venv/bin/python tools/export.py --ensure-templates",
              file=sys.stderr)
        return 2
    if toolpaths.godot() is None:
        print("FAIL  no Godot binary on this machine (see tools/toolpaths.py)",
              file=sys.stderr)
        return 2

    keys = list(PRESETS) if args.preset == "all" else [args.preset]
    record: dict = {"godot_version": GODOT_VERSION, "presets": {}}
    failed = False

    for key in keys:
        t0 = time.monotonic()
        ok, out, err = export(key)
        entry = {"ok": ok, "path": str(out.relative_to(ROOT)),
                 "seconds": round(time.monotonic() - t0, 1)}
        if ok:
            entry["bytes"] = _artifact_bytes(key)
            print(f"ok    export {key:6s} {entry['bytes'] / 1e6:8.1f} MB  "
                  f"{entry['seconds']:5.1f}s  {entry['path']}")
        else:
            entry["error"] = err
            failed = True
            print(f"FAIL  export {key:6s} {err}", file=sys.stderr)
        record["presets"][key] = entry

    if "linux" in keys and record["presets"]["linux"]["ok"] and not args.no_verify:
        t0 = time.monotonic()
        ok, info = verify(ROOT / PRESETS["linux"][1], anchor=args.anchor, frames=args.frames)
        info["seconds"] = round(time.monotonic() - t0, 1)
        record["verify"] = dict(info, ok=ok)
        if ok:
            print(f"ok    verify {info['anchor']} coverage={info['coverage']:.4f} "
                  f"distinct={info['distinct']} drawables={info['drawables']} "
                  f"frame={info['state_frame']}  {info['seconds']:.1f}s")
        else:
            failed = True
            print(f"FAIL  verify {info.get('error')}", file=sys.stderr)
    elif args.no_verify:
        # Never let a skipped verification read as a pass — the same contract tools/check.py
        # holds itself to for a tier-skipped check.
        record["verify"] = {"ok": None, "skipped_reason": "--no-verify"}
        print("skip  verify (--no-verify): the binary exists, nothing has played it")

    if args.json:
        Path(args.json).write_text(json.dumps(record, indent=2) + "\n")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
