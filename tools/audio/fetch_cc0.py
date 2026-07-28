#!/usr/bin/env python3
"""Find and stage CC0 sound effects for the twelve cues synthesis cannot fake.

Why this file exists
--------------------
`synth_sfx.py` generates 35 cues as pure functions of their names, which is why the bank
rebuilds byte-identical anywhere (decision 009). It cannot generate the physical ones:
wind over a structure, grit under a boot, debris settling, metal under stress. Those have
to be recordings, and decision 038 says recordings enter this repo **CC0 only**, filtered
on machine-readable licence metadata rather than on a page that says "free".

Sources, and why in this order
------------------------------
**OpenGameArt** is primary. Its advanced search filters by licence (CC0 is term id 4) and
art type (sound effect is 13), every item page states its licences explicitly, and files
download without credentials.

**Wikimedia Commons** is secondary and off by default. Its API returns a clean per-file
licence, and the CC0 filter here works perfectly — but its *corpus* is wrong. A search for
wind returns a 55 MB interview recording; a search for electric arc returns Dutch
dictionary pronunciations of the word "zap". Correct licences, useless sounds. It is kept
for the rare case where a real field recording is wanted, behind `--commons`.

Both are verified the same way: an item is rejected unless CC0 appears in the licences it
actually declares. Where an item offers CC0 alongside another licence, CC0 is the one
relied on and the alternatives are recorded so the choice is auditable.

What it deliberately does not do
--------------------------------
It does not put anything in `assets/`. Nothing here has been *heard* — a licence check says
a file is safe to ship, not that it is the right sound. Candidates stage next to the music
masters, outside git (decision 012), until a human auditions them. This project already
knows better than to trust an audio metric over an ear: decision 011 records a loop scorer
that was tuned against a number and made the loops measurably worse.

    .venv/bin/python tools/audio/fetch_cc0.py --list
    .venv/bin/python tools/audio/fetch_cc0.py --dry-run       # search, download nothing
    .venv/bin/python tools/audio/fetch_cc0.py                 # search + stage all cues
    .venv/bin/python tools/audio/fetch_cc0.py amb_wind_loop   # one cue
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGE = Path.home() / "Latticefall-masters" / "cc0-candidates"
MANIFEST = STAGE / "candidates.json"

OGA = "https://opengameart.org"
OGA_SEARCH = OGA + "/art-search-advanced"
OGA_CC0_TID = 4            # licence: CC0
OGA_SFX_TID = 13           # art type: sound effect
COMMONS = "https://commons.wikimedia.org/w/api.php"

UA = "Mozilla/5.0 (compatible; Latticefall-asset-sourcing/1.0)"
AUDIO_EXT = (".ogg", ".wav", ".mp3", ".flac", ".opus", ".aiff", ".aif")

## The only accepted licence. "Public domain" is deliberately absent: it covers term-expiry
## and government-work cases whose status varies by jurisdiction, where CC0 is an explicit
## worldwide waiver by the author. Decision 038.
CC0_PATTERN = re.compile(r"\bCC0\b", re.I)

CANDIDATES_PER_CUE = 5

## A one-shot has no business being 42 MB. The first run staged two 42 MB static
## recordings as candidates for a radio squelch, which is a file that would have to be
## cut down to a tenth of a second to be usable.
MAX_BYTES = {True: 25_000_000, False: 6_000_000}     # keyed by `loop`

## Words that say nothing about what a file sounds like, so they must not be what makes a
## file look relevant.
STOPWORDS = {"sound", "sounds", "effect", "effects", "sfx", "loop", "ambience", "ambient",
             "pack", "free", "the", "and", "a", "of"}


def tokens_for(cue: str, spec: dict) -> set[str]:
    """The words a candidate's *filename* has to contain to be considered for this cue.

    The first run scored relevance on the item title alone and then took every file the
    item contained, so one pack called "bangs and beeps" answered both `rubble_impact` and
    `footstep_grit` with the same five files, `beep1.wav` among them. A pack matching a
    query says the pack is worth opening; it does not say every file inside it is the
    sound being looked for.
    """
    words = set()
    for query in spec["queries"]:
        words.update(w for w in re.split(r"[^a-z]+", query.lower()) if w)
    words.update(w for w in cue.split("_") if w)
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def relevance(url: str, item: dict, want: set[str]) -> int:
    """How many wanted words appear in this file's own name. Item title counts for half a
    point, expressed as a tie-break rather than a substitute."""
    stem = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).stem.lower()
    stem_words = set(w for w in re.split(r"[^a-z]+", stem) if w)
    hits = len(stem_words & want)
    title_words = set(w for w in re.split(r"[^a-z]+", item["title"].lower()) if w)
    return hits * 2 + (1 if title_words & want else 0)

## The twelve cues, grounded in what each act actually is (docs/STORY.md): Act I a shuttered
## Ordinal facility with no weather, Act II half-stripped anchors with scaffolding and
## contractor lighting, Act III anchors the Lattice is metabolizing.
##
## `note` is what the cue is *for*. It is carried into the audition manifest, because
## "wind" is not a brief and the person judging these will not be the one who wrote them.
WANTED: dict[str, dict] = {
    "amb_facility_loop": {
        "note": "Act I bed. Dead air in a shuttered facility — room tone, not weather. "
                "A building that still has power and nobody in it.",
        "queries": ["ambience loop", "industrial ambience", "machine room"],
        "loop": True,
    },
    "amb_wind_loop": {
        "note": "Act II bed. Wind over exposed structure and scaffolding. Exterior, cold, "
                "no vegetation — this world has no sky worth looking at.",
        "queries": ["wind ambience", "wind loop", "space wind"],
        "loop": True,
    },
    "amb_hollow_loop": {
        "note": "Act III bed. Vast wrong interior. Cave or cathedral tone — the anchor has "
                "become something with a throat.",
        "queries": ["cave ambience", "dungeon ambience", "dark ambience loop"],
        "loop": True,
    },
    "comms_squelch": {
        "note": "Radio opens before a dialog line. Every line is currently announced by a "
                "quiet UI tick, which is a menu sound doing a soldier's job.",
        "queries": ["radio static", "radio noise", "interference"],
        "loop": False,
    },
    "comms_close": {
        "note": "Radio closes after a dialog line. Shorter and drier than the open.",
        "queries": ["radio", "beep", "static"],
        "loop": False,
    },
    "debris_settle": {
        "note": "Small rubble and dust falling after a construct dies. The tail on a kill, "
                "layered under the synthesized warden_death.",
        "queries": ["debris", "rubble", "rocks falling"],
        "loop": False,
    },
    "rubble_impact": {
        "note": "Mortar landing on terrain rather than on armour. Earth and stone — "
                "impact_metal already covers the metal case.",
        "queries": ["rock impact", "stone hit", "crash rubble"],
        "loop": False,
    },
    "metal_stress": {
        "note": "Act II scaffolding under load. An occasional creak that says the structure "
                "is holding, but only just.",
        "queries": ["metal creak", "creaking", "metal groan"],
        "loop": False,
    },
    "footstep_grit": {
        "note": "Boots on grit. Contractor presence in Act II, used sparsely as ambience "
                "rather than as a per-unit sound.",
        "queries": ["footsteps gravel", "footsteps concrete", "footstep"],
        "loop": False,
    },
    "heavy_footfall": {
        "note": "Organic layer under the synthesized heavy_stomp, so the big Ordinal and "
                "Hollow units land with weight instead of a filtered thud.",
        "queries": ["heavy thud", "stomp", "impact deep"],
        "loop": False,
    },
    "distant_collapse": {
        "note": "Far-off structural failure. Punctuation for a wave cleared or an anchor "
                "lost — something large gave way elsewhere on the ring.",
        "queries": ["collapse", "distant explosion", "rumble"],
        "loop": False,
    },
    "electric_arc": {
        "note": "Real high-voltage arcing to layer under arc_node_fire. Synthesis makes arcs "
                "sound like white noise through a gate; the real thing is erratic.",
        "queries": ["electric arc", "electricity", "spark"],
        "loop": False,
    },
}


def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_text(url: str) -> str:
    try:
        return fetch(url).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — one dead page must not abandon eleven cues
        print(f"      ! {url}: {exc}", file=sys.stderr)
        return ""


def strip_tags(raw: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


# ───────────────────────────────────────────────────────── opengameart ──

def oga_search(query: str, pages: int = 1) -> list[str]:
    """Item paths matching a query, already filtered to CC0 sound effects by the site."""
    found: list[str] = []
    for page in range(pages):
        params = [
            ("keys", query),
            ("field_art_licenses_tid[]", str(OGA_CC0_TID)),
            ("field_art_type_tid[]", str(OGA_SFX_TID)),
            ("sort_by", "count"),
            ("page", str(page)),
        ]
        body = fetch_text(OGA_SEARCH + "?" + urllib.parse.urlencode(params))
        for path in re.findall(r'href="(/content/[a-z0-9][a-z0-9-]*)"', body):
            if path != "/content/faq" and path not in found:
                found.append(path)
    return found


def oga_item(path: str) -> dict | None:
    """Parse an item page. Returns None unless the page itself declares CC0 — the search
    filter is the site's claim, and this is the check that the item agrees with it."""
    body = fetch_text(OGA + path)
    if not body:
        return None

    licences = [strip_tags(m) for m in re.findall(
        r'<span[^>]*class="license-name"[^>]*>(.*?)</span>', body, re.S)]
    if not licences:                       # fall back to the licence block as a whole
        block = re.search(r'License\(s\)(.{0,1200}?)</div>\s*</div>', body, re.S)
        if block:
            licences = [strip_tags(block.group(1))]
    if not any(CC0_PATTERN.search(lic) for lic in licences):
        return None

    author = "unknown"
    m = re.search(r"<span class='username'><a[^>]*>([^<]+)</a>", body)
    if m:
        author = html.unescape(m.group(1)).strip()

    files: list[str] = []
    for url in re.findall(r'href="(https://opengameart\.org/sites/default/files/[^"]+)"', body):
        clean = html.unescape(url)
        if clean.lower().endswith(AUDIO_EXT) and clean not in files:
            files.append(clean)

    title = strip_tags(re.search(r"<title>(.*?)</title>", body, re.S).group(1)) \
        if re.search(r"<title>(.*?)</title>", body, re.S) else path

    return {
        "source": "opengameart",
        "page": OGA + path,
        "title": title.replace(" | OpenGameArt.org", "").strip(),
        "author": author,
        "licences": licences,
        "licence": "CC0",
        "files": files,
    }


