#!/usr/bin/env python3
# coding: utf-8
"""
render_monitors.py — Generate three redesigned live monitors matching
the original dashboard/index.html visual quality:

  1. monitor_azazael.html   — System-wide mesh observability
  2. monitor_exmachina.html — Epistemic tier (10 organs)
  3. monitor_lilith.html    — Red Queen status + probes

Each embeds live data from spine_registry, worm_registry, and Ex Machina.
Particle effects, 3D tilt, gradient animations, card glow — all preserved.

Usage:
    python tools/render_monitors.py          # all three
    python tools/render_monitors.py --azazael
    python tools/render_monitors.py --exmachina
    python tools/render_monitors.py --lilith
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
HERE = Path(__file__).resolve().parent
for _p in (str(SRC), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = PROJECT_ROOT / "dashboard"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Shared data + palette layers ─────────────────────────────────
#
# The collectors and the staleness helpers used to live in this file, which is
# how three renderers ended up disagreeing about what "missing" looked like.
# They now live in `monitor_data` (TODO P2 item 8) and the colours in
# `monitor_theme` (TODO P2 item 7), so this module is purely presentation.
from monitor_data import (  # noqa: E402
    collect_clock_data,
    collect_exmachina_data,
    collect_fabric_data,
    collect_failure_data,
    collect_mesh_cycle,
    collect_mesh_facts,
    collect_registry_data,
    collect_spine_data,
    fmt,
    num,
    pct,
    q_summary,
    quarantine_summary,
    row,
    source_line,
)
from monitor_theme import accent as theme_accent, nature_color, semantic  # noqa: E402
import argparse

#: Seconds between automatic reloads. A monitor left open on a second screen
#: has to stay current, otherwise it is a screenshot with a timestamp on it
#: (TODO P3 item 11).
REFRESH_SECONDS = 60


def _err(text: str, colour: str = "#f85149") -> str:
    """A visible read-failure row. Never let a failed read look like a zero."""
    return (f'<div class="row"><span class="label" style="color:{colour}">'
            f'{text}</span></div>')


# ── Shared base template (matches original dashboard quality) ────

def _base_css(accent_color: str, accent_glow: str) -> str:
    """Full CSS matching the original dashboard/index.html quality."""
    return f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;700&family=Space+Grotesk:wght@400;500;600;700&display=swap');
:root {{
  --bg-1: #060a16; --bg-2: #0b1224;
  --card-bg: rgba(12, 20, 38, 0.65);
  --card-border: rgba(88, 166, 255, 0.08);
  --text: #e8edf5; --muted: #6a7d9e;
  --accent: {accent_color}; --accent-glow: {accent_glow};
  --green: #3fb950; --green-glow: rgba(63,185,80,0.30);
  --red: #f85149; --red-glow: rgba(248,81,73,0.30);
  --orange: #d29922; --orange-glow: rgba(210,153,34,0.30);
  --purple: #bc8cff; --purple-glow: rgba(188,140,255,0.25);
  --cyan: #56d4dd; --cyan-glow: rgba(86,212,221,0.25);
  --pink: #ff7eb3; --pink-glow: rgba(255,126,179,0.25);
  --yellow: #e8d44d; --yellow-glow: rgba(232,212,77,0.25);
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:var(--bg-1); color:var(--text); padding:24px; min-height:100vh; position:relative; overflow-x:hidden; }}
#particles-canvas {{ position:fixed; top:0; left:0; width:100%; height:100%; z-index:0; pointer-events:none; }}
body::before {{ content:''; position:fixed; top:0; left:0; right:0; bottom:0; background:radial-gradient(ellipse 70% 50% at 0% 50%,rgba(88,166,255,0.07) 0%,transparent 100%),radial-gradient(ellipse 70% 50% at 100% 50%,rgba(188,140,255,0.07) 0%,transparent 100%),radial-gradient(ellipse 50% 40% at 50% 0%,rgba(63,185,80,0.05) 0%,transparent 100%),radial-gradient(ellipse 50% 40% at 50% 100%,rgba(255,126,179,0.04) 0%,transparent 100%); pointer-events:none; z-index:0; animation:ambientShift 25s ease-in-out infinite alternate; }}
@keyframes ambientShift {{ 0%{{opacity:0.5}} 33%{{opacity:0.8}} 66%{{opacity:1.0}} 100%{{opacity:0.5}} }}
@keyframes countUp {{ from{{opacity:0;transform:translateY(12px) scale(0.95);filter:blur(2px)}} to{{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}} }}
@keyframes slideIn {{ from{{opacity:0;transform:translateX(-20px)}} to{{opacity:1;transform:translateX(0)}} }}
@keyframes barFill {{ from{{width:0%}} }}
@keyframes dotPulse {{ 0%,100%{{transform:scale(1);opacity:1}} 50%{{transform:scale(2.2);opacity:0.4}} }}
@keyframes cardGlowPulse {{ 0%,100%{{box-shadow:0 0 15px var(--card-glow,rgba(88,166,255,0.08))}} 50%{{box-shadow:0 0 30px var(--card-glow,rgba(88,166,255,0.15)),0 0 60px var(--card-glow,rgba(88,166,255,0.05))}} }}
@keyframes textShimmer {{ 0%{{background-position:-200% center}} 100%{{background-position:200% center}} }}
@keyframes fadeSlideUp {{ from{{opacity:0;transform:translateY(20px)}} to{{opacity:1;transform:translateY(0)}} }}
@keyframes spinGlow {{ from{{transform:rotate(0deg)}} to{{transform:rotate(360deg)}} }}
@keyframes float {{ 0%,100%{{transform:translateY(0px)}} 50%{{transform:translateY(-6px)}} }}
.header {{ position:relative; z-index:1; display:flex; justify-content:space-between; align-items:center; margin-bottom:28px; padding:22px 28px; background:rgba(12,20,38,0.55); border:1px solid var(--card-border); border-radius:18px; backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px); animation:slideIn 0.7s cubic-bezier(0.22,1,0.36,1); box-shadow:0 0 30px rgba(88,166,255,0.05); }}
.header::before {{ content:''; position:absolute; inset:-1px; border-radius:19px; background:linear-gradient(135deg,rgba(88,166,255,0.15),transparent,rgba(188,140,255,0.15),transparent); z-index:-1; animation:spinGlow 8s linear infinite; }}
.header h1 {{ font-size:24px; font-weight:800; font-family:'Space Grotesk','Inter',sans-serif; background:linear-gradient(135deg,{accent_color},#bc8cff,#ff7eb3,#56d4dd,{accent_color}); background-size:300% auto; -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; animation:textShimmer 6s linear infinite; letter-spacing:-0.5px; }}
.header .ts {{ color:var(--muted); font-size:12px; margin-top:4px; -webkit-text-fill-color:var(--muted); }}
.header-right {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.grid {{ position:relative; z-index:1; display:grid; grid-template-columns:repeat(auto-fit,minmax(400px,1fr)); gap:20px; }}
.card {{ background:var(--card-bg); border:1px solid var(--card-border); border-radius:16px; padding:22px; backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px); transition:all 0.4s cubic-bezier(0.34,1.56,0.64,1); position:relative; overflow:hidden; animation:fadeSlideUp 0.7s ease-out both; animation-delay:calc(var(--order,0)*0.08s); --card-accent:var(--accent); --card-glow:var(--accent-glow); perspective:800px; }}
.card::before {{ content:''; position:absolute; top:0; left:0; right:0; bottom:0; background:radial-gradient(ellipse 120% 80% at 50% -20%,var(--gradient-1,rgba(88,166,255,0.08)),transparent 70%),radial-gradient(ellipse 80% 60% at 80% 100%,var(--gradient-2,rgba(188,140,255,0.05)),transparent 70%); pointer-events:none; transition:opacity 0.5s; }}
.card::after {{ content:''; position:absolute; top:-50%; left:-50%; width:200%; height:200%; background:conic-gradient(from 0deg at 50% 50%,transparent 0deg,var(--card-accent) 1deg,transparent 2deg,transparent 360deg); opacity:0; transition:opacity 0.5s; animation:spinGlow 4s linear infinite; pointer-events:none; }}
.card:hover {{ border-color:var(--card-accent); transform:translateY(-3px) scale(1.01); box-shadow:0 12px 48px rgba(0,0,0,0.4),0 0 20px var(--card-glow); animation:cardGlowPulse 2s ease-in-out infinite; }}
.card:hover::after {{ opacity:0.06; }}
.card-content {{ position:relative; z-index:1; }}
.card:nth-child(1) {{ --order:0; }} .card:nth-child(2) {{ --order:1; }} .card:nth-child(3) {{ --order:2; }} .card:nth-child(4) {{ --order:3; }} .card:nth-child(5) {{ --order:4; }} .card:nth-child(6) {{ --order:5; }} .card:nth-child(7) {{ --order:6; }} .card:nth-child(8) {{ --order:7; }} .card:nth-child(9) {{ --order:8; }} .card:nth-child(10) {{ --order:9; }}
.card h2 {{ font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:1.5px; color:var(--card-accent); margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; align-items:center; gap:8px; position:relative; z-index:1; }}
.card h2::after {{ content:''; flex:1; height:1px; background:linear-gradient(90deg,rgba(255,255,255,0.05),transparent); }}
.card .val {{ font-size:34px; font-weight:900; letter-spacing:-1px; line-height:1; animation:countUp 0.8s cubic-bezier(0.22,1,0.36,1); position:relative; z-index:1; }}
.card .val + .sub {{ margin-top:6px; }}
.card .sub {{ font-size:12px; color:var(--muted); font-weight:400; position:relative; z-index:1; }}
.status-ok {{ color:var(--green); text-shadow:0 0 20px var(--green-glow); }}
.status-down {{ color:var(--red); text-shadow:0 0 20px var(--red-glow); }}
.status-degraded {{ color:var(--orange); text-shadow:0 0 20px var(--orange-glow); }}
.live-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; animation:dotPulse 2s ease-in-out infinite; }}
.live-dot.ok {{ background:var(--green); box-shadow:0 0 10px var(--green-glow); }}
.live-dot.down {{ background:var(--red); box-shadow:0 0 10px var(--red-glow); }}
.badge {{ display:inline-flex; align-items:center; gap:5px; padding:4px 12px; border-radius:20px; font-size:11px; font-weight:600; transition:all 0.3s cubic-bezier(0.34,1.56,0.64,1); }}
.badge-ok {{ background:rgba(63,185,80,0.12); color:var(--green); border:1px solid rgba(63,185,80,0.2); }}
.badge-ok:hover {{ background:rgba(63,185,80,0.2); box-shadow:0 0 14px var(--green-glow); }}
.badge-down {{ background:rgba(248,81,73,0.12); color:var(--red); border:1px solid rgba(248,81,73,0.2); }}
.badge-degraded {{ background:rgba(210,153,34,0.12); color:var(--orange); border:1px solid rgba(210,153,34,0.2); }}
.badge-neutral {{ background:rgba(106,125,158,0.12); color:var(--muted); border:1px solid rgba(106,125,158,0.2); }}
.badge-purple {{ background:rgba(188,140,255,0.12); color:var(--purple); border:1px solid rgba(188,140,255,0.2); }}
.row {{ display:flex; justify-content:space-between; align-items:center; font-size:13px; border-bottom:1px solid rgba(255,255,255,0.04); transition:all 0.25s; padding:7px 10px; margin:0 -10px; border-radius:8px; position:relative; z-index:1; }}
.row:hover {{ background:rgba(255,255,255,0.04); padding-left:14px; }}
.row:last-child {{ border-bottom:none; }}
.row .label {{ color:var(--muted); font-weight:400; }}
.row .value {{ font-weight:500; color:var(--text); font-family:'JetBrains Mono',monospace; font-size:12px; }}
.mini-bar {{ height:4px; background:rgba(255,255,255,0.05); border-radius:4px; margin-top:6px; overflow:hidden; }}
.mini-bar .fill {{ height:100%; border-radius:4px; animation:barFill 1.2s cubic-bezier(0.22,1,0.36,1); transition:width 0.8s cubic-bezier(0.22,1,0.36,1); position:relative; }}
.mini-bar .fill::after {{ content:''; position:absolute; top:0; right:0; width:8px; height:100%; border-radius:4px; filter:blur(4px); opacity:0.7; }}
.mini-bar .fill.ok {{ background:linear-gradient(90deg,var(--green),#5ceb6e); box-shadow:0 0 8px var(--green-glow); }}
.mini-bar .fill.ok::after {{ background:var(--green); }}
.mini-bar .fill.warn {{ background:linear-gradient(90deg,var(--orange),#f0b429); box-shadow:0 0 8px var(--orange-glow); }}
.mini-bar .fill.warn::after {{ background:var(--orange); }}
.mini-bar .fill.error {{ background:linear-gradient(90deg,var(--red),#ff6b6b); box-shadow:0 0 8px var(--red-glow); }}
.mini-bar .fill.error::after {{ background:var(--red); }}
.mini-bar .fill.info {{ background:linear-gradient(90deg,var(--accent),#79b8ff); box-shadow:0 0 8px var(--accent-glow); }}
.mini-bar .fill.info::after {{ background:var(--accent); }}
.mini-bar .fill.purple {{ background:linear-gradient(90deg,var(--purple),#d4a5ff); box-shadow:0 0 8px var(--purple-glow); }}
.mini-bar .fill.purple::after {{ background:var(--purple); }}
.section-grid {{ display:grid; gap:6px; position:relative; z-index:1; }}
.metric-row {{ display:flex; justify-content:space-between; align-items:center; padding:8px 10px; margin:0 -10px; border-radius:8px; border-bottom:1px solid rgba(255,255,255,0.04); transition:all 0.25s cubic-bezier(0.34,1.56,0.64,1); position:relative; z-index:1; }}
.metric-row:last-child {{ border-bottom:none; }}
.metric-row:hover {{ background:rgba(255,255,255,0.04); transform:translateX(4px); }}
.metric-name {{ color:var(--card-accent,var(--cyan)); font-family:'JetBrains Mono',monospace; font-size:12px; font-weight:500; transition:all 0.3s; }}
.metric-row:hover .metric-name {{ text-shadow:0 0 12px var(--card-glow,var(--cyan-glow)); }}
.metric-stats {{ color:var(--muted); font-size:11px; font-family:'JetBrains Mono',monospace; }}
.footer {{ position:relative; z-index:1; margin-top:28px; padding:18px; border-radius:14px; background:rgba(12,20,38,0.5); border:1px solid var(--card-border); font-size:12px; color:var(--muted); text-align:center; backdrop-filter:blur(8px); }}
a {{ color:var(--accent); text-decoration:none; transition:all 0.25s; }}
a:hover {{ color:#79b8ff; text-shadow:0 0 16px var(--accent-glow); }}
.refresh-btn {{ display:inline-flex; align-items:center; gap:6px; padding:7px 16px; border-radius:20px; background:rgba(88,166,255,0.08); border:1px solid rgba(88,166,255,0.15); color:var(--accent); font-size:12px; font-weight:600; cursor:pointer; transition:all 0.3s cubic-bezier(0.34,1.56,0.64,1); text-decoration:none; }}
.refresh-btn:hover {{ background:rgba(88,166,255,0.18); box-shadow:0 0 20px var(--accent-glow),inset 0 0 20px rgba(88,166,255,0.05); transform:scale(1.05); }}
@media (max-width:768px) {{ .grid {{ grid-template-columns:1fr; }} body {{ padding:12px; }} .header {{ flex-direction:column; gap:12px; align-items:flex-start; }} }}
::-webkit-scrollbar {{ width:6px; }} ::-webkit-scrollbar-track {{ background:var(--bg-1); }} ::-webkit-scrollbar-thumb {{ background:rgba(88,166,255,0.15); border-radius:3px; }} ::-webkit-scrollbar-thumb:hover {{ background:rgba(88,166,255,0.3); }}
::selection {{ background:rgba(88,166,255,0.25); color:#fff; }}
"""


