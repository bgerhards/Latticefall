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
(paragraph, heading, quote, list, table, image, pre). This script is the only thing that turns
that data into `index.html` and `entries/*.html`. It is idempotent: running it twice with an
unchanged `chronicle.json` produces byte-identical output, because nothing here reads the
clock or the filesystem beyond the JSON and the already-committed images it references.

**Element content and attribute values are escaped by different functions, `esc()` and
`attr()`, and prose bound for an attribute is stripped to plain text by `render_plain()`
first.** That split is not stylistic. One function called with `quote=False` served both for
59 entries, which is LF-211: a straight double quote in an image caption ended the `alt`
attribute early and spilled the rest of the caption into the tag. The general shape — a
prose field that skips the inline renderer, or a template hole that takes the wrong escaper —
has now bitten four times (table headers in e5e3902, `alt`, the meta description, the entry
navigation's `href`), so the rule is: an interpolation inside `"…"` takes `attr()`, an
interpolation in element content takes `render_inline()` unless it is an identifier.

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
import re
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
    """Escape for ELEMENT CONTENT — `&`, `<`, `>` only.

    Quotes are deliberately left alone here because a `"` inside a text node is not markup
    and `&quot;` in prose is noise. That is safe *only* in element content, which is why
    attribute values go through `attr()` instead. The two used to be one function called
    with `quote=False` everywhere, and the consequence was LF-211: a straight double quote
    in an image caption closed the `alt` attribute early and the rest of the caption leaked
    out of the string and into the tag as bogus attributes. It was worked around by every
    author remembering to type curly quotes, which is not a mechanism.
    """
    return html.escape(s, quote=False)


def attr(s: str) -> str:
    """Escape for an ATTRIBUTE VALUE — `&`, `<`, `>`, `"` and `'`.

    Every interpolation inside a `"..."` in this file's templates must go through this and
    not through `esc()`. Both quote styles are escaped rather than only the double: the
    difference is invisible in correct output and the whole point of LF-211 is that a
    generator which is *nearly* right about escaping is a generator that ships broken tags
    with nothing red anywhere.

    Prose bound for an attribute needs `render_plain()` FIRST — an attribute value cannot
    hold tags, so `render_inline()`'s `<strong>` would arrive as visible `&lt;strong&gt;`.
    """
    return html.escape(s, quote=True)


def _wrap_pairs(s: str, delim: str, tag: str | None) -> str:
    """Wrap `delim`-delimited spans in `<tag>`, left to right, leaving anything unpaired as
    literal text. `tag=None` strips the delimiters and keeps the content bare, which is what
    `render_plain` needs for an attribute value that cannot hold a tag at all.

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
                out.append(content if tag is None else f"<{tag}>{content}</{tag}>")
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

    **Bold pairs ACROSS a code span, which closes LF-189.** The first version of this fix
    applied bold to each prose segment *independently*, so a span shaped like
    ``**see `X` for why**`` opened its bold in segment 0 and closed it in segment 2 and
    neither half ever found its partner — the asterisks printed literally. That is strictly
    safer than the crossed tags it replaced, but it is still wrong output, and it was hit by
    the only author using this renderer in two consecutive entries plus two entries already
    published. So code spans are swapped for opaque sentinels, bold is paired over the whole
    string at once, and the sentinels are restored: the code content cannot participate in
    pairing (a `` `split("**")` `` stays verbatim) while prose on either side of it can.
    `audit_markup` asserts the result, because "an author noticed stray asterisks" is not a
    check.
    """
    stitched, codes = _hide_code_spans(esc(text))
    out = _wrap_pairs(stitched, "**", "strong")
    for i, code in enumerate(codes):
        out = out.replace(f"\x00{i}\x00", f"<code>{code}</code>", 1)
    return out


def _hide_code_spans(text: str) -> tuple[str, list[str]]:
    """Replace `` `x` `` spans with opaque `\\x00N\\x00` sentinels, returning the stitched
    string and the extracted contents in order.

    NUL cannot appear in the source, so a sentinel cannot collide with real content, and it
    carries no asterisk — which is the property `render_inline` relies on to pair bold ACROSS
    a code span without the code span's own characters ever participating (LF-189).
    """
    codes: list[str] = []
    stitched: list[str] = []
    for i, part in enumerate(text.split("`")):
        if i % 2:
            stitched.append(f"\x00{len(codes)}\x00")
            codes.append(part)
        else:
            stitched.append(part)
    return "".join(stitched), codes


def render_plain(text: str) -> str:
    """Render the same inline grammar as `render_inline`, but to PLAIN TEXT: no tags, no
    escaping, delimiters removed. `` `x` `` -> `x`, `**x**` -> `x`.

    This exists because three destinations in this file's templates cannot hold a tag:
    `<meta name="description" content="…">`, `<img alt="…">` and `<title>`. All three used to
    take `esc(field)` straight, which shipped the markup LITERALLY — measured on the
    published site, entry 56's meta description opens ``content="`LF-080` builds…`` and nine
    image captions carry backticks or asterisks into their `alt`. That is what search
    results, link unfurls and screen readers read.

    **`LF-231` asked for `render_inline` here and that would have been wrong**, which is
    worth writing down because the two functions look interchangeable. An attribute value is
    not markup: `render_inline` would produce `<strong>` inside `content="…"`, `attr()` would
    then escape it, and the description would read `&lt;strong&gt;first&lt;/strong&gt;` —
    trading unrendered asterisks for visible tag names, which is worse. `<title>` is RCDATA
    and would show the tags for the same reason. The markup is *removed*, not rendered.

    Pairing follows `render_inline` exactly — same code-span extraction, same `_wrap_pairs`
    guard — so an unpaired delimiter stays literal in both. Anything else would mean two
    parsers to keep in step, which is the drift this whole file exists to avoid.
    """
    stitched, codes = _hide_code_spans(text)
    out = _wrap_pairs(stitched, "**", None)
    for i, code in enumerate(codes):
        out = out.replace(f"\x00{i}\x00", code, 1)
    return out


def render_block(block: dict[str, Any], image_prefix: str) -> str:
    t = block["type"]
    if t == "p":
        return f"<p>{render_inline(block['text'])}</p>"
    if t in ("h2", "h3"):
        return f"<{t}>{render_inline(block['text'])}</{t}>"
    if t == "quote":
        out = f"<blockquote>{render_inline(block['text'])}"
        if block.get("attribution"):
            # Prose, so it takes the inline renderer like every other prose field. An
            # attribution is nearly always a file, a function or a check name — the exact
            # shape that wants backticks — and it was the last field still bypassing
            # `render_inline`, which is how the table headers fixed in e5e3902 shipped
            # literal backticks to a published page.
            out += f"<cite>{render_inline(block['attribution'])}</cite>"
        out += "</blockquote>"
        return out
    if t == "list":
        items = "".join(f"<li>{render_inline(i)}</li>" for i in block["items"])
        return f"<ul>{items}</ul>"
    if t == "table":
        # Headers go through `render_inline` like every other prose field. They used not to,
        # and the result was the LF-173/LF-189 shape one more time: two headers in the
        # already-published `the-test-of-the-instrument-was-red` printed literal backticks
        # around `lane_coverage()`, and `audit_markup` could not see it because it only scans
        # for `**`. A header naming a function or a path is prose about code, so it needs the
        # same code spans the cells beneath it already get.
        headers = "".join(f"<th>{render_inline(h)}</th>" for h in block["headers"])
        rows = "".join(
            "<tr>" + "".join(f"<td>{render_inline(str(c))}</td>" for c in row) + "</tr>"
            for row in block["rows"]
        )
        return f'<div class="table-wrap"><table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table></div>'
    if t == "pre":
        # Verbatim tool output — an ASCII board preview, a grade table, a gate tally. It is
        # NOT prose and must never go through `render_inline`: a listing's backticks,
        # asterisks and angle brackets are content, so the text is escaped once and emitted
        # untouched. `<code>` inside `<pre>` is the semantic pairing for a code listing and
        # it also makes the listing invisible to `audit_markup`'s bold scan, which is
        # correct — a `**` in captured output is not unrendered markup.
        cap = block.get("caption", "")
        cap_html = f"<figcaption>{render_inline(cap)}</figcaption>" if cap else ""
        return (
            f'<figure class="listing"><div class="pre-wrap">'
            f'<pre><code>{esc(block["text"])}</code></pre></div>{cap_html}</figure>'
        )
    if t == "image":
        src = f"{image_prefix}{block['file']}"
        # The caption is rendered twice, to two different grammars, and that is the LF-211
        # fix: `<figcaption>` is element content and gets tags, `alt` is an attribute value
        # and gets plain text through `attr()`. Before this, `alt` took `esc(caption)` — so
        # one straight double quote ended the attribute and spilled the rest of the caption
        # into the tag, and any markup arrived verbatim for a screen reader to read out.
        cap = block.get("caption", "")
        return (
            f'<figure><img src="{attr(src)}" alt="{attr(render_plain(cap))}" loading="lazy">'
            f"<figcaption>{render_inline(cap)}</figcaption></figure>"
        )
    sys.exit(f"unknown body block type: {t!r}")


def render_chips(entry: dict[str, Any]) -> str:
    """Tags and decision numbers. These are IDENTIFIERS, not prose — an epic name or a
    decision number never carries markup and would be wrong to render if it did — so `esc()`
    in element content is the whole requirement. Same for a commit hash. Noted because the
    audit that produced the rest of this file's fixes had to say why these two stayed put."""
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
            f'<a class="entry-link" href="entries/{attr(e["id"])}.html">'
            f'<time datetime="{attr(e["date"])}">{esc(date_str)}</time>'
            f'<h2>{render_inline(e["title"])}</h2>'
            f"{render_chips(e)}"
            f'<p class="summary">{render_inline(e["summary"])}</p>'
            f"</a></li>"
        )
    body = f"""
<header class="masthead">
  <p class="kicker">{render_inline(site.get("kicker", "Latticefall"))}</p>
  <h1>{render_inline(site["title"])}</h1>
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
        title=esc(render_plain(site["title"])),
        description=attr(render_plain(site["subtitle"])),
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
    # The two `href`s here were interpolated raw — no escaping at all — and the link text
    # took `esc()` while the same title takes `render_inline()` in the header and on the
    # index card. Both are the LF-211 shape: an id is an attribute value and a title is prose.
    if prev_e:
        nav_parts.append(
            f'<a class="prev" href="{attr(prev_e["id"])}.html">&larr; {render_inline(prev_e["title"])}</a>')
    if next_e:
        nav_parts.append(
            f'<a class="next" href="{attr(next_e["id"])}.html">{render_inline(next_e["title"])} &rarr;</a>')

    superseded = ""
    if entry.get("superseded_by"):
        superseded = (
            f'<p class="superseded">Superseded by '
            f'<a href="{attr(entry["superseded_by"])}.html">a later entry</a>.</p>'
        )

    body = f"""
