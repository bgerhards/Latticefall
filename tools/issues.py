#!/usr/bin/env python3
"""
Create and update GitHub issues from the plain-text specs in `docs/issues/`.

Why this exists rather than a pile of `gh issue create` calls: an issue that carries a
dependency ("blocked by the spatial hash") is only useful if the dependency is a *link* a
human can click, and the issue it points at may not exist yet. So this is a two-pass tool.
Pass one creates every issue and records the number it was assigned in `docs/issues/.map.json`;
pass two rewrites each body, replacing `{{TS-03}}` placeholders with real `#N` references, and
pushes the updated bodies. Hand-maintaining that cross-reference across ~40 issues is exactly
the kind of bookkeeping that goes stale on the second edit.

The specs are the source of truth and live in git. GitHub is a projection of them, which
means a lost repo, a moved org or a rate limit costs nothing — re-run and the issues come
back with the same content. It also means an issue can be reviewed in a pull request before
it is ever filed.

Spec format — a markdown file per issue, with a YAML-ish header ending at the first `---`:

    id: TS-03
    title: Spatial hash for target acquisition
    labels: perf, engine, phase-1
    depends: TS-01, TS-02          # optional; rendered as a blocked-by list with links
    blocks: TS-07                  # optional; the inverse, for readability
    milestone: Phase 1             # optional
    ---
    <markdown body>

Everything after the `---` is the body, verbatim. Dependency lists are rendered into a
"Dependencies" section appended to the body, so the spec author writes the graph once as ids
and never writes an issue number by hand.

Usage:
    tools/issues.py plan                 # parse and validate specs; print what would happen
    tools/issues.py create               # create missing issues, record numbers
    tools/issues.py sync                 # rewrite bodies with resolved cross-references
    tools/issues.py all                  # create then sync
    tools/issues.py labels               # ensure every label used by a spec exists

Requires `gh` authenticated against the repo's origin remote. `gh auth status` is checked up
front and the failure message says exactly what to run, because "HTTP 401" is not an
actionable error for whoever picks this up next.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "docs" / "issues"
MAP_PATH = SPEC_DIR / ".map.json"

## Colours are only a readability aid in the GitHub UI, but a label created ad hoc by
## `gh issue create` gets a random one, and forty issues in forty colours is noise. Declared
## here so the set is auditable in one place.
LABEL_COLOURS = {
    "phase-0": "6E5494", "phase-1": "1D76DB", "phase-2": "0E8A16",
    "phase-3": "FBCA04", "phase-4": "D93F0B",
    "engine": "0052CC", "rules": "5319E7", "art": "C2E0C6", "audio": "BFD4F2",
    "tooling": "FEF2C0", "design": "F9D0C4", "ui": "D4C5F9", "content": "C5DEF5",
    "perf": "E99695", "risk": "B60205", "process": "BFDADC", "epic": "0B3D91",
}

REF = re.compile(r"\{\{([A-Z]{2,4}-\d{2,3})\}\}")


def run(args: list[str], check: bool = True) -> str:
    p = subprocess.run(args, capture_output=True, text=True)
    if check and p.returncode != 0:
        sys.exit(f"command failed: {' '.join(args)}\n{p.stderr.strip()}")
    return p.stdout.strip()


def require_gh() -> None:
    if not _which("gh"):
        sys.exit("gh is not installed. See https://cli.github.com/ — on Debian/Ubuntu:\n"
                 "  sudo apt-get install gh")
    p = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("gh is not authenticated for this repo.\n"
                 "Run this yourself (it is interactive and opens a browser):\n"
                 "  gh auth login --hostname github.com --git-protocol ssh --web\n"
                 "then re-run this command.")


def _which(name: str) -> str | None:
    from shutil import which
    return which(name)


def parse_spec(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if "\n---\n" not in text:
        sys.exit(f"{path.name}: no '---' separating the header from the body")
    head, body = text.split("\n---\n", 1)
    meta: dict = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            sys.exit(f"{path.name}: header line is not 'key: value' -> {line!r}")
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    for required in ("id", "title"):
        if required not in meta:
            sys.exit(f"{path.name}: header is missing '{required}'")
    for listy in ("labels", "depends", "blocks"):
        raw = meta.get(listy, "")
        meta[listy] = [x.strip() for x in raw.split(",") if x.strip()]
    meta["body"] = body.strip("\n")
    meta["path"] = path
    return meta


def load_specs() -> list[dict]:
    if not SPEC_DIR.is_dir():
        sys.exit(f"no spec directory at {SPEC_DIR}")
    specs = [parse_spec(p) for p in sorted(SPEC_DIR.glob("*.md"))]
    ids = [s["id"] for s in specs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        sys.exit(f"duplicate issue ids: {', '.join(sorted(dupes))}")
    known = set(ids)
    # Derive the inverse edge from `depends`, which is authoritative.
    for s in specs:
        s["_blocked"] = sorted(o["id"] for o in specs if s["id"] in o["depends"])
    for s in specs:
        for dep in s["depends"] + s["blocks"]:
            if dep not in known:
                sys.exit(f"{s['path'].name}: references unknown id {dep}")
        for ref in REF.findall(s["body"]):
            if ref not in known:
                sys.exit(f"{s['path'].name}: body references unknown id {ref}")
    return specs


def load_map() -> dict:
    return json.loads(MAP_PATH.read_text()) if MAP_PATH.exists() else {}


def save_map(m: dict) -> None:
    MAP_PATH.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")


def render_body(spec: dict, mapping: dict) -> str:
    """The body with `{{ID}}` refs and the dependency block resolved to issue numbers.

    An unresolved id renders as the bare id rather than a broken link — `create` runs before
    every number is known, and half a graph is more useful than a crash."""
    def sub(m: re.Match) -> str:
        num = mapping.get(m.group(1))
        return f"#{num}" if num else m.group(1)

    body = REF.sub(sub, spec["body"])
    lines = []
    if spec["depends"]:
        refs = ", ".join(f"#{mapping[d]}" if d in mapping else d for d in spec["depends"])
        lines.append(f"**Blocked by:** {refs}")
    # `blocks` is DERIVED, never taken from the header. Two hand-authored lists describing
    # one edge drift the moment either side is edited, and they did: of 13 hand-written
    # `blocks:` claims in the first pass, most were merely transitive and three contradicted
    # the other issue's own `depends` outright — one spec asserted it blocked work that the
    # PRD deliberately wants running in parallel. `depends` is the single source of truth;
    # the header's `blocks` is kept only so `plan` can report the disagreement.
    inverse = spec.get("_blocked", [])
    if inverse:
        refs = ", ".join(f"#{mapping[b]}" if b in mapping else b for b in inverse)
        lines.append(f"**Blocks:** {refs}")
    if lines:
        body += "\n\n---\n\n### Dependencies\n\n" + "\n\n".join(lines) + "\n"
    body += f"\n\n<sub>Spec: `docs/issues/{spec['path'].name}` — edit there, then `tools/issues.py sync`.</sub>\n"
    return body


def cmd_plan(_a) -> None:
    specs = load_specs()
    mapping = load_map()
    print(f"{len(specs)} spec(s) in {SPEC_DIR.relative_to(ROOT)}")
    labels = sorted({l for s in specs for l in s["labels"]})
    print(f"labels used: {', '.join(labels)}")
    unknown = [l for l in labels if l not in LABEL_COLOURS]
    if unknown:
        print(f"  (no declared colour for: {', '.join(unknown)} — will be created grey)")
    stale = [(s["id"], b) for s in specs for b in s["blocks"]
             if s["id"] not in next(o for o in specs if o["id"] == b)["depends"]]
    if stale:
        print(f"\n{len(stale)} hand-written 'blocks:' claim(s) the other issue does not "
              f"confirm — ignored, `depends` wins:")
        for a, b in stale:
            print(f"  {a} claims it blocks {b}; {b} does not list {a} in depends")
        print()
    for s in specs:
        state = f"#{mapping[s['id']]}" if s["id"] in mapping else "NEW"
        dep = f"  <- {', '.join(s['depends'])}" if s["depends"] else ""
        print(f"  {state:>6}  {s['id']:8} {s['title'][:66]}{dep}")


def cmd_labels(_a) -> None:
    require_gh()
    specs = load_specs()
    existing = set()
    out = run(["gh", "label", "list", "--limit", "200", "--json", "name"], check=False)
    if out:
        existing = {x["name"] for x in json.loads(out)}
    for label in sorted({l for s in specs for l in s["labels"]}):
        if label in existing:
            continue
        colour = LABEL_COLOURS.get(label, "CCCCCC")
        run(["gh", "label", "create", label, "--color", colour], check=False)
        print(f"label + {label}")


def cmd_create(_a) -> None:
    require_gh()
    specs = load_specs()
    mapping = load_map()
    for s in specs:
        if s["id"] in mapping:
            continue
        args = ["gh", "issue", "create", "--title", f"[{s['id']}] {s['title']}",
                "--body", render_body(s, mapping)]
        for l in s["labels"]:
            args += ["--label", l]
        if s.get("milestone"):
            args += ["--milestone", s["milestone"]]
        url = run(args)
        num = url.rstrip("/").rsplit("/", 1)[-1]
        mapping[s["id"]] = int(num)
        save_map(mapping)          # save per issue: a rate limit halfway through loses nothing
        print(f"created #{num}  {s['id']}  {s['title'][:60]}")
    print(f"{len(mapping)} issue(s) tracked")


def cmd_sync(_a) -> None:
    require_gh()
    specs = load_specs()
    mapping = load_map()
    missing = [s["id"] for s in specs if s["id"] not in mapping]
    if missing:
        sys.exit(f"not created yet: {', '.join(missing)} — run `create` first")
    for s in specs:
        run(["gh", "issue", "edit", str(mapping[s["id"]]),
             "--title", f"[{s['id']}] {s['title']}",
             "--body", render_body(s, mapping)])
        print(f"synced #{mapping[s['id']]}  {s['id']}")


def cmd_close(a: argparse.Namespace) -> None:
    """Close the issues for one or more spec ids, each with a note saying what landed.

    This exists because it did not, and the projection silently rotted for a whole
    session: twenty-nine specs were implemented, verified and committed while every one
    of their GitHub issues stayed open, because `create` and `sync` are the only verbs
    and neither has anything to say about an issue being *finished*. A projection that
    can only ever grow is not a projection of the work.

    The note is required rather than optional. "Closed" with no comment leaves the
    evidence in a commit message nobody will find from the issue, and this project's
    whole method is that a claim is falsifiable — so the close carries the number, the
    measurement or the decision that settles it.
    """
    require_gh()
    mapping = load_map()
    missing = [i for i in a.ids if i not in mapping]
    if missing:
        raise SystemExit(f"no issue recorded for: {', '.join(missing)} — run `create` first")
    for spec_id in a.ids:
        n = mapping[spec_id]
        r = subprocess.run(["gh", "issue", "close", str(n), "--comment", a.note],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"gh issue close {n} failed: {r.stderr.strip()}")
        print(f"closed #{n}  {spec_id}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_close = sub.add_parser("close", help="close the issue(s) for one or more spec ids")
    p_close.add_argument("ids", nargs="+", metavar="ID", help="spec ids, e.g. PRC-02 CAM-01")
    p_close.add_argument("--note", required=True,
                         help="what landed and how it was proved — required, because a "
                              "bare close leaves the evidence somewhere nobody will look")
    sub.add_parser("plan", help="parse and validate specs, print what would happen")
    sub.add_parser("labels", help="create any label a spec uses that does not exist yet")
    sub.add_parser("create", help="create missing issues and record their numbers")
    sub.add_parser("sync", help="rewrite every body with resolved cross-references")
    sub.add_parser("all", help="create, then sync")
    a = ap.parse_args()
    if a.cmd == "plan":
        cmd_plan(a)
    elif a.cmd == "labels":
        cmd_labels(a)
    elif a.cmd == "create":
        cmd_create(a)
    elif a.cmd == "sync":
        cmd_sync(a)
    elif a.cmd == "close":
        cmd_close(a)
    elif a.cmd == "all":
        cmd_labels(a); cmd_create(a); cmd_sync(a)


if __name__ == "__main__":
    main()
