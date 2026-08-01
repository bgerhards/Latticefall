#!/usr/bin/env python3
"""
Generate the Latticefall build journal's HTML from `docs/chronicle/chronicle.json`.

Why a generator at all, rather than hand-written pages: `docs/chronicle/` publishes to
GitHub Pages on every push to `main` with no build step (see `.github/workflows/pages.yml`),
so whatever is committed under that directory is exactly what a reader gets. Hand-edited
HTML and a hand-edited index drift from each other the moment one is updated and the other
is forgotten — the same reason `data/*.json` is the source of truth for game content and the
schemas in `data/schema/` are what CI checks it against, and the same reason
`tools/issues.py` treats `docs/issues/*.md` as the source and the GitHub issues it creates as
a disposable projection of them.

`chronicle.json` is that source here. It is an ordered list of entries — date, title,
summary, tags, decision references, commits, and a body made of small typed blocks
(paragraph, heading, quote, list, table, image). This script is the only thing that turns
that data into `index.html` and `entries/*.html`. It is idempotent: running it twice with an
unchanged `chronicle.json` produces byte-identical output, because nothing here reads the
clock or the filesystem beyond the JSON and the already-committed images it references.

History itself is append-only — that rule lives in the data, not in this script. This file
will happily regenerate an edited entry if asked to; it is `.claude/agents/chronicler.md`
and the working agreement in `CLAUDE.md` that say a past entry is never rewritten, only
superseded by a new one. This script's job is projection, not policy.

Usage:
    tools/chronicle.py              # regenerate index.html and entries/*.html
    tools/chronicle.py --check      # regenerate into a temp dir and diff against committed
                                     # output; exits 1 if the committed HTML is stale
"""

from __future__ import annotations

import argparse
import filecmp
import html
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CHRONICLE_DIR = ROOT / "docs" / "chronicle"
DATA_PATH = CHRONICLE_DIR / "chronicle.json"
ENTRIES_DIR = CHRONICLE_DIR / "entries"
CSS_FILE = "chronicle.css"

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def load_data() -> dict[str, Any]:
    with DATA_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        sys.exit(f"{DATA_PATH}: 'entries' must be a non-empty list")
    seen_ids: set[str] = set()
    for e in entries:
        for key in ("id", "date", "title", "summary", "body"):
            if key not in e:
                sys.exit(f"{DATA_PATH}: entry missing required key {key!r}: {e.get('id', '?')}")
        if e["id"] in seen_ids:
            sys.exit(f"{DATA_PATH}: duplicate entry id {e['id']!r}")
        seen_ids.add(e["id"])
    return data


