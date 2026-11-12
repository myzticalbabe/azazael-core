#!/usr/bin/env python3
"""ecosystem_graph.py — map the full Azazael ecosystem (src/ + scripts/) as one graph.

Builds an import graph of every local .py file (510 in src/, 84 in scripts/) with
ast-resolved import edges, then enriches the learning-loop nodes with LIVE data
from the LocalMemoryStore (24h event counts, steering targets, adopted strategy
flips) so the map reflects behavior, not just structure.

Drives the same pipeline as learning_loop_graph.py / bcc_network_sna.py:
SNA (centrality, Louvain, k-core, cliques), Gephi CSVs + GraphML + GEXF,
PyVis interactive HTML, cosmos viewer format, and Mermaid.

Outputs (all under <out>/):
  cosmos/graph.labels.0.js, cosmos/graph.links.js   (cosmos viewer format)
  graph.json                                        (node-link for export_graph_formats)
  sna_results.json                                  (SNA dump)
  gephi/nodes.csv, gephi/edges.csv                  (Gephi spreadsheet import)
  ecosystem.graphml, ecosystem.gexf                 (one-drag Gephi import)
  ecosystem.html                                    (PyVis interactive)
  ecosystem.mmd                                     (Mermaid, ASCII-safe)

Usage:
  python3 scripts/ecosystem_graph.py [--out azazael/graphs/ecosystem] [--no-db]
"""
import argparse
import ast
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

# numpy 2.x removed np.float_ (networkx 2.8.8 GraphML writer still uses it)
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
import shutil

ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
SCRIPTS = os.path.join(ROOT, "scripts")

SKIP_PARTS = {"__pycache__", "egg-info", ".git"}
# files that are never valid import targets (logs, backups)
SKIP_SUFFIXES = (".pre_gate_backup",)

# --------------------------------------------------------------------------
# Live enrichment: which module basename produces which event types
# --------------------------------------------------------------------------
MODULE_EVENTS = {
    "dreaming.py":           ["dream_generated", "strategy_steered"],
    "dream_policy_bridge.py":["policy_applied"],
    "meta_cognition.py":     ["strategy_adaptation", "state_saved"],
    "metacog_cycle.py":      ["mc_cycle"],
    "evolution_cycle.py":    ["evolution_result"],
    "learning.py":           ["training_complete", "q_table_persisted"],
    "evolution.py":          ["evolution_result"],
    "CLUSTERWORMS.py":       ["clusterwormz_execute_complete"],
}

# --------------------------------------------------------------------------
# Color differentiation: curated palette for the major areas, deterministic
# hash fallback for the long tail (dark-theme friendly)
# --------------------------------------------------------------------------
AREA_COLORS = {
    "CORE":              "#3fb950",  # green  - core infrastructure
    "scripts":           "#58a6ff",  # blue   - driver/cycle scripts
    "SCRIPTS":           "#58a6ff",
    "ORCHESTRATION":     "#d29922",  # amber  - orchestration / HYDRA / training
    "HYDRA":             "#d29922",
    "CODEX":             "#a371f7",  # purple - codex / media / video
    "UTILITIES":         "#f778ba",  # pink   - utility / toolkit
    "TOOLS":             "#f778ba",
    "AZAZAEL_TOOLKIT":   "#f778ba",
    "META":              "#e3b341",  # gold   - meta / compliance / audit
    "eve_unleashed":     "#f0883e",  # orange - desktop apps
    "tests":             "#8b949e",  # gray   - tests
    "INTERPRETERS":      "#39c5cf",  # cyan   - language runners
    "DAEMONS":           "#39c5cf",
    "CLI":               "#39c5cf",
}


def area_color(area: str) -> str:
    if area in AREA_COLORS:
        return AREA_COLORS[area]
    # deterministic hash onto a bright, dark-theme-friendly palette
    palette = ["#f47067", "#7ee787", "#79c0ff", "#ffa657", "#d2a8ff",
               "#ff7b72", "#a5d6ff", "#ffc680", "#c69026", "#ffa198",
               "#56d4dd", "#e3b341", "#bc8cff", "#76e3ea", "#f778ba"]
    return palette[sum(ord(ch) for ch in area) % len(palette)]