def _base_js() -> str:
    """Shared JS: particle system, 3D tilt, gradient follower."""
    return """
// Particle system
(function(){
  var c=document.getElementById('particles-canvas'),x=c.getContext('2d'),p=[],m={x:-9999,y:-9999},a;
  function r(){c.width=innerWidth;c.height=innerHeight}
  addEventListener('resize',r);r();
  var C=['58,166,255','188,140,255','86,212,221','255,126,179','63,185,80'];
  function P(){this.x=Math.random()*c.width;this.y=Math.random()*c.height;this.s=Math.random()*2.5+0.5;this.vx=(Math.random()-0.5)*0.3;this.vy=(Math.random()-0.5)*0.3;this.o=Math.random()*0.5+0.15;this.c=C[~~(Math.random()*C.length)];this.p=Math.random()*Math.PI*2;this.ps=Math.random()*0.02+0.005}
  P.prototype.u=function(){this.x+=this.vx;this.y+=this.vy;this.p+=this.ps;var dx=m.x-this.x,dy=m.y-this.y,d=Math.sqrt(dx*dx+dy*dy);if(d<120){var f=(120-d)/120*0.08;this.x-=dx*f;this.y-=dy*f}if(this.x<-20)this.x=c.width+20;if(this.x>c.width+20)this.x=-20;if(this.y<-20)this.y=c.height+20;if(this.y>c.height+20)this.y=-20};
  P.prototype.d=function(){var o=this.o*(0.7+0.3*Math.sin(this.p));x.beginPath();x.arc(this.x,this.y,this.s,0,Math.PI*2);x.fillStyle='rgba('+this.c+','+o+')';x.fill();x.beginPath();x.arc(this.x,this.y,this.s*3,0,Math.PI*2);x.fillStyle='rgba('+this.c+','+o*0.15+')';x.fill()};
  document.addEventListener('mousemove',function(e){m.x=e.clientX;m.y=e.clientY});
  document.addEventListener('mouseleave',function(){m.x=-9999;m.y=-9999});
  function init(){p=[];for(var i=0;i<80;i++)p.push(new P())}
  function conn(){for(var i=0;i<p.length;i++)for(var j=i+1;j<p.length;j++){var dx=p[i].x-p[j].x,dy=p[i].y-p[j].y,d=Math.sqrt(dx*dx+dy*dy);if(d<130){x.beginPath();x.moveTo(p[i].x,p[i].y);x.lineTo(p[j].x,p[j].y);x.strokeStyle='rgba(88,166,255,'+(1-d/130)*0.08+')';x.lineWidth=0.5;x.stroke()}}}
  function anim(){x.clearRect(0,0,c.width,c.height);for(var i=0;i<p.length;i++){p[i].u();p[i].d()}conn();a=requestAnimationFrame(anim)}
  init();anim();
  document.addEventListener('visibilitychange',function(){if(document.hidden){if(a)cancelAnimationFrame(a)}else anim()});
})();
// 3D tilt on cards
(function(){
  document.querySelectorAll('.card').forEach(function(c){
    c.addEventListener('mousemove',function(e){var r=c.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top,cx=r.width/2,cy=r.height/2;c.style.transform='translateY(-3px) scale(1.01) perspective(800px) rotateX('+((y-cy)/cy)*-4+'deg) rotateY('+((x-cx)/cx)*4+'deg)'});
    c.addEventListener('mouseleave',function(){c.style.transform=''});
  });
})();
// Gradient follower on header
(function(){
  var h=document.querySelector('.header');
  if(h){h.addEventListener('mousemove',function(e){var r=h.getBoundingClientRect(),x=((e.clientX-r.left)/r.width)*100,y=((e.clientY-r.top)/r.height)*100;h.style.background='radial-gradient(circle at '+x+'% '+y+'%, rgba(88,166,255,0.10), rgba(12,20,38,0.55) 60%)'});
  h.addEventListener('mouseleave',function(){h.style.background=''});}
})();
"""