def fmt_date(date: str, time: str | None) -> str:
    """'2026-07-30' -> 'July 30, 2026', optionally with a trailing time."""
    y, m, d = date.split("-")
    out = f"{MONTHS[int(m) - 1]} {int(d)}, {y}"
    if time:
        out += f" · {time}"
    return out


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def _wrap_pairs(s: str, delim: str, tag: str) -> str:
    """Wrap `delim`-delimited spans in `<tag>`, left to right, leaving anything unpaired as
    literal text.

    A delimiter only opens a span if a closing one exists AND the content between them is
    non-empty and not whitespace-bounded. That guard is what keeps a stray `**` in prose
    (`sim/**`, a glob) from emitting an empty `<strong></strong>` — the naive
    `split(delim)` this replaced had no way to express "this delimiter is not markup",
    so every occurrence became a tag boundary whether or not it was paired.
    """
    out: list[str] = []
    i, n, d = 0, len(s), len(delim)
    while i < n:
        if s.startswith(delim, i):
            close = s.find(delim, i + d)
            content = s[i + d:close] if close != -1 else ""
            if close != -1 and content and not content[0].isspace() and not content[-1].isspace():
                out.append(f"<{tag}>{content}</{tag}>")
                i = close + d
                continue
            out.append(delim)          # unpaired — literal, not markup
            i += d
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def render_inline(text: str) -> str:
    """Escape text, then apply a tiny code/bold markup so body prose can carry emphasis
    without every entry hand-writing HTML in the data file. `` `x` `` -> <code>,
    `**x**` -> <strong>. Deliberately not a full markdown parser: the data file's blocks
    already carry structure (heading, list, table), so inline markup only ever needs these
    two.

    **Code spans are extracted FIRST and never re-scanned, and that ordering is the fix for
    LF-173.** The previous version wrapped code spans and THEN ran a `split("**")` over the
    whole string, including the text it had just placed inside `<code>`. A glob inside
    backticks therefore came out as *crossed* tags — `` `sim/**` `` rendered as
    `<code>sim/<strong></code></strong>` — and worse, one `**` inside a single code span
    inverted the pairing for every bold later on the same line. Both were live on the
    published site. Bold is now applied only to the even-indexed prose segments; whatever is
    inside backticks is verbatim by construction, which is what a code span means.

    **Single-asterisk emphasis is deliberately NOT supported, and that is a reversal of
    LF-173's second half — measured, not assumed.** LF-173 asked for `*x*` -> <em> because
    one entry renders literal asterisks. It was implemented, and then run against the whole
    journal: `chronicle.json` contains exactly two strings carrying a bare `*` outside a code
    span, and they split one-to-one. One is genuine emphasis (`*current accepted bank*`); the
    other is globs in prose — `scripts/*.gd filename against every tools/*.py,
    scripts/test/*.gd and data/scenarios/*.json` — which the feature silently ate into
    `<em>.gd filename against every tools/</em>`. A 50% false-positive rate on the real
    corpus is not a rendering improvement, and the cause is structural rather than a tuning
    problem: `*` is an overloaded delimiter in a project whose prose is full of paths and
    globs, and no amount of pair-guarding distinguishes the two cases. Authors use `**` for
    emphasis, which is unambiguous and already the established habit.

    `_wrap_pairs` keeps its unpaired-delimiter guard even though only `**` uses it now: a
    stray `**` in prose (`sim/**`, a glob) must stay literal rather than emit an empty
    `<strong></strong>`, which the old `split("**")` had no way to express.
    """
    parts = esc(text).split("`")
    for i, part in enumerate(parts):
        if i % 2:
            parts[i] = f"<code>{part}</code>"        # verbatim; never re-scanned
        else:
            parts[i] = _wrap_pairs(part, "**", "strong")
    return "".join(parts)


def render_block(block: dict[str, Any], image_prefix: str) -> str:
    t = block["type"]
    if t == "p":
        return f"<p>{render_inline(block['text'])}</p>"
    if t in ("h2", "h3"):
        return f"<{t}>{render_inline(block['text'])}</{t}>"
    if t == "quote":
        out = f"<blockquote>{render_inline(block['text'])}"
        if block.get("attribution"):
            out += f"<cite>{esc(block['attribution'])}</cite>"
        out += "</blockquote>"
        return out
    if t == "list":
        items = "".join(f"<li>{render_inline(i)}</li>" for i in block["items"])
        return f"<ul>{items}</ul>"
    if t == "table":
        headers = "".join(f"<th>{esc(h)}</th>" for h in block["headers"])
        rows = "".join(
            "<tr>" + "".join(f"<td>{render_inline(str(c))}</td>" for c in row) + "</tr>"
            for row in block["rows"]
        )
        return f'<div class="table-wrap"><table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table></div>'
    if t == "image":
        src = f"{image_prefix}{block['file']}"
        cap = render_inline(block.get("caption", ""))
        return (
            f'<figure><img src="{esc(src)}" alt="{esc(block.get("caption", ""))}" loading="lazy">'
            f"<figcaption>{cap}</figcaption></figure>"
        )
    sys.exit(f"unknown body block type: {t!r}")


def render_chips(entry: dict[str, Any]) -> str:
    chips = []
    for tag in entry.get("tags", []):
        chips.append(f'<span class="chip chip-epic">{esc(tag)}</span>')
    for d in entry.get("decisions", []):
        chips.append(f'<span class="chip chip-decision">decision {esc(str(d))}</span>')
    if not chips:
        return ""
    return f'<div class="chips">{"".join(chips)}</div>'


def render_commits(entry: dict[str, Any]) -> str:
    commits = entry.get("commits", [])
    if not commits:
        return ""
    rows = "".join(
        f'<li><code>{esc(c["hash"])}</code> {render_inline(c["subject"])}</li>' for c in commits
    )
    return f'<section class="commits"><h4>Commits</h4><ul class="commit-list">{rows}</ul></section>'


PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="stylesheet" href="{css}">
</head>
<body>
"""

PAGE_TAIL = """
</body>
</html>
"""


def render_index(data: dict[str, Any]) -> str:
    site = data["site"]
    entries = list(reversed(data["entries"]))  # newest first
    cards = []
    for e in entries:
        date_str = fmt_date(e["date"], None)
        cards.append(
            f'<li class="entry-card">'
            f'<a class="entry-link" href="entries/{esc(e["id"])}.html">'
            f'<time datetime="{esc(e["date"])}">{esc(date_str)}</time>'
            f'<h2>{render_inline(e["title"])}</h2>'
            f"{render_chips(e)}"
            f'<p class="summary">{render_inline(e["summary"])}</p>'
            f"</a></li>"
        )
    body = f"""
