#!/usr/bin/env python3
"""learning_loop_graph.py — map the autonomous learning-loop subsystem.

Builds a multi-type graph of the learning-loop domain (modules, events,
strategies, actions) in the project's cosmos format, then drives the same
pipeline as bcc_network_sna.py: SNA (centrality, Louvain, k-core, cliques),
Gephi CSVs + GraphML + GEXF, PyVis interactive HTML, and Mermaid.

Nodes and static edges come from the source inventory below; live edges are
weighted by real events from the LocalMemoryStore (event counts, steering
targets, adopted strategy flips) when the DB is reachable.

Outputs (all under <out>/):
  cosmos/graph.labels.0.js, cosmos/graph.links.js   (cosmos viewer format)
  graph.json                                        (node-link for export_graph_formats)
  sna_results.json                                  (SNA dump)
  gephi/nodes.csv, gephi/edges.csv                  (Gephi spreadsheet import)
  learning_loop.graphml, learning_loop.gexf         (one-drag Gephi import)
  learning_loop.html                                (PyVis interactive)
  learning_loop.mmd                                 (Mermaid)

Usage:
  python3 scripts/learning_loop_graph.py [--out azazael/graphs/learning_loop] [--no-db]
"""
import argparse
import base64
import json
import os
import struct
import sys
import sysconfig

try:
    import networkx as nx
    from networkx.algorithms.community import louvain_communities
except ImportError:
    sys.exit("networkx required")

# numpy 2.x removed np.float_ (networkx 2.8.8 GraphML writer still uses it);
# lxml on some 3.14t installs is broken, so the XML fallback must work.
try:
    import numpy as _np
    if not hasattr(_np, "float_"):
        _np.float_ = _np.float64
except ImportError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))


def _ensure_gil():
    """Free-threaded 3.14t builds have a flaky sqlite3; re-exec with GIL on."""
    if (sysconfig.get_config_var("Py_GIL_DISABLED")
            and os.environ.get("AZAZAEL_GIL_ENFORCED") != "1"):
        os.environ["AZAZAEL_GIL_ENFORCED"] = "1"
        os.execv(sys.executable, [sys.executable, "-X", "gil=1"] + sys.argv)


_ensure_gil()
sys.path.insert(0, HERE)
from bcc_network_sna import analyze, export_csv  # noqa: E402
import csv

# --------------------------------------------------------------------------
# Static inventory: the learning-loop domain as of 2026-09-07
# --------------------------------------------------------------------------

# (id, label, type, file)
MODULES = [
    ("dreaming",        "dreaming.py",           "module",  "src/CORE/WORMS/expansion/dreaming.py"),
    ("meta_cognition",  "meta_cognition.py",     "module",  "src/CORE/WORMS/expansion/meta_cognition.py"),
    ("learning",        "learning.py",           "module",  "src/CORE/WORMS/expansion/learning.py"),
    ("evolution",       "evolution.py",          "module",  "src/CORE/WORMS/expansion/evolution.py"),
    ("bridge",          "dream_policy_bridge.py","module",  "src/CORE/dream_policy_bridge.py"),
    ("local_memory",    "local_memory.py",       "module",  "src/CORE/local_memory.py"),
    ("monitor",         "monitor_learning.py",   "module",  "src/scripts/monitor_learning.py"),
    ("dream_cycle",     "dream_cycle.py",        "module",  "scripts/dream_cycle.py"),
    ("metacog_cycle",   "metacog_cycle.py",      "module",  "scripts/metacog_cycle.py"),
    ("evolution_cycle", "evolution_cycle.py",    "module",  "scripts/evolution_cycle.py"),
    ("CLUSTERWORMS",    "CLUSTERWORMS.py",       "external","src/CORE/WORMS/CLUSTERWORMS.py"),
]

EVENTS = [
    ("ev_dream",           "dream_generated",           "event"),
    ("ev_steered",         "strategy_steered",          "event"),
    ("ev_policy",          "policy_applied",            "event"),
    ("ev_adapt",           "strategy_adaptation",       "event"),
    ("ev_state",           "state_saved",               "event"),
    ("ev_mccycle",         "mc_cycle",                  "event"),
    ("ev_evoresult",       "evolution_result",          "event"),
    ("ev_train",           "training_complete",         "event"),
    ("ev_qtbl",            "q_table_persisted",         "event"),
    ("ev_cluster",         "clusterwormz_execute_complete", "event"),
]

