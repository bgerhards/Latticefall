id: PRC-17
title: CI has never run past tier 1 — no self-hosted runner exists, and nightly.yml has zero runs
labels: phase-1, tooling, process, risk
depends: PRC-08
milestone: E1 Process
---
## Problem

Testing-audit finding #2. `docs/STATE.md` describes `nightly.yml` as "inert behind a repo
variable", which reads as a one-flag fix. It is not. Measured directly against this
repository, today:

```
$ gh variable list
(empty — no repository variables are set at all, LF_NIGHTLY_ENABLED does not exist)

$ gh api repos/{owner}/{repo}/actions/runners
{"total_count": 0, "runners": []}

$ gh run list --workflow=nightly.yml --limit 5
(empty — nightly.yml has never executed once, on any trigger, ever)
```

`nightly.yml` targets `runs-on: [self-hosted, linux, latticefall]`. **No runner with those
labels — or any labels — is registered against this repository at all.** Flipping
`LF_NIGHTLY_ENABLED` to `true` today would not make the workflow run; it would make the job
queue forever with nothing able to claim it. The workflow is not "behind a flag", it is behind
infrastructure that does not exist yet.

`gate.yml` (tier 1 only) is the *only* CI that has ever executed:

```
$ gh run list --workflow=gate.yml --limit 10
10 runs: 4 success, 6 failure
```

Every failure in that history is CI's own onboarding breakage, not a caught regression.
Confirmed by reading the actual failed step output rather than trusting the conclusion field:
run `30639316129`'s `gdscript parses` failed on
`scripts/anchor_view.gd:191: Parse Error: Identifier "Loadout" not declared in the current
scope` — the exact `class_name`-cache gap `gate.yml`'s own header comment already documents
as case (1) of "why the Import step exists" — fixed two commits later by adding that step. No
run in this project's CI history has ever turned red because of a real bug in game or rule
code; the population is small (10 runs) but it is the *entire* population, and every single
red run says the same thing: CI was still being built, not yet watching the game.