def discover_files() -> list:
    """Return (path, rel_module_dotted) for every local .py file."""
    files = []
    for base in (SRC, SCRIPTS):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_PARTS and not any(s in d for s in SKIP_SUFFIXES)]
            for fn in filenames:
                if not fn.endswith(".py") or fn.startswith("."):
                    continue
                if any(s in fn for s in SKIP_SUFFIXES):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                files.append((full, rel))
    files.sort(key=lambda x: x[1])
    return files


def dotted_to_rel(dotted: str) -> str | None:
    """Map a dotted module path (CORE.uuid7) to rel path (src/CORE/uuid7.py)."""
    parts = dotted.split(".")
    # candidate: src/<A/B/C.py> or src/<A/B/C/__init__.py>, same for scripts/
    for base in (SRC, SCRIPTS):
        rel_base = "src" if base is SRC else "scripts"
        for i in range(len(parts), 0, -1):
            sub = parts[:i]
            py = os.path.join(base, *sub) + ".py"
            pkg = os.path.join(base, *sub, "__init__.py")
            if os.path.exists(py):
                return os.path.relpath(py, ROOT).replace(os.sep, "/")
            if os.path.exists(pkg):
                return os.path.relpath(pkg, ROOT).replace(os.sep, "/")
    return None


def extract_imports(path: str) -> list:
    """Return list of dotted module paths this file imports (local-only resolved)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read(), filename=path)
    except SyntaxError:
        return []
    mod = os.path.relpath(path, ROOT).replace(os.sep, "/")
    src_prefix = "src/"
    if mod.startswith(src_prefix):
        pkg = mod[len(src_prefix):-3].replace("/", ".")
        if pkg.endswith(".__init__"):
            pkg = pkg[:-len(".__init__")]
    elif mod.startswith("scripts/"):
        pkg = "scripts." + mod[len("scripts/"):-3].replace("/", ".")
        if pkg.endswith(".__init__"):
            pkg = pkg[:-len(".__init__")]
    else:
        pkg = ""
    dotted = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                dotted.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: resolve against own package
                base = pkg.split(".")
                if node.level > len(base):
                    continue
                base = base[:-node.level]
                if node.module:
                    base = base + node.module.split(".")
                dotted.append(".".join(base))
            elif node.module:
                dotted.append(node.module)
    return dotted


def resolve_edges(files: list) -> tuple:
    """Build (rel_path -> node_id) and list of (src_rel, dst_rel) import edges."""
    rel_to_id = {rel: i for i, (_, rel) in enumerate(files)}
    edges = []
    for i, (path, rel) in enumerate(files):
        for dotted in extract_imports(path):
            target = dotted_to_rel(dotted)
            if target and target in rel_to_id and rel_to_id[target] != i:
                edges.append((rel, target))
    return rel_to_id, edges


def live_counts() -> dict:
    """24h event counts from the live DB, keyed by module rel path -> {etype: n}."""
    mem = os.environ.get("AZAZAEL_MEMORY_DIR", "")
    db = mem if mem.endswith(".db") else os.path.join(mem, "memory_store.db")
    out = {}
    if not mem:
        return out
    import sqlite3
    import tempfile
    import time
    con = None
    for _ in range(3):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.execute("SELECT COUNT(*) FROM memories").fetchone()
            break
        except Exception:
            try:
                if con is not None:
                    con.close()
            except Exception:
                pass
            con = None
            try:
                con = sqlite3.connect(db, timeout=30)
                con.execute("SELECT COUNT(*) FROM memories").fetchone()
                break
            except Exception:
                try:
                    if con is not None:
                        con.close()
                except Exception:
                    pass
                con = None
                time.sleep(1)
    if con is None and os.path.exists(db):
        # sqlite locking over drvfs is unreliable right after a cycle write;
        # copy to local ext4 and read the copy, retrying until a readable
        # snapshot lands (a torn copy opens but fails its first query)
        tmp = os.path.join(tempfile.gettempdir(), "ms_eco_copy.db")
        for _ in range(5):
            try:
                shutil.copy2(db, tmp)
                con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
                con.execute("SELECT COUNT(*) FROM memories").fetchone()
                break
            except Exception as e:
                sys.stderr.write(f"[ecosystem_graph] copy fallback retry: {e}\n")
                try:
                    con.close()
                except Exception:
                    pass
                con = None
                time.sleep(2)
    if con is None:
        return out
    try:
        # locate each learning-loop module by basename (they live in
        # subdirectories: src/CORE/WORMS/expansion/*.py, scripts/*.py)
        def find_rel(basename: str):
            for base in (SRC, SCRIPTS):
                for dirpath, dirnames, filenames in os.walk(base):
                    dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
                    if basename in filenames:
                        return os.path.relpath(os.path.join(dirpath, basename),
                                               ROOT).replace(os.sep, "/")
            return None

        cur = con.cursor()
        for mod, evts in MODULE_EVENTS.items():
            rel = find_rel(mod)
            if not rel:
                continue
            counts = {}
            for e in evts:
                row = cur.execute(
                    "SELECT COUNT(*) FROM memories WHERE event_type=? AND "
                    "timestamp >= datetime('now', '-24 hours')", (e,)).fetchone()
                counts[e] = row[0] if row else 0
            out[rel] = counts
        # steering targets + adaptation flips for edge weighting
        steering = {}
        for row in cur.execute(
                "SELECT data FROM memories WHERE event_type='strategy_steered' "
                "AND timestamp >= datetime('now', '-24 hours')").fetchall():
            d = row[0]
            try:
                d = json.loads(d) if isinstance(d, str) else d
            except Exception:
                continue
            s = d.get("strategy")
            steering[s] = steering.get(s, 0) + 1
        out["_steering"] = steering
        con.close()
    except Exception as e:
        sys.stderr.write(f"[ecosystem_graph] enrichment skipped: {e}\n")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Map the full Azazael ecosystem through the graphing pipeline")
    ap.add_argument("--out", default=os.path.join(ROOT, "graphs", "ecosystem"))
    ap.add_argument("--no-db", action="store_true", help="skip live DB enrichment")
    args = ap.parse_args()

    outdir = os.path.abspath(args.out)
    os.makedirs(outdir, exist_ok=True)

    files = discover_files()
    rel_to_id, edges = resolve_edges(files)
    counts = {} if args.no_db else live_counts()

    # drop duplicate undirected edges, keep multiplicity for weight
    edge_map = {}
    for s, t in edges:
        key = tuple(sorted((rel_to_id[s], rel_to_id[t])))
        edge_map[key] = edge_map.get(key, 0) + 1

    nodes = []
    for i, (_, rel) in enumerate(files):
        area = rel.split("/")[1] if rel.startswith("src/") and "/" in rel else rel.split("/")[0]
        nodes.append({"id": f"n{i:04d}", "label": rel, "rel": rel,
                      "area": area, "type": "module"})

    G = nx.Graph()
    for i, n in enumerate(nodes):
        G.add_node(i, id=n["id"], label=n["label"], area=n["area"], type="module",
                   file=n["rel"])
        c = counts.get(n["rel"], {})
        G.nodes[i]["event_count"] = sum(c.values()) if isinstance(c, dict) else 0
        G.nodes[i]["count_detail"] = json.dumps(c) if isinstance(c, dict) and c else ""

    for (a, b), w in edge_map.items():
        G.add_edge(a, b, weight=w, etype=["import"])

    isolated = sum(1 for d in dict(G.degree()).values() if d == 0)
    print(f"files: {len(files)} | import edges: {len(edges)} (unique {len(edge_map)}) "
          f"| isolated: {isolated}")
    if counts.get("_steering"):
        print("live steering (24h): " + json.dumps(counts["_steering"]))

    # ---- cosmos ----
    os.makedirs(os.path.join(outdir, "cosmos"), exist_ok=True)
    deg = dict(G.degree())
    labels = [{"id": n["id"], "label": n["label"], "file": n["rel"], "type": n["area"],
               "color": area_color(n["area"]),
               "deg": deg[i], "events": G.nodes[i].get("event_count", 0)}
              for i, n in enumerate(nodes)]
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

    # ---- graph.json ----
    with open(os.path.join(outdir, "graph.json"), "w", encoding="utf-8") as f:
        json.dump({"directed": False, "multigraph": False, "graph": {},
                   "nodes": [{"id": n["id"], "label": n["label"], "type": n["area"],
                              "file": n["rel"], "events": G.nodes[i].get("event_count", 0)}
                             for i, n in enumerate(nodes)],
                   "links": [{"source": G.nodes[a]["id"], "target": G.nodes[b]["id"],
                              "weight": G.edges[a, b].get("weight", 1)}
                             for a, b in G.edges()]},
                  f, indent=1)

    # ---- SNA ----
    print("computing SNA (centrality, Louvain, k-core, cliques)...")
    results = analyze(G, seed=42)
    dump = {k: v for k, v in results.items() if k != "_internal"}
    with open(os.path.join(outdir, "sna_results.json"), "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)

    print("\n=== TOP BETWEENNESS (bridges) ===")
    for label in results["rankings"]["top_betweenness"][:12]:
        print(f"  {label}")
    print("\n=== TOP EIGENVECTOR (influence) ===")
    for label in results["rankings"]["top_eigenvector"][:12]:
        print(f"  {label}")
    print("\n=== TOP DEGREE ===")
    for label in results["rankings"]["top_degree"][:12]:
        print(f"  {label}")

    cc = results["core_clique"]
    print(f"\n=== K-CORE SHELLS (top) ===")
    for k in sorted((int(x) for x in cc["core_shells"]), reverse=True)[:5]:
        s = cc["core_shells"][str(k)]
        print(f"  {k}-core (n={s['n']}): {', '.join(s['members'][:6])}")
    print(f"  clique number: {cc['clique_number']} | maximal cliques: {cc['maximal_cliques_count']}")

    comms = results["communities"]
    print(f"\n=== LOUVAIN: {len(comms)} communities ===")
    for c in comms[:12]:
        print(f"  C{c['index']} (n={c['n']}): {', '.join(c['members'][:6])}")
    if len(comms) > 12:
        print(f"  ... +{len(comms) - 12} smaller communities")

    # ---- Gephi exports ----
    export_csv(G, nodes, os.path.join(outdir, "gephi"), results)
    int_ = results["_internal"]
    comm_of = int_["comm_of"]
    core = nx.core_number(G)
    for i in G.nodes():
        G.nodes[i]["Label"] = nodes[i]["label"]
        G.nodes[i]["Area"] = nodes[i]["area"]
        G.nodes[i]["Events24h"] = G.nodes[i].get("event_count", 0)
        G.nodes[i]["Betweenness"] = round(int_["bet"].get(i, 0), 4)
        G.nodes[i]["Eigenvector"] = round(int_["eig"].get(i, 0), 4)
        G.nodes[i]["Closeness"] = round(int_["close"].get(i, 0), 4)
        G.nodes[i]["Community"] = comm_of.get(i, "")
        G.nodes[i]["KCore"] = core.get(i, 0)
    for a, b in G.edges():
        et = G.edges[a, b].get("etype", ["link"])
        if isinstance(et, list):
            G.edges[a, b]["etype"] = "+".join(et)
        G.edges[a, b]["EdgeType"] = "import"
        G.edges[a, b]["Weight"] = G.edges[a, b].get("weight", 1)
    gml = os.path.join(outdir, "ecosystem.graphml")
    nx.write_graphml(G, gml, encoding="utf-8")
    gexf = os.path.join(outdir, "ecosystem.gexf")
    for n, d in G.nodes(data=True):
        for k, v in list(d.items()):
            if v is None:
                d[k] = ""
    for u, v, d in G.edges(data=True):
        for k, val in list(d.items()):
            if val is None:
                d[k] = ""
    nx.write_gexf(G, gexf, encoding="utf-8")
    print(f"\nWrote {gml}\nWrote {gexf}")

    # ---- Mermaid (top 60 by degree, ASCII-safe, colored by area) ----
    top = sorted(G.nodes(), key=lambda i: deg[i], reverse=True)[:60]
    keep = set(top)
    lines = ["graph TD"]
    area_defs = {}
    for i in top:
        n = nodes[i]
        ev = G.nodes[i].get("event_count", 0)
        lab = n["label"].replace("/", "_") + (f" ({ev})" if ev else "")
        lines.append(f"    {n['id']}[\"{lab}\"]")
        a = n["area"]
        if a not in area_defs:
            c = area_color(a)
            area_defs[a] = f"classDef area_{len(area_defs)} fill:#111,stroke:{c},color:{c}"
            lines.append(f"    {area_defs[a]}")
    area_to_class = {}
    for i in top:
        a = nodes[i]["area"]
        if a not in area_to_class:
            area_to_class[a] = f"area_{len(area_to_class)}"
    for i in top:
        a = nodes[i]["area"]
        lines.append(f"    class {nodes[i]['id']} {area_to_class[a]}")
    for a, b in G.edges():
        if a in keep and b in keep:
            lines.append(f"    {nodes[a]['id']} --> {nodes[b]['id']}")
    with open(os.path.join(outdir, "ecosystem.mmd"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # ---- PyVis ----
    try:
        from pyvis.network import Network
        max_deg = max(deg.values()) or 1
        net = Network(height="900px", width="100%", bgcolor="#0d1117",
                      font_color="#c9d1d9", directed=False)
        for i, n in enumerate(nodes):
            ev = G.nodes[i].get("event_count", 0)
            col = area_color(n["area"])
            # live-active modules (fired events in the last 24h) get a
            # glowing border so behavior stands out from structure
            border = col if ev else "#30363d"
            bw = 3.5 if ev else 1.0
            net.add_node(i, label=n["label"].split("/")[-1], color=col,
                         borderWidth=bw, borderWidthSelected=bw + 2,
                         size=(7 + 14 * (deg.get(i, 1) / max_deg)) * (1.25 if ev else 1.0),
                         title=f"{n['label']}<br>area {n['area']} · community "
                               f"C{comm_of.get(i, '?')} · degree {deg.get(i, 0)} · events24h {ev}")
        for a, b in G.edges():
            net.add_edge(a, b, color="#484f58", width=min(1 + G.edges[a, b].get("weight", 1) * 0.15, 4))
        net.set_options(json.dumps({
            "nodes": {"borderWidth": 1.0, "font": {"size": 11, "face": "Tahoma"}},
            "edges": {"smooth": {"type": "continuous"}},
            "physics": {"enabled": True, "stabilization": {"iterations": 250}},
            "interaction": {"hover": True, "tooltipDelay": 120},
        }))
        html = os.path.join(outdir, "ecosystem.html")
        net.show(html, notebook=False)
        _inject_legend(html, AREA_COLORS, "node color = source area · glowing border = live (events in 24h)")
        print(f"Wrote {html}")
    except Exception as e:
        print(f"PyVis skipped: {e}")

    print("\nAll artifacts under " + outdir)
    return 0


def _inject_legend(html_path: str, color_map: dict, caption: str) -> None:
    """Append a fixed-position color legend to a generated PyVis HTML."""
    swatches = "".join(
        f'<span style="display:inline-block;margin:2px 10px 2px 0;">'
        f'<span style="display:inline-block;width:12px;height:12px;'
        f'background:{c};border:1px solid #555;border-radius:2px;'
        f'margin-right:4px;vertical-align:middle;"></span>{a}</span>'
        for a, c in sorted(color_map.items()))
    legend = (
        '<div style="position:fixed;bottom:8px;left:8px;z-index:9999;'
        'background:rgba(13,17,23,0.92);border:1px solid #30363d;'
        'border-radius:6px;padding:8px 12px;font-family:Tahoma,Arial,sans-serif;'
        'font-size:11px;color:#c9d1d9;max-width:70%;">'
        f'<div style="font-weight:bold;margin-bottom:4px;">' + caption + '</div>'
        + swatches + '</div>')
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        if "</body>" in html:
            html = html.replace("</body>", legend + "</body>")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
    except Exception as e:
        sys.stderr.write(f"[ecosystem_graph] legend inject skipped: {e}\n")


if __name__ == "__main__":
    sys.exit(main())