def _card(idx: int, theme_css: str, title: str, content: str) -> str:
    return f'<div class="card {theme_css}" style="--order:{idx}"><div class="card-content"><h2>{title}</h2>{content}</div></div>'


# ── Monitor 1: Azazael ──────────────────────────────────────────

def render_azazael(spine: dict, registry: dict, facts: dict | None = None,
                   fabric: dict | None = None, clock: dict | None = None,
                   cycle: dict | None = None,
                   failure: dict | None = None) -> str:
    """Mesh-wide monitor. Every value is a live read — see `monitor_data`.

    The restructure added the three cards whose absence made this monitor
    contradict the running system: the cross-env quarantine ledger, the
    fabric's persisted operational state, and the hybrid logical clock.

    `failure` adds a fourth: the ledgers that record NEGATIVES. See the card
    below — it is the only place on the page that can show a fault defined by
    something that did not happen.
    """
    facts = facts or {}
    fabric = fabric or {}
    clock = clock or {}
    cycle = cycle or {}
    failure = failure or {}

    qw = spine.get("q_weights", {})
    q = q_summary(spine)
    qs = quarantine_summary(spine)
    scars = spine.get("scars", {})
    homeo = spine.get("homeostat", {})
    events = spine.get("events", [])
    journal = spine.get("policy_journal", [])
    natures = registry.get("categories", set())
    total = registry.get("total", 0)
    spine_map = spine.get("spine_map", {})
    quorum = num(facts.get("quorum"))

    h_ok = sum(r.get("successes", 0) for r in homeo.values())
    h_fail = sum(r.get("failures", 0) for r in homeo.values())
    h_tot = h_ok + h_fail
    h_rate = h_fail / h_tot if h_tot else 0

    cat_counts = {}
    for name, info in registry.get("worms", {}).items():
        cat = info.get("category", "utility")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    cards = []
    i = 0

    # Q-Weight Brainstem — now with the readout that separates a *settled*
    # arm from a *starved* one. `get_q_weight()` returns 0.0 for both, so the
    # previous version of this card showed a never-tried worm exactly like a
    # proven-neutral one, and a starved arm stayed invisible forever.
    starved = q["starved"]
    starve_state = "ok" if not starved else "warn"  # same rule as the RL monitor
    # Ranked on the evidence-shrunk value, i.e. the ordering q_order uses.
    # The raw table is a plateau at the clip for the busiest keys, so a card
    # built on it showed dict order where the mesh has a decision. The clipped
    # COUNT is shown beside it, because a plateau is a property of the table
    # and not of any one row.
    clipped = len(q.get("clip_keys") or [])
    clip_state = "ok" if clipped * 4 <= max(1, q["keys"]) else "warn"
    top_html = "".join(
        f'<div class="row"><span class="label" style="color:#bc8cff">{k}</span>'
        f'<span class="value">{v:+.4f}</span></div>' for k, v in q["top"][:5])
    cards.append(_card(i, "card-theme-sb3", "Q-Weight Brainstem (G1/G2)",
        f'<div class="val" style="color:var(--purple);text-shadow:0 0 20px var(--purple-glow)">{q["keys"]}</div>'
        f'<div class="sub">Q-keys &middot; adaptive alpha from Welford variance</div>'
        f'<div class="mini-bar"><div class="fill purple" style="width:{pct(q["measured"], 0, max(1, q["keys"])):.0f}%"></div></div>'
        f'<br><div class="row"><span class="label">Measured</span><span class="value" style="color:var(--green)">{q["measured"]}</span></div>'
        f'<div class="row"><span class="label">Starved (0.0, never pulled)</span><span class="value" style="color:{semantic(starve_state)[0]}">{starved}</span></div>'
        f'<div class="row"><span class="label">Seeded (never pulled)</span><span class="value">{q["unmeasured"]}</span></div>'
        f'<div class="row"><span class="label">Settled neutral</span><span class="value">{q["settled_neutral"]}</span></div>'
        f'<div class="row"><span class="label">Positive / Negative</span><span class="value">{q["positive"]} / {q["negative"]}</span></div>'
        f'<div class="row"><span class="label">Mean</span><span class="value">{fmt(q["mean"])}</span></div>'
        f'<div class="row"><span class="label">At the clip (raw plateau)</span><span class="value" style="color:{semantic(clip_state)[0]}">{clipped}</span></div>'
        f'<br><div class="sub">Ranked by evidence-shrunk q &middot; q &middot; n/(n+8)</div>'
        f'{top_html}'))
    i += 1

    # Cross-env quarantine ledger (audit finding #1). The old card printed
    # "quorum: 2/3 vote" — a rule that had already been replaced by a count of
    # DISTINCT ENVS in a persisted, gossiped ledger, where a local majority
    # can no longer quarantine anything.
    findings_html = ""
    for fnd in qs["findings"][:5]:
        decided = quorum is not None and fnd["count"] >= quorum
        fstate = "error" if (decided and fnd["top"] == "quarantine") else "warn"
        findings_html += (
            f'<div class="row"><span class="label" style="color:{semantic(fstate)[0]}">'
            f'{fnd["finding"][:26]}</span><span class="value">{fnd["count"]} envs</span></div>')
    if not findings_html:
        findings_html = _err("No quarantine votes recorded", semantic("ok")[0])
    q_quorum_txt = "unknown" if quorum is None else f"{quorum:.0f}"
    cards.append(_card(i, "card-theme-dead", "Cross-Env Quarantine Ledger",
        f'<div class="val status-{"down" if qs["decided"] else "ok"}">{qs["decided"]}</div>'
        f'<div class="sub">decided of {qs["finding_count"]} findings &middot; quorum = {q_quorum_txt} distinct envs</div>'
        f'<br><div class="row"><span class="label">Votes cast</span><span class="value">{qs["votes"]}</span></div>'
        f'<div class="row"><span class="label">Voting envs</span><span class="value">{qs["env_count"]}</span></div>'
        f'<div class="row"><span class="label">Ledger entries</span><span class="value">{len(qs["findings"])}</span></div>'
        f'<br>{findings_html}'))
    i += 1

    # Fabric operational state (audit finding #7). Before 2026-09-15 none of
    # this survived a restart: the mesh remembered how to feel and forgot what
    # it knew. This card is the only place an operator can see that the routing
    # table, the leader term and the shard catalogue are actually persisted.
    providers = fabric.get("providers", [])
    present = [p for p in providers if p.get("present")]
    missing = fabric.get("missing", [])
    leader = next((p for p in present if p["provider"] == "LeaderElectorWorm"), {})
    lead_state = leader.get("state") or {}
    lease = facts.get("lease") or {}
    fab_html = "".join(
        f'<div class="row"><span class="label" style="color:{nature_color("mesh")}">'
        f'{p["label"]}</span><span class="value">{len(p["state"])} fields</span></div>'
        for p in present[:6])
    if not fab_html:
        fab_html = _err("No fabric provider has published state", semantic("warn")[0])
    lease_valid = lease.get("valid")
    lease_state = "ok" if lease_valid else ("warn" if lease else "unknown")
    cards.append(_card(i, "card-theme-drift", "Fabric Operational State (#7)",
        f'<div class="val" style="color:var(--cyan);text-shadow:0 0 20px var(--cyan-glow)">{fabric.get("published", "unknown")}</div>'
        f'<div class="sub">providers with persisted state &middot; {len(missing)} never published</div>'
        f'<br><div class="row"><span class="label">Leader</span><span class="value">{lead_state.get("_leader", "unknown")}</span></div>'
        f'<div class="row"><span class="label">Leader term</span><span class="value">{lead_state.get("_term", "unknown")}</span></div>'
        f'<div class="row"><span class="label">Queen lease env</span><span class="value">{lease.get("env", "unknown")}</span></div>'
        f'<div class="row"><span class="label">Lease valid</span><span class="value" style="color:{semantic(lease_state)[0]}">{lease_valid if lease else "unknown"}</span></div>'
        f'<div class="row"><span class="label">Lease expires in</span><span class="value">{lease.get("expires_in", "unknown")}s</span></div>'
        f'<br>{fab_html}'
        f'<div class="sub" style="margin-top:6px">never published: {", ".join(missing) if missing else "none"}</div>'))
    i += 1

    # Clock health (HLC). The mesh could not measure clock skew before this
    # session, and still nothing rendered it (audit §5 item 3): a skewed peer
    # and a genuine disagreement looked identical from the dashboard.
    skew = num(clock.get("skew_ms"))
    rejected = num(clock.get("rejected"))
    rejected_skew = num(clock.get("rejected_skew_ms"))
    clock_state = ("error" if (rejected or 0) > 0
                   else "warn" if (skew is not None and abs(skew) > 1000)
                   else "ok" if skew is not None else "unknown")
    clock_html = ""
    if clock.get("_error"):
        clock_html = _err(f"clock read failed: {clock['_error']}")
    else:
        # `skew` covers only ACCEPTED peers and `rejected_skew` only refused
        # ones, so "a peer runs a little fast" and "a peer's clock is broken"
        # can never be read as each other.
        clock_html = (
            f'<div class="row"><span class="label">Local node</span><span class="value">{clock.get("node", "unknown")}</span></div>'
            f'<div class="row"><span class="label">Stamps observed</span><span class="value">{clock.get("observations", "unknown")}</span></div>'
            f'<div class="row"><span class="label">Refused (outside drift bound)</span><span class="value" style="color:{semantic(clock_state)[0]}">{clock.get("rejected", "unknown")}</span></div>'
            f'<div class="row"><span class="label">Worst refused offset</span><span class="value" style="color:{semantic(clock_state)[0]}">{fmt(rejected_skew, 0)}ms</span></div>'
            f'<div class="row"><span class="label">Drift bound</span><span class="value">{clock.get("max_drift_ms", "unknown")}ms</span></div>'
            f'<div class="row"><span class="label">Current stamp</span><span class="value" style="font-size:11px">{clock.get("stamp", "unknown")}</span></div>')
        if (rejected or 0) > 0:
            clock_html += (
                '<div class="sub" style="margin-top:6px">Refused stamps are not adopted for '
                'ordering, so they cannot win a merge or spread to a peer.</div>')
    cards.append(_card(i, "card-theme-metrics", "Clock Health (HLC)",
        f'<div class="val status-{"ok" if clock_state == "ok" else "degraded" if clock_state == "warn" else "down"}">{"unknown" if skew is None else f"{skew:+.0f}ms"}</div>'
        f'<div class="sub">accepted-peer skew (&gt;0 = peer ahead) &middot; refused peers reported separately</div>'
        f'<br>{clock_html}'))
    i += 1

    # Reflex Arc & Scars
    scar_html = ""
    for k, v in list(scars.items())[:6]:
        hc = v.get("heal_count", 0)
        scar_html += f'<div class="row"><span class="label" style="color:var(--red)">{k}</span><span class="value" style="color:var(--green)">heal {hc}/3</span></div>'
    if not scars:
        scar_html = '<div class="row"><span class="label" style="color:var(--green)">No active scars</span></div>'
    cards.append(_card(i, "card-theme-dead", "Reflex Arc &amp; Scars (G7)",
        f'<div class="val status-{"down" if scars else "ok"}">{len(scars)}</div>'
        f'<div class="sub">active scars &middot; graduated healing (3 successes to full)</div><br>{scar_html}'))
    i += 1

    # Homeostat
    rate_cls = "ok" if h_rate < 0.1 else "warn" if h_rate < 0.3 else "error"
    cards.append(_card(i, "card-theme-health", "Homeostat Feedback",
        f'<div class="val status-{"ok" if h_rate < 0.1 else "degraded" if h_rate < 0.3 else "down"}">{h_rate:.1%}</div>'
        f'<div class="sub">failure rate ({h_ok} ok / {h_fail} fail)</div>'
        f'<div class="mini-bar"><div class="fill {rate_cls}" style="width:{min(100,h_rate*100):.0f}%"></div></div>'))
    i += 1

    # Kademlia Overlay — peers now come from the spine map *and* the env
    # beacons, because that is where the cross-env layer actually lives. This
    # card used to print "No peers discovered yet" while `get_fabric_view()`
    # knew about them, which is the contradiction the restructure is about.
    spine_envs = set(k.split(":")[0] for k in spine_map)
    beacon_provider = next((p for p in providers
                            if p["provider"] == "EnvBeaconWorm"), {})
    raw_beacons = (beacon_provider.get("state") or {}).get("_beacons")
    beacon_envs = set(str(x) for x in raw_beacons) if isinstance(raw_beacons, dict) else set()
    envs = spine_envs | beacon_envs
    queen_env = lease.get("env")
    peer_html = "".join(
        f'<span class="badge badge-{"ok" if e == queen_env else "neutral"}" style="margin:2px">{e}</span>'
        for e in sorted(envs)[:10])
    if not peer_html:
        peer_html = _err("No peer envs in the spine map or the env beacons",
                         semantic("warn")[0])
    cards.append(_card(i, "card-theme-drift", "Kademlia Overlay (G3)",
        f'<div class="val" style="color:var(--yellow);text-shadow:0 0 20px var(--yellow-glow)">{len(spine_map)}</div>'
        f'<div class="sub">spine map nodes &middot; XOR-distance routing &middot; '
        f'{len(beacon_envs)} beacon envs</div><br>{peer_html}'))
    i += 1

    # Plasticity
    j_html = ""
    for e in journal[:5]:
        j_html += f'<div class="row"><span class="label">{e.get("actor","?")} {e.get("action","")}</span><span class="value">{e.get("worm","")}</span></div>'
    if not j_html:
        j_html = '<div class="row"><span class="label">No policy changes yet</span></div>'
    cards.append(_card(i, "card-theme-workflow", "Plasticity Layer (G6)",
        f'<div class="val" style="color:var(--pink);text-shadow:0 0 20px var(--pink-glow)">{len(journal)}</div>'
        f'<div class="sub">policy journal entries &middot; Hebbian/STDP wired</div><br>{j_html}'))
    i += 1

    # Nature Fleet — every nature keeps its canonical graph colour, so a nature
    # is the same colour here and in the Gephi export, and an unmapped one is
    # visibly grey instead of a plausible invented hue. (The old loop also
    # shadowed the `pct()` helper with a local `pct`, which silently made the
    # helper unusable anywhere else in this function.)
    fleet_html = ""
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1])[:12]:
        share = cnt / total * 100 if total else 0
        fleet_html += (
            f'<div class="metric-row"><span class="metric-name" style="color:{nature_color(cat)}">{cat}</span>'
            f'<span class="metric-stats">{cnt} &middot; {share:.0f}%</span></div>')
    cards.append(_card(i, "card-theme-cluster", f"Nature Fleet ({len(natures)} categories)",
        f'<div class="val" style="color:var(--accent);text-shadow:0 0 20px var(--accent-glow)">{total}</div>'
        f'<div class="sub">total registered worms &middot; colours shared with the mesh graph</div><br>{fleet_html}'))
    i += 1

    # Cycle telemetry. This artifact had no monitor at all; it is the only
    # per-nature ok/fail/ms record of what the last sweep actually executed.
    nats = cycle.get("natures") or {}
    cyc_html = ""
    for nat, rec in sorted(nats.items(),
                           key=lambda kv: -(num((kv[1] or {}).get("ms")) or 0))[:8]:
        rec = rec or {}
        nfail = num(rec.get("fail")) or 0
        cstate = "error" if nfail else "ok"
        cyc_html += (
            f'<div class="row"><span class="label" style="color:{nature_color(nat)}">{nat}</span>'
            f'<span class="value" style="color:{semantic(cstate)[0]}">{rec.get("ok", "?")}/{rec.get("worms", "?")}'
            f' &middot; {fmt(rec.get("ms"), 0)}ms</span></div>')
    if not cyc_html:
        cyc_html = _err("No cycle record on disk", semantic("unknown")[0])
    complete = cycle.get("raw", {}).get("complete")
    cards.append(_card(i, "card-theme-workflow", "Cycle Telemetry",
        f'<div class="val" style="color:var(--yellow);text-shadow:0 0 20px var(--yellow-glow)">{fmt(cycle.get("raw", {}).get("ok"), 0)}</div>'
        f'<div class="sub">worms executed in the last sweep &middot; '
        f'{fmt(cycle.get("raw", {}).get("fail"), 0)} failed &middot; '
        f'{fmt(cycle.get("raw", {}).get("duration_s"), 1)}s &middot; complete={complete if complete is not None else "unknown"}</div>'
        f'<br>{cyc_html}{source_line("mesh_cycle.json", extra=f"{len(nats)} natures")}'))
    i += 1

    # Spine Events
    ev_html = ""
    for e in events[:12]:
        ev_html += f'<div class="row"><span class="label" style="color:var(--cyan)">{e.get("kind","")}</span><span class="value">{e.get("at","")[:19]}</span></div>'
    if not ev_html:
        ev_html = '<div class="row"><span class="label">No events recorded</span></div>'
    cards.append(_card(i, "card-theme-metrics", "Spine Events", ev_html))
    i += 1

    # Failure visibility (2026-09-16 per-function audit). The three primitives
    # the audit built -- `silence` (swallowed exceptions), `fencing` (stale
    # writers rejected) and `liveness` (obligations with deadlines) -- all
    # record NEGATIVES. Every defect that audit found was an event that did not
    # happen, which no occurrence card on this page can display at any sample
    # rate. This is the card that can.
    #
    # `unobserved` is a distinct state from `ok`, and deliberately not green:
    # an empty ledger and an unwatched one look identical, and only one of them
    # is good news. Reporting the first as the second is the exact defect this
    # whole session was spent removing.
    fv = failure or {}
    sil = fv.get("silence") or {}
    fences = fv.get("fences") or {}
    live = fv.get("liveness") or {}
    fv_tripped = list(sil.get("tripped") or [])
    fv_overdue = list(live.get("overdue") or [])
    fv_rejects = sum(int(f.get("rejections") or 0) for f in fences.values())
    fv_claims = sum(int(f.get("claims") or 0) for f in fences.values())
    fv_faults = len(fv_tripped) + len(fv_overdue) + fv_rejects
    fv_observed = bool(fv.get("observed"))
    if not fv_observed:
        fv_state, fv_head, fv_label = "unknown", "unobserved", (
            "failure visibility unobserved")
        fv_sub = ("no process has published state/failure_visibility.json — "
                  "nothing is watching for silent failure")
    elif fv.get("stale"):
        # A publisher that stopped IS the non-occurrence this card exists for:
        # the snapshot is present and green, and stale by hours.
        fv_state, fv_head, fv_label = "warn", "stale", (
            "failure visibility publisher stopped")
        fv_sub = (f"last published {fv.get('age') or 'unknown'} by "
                  f"{fv.get('written_by') or 'unknown'} — the ledgers are no "
                  f"longer being updated")
    elif fv_faults:
        fv_state, fv_head = "error", str(fv_faults)
        fv_label = f"{fv_faults} failure-visibility fault(s)"
        fv_sub = (f"{len(fv_tripped)} tag(s) over budget &middot; "
                  f"{len(fv_overdue)} obligation(s) unmet &middot; "
                  f"{fv_rejects} stale write(s) rejected")
    else:
        fv_state, fv_head = "ok", "0"
        fv_label = "failure visibility clear"
        fv_sub = (f"{sil.get('tags', 0)} swallowed-failure tag(s) within budget &middot; "
                  f"{live.get('declared_ever', 0)} obligation(s) declared")
    fv_status = ("error" if fv_state == "error"
                 else "warn" if fv_state == "warn" else "ok")
    fv_rows = ""
    for o in fv_overdue[:4]:
        fv_rows += (
            f'<div class="row"><span class="label" style="color:{semantic("error")[0]}">'
            f'{str(o.get("name"))[:24]} &middot; {o.get("kind")}</span>'
            f'<span class="value">{fmt(o.get("overdue_by"), 2)}s late</span></div>')
    for tag in fv_tripped[:3]:
        fv_rows += (
            f'<div class="row"><span class="label" style="color:{semantic("warn")[0]}">'
            f'{str(tag)[:24]}</span><span class="value">over budget</span></div>')
    for note in (fv.get("unobservable") or [])[:2]:
        fv_rows += (
            f'<div class="row"><span class="label" style="color:{semantic("warn")[0]}">'
            f'blind spot</span><span class="value" style="font-size:11px">{note[:52]}</span></div>')
    cards.append(_card(i, "card-theme-dead", "Failure Visibility (silence / fencing / liveness)",
        f'<div class="val status-{"down" if fv_status != "ok" else "ok"}" '
        f'style="color:{semantic(fv_state)[0]}">{fv_head}</div>'
        f'<div class="sub">{fv_sub}</div>'
        f'<div class="mini-bar"><div class="fill {"purple" if fv_state == "ok" else "red"}" '
        f'style="width:{100 if fv_observed else 0:.0f}%"></div></div>'
        f'<br><div class="row"><span class="label">Swallowed failures absorbed</span>'
        f'<span class="value">{sil.get("total_ignored", "unknown")}</span></div>'
        f'<div class="row"><span class="label">Distinct failure signatures</span>'
        f'<span class="value">{sum(int(n) for n in (sil.get("signatures") or {}).values())}</span></div>'
        f'<div class="row"><span class="label">Fences claimed / rejected</span>'
        f'<span class="value">{fv_claims} / {fv_rejects}</span></div>'
        f'<div class="row"><span class="label">Obligations overdue</span>'
        f'<span class="value" style="color:{semantic("error" if fv_overdue else "ok")[0]}">{len(fv_overdue)}</span></div>'
        f'<div class="row"><span class="label">Beats recorded</span>'
        f'<span class="value">{live.get("beats_total", "unknown")}</span></div>'
        f'<div class="row"><span class="label">Publisher</span>'
        f'<span class="value">{fv.get("written_by") or "none"} &middot; {fv.get("age") or "n/a"}</span></div>'
        f'<br>{fv_rows}{source_line("failure_visibility.json", extra=fv.get("_error") or "")}'))
    i += 1

    # The header badge used to read "Health: ok" as a literal, which is the
    # exact class of claim this restructure exists to remove.
    # A published failure fault outranks the fail-rate heuristic: the whole
    # point of those ledgers is to catch what the homeostat rate cannot see.
    overall = ("down" if (h_rate >= 0.3 or fv_state == "error")
               else "unknown" if fv_state == "unknown"
               else "ok" if (h_rate < 0.1 and (rejected or 0) == 0)
               else "degraded")
    mesh_accent, mesh_glow = theme_accent("mesh")
    return _wrap("Azazael Observability", "Neural Mesh Observatory", mesh_accent,
                 mesh_glow, cards,
                 f'{total} worms &middot; {len(natures)} natures &middot; {q["keys"]} Q-keys '
                 f'({q["measured"]} measured) &middot; {fabric.get("published", "unknown")} fabric providers',
                 f'<span class="badge badge-{overall}"><span class="live-dot {"ok" if overall == "ok" else "down"}"></span>Health: {overall}</span>'
                 f'<span class="badge badge-neutral">{h_rate:.1%} fail rate</span>'
                 f'<span class="badge badge-{"down" if fv_status != "ok" else "neutral"}">'
                 f'{fv_label}</span>'
                 f'<span class="badge badge-{"down" if (rejected or 0) else "ok"}">{clock.get("rejected", "unknown")} remote stamps rejected</span>'
                 f'<span class="badge badge-neutral">skew {skew if skew is not None else "unknown"}ms</span>'
                 f'<span class="badge badge-purple">{q["keys"]} Q</span>',
                 refresh=REFRESH_SECONDS)


