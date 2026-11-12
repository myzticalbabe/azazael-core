#!/usr/bin/env python3
"""
bench_rustworkx_vs_networkx.py -- rustworkx vs networkx on a 41k-node scale-free graph

Benchmarks k-core, PageRank, and single-source shortest paths on a Barabasi-Albert
graph (the same scale-free topology class as the Kali/azazael cosmos graphs) with
rustworkx (Rust) vs networkx (pure Python). Run in the 3.13 tooling venv.

    venv-313-tools/Scripts/python.exe azazael/scripts/bench_rustworkx_vs_networkx.py

Accepts --nd7 <file.js> to run the same battery against a real graphify ND7 dump.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import networkx as nx
try:
    import rustworkx as rx
except ImportError as _dep_exc:
    raise SystemExit("bench_rustworkx_vs_networkx.py needs rustworkx (3.13 tooling venv per docstring): " + str(_dep_exc)) from _dep_exc

OUT = Path.home() / "RUSTWORKX_BENCH_evidence.json"


def build_synthetic(n: int = 41_000, m: int = 3) -> nx.Graph:
    t0 = time.perf_counter()
    g = nx.barabasi_albert_graph(n, m, seed=7)
    return g, time.perf_counter() - t0


def load_nd7(path: str):
    """Load a graphify-out graph.nd7.js (window.ND7 = [...]) into a networkx graph."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"=\s*(\[.*\])\s*;?\s*$", raw, re.S)
    if not m:
        raise SystemExit("cannot parse ND7")
    nodes = json.loads(m.group(1))
    g = nx.Graph()
    for nd in nodes:
        g.add_node(nd["id"], label=nd.get("label", nd["id"]))
    for nd in nodes:
        for nb in nd.get("neighbors", []):
            g.add_edge(nd["id"], nb)
    return g


def bench(name: str, g: nx.Graph) -> dict:
    n, e = g.number_of_nodes(), g.number_of_edges()
    print(f"=== {name}: {n} nodes / {e} edges ===")
    res = {"graph": name, "nodes": n, "edges": e, "results": {}}

    # k-core (max core number)
    t = time.perf_counter()
    nx_core = nx.core_number(g)
    res["results"]["networkx_core_number_s"] = round(time.perf_counter() - t, 3)
    res["results"]["networkx_max_core"] = max(nx_core.values())

    rxg = rx.PyGraph()
    idx = {nd: rxg.add_node(nd) for nd in g.nodes()}
    rxg.add_edges_from_no_data([(idx[u], idx[v]) for u, v in g.edges()])
    t = time.perf_counter()
    rx_core = rx.core_number(rxg)
    res["results"]["rustworkx_core_number_s"] = round(time.perf_counter() - t, 3)
    res["results"]["rustworkx_max_core"] = max(rx_core.values())

    # PageRank
    t = time.perf_counter()
    nx.pagerank(g, alpha=0.85)
    res["results"]["networkx_pagerank_s"] = round(time.perf_counter() - t, 3)
    rxd = rx.PyDiGraph()
    idxd = {nd: rxd.add_node(nd) for nd in g.nodes()}
    rxd.add_edges_from_no_data(
        [(idxd[u], idxd[v]) for u, v in g.edges()]
        + [(idxd[v], idxd[u]) for u, v in g.edges()]
    )
    t = time.perf_counter()
    rx.pagerank(rxd, alpha=0.85)
    res["results"]["rustworkx_pagerank_s"] = round(time.perf_counter() - t, 3)

    # Single-source shortest paths from 5 sampled nodes
    sources = list(g.nodes())[:5]
    t = time.perf_counter()
    for s in sources:
        nx.single_source_shortest_path_length(g, s)
    res["results"]["networkx_sssp_5src_s"] = round(time.perf_counter() - t, 3)
    t = time.perf_counter()
    for s in sources:
        rx.dijkstra_shortest_path_lengths(rxg, idx[s], edge_cost_fn=lambda _: 1.0)
    res["results"]["rustworkx_sssp_5src_s"] = round(time.perf_counter() - t, 3)

    res["results"]["core_speedup_x"] = round(
        res["results"]["networkx_core_number_s"]
        / max(res["results"]["rustworkx_core_number_s"], 1e-9),
        1,
    )
    res["results"]["pagerank_speedup_x"] = round(
        res["results"]["networkx_pagerank_s"]
        / max(res["results"]["rustworkx_pagerank_s"], 1e-9),
        1,
    )
    res["results"]["sssp_speedup_x"] = round(
        res["results"]["networkx_sssp_5src_s"]
        / max(res["results"]["rustworkx_sssp_5src_s"], 1e-9),
        1,
    )
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nd7")
    args = ap.parse_args()

    if args.nd7:
        g, name = load_nd7(args.nd7), f"real ND7 {Path(args.nd7).name}"
    else:
        g, build_s = build_synthetic()
        name = "synthetic BA 41k (scale-free, approximates cosmos topology)"
        print(f"build: {build_s:.2f}s")

    results = bench(name, g)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(json.dumps(results["results"], indent=2))
    print(f"Evidence: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