STRATEGIES = [
    ("st_default",     "default",     "strategy"),
    ("st_conservative","conservative","strategy"),
    ("st_defensive",   "defensive",   "strategy"),
    ("st_aggressive",  "aggressive",  "strategy"),
    ("st_adaptive",    "adaptive",    "strategy"),
    ("st_batched",     "batched",     "strategy"),
    ("st_staged",      "staged",      "strategy"),
]

ACTIONS = [
    ("ac_ignore",    "ignore",    "action"),
    ("ac_log",       "log",       "action"),
    ("ac_alert",     "alert",     "action"),
    ("ac_remediate", "remediate", "action"),
]

# (source, target, kind) -- static edges
STATIC_EDGES = [
    # module -> module (import / call)
    ("dreaming", "local_memory", "import"), ("dreaming", "bridge", "import"),
    ("dreaming", "meta_cognition", "import"),
    ("meta_cognition", "local_memory", "import"),
    ("learning", "local_memory", "import"),
    ("bridge", "meta_cognition", "import"),
    ("monitor", "local_memory", "import"),
    ("dream_cycle", "dreaming", "calls"),
    ("metacog_cycle", "meta_cognition", "calls"),
    ("metacog_cycle", "local_memory", "import"),
    ("evolution_cycle", "evolution", "calls"),
    ("evolution_cycle", "learning", "import"),
    # module -> event (produces)
    ("dreaming", "ev_dream", "produces"), ("dreaming", "ev_steered", "produces"),
    ("bridge", "ev_policy", "produces"),
    ("meta_cognition", "ev_adapt", "produces"), ("meta_cognition", "ev_state", "produces"),
    ("metacog_cycle", "ev_mccycle", "produces"),
    ("evolution_cycle", "ev_evoresult", "produces"),
    ("learning", "ev_train", "produces"), ("learning", "ev_qtbl", "produces"),
    ("CLUSTERWORMS", "ev_cluster", "produces"),
    # event -> module (consumes)
    ("metacog_cycle", "ev_cluster", "consumes"),
    ("monitor", "ev_dream", "consumes"), ("monitor", "ev_policy", "consumes"),
    ("monitor", "ev_state", "consumes"), ("monitor", "ev_mccycle", "consumes"),
    ("monitor", "ev_evoresult", "consumes"), ("monitor", "ev_train", "consumes"),
    # strategy / action space (applies / steers / can choose)
    ("dreaming", "st_default", "steers"), ("dreaming", "st_conservative", "steers"),
    ("dreaming", "st_defensive", "steers"), ("dreaming", "st_aggressive", "steers"),
    ("dreaming", "st_adaptive", "steers"), ("dreaming", "st_batched", "steers"),
    ("dreaming", "st_staged", "steers"),
    ("meta_cognition", "st_default", "applies"), ("meta_cognition", "st_conservative", "applies"),
    ("meta_cognition", "st_defensive", "applies"), ("meta_cognition", "st_aggressive", "applies"),
    ("meta_cognition", "st_adaptive", "applies"), ("meta_cognition", "st_batched", "applies"),
    ("meta_cognition", "st_staged", "applies"),
    ("learning", "ac_ignore", "chooses"), ("learning", "ac_log", "chooses"),
    ("learning", "ac_alert", "chooses"), ("learning", "ac_remediate", "chooses"),
]

EVENT_ID_BY_NAME = {name: eid for eid, name, _ in EVENTS}
STRAT_ID_BY_NAME = {name: sid for sid, name, _ in STRATEGIES}