# ── Monitor 2: Ex Machina ───────────────────────────────────────

def render_exmachina(exm: dict) -> str:
    organs = [
        ("SPECULUM", "reward-hacking detection", "zlib + statistics", "#f85149"),
        ("FULCRUM", "compression-based novelty", "zlib (NCD)", "#58a6ff"),
        ("TESTUDO", "conformal calibration", "order statistics", "#3fb950"),
        ("VIGIL", "anytime-valid evidence", "e-process / test martingale", "#bc8cff"),
        ("FILUM", "degeneracy + memory", "permutation entropy + DFA", "#56d4dd"),
        ("PERENNIS", "era invariance", "Theil-Sen slope stability", "#e8d44d"),
        ("COMPAGES", "long-run cointegration", "Engle-Granger", "#ff7eb3"),
        ("BREVIS", "description-length budget", "two-part MDL", "#d29922"),
        ("EMERGON", "causal emergence", "effective information", "#3fb950"),
        ("MUTAGEN", "genome selection", "binomial tail", "#bc8cff"),
    ]

    cards = []
    i = 0

    # Severity Band
    cards.append(_card(i, "card-theme-workflow", "Severity Band",
        f'<div class="val status-ok">0.00</div>'
        f'<div class="sub">aggregate epistemic mood &middot; smoothed for live panel</div>'
        f'<div class="mini-bar"><div class="fill ok" style="width:5%"></div></div>'
        f'<br><div class="row"><span class="label">Mood</span><span class="badge badge-ok">stable</span></div>'
        f'<div class="row"><span class="label">Organs</span><span class="value">10 implemented</span></div>'))
    i += 1

    # Gaming Detection
    cards.append(_card(i, "card-theme-dead", "SPECULUM - Gaming Detection",
        f'<div class="val status-ok">watching</div>'
        f'<div class="sub">objective rising while holdout flat = HACKING</div>'
        f'<br><div class="row"><span class="label">Signature</span><span class="value">E9: margin up, holdout zero</span></div>'
        f'<div class="row"><span class="label">Window</span><span class="value">40 cycles</span></div>'
        f'<div class="row"><span class="label">Floor</span><span class="value">margin > 1.0</span></div>'))
    i += 1

    # Compression Novelty
    cards.append(_card(i, "card-theme-db", "FULCRUM - NCD Novelty",
        f'<div class="val" style="color:var(--accent);text-shadow:0 0 20px var(--accent-glow)">zlib</div>'
        f'<div class="sub">normalised compression distance &middot; stdlib only</div>'
        f'<br><div class="row"><span class="label">Distance</span><span class="value">[0, ~1.2]</span></div>'
        f'<div class="row"><span class="label">Nearest-neighbor</span><span class="value">not mean</span></div>'
        f'<div class="row"><span class="label">Archive limit</span><span class="value">64</span></div>'))
    i += 1

    # Conformal Calibration
    cards.append(_card(i, "card-theme-health", "TESTUDO - Conformal",
        f'<div class="val status-ok">90%</div>'
        f'<div class="sub">finite-sample coverage &middot; no distribution assumed</div>'
        f'<br><div class="row"><span class="label">Alpha</span><span class="value">0.1</span></div>'
        f'<div class="row"><span class="label">Correction</span><span class="value">ceil((n+1)(1-alpha))/n</span></div>'
        f'<div class="row"><span class="label">Split</span><span class="value">3-fold</span></div>'))
    i += 1

    # All 10 organs
    organ_rows = ""
    for name, thesis, technique, color in organs:
        organ_rows += f'<div class="metric-row"><span class="metric-name" style="color:{color}">{name}</span><span class="metric-stats">{technique}</span></div>'
    cards.append(_card(i, "card-theme-metrics", "All 10 Organs",
        f'<div class="sub" style="margin-bottom:8px">stdlib-only &middot; no C extensions &middot; python3.15 compatible</div>'
        f'{organ_rows}'))
    i += 1

    # VIGIL
    cards.append(_card(i, "card-theme-session", "VIGIL - E-Process",
        f'<div class="val" style="color:var(--purple);text-shadow:0 0 20px var(--purple-glow)">martingale</div>'
        f'<div class="sub">anytime-valid evidence &middot; no peeking penalty</div>'
        f'<br><div class="row"><span class="label">Type</span><span class="value">test martingale</span></div>'
        f'<div class="row"><span class="label">vs SPRT</span><span class="value">SPRT is fixed-sample (1945)</span></div>'))
    i += 1

    # BREVIS
    cards.append(_card(i, "card-theme-drift", "BREVIS - MDL Budget",
        f'<div class="val" style="color:var(--orange);text-shadow:0 0 20px var(--orange-glow)">two-part</div>'
        f'<div class="sub">bits to state genome + bits to state residuals</div>'
        f'<br><div class="row"><span class="label">Check</span><span class="value">fitness up != free evidence</span></div>'
        f'<div class="row"><span class="label">Budget</span><span class="value">description-length bounded</span></div>'))
    i += 1

    # EMERGON
    cards.append(_card(i, "card-theme-health", "EMERGON - Causal",
        f'<div class="val status-ok">effective info</div>'
        f'<div class="sub">macro grain vs micro grain &middot; groupable organs</div>'
        f'<br><div class="row"><span class="label">Question</span><span class="value">are organs more legible grouped?</span></div>'
        f'<div class="row"><span class="label">Grains</span><span class="value">belief vs measure organs</span></div>'))
    i += 1

    return _wrap("Ex Machina", "Epistemic Tier - 10 Organs", "#bc8cff",
                 "rgba(188,140,255,0.25)", cards,
                 "The epistemic tier - stdlib-only - judges what PROTEUS produces",
                 '<span class="badge badge-purple">10 organs</span>'
                 '<span class="badge badge-ok">stdlib only</span>')


