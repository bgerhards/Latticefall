#!/usr/bin/env python3
"""Build the loop audition page.

Writes docs/latticefall-loops.html, which loads the real Ogg files from
assets/audio/ by relative path and loops them sample-accurately with Web Audio.

Why not embed the music: the 12 music loops are 29 MB. Why not use the 12 s wrap
clips: looping a clip made of [tail + head] introduces a *second*, artificial
join, so you would be judging an artifact. Playing the whole track with
AudioBufferSourceNode.loop = true reproduces exactly what the game does, and the
"skip to wrap" control drops the playhead 8 s before the seam so you can hear it
on demand instead of waiting out the track.

The short SFX loops are embedded directly — they are tiny and should work even
if the page is opened without a server.

    .venv/bin/python tools/audio/make_loop_page.py
    .venv/bin/python tools/audio/serve.py          # then open the URL
"""

from __future__ import annotations

import base64
import io
import json
import sys
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
AUDIO = ROOT / "assets" / "audio"
SFX = AUDIO / "sfx"
OUT = ROOT / "docs" / "latticefall-loops.html"

SFX_LOOPS = ["reactor_hum_loop", "anchor_ambient_loop", "beam_loop", "drone_hover_loop"]

ACT_OF = {"TTL": "Title", "A1": "Act I — Dead Air", "A2": "Act II — Salvage Rights",
          "A3": "Act III — The Hollow", "SYS": "System"}


def envelope(a: np.ndarray, buckets: int) -> list[float]:
    if len(a) < buckets:
        a = np.pad(a, (0, buckets - len(a)))
    peaks = np.array([c.max() for c in np.array_split(np.abs(a), buckets)])
    m = peaks.max()
    return [round(float(v / m), 3) for v in peaks] if m > 0 else [0.0] * buckets


def build() -> str:
    man = json.loads((AUDIO / "music_manifest.json").read_text())

    music = []
    for t in man["tracks"]:
        if not t["loop"]:
            continue
        p = AUDIO / t["file"]
        a, sr = sf.read(str(p), dtype="float32", always_2d=True)
        music.append({
            "id": t["id"],
            "act": ACT_OF[t["id"].split("-")[0]],
            "name": t["name"].replace("_", " ").title(),
            "src": "../assets/audio/" + t["file"],
            "dur": round(t["duration"], 2),
            "seam": t["seam_error"],
            "lufs": t["target_lufs"],
            "mb": round(p.stat().st_size / 1e6, 2),
            "env": envelope(a.mean(axis=1), 260),
        })

    sfx = []
    for name in SFX_LOOPS:
        p = SFX / f"{name}.wav"
        if not p.exists():
            continue
        with wave.open(str(p)) as w:
            n, sr = w.getnframes(), w.getframerate()
            raw = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768
        buf = io.BytesIO()
        sf.write(buf, raw, sr, format="OGG", subtype="VORBIS")
        sfx.append({
            "id": name,
            "name": name.replace("_loop", "").replace("_", " ").title(),
            "dur": round(n / sr, 3),
            "env": envelope(raw, 120),
            "uri": "data:audio/ogg;base64," + base64.b64encode(buf.getvalue()).decode(),
        })

    return TEMPLATE.replace("__MUSIC__", json.dumps(music)) \
                   .replace("__SFX__", json.dumps(sfx)) \
                   .replace("__NMUSIC__", str(len(music))) \
                   .replace("__NSFX__", str(len(sfx)))


