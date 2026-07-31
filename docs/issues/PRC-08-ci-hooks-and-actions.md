id: PRC-08
title: CI — pre-commit and pre-push hooks, and a GitHub Actions workflow running tier 1
labels: phase-1, tooling, process
depends: PRC-03, PRC-04
milestone: E1 Process
---
## Problem

**There is no CI at all.** There is no `.github/` directory in this repository, and the only
git hooks installed are git-lfs's own (`.git/hooks/pre-push` and `post-commit` are the
generated `git lfs …` shims). Every guarantee this project makes — schema validation, parity,
nomenclature, accessibility — rests on a human remembering to type
`.venv/bin/python tools/check.py`, on a gate that takes **eleven minutes**
(`docs/STATE.md`). Decision 051's whole finding was that a rule which depends on remembering
does not hold; this is the largest remaining one.

With {{PRC-04}}'s tiers the cost objection disappears: **~6 s pre-commit, ~14 s pre-push**,
and a PR tier of ~66 s that a runner can absorb.

## Tasks

- [ ] Write `tools/install_hooks.py` — idempotent, re-runnable, and **chaining**: the existing
      `.git/hooks/pre-push` is git-lfs's shim and clobbering it breaks LFS pushes for a repo
      whose 224 sprite renders live in LFS. Detect the lfs shim, move it aside to
      `pre-push.lfs`, and have the new hook call it first, failing the push if it fails.
- [ ] `pre-commit` runs `tools/check.py --tier 1 --json .cache/gate-precommit.json` and blocks
      on failure. Document the escape (`git commit --no-verify`) in the hook's own error
      message — an unbypassable hook gets uninstalled.
- [ ] `pre-push` runs `--tier 2`, after the lfs shim.
- [ ] Make the hooks tolerate a missing `.venv` (a fresh clone) by skipping with a loud
      message rather than blocking every commit on a machine that has not bootstrapped.
- [ ] Add `.github/workflows/gate.yml`: on `pull_request` and on `push` to `main`, ubuntu
      runner, checkout with `lfs: false` (the gate's tier 1 and 2 do not read sprite pixels —
      confirm that `sprite atlas` does, and tier it accordingly), set up Python, create the
      venv, run `--tier 1 --json gate.json`.
- [ ] Upload `gate.json` as an artifact and post it as a PR comment with {{PRC-03}}'s
      `tools/gate_report.py` renderer. One renderer, so the comment and the terminal cannot
      disagree.
- [ ] **Say honestly what CI cannot do here.** `game renders`, `menu renders`, `accessibility`
      and `rules parity` need a Godot binary; on Linux they additionally need `xvfb-run` and a
      Mesa software-GL stack (decision 052). A hosted `ubuntu-latest` runner has none of that
      out of the box. Document the two options in the workflow's own comments: a
      **self-hosted runner** on the owner's machine, or a **container image** carrying
      `Godot_v4.7.1-stable_linux.x86_64` + `xvfb` + `mesa-utils`. Do not silently leave those
      checks out of CI without the file saying so.
- [ ] Add a second, manually-dispatchable workflow (`workflow_dispatch` + nightly `schedule`)
      that runs `--tier 4` on a self-hosted runner, gated behind a repository variable so it
      is inert until a runner exists. It should shard parity via {{PRC-05}}'s `--shard I/N`.
- [ ] Make the tier-1 job fail loudly if any check reports `skip` with
      `skipped_reason: "subsystem"` — in CI, a missing subsystem is a broken environment, not
      an acceptable skip.
- [ ] Add `.github/pull_request_template.md` with the three lines this project actually cares
      about: which gate tier was run, what `tools/reap.py` printed, and which decision entry
      (if any) the change adds.
- [ ] Run `tools/issues.py labels` context aside: ensure the workflow does not need `gh` auth
      beyond the default `GITHUB_TOKEN` for the PR comment (`pull-requests: write`).
- [ ] Update `CLAUDE.md`'s Commands block with `tools/install_hooks.py` and record in
      `docs/STATE.md` that CI exists and exactly what it covers.

## Acceptance criteria

- `tools/install_hooks.py` run twice produces the same `.git/hooks/pre-commit` and
  `pre-push` (idempotent), and `git lfs push` still works afterwards — verified by an actual
  push of an LFS-tracked file, not by reading the script.
- A commit that breaks `data/towers.json` is blocked by `pre-commit` with the failing check
  named; `git commit --no-verify` still lets it through.
- A push whose tree fails `--tier 2` is blocked; the lfs shim ran first (visible in output).
- Opening a PR produces a comment containing the tier-1 table, and a `gate.json` artifact.
- The workflow file contains an explicit comment block naming the four checks CI does not run
  and the two ways to make it run them.
- A deliberately removed `sim/` directory in CI produces a red run (subsystem skip escalated),
  not a green one.

## Verification

```bash
.venv/bin/python tools/install_hooks.py && .venv/bin/python tools/install_hooks.py
head -20 .git/hooks/pre-commit .git/hooks/pre-push
git commit --allow-empty -m "probe"        # expect tier 1 to run
# CI: open a draft PR and confirm the comment and the artifact
```

## Risks / gotchas

- **git-lfs owns `pre-push` and `post-commit` today.** Overwriting `pre-push` silently breaks
  LFS for a repo with 415 MB of history discipline behind it (decision 012, `music_manifest`).
  Chain, do not replace, and verify with a real push.
- **A CI runner with no Godot will report `godot not installed` as a SKIP and the gate exits
  0.** That is by design locally and is a lie in CI. Escalating subsystem skips to failures in
  CI is the whole point of that task box.
- **`--no-window` and `--tier` are not the same claim.** Do not use `--no-window` in CI to make
  a red run green; use the tier, so the report says which checks were out of scope.
- Do not run the gate in CI against a shallow clone if {{PRC-05}}'s digest reads `git ls-files`
  — confirm behaviour under `actions/checkout` defaults.
- The gate opens a **real window** on any machine without a native Linux Godot + `xvfb-run`
  (`docs/STATE.md`). A self-hosted runner on the owner's desktop must be the WSL2 Linux path,
  or CI starts popping windows over their work — the LF-061 failure with a new trigger.
- CI must not run `tools/reap.py --kill` unscoped on a self-hosted runner shared with the
  owner's own sessions. {{PRC-07}} is the prerequisite for that, and the nightly workflow
  should pass a lease-scoped reap.

## Files likely touched

- `tools/install_hooks.py` (new)
- `.github/workflows/gate.yml`, `.github/workflows/nightly.yml` (new)
- `.github/pull_request_template.md` (new)
- `tools/gate_report.py` (from {{PRC-03}})
- `CLAUDE.md`, `docs/STATE.md`
