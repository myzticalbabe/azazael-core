#!/usr/bin/env python3
"""bcc_vault_sna.py — Social network analysis of the BCC investigation document corpus.

Builds the document network from the Obsidian vault connector notes (one node per
corpus doc, edges = [[wikilink]] cross-references) and computes:
  * in/out degree (hub = most-cited doc, source = most-linking doc)
  * betweenness / eigenvector / closeness centrality (bridges + influence)
  * Louvain community detection (networkx built-in, seedable)

Optional --exclude-index drops the vault 00_INDEX node (it artificially links
everything; removing it shows the true corpus articulation points).

Usage:
  python bcc_vault_sna.py --vault "C:/Users/clint/Documents/BCC_Investigation_Vault"
  python bcc_vault_sna.py --exclude-index --out results.json

Artifacts: vault connector notes (build_obsidian_vault.py) -> this SNA.
Companion (people-entity SNA): bcc_network_sna.py.
"""
import argparse
import json
import os
import re
import sys
from glob import glob

import networkx as nx
from networkx.algorithms.community import louvain_communities


def load_vault_graph(vault: str, exclude_index: bool = False):
    notes = {}
    for n in glob(os.path.join(vault, "*.md")):
        stem = os.path.splitext(os.path.basename(n))[0]
        src = open(n, encoding="utf-8").read()
        out = [m for m in re.findall(r"\[\[([^\]|#]+)", src)]
        cat = re.search(r"^category: (.+)$", src, re.M)
        title = re.search(r'^title: "?(.+?)"?$', src, re.M)
        if exclude_index and stem == "00_INDEX":
            continue
        notes[stem] = {"out": out, "cat": cat.group(1) if cat else "?",
                       "title": title.group(1) if title else stem}

    G = nx.DiGraph()
    for s, info in notes.items():
        G.add_node(s, cat=info["cat"], title=info["title"])
    for s, info in notes.items():
        for t in info["out"]:
            if t in notes and t != s:
                G.add_edge(s, t)
    return G, notes


def analyze(G: nx.DiGraph, seed: int = 42) -> dict:
    U = G.to_undirected()
    indeg = dict(G.in_degree())
    outdeg = dict(G.out_degree())
    bet = nx.betweenness_centrality(U)
    eig = nx.eigenvector_centrality(U, max_iter=1000)
    close = nx.closeness_centrality(U)
    comms = sorted(louvain_communities(U, seed=seed), key=len, reverse=True)

    communities = []
    for ci, c in enumerate(comms):
        members = sorted(c)
        cats = {}
        for m in members:
            cats[G.nodes[m]["cat"]] = cats.get(G.nodes[m]["cat"], 0) + 1
        communities.append({"index": ci, "n": len(c), "cats": cats, "members": members})

    comps = sorted(nx.connected_components(U), key=len, reverse=True)
    giant = U.subgraph(comps[0]) if comps else U
    diam = nx.diameter(giant)
    avg_path = round(nx.average_shortest_path_length(giant), 3) if giant.number_of_nodes() > 1 else 0.0
    return {
        "network": {
            "nodes": U.number_of_nodes(),
            "edges": U.number_of_edges(),
            "directed_edges": G.number_of_edges(),
            "components": nx.number_connected_components(U),
            "density": round(nx.density(U), 4),
            "diameter": diam,
            "avg_shortest_path": avg_path,
        },
        "centrality": {
            "in_degree": {G.nodes[i]["title"]: v for i, v in indeg.items()},
            "out_degree": {G.nodes[i]["title"]: v for i, v in outdeg.items()},
            "betweenness": {G.nodes[i]["title"]: round(v, 4) for i, v in bet.items()},
            "eigenvector": {G.nodes[i]["title"]: round(v, 4) for i, v in eig.items()},
            "closeness": {G.nodes[i]["title"]: round(v, 4) for i, v in close.items()},
        },
        "rankings": {
            "top_in_degree": [G.nodes[i]["title"] for i, _ in sorted(indeg.items(), key=lambda x: -x[1])[:12]],
            "top_betweenness": [G.nodes[i]["title"] for i, _ in sorted(bet.items(), key=lambda x: -x[1])[:8]],
            "top_eigenvector": [G.nodes[i]["title"] for i, _ in sorted(eig.items(), key=lambda x: -x[1])[:8]],
        },
        "communities": communities,
        "_internal": {"indeg": indeg, "bet": bet, "eig": eig},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="SNA on the BCC investigation document corpus")
    ap.add_argument("--vault", default=r"C:\Users\clint\Documents\BCC_Investigation_Vault")
    ap.add_argument("--exclude-index", action="store_true",
                    help="drop 00_INDEX node (it links everything and dominates betweenness)")
    ap.add_argument("--out", help="write JSON results dump")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    G, notes = load_vault_graph(args.vault, args.exclude_index)
    res = analyze(G, seed=args.seed)
    n = res["network"]
    print(f"Corpus network: {n['nodes']} docs, {n['edges']} undirected links, "
          f"{n['components']} components, diam {n['diameter']}, avg path {n['avg_shortest_path']}")
    print("\n=== TOP IN-DEGREE (most-cited hubs) ===")
    for t in res["rankings"]["top_in_degree"]:
        print(f"  {t}")
    print("\n=== TOP BETWEENNESS (bridges) ===")
    for t in res["rankings"]["top_betweenness"]:
        print(f"  {t}")
    print("\n=== TOP EIGENVECTOR (influence) ===")
    for t in res["rankings"]["top_eigenvector"]:
        print(f"  {t}")
    print(f"\n=== LOUVAIN: {len(res['communities'])} communities ===")
    for c in res["communities"]:
        print(f"  C{c['index']} (n={c['n']}, {c['cats']})")
        for m in c["members"]:
            print(f"      - {m}")

    if args.out:
        dump = {k: v for k, v in res.items() if k != "_internal"}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2)
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())