TEMPLATE = r"""<meta charset="utf-8">
<title>LATTICEFALL — Loop Audition</title>
<style>
:root{--ground:#0E1417;--ground-2:#121B1F;--panel:#162126;--line:#24343A;--line-soft:#1C282D;
--bone:#DCE4E1;--muted:#7E9091;--verd:#5FA894;--verd-dim:#2E5A50;--amber:#E8A33D;--amber-dim:#6B4A18;--alert:#D2543F;
--shadow:0 1px 0 rgba(255,255,255,.03),0 18px 40px -24px rgba(0,0,0,.9);
--mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
--serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
--sans:system-ui,"Avenir Next","Segoe UI",Roboto,sans-serif;
--s1:.35rem;--s2:.7rem;--s3:1.1rem;--s4:1.8rem;--s5:2.8rem;--s6:4.4rem;--s7:7rem;--col:68ch}
@media (prefers-color-scheme:light){:root{--ground:#E3E8E5;--ground-2:#DADFDC;--panel:#F5F8F6;--line:#BAC6C2;
--line-soft:#CCD6D2;--bone:#111C1A;--muted:#5A6B69;--verd:#2A6E5D;--verd-dim:#A8C9BF;--amber:#9A6410;
--amber-dim:#E4CFA4;--alert:#A93520;--shadow:0 1px 0 rgba(255,255,255,.7),0 14px 30px -22px rgba(20,40,36,.5)}}
:root[data-theme=light]{--ground:#E3E8E5;--ground-2:#DADFDC;--panel:#F5F8F6;--line:#BAC6C2;--line-soft:#CCD6D2;
--bone:#111C1A;--muted:#5A6B69;--verd:#2A6E5D;--verd-dim:#A8C9BF;--amber:#9A6410;--amber-dim:#E4CFA4;--alert:#A93520}
:root[data-theme=dark]{--ground:#0E1417;--ground-2:#121B1F;--panel:#162126;--line:#24343A;--line-soft:#1C282D;
--bone:#DCE4E1;--muted:#7E9091;--verd:#5FA894;--verd-dim:#2E5A50;--amber:#E8A33D;--amber-dim:#6B4A18;--alert:#D2543F}
body{background:var(--ground);color:var(--bone);font-family:var(--sans);font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}
.doc{max-width:1080px;margin:0 auto;padding:var(--s5) var(--s3) var(--s7);display:flex;flex-direction:column;gap:var(--s5)}
h1,h2{text-wrap:balance;margin:0}p{margin:0}
code{font-family:var(--mono);font-size:.88em;color:var(--amber)}
:focus-visible{outline:2px solid var(--amber);outline-offset:3px}
.mast{display:grid;grid-template-columns:auto 1fr;gap:var(--s3) var(--s4);align-items:center;border-bottom:1px solid var(--line);padding-bottom:var(--s4)}
.emblem{width:70px;height:70px;display:block}
.wordmark{font-family:var(--mono);font-weight:700;font-size:clamp(1.5rem,4.6vw,2.5rem);letter-spacing:.12em;line-height:1}
.wordmark span{color:var(--verd)}
.mast-sub{font-family:var(--mono);font-size:.72rem;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-top:var(--s2)}
h2{font-family:var(--mono);font-size:1.1rem;letter-spacing:.1em;font-weight:600;text-transform:uppercase;color:var(--verd)}
.intro{max-width:var(--col);color:var(--muted);display:flex;flex-direction:column;gap:var(--s2)}
.intro b{color:var(--bone)}
.cmd{font-family:var(--mono);font-size:.82rem;background:var(--ground-2);border:1px solid var(--line);padding:var(--s2);overflow-x:auto;color:var(--bone)}
.cmd b{color:var(--verd)}
.banner{font-family:var(--mono);font-size:.75rem;letter-spacing:.06em;border:1px solid var(--alert);color:var(--alert);padding:var(--s2);display:none}
.banner.on{display:block}
.grp{font-family:var(--mono);font-size:.7rem;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:var(--s1);margin-top:var(--s3)}
.row{background:var(--panel);border:1px solid var(--line);padding:var(--s2) var(--s3);margin-bottom:2px;display:grid;grid-template-columns:auto 1fr auto;gap:var(--s3);align-items:center}
.row.playing{border-color:var(--verd)}
.btn{font-family:var(--mono);font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;background:var(--ground-2);color:var(--verd);border:1px solid var(--verd-dim);padding:.4rem .6rem;cursor:pointer;white-space:nowrap}
.btn:hover{background:var(--verd-dim);color:var(--bone)}
.btn.on{background:var(--amber);border-color:var(--amber);color:#0E1417}
.btn.wrap{color:var(--amber);border-color:var(--amber-dim)}
.btn.wrap:hover{background:var(--amber-dim);color:var(--bone)}
.btn:disabled{opacity:.35;cursor:not-allowed}
.mid{min-width:0;display:flex;flex-direction:column;gap:.2rem}
.nm{font-family:var(--mono);font-size:.85rem;letter-spacing:.05em}
.nm i{color:var(--muted);font-style:normal;font-size:.7rem;letter-spacing:.12em;margin-left:.5rem}
.meta{font-family:var(--mono);font-size:.64rem;letter-spacing:.1em;color:var(--muted);font-variant-numeric:tabular-nums}
.meta b{color:var(--verd);font-weight:600}
.viz{position:relative;height:38px;margin-top:.15rem}
.viz svg{width:100%;height:38px;display:block}
.play-head{position:absolute;top:0;bottom:0;width:1px;background:var(--amber);left:0;display:none}
.row.playing .play-head{display:block}
.seam-mark{position:absolute;top:0;bottom:0;right:0;width:2px;background:var(--alert);opacity:.75}
.ctl{display:flex;gap:var(--s1);flex-direction:column;align-items:stretch}
.count{font-family:var(--mono);font-size:.62rem;letter-spacing:.12em;color:var(--muted);text-align:center}
.count b{color:var(--amber)}
@media (max-width:700px){.row{grid-template-columns:1fr}.ctl{flex-direction:row}}
.foot{border-top:1px solid var(--line);padding-top:var(--s3);font-family:var(--mono);font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);display:flex;flex-wrap:wrap;gap:var(--s3);justify-content:space-between}
</style>
<div class="doc">
<header class="mast">
<svg class="emblem" viewBox="0 0 100 100" role="img" aria-label="Lattice anchor emblem">
<g fill="none" stroke="var(--verd)" stroke-width="1.5"><path d="M50 6 L88 28 L88 72 L50 94 L12 72 L12 28 Z" opacity=".45"/>
<circle cx="50" cy="50" r="26"/><circle cx="50" cy="50" r="17" opacity=".5"/></g>
<g fill="var(--amber)"><path d="M50 18 l4.5 7 h-9 Z"/><path d="M50 82 l4.5 -7 h-9 Z"/><path d="M18 50 l7 -4.5 v9 Z"/><path d="M82 50 l-7 -4.5 v9 Z"/></g>
<circle cx="50" cy="50" r="4.5" fill="var(--verd)"/></svg>
<div><h1 class="wordmark">LOOP <span>AUDITION</span></h1>
<div class="mast-sub">__NMUSIC__ music loops · __NSFX__ sfx loops · sample-accurate</div></div>
</header>

<section class="intro">
<h2>How to judge these</h2>
<p>Each track plays through <b>AudioBufferSourceNode</b> with looping on, which is sample-accurate — no gap inserted at the wrap, exactly what the game will do. Press <b>Skip to wrap</b> to drop the playhead 8 seconds before the seam instead of waiting out a four-minute track. The red marker on the waveform is the loop point.</p>
<p>If you can't tell where it wraps, the loop is good. That's the whole test — automated seam numbers don't describe loop quality here, because the crossfade joins samples that were already adjacent in the source.</p>
<div class="banner" id="banner"></div>
<div class="cmd">.venv/bin/python tools/audio/<b>serve.py</b>     # then open the printed URL</div>
<p>The music files are loaded from disk by relative path rather than embedded, because the twelve loops total 29 MB. The four SFX loops below are embedded and work regardless.</p>
</section>

<section><h2>Music</h2><div id="music"></div></section>
<section><h2>SFX loops</h2><div id="sfx"></div></section>

<footer class="foot"><span>Latticefall · loop audition</span><span>Web Audio · gapless</span><span>Judge by ear</span></footer>
</div>
<script>
var MUSIC = __MUSIC__, SFX = __SFX__;
(function () {
  var AC = window.AudioContext || window.webkitAudioContext;
  var ctx = null, cur = null, timer = null;
  var banner = document.getElementById('banner');

  function actx() { if (!ctx) ctx = new AC(); if (ctx.state === 'suspended') ctx.resume(); return ctx; }

  function waveSvg(env, buckets) {
    var w = 900, h = 38, mid = h / 2, step = w / env.length, d = '';
    for (var i = 0; i < env.length; i++) {
      var x = (i * step).toFixed(1), a = Math.max(env[i] * mid * 0.92, 0.4);
      d += 'M' + x + ' ' + (mid - a).toFixed(1) + 'L' + x + ' ' + (mid + a).toFixed(1);
    }
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">' +
           '<path d="' + d + '" stroke="var(--verd)" stroke-width="1.2" fill="none" opacity=".8"/></svg>';
  }

  function stop() {
    if (!cur) return;
    try { cur.src.stop(); } catch (e) {}
    cur.row.classList.remove('playing');
    cur.btn.classList.remove('on');
    cur.btn.textContent = 'Play';
    cur = null;
    if (timer) { clearInterval(timer); timer = null; }
  }

  // setInterval rather than requestAnimationFrame: rAF stops entirely in a
  // background tab, so the playhead and wrap counter would freeze while the
  // audio kept going. Timers throttle but do not stop.
  function tick() {
    if (!cur) return;
    var d = cur.buf.duration;
    var pos = ((actx().currentTime - cur.t0 + cur.offset) % d) / d;
    cur.head.style.left = (pos * 100) + '%';
    var n = Math.floor((actx().currentTime - cur.t0 + cur.offset) / d);
    if (n !== cur.laps) { cur.laps = n; cur.count.innerHTML = 'wraps <b>' + n + '</b>'; }
  }

  function start(item, offset) {
    stop();
    var c = actx();
    var src = c.createBufferSource();
    src.buffer = item.buf;
    src.loop = true;                       // sample-accurate; no gap inserted
    src.connect(c.destination);
    src.start(0, offset);
    cur = { src: src, buf: item.buf, t0: c.currentTime, offset: offset,
            row: item.row, btn: item.btn, head: item.head, count: item.count, laps: 0 };
    item.row.classList.add('playing');
    item.btn.classList.add('on');
    item.btn.textContent = 'Stop';
    item.count.innerHTML = 'wraps <b>0</b>';
    tick();
    timer = setInterval(tick, 60);
  }

  function makeRow(host, o, isMusic) {
    var row = document.createElement('div');
    row.className = 'row';
    var meta = isMusic
      ? o.dur.toFixed(1) + 's · ' + o.lufs + ' LUFS · seam ' + (o.seam != null ? o.seam.toFixed(5) : '—') + ' · ' + o.mb + ' MB'
      : o.dur.toFixed(3) + 's · seamless by construction';
    row.innerHTML =
      '<button class="btn" type="button">Play</button>' +
      '<div class="mid"><span class="nm">' + o.name + '<i>' + o.id + '</i></span>' +
      '<span class="meta">' + meta + '</span>' +
      '<div class="viz">' + waveSvg(o.env) + '<div class="play-head"></div><div class="seam-mark"></div></div></div>' +
      '<div class="ctl">' + (isMusic ? '<button class="btn wrap" type="button">Skip to wrap</button>' : '') +
      '<span class="count">wraps <b>0</b></span></div>';
    host.appendChild(row);

    var btn = row.querySelector('.btn');
    var wrapBtn = row.querySelector('.btn.wrap');
    var item = { row: row, btn: btn, head: row.querySelector('.play-head'),
                 count: row.querySelector('.count'), buf: null };

    function load() {
      if (item.buf) return Promise.resolve(item.buf);
      btn.disabled = true; btn.textContent = 'Loading';
      return fetch(o.src || o.uri)
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
        .then(function (ab) { return actx().decodeAudioData(ab); })
        .then(function (b) { item.buf = b; btn.disabled = false; btn.textContent = 'Play'; return b; })
        .catch(function (e) {
          btn.disabled = false; btn.textContent = 'Failed';
          banner.textContent = 'Could not load audio (' + e.message + '). ' +
            'Music is read from disk — open this page over the local server, not by double-clicking the file.';
          banner.classList.add('on');
          throw e;
        });
    }

    btn.addEventListener('click', function () {
      if (cur && cur.row === row) { stop(); return; }
      load().then(function () { start(item, 0); }).catch(function () {});
    });

    if (wrapBtn) wrapBtn.addEventListener('click', function () {
      load().then(function (b) { start(item, Math.max(0, b.duration - 8)); }).catch(function () {});
    });
  }

  var mhost = document.getElementById('music'), act = null;
  MUSIC.forEach(function (o) {
    if (o.act !== act) {
      act = o.act;
      var h = document.createElement('div'); h.className = 'grp'; h.textContent = act; mhost.appendChild(h);
    }
    makeRow(mhost, o, true);
  });
  SFX.forEach(function (o) { makeRow(document.getElementById('sfx'), o, false); });

  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') stop(); });
})();
</script>
"""


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    html = build()
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(ROOT)}  {len(html)/1024:.0f} KB")
    print("serve it:  .venv/bin/python tools/audio/serve.py")