# ──────────────────────────────────────────────────────────── commons ──

def commons_search(query: str, limit: int = 20) -> list[dict]:
    url = COMMONS + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:audio {query}", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size",
    })
    try:
        data = json.loads(fetch(url).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"      ! commons: {exc}", file=sys.stderr)
        return []
    rows = []
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        lic = str(meta.get("LicenseShortName", {}).get("value", "")).strip()
        if not CC0_PATTERN.match(lic):
            continue
        rows.append({
            "source": "commons",
            "page": info.get("descriptionurl", ""),
            "title": page.get("title", "").removeprefix("File:"),
            "author": strip_tags(str(meta.get("Artist", {}).get("value", ""))) or "unknown",
            "licences": [lic],
            "licence": lic,
            "files": [info.get("url", "")],
        })
    return rows


# ────────────────────────────────────────────────────────────── stage ──

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def safe_name(cue: str, url: str) -> str:
    stem = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    suffix = Path(stem).suffix or ".ogg"
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(stem).stem)[:56]
    return f"{cue}__{slug}{suffix}"


def remote_size(url: str) -> int:
    """Content-Length without pulling the body. The size gate used to download first and
    delete after, which threw away about 400 MB of transfer per run on a handful of 42 MB
    static recordings — and did it again on every re-run, because deleting the file is
    exactly what makes the next run fetch it afresh."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:  # noqa: BLE001 — an unknown size is not a reason to skip the file
        return 0


def download(url: str, dest: Path) -> str:
    """Fetch to `dest` and return its SHA-256. Skips the transfer when already staged, so
    the script is re-runnable without re-pulling everything."""
    if not dest.exists():
        data = fetch(url, timeout=120)
        dest.write_bytes(data)
    return sha256_of(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cues", nargs="*", help="cues to source (default: all)")
    ap.add_argument("--list", action="store_true", help="list the wanted cues and exit")
    ap.add_argument("--per-cue", type=int, default=CANDIDATES_PER_CUE,
                    help="how many files to keep per cue")
    ap.add_argument("--dry-run", action="store_true", help="search and report, download nothing")
    ap.add_argument("--commons", action="store_true",
                    help="also search Wikimedia Commons (clean licences, wrong corpus)")
    args = ap.parse_args()

    if args.list:
        for cue, spec in WANTED.items():
            print(f"{cue:22s} {'loop' if spec['loop'] else 'shot'}  {spec['note']}")
        return 0

    cues = args.cues or list(WANTED)
    if unknown := [c for c in cues if c not in WANTED]:
        print(f"unknown cue(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    if not args.dry_run:
        STAGE.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}

    staged = rejected = 0
    taken: set[str] = set()      # one file answers one cue, across the whole run
    for cue in cues:
        spec = WANTED[cue]
        print(f"\n{cue}  ·  {spec['note'].split('.')[0]}")

        items: list[dict] = []
        seen_pages: set[str] = set()
        for query in spec["queries"]:
            for path in oga_search(query):
                if OGA + path in seen_pages:
                    continue
                seen_pages.add(OGA + path)
                item = oga_item(path)
                if item is None:
                    rejected += 1
                elif item["files"]:
                    items.append(item)
                time.sleep(0.3)
            if args.commons:
                items.extend(commons_search(query, 10))
            time.sleep(0.3)

        want = tokens_for(cue, spec)
        ranked: list[tuple[int, str, dict]] = []
        for item in items:
            for url in item["files"]:
                if url in taken:
                    continue                 # already answering another cue
                score = relevance(url, item, want)
                if score < 2:
                    continue                 # the filename itself must be on topic
                ranked.append((score, url, item))
        ranked.sort(key=lambda r: -r[0])

        entries: list[dict] = []
        for score, url, item in ranked:
            if len(entries) >= args.per_cue:
                break
            name = safe_name(cue, url)
            size = remote_size(url)
            if size > MAX_BYTES[spec["loop"]]:
                print(f"   - {name}: {size/1e6:.0f} MB, too long for "
                      f"{'a bed' if spec['loop'] else 'a one-shot'}")
                continue
            if args.dry_run:
                print(f"   · {item['licence']:6s} r{score} "
                      f"{item['author'][:16]:16s} {name}")
                entries.append({"file": name})
                taken.add(url)
                continue
            dest = STAGE / name
            try:
                digest = download(url, dest)
            except Exception as exc:  # noqa: BLE001
                print(f"   ! {name}: {exc}", file=sys.stderr)
                continue
            if dest.stat().st_size > MAX_BYTES[spec["loop"]]:
                # Only reachable when the server declined to give a Content-Length.
                print(f"   - {name}: {dest.stat().st_size/1e6:.0f} MB, over the limit")
                dest.unlink()
                continue
            taken.add(url)
            entries.append({
                "file": name, "url": url, "page": item["page"], "title": item["title"],
                "author": item["author"], "licence": item["licence"],
                "licences_offered": item["licences"], "source": item["source"],
                "sha256": digest, "bytes": dest.stat().st_size,
                "relevance": score, "verdict": "unheard",
            })
            print(f"   · {item['licence']:6s} {dest.stat().st_size/1e6:5.2f} MB  "
                  f"{item['author'][:16]:16s} {name}")
        print(f"   {len(entries)} staged from {len(items)} CC0 items")
        if not args.dry_run:
            manifest[cue] = {"note": spec["note"], "loop": spec["loop"], "candidates": entries}
            staged += len(entries)

    if not args.dry_run:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"\nstaged {staged} files -> {STAGE}")
        print(f"rejected {rejected} items that did not declare CC0 on their own page")
        print("manifest:", MANIFEST)
        print("\nNothing here has been heard. Audition before anything reaches assets/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