<nav class="entry-nav top">{" ".join(nav_parts[:1])}</nav>
<article>
  <header class="entry-header">
    <time datetime="{attr(entry["date"])}">{esc(date_str)}</time>
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
        title=esc(f'{render_plain(entry["title"])} — Latticefall build journal'),
        description=attr(render_plain(entry["summary"])),
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


RE_LONE_ASTERISK = re.compile(r"(?<![\w*/.\-])\*(?=\S)[^*\n]*\S\*(?![\w*/])")

LEGACY_LONE_ASTERISK: frozenset[str] = frozenset({
    "a-panel-that-cannot-lie",
    "four-anchors-four-different-levers",
    "gc-was-lying",
    "measuring-the-fifth-non-negotiable",
    "merging-closed-nothing",
    "no-instrument-panel-no-brake",
    "placement-rule-both-engines",
    "the-acceptance-criterion-was-not-a-selector",
    "the-blocker-was-already-fixed",
    "the-gate-was-exempt-from-its-own-rule",
    "the-guard-denied-its-own-advice",
    "the-instrument-measured-the-wrong-quantity",
    "the-net-the-fix-removed",
    "the-sound-kept-counting",
    "three-refusals-that-look-different",
})


def audit_markup(out_dir: Path) -> bool:
    """Assert no generated page carries markup that failed to render (LF-189).

    Staleness is not the only way a page can be wrong. Two failure shapes have reached the
    *published* site and neither made `check()` unhappy, because the committed HTML matched
    `chronicle.json` perfectly — it was the rendering that was wrong, identically, in both
    places:

      - **A literal `**` survives into the output.** `render_inline` splits on backticks
        first and pairs bold within each prose segment, so a bold span that CROSSES a code
        span (`**see `X` for why**`) never finds its partner and prints its asterisks.
        Hit live by the chronicler in two consecutive entries, and caught both times only
        because a human thought to diff the rendered HTML for stray asterisks — which is
        not a check anyone should have to remember.
      - **An unbalanced `<strong>`/`<code>`/`<em>`.** The LF-173 shape, where a code span
        was re-scanned and the tags came out crossed rather than nested.

    LF-189 proposed this as the cheap option (2) alongside a proper renderer fix (1).
    **Both were done**, in this order and deliberately: the audit was written first, run
    against the corpus, and found the defect live in two ALREADY-PUBLISHED entries
    (`measuring-the-fifth-non-negotiable`, `the-headline-did-not-survive`) that nobody had
    noticed in either. That is what justified fixing `render_inline` properly rather than
    asking authors to keep working around it — see its docstring.

    Its first run also caught its own false positive, which is worth keeping in mind before
    tightening it: `**` INSIDE a `<code>` element is legitimate content (an entry quoting
    `` `split("**")` ``), so code spans are stripped before the scan. A check that cries
    wolf on correct output is worse than no check.

    **Known remaining false positive:** a bare, un-backticked glob in prose (`sim/**`)
    trips the `**` scan. That is accepted rather than worked around — every path in this
    journal belongs in backticks by house style, so the check nudges toward the convention
    instead of fighting it, and the message says so. A red run here almost always means an
    entry needs a pair of backticks or a closing `**`, not that this file is broken.

    **A LONE asterisk pair is the third shape, and it is a ratchet rather than a plain
    assertion (LF-231).** Single-asterisk emphasis prints literally *by design* — that is
    LF-173's measured reversal, argued in `render_inline` — so `*like this*` in prose is
    always unrendered markup, and nothing checked for it: six spans got through one draft
    and were promoted to `**` by hand. Widening the scan is the fix, but running it over the
    corpus for the first time found **54 spans across 15 already-published entries**, and
    this journal is append-only: those entries record what was true on the day they were
    written and are not editable to make a check green.

    So the exemption below is exact in both directions. A lone asterisk in an entry NOT in
    `LEGACY_LONE_ASTERISK` fails, which is the check the ticket asked for; and an id in that
    set that no longer produces one also fails, as a stale exemption, so the list cannot
    quietly grow to cover new work. It can only ever shrink. The list is entry ids and not
    spans deliberately — an entry is immutable once published, so per-entry granularity
    cannot hide a later addition to an older page.

    The pattern is guarded the way `_wrap_pairs` is: an opening `*` must be preceded by
    something that is not a word character, `/`, `.`, `-` or another `*`, and must be
    followed by non-whitespace. That is what keeps `tools/*.py`, `scripts/test/*.gd` and
    `a * b` out of it — the same globs-in-prose ambiguity that made single-asterisk
    *rendering* unshippable is tractable for *detection*, because detection may be
    conservative and a renderer may not.
    """
    ok = True
    seen_ids: set[str] = set()
    for path in sorted(out_dir.rglob("*.html")):
        html_text = path.read_text(encoding="utf-8")
        rel = path.relative_to(out_dir)
        entry_id = path.stem if path.parent.name == "entries" else ""
        seen_ids.add(entry_id)

        # `**` INSIDE a <code> element is legitimate content, not unrendered markup — an
        # entry quoting `split("**")` is correct output. Only asterisks left in PROSE mean
        # a bold span failed to pair. Caught by this check's own first run, which flagged
        # `<code>split("**")</code>` in the very entry describing the LF-173 fix; a check
        # that cries wolf on correct content is worse than no check at all.
        prose = re.sub(r"<code>.*?</code>", "", html_text, flags=re.DOTALL)
        if "**" in prose:
            i = prose.index("**")
            print(f"unrendered bold in {rel}: literal '**' in prose — "
                  f"...{prose[max(0, i - 70):i + 70]}...")
            print("  either a bold span that never closed, or a bare glob in prose — "
                  "put paths and globs in backticks, which is house style anyway")
            ok = False

        lone = [m.group(0) for m in RE_LONE_ASTERISK.finditer(prose)]
        if lone and entry_id not in LEGACY_LONE_ASTERISK:
            print(f"unrendered emphasis in {rel}: {len(lone)} lone-asterisk span(s) — "
                  f"{', '.join(repr(s[:60]) for s in lone[:3])}")
            print("  single-asterisk emphasis prints literally by design (LF-173) — "
                  "use ** for emphasis, or backticks if it is a path or a glob")
            ok = False
        elif not lone and entry_id in LEGACY_LONE_ASTERISK:
            print(f"stale exemption in {rel}: {entry_id!r} is in LEGACY_LONE_ASTERISK but no "
                  "longer carries a lone-asterisk span — remove it from the set")
            ok = False

        for tag in ("strong", "code", "em"):
            opens, closes = html_text.count(f"<{tag}>"), html_text.count(f"</{tag}>")
            if opens != closes:
                print(f"unbalanced <{tag}> in {rel}: {opens} open, {closes} close")
                ok = False

        for m in re.finditer(r"<code>[^<]*<(strong|em)>[^<]*</code>", html_text):
            print(f"crossed tags in {rel}: {m.group(0)[:80]}")
            ok = False

    for gone in sorted(LEGACY_LONE_ASTERISK - seen_ids):
        print(f"stale exemption: {gone!r} is in LEGACY_LONE_ASTERISK but no entry renders it")
        ok = False
    return ok


def check(out_dir: Path) -> bool:
    """Regenerate into a scratch directory and diff every file against what is committed,
    then audit the generated markup itself (see `audit_markup` — staleness and correctness
    are different questions, and only the first one used to be asked)."""
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
        return audit_markup(tmp_dir) and ok


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
