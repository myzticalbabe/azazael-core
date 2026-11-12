# coding: utf-8
"""embedding_dashboard.py — Live embedding/dimension/memory dashboard.

Serves a single-page dashboard on http://127.0.0.1:8765 that auto-refreshes
in place (no page reload, no scroll — everything fits one screen).

    python scripts/embedding_dashboard.py [--port 8765]

Endpoints:
    /            the dashboard HTML
    /api/stats   live JSON snapshot (backend, dim sweep, cache, store)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from CORE.embedding_backend import health  # noqa: E402
from CORE.pattern_memory import query_patterns  # noqa: E402

PORT = 8765


def _store_stats() -> dict:
    db = Path.home() / ".azazael" / "memory_store.db"
    if not db.exists():
        return {"error": "store missing"}
    try:
        con = sqlite3.connect(str(db), timeout=5)
        con.row_factory = sqlite3.Row
        k = con.execute("SELECT COUNT(*) c FROM knowledge_patterns").fetchone()["c"]
        ke = con.execute(
            "SELECT COUNT(*) c FROM knowledge_patterns WHERE embedding != '[]'"
        ).fetchone()["c"]
        a = con.execute("SELECT COUNT(*) c FROM agent_patterns").fetchone()["c"]
        ae = con.execute(
            "SELECT COUNT(*) c FROM agent_patterns WHERE embedding != '[]'"
        ).fetchone()["c"]
        events = con.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        traces = con.execute(
            "SELECT COUNT(*) c FROM knowledge_patterns WHERE pattern_type='code_repair_reasoning'"
        ).fetchone()["c"]
        con.close()
        return {"knowledge": k, "knowledge_embedded": ke,
                "agent": a, "agent_embedded": ae,
                "events": events, "reasoning_traces": traces}
    except Exception as e:
        return {"error": str(e)}


def _retrieval_probe() -> dict:
    """Live retrieval quality probe: the docstring-trap query must hit TR-03."""
    hits = query_patterns(
        "pyflakes says name undefined but grep shows import inside docstring",
        top_k=4,
    )
    out = []
    for h in hits:
        m = h.get("metadata", {})
        out.append({"id": h["id"], "pattern_type": h["pattern_type"],
                    "score": h["score"],
                    "trace": m.get("trace_id", "-"),
                    "problem": str(m.get("problem", ""))[:80]})
    return {"hits": out, "ts": time.time()}


def _build_stats() -> dict:
    h = health()
    opt = h.get("dim_optimization", {})
    return {
        "backend": {
            "active": h.get("active_backend"),
            "ollama_reachable": h.get("ollama_reachable"),
            "llama_server_reachable": h.get("llama_server_reachable"),
            "canonical_dim": h.get("canonical_dim"),
            "optimal_dim": h.get("optimal_dim"),
            "optimal_model": h.get("optimal_model"),
            "last_used": h.get("last_used_backend"),
            "model": h.get("model"),
        },
        "models": h.get("models", {}),
        "dim_sweep": {
            "scores": opt.get("scores", {}),
            "models": opt.get("models", {}),
            "pulled": opt.get("pulled_models", []),
            "best_dim": opt.get("best_dim"),
            "best_model": opt.get("best_model"),
            "corpus_size": opt.get("corpus_size"),
            "evaluated_at": opt.get("evaluated_at"),
        },
        "cache": h.get("cache", {}),
        "store": _store_stats(),
        "probe": _retrieval_probe(),
        "ts": time.time(),
    }


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Azazael — Embedding Intelligence</title>
<style>
  :root{--bg:#0b0e14;--panel:#12161f;--panel2:#171c28;--line:#232a3a;
        --txt:#dbe2f0;--dim:#7a8699;--acc:#4fd1c5;--good:#4ade80;--bad:#f87171;
        --warn:#fbbf24;--mono:'Cascadia Code','Consolas',monospace}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);font:13px/1.45 'Segoe UI',system-ui,sans-serif;
       padding:10px;height:100vh;overflow:hidden}
  .grid{display:grid;gap:10px;height:100%;
        grid-template-columns:340px 1fr 1fr;
        grid-template-rows:auto auto 1fr;
        grid-template-areas:"head head head" "be side side" "cache side side"}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
  #head{grid-area:head;display:flex;align-items:center;justify-content:space-between}
  #head h1{font-size:17px;letter-spacing:.5px}
  #head h1 b{color:var(--acc)}
  .live{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:12px;color:var(--dim)}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--good);
       animation:pulse 1.6s infinite}
  @keyframes pulse{50%{opacity:.35}}
  #be{grid-area:be}
  #side{grid-area:side;display:flex;flex-direction:column;gap:10px;overflow:hidden}
  #cache{grid-area:cache}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:var(--dim);
     margin-bottom:8px;border-bottom:1px solid var(--line);padding-bottom:5px}
  .kv{display:flex;justify-content:space-between;padding:3px 0;font-family:var(--mono);font-size:12.5px}
  .kv .k{color:var(--dim)} .kv .v{color:var(--txt)}
  .kv .v.ok{color:var(--good)} .kv .v.bad{color:var(--bad)} .kv .v.warn{color:var(--warn)}
  .kv .v.acc{color:var(--acc);font-weight:600}
  table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px}
  th{color:var(--dim);text-align:left;font-weight:500;padding:3px 6px;border-bottom:1px solid var(--line)}
  td{padding:3px 6px;border-bottom:1px solid rgba(35,42,58,.5)}
  tr.best td{background:rgba(79,209,197,.08);color:var(--acc)}
  .bar{display:inline-block;height:9px;border-radius:4px;background:linear-gradient(90deg,#2dd4bf,#4ade80);
       vertical-align:middle;min-width:2px}
  .bar.best{background:linear-gradient(90deg,#fbbf24,#f87171)}
  .chip{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10.5px;font-family:var(--mono);
        background:var(--panel2);border:1px solid var(--line);color:var(--dim)}
  .chip.ok{color:var(--good);border-color:rgba(74,222,128,.35)}
  .chip.bad{color:var(--bad);border-color:rgba(248,113,113,.35)}
  #probe{margin-top:auto}
  .probe-row{display:flex;justify-content:space-between;font-family:var(--mono);font-size:11.5px;
             padding:2px 0;color:var(--dim)}
  .probe-row.top{color:var(--acc);font-weight:600}
  #err{color:var(--bad);font-family:var(--mono);font-size:12px}
  .sub{color:var(--dim);font-size:11px}
</style>
</head>
<body>
<div class="grid">
  <div class="panel" id="head">
    <h1>AZAZAEL <b>EMBEDDING INTELLIGENCE</b></h1>
    <div class="live"><span class="dot"></span>LIVE · refresh <span id="secs">3s</span> · <span id="clock">--:--:--</span></div>
  </div>

  <div class="panel" id="be">
    <h2>Backend</h2>
    <div id="be-body">loading…</div>
  </div>

  <div class="panel" id="side">
    <div id="dimwrap">
      <h2>Dimension Sweep · self-tuned</h2>
      <div id="dim-body"><table><thead><tr><th>dims</th><th>MRR@5</th><th>model</th><th>score</th></tr></thead><tbody id="dim-rows"></tbody></table></div>
    </div>
    <div id="probe">
      <h2>Retrieval Probe · live</h2>
      <div id="probe-body">…</div>
    </div>
  </div>

  <div class="panel" id="cache">
    <h2>Cache &amp; Store</h2>
    <div id="cache-body">…</div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
let secs=3, timer=null;
function fmt(n){return n==null?'–':n.toLocaleString?n.toLocaleString():n}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}

function render(d){
  const b=d.backend;
  // backend panel
  const ok=b.ollama_reachable;
  let h=`<div class="kv"><span class="k">active backend</span><span class="v ${ok?'ok':'bad'}">${esc(b.active)}</span></div>
         <div class="kv"><span class="k">ollama</span><span class="v ${ok?'ok':'bad'}">${ok?'reachable':'DOWN'}</span></div>
         <div class="kv"><span class="k">llama-server</span><span class="v ${b.llama_server_reachable?'ok':'dim'}">${b.llama_server_reachable?'up':'down'}</span></div>
         <div class="kv"><span class="k">optimal model</span><span class="v acc">${esc(b.optimal_model)}</span></div>
         <div class="kv"><span class="k">optimal dim</span><span class="v acc">${b.optimal_dim}</span></div>
         <div class="kv"><span class="k">native dim</span><span class="v">${b.canonical_dim}</span></div>
         <div class="kv"><span class="k">last used</span><span class="v">${esc(b.last_used)}</span></div>
         <div style="margin-top:8px">`;
  for(const [m,info] of Object.entries(d.models||{})){
    const r=info.reachable?'ok':'bad';
    h+=`<div class="kv"><span class="k"><span class="chip ${r}">${esc(m)}</span></span>
         <span class="v ${r}">${info.native_dim}d ${r==='ok'?'●':'○'}</span></div>`;
  }
  h+=`</div>`;
  $('be-body').innerHTML=h;

  // dim sweep
  const sw=d.dim_sweep;
  if(sw && sw.scores && Object.keys(sw.scores).length){
    const rows=Object.entries(sw.scores)
      .map(([k,v])=>[parseInt(k),v]).sort((a,b)=>a[0]-b[0]);
    const max=Math.max(0.0001,...rows.map(r=>r[1]));
    let t='';
    for(const [k,v] of rows){
      const best=sw.best_dim===k;
      const pct=(v/max*100).toFixed(0);
      t+=`<tr class="${best?'best':''}"><td>${k}</td><td>${v.toFixed(4)}</td>
           <td class="sub">${esc((sw.models||{})[k]||'-')}</td>
           <td><span class="bar ${best?'best':''}" style="width:${Math.max(2,pct)}%"></span></td></tr>`;
    }
    $('dim-rows').innerHTML=t;
  } else {
    $('dim-rows').innerHTML=`<tr><td colspan=4 class="sub">no sweep yet — run optimize_dimension()</td></tr>`;
  }

  // cache + store
  const c=d.cache||{}, s=d.store||{};
  const byB=c.by_backend||{};
  $('cache-body').innerHTML=`
    <div class="kv"><span class="k">cache path</span><span class="v">${esc(c.cache_path||'')}</span></div>
    <div class="kv"><span class="k">vectors cached</span><span class="v">${fmt(c.cached)}</span></div>
    <div class="kv"><span class="k">by backend</span><span class="v">${Object.entries(byB).map(([k,v])=>esc(k)+':'+v).join(' · ')||'–'}</span></div>
    <div style="height:8px"></div>
    <div class="kv"><span class="k">knowledge patterns</span><span class="v">${fmt(s.knowledge)}</span></div>
    <div class="kv"><span class="k">… embedded</span><span class="v">${fmt(s.knowledge_embedded)}</span></div>
    <div class="kv"><span class="k">agent patterns</span><span class="v">${fmt(s.agent)}</span></div>
    <div class="kv"><span class="k">reasoning traces</span><span class="v acc">${fmt(s.reasoning_traces)}</span></div>
    <div class="kv"><span class="k">store events</span><span class="v">${fmt(s.events)}</span></div>
    <div class="kv"><span class="k">sweep corpus</span><span class="v">${fmt(sw.corpus_size)} docs</span></div>
    <div class="kv"><span class="k">pulled models</span><span class="v">${esc((sw.pulled||[]).join(', ')||'–')}</span></div>`;

  // probe
  const pr=d.probe||{};
  if(pr.hits){
    $('probe-body').innerHTML=pr.hits.map((x,i)=>
      `<div class="probe-row ${i===0?'top':''}"><span>#${i+1} ${esc(x.trace)} · ${esc(x.pattern_type)}</span>
       <span>${x.score.toFixed(4)}</span></div>`).join('');
  } else $('probe-body').textContent='no probe';
}

async function tick(){
  try{
    const r=await fetch('/api/stats',{cache:'no-store'});
    const d=await r.json();
    if(d.error){$('err') && ($('err').textContent=d.error); return}
    render(d);
    const t=new Date(d.ts*1000);
    $('clock').textContent=t.toTimeString().slice(0,8);
  }catch(e){ /* server restarting */ }
}
tick();
timer=setInterval(tick,3000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/stats"):
            try:
                payload = json.dumps(_build_stats()).encode()
            except Exception as e:  # noqa: BLE001
                payload = json.dumps({"error": str(e)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"embedding dashboard: http://127.0.0.1:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()