def live_edges(db_path: str) -> dict:
    """Read-only DB enrichment: event counts + steering/adaptation weights."""
    import sqlite3
    import shutil
    import tempfile
    out = {"counts": {}, "edges": []}
    try:
        con = None
        for _attempt in range(3):
            try:
                # read-only first; fall back to rw when the WAL needs
                # recovery (e.g. a training run is writing concurrently)
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                break
            except sqlite3.OperationalError:
                try:
                    con = sqlite3.connect(db_path, timeout=30)
                    break
                except sqlite3.OperationalError:
                    import time
                    time.sleep(1)
        if con is None and os.path.exists(db_path):
            # sqlite locking over drvfs is unreliable right after a cycle
            # write; copy to local ext4 and read the copy instead, retrying
            # until a readable snapshot lands (torn copy = open but bad query)
            tmp = os.path.join(tempfile.gettempdir(), "ms_loop_copy.db")
            for _ in range(5):
                try:
                    shutil.copy2(db_path, tmp)
                    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
                    con.execute("SELECT COUNT(*) FROM memories").fetchone()
                    break
                except Exception as e:
                    sys.stderr.write(f"[learning_loop_graph] copy fallback retry: {e}\n")
                    try:
                        con.close()
                    except Exception:
                        pass
                    con = None
                    time.sleep(2)
        if con is None:
            raise sqlite3.OperationalError(f"cannot open {db_path}")
        cur = con.cursor()
        for eid, name, _ in EVENTS:
            row = cur.execute(
                "SELECT COUNT(*) FROM memories WHERE event_type=? "
                "AND timestamp >= datetime('now', '-24 hours')", (name,)).fetchone()
            out["counts"][eid] = row[0] if row else 0
        # steering targets (strategy_steered data->strategy)
        for row in cur.execute(
                "SELECT data FROM memories WHERE event_type='strategy_steered' "
                "AND timestamp >= datetime('now', '-24 hours')").fetchall():
            d = row[0]
            try:
                d = json.loads(d) if isinstance(d, str) else d
            except Exception:
                continue
            sid = STRAT_ID_BY_NAME.get(d.get("strategy"))
            if sid:
                out["edges"].append(("ev_steered", sid, "leads_to"))
        # adopted strategy flips (strategy_adaptation where should_adapt)
        for row in cur.execute(
                "SELECT data FROM memories WHERE event_type='strategy_adaptation' "
                "AND timestamp >= datetime('now', '-24 hours')").fetchall():
            d = row[0]
            try:
                d = json.loads(d) if isinstance(d, str) else d
            except Exception:
                continue
            if d.get("should_adapt"):
                sid = STRAT_ID_BY_NAME.get(d.get("recommended_strategy"))
                if sid:
                    out["edges"].append(("ev_adapt", sid, "leads_to"))
        con.close()
    except Exception as e:
        sys.stderr.write(f"[learning_loop_graph] DB enrichment skipped: {e}\n")
    return out


def build_graph(use_db: bool) -> tuple:
    """Return (G, nodes, node_index, edge_kinds) in cosmos-compatible form."""
    nodes = []
    for nid, label, ntype, f in MODULES:
        nodes.append({"id": nid, "label": label, "type": ntype, "file": f})
    for eid, name, ntype in EVENTS:
        nodes.append({"id": eid, "label": name, "type": ntype, "file": ""})
    for sid, name, ntype in STRATEGIES:
        nodes.append({"id": sid, "label": name, "type": ntype, "file": ""})
    for aid, name, ntype in ACTIONS:
        nodes.append({"id": aid, "label": name, "type": ntype, "file": ""})

    idx = {n["id"]: i for i, n in enumerate(nodes)}

    G = nx.Graph()
    for i, n in enumerate(nodes):
        G.add_node(i, id=n["id"], label=n["label"], type=n["type"], file=n.get("file", ""))

    _mem_dir = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    _db = _mem_dir if _mem_dir.endswith(".db") else os.path.join(_mem_dir, "memory_store.db")
    live = live_edges(_db) if use_db and _mem_dir else {"counts": {}, "edges": []}
    for n in nodes:
        G.nodes[idx[n["id"]]]["count"] = live["counts"].get(n["id"], 0)

    kinds = {}
    for s, t, kind in STATIC_EDGES + [tuple(e) for e in live["edges"]]:
        a, b = idx[s], idx[t]
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        if key in kinds:
            if kind not in kinds[key]:
                kinds[key] = kinds[key] + "+" + kind
            continue
        kinds[key] = kind
        G.add_edge(a, b, etype=[kind])

    return G, nodes, idx, kinds


COSMOS_COLOR = {"module": "#3fb950", "external": "#ffd700", "event": "#58a6ff",
                "strategy": "#ff8844", "action": "#aa66ff"}


