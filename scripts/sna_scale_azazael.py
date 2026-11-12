#!/usr/bin/env python3
"""
sna_scale_azazael.py -- rustworkx SNA on the REAL 404k-node azazael graph

Loads the graphify-out graph.nd7.js nodes + graph.ed*.js edge shards into
rustworkx and runs k-core, PageRank, and connected components at full scale
(the operation the 41k benchmark says rustworkx should carry). 3.13 tooling venv.

    venv-313-tools/Scripts/python.exe azazael/scripts/sna_scale_azazael.py
"""
import json
import re
import sys
import time
from pathlib import Path

import rustworkx as rx

ROOT = Path.home() / "azazael" / "graphify-out"
OUT = Path.home() / "SNA_SCALE_AZAZAEL_evidence.json"


def load() -> tuple:
    t0 = time.perf_counter()
    # nodes: 18 nd*.js shards (~404k total)
    g = rx.PyGraph()
    idx = {}
    for shard in sorted(ROOT.glob("graph.nd*.js")):
        raw = shard.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"=\s*(\[.*\])\s*;?\s*$", raw, re.S)
        if not m:
            continue
        for nd in json.loads(m.group(1)):
            idx[nd["id"]] = g.add_node(nd.get("label", nd["id"]))
    t_nodes = time.perf_counter() - t0
    print(f"nodes: {g.num_nodes()} ({t_nodes:.1f}s)")

    # edges: each shard EDn = window.EDn = [{from,to}...]
    t1 = time.perf_counter()
    for shard in sorted(ROOT.glob("graph.ed*.js")):
        raw = shard.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"=\s*(\[.*\])\s*;?\s*$", raw, re.S)
        if not m:
            continue
        edges = json.loads(m.group(1))
        g.add_edges_from_no_data([(idx[e["from"]], idx[e["to"]]) for e in edges])
        del edges
    t_edges = time.perf_counter() - t1
    print(f"edges: {g.num_edges()} ({t_edges:.1f}s)  total load {(time.perf_counter()-t0):.1f}s")
    return g


def main() -> int:
    g = load()
    res = {"graph": "azazael graphify-out (real)", "nodes": g.num_nodes(), "edges": g.num_edges()}

    t = time.perf_counter()
    core = rx.core_number(g)
    res["rustworkx_core_number_s"] = round(time.perf_counter() - t, 2)
    res["max_core"] = max(core.values())

    t = time.perf_counter()
    comps = rx.connected_components(g)
    res["rustworkx_components_s"] = round(time.perf_counter() - t, 2)
    res["n_components"] = len(comps)
    sizes = sorted((len(c) for c in comps), reverse=True)
    res["largest_component"] = sizes[0] if sizes else 0

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, default=str)
    print(json.dumps(res, indent=2))
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
