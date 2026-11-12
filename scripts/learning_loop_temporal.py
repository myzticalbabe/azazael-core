#!/usr/bin/env python3
"""learning_loop_temporal.py — animate how the learning loop evolves hour by hour.

Buckets the live memory DB's learning-loop events (dreams, steering, policy,
adaptation, state saves, metacog cycles, evolution results, training) by hour
over the last 24h, builds a per-hour weighted graph of the learning-loop domain,
and writes:

  <out>/frames/hour_{HH}.html      one PyVis interactive frame per hour
  <out>/timeline.html              slideshow viewer (prev/next/play/slider)
  <out>/hourly_counts.json         per-hour event counts for analysis

Node set matches learning_loop_graph.py (modules, events, strategies, actions).
Edge weights per hour = event counts in that hour + steering/adaptation flips.

Usage:
  AZAZAEL_MEMORY_DIR=C:\\Users\\clint\\.azazael python scripts/learning_loop_temporal.py
  python3 scripts/learning_loop_temporal.py --hours 24 --no-db
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import os as _os
import shutil

try:
    import networkx as nx
except ImportError:
    sys.exit("networkx required")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))


def _ensure_gil():
    """Free-threaded 3.14t builds have a flaky sqlite3; re-exec with GIL on."""
    import sysconfig as _sc
    if (_sc.get_config_var("Py_GIL_DISABLED")
            and _os.environ.get("AZAZAEL_GIL_ENFORCED") != "1"):
        _os.environ["AZAZAEL_GIL_ENFORCED"] = "1"
        _os.execv(sys.executable, [sys.executable, "-X", "gil=1"] + sys.argv)


_ensure_gil()

MODULES = [
    ("dreaming", "dreaming.py", "module"), ("meta_cognition", "meta_cognition.py", "module"),
    ("learning", "learning.py", "module"), ("evolution", "evolution.py", "module"),
    ("bridge", "dream_policy_bridge.py", "module"), ("local_memory", "local_memory.py", "module"),
    ("dream_cycle", "dream_cycle.py", "module"), ("metacog_cycle", "metacog_cycle.py", "module"),
    ("evolution_cycle", "evolution_cycle.py", "module"),
]
EVENTS = [
    ("ev_dream", "dream_generated"), ("ev_steered", "strategy_steered"),
    ("ev_policy", "policy_applied"), ("ev_adapt", "strategy_adaptation"),
    ("ev_state", "state_saved"), ("ev_mccycle", "mc_cycle"),
    ("ev_evoresult", "evolution_result"), ("ev_train", "training_complete"),
    ("ev_qtbl", "q_table_persisted"),
]
STRATEGIES = [("st_%s" % s, s) for s in
              ("default", "conservative", "defensive", "aggressive", "adaptive",
               "batched", "staged")]
ACTIONS = [("ac_ignore", "ignore"), ("ac_log", "log"), ("ac_alert", "alert"),
           ("ac_remediate", "remediate")]
# (producer_module, event) — weight source per hour
PRODUCES = [("dreaming", "ev_dream"), ("dreaming", "ev_steered"),
            ("bridge", "ev_policy"), ("meta_cognition", "ev_adapt"),
            ("meta_cognition", "ev_state"), ("metacog_cycle", "ev_mccycle"),
            ("evolution_cycle", "ev_evoresult"), ("learning", "ev_train"),
            ("learning", "ev_qtbl")]
EVENT_NAME = {eid: name for eid, name in EVENTS}
EVENT_ID = {name: eid for eid, name in EVENTS}
STRAT_ID = {name: sid for sid, name in STRATEGIES}
EVENT_COLOR = {"ev_dream": "#3fb950", "ev_steered": "#ff8844", "ev_policy": "#d29922",
               "ev_adapt": "#f778ba", "ev_state": "#8b949e", "ev_mccycle": "#58a6ff",
               "ev_evoresult": "#a371f7", "ev_train": "#39c5cf", "ev_qtbl": "#39c5cf"}
NODE_COLOR = dict(MODULES and [(m, "#3fb950") for m, _, _ in MODULES])
NODE_COLOR.update(EVENT_COLOR)
NODE_COLOR.update({sid: "#ff8844" for sid, _ in STRATEGIES})
NODE_COLOR.update({aid: "#aa66ff" for aid, _ in ACTIONS})
NODE_COLOR["local_memory"] = "#7ee787"
NODE_COLOR["bridge"] = "#79c0ff"


def build_static():
    nodes = []
    for nid, label, ntype in ([(m, lb, "module") for m, lb, _ in MODULES]
                              + [(eid, lb, "event") for eid, lb in EVENTS]
                              + [(sid, lb, "strategy") for sid, lb in STRATEGIES]
                              + [(aid, lb, "action") for aid, lb in ACTIONS]):
        nodes.append({"id": nid, "label": label, "type": ntype})
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    G = nx.Graph()
    for i, n in enumerate(nodes):
        G.add_node(i, id=n["id"], label=n["label"], type=n["type"])
    static = [("dreaming", "local_memory"), ("dreaming", "bridge"),
              ("dreaming", "meta_cognition"), ("meta_cognition", "local_memory"),
              ("learning", "local_memory"), ("bridge", "meta_cognition"),
              ("dream_cycle", "dreaming"), ("metacog_cycle", "meta_cognition"),
              ("evolution_cycle", "evolution"), ("evolution_cycle", "learning")]
    for s, t in static:
        G.add_edge(idx[s], idx[t], weight=0.0)
    for prod, eid in PRODUCES:
        G.add_edge(idx[prod], idx[eid], weight=0.0)
    for m, _, _ in MODULES:
        for sid, _ in STRATEGIES:
            if m in ("dreaming", "meta_cognition"):
                G.add_edge(idx[m], idx[sid], weight=0.0)
    for aid, _ in ACTIONS:
        G.add_edge(idx["learning"], idx[aid], weight=0.0)
    return G, nodes, idx


def hourly_counts(db_path: str, hours: int) -> dict:
    """hour -> {etype: n} over the window."""
    import sqlite3
    import tempfile
    import time
    con = None
    for _ in range(3):
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
            break
        except Exception:
            try:
                con = sqlite3.connect(db_path, timeout=30)
                con.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
                break
            except Exception:
                time.sleep(1)
    if con is None and os.path.exists(db_path):
        tmp = os.path.join(tempfile.gettempdir(), "ms_temporal_copy.db")
        for _ in range(5):
            try:
                shutil.copy2(db_path, tmp)
                con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
                con.execute("SELECT COUNT(*) FROM memories").fetchone()
                break
            except Exception:
                con = None
                time.sleep(2)
    if con is None:
        raise RuntimeError(f"cannot open {db_path}")
    cur = con.cursor()
    # bucket timestamps by ISO hour
    rows = cur.execute(
        "SELECT event_type, timestamp, data FROM memories WHERE timestamp >= "
        "datetime('now', ?)", (f"-{hours} hours",)).fetchall()
    con.close()
    buckets = defaultdict(lambda: defaultdict(int))
    for etype, ts, data in rows:
        try:
            dt = datetime.fromisoformat(ts)
            hour = dt.strftime("%Y-%m-%dT%H:00Z")
        except Exception:
            continue
        buckets[hour][etype] += 1
    return buckets


def build_hour_graph(static_G, nodes, idx, counts: dict):
    G = static_G.copy()
    total = sum(counts.values())
    for eid, name in EVENTS:
        n = counts.get(name, 0)
        if n:
            i = idx[eid]
            G.nodes[i]["count"] = n
            for prod, peid in PRODUCES:
                if peid == eid:
                    G.edges[idx[prod], i]["weight"] += n
    # steering/adaptation weight into strategy nodes happens via event edges
    for i in G.nodes():
        G.nodes[i].setdefault("count", 0)
    return G, total


def render_frame(G, nodes, hour, total, outdir):
    from pyvis.network import Network
    deg = dict(G.degree())
    max_deg = max(deg.values()) or 1
    max_w = max((d.get("weight", 0) for _, _, d in G.edges(data=True)), default=0) or 1
    net = Network(height="620px", width="100%", bgcolor="#0d1117",
                  font_color="#c9d1d9", directed=False)
    for i, n in enumerate(nodes):
        c = G.nodes[i].get("count", 0)
        net.add_node(i, label=n["label"], color=NODE_COLOR.get(n["id"], "#888"),
                     size=8 + (14 if c else 4) * (1 + deg.get(i, 1) / max_deg) * 0.6,
                     title=f"{n['label']}<br>events this hour: {c}<br>degree {deg.get(i, 0)}")
    for a, b, d in G.edges(data=True):
        w = d.get("weight", 0)
        if w > 0:
            net.add_edge(a, b, color="#f0883e", width=min(0.6 + 2.5 * (w / max_w), 6))
        else:
            net.add_edge(a, b, color="#30363d", width=0.5)
    net.set_options(json.dumps({
        "nodes": {"borderWidth": 1.0, "font": {"size": 11, "face": "Tahoma"}},
        "edges": {"smooth": {"type": "continuous"}},
        "physics": {"enabled": True, "stabilization": {"iterations": 150}},
        "interaction": {"hover": True, "tooltipDelay": 100},
    }))
    frame = os.path.join(outdir, "frames", f"hour_{hour[11:13]}.html")
    os.makedirs(os.path.dirname(frame), exist_ok=True)
    net.show(frame, notebook=False)
    legend = (f'<div style="position:fixed;bottom:6px;left:6px;z-index:9999;'
              f'background:rgba(13,17,23,0.92);border:1px solid #30363d;'
              f'border-radius:6px;padding:6px 10px;font-family:Tahoma;font-size:11px;'
              f'color:#c9d1d9;">hour {hour[11:13]} UTC · {total} events</div>')
    with open(frame, "r", encoding="utf-8") as f:
        h = f.read()
    if "</body>" in h:
        with open(frame, "w", encoding="utf-8") as f:
            f.write(h.replace("</body>", legend + "</body>"))
    return frame


def write_viewer(frames, hours, outdir):
    frame_names = [os.path.basename(f) for f in frames]
    slides = ",\n    ".join(f'{{ "label": "{fn[5:7]}:00", "file": "frames/{fn}" }}'
                            for fn in frame_names)
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Learning loop — hourly evolution</title>
<style>
  body {{ background:#0d1117; color:#c9d1d9; font-family:Tahoma,Arial,sans-serif; margin:0; }}
  #head {{ padding:14px 20px 6px; }}
  #head h1 {{ margin:0; font-size:18px; }}
  #head p {{ margin:4px 0 0; font-size:12px; color:#8b949e; }}
  #frame {{ width:100%; height:640px; border:0; }}
  #controls {{ padding:8px 20px; display:flex; align-items:center; gap:10px; }}
  button {{ background:#21262d; color:#c9d1d9; border:1px solid #30363d; border-radius:6px;
            padding:6px 14px; cursor:pointer; font-size:13px; }}
  button:hover {{ background:#30363d; }}
  #slider {{ flex:1; accent-color:#3fb950; }}
  #cur {{ font-size:13px; min-width:70px; }}
</style></head><body>
<div id="head">
  <h1>Learning loop — hourly evolution (last {hours}h)</h1>
  <p>node size = events that hour · orange edges = activity weight · grey = static links</p>
</div>
<iframe id="frame" src="frames/{frame_names[0]}"></iframe>
<div id="controls">
  <button id="prev">&#9664; prev</button>
  <button id="play">&#9654; play</button>
  <button id="next">next &#9654;</button>
  <span id="cur">{frame_names[0][5:7]}:00</span>
  <input type="range" id="slider" min="0" max="{len(frame_names)-1}" value="0" step="1">
</div>
<script>
const slides = [
    {slides}
];
let i = 0, timer = null;
const frame = document.getElementById('frame');
const slider = document.getElementById('slider');
const cur = document.getElementById('cur');
function show(n) {{
  i = (n + slides.length) % slides.length;
  frame.src = 'frames/' + slides[i].file.split('/').pop();
  slider.value = i;
  cur.textContent = slides[i].label;
}}
document.getElementById('prev').onclick = () => show(i - 1);
document.getElementById('next').onclick = () => show(i + 1);
document.getElementById('play').onclick = () => {{
  if (timer) {{ clearInterval(timer); timer = null; document.getElementById('play').textContent = '&#9654; play'; return; }}
  timer = setInterval(() => {{ show(i + 1); }}, 2500);
  document.getElementById('play').textContent = '&#9632; pause';
}};
slider.oninput = () => show(parseInt(slider.value));
</script></body></html>"""
    out = os.path.join(outdir, "timeline.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


def main():
    ap = argparse.ArgumentParser(description="Temporal learning-loop animation")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--out", default=os.path.join(ROOT, "graphs", "learning_loop_temporal"))
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()

    outdir = os.path.abspath(args.out)
    os.makedirs(os.path.join(outdir, "frames"), exist_ok=True)

    G, nodes, idx = build_static()
    buckets = {}
    if not args.no_db:
        mem = os.environ.get("AZAZAEL_MEMORY_DIR", "")
        db = mem if mem.endswith(".db") else os.path.join(mem, "memory_store.db")
        if mem:
            try:
                buckets = hourly_counts(db, args.hours)
            except Exception as e:
                sys.stderr.write(f"[temporal] DB read failed: {e}\n")
    if not buckets:
        # fall back to synthetic evenly-spread counts so the viewer still runs
        now = datetime.now(timezone.utc)
        for h in range(args.hours):
            hour = (now - timedelta(hours=args.hours - 1 - h)).strftime("%Y-%m-%dT%H:00Z")
            buckets[hour] = defaultdict(int, {"dream_generated": 30 + (h % 5) * 7})

    hours_sorted = sorted(buckets.keys())
    counts_dump = {h: dict(buckets[h]) for h in hours_sorted}
    with open(os.path.join(outdir, "hourly_counts.json"), "w", encoding="utf-8") as f:
        json.dump(counts_dump, f, indent=1)

    frames = []
    for hour in hours_sorted:
        hG, total = build_hour_graph(G, nodes, idx, buckets[hour])
        frames.append(render_frame(hG, nodes, hour, total, outdir))
    viewer = write_viewer(frames, args.hours, outdir)
    print(f"{len(frames)} hourly frames -> {os.path.join(outdir, 'frames')}")
    print("viewer ->", viewer)
    print("counts ->", os.path.join(outdir, "hourly_counts.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
