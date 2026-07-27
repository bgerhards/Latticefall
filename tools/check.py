#!/usr/bin/env python3
"""
The gate. Run before every commit.

One command, one exit code. Each check is mechanical and fast — this catches breakage,
not cheapness. For "does it feel finished", use the `verify` skill and the
`build-verifier` agent.

Checks that cannot run yet (because the subsystem does not exist) report SKIP rather than
passing silently. A green run that quietly skipped half the suite is worse than a red one.

    .venv/bin/python tools/check.py
    .venv/bin/python tools/check.py --list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

OK, FAIL, SKIP = "ok", "FAIL", "skip"


class Result:
    def __init__(self, status: str, detail: str = "") -> None:
        self.status, self.detail = status, detail


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT))


# ─────────────────────────────────────────────────────────────── checks ──

def check_python_syntax() -> Result:
    files = [p for p in ROOT.rglob("*.py")
             if ".venv" not in p.parts and "addons" not in p.parts]
    bad = []
    for p in files:
        try:
            py_compile.compile(str(p), doraise=True, cfile=str(p.with_suffix(".pyc-check")))
        except py_compile.PyCompileError as e:
            bad.append(f"{p.relative_to(ROOT)}: {e.msg.splitlines()[-1]}")
        finally:
            p.with_suffix(".pyc-check").unlink(missing_ok=True)
    if bad:
        return Result(FAIL, "\n".join(bad))
    return Result(OK, f"{len(files)} files")


def check_game_data() -> Result:
    script = ROOT / "tools" / "validate" / "validate_data.py"
    if not script.exists():
        return Result(SKIP, "validator missing")
    r = run(PY, str(script), "--quiet")
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip())
    warns = [l for l in r.stdout.splitlines() if l.strip().startswith("warn")]
    return Result(OK, f"{len(warns)} warning(s)" if warns else "no warnings")


def check_json_parses() -> Result:
    bad = []
    for p in ROOT.rglob("*.json"):
        if ".venv" in p.parts or "addons" in p.parts or ".godot" in p.parts:
            continue
        try:
            json.loads(p.read_text())
        except Exception as e:
            bad.append(f"{p.relative_to(ROOT)}: {e}")
    return Result(FAIL, "\n".join(bad)) if bad else Result(OK)


def check_sfx_reproducible() -> Result:
    """The bank claims to be a pure function of sound names. Verify one sound."""
    script = ROOT / "tools" / "audio" / "synth_sfx.py"
    target = ROOT / "assets" / "audio" / "sfx" / "ui_confirm.wav"
    if not (script.exists() and target.exists()):
        return Result(SKIP, "sfx bank not built")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    r = run(PY, str(script), "ui_confirm")
    if r.returncode != 0:
        return Result(FAIL, r.stderr.strip()[-400:])
    after = hashlib.sha256(target.read_bytes()).hexdigest()
    if before != after:
        return Result(FAIL, "ui_confirm.wav changed on regeneration — synthesis is not "
                            "deterministic, so the bank cannot be trusted to rebuild")
    return Result(OK, "ui_confirm byte-identical")


def check_music_manifest() -> Result:
    man = ROOT / "assets" / "audio" / "music_manifest.json"
    if not man.exists():
        return Result(SKIP, "no music manifest")
    doc = json.loads(man.read_text())
    missing = [t["id"] for t in doc["tracks"]
               if not (ROOT / "assets" / "audio" / t["file"]).exists()]
    if missing:
        return Result(FAIL, f"manifest references missing ogg: {', '.join(missing)}")
    no_hash = [t["id"] for t in doc["tracks"] if not t.get("source_sha256")]
    if no_hash:
        return Result(FAIL, f"no master sha256 recorded for: {', '.join(no_hash)} — "
                            f"masters are not versioned, so the hash is the only proof")
    return Result(OK, f"{len(doc['tracks'])} tracks")


def check_backlog_rendered() -> Result:
    store, doc = ROOT / "backlog.json", ROOT / "docs" / "BACKLOG.md"
    if not store.exists():
        return Result(SKIP, "no backlog")
    if not doc.exists():
        return Result(FAIL, "docs/BACKLOG.md missing — run tools/backlog.py render")
    if store.stat().st_mtime > doc.stat().st_mtime + 1:
        return Result(FAIL, "docs/BACKLOG.md is stale — run tools/backlog.py render")
    n = len([i for i in json.loads(store.read_text())["items"]
             if i["status"] in ("open", "wip")])
    return Result(OK, f"{n} open")


def check_banned_terms() -> Result:
    """Nomenclature is a legal risk, so it gets a mechanical check, not a promise.

    nomenclature-exempt
    """
    nom = ROOT / "docs" / "NOMENCLATURE.md"
    if not nom.exists():
        return Result(SKIP, "no nomenclature bible")
    # Files that legitimately name these terms in order to forbid them carry the
    # marker below. An allowlist of paths would rot; a marker travels with the file.
    EXEMPT = "nomenclature-exempt"
    # Terms specific enough that any occurrence outside the bible is a real hit.
    banned = ["stargate", "goa'uld", "jaffa", "naquadah", "tok'ra", "ha'tak",
              "asgard", "chevron", "dial-home", "zero point module", "kawoosh"]
    hits = []
    scan = [p for p in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.json"))
            + list(ROOT.rglob("*.gd")) + list(ROOT.rglob("*.md"))
            if ".venv" not in p.parts and "addons" not in p.parts
            and ".godot" not in p.parts and p != nom]
    for p in scan:
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        if EXEMPT in text:
            continue
        low = text.lower()
        for term in banned:
            if term in low:
                hits.append(f"{p.relative_to(ROOT)}: '{term}'")
    return Result(FAIL, "\n".join(hits)) if hits else Result(OK, f"{len(scan)} files clean")


def check_sim() -> Result:
    sim = ROOT / "sim" / "run.py"
    if not sim.exists():
        return Result(SKIP, "headless sim not written (LF-002)")
    a = run(PY, str(sim), "--anchor", "anchor-01", "--seed", "1", "--json")
    b = run(PY, str(sim), "--anchor", "anchor-01", "--seed", "1", "--json")
    if a.returncode != 0:
        return Result(FAIL, a.stderr.strip()[-400:])
    if a.stdout != b.stdout:
        return Result(FAIL, "same seed produced different output — sim is not deterministic")
    return Result(OK, "deterministic")


def check_godot_boots() -> Result:
    """Load the main scene headlessly and assert no script errors.

    A GDScript parse error does not stop the process — Godot logs it and carries on
    with the scene missing. Exit code alone would call that a pass, so the output is
    what has to be asserted on.
    """
    godot = "/Applications/Godot.app/Contents/MacOS/Godot"
    if not Path(godot).exists():
        return Result(SKIP, "godot not installed")
    r = run(godot, "--headless", "--path", str(ROOT), "--quit-after", "120")
    blob = r.stdout + r.stderr
    bad = [l for l in blob.splitlines()
           if "SCRIPT ERROR" in l or "Parse Error" in l or "Compile Error" in l]
    if bad:
        return Result(FAIL, "\n".join(bad[:8]))
    return Result(OK, "main scene loads clean")


def check_game_renders() -> Result:
    """Run the real renderer and assert the frame is not blank.

    `check_godot_boots` runs headless and only greps for script errors, so it passes
    happily on a scene that draws nothing at all — which is how scenes/main.tscn stayed
    a childless Node2D for several sessions while the gate stayed green. The build
    reports `FRAME coverage=… distinct=…` alongside its self-screenshot; measured here,
    a healthy anchor-01 frame is ~0.39 coverage and a board that failed to load is
    ~0.03, so the bar sits between them with room on both sides.

    This needs a real window: GL Compatibility headless renders nothing to read back.
    """
    godot = "/Applications/Godot.app/Contents/MacOS/Godot"
    if not Path(godot).exists():
        return Result(SKIP, "godot not installed")

    MIN_COVERAGE, MIN_DISTINCT = 0.15, 12
    shot = ROOT / ".godot" / "gate-frame.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    r = run(godot, "--path", str(ROOT), "--fixed-fps", "60",
            "--", "--shot", str(shot), "120")
    blob = r.stdout + r.stderr

    line = next((l for l in blob.splitlines() if l.startswith("FRAME ")), "")
    if not line:
        return Result(FAIL, "build never reported a frame — it did not reach the shot:\n"
                            + blob.strip()[-800:])
    try:
        coverage = float(line.split("coverage=")[1].split()[0])
        distinct = int(line.split("distinct=")[1].split()[0])
    except (IndexError, ValueError):
        return Result(FAIL, f"could not parse frame stats: {line!r}")

    if coverage < MIN_COVERAGE or distinct < MIN_DISTINCT:
        return Result(FAIL,
                      f"frame is effectively blank: coverage={coverage:.4f} "
                      f"(min {MIN_COVERAGE}), distinct={distinct} (min {MIN_DISTINCT})")
    shot.unlink(missing_ok=True)
    return Result(OK, f"coverage {coverage:.2f}, {distinct} tones")


def check_rules_parity() -> Result:
    """The rules exist twice, in Python and GDScript. Prove they agree.

    Without this the game could silently stop playing the level that was balanced,
    and nothing would announce it.
    """
    script = ROOT / "tools" / "test_parity.py"
    if not script.exists():
        return Result(SKIP, "parity harness missing")
    if not Path("/Applications/Godot.app/Contents/MacOS/Godot").exists():
        return Result(SKIP, "godot not installed")
    r = run(PY, str(script))
    if r.returncode != 0:
        return Result(FAIL, (r.stderr + r.stdout).strip()[-1200:])
    return Result(OK, r.stdout.strip().replace("parity ok — ", ""))


CHECKS = [
    ("python syntax",     check_python_syntax),
    ("json parses",       check_json_parses),
    ("game data",         check_game_data),
    ("banned terms",      check_banned_terms),
    ("sfx determinism",   check_sfx_reproducible),
    ("music manifest",    check_music_manifest),
    ("backlog rendered",  check_backlog_rendered),
    ("sim determinism",   check_sim),
    ("godot boots",       check_godot_boots),
    ("game renders",      check_game_renders),
    ("rules parity",      check_rules_parity),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Latticefall pre-commit gate.")
    ap.add_argument("--list", action="store_true", help="list checks and exit")
    args = ap.parse_args()

    if args.list:
        for name, _ in CHECKS:
            print(name)
        return 0

    failed = skipped = 0
    t0 = time.time()
    for name, fn in CHECKS:
        start = time.time()
        try:
            res = fn()
        except Exception as e:
            res = Result(FAIL, f"check itself raised: {type(e).__name__}: {e}")
        ms = (time.time() - start) * 1000
        mark = {OK: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[res.status]
        print(f"[{mark}] {name:<20s} {ms:6.0f}ms  {res.detail.splitlines()[0] if res.detail else ''}")
        if res.status == FAIL:
            failed += 1
            for line in res.detail.splitlines()[1:]:
                print(f"           {line}")
        elif res.status == SKIP:
            skipped += 1

    total = (time.time() - t0) * 1000
    print(f"\n{len(CHECKS) - failed - skipped} passed · {failed} failed · "
          f"{skipped} skipped · {total:.0f}ms")
    if skipped:
        print("skipped checks are not passes — the subsystem does not exist yet")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
