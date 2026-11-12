#!/usr/bin/env python3
"""live_monitor_web.py — serve the Azazael live monitor as a webpage.

Serves the same data as the console dashboard over HTTP so you can open it
from any device on the LAN (phone, tablet, second monitor).

Endpoints:
  /       auto-refreshing dark-themed HTML page (no external assets)
  /api    JSON snapshot of the current dashboard data

Stdlib only. Runs on Windows Python 3.14 (or any Python >= 3.9).

Usage:
  python scripts/live_monitor_web.py [--port 8765] [--refresh 30]

Open http://<this-pc-lan-ip>:8765 from another device. If the firewall
blocks it, allow python.exe inbound once (private networks).
"""
import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import track_monitor as lm  # noqa: E402  (reengineered 2026-10-03;
    # rotation comes from track_registry. Archive: live_monitor.py retired
    # for carrying a drifted track list; see archive/live-monitors-20261002/)
except ImportError:
    lm = None  # page renders standby (collect_snapshot returns None)

REFRESH = 30

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AZAZAEL Live Monitor</title>
<style>
  :root { color-scheme: dark; }
  body { background:#0d1117; color:#c9d1d9; font-family:Consolas,monospace; margin:0; padding:20px; }
  h1 { color:#e6edf3; font-size:18px; margin:0 0 4px; }
  .meta { color:#8b949e; font-size:12px; margin-bottom:14px; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px 16px; margin-bottom:12px; }
  .card h2 { color:#58a6ff; font-size:13px; text-transform:uppercase; letter-spacing:1px; margin:0 0 10px; }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  td, th { padding:3px 10px 3px 0; text-align:left; }
  .ok  { color:#3fb950; font-weight:bold; }
  .bad { color:#f85149; font-weight:bold; animation:blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity:0.2; } }
  .fresh { font-size:13px; }
  .fresh td { padding:5px 12px 5px 0; }
  .spark { font-size:16px; letter-spacing:1px; color:#58a6ff; white-space:pre; }
  .num { color:#ffd700; }
  .up { color:#3fb950; } .down { color:#f85149; }
  #status { color:#8b949e; font-size:12px; }
</style>
</head>
<body>
  <h1>AZAZAEL LIVE MONITOR</h1>
  <div class="meta">auto-refresh <span id="refresh">30</span>s &middot; updated <span id="updated">-</span> &middot; <span id="status">connecting&hellip;</span></div>

  <div class="card"><h2>Cycle freshness</h2>
    <table class="fresh" id="fresh"></table>
  </div>

  <div class="card"><h2>What it means</h2><div id="means"></div></div>

  <div class="card"><h2>Cycle freshness</h2>
    <table class="fresh" id="fresh"></table>
  </div>

  <div class="card"><h2>Evolution</h2>
    <div id="evo"></div>
  </div>

  <div class="card"><h2>Steering</h2><div id="steer"></div></div>

  <div class="card"><h2>Activity (last 60 min)</h2><div id="act"></div></div>

<script>
const REFRESH = %REFRESH%;
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function fmtAge(a){ return a==null ? '-' : a.toFixed(1)+' min'; }

function render(d){
  document.getElementById('updated').textContent = new Date().toLocaleTimeString();
  // plain-English interpretation (served by the API)
  let mn = '';
  if (d.means && d.means.length){
    for (const m of d.means){
      const col = {OK:'#3fb950',WARN:'#f85149',EVO:'#58a6ff',STEER:'#ffd700',ACT:'#58a6ff'}[m.tag] || '#c9d1d9';
      mn += `<div style="margin:3px 0"><span style="color:${col};font-weight:bold">${esc(m.tag)}</span> ${esc(m.msg)}</div>`;
    }
    if (d.bottom_line) mn += `<div style="margin-top:8px;color:#e6edf3"><b>BOTTOM LINE:</b> ${esc(d.bottom_line)}</div>`;
  } else {
    mn = '<div>collecting…</div>';
  }
  document.getElementById('means').innerHTML = mn;

  let fh = '';
  for (const f of d.freshness){
    const cls = f.ok ? 'ok' : 'bad';
    fh += `<tr><td>${esc(f.label)}</td><td>${f.ts?esc(f.ts.slice(11,19))+'Z':'-'}</td><td class="${cls}">${cls==='ok'?'OK':fmtAge(f.age)+' STALE'}</td></tr>`;
  }
  document.getElementById('fresh').innerHTML = fh;

  let ev = '';
  if (d.evo && d.evo.length){
    const e0 = d.evo[0];
    const traj = e0.traj.map(x=>x.toFixed(2)).join(' &rarr; ');
    const flat = new Set(e0.traj.map(x=>x.toFixed(3))).size <= 1;
    ev += `<table>
      <tr><th>latest best</th><td class="num">${e0.best.toFixed(3)}</td><th>non-flat</th><td>${flat?'no':'<span class="up">yes</span>'}</td></tr>
      <tr><th>lr</th><td>${e0.genes.learning_rate?.toFixed(4) ?? '-'}</td><th>eps</th><td>${e0.genes.epsilon?.toFixed(4) ?? '-'}</td><th>disc</th><td>${e0.genes.discount?.toFixed(4) ?? '-'}</td></tr>
      <tr><th>trajectory</th><td colspan="5">${traj}</td></tr>
    </table>`;
    ev += '<h3 style="font-size:12px;color:#8b949e;margin:10px 0 4px">Last 5 runs</h3><table>';
    for (const r of d.evo.slice(0,5)){
      const f2 = new Set(r.traj.map(x=>x.toFixed(3))).size <= 1;
      ev += `<tr><td>${esc(r.ts.slice(11,16))}Z</td><td class="num">${r.best.toFixed(2)}</td><td>gens=${r.traj.length}</td><td>eps=${r.genes.epsilon?.toFixed(3) ?? '-'}</td><td>${f2?'flat':'<span class="up">rising</span>'}</td></tr>`;
    }
    ev += '</table>';
    const bests = d.evo.map(r=>r.best).reverse();
    const sorted = [...bests].sort((a,b)=>a-b);
    const lo = sorted[Math.floor(sorted.length*0.1)] ?? sorted[0];
    const hi = sorted[Math.floor(sorted.length*0.9)] ?? sorted[sorted.length-1];
    const cells = bests.map(v => {
      const lv = Math.round((v-lo)/((hi-lo)||1)*8);
      return ' .:-=+*#%'[Math.max(0,Math.min(8,lv))];
    }).join('');
    ev += `<h3 style="font-size:12px;color:#8b949e;margin:10px 0 4px">Climb (${bests.length} runs, oldest left)</h3>
           <div class="spark">${esc(cells)}</div>
           <div style="font-size:12px;color:#8b949e">min ${lo.toFixed(2)} &middot; max ${hi.toFixed(2)}</div>`;
  } else {
    ev = '<div>no evolution runs yet</div>';
  }
  document.getElementById('evo').innerHTML = ev;

  const st = d.strategy || {};
  document.getElementById('steer').innerHTML =
    `<table><tr><th>active</th><td class="num">${esc(st.value)}</td><th>at</th><td>${st.ts?esc(st.ts.slice(11,19))+'Z':'-'}</td><th>events 24h</th><td class="num">${d.steer_24h}</td></tr></table>`;

  const a = d.activity || {};
  document.getElementById('act').innerHTML =
    `<table><tr><th>dreams</th><td class="num">${a.dreams}</td><th>avg quality</th><td class="num">${a.quality}</td></tr>
     <tr><th>training</th><td class="num">${a.training}</td><th>adaptations</th><td class="num">${a.adaptations}</td></tr>
     <tr><th>policy apps</th><td class="num">${a.policy_applied}</td></tr></table>`;
}

async function tick(){
  try {
    const r = await fetch('/api', {cache:'no-store'});
    const d = await r.json();
    document.getElementById('status').textContent = 'live';
    render(d);
  } catch(e) {
    document.getElementById('status').textContent = 'offline: '+e;
  }
}
tick();
setInterval(tick, REFRESH*1000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep console quiet

    def do_GET(self):
        if self.path.startswith("/api"):
            # Served through the shared cache: browsers poll this endpoint, and
            # opening a fresh SQLite connection plus a full collect per request
            # meant N open tabs drove N full collections per interval.
            data = snapshot_cached()
            if data is None:
                self._send(503, b'{"error":"db unavailable"}', "application/json")
                return
            self._send(200, json.dumps(data).encode(), "application/json")
        else:
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


#: One-snapshot reuse for the LAN page. A browser tab polls /api on a timer,
#: so re-collecting per request multiplied DB work by the number of open tabs.
#: Held under a short TTL: the page stays live, the DB stops being stampeded.
_CACHE = {"at": 0.0, "data": None}


def collect_snapshot():
    """Build one full dashboard payload, or None when the DB is unavailable.

    Kept as a module-level function (not inlined into the handler) so it can
    be substituted in tests and reused by any other entry point.
    """
    if lm is None:
        return None  # monitor archived 2026-10-02; page renders standby
    con = lm.db_connect()
    if con is None:
        return None
    try:
        data = lm.collect(con)
        data["means"] = [{"tag": t, "msg": m} for t, m in lm.explain(data)]
        data["bottom_line"] = lm.bottom_line(data)
        data["server_time"] = datetime.now().isoformat()
        return data
    finally:
        con.close()


def snapshot_cached(ttl=2.0, force=False):
    """Return a snapshot, reusing the last one while it is younger than `ttl`.

    `collect_snapshot` is looked up as a module global on every call, so a
    test (or an operator) can substitute it and have the substitution take
    effect -- the cache must not freeze a binding it captured at import time.
    """
    now = time.monotonic()
    cached = _CACHE.get("data")
    if (not force and cached is not None
            and (now - float(_CACHE.get("at") or 0.0)) < ttl):
        return cached
    data = collect_snapshot()
    if data is not None:
        _CACHE["at"] = now
        _CACHE["data"] = data
    return data


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    global REFRESH
    ap = argparse.ArgumentParser(description="Serve the Azazael live monitor on the LAN")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--refresh", type=int, default=30)
    args = ap.parse_args()
    REFRESH = max(5, args.refresh)

    global PAGE
    PAGE = PAGE.replace("%REFRESH%", str(REFRESH))

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    ip = lan_ip()
    print(f"AZAZAEL Live Monitor web: http://{ip}:{args.port}  (Ctrl+C to stop)")
    print(f"  open from another device: http://{ip}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())