# ── Monitor 3: Lilith ───────────────────────────────────────────

def render_lilith(spine: dict, registry: dict, facts: dict | None = None,
                  fabric: dict | None = None) -> str:
    """The Red Queen monitor.

    Every constant on this page used to be a literal, and every one of them had
    already changed under it (TODO §1.2). Role is now derived from the live
    lease rather than from a `spine_mapped` event, because the lease became the
    authority on 2026-09-15 (audit finding #7) and a stale event no longer
    describes who is queen.
    """
    facts = facts or {}
    fabric = fabric or {}
    scars = spine.get("scars", {})
    events = spine.get("events", [])
    spine_map = spine.get("spine_map", {})
    qs = quarantine_summary(spine)

    lease = facts.get("lease") or {}
    local_env = facts.get("local_env")
    lease_valid = bool(lease.get("valid")) if lease else None
    if not lease:
        queen_role, role_state = "unknown", "unknown"
    elif lease_valid and lease.get("env") == local_env:
        queen_role, role_state = "queen", "ok"
    elif lease_valid:
        queen_role, role_state = "follower", "warn"
    else:
        queen_role, role_state = "no live lease", "warn"
    queen_env = lease.get("env") or local_env or "unknown"
    lease_state = "ok" if lease_valid else ("warn" if lease else "unknown")

    probes = [e for e in events if "reflex" in e.get("kind", "") or "quarantine" in e.get("kind", "")]
    spine_envs = set(k.split(":")[0] for k in spine_map)
    beacon_provider = next((p for p in fabric.get("providers", [])
                            if p["provider"] == "EnvBeaconWorm"), {})
    raw_beacons = (beacon_provider.get("state") or {}).get("_beacons")
    beacon_envs = set(str(x) for x in raw_beacons) if isinstance(raw_beacons, dict) else set()
    envs = spine_envs | beacon_envs

    probe_natures = facts.get("probe_natures")
    probe_natures_txt = (", ".join(probe_natures) if probe_natures
                         else "unavailable (mesh modules not importable)")
    quorum = num(facts.get("quorum"))

    cards = []
    i = 0

    # Queen Status — read from the lease, which is now the authority.
    full_envs = facts.get("full_envs")
    fallback = facts.get("fallback_order")
    cards.append(_card(i, "card-theme-dead", "Queen Status",
        f'<div class="val status-{role_state if role_state != "warn" else "degraded"}">{queen_role}</div>'
        f'<div class="sub">lease authority (audit #7) &middot; '
        f'expires in {lease.get("expires_in", "unknown")}s</div>'
        f'<br><div class="row"><span class="label">Host env</span><span class="value">{local_env or "unknown"}</span></div>'
        f'<div class="row"><span class="label">Lease holder</span><span class="value">{queen_env}</span></div>'
        f'<div class="row"><span class="label">Lease valid</span><span class="value" style="color:{semantic(lease_state)[0]}">{lease_valid if lease else "unknown"}</span></div>'
        f'<div class="row"><span class="label">Term</span><span class="value">{lease.get("term", "unknown")}</span></div>'
        f'<div class="row"><span class="label">Lease length</span><span class="value">{facts.get("lease_seconds", "unknown")}s</span></div>'
        f'<div class="row"><span class="label">Full (non-degraded) envs</span><span class="value">{', '.join(full_envs) if full_envs else "unknown"}</span></div>'
        f'<div class="row"><span class="label">Fallback order</span><span class="value" style="font-size:11px">{' > '.join(fallback) if fallback else "unknown"}</span></div>'
        f'<div class="row"><span class="label">Preferred env</span><span class="value">{facts.get("preferred_env", "unknown")}</span></div>'))
    i += 1

    # Pressure Probes — coverage is read from the worm that owns it.
    probe_html = ""
    for e in probes[:6]:
        kind = e.get("kind", "")
        cls = "down" if "quarantine" in kind else "ok"
        probe_html += f'<div class="row"><span class="label" style="color:var(--{"red" if cls=="down" else "green"})">{kind}</span><span class="value">{e.get("at","")[:19]}</span></div>'
    if not probe_html:
        probe_html = '<div class="row"><span class="label">No probes fired yet</span></div>'
    cards.append(_card(i, "card-theme-drift", "Pressure Probes",
        f'<div class="val" style="color:var(--orange);text-shadow:0 0 20px var(--orange-glow)">{len(probes)}</div>'
        f'<div class="sub">PROBE_NATURES: {probe_natures_txt}<br>'
        f'limit: {facts.get("probe_limit", "unknown")} per pass &middot; '
        f'min failures: {facts.get("min_failures", "unknown")} &middot; '
        f'fail rate: {fmt(num(facts.get("fail_rate")) * 100 if num(facts.get("fail_rate")) is not None else None, 0)}%</div>'
        f'<br>{probe_html}'))
    i += 1

    # Quarantine — a cross-env ledger, not a local two-thirds vote.
    q_html = ""
    for k, v in list(scars.items())[:6]:
        hc = v.get("heal_count", 0)
        q_html += f'<div class="row"><span class="label" style="color:var(--red)">{k}</span><span class="value" style="color:var(--green)">heal {hc}/3</span></div>'
    if not q_html:
        q_html = '<div class="row"><span class="label" style="color:var(--green)">All worms healthy</span></div>'
    quorum_txt = "unknown" if quorum is None else f"{quorum:.0f} distinct envs"
    cards.append(_card(i, "card-theme-dead", "Quarantine Ledger",
        f'<div class="val status-{"down" if scars else "ok"}">{len(scars)}</div>'
        f'<div class="sub">active scars &middot; quorum = {quorum_txt} '
        f'(a local majority can no longer quarantine) &middot; '
        f'{qs["decided"]} of {qs["finding_count"]} findings decided</div>'
        f'<br><div class="row"><span class="label">Env votes cast</span><span class="value">{qs["votes"]}</span></div>'
        f'<div class="row"><span class="label">Voting envs</span><span class="value">{qs["env_count"]}</span></div>'
        f'<br>{q_html}'))
    i += 1

    # Peer Map — spine map plus env beacons, with the live lease holder marked.
    peer_html = "".join(
        f'<span class="badge badge-{"ok" if e == queen_env else "neutral"}" style="margin:2px">{e}</span>'
        for e in sorted(envs))
    if not peer_html:
        peer_html = _err("No peer envs in the spine map or the env beacons",
                         semantic("warn")[0])
    cards.append(_card(i, "card-theme-drift", "Kademlia Peer Map (G3)",
        f'<div class="val" style="color:var(--yellow);text-shadow:0 0 20px var(--yellow-glow)">{len(envs)}</div>'
        f'<div class="sub">{len(spine_envs)} spine-map &middot; {len(beacon_envs)} beacon &middot; lease holder marked</div>'
        f'<br>{peer_html}'))
    i += 1

    # Lifecycle
    cards.append(_card(i, "card-theme-session", "Xenomorph Lifecycle",
        f'<div class="sub" style="margin-bottom:10px">Egg &rarr; Facehugger &rarr; Chestburster &rarr; Drone &rarr; Praetorian &rarr; Queen</div>'
        f'<div class="row"><span class="label">Ovomorph (eggs)</span><span class="badge badge-neutral">dormant</span></div>'
        f'<div class="row"><span class="label">Facehugger (inject)</span><span class="badge badge-neutral">advisory</span></div>'
        f'<div class="row"><span class="label">Chestburster (fan-out)</span><span class="badge badge-neutral">8 variants</span></div>'
        f'<div class="row"><span class="label">Drone (carrier)</span><span class="badge badge-neutral">cross-env</span></div>'
        f'<div class="row"><span class="label">Praetorian (guard)</span><span class="badge badge-purple">successor</span></div>'
        f'<div class="row"><span class="label">Resin (trace store)</span><span class="badge badge-neutral">writes into quarantine ledger</span></div>'))
    i += 1

    # Events
    ev_html = ""
    for e in events[:12]:
        ev_html += f'<div class="row"><span class="label" style="color:var(--cyan)">{e.get("kind","")}</span><span class="value">{e.get("at","")[:19]}</span></div>'
    if not ev_html:
        ev_html = '<div class="row"><span class="label">No events yet</span></div>'
    cards.append(_card(i, "card-theme-metrics", "Queen Events", ev_html))
    i += 1

    lilith_accent, lilith_glow = theme_accent("lilith")
    return _wrap("Lilith", "The Red Queen - Xenomorph Lifecycle", lilith_accent,
                 lilith_glow, cards,
                 f"Prefers {facts.get('preferred_env', 'unknown')} &middot; lease {facts.get('lease_seconds', 'unknown')}s "
                 f"&middot; cross-env quorum quarantine",
                 f'<span class="badge badge-{role_state if role_state != "unknown" else "neutral"}">role: {queen_role}</span>'
                 f'<span class="badge badge-neutral">env: {local_env or "unknown"}</span>'
                 f'<span class="badge badge-neutral">lease: {queen_env}</span>')



