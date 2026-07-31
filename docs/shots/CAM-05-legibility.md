# CAM-05 — measured sprite legibility floor

**Status: the decision is settled.** Decision 056 picked the zoom-floor option (a `ZOOM_MIN`
sized to the sprite library, with `{{CAM-04}}`'s minimap doing the whole-board read) —
detail loss at zoom-out is accepted, not fought. This page is no longer three options
awaiting a choice; it is the measured floor of the *current* library, kept here so a future
art pass can aim at it without re-measuring.

Everything below was produced by `tools/legibility.py` from the already-committed atlas
under `assets/renders/` — no Blender render, no game code touched.

## 1. Contact sheet — `legibility.png`

```
.venv/bin/python tools/legibility.py --sizes 30,45,60,90 --out docs/shots/legibility.png
```

All 22 drawable ids (9 towers, 13 enemies), yaw 045, downsampled from the native 256px
albedo to 30/45/60/90px, laid on `(14, 20, 23)` — the real board tile colour, sampled
directly from a `tools/shot.py anchor-24` capture (an empty tile, away from the diagonal
path strip and any sprite), not `backdrop.gd`'s authored sky gradient.

Read it at 100%, not zoomed in by a viewer — that is the point. At 30px almost every id
reads as an ambiguous coloured blob; a faction or weapon read only starts to return around
60–90px, and even then it leans on colour and glow more than on outline.

## 2. Pairwise silhouette matrix — `legibility_matrix.txt` / `.json`

```
.venv/bin/python tools/legibility.py --matrix --sizes 30,45,60,90
```

Method: downsample each id's albedo to the target size, threshold alpha at ≥128/255,
Jaccard-distance every pair's binary mask (0 = identical silhouette, 1 = no overlap). This
measures **outline extent only** — it cannot see interior detail or colour, so it is a floor
on distinguishability, not the whole of it.

**Finding: no pair drops to or below a 0.10 shape-identity cutoff at any tested size
(30–90px).** Nothing collapses outright by pure silhouette extent in this range. The closest
pair at every single size is the same one:

| size | closest pair | distance |
|---|---|---|
| 30px | flak-array / pulse-turret | 0.1583 |
| 45px | flak-array / pulse-turret | 0.1607 |
| 60px | flak-array / pulse-turret | 0.1756 |
| 90px | flak-array / pulse-turret | 0.1872 |

Runners-up cluster the same way at every size: `warden-heavy`/`anchor-damper` (~0.24–0.27),
`anchor-damper`/`flak-array` (~0.25–0.28), `ion-lance`/`pulse-turret` (~0.25–0.28),
`arc-node`/`pulse-turret` (~0.27–0.31). **Towers are the least shape-distinct group in the
library** — they share a squat cylindrical base — but even the closest tower pair stays
well clear of collapsing.

The practical legibility loss the contact sheet shows at 30–45px is therefore mostly
**interior colour and detail**, not gross outline — this metric says the outline itself is
holding up better than the eye-test suggests. Useful for a future art pass: spending effort
on tower silhouettes specifically (the flak-array/pulse-turret pair, then the
anchor-damper cluster) buys more distinguishability per stroke than reworking enemies, whose
silhouettes are already comparatively spread out.

Full numeric matrix for every pair, every size: `legibility_matrix.json`.

## 3. Zoom ladder (downsample proxy) — `CAM-05-zoom-ladder.png`

```
.venv/bin/python tools/legibility.py --zoom-ladder <100%-capture.png> <200%-capture.png> \
    --out docs/shots/CAM-05-zoom-ladder.png
```

One real `tools/shot.py anchor-24` capture per interface scale (100% and 200%), frame 1800,
autoplay on — wave 1/10 in progress, 6 Hollow-faction units and 12 emplacements on board —
each resized to 1.0 / 0.5 / 0.35 / 0.234 / 0.117. The two extremes are LF-104's measured
zoom levels for fitting a 64² board into the strip between the instrument panels: 0.234× at
100% interface scale, 0.117× at 200%.

**Honestly labelled as a proxy, not the real thing**, because CAM-01 (the board camera) is
unbuilt and there is no `--camera` hook to zoom only the board layer. This downsamples the
*whole* captured frame — HUD and instrument panels included — so it is strictly more
pessimistic about HUD legibility than the real camera will be (the real one leaves the HUD
at native resolution per `LF-052`'s scoping; only the board layer zooms). For the board
sprites themselves, though, it is a fair proxy: at both 0.234× and 0.117× the sprites are
unreadable dots in this capture, consistent with the contact sheet's 30px row.

## What this means going forward

Decision 056 already accepted that a zoomed-out board loses detail — that is what makes a
zoom floor tenable at all. The one thing this page adds beyond "yes, detail is lost" is a
durable, reproducible pointer at *where*: the tower family's silhouettes are the closest
together in the library, `flak-array`/`pulse-turret` most of all, though none has actually
collapsed by outline alone in the sizes tested. Re-run `tools/legibility.py` after any
sprite change to see whether that has moved.

`ZOOM_MIN` itself is set in `{{CAM-01}}`, citing decision 056 — not this file.