<header class="masthead">
  <p class="kicker">{esc(site.get("kicker", "Latticefall"))}</p>
  <h1>{esc(site["title"])}</h1>
  <p class="subtitle">{render_inline(site["subtitle"])}</p>
</header>
<main>
  <ul class="entry-list">
    {"".join(cards)}
  </ul>
</main>
<footer class="site-footer">
  <p>{render_inline(site.get("footer", ""))}</p>
</footer>
"""
    head = PAGE_HEAD.format(
        title=esc(site["title"]),
        description=esc(site["subtitle"]),
        css=CSS_FILE,
    )
    return head + body + PAGE_TAIL


def render_entry(entry: dict[str, Any], all_entries: list[dict[str, Any]]) -> str:
    idx = next(i for i, e in enumerate(all_entries) if e["id"] == entry["id"])
    prev_e = all_entries[idx - 1] if idx > 0 else None
    next_e = all_entries[idx + 1] if idx < len(all_entries) - 1 else None

    body_html = "".join(render_block(b, "../assets/") for b in entry["body"])
    date_str = fmt_date(entry["date"], entry.get("time"))

    nav_parts = ['<a class="back" href="../index.html">&larr; the journal</a>']
    if prev_e:
        nav_parts.append(f'<a class="prev" href="{prev_e["id"]}.html">&larr; {esc(prev_e["title"])}</a>')
    if next_e:
        nav_parts.append(f'<a class="next" href="{next_e["id"]}.html">{esc(next_e["title"])} &rarr;</a>')

    superseded = ""
    if entry.get("superseded_by"):
        superseded = (
            f'<p class="superseded">Superseded by '
            f'<a href="{esc(entry["superseded_by"])}.html">a later entry</a>.</p>'
        )

    body = f"""
<nav class="entry-nav top">{" ".join(nav_parts[:1])}</nav>
<article>
  <header class="entry-header">
    <time datetime="{esc(entry["date"])}">{esc(date_str)}</time>
    <h1>{render_inline(entry["title"])}</h1>
    {render_chips(entry)}
    <p class="summary lede">{render_inline(entry["summary"])}</p>
    {superseded}
  </header>
  <div class="entry-body">
    {body_html}
  </div>
  {render_commits(entry)}
</article>
<nav class="entry-nav bottom">
  {' '.join(f'<span>{p}</span>' for p in nav_parts)}
</nav>
"""
    head = PAGE_HEAD.format(
        title=esc(f'{entry["title"]} — Latticefall build journal'),
        description=esc(entry["summary"]),
        css=f"../{CSS_FILE}",
    )
    return head + body + PAGE_TAIL


def generate(out_dir: Path) -> list[Path]:
    data = load_data()
    entries = data["entries"]
    written = []

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "entries").mkdir(parents=True, exist_ok=True)

    index_path = out_dir / "index.html"
    index_path.write_text(render_index(data), encoding="utf-8")
    written.append(index_path)

    for entry in entries:
        page_path = out_dir / "entries" / f'{entry["id"]}.html'
        page_path.write_text(render_entry(entry, entries), encoding="utf-8")
        written.append(page_path)

    return written


def check(out_dir: Path) -> bool:
    """Regenerate into a scratch directory and diff every file against what is committed."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        generate(tmp_dir)
        ok = True
        for path in sorted(tmp_dir.rglob("*.html")):
            rel = path.relative_to(tmp_dir)
            committed = CHRONICLE_DIR / rel
            if not committed.exists():
                print(f"missing: {rel} (run tools/chronicle.py to generate it)")
                ok = False
            elif not filecmp.cmp(path, committed, shallow=False):
                print(f"stale: {rel} (chronicle.json changed since this was last generated)")
                ok = False
        return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify committed HTML matches chronicle.json; do not write")
    args = ap.parse_args()

    if args.check:
        if check(CHRONICLE_DIR):
            print("chronicle: committed HTML matches chronicle.json")
            sys.exit(0)
        sys.exit(1)

    written = generate(CHRONICLE_DIR)
    print(f"chronicle: wrote {len(written)} files from {DATA_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