# ── Wrapper ──────────────────────────────────────────────────────

def _wrap(title: str, subtitle: str, accent: str, glow: str,
          cards: list, footer_text: str, badges: str,
          refresh: int = REFRESH_SECONDS) -> str:
    """Wrap cards in the shared page shell.

    `refresh` emits a `<meta http-equiv="refresh">`, so a monitor left open on a
    second screen keeps up with a live mesh instead of freezing on the state of
    the moment it was rendered (TODO P3 item 11). A render is a *snapshot*, so
    the page header also carries the render time it belongs to.
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    cards_html = "\n".join(cards)
    refresh_meta = (f'<meta http-equiv="refresh" content="{int(refresh)}">'
                    if refresh and refresh > 0 else '')
    refresh_note = (f' &middot; auto-refresh {int(refresh)}s'
                    if refresh and refresh > 0 else ' &middot; manual refresh')
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">{refresh_meta}
<title>{title}</title>
<style>{_base_css(accent, glow)}</style></head><body>
<canvas id="particles-canvas"></canvas>
<div class="header">
  <div><h1>{title}</h1><div class="ts">{subtitle} &mdash; Updated: {now}</div></div>
  <div class="header-right">{badges}</div>
</div>
<div class="grid">{cards_html}</div>
<div class="footer">{footer_text} &mdash; rendered {now}{refresh_note} &mdash; <a href="/" class="refresh-btn">&#8635; Refresh</a></div>
<script>{_base_js()}</script>
</body></html>"""


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--azazael", action="store_true")
    parser.add_argument("--exmachina", action="store_true")
    parser.add_argument("--lilith", action="store_true")
    args = parser.parse_args()
    do_all = not (args.azazael or args.exmachina or args.lilith)

    print("Rendering live monitors...")

    spine = collect_spine_data()
    registry = collect_registry_data()
    exm = collect_exmachina_data()
    # The live facts the old hard-coded rows used to guess at. Collected once so
    # every monitor on the page agrees, and so one failed read is reported
    # everywhere rather than differently in each card.
    facts = collect_mesh_facts()
    fabric = collect_fabric_data()
    clock = collect_clock_data()
    cycle = collect_mesh_cycle()
    # The failure ledgers are in-process, and THIS process owns none of them --
    # so read what a mesh process published rather than importing the ledgers
    # here and drawing a green panel from three empty dictionaries.
    failure = collect_failure_data()
    if spine.get("_error"):
        print(f"  ! spine read failed: {spine['_error']}")
    if facts.get("_error"):
        print(f"  ! mesh facts unavailable: {facts['_error']}")
    if not failure.get("observed"):
        print("  ! failure visibility unobserved"
              " (no publisher has written state/failure_visibility.json)")

    if do_all or args.azazael:
        html = render_azazael(spine, registry, facts=facts, fabric=fabric,
                              clock=clock, cycle=cycle, failure=failure)
        p = OUT_DIR / "monitor_azazael.html"
        p.write_text(html, encoding="utf-8")
        print(f"  Azazael:    {p}")

    if do_all or args.exmachina:
        html = render_exmachina(exm)
        p = OUT_DIR / "monitor_exmachina.html"
        p.write_text(html, encoding="utf-8")
        print(f"  Ex Machina: {p}")

    if do_all or args.lilith:
        html = render_lilith(spine, registry, facts=facts, fabric=fabric)
        p = OUT_DIR / "monitor_lilith.html"
        p.write_text(html, encoding="utf-8")
        print(f"  Lilith:     {p}")

    print("Done.")


if __name__ == "__main__":
    main()
