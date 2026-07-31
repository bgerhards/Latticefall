#!/usr/bin/env python3
"""
One command for the whole sprite pipeline: render -> mask_glow -> pack_atlas -> --import,
always in that order, and skipping whatever is already current.

Why this exists (PRC-13)
-------------------------
The pipeline is four commands that have to run in this exact order, and `CLAUDE.md` says
**ALWAYS** three times, because skipping any one step makes a correct art fix *look like it
did nothing* in two different ways:

  1. Skip `--import` and Godot keeps serving the previous run's cached `.ctex` — the PNG on
     disk is right, the game just never picked it up.
  2. Skip `pack_atlas.py` (or run it before `mask_glow.py`) and the board draws from a stale
     or unmasked atlas page instead of the fresh loose renders underneath it.

Documenting a hazard is not the same as removing it — a human has to remember all four
steps, in order, every time. This is the fix: one entry point that always runs them in
order and cannot be asked to skip one (there is deliberately no `--skip-import` flag; see
`docs/issues/PRC-13-incremental-asset-build.md`, "Risks / gotchas").

Why it is incremental
----------------------
It is not incremental just by wrapping the four steps — that would make `ART-01`'s ~680
render library (heads at 16 yaws, bases at 4, units at 8) reprocess in full on every touch.
So `build.py` computes a content hash per asset — its builder function's own source
(`render.py`'s `compute_hashes()`, over `inspect.getsource()` on the `ASSETS` dict), the
shared material/rig code every builder can call, and the handful of module constants and
the Blender version string that change a render without touching any function body — and
skips render.py entirely for an asset whose hash still matches what's stored in
`assets/renders/sprites.json`, provided that asset's render files are actually still on
disk. `mask_glow.py` and `pack_atlas.py` are both cheap enough, and (post LF-071/this
issue) both idempotent enough, that this file always runs `mask_glow` and only calls
`pack_atlas` when something changed — see `_atlas_is_stale()`.

    .venv/bin/python tools/blender/build.py                       # build everything, skip
                                                                    # what is unchanged
    .venv/bin/python tools/blender/build.py --only pulse_turret   # one asset
    .venv/bin/python tools/blender/build.py --force               # ignore hashes
    .venv/bin/python tools/blender/build.py --dry-run             # report only, touch nothing

Exit code is nonzero if anything in the chain failed. A `--dry-run` never launches Blender
for anything beyond the read-only hash query, never writes a PNG, and never touches
`.godot/`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "blender"))

import reap                # noqa: E402  — same tool suite; reused rather than shelled out to
import toolpaths            # noqa: E402
import mask_glow            # noqa: E402  — pure Python + numpy/PIL, safe to import directly
import pack_atlas           # noqa: E402  — pure Python + PIL, safe to import directly

RENDER_SCRIPT = Path(__file__).with_name("render.py")
MANIFEST = ROOT / "assets" / "renders" / "sprites.json"

# Bootstrap fallback only — used when there is no manifest yet to read "yaws" from (the
# very first build.py run in a fresh checkout). Matches render.py's own YAWS and the
# camera facts CLAUDE.md documents as measured on this machine.
DEFAULT_YAWS = (45, 135, 225, 315)

DEFAULT_TIMEOUT = 1800.0


def _out(line: str) -> None:
    print(line, flush=True)


def _err(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


# ── Blender subprocess plumbing ──────────────────────────────────────────────────────

def _run_blender(script_args: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        argv = toolpaths.blender_argv(RENDER_SCRIPT, script_args)
    except RuntimeError as exc:
        raise SystemExit(f"build: {exc}")
    try:
        return subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT),
                               timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run(timeout=...) only kills the direct child. Blender under WSL
        # interop (this machine's install is a Windows .exe — see toolpaths.py) can
        # leave the same class of survivor tools/reap.py exists for; reap right away
        # rather than leaving it for whoever notices the fan later.
        procs = reap.find()
        killed = reap._kill(procs, quiet=False) if procs else 0
        _err(f"build: Blender timed out after {timeout:.0f}s; reaped {killed} of "
             f"{len(procs)} stray process(es)")
        for p in procs:
            _err(f"  pid {p['pid']}  {p['kind']}  {p['cmd'][:120]}")
        out = exc.stdout or ""
        _err((out.decode(errors="replace") if isinstance(out, bytes) else out)[-2000:])
        raise SystemExit(1)


def query_hashes(only: str | None, cell: int | None, timeout: float) -> dict:
    """One fast, read-only Blender launch: content hashes for every asset `render.py`
    would consider (or just `only`), with no scene ever built and no frame ever rendered
    — see `render.py`'s `compute_hashes()` docstring for why this is safe to call as
    often as we like without dirtying `git status`.
    """
    args = []
    if cell:
        args += ["--cell", str(cell)]
    if only:
        args += ["--only", only]
    args += ["--print-hashes"]
    r = _run_blender(args, timeout)
    blob = r.stdout + r.stderr
    line = next((l for l in blob.splitlines() if l.startswith("HASHES_JSON ")), None)
    if r.returncode != 0 or line is None:
        _err("build: hash query failed — no HASHES_JSON line in Blender's output")
        _err(blob.strip()[-2000:])
        raise SystemExit(1)
    return json.loads(line[len("HASHES_JSON "):])


def render_assets(names: list[str], cell: int | None, timeout: float) -> None:
    args = []
    if cell:
        args += ["--cell", str(cell)]
    args += ["--assets", ",".join(names)]
    r = _run_blender(args, timeout)
    for line in (r.stdout + r.stderr).splitlines():
        if line.startswith(("RENDERED ", "MANIFEST ", "CALIBRATION")):
            _out(f"  {line}")
    if r.returncode != 0:
        _err(f"build: render failed (exit {r.returncode})")
        _err((r.stdout + r.stderr).strip()[-3000:])
        raise SystemExit(1)


# ── hash-vs-disk bookkeeping ─────────────────────────────────────────────────────────

def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {}


def files_present(entry: dict | None, yaws: list[int]) -> bool:
    """True only if every yaw x pass this asset needs is both listed in its manifest
    entry AND actually on disk. A hash match with a missing file (an interrupted render,
    a hand-deleted PNG) must still trigger a re-render — the hash alone is not proof the
    pixels exist.
    """
    if not entry:
        return False
    for yaw in yaws:
        slot = "y%03d" % yaw
        by_pass = entry.get(slot)
        if not by_pass:
            return False
        for pass_name in ("albedo", "glow"):
            rel = by_pass.get(pass_name)
            if not rel or not (ROOT / rel).exists():
                return False
    return True


def plan(candidates: list[str], fresh_hashes: dict, stored_hashes: dict, sprites: dict,
         yaws: list[int], force: bool) -> tuple[list[tuple[str, str]], list[str]]:
    to_render: list[tuple[str, str]] = []
    skipped: list[str] = []
    for name in candidates:
        stored = stored_hashes.get(name)
        ok_files = files_present(sprites.get(name), yaws)
        if not force and stored is not None and stored == fresh_hashes[name] and ok_files:
            skipped.append(name)
            continue
        if force:
            reason = "forced"
        elif stored is None:
            reason = "never rendered"
        elif not ok_files:
            reason = "render file(s) missing"
        else:
            reason = "source changed"
        to_render.append((name, reason))
    return to_render, skipped


# ── atlas staleness ──────────────────────────────────────────────────────────────────

def atlas_is_stale(doc: dict) -> bool:
    """Same definition `tools/check.py`'s `sprite atlas` check uses — imported straight
    from `pack_atlas`, not reimplemented, so this file's "does the atlas need repacking"
    answer and the gate's "is the atlas in sync" answer can never quietly disagree.
    """
    atlas = doc.get("atlas")
    if not atlas:
        return True
    groups = pack_atlas.collect(doc)
    missing = [p for g in groups.values() for (_, _, p) in g if not p.exists()]
    if missing:
        return True
    for pass_name, rel in atlas.get("pages", {}).items():
        if not (ROOT / rel).exists():
            return True
    return pack_atlas.source_digest(groups) != atlas.get("source_digest")


# ── Godot-live guard (LF-075) ────────────────────────────────────────────────────────

def _linux_godot_lines() -> list[str]:
    """Every Linux-visible Godot process whose command line names *this* project's
    root — scoped the same way `tools/reap.py`'s own matchers are (`repo in cmd`), and
    deliberately broader than reap.py's own Godot classifier otherwise: that one only
    flags a *verification* run (`--headless`/`--fixed-fps`). LF-075 is about an
    *interactive* session — the owner playing or editing — which carries neither flag
    and would pass straight through reap.py's scope unnoticed. Path-scoped rather than
    "any Godot process on the machine" because this machine legitimately runs more than
    one Godot project (`tower-defense-godot`, `farm-to-table-godot` alongside this one) —
    an unscoped match would be a real false positive here, not a theoretical one.
    """
    try:
        out = subprocess.run(["ps", "-ax", "-o", "command="], capture_output=True,
                              text=True, check=False, timeout=10).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    root = str(ROOT)
    return [ln.strip() for ln in out.splitlines()
            if "godot" in ln.lower() and "godot-ai" not in ln.lower() and root in ln]


def _windows_godot_lines() -> tuple[list[str], bool]:
    """(scoped_live_lines, other_godot_seen_but_unscoped).

    Best-effort coverage for the case LF-075 actually was: the owner's editor is a
    native Windows `Godot*.exe` reached by opening `D:\\dev\\Latticefall` directly, which
    never shows up in WSL's own `ps` at all. Queries via PowerShell's
    `Get-CimInstance Win32_Process` rather than bare `tasklist.exe`: only the CIM query
    exposes each process's `CommandLine`, and a command line is the only way to scope a
    match to *this* project rather than "a Godot.exe exists somewhere on Windows" — this
    project is not the only Godot project on this machine (see `_linux_godot_lines`).
    `other_godot_seen_but_unscoped` is True when a Godot.exe exists but its command line
    carries no path to compare (a bare GUI/editor launch, e.g. from the Project Manager's
    recent-projects list) — genuinely ambiguous rather than confirmed clear or confirmed
    live, and reported as such by the caller rather than silently folded into either.
    """
    ps = shutil.which("powershell.exe")
    if not ps:
        return [], False
    try:
        out = subprocess.run(
            [ps, "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name LIKE 'Godot%'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, check=False, timeout=15,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return [], False
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return [], False
    win_root = toolpaths.host_path_for("x.exe", ROOT).lower()
    scoped = [ln for ln in lines if win_root in ln.lower().replace("\\", "/")]
    other_present = len(scoped) < len(lines)
    return scoped, other_present


def godot_liveness() -> tuple[list[str], bool]:
    """(live_process_descriptions scoped to THIS project's root, unverifiable_risk).

    `unverifiable_risk` is True when the check could plausibly be missing something
    relevant: no PowerShell reachable at all (Windows-side detection did not run), or a
    Godot.exe process exists on Windows whose command line carries no path to compare —
    present, but not provably (or provably not) this project.
    """
    linux_live = _linux_godot_lines()
    win_scoped, win_other_present = _windows_godot_lines()
    ps_reachable = shutil.which("powershell.exe") is not None
    live = linux_live + win_scoped
    unverified = win_other_present or not ps_reachable
    return live, unverified


# ── main ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="render -> mask_glow -> pack_atlas -> --import, in order, "
                     "skipping whatever is already current (PRC-13)")
    ap.add_argument("--only", help="one asset name; default is the whole library")
    ap.add_argument("--force", action="store_true",
                     help="ignore stored hashes, re-render everything asked for")
    ap.add_argument("--dry-run", action="store_true",
                     help="report what would happen, touch nothing")
    ap.add_argument("--cell", type=int, default=None,
                     help="forwarded to render.py's --cell override (ART-03/LF-102)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                     help="seconds allowed for each Blender subprocess "
                          f"(default {DEFAULT_TIMEOUT:.0f})")
    args = ap.parse_args()

    manifest = load_manifest()
    yaws = manifest.get("yaws", list(DEFAULT_YAWS))
    stored_hashes = manifest.get("hashes", {})
    sprites = manifest.get("sprites", {})

    _out("build: querying current content hashes ...")
    payload = query_hashes(args.only, args.cell, args.timeout)
    fresh_hashes = payload["hashes"]
    blender_version = payload["blender_version"]
    candidates = list(fresh_hashes)
    _out(f"build: Blender {blender_version}, {len(candidates)} asset(s) considered")

    to_render, skipped = plan(candidates, fresh_hashes, stored_hashes, sprites, yaws,
                               args.force)
    for name, reason in to_render:
        _out(f"  RENDER  {name:24s} ({reason})")
    for name in skipped:
        _out(f"  skip    {name:24s} (hash unchanged)")
    _out(f"build: {len(to_render)} to render, {len(skipped)} skipped")

    if args.dry_run:
        # Read-only report of what pack/import *would* do, computed the same way the
        # real run below would — nothing here writes a file or launches Godot.
        would_pack = bool(to_render) or (not manifest) or atlas_is_stale(manifest)
        live, unverified = godot_liveness()
        _out(f"build: --dry-run — would repack: {would_pack}; "
             f"would import: {would_pack and not live}"
             + (" (Godot appears live, LF-075 — import would be refused)" if live else "")
             + (" [Windows-side liveness unverified]" if unverified else ""))
        return 0

    rendered_names = [n for n, _ in to_render]
    if rendered_names:
        _out(f"build: rendering {len(rendered_names)} asset(s) in one Blender launch ...")
        render_assets(rendered_names, args.cell, args.timeout)
        manifest = load_manifest()   # render.py just rewrote it

    # mask_glow always runs: cheap, and (LF-071, this issue) writes nothing when the
    # computed output already matches what's on disk, so a no-op build costs one read
    # pass over the glow PNGs and rewrites none of them.
    _out("build: mask_glow ...")
    n_glow, n_written, _tot_before, _tot_after = _run_mask_glow()
    _out(f"  masked {n_glow} glow image(s), {n_written} rewritten")

    doc = load_manifest()
    need_pack = bool(rendered_names) or atlas_is_stale(doc)
    pages_packed: list[str] = []
    if need_pack:
        _out("build: pack_atlas ...")
        rc = pack_atlas.run(check=False)
        if rc != 0:
            _err("build: pack_atlas failed")
            return rc
        doc = load_manifest()
        pages_packed = sorted(doc.get("atlas", {}).get("pages", {}))
    else:
        _out("build: pack_atlas skipped (atlas already matches the renders on disk)")

    imported = False
    if need_pack:
        live, unverified = godot_liveness()
        if live:
            _err("build: REFUSING to import — a Godot process appears to be live "
                 "(LF-075: rebuilding the import cache while Godot has this project "
                 "open blanks whatever it has loaded, and reads exactly like a code "
                 "regression). Close it, or re-run once it's down:")
            for ln in live[:10]:
                _err(f"    {ln[:160]}")
            return 1
        if unverified:
            _out("build: Windows-side Godot liveness is not fully verified (either "
                 "PowerShell is unreachable, or a Godot.exe exists with no path in its "
                 "command line to compare) — proceeding, but this cannot rule out the "
                 "owner's native Windows editor having the project open (LF-075)")
        _out("build: importing into .godot/ IN PLACE (never moved aside — LF-075). "
             "If the owner is playing, this touches their loaded assets now.")
        try:
            argv = toolpaths.godot_argv(ROOT, ["--headless", "--import"],
                                         want_window=False)
        except RuntimeError as exc:
            _err(f"build: {exc}")
            return 1
        try:
            r = subprocess.run(argv, capture_output=True, text=True, cwd=str(ROOT),
                               timeout=args.timeout)
        except subprocess.TimeoutExpired:
            procs = reap.find()
            killed = reap._kill(procs, quiet=False) if procs else 0
            _err(f"build: import timed out; reaped {killed} of {len(procs)} stray "
                 "process(es)")
            return 1
        if r.returncode != 0:
            _err(f"build: import failed (exit {r.returncode})")
            _err((r.stdout + r.stderr).strip()[-2000:])
            return 1
        imported = True
        _out("build: import complete")

    _out("build: summary — "
         f"{len(candidates)} considered, {len(rendered_names)} re-rendered, "
         f"{len(skipped)} skipped, {n_written} glow file(s) rewritten, "
         f"atlas pages packed: {pages_packed or 'none'}, import ran: {imported}")
    return 0


def _run_mask_glow() -> tuple[int, int, int, int]:
    """`mask_glow.main()` prints its own summary and returns an int; this re-does its
    loop directly so build.py can fold the counts into its own summary line instead of
    scraping stdout for them."""
    if not MANIFEST.exists():
        return 0, 0, 0, 0
    doc = json.loads(MANIFEST.read_text())
    n, n_written, tot_before, tot_after = 0, 0, 0, 0
    for _name, yaws_entry in doc.get("sprites", {}).items():
        for _yaw, passes in yaws_entry.items():
            p = ROOT / passes["glow"]
            if not p.exists():
                continue
            b, a, written = mask_glow.mask(p)
            tot_before += b
            tot_after += a
            n += 1
            n_written += int(written)
    return n, n_written, tot_before, tot_after


if __name__ == "__main__":
    sys.exit(main())
