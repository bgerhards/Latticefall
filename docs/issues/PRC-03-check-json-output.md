id: PRC-03
title: check.py --json, so CI and a PR comment can consume a gate result
labels: phase-0, tooling
blocks: PRC-08
milestone: E1 Process
---
## Problem

`tools/check.py` emits one human line per check —
`[  ok  ] banned terms          28678ms  132 files clean` — and a summary line, then exits 0
or 1 (`tools/check.py:747-767`). Everything downstream has to scrape it. `tools/session.py`
already does exactly that to regenerate `docs/STATE.md`'s gate block, and the block is
currently annotated *"(from gate.txt, not re-run)"* because there is no machine-readable
artefact to carry forward. There is **no CI at all** (no `.github/` directory), so the moment
{{PRC-08}} lands, a workflow has to turn 18 check results into a PR comment by parsing
column-aligned text whose field widths are `%-20s`/`%6.0f` format strings.

An exit code also cannot express the distinction the file goes out of its way to make in
prose: a `skip` because a subsystem does not exist is not the same claim as a `skip` because
`--no-window` was passed (`tools/check.py:759-766`), and both are exit 0 today.

## Tasks

- [ ] Define the schema and write it down in the module docstring: top-level
      `{schema, version, started_at, duration_ms, root_commit, tier, checks: [...], summary}`
      where each check is
      `{name, status: ok|FAIL|skip, ms, detail, skipped_reason: null|"subsystem"|"flag"|"cached"}`.
- [ ] Extend `Result` (`tools/check.py:36-38`) with an optional `skipped_reason`, and set it at
      the two existing skip sites and the `--no-window` branch (`tools/check.py:738-741`).
- [ ] Add `--json [PATH]` to `main()`: with no path, write JSON to stdout and suppress the
      human lines; with a path, write both. CI wants the file; a human running it wants the
      table.
- [ ] Keep the human output byte-identical when `--json` is absent. `tools/session.py` parses
      it and `docs/STATE.md`'s gate block is generated from it.
- [ ] Record `root_commit` from `git rev-parse HEAD` and a `dirty` boolean from
      `git status --porcelain`, so a pasted result can be tied to a tree.
- [ ] Include per-check `ms` for every check including skipped ones (0 ms), so
      {{PRC-04}}'s tier budgets and {{PRC-05}}'s cost-balanced sharding have a data source
      that is not a screenshot of a terminal.
- [ ] Make `detail` the **full** multi-line detail, not the first line. The human table prints
      `detail.splitlines()[0]` and indents the rest; JSON should carry all of it, because that
      is what a PR comment needs to be useful.
- [ ] Rewrite `tools/session.py` to prefer the JSON artefact when one exists and fall back to
      parsing text, then drop the "(from gate.txt, not re-run)" caveat when JSON is present.
- [ ] Add a tiny `tools/gate_report.py` (or a function in `issues.py`-style) that renders the
      JSON as a GitHub-flavoured markdown table, for {{PRC-08}} to post. One code path, so the
      PR comment and the terminal never disagree.
- [ ] Ensure the JSON is written even when a check raises — the existing `except Exception`
      wrapper (`tools/check.py:744-745`) must still produce a record with
      `status: FAIL, detail: "check itself raised: …"`.
- [ ] Write the JSON to a caller-supplied path only; never a fixed path inside `.godot/`
      (see {{PRC-15}} — concurrent gate runs already collide on fixed artefact paths).
- [ ] Update `CLAUDE.md`'s Commands block with the `--json` invocation.

## Acceptance criteria

- `tools/check.py --json /tmp/gate.json --no-window` writes a file that
  `json.loads()` parses, containing exactly 18 check entries.
- `jq -r '.checks[] | select(.status=="FAIL") | .name' /tmp/gate.json` lists precisely the
  checks that printed ` FAIL `.
- The three `--no-window` skips carry `skipped_reason: "flag"`; a genuinely missing subsystem
  carries `skipped_reason: "subsystem"`.
- `.venv/bin/python tools/check.py --no-window` with no `--json` produces output byte-identical
  to the same command on `main` (verify with `diff`).
- `tools/session.py` regenerates `docs/STATE.md`'s gate block from the JSON and the block no
  longer says "not re-run".
- The exit code is unchanged: 0 unless something failed.

## Verification

```bash
.venv/bin/python tools/check.py --no-window > /tmp/before.txt 2>&1 || true      # on main
# after the change:
.venv/bin/python tools/check.py --no-window > /tmp/after.txt 2>&1 || true
diff /tmp/before.txt /tmp/after.txt          # must be empty (timings aside — normalise with sed)
.venv/bin/python tools/check.py --no-window --json /tmp/gate.json ; echo "exit=$?"
.venv/bin/python -c "import json;d=json.load(open('/tmp/gate.json'));print(len(d['checks']), d['summary'])"
```

Proof: 18 entries, a `summary` matching the printed tally, and an empty `diff` for the human
path.

## Risks / gotchas

- **Python block-buffers a redirected stdout** (`docs/STATE.md`) — a `--json` run piped to a
  file shows nothing until it ends, which is indistinguishable from a stall. Use `python -u`
  in CI and in any wrapper.
- Do not make `--json` imply `--no-window`. A JSON run that quietly skipped the three rendered
  checks is exactly the "green run that skipped half the suite" this file's own docstring
  forbids.
- `git rev-parse` inside a detached CI checkout still works, but `git status --porcelain` in a
  fresh clone is empty even when the workflow patched files — do not treat `dirty: false` as
  proof of anything.
- The parity check's detail line is `r.stdout.strip().replace("parity ok — ", "")`; carrying
  the full detail into JSON means carrying whatever `test_parity.py` prints. Keep it a string,
  do not try to structure it here.

## Files likely touched

- `tools/check.py`
- `tools/session.py`
- `tools/gate_report.py` (new, small)
- `CLAUDE.md`
