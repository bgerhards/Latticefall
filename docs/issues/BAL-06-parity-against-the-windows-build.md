id: BAL-06
title: Parity against the Windows build — the gate never tests the binary the owner plays
labels: phase-3, tooling, rules, risk
depends: PRC-04
milestone: E7 Balance
---
## Problem

**Blocker (LF-105, PRD §7 risk 2).** `tools/test_parity.py` resolves Godot through
`toolpaths.godot_argv()` (`tools/test_parity.py:47`), and `toolpaths.godot()` prefers the
native Linux build on this machine (`docs/STATE.md`: it prefers
`/mnt/d/godot/linux/Godot_v4.7.1-stable_linux.x86_64`). So **all 864 parity runs compare
CPython against *Linux* Godot. The owner plays the Windows build.** There is zero coverage of
the binary that ships.

This is not paranoia; it is measured. Across 100,000 float64 samples on CPython, Linux Godot
4.7.1 and **Windows Godot 4.7.1** (PRD §2.1):

| Operation | Result |
|---|---|
| `+ − × ÷`, `sqrt`, `fmod`, `floor`, `min`/`max`, comparisons | 0 mismatches out of 100,000 on all three |
| `atan2` | Windows diverges on 0.084% of samples |
| `sin` / `cos` | 0.133% / 0.120% |
| `pow` / `log` / `exp` | 0.130% / 0.031% / 0.069% |
| `tan` | **4.32%** |

Windows Godot uses the MSVC UCRT; CPython and Linux Godot use glibc. The divergence is stable
across runs — a library difference, not nondeterminism. The rules happen to use none of the
divergent operations today, so **cross-platform parity holds by accident, not by design**.
Theatre Scale adds off-grid geometry, firing arcs and visibility maths; the accident will not
survive it. {{BAL-07}} makes the accident into a rule; this issue makes it observable.

## Tasks

- [ ] Add `--godot <path>` (or `--platform windows|linux`) to `tools/test_parity.py`, resolved
      through `toolpaths` rather than a bare path, so the Windows `.exe` gets WSL interop and
      `host_path_for()` translation (`tools/toolpaths.py:184-210`).
- [ ] Confirm `toolpaths.godot()`'s Windows candidates actually resolve on this machine —
      `/mnt/*/godot/Godot_v*_win64_console.exe` and friends (`tools/toolpaths.py:62-64`) — and
      record the resolved path in the run's output. A parity run that silently fell back to
      Linux would report a green cross-platform result, which is the worst possible outcome.
- [ ] Verify the Windows console build reaches `PARITY_JSON` under `--headless --script
      res://scripts/test/parity.gd`. It is a different binary with different path handling;
      probe it before building anything on top.
- [ ] Handle path translation for `--path .` and the script `res://` URI under WSL interop, and
      for wherever the JSON is emitted. `docs/STATE.md` records the class of failure exactly:
      `render.py` once wrote manifest paths with `os.path.relpath` under Windows Python,
      producing backslashes Linux `pathlib` reads as one opaque filename, and every downstream
      step silently processed nothing.
- [ ] Run the full 864-run set against Windows Godot and record the result. If it diverges
      **today**, that is a genuine bug in the shipped game and this issue's priority changes.
- [ ] Add a three-way comparison mode: CPython vs Linux Godot vs Windows Godot, reporting which
      pair diverged. Two-way output cannot tell you whether Python or a runtime is the odd one
      out.
- [ ] Add a `rules parity (windows)` gate check at **tier 4** ({{PRC-04}}), skipping with a
      clear reason when no Windows build is resolvable — a skip is never a pass, and on a Linux
      CI box this must say "no Windows build" rather than reporting success.
- [ ] Make it a **release gate**: state in `CLAUDE.md` that a release runs Windows parity, and
      wire it into {{PRC-08}}'s nightly/dispatch workflow behind the self-hosted-runner
      variable.
- [ ] Fold the Windows run into {{PRC-05}}'s digest: the Godot **version and platform** are part
      of the parity input hash, so a Windows run is not skipped because a Linux run passed.
- [ ] Measure the Windows run's wall clock. WSL interop is slower than a native launch; if it is
      materially worse than 542 s, use {{PRC-05}}'s `--shard I/N`.
- [ ] Record the measured divergence table (above) in `docs/DECISIONS.md` as part of the entry
      that supersedes decision 030, cross-referencing {{BAL-07}}.
- [ ] Close LF-105.

## Acceptance criteria

- `tools/test_parity.py --platform windows` runs and prints the resolved binary path.
- The full set reports identical outcomes against the Windows build, or reports the exact
  divergent (anchor, policy, difficulty, field) triples — either is a pass for this issue; a
  silent fallback to Linux is not.
- `tools/check.py --tier 4` includes `rules parity (windows)`.
- On a machine with no Windows build, that check reports `skip` with reason "no Windows Godot
  resolvable", and the summary states it is not a pass.
- A three-way run names which runtime pair diverged when one does.
- {{PRC-05}}'s cache does not let a Linux pass suppress a Windows run.
- The measured divergence table is in `docs/DECISIONS.md`.

## Verification

```bash
.venv/bin/python -c "import sys;sys.path.insert(0,'tools');import toolpaths;print(toolpaths.godot())"
.venv/bin/python tools/test_parity.py --platform windows --verbose 2>&1 | tail -20
.venv/bin/python tools/check.py --tier 4 2>&1 | grep -i 'windows'
.venv/bin/python tools/reap.py
```

Proof: the printed Windows `.exe` path, and `parity ok — N runs identical` (or a named
divergence) from that binary.

## Risks / gotchas

- **A silent fallback is the failure mode.** `toolpaths.godot()` prefers Linux; if the
  `--platform windows` request cannot be satisfied it must **error**, never fall back. A green
  "Windows parity" run that was actually Linux is worse than no check.
- **`--headless` never opens a window on any build**, so `want_window=True` is correct here and
  there is nothing for Xvfb to hide (`tools/check.py:424-426`). Do not wrap a Windows `.exe` in
  `xvfb-run`.
- **An import performed by the Linux Godot build does not satisfy the Windows editor**, which
  re-imports on open (LF-075). A Windows parity run may trigger an import; it must not rebuild
  `.godot/` while the owner is playing out of the same tree.
- **Killing the parity check does not kill its Godot** (`CLAUDE.md`) — and under WSL interop the
  process tree is a Windows process reachable only awkwardly from Linux. Confirm `tools/reap.py`
  can actually see and kill it; if it cannot, that is a follow-up item, not a footnote.
- The rules use none of the divergent ops **today**. Do not let this issue's green result be
  read as "we are safe" — {{BAL-07}} is what keeps it true, and PRD §2.1's off-grid geometry is
  the change that makes it matter.
- `tan` at 4.32% is the standout. If any presentation code ever migrates into the rules, that is
  the first thing to check for.

## Files likely touched

- `tools/test_parity.py`, `tools/toolpaths.py`
- `tools/check.py` (one new tier-4 check)
- `.github/workflows/nightly.yml` (from {{PRC-08}})
- `CLAUDE.md`, `docs/DECISIONS.md`, `backlog.json`, `docs/BACKLOG.md`