def write_cosmos(G, nodes, outdir: str) -> None:
    os.makedirs(os.path.join(outdir, "cosmos"), exist_ok=True)
    deg = dict(G.degree())
    labels = [{"id": n["id"], "label": n["label"], "file": n.get("file", ""),
               "type": n["type"], "color": COSMOS_COLOR.get(n["type"], "#888"),
               "deg": deg[i]} for i, n in enumerate(nodes)]
    with open(os.path.join(outdir, "cosmos", "graph.labels.0.js"), "w", encoding="utf-8") as f:
        f.write("window.GV_LABELS_CHUNKS = window.GV_LABELS_CHUNKS || [];\n")
        f.write("window.GV_LABELS_CHUNKS.push(" + json.dumps(labels) + ");\n")
    pairs = []
    for a, b in G.edges():
        pairs += [a, b]
    raw = struct.pack("<%dI" % len(pairs), *pairs)
    with open(os.path.join(outdir, "cosmos", "graph.links.js"), "w", encoding="utf-8") as f:
        f.write("window.GV_LINKS = new Uint32Array(Uint8Array.from(atob('")
        f.write(base64.b64encode(raw).decode())
        f.write("')));\n")


def write_graph_json(G, nodes, outdir: str) -> None:
    data = {"directed": False, "multigraph": False, "graph": {},
            "nodes": [{"id": n["id"], "label": n["label"], "type": n["type"],
                       "file": n.get("file", ""), "count": G.nodes[i].get("count", 0)}
                      for i, n in enumerate(nodes)],
            "links": [{"source": G.nodes[a]["id"], "target": G.nodes[b]["id"]}
                      for a, b in G.edges()]}
    with open(os.path.join(outdir, "graph.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def write_mermaid(G, nodes, outdir: str) -> None:
    """ASCII-only Mermaid (project convention): subgraphs per type."""
    type_node = {}
    for i, n in enumerate(nodes):
        type_node.setdefault(n["type"], []).append(i)
    lines = ["graph TD"]
    style = {"module": "classDef module fill:#1a3a1a,stroke:#00ff88,color:#00ff88",
             "external": "classDef external fill:#3a3a1a,stroke:#ffd700,color:#ffd700",
             "event": "classDef event fill:#1a1a3a,stroke:#4488ff,color:#4488ff",
             "strategy": "classDef strategy fill:#3a1a1a,stroke:#ff8844,color:#ff8844",
             "action": "classDef action fill:#2a1a3a,stroke:#aa66ff,color:#aa66ff"}
    for t, c in style.items():
        lines.append("    " + c)
    for t, members in type_node.items():
        if len(members) > 1:
            lines.append(f"    subgraph {t.upper()}S[{t.upper()}]")
        for i in members:
            n = nodes[i]
            count = G.nodes[i].get("count", 0)
            lab = n["label"] + (f" ({count})" if count else "")
            lines.append(f"      {n['id']}[\"{lab}\"]")
        if len(members) > 1:
            lines.append("    end")
    edge_style = {"import": "#8b949e", "calls": "#a371f7", "produces": "#3fb950",
                  "consumes": "#58a6ff", "steers": "#ff8844", "applies": "#d29922",
                  "chooses": "#aa66ff", "leads_to": "#f778ba"}
    for li, (a, b) in enumerate(G.edges()):
        na, nb = nodes[a], nodes[b]
        lines.append(f"    {na['id']} --> {nb['id']}")
        et = G.edges[a, b].get("etype", ["import"])
        if isinstance(et, list):
            et = et[0] if et else "import"
        base = et.split("+")[0] if "+" in et else et
        lines.append(f"    linkStyle {li} stroke:{edge_style.get(base, '#666')}")
    cls = {"module": [], "external": [], "event": [], "strategy": [], "action": []}
    for t, members in type_node.items():
        cls[t] = [nodes[i]["id"] for i in members]
    for t, ids in cls.items():
        if ids:
            lines.append(f"    class {','.join(ids)} {t}")
    seq = [
        "",
        "sequenceDiagram",
        "    participant CYC as dream_cycle (5 min)",
        "    participant D as dreaming",
        "    participant B as dream_policy_bridge",
        "    participant M as meta_cognition",
        "    participant MC as metacog_cycle (5 min)",
        "    participant E as evolution_cycle",
        "    participant L as local_memory",
        "    CYC->>D: run_dreaming_pipeline(8 eps)",
        "    D->>M: dream_tuning_for(global)",
        "    M-->>D: tuned params (steered)",
        "    D->>L: dream + strategy_steered",
        "    D->>B: apply_dream_policy",
        "    B->>M: adapt_strategy(recommended, confidence)",
        "    M-->>B: adopted/held (0.5 bar)",
        "    MC->>M: observe(cluster outcomes)",
        "    M-->>MC: recommend_from_evidence",
        "    MC->>M: adapt_strategy(global, evidence)",
        "    E->>L: train_from_memory(avg_reward)",
    ]
    with open(os.path.join(outdir, "learning_loop.mmd"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines + seq) + "\n")


def write_graphml(G, nodes, results, outdir: str) -> str:
    """GraphML with centrality/community/k-core attrs (mirrors bcc launcher)."""
    int_ = results["_internal"]
    comm_of = int_["comm_of"]
    core = nx.core_number(G)
    for i, n in enumerate(nodes):
        G.nodes[i]["Label"] = n["label"]
        G.nodes[i]["Type"] = n["type"]
        G.nodes[i]["Betweenness"] = round(int_["bet"].get(i, 0), 4)
        G.nodes[i]["Eigenvector"] = round(int_["eig"].get(i, 0), 4)
        G.nodes[i]["Closeness"] = round(int_["close"].get(i, 0), 4)
        G.nodes[i]["Community"] = comm_of.get(i, "")
        G.nodes[i]["KCore"] = core.get(i, 0)
    for a, b in G.edges():
        et = G.edges[a, b].get("etype", ["link"])
        if isinstance(et, list):
            G.edges[a, b]["etype"] = "+".join(et)
        G.edges[a, b]["EdgeType"] = G.edges[a, b]["etype"]
    path = os.path.join(outdir, "learning_loop.graphml")
    nx.write_graphml(G, path, encoding="utf-8")
    return path


def write_gexf(G, outdir: str) -> str:
    for n, d in G.nodes(data=True):
        for k, v in list(d.items()):
            if v is None:
                d[k] = ""
    for u, v, d in G.edges(data=True):
        for k, val in list(d.items()):
            if val is None:
                d[k] = ""
    path = os.path.join(outdir, "learning_loop.gexf")
    nx.write_gexf(G, path, encoding="utf-8")
    return path


def _write_sna_view(G, nodes, results, outdir: str, mode: str) -> None:
    """Metric-based PyVis view: size by betweenness, color by community or
    betweenness ramp, saved as learning_loop_sna.html (keeps the default
    type-colored learning_loop.html intact)."""
    from pyvis.network import Network
    int_ = results["_internal"]
    bet = int_["bet"]
    comm_of = int_["comm_of"]
    comm_colors = ["#3fb950", "#58a6ff", "#d29922", "#a371f7", "#f778ba",
                   "#ff8844", "#39c5cf", "#e3b341", "#f47067", "#8b949e"]
    deg = dict(G.degree())
    max_deg = max(deg.values()) or 1
    max_bet = max((bet.get(i, 0) for i in G.nodes()), default=0.0) or 1e-9
    n_comm = max((comm_of.get(i, -1) for i in G.nodes()), default=-1) + 1

    net = Network(height="900px", width="100%", bgcolor="#0d1117",
                  font_color="#c9d1d9", directed=False)
    for i, n in enumerate(nodes):
        b = bet.get(i, 0.0)
        if mode == "community":
            col = comm_colors[comm_of.get(i, 0) % len(comm_colors)]
            size = 8 + 20 * (deg.get(i, 1) / max_deg)
            title = (f"{n['label']}<br>community C{comm_of.get(i, '?')} · "
                     f"degree {deg.get(i, 0)} · betweenness {b:.4f}")
        else:  # betweenness ramp: low=dim blue -> high=hot orange
            t = b / max_bet
            col = f"rgb({int(60 + 195 * t)},{int(140 - 80 * t)},{int(200 - 120 * t)})"
            size = 6 + 40 * (b / max_bet)
            title = (f"{n['label']}<br>betweenness {b:.4f} (rank by size) · "
                     f"community C{comm_of.get(i, '?')} · degree {deg.get(i, 0)}")
        net.add_node(i, label=n["label"], color=col, size=size, title=title)
    for a, b in G.edges():
        net.add_edge(a, b, color="#30363d", width=1.0)
    net.set_options(json.dumps({
        "nodes": {"borderWidth": 1.5, "font": {"size": 13, "face": "Tahoma"}},
        "edges": {"smooth": {"type": "continuous"}},
        "physics": {"enabled": True, "stabilization": {"iterations": 300}},
        "interaction": {"hover": True, "tooltipDelay": 120},
    }))
    html = os.path.join(outdir, f"learning_loop_sna_{mode}.html")
    net.show(html, notebook=False)
    legend = (
        '<div style="position:fixed;bottom:8px;left:8px;z-index:9999;'
        'background:rgba(13,17,23,0.92);border:1px solid #30363d;border-radius:6px;'
        'padding:8px 12px;font-family:Tahoma,Arial,sans-serif;font-size:11px;'
        'color:#c9d1d9;">'
        + (f"<b>{mode} coloring</b> · {n_comm} communities<br>"
           + "".join(f'<span style="display:inline-block;margin-right:8px;">'
                     f'<span style="display:inline-block;width:10px;height:10px;background:{comm_colors[c % len(comm_colors)]};'
                     f'margin-right:3px;border-radius:2px;"></span>C{c}</span>'
                     for c in range(n_comm))
           if mode == "community" else
           "<b>betweenness ramp</b>: blue = low influence · orange = high influence<br>"
           "node size = betweenness")
        + '</div>')
    try:
        with open(html, "r", encoding="utf-8") as f:
            h = f.read()
        if "</body>" in h:
            h = h.replace("</body>", legend + "</body>")
            with open(html, "w", encoding="utf-8") as f:
                f.write(h)
    except Exception as e:
        sys.stderr.write(f"[learning_loop_graph] sna legend skipped: {e}\n")
    print(f"Wrote {html} (color_by={mode})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Map the learning-loop subsystem through the full graphing pipeline")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "graphs", "learning_loop"))
    ap.add_argument("--no-db", action="store_true", help="skip live DB enrichment")
    ap.add_argument("--color-by", choices=["type", "community", "betweenness"],
                    default="type",
                    help="node color/size scheme: type (default), community (Louvain), "
                         "betweenness (size=betweenness, color=community)")
    args = ap.parse_args()

    outdir = os.path.abspath(args.out)
    os.makedirs(outdir, exist_ok=True)

    G, nodes, idx, kinds = build_graph(use_db=not args.no_db)
    write_cosmos(G, nodes, outdir)
    write_graph_json(G, nodes, outdir)
    write_mermaid(G, nodes, outdir)
    print(f"graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges -> {outdir}")

    results = analyze(G, seed=42)
    dump = {k: v for k, v in results.items() if k != "_internal"}
    with open(os.path.join(outdir, "sna_results.json"), "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)

    if args.color_by != "type":
        _write_sna_view(G, nodes, results, outdir, args.color_by)

    print("\n=== TOP DEGREE ===")
    for label in results["rankings"]["top_degree"][:8]:
        print(f"  {label}")
    print("\n=== TOP BETWEENNESS (bridges) ===")
    for label in results["rankings"]["top_betweenness"][:8]:
        print(f"  {label}")
    print("\n=== TOP EIGENVECTOR (influence) ===")
    for label in results["rankings"]["top_eigenvector"][:8]:
        print(f"  {label}")

    print(f"\n=== LOUVAIN: {len(results['communities'])} communities ===")
    for c in results["communities"]:
        print(f"  C{c['index']} (n={c['n']}, {c['types']})")
        for m in c["members"][:10]:
            print(f"      - {m}")
        if len(c["members"]) > 10:
            print(f"      ... +{len(c['members']) - 10} more")

    cc = results["core_clique"]
    print("\n=== K-CORE SHELLS ===")
    for k in sorted((int(x) for x in cc["core_shells"]), reverse=True):
        s = cc["core_shells"][str(k)]
        print(f"  {k}-core (n={s['n']}): {', '.join(s['members'][:8])}")
    print(f"  clique number: {cc['clique_number']} | maximal cliques: {cc['maximal_cliques_count']}")

    # Gephi exports: CSV pair + GraphML + GEXF
    export_csv(G, nodes, os.path.join(outdir, "gephi"), results)
    gml = write_graphml(G, nodes, results, outdir)
    gexf = write_gexf(G, outdir)
    print(f"\nWrote {gml}\nWrote {gexf}")

    # PyVis interactive HTML (mirrors bcc_pyvis_network.py)
    try:
        from pyvis.network import Network
        COLOR = {"module": "#3fb950", "external": "#ffd700", "event": "#58a6ff",
                 "strategy": "#ff8844", "action": "#aa66ff"}
        EDGE_COLOR = {"import": "#8b949e", "calls": "#a371f7", "produces": "#3fb950",
                      "consumes": "#58a6ff", "steers": "#ff8844", "applies": "#d29922",
                      "chooses": "#aa66ff", "leads_to": "#f778ba"}
        deg = dict(G.degree())
        max_deg = max(deg.values()) or 1
        comm_of = results["_internal"]["comm_of"]
        net = Network(height="900px", width="100%", bgcolor="#0d1117",
                      font_color="#c9d1d9", directed=False)
        for i, n in enumerate(nodes):
            net.add_node(i, label=n["label"], color=COLOR.get(n["type"], "#888"),
                         size=8 + 20 * (deg.get(i, 1) / max_deg),
                         shape="star" if n["type"] == "strategy" else
                               "diamond" if n["type"] == "action" else
                               "square" if n["type"] == "event" else "dot",
                         title=f"{n['label']}<br>type {n['type']} · community C{comm_of.get(i, '?')} · degree {deg.get(i, 0)}")
        for a, b in G.edges():
            et = G.edges[a, b].get("etype", ["import"])
            if isinstance(et, list):
                et = et[0] if et else "import"
            base = et.split("+")[0] if "+" in et else et
            net.add_edge(a, b, color=EDGE_COLOR.get(base, "#30363d"), width=1.2)
        net.set_options(json.dumps({
            "nodes": {"borderWidth": 1.5, "font": {"size": 13, "face": "Tahoma"}},
            "edges": {"smooth": {"type": "continuous"}, "color": {"inherit": False}},
            "physics": {"enabled": True, "stabilization": {"iterations": 300}},
            "interaction": {"hover": True, "tooltipDelay": 120},
        }))
        html = os.path.join(outdir, "learning_loop.html")
        net.show(html, notebook=False)
        _inject_legend(html, COLOR, EDGE_COLOR,
                       "node = type · edge = relation (import/calls/produces/consumes/steers/applies/chooses/leads_to)")
        print(f"Wrote {html}")
    except Exception as e:
        print(f"PyVis skipped: {e}")

    print("\nAll artifacts under " + outdir)
    return 0


def _inject_legend(html_path: str, node_colors: dict, edge_colors: dict, caption: str) -> None:
    """Append a fixed-position color legend (nodes + edges) to the PyVis HTML."""
    def swatch(color, label):
        return (f'<span style="display:inline-block;margin:2px 10px 2px 0;">'
                f'<span style="display:inline-block;width:12px;height:12px;'
                f'background:{color};border:1px solid #555;border-radius:2px;'
                f'margin-right:4px;vertical-align:middle;"></span>{label}</span>')
    node_part = "".join(swatch(c, t) for t, c in sorted(node_colors.items()))
    edge_part = "".join(swatch(c, t) for t, c in sorted(edge_colors.items()))
    legend = (
        '<div style="position:fixed;bottom:8px;left:8px;z-index:9999;'
        'background:rgba(13,17,23,0.92);border:1px solid #30363d;'
        'border-radius:6px;padding:8px 12px;font-family:Tahoma,Arial,sans-serif;'
        'font-size:11px;color:#c9d1d9;max-width:85%;">'
        f'<div style="font-weight:bold;margin-bottom:4px;">{caption}</div>'
        '<div><b>nodes</b>&nbsp;' + node_part + '</div>'
        '<div><b>edges</b>&nbsp;' + edge_part + '</div></div>')
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        if "</body>" in html:
            html = html.replace("</body>", legend + "</body>")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
    except Exception as e:
        sys.stderr.write(f"[learning_loop_graph] legend inject skipped: {e}\n")


if __name__ == "__main__":
    sys.exit(main())