**The consequence for coverage.** Every merge to `main` is currently gated on 14 of 29 checks
— the tier-1 set (`python syntax`, `json parses`, `gdscript parses`, `game data`,
`wave density`, `dialog capacity`, `backlog rendered`, `agent models`, `leases wired`,
`banned terms`, `safe operations`, `rules autoloads`, `yaw hysteresis`, `asset coverage`). The
other 15 — `sfx determinism`, `music manifest`, `sprite atlas`, `sprite coverage`,
`sim determinism`, `godot boots`, `scenarios pass`, `game renders`, `menu renders`,
`accessibility`, `terrain parsers agree`, `hooks configured`, `facing harness`, and —
critically — **`rules parity` and `rules parity (windows)`** have run in CI **zero times**.
`rules parity` is, in the gate's own words, "the one thing making a balance claim in this
project falsifiable" (PRD §1, `docs/DECISIONS.md`). It currently depends entirely on a human
remembering to run `check.py` with no `--tier` flag before merging — which `CLAUDE.md` already
names as the exact failure mode tiering was supposed to fix ("nobody runs an eleven-minute
check before every commit"). Tiering fixed the *local* version of that problem and, so far,
reproduced it at the CI layer instead.

This is not speculative. It is the measured, current, and — until this issue — undocumented
state of the project's actual safety net.

## Tasks

- [ ] **Decide the self-hosted path (owner call).** Either register a runner with labels
      `self-hosted, linux, latticefall` on a machine that already satisfies decision 052
      (native Linux Godot + `xvfb-run` + Mesa — this WSL2 box already qualifies), or explicitly
      decline to and say so in `docs/STATE.md` rather than leaving an unregistered workflow
      that reads as though it is only "turned off".
- [ ] If accepted: register the runner, then set `LF_NIGHTLY_ENABLED=true`
      (`gh variable set LF_NIGHTLY_ENABLED --body true`) — only after confirming
      `tools/reap.py`'s lease-scoped `--kill` (PRC-07/LF-133) is safe to run unattended on a
      machine the owner also plays from interactively; `nightly.yml`'s own header comment
      already refuses `--kill` there for exactly that reason and only reports.
- [ ] **Independently of the runner decision**, build the hosted-runner container image
      `gate.yml`'s own header comment names as "option 2" (`Godot_v4.7.1-stable_linux.x86_64` +
      `xvfb-run` + a software-GL Mesa stack, baked in) and add a tier-2/tier-3 job to CI using
      it, with `lfs: true` (unlike the tier-1 job, tier 2/3 read real sprite/audio bytes — see
      `gate.yml`'s own comment on why it is `lfs: false` today). This is available immediately,
      needs no infrastructure decision, and would put `sprite atlas`, `sfx determinism`,
      `godot boots`, `terrain parsers agree`, `game renders`, `menu renders`, and
      `accessibility` onto every PR. Only `rules parity`/`rules parity (windows)` (the
      ~9–11-minute pair) need stay nightly/self-hosted-only.
- [ ] Measure the container's `game renders`/`accessibility` coverage numbers once it exists —
      those thresholds were tuned on this specific machine's Mesa llvmpipe output, and a
      different virtualised GPU-less environment is not guaranteed to reproduce them; log the
      first real numbers rather than assuming.
- [ ] Add a freshness check: something that notices when the *last successful* nightly run is
      more than 48h old and says so loudly — "the workflow file exists" and "the workflow ran"
      have now been shown to be different claims on this project, and a silently-broken
      schedule is exactly how that happens again unnoticed.
- [ ] Rewrite `docs/STATE.md`'s "nightly.yml is inert behind a repo variable" line to state
      the verified fact: no runner exists, zero runs in history, and which checks that means
      have never run in CI at all. Update `CLAUDE.md` if it implies otherwise anywhere.

## Acceptance criteria

- Either `gh api repos/{owner}/{repo}/actions/runners` lists a registered runner AND
  `gh run list --workflow=nightly.yml` shows at least one completed run, or `docs/STATE.md`
  explicitly states the self-hosted path was declined and why.
- `gate.yml` (or a new workflow) runs a tier-2/tier-3 job on a hosted runner via container
  image, green on a real PR, with `sprite atlas`/`godot boots`/`game renders`/`menu renders`/
  `accessibility`/`terrain parsers agree`/`sfx determinism` all reporting `ok` rather than
  `skip`.
- `docs/STATE.md` no longer describes the nightly gap with softer language than the measured
  reality.

## Verification

```bash
gh variable list
gh api repos/{owner}/{repo}/actions/runners
gh run list --workflow=nightly.yml --limit 5
gh run list --workflow=gate.yml --limit 10 --json conclusion,displayTitle,createdAt
# after the container job lands:
gh run list --workflow=gate.yml --limit 1 --json conclusion
gh run view <run-id> --log | grep -E "\[ FAIL|\[  ok  \]" | wc -l   # expect 27+ (tier 3)
```

## Risks / gotchas

- Running a self-hosted runner on the owner's own dev machine means a scheduled nightly job
  competes for the same resources concurrent Godot captures already contend for measurably
  (LF-116: one capture takes ~8 of 16 cores). A scheduled Actions job is a different process
  tree than anything `tools/lease.py` currently instruments — confirm the lease/reap machinery
  actually sees it before enabling anything unattended on shared hardware.
- A container image's software-GL stack is not automatically the same Mesa llvmpipe behaviour
  measured on this machine — verify the coverage/contrast thresholds `check_game_renders`/
  `check_accessibility` assert still mean the same thing there before trusting a green run.
- Do not let "the workflow file exists" read as "the tests run" ever again — that reading was
  false in this project's own STATE.md on the same day the tiered gate was built, and nothing
  caught it until this audit.

## Files likely touched

- `.github/workflows/nightly.yml`, `.github/workflows/gate.yml` (or a new workflow file)
- A new Dockerfile/image definition (e.g. under `tools/ci/`)
- `CLAUDE.md`, `docs/STATE.md`
