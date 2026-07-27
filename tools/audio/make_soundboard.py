#!/usr/bin/env python3
"""Build the playable SFX soundboard page from the generated bank.

Reads assets/audio/sfx/*.wav, embeds each as a data URI, draws a real peak
envelope per sound, and writes docs/latticefall-sfx.html. Regenerate any time
the bank changes:

    .venv/bin/python tools/audio/synth_sfx.py
    .venv/bin/python tools/audio/make_soundboard.py
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
SFX = ROOT / "assets" / "audio" / "sfx"
OUT = ROOT / "docs" / "latticefall-sfx.html"

# Groups are derived from the bank so this page can never drift out of sync
# with tools/synth_sfx.py. Add a sound there and it appears here.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from synth_sfx import BANK  # noqa: E402

GROUP_OF = {
    "ui_": "Interface", "place_": "Interface",
    "power_": "Power", "brownout": "Power", "reactor": "Power",
    "capacity": "Power", "breaker": "Power", "overload": "Power",
    "turret_": "Weapons", "arc_": "Weapons", "lance_": "Weapons",
    "mortar_": "Weapons", "flak_": "Weapons", "beam_": "Weapons", "pulse_": "Weapons",
    "impact_": "Impacts", "shield_": "Impacts", "ricochet": "Impacts", "crit_": "Impacts",
    "warden_": "Enemies", "drone_": "Enemies", "heavy_": "Enemies",
    "anchor_": "World",
}
ORDER = ["Interface", "Power", "Weapons", "Impacts", "Enemies", "World"]


def group_of(name: str) -> str:
    for prefix, grp in GROUP_OF.items():
        if name.startswith(prefix) or prefix in name:
            return grp
    return "World"


CATALOG = [
    (name, group_of(name), name.replace("_", " ").replace(" loop", "").strip().title(), desc)
    for name, (_fn, desc) in BANK.items()
]
CATALOG.sort(key=lambda r: (ORDER.index(r[1]), r[0]))

# The full 60. S = synthesized here, C = CC0-sourced then layered with synth.
PLAN = [
    ("Interface", 12, "S", "click, hover, confirm, deny, panel open/close, tab, purchase, upgrade, sell, pause, resume"),
    ("Power",     10, "S", "online, offline, brownout alarm, recovery, capacity increase, overload warning, reactor bed, bus tick, breaker trip, low-power warning"),
    ("Weapons",   12, "S", "pulse fire/charge, arc fire/chain, lance fire/charge, mortar fire/travel, flak burst, beam start/loop/stop"),
    ("Impacts",    8, "S", "armour, shield, stone, flesh, ricochet, shield break, armour crack, critical"),
    ("Enemies",   10, "C", "warden step loop, warden spawn/death, drone hover/death, heavy stomp, hollow whisper, hollow death, boss vocal, swarm bed"),
    ("World",      8, "C", "anchor bed, gate open/close, three act ambiences, rain, debris settle"),
]


def envelope(a: np.ndarray, buckets: int = 150) -> list[float]:
    if len(a) < buckets:
        a = np.pad(a, (0, buckets - len(a)))
    chunks = np.array_split(np.abs(a), buckets)
    peaks = np.array([c.max() for c in chunks])
    m = peaks.max()
    return [round(float(v / m), 4) for v in peaks] if m > 0 else [0.0] * buckets


def to_ogg(p: Path) -> bytes:
    """Vorbis for the page only. The repo keeps lossless WAV masters."""
    a, sr = sf.read(str(p), dtype="float32", always_2d=True)
    buf = io.BytesIO()
    sf.write(buf, a, sr, format="OGG", subtype="VORBIS")
    return buf.getvalue()


def read_wav(p: Path):
    with wave.open(str(p)) as w:
        n, sr = w.getnframes(), w.getframerate()
        a = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float64) / 32768
    return a, sr


def build() -> str:
    sounds = []
    for name, group, label, note in CATALOG:
        p = SFX / f"{name}.wav"
        if not p.exists():
            print(f"  ! missing {p.name} — run synth_sfx.py first")
            continue
        a, sr = read_wav(p)
        rms = float(np.sqrt(np.mean(a ** 2)))
        peak = float(np.max(np.abs(a)))
        sounds.append({
            "id": name, "group": group, "label": label, "note": note,
            "dur": round(len(a) / sr, 3),
            "kb": round(p.stat().st_size / 1024, 1),
            "rms": round(rms, 4),
            "crest": round(peak / rms, 2) if rms else 0,
            "loop": "loop" in name,
            "env": envelope(a),
            "uri": "data:audio/ogg;base64," + base64.b64encode(to_ogg(p)).decode(),
        })
    total_synth = sum(n for _, n, k, _ in PLAN if k == "S")
    total_src = sum(n for _, n, k, _ in PLAN if k == "C")
    return TEMPLATE.replace("__SOUNDS__", json.dumps(sounds)) \
                   .replace("__PLAN__", json.dumps(PLAN)) \
                   .replace("__NSYNTH__", str(total_synth)) \
                   .replace("__NSRC__", str(total_src)) \
                   .replace("__NTOTAL__", str(total_synth + total_src)) \
                   .replace("__NBANK__", str(len(sounds)))


TEMPLATE = r"""<title>LATTICEFALL — SFX Bank</title>
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
--bone:#111C1A;--muted:#5A6B69;--verd:#2A6E5D;--verd-dim:#A8C9BF;--amber:#9A6410;--amber-dim:#E4CFA4;--alert:#A93520;
--shadow:0 1px 0 rgba(255,255,255,.7),0 14px 30px -22px rgba(20,40,36,.5)}
:root[data-theme=dark]{--ground:#0E1417;--ground-2:#121B1F;--panel:#162126;--line:#24343A;--line-soft:#1C282D;
--bone:#DCE4E1;--muted:#7E9091;--verd:#5FA894;--verd-dim:#2E5A50;--amber:#E8A33D;--amber-dim:#6B4A18;--alert:#D2543F;
--shadow:0 1px 0 rgba(255,255,255,.03),0 18px 40px -24px rgba(0,0,0,.9)}
body{background:var(--ground);color:var(--bone);font-family:var(--sans);font-size:16px;line-height:1.65;-webkit-font-smoothing:antialiased}
.doc{max-width:1080px;margin:0 auto;padding:var(--s5) var(--s3) var(--s7);display:flex;flex-direction:column;gap:var(--s6)}
h1,h2,h3{text-wrap:balance;margin:0}p{margin:0}
code{font-family:var(--mono);font-size:.88em;color:var(--amber)}
:focus-visible{outline:2px solid var(--amber);outline-offset:3px}
.tag{font-family:var(--mono);font-size:.69rem;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.mast{display:grid;grid-template-columns:auto 1fr;gap:var(--s3) var(--s4);align-items:center;border-bottom:1px solid var(--line);padding-bottom:var(--s4)}
.emblem{width:76px;height:76px;display:block}
.wordmark{font-family:var(--mono);font-weight:700;font-size:clamp(1.6rem,5vw,2.8rem);letter-spacing:.12em;line-height:1}
.wordmark span{color:var(--verd)}
.mast-sub{font-family:var(--mono);font-size:.72rem;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-top:var(--s2)}
.sec{display:grid;grid-template-columns:8.5rem 1fr;gap:var(--s4);align-items:start}
.sec>.rail{position:sticky;top:var(--s3);font-family:var(--mono);font-size:.68rem;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);border-top:2px solid var(--verd);padding-top:var(--s2);display:flex;flex-direction:column;gap:var(--s1)}
.rail b{color:var(--verd);font-weight:600}
.body-col{display:flex;flex-direction:column;gap:var(--s3);min-width:0}
.sec h2{font-family:var(--mono);font-size:1.42rem;letter-spacing:.06em;font-weight:600;text-transform:uppercase}
.lede{font-size:1.1rem;max-width:var(--col)}
.body-col>p{max-width:var(--col);color:var(--muted)}
.body-col>p strong{color:var(--bone);font-weight:600}
@media (max-width:720px){.sec{grid-template-columns:1fr;gap:var(--s3)}.sec>.rail{position:static;flex-direction:row;gap:var(--s3)}}
.grp{font-family:var(--mono);font-size:.72rem;letter-spacing:.18em;text-transform:uppercase;color:var(--verd);border-bottom:1px solid var(--line);padding-bottom:var(--s1);margin-top:var(--s2)}
.snd{display:grid;grid-template-columns:auto 1fr auto;gap:var(--s3);align-items:center;background:var(--panel);border:1px solid var(--line);padding:var(--s2) var(--s3);margin-bottom:2px}
.snd:hover{border-color:var(--verd-dim)}
.play{width:42px;height:42px;flex:none;border:1px solid var(--verd-dim);background:var(--ground-2);color:var(--verd);cursor:pointer;font-family:var(--mono);font-size:.9rem;display:grid;place-items:center}
.play:hover{background:var(--verd-dim);color:var(--bone)}
.play.on{background:var(--amber);border-color:var(--amber);color:#0E1417}
.snd-mid{min-width:0;display:flex;flex-direction:column;gap:.15rem}
.snd-name{font-family:var(--mono);font-size:.86rem;letter-spacing:.06em;color:var(--bone)}
.snd-id{font-family:var(--mono);font-size:.63rem;letter-spacing:.12em;color:var(--muted)}
.snd-note{font-size:.84rem;color:var(--muted)}
.wave{width:100%;height:34px;display:block;margin-top:.2rem}
.snd-meta{font-family:var(--mono);font-size:.65rem;letter-spacing:.1em;color:var(--muted);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;display:flex;flex-direction:column;gap:.1rem}
.snd-meta b{color:var(--verd);font-weight:600}
.lp{color:var(--amber);border:1px solid var(--amber-dim);padding:0 .3rem;font-size:.58rem}
@media (max-width:640px){.snd{grid-template-columns:auto 1fr}.snd-meta{grid-column:2;text-align:left;flex-direction:row;gap:var(--s2)}}
.tw{overflow-x:auto;border:1px solid var(--line)}
table{border-collapse:collapse;width:100%;font-size:.86rem;min-width:560px}
th,td{text-align:left;padding:.6rem .8rem;border-bottom:1px solid var(--line-soft);vertical-align:top}
th{font-family:var(--mono);font-size:.66rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);background:var(--ground-2);font-weight:600}
td{color:var(--muted)}td:first-child{font-family:var(--mono);color:var(--bone);font-size:.8rem;white-space:nowrap}
tr:last-child td{border-bottom:0}
.pill{font-family:var(--mono);font-size:.63rem;letter-spacing:.12em;padding:.12rem .45rem;border:1px solid currentColor;white-space:nowrap}
.pill.s{color:var(--verd)}.pill.c{color:var(--amber)}
.note{font-family:var(--mono);font-size:.73rem;line-height:1.6;color:var(--muted);border-left:2px solid var(--amber);padding:var(--s1) 0 var(--s1) var(--s2);max-width:var(--col)}
.note b{color:var(--amber);letter-spacing:.1em}
.note.bad{border-left-color:var(--alert)}.note.bad b{color:var(--alert)}
.cmd{font-family:var(--mono);font-size:.8rem;background:var(--ground-2);border:1px solid var(--line);padding:var(--s2);overflow-x:auto;color:var(--bone)}
.cmd b{color:var(--verd)}
.foot{border-top:1px solid var(--line);padding-top:var(--s3);display:flex;flex-wrap:wrap;justify-content:space-between;gap:var(--s2);font-family:var(--mono);font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
</style>
<div class="doc">
<header class="mast">
<svg class="emblem" viewBox="0 0 100 100" role="img" aria-label="Lattice anchor emblem">
<g fill="none" stroke="var(--verd)" stroke-width="1.5"><path d="M50 6 L88 28 L88 72 L50 94 L12 72 L12 28 Z" opacity=".45"/>
<circle cx="50" cy="50" r="26"/><circle cx="50" cy="50" r="17" opacity=".5"/></g>
<g fill="var(--amber)"><path d="M50 18 l4.5 7 h-9 Z"/><path d="M50 82 l4.5 -7 h-9 Z"/><path d="M18 50 l7 -4.5 v9 Z"/><path d="M82 50 l-7 -4.5 v9 Z"/></g>
<circle cx="50" cy="50" r="4.5" fill="var(--verd)"/></svg>
<div><h1 class="wordmark">SFX <span>BANK</span></h1>
<div class="mast-sub">Latticefall · __NBANK__ sounds playable · generated from code</div></div>
</header>

<section class="sec"><div class="rail"><b>00</b><span>Answer</span></div><div class="body-col">
<h2>Where combat effects come from</h2>
<p class="lede">We synthesize __NSYNTH__ of the __NTOTAL__ from code, and source the remaining __NSRC__ as CC0 recordings that get layered with synthesis.</p>
<p>Everything on this page was generated by <code>tools/audio/synth_sfx.py</code> in 1.7 seconds. No downloads, no sample library, no licence to track. Each sound is a pure function of its name, so the bank rebuilds byte-identical anywhere, and <strong>a sound is a diff</strong> — if the pulse turret is too bright, that's a number in a file, not a re-download and a re-edit.</p>
<p>Press play. This is the real output, not a mockup.</p>
<div class="note"><b>Why not Suno →</b> Suno writes music: bars, structure, an arrangement. A 40 ms interface tick or a turret discharge has none of those. Asking it for combat effects gets you short songs, not sounds.</div>
</div></section>

<section class="sec"><div class="rail"><b>01</b><span>Listen</span></div><div class="body-col">
<h2>The bank — __NBANK__ sounds</h2>
<p>Waveforms are real peak envelopes drawn from the samples. <strong>Crest</strong> is peak-to-RMS ratio — it's the number that tells you whether a sound has punch or has been crushed flat. Loops are marked; they're built at durations where every partial completes a whole number of cycles, so they repeat without a seam.</p>
<div id="bank"></div>
<div class="note"><b>Listen for →</b> Confirm and Deny are an open fifth and a minor second. That's the score bible's interval language applied to interface audio, so the UI and the soundtrack argue from the same harmonic vocabulary instead of coexisting by accident.</div>
</div></section>

<section class="sec"><div class="rail"><b>02</b><span>The sixty</span></div><div class="body-col">
<h2>Full bank plan</h2>
<p>Synthesis wins wherever a sound is electrical, energetic, or interface — which is most of this game. It loses on organic complexity: footsteps on stone, debris, weather, anything with a voice. Those get sourced.</p>
<div class="tw"><table><thead><tr><th>Group</th><th>Count</th><th>Method</th><th>Contents</th></tr></thead><tbody id="plan"></tbody></table></div>
<div class="note"><b>Sourcing rule →</b> Freesound filtered to <b>CC0 only</b>. Not CC-BY — attribution obligations across dozens of clips is a liability nobody maintains, and one missed credit is a licence breach. Every sourced clip is logged in <code>assets/audio/SOURCES.md</code> with URL, author, licence and SHA-256 before it enters the repo.</div>
<div class="note"><b>Alternative if you'd rather pay than curate →</b> ElevenLabs Sound Effects is text-to-SFX with commercial rights on paid tiers, and would cover those __NSRC__ organic ones the same way Suno covers the score. I'd still layer its output with synthesis. Your call; CC0 is the default I'll proceed with.</div>
</div></section>

<section class="sec"><div class="rail"><b>03</b><span>Pipeline</span></div><div class="body-col">
<h2>Regenerating</h2>
<div class="cmd">.venv/bin/python tools/audio/<b>synth_sfx.py</b>              # rebuild the bank<br>.venv/bin/python tools/audio/<b>synth_sfx.py</b> ui_click     # one sound<br>.venv/bin/python tools/audio/<b>synth_sfx.py</b> --list       # what exists<br>.venv/bin/python tools/audio/<b>make_soundboard.py</b>        # rebuild this page</div>
<p>WAVs land in <code>assets/audio/sfx/</code> at 44.1 kHz mono. Godot import settings and the Ogg encode happen at build time, not here — the repo keeps the lossless masters.</p>
<div class="note bad"><b>Blocked →</b> <code>ffmpeg</code> on your machine is broken: <code>Library not loaded: libx265.215.dylib</code>. Not needed for these, but the music loop pipeline needs it. Run <code>brew reinstall ffmpeg</code> when convenient.</div>
</div></section>

<footer class="foot"><span>Latticefall · SFX bank Rev A</span><span>__NBANK__ of 60 built</span><span>Synthesized, not sampled</span></footer>
</div>
<script>
var SOUNDS = __SOUNDS__, PLAN = __PLAN__;
(function(){
  var bank=document.getElementById('bank'), ctx=null, cur=null, groups=[];
  SOUNDS.forEach(function(s){ if(groups.indexOf(s.group)<0) groups.push(s.group); });

  function wave(env){
    var w=600,h=34,mid=h/2,step=w/env.length,d='';
    for(var i=0;i<env.length;i++){
      var x=(i*step).toFixed(1), a=Math.max(env[i]*mid*0.94,0.5);
      d+='M'+x+' '+(mid-a).toFixed(1)+'L'+x+' '+(mid+a).toFixed(1);
    }
    return '<svg class="wave" viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" aria-hidden="true">'+
           '<path d="'+d+'" stroke="var(--verd)" stroke-width="1.4" fill="none" opacity=".85"/></svg>';
  }

  groups.forEach(function(g){
    var head=document.createElement('div'); head.className='grp'; head.textContent=g; bank.appendChild(head);
    SOUNDS.filter(function(s){return s.group===g;}).forEach(function(s){
      var el=document.createElement('div'); el.className='snd';
      el.innerHTML='<button class="play" type="button" aria-label="Play '+s.label+'">▶</button>'+
        '<div class="snd-mid"><span class="snd-name">'+s.label+(s.loop?' <span class="lp">LOOP</span>':'')+'</span>'+
        '<span class="snd-id">'+s.id+'</span><span class="snd-note">'+s.note+'</span>'+wave(s.env)+'</div>'+
        '<div class="snd-meta"><span><b>'+s.dur.toFixed(2)+'</b>s</span><span>crest '+s.crest.toFixed(1)+'</span>'+
        '<span>rms '+s.rms.toFixed(3)+'</span><span>'+s.kb.toFixed(0)+' KB</span></div>';
      var btn=el.querySelector('.play'), audio=new Audio(s.uri);
      audio.loop=s.loop;
      audio.addEventListener('ended',function(){btn.classList.remove('on');btn.textContent='▶';});
      btn.addEventListener('click',function(){
        if(cur&&cur.a!==audio){cur.a.pause();cur.a.currentTime=0;cur.b.classList.remove('on');cur.b.textContent='▶';}
        if(!audio.paused){audio.pause();audio.currentTime=0;btn.classList.remove('on');btn.textContent='▶';cur=null;return;}
        audio.currentTime=0; audio.play();
        btn.classList.add('on'); btn.textContent=s.loop?'■':'▶'; cur={a:audio,b:btn};
        if(!s.loop) setTimeout(function(){btn.classList.remove('on');btn.textContent='▶';}, s.dur*1000+80);
      });
      bank.appendChild(el);
    });
  });

  var tb=document.getElementById('plan');
  PLAN.forEach(function(r){
    var tr=document.createElement('tr');
    tr.innerHTML='<td>'+r[0]+'</td><td>'+r[1]+'</td>'+
      '<td><span class="pill '+(r[2]==='S'?'s':'c')+'">'+(r[2]==='S'?'SYNTH':'CC0 + SYNTH')+'</span></td><td>'+r[3]+'</td>';
    tb.appendChild(tr);
  });
})();
</script>
"""


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    html = build()
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(ROOT)}  {len(html)/1024:.0f} KB")
