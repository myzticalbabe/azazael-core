"""
bcc_pyvis_network.py — interactive pyvis network of the BCC two-sphere firewall.

Rebuilds the people-entity graph with the SAME loaders as bcc_network_sna.py
(cosmos labels/links + board clique + typed person->person intimacy layer), then
emits a browser-interactive HTML graph: hover, drag, community-colored,
diamond = board sphere, star = BCC, sized by degree.

Run:  C:/Azazael/.venvs/py315t/Scripts/python.exe azazael/scripts/bcc_pyvis_network.py
      (deps belong in py315t; .venvs/py314m is the sanctioned 3.14 fallback)
      [--cosmos-dir Desktop/AZAZAEL_COSMOS_VIEWER_bcc/cosmos]
      [--person-edges azazael/scripts/bcc_person_edges.json]
Out:  BCC_TWO_SPHERE_NETWORK.html
"""
import argparse
import json
import os
import sys

import networkx as nx
from pyvis.network import Network

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bcc_network_sna import load_graph, merge_person_edges  # noqa: E402

COMMUNITY_COLORS = [
    "#58a6ff",  # C0 ops/oversight - blue
    "#f78166",  # C1 board sphere - orange
    "#d2a8ff",  # C2 - purple
    "#3fb950",  # green
    "#ffa657",  # amber
]

BOARD_SPHERE_HINTS = ("Edward Jones", "TPUC", "Robinson", "Vanguard", "GSK",
                      "Central Tire", "Convention & Visitors", "Alvernia",
                      "Furniture Soup", "Council on Chemical Abuse", "COCA",
                      "Goodman", "Orlando")


def is_board_sphere(name: str) -> bool:
    return any(h.lower() in name.lower() for h in BOARD_SPHERE_HINTS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cosmos-dir",
                    default="Desktop/AZAZAEL_COSMOS_VIEWER_bcc/cosmos")
    ap.add_argument("--person-edges", default="azazael/scripts/bcc_person_edges.json")
    args = ap.parse_args()

    G, nodes = load_graph(args.cosmos_dir)
    added = merge_person_edges(G, nodes, args.person_edges)
    id_to_label = {i: n["label"] for i, n in enumerate(nodes)}

    comms = list(nx.community.louvain_communities(G, seed=42))
    node_comm = {}
    for cid, members in enumerate(comms):
        for m in members:
            node_comm[m] = cid

    deg = dict(G.degree())
    max_deg = max(deg.values()) if deg else 1

    g = Network(height="900px", width="100%", bgcolor="#0d1117",
                font_color="#c9d1d9", directed=False)

    for i, n in enumerate(nodes):
        label = n["label"]
        cid = node_comm.get(i, 0)
        color = COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)]
        size = 8 + 22 * (deg.get(i, 1) / max_deg)
        if is_board_sphere(label):
            shape = "diamond"
        elif "BCC)" in label or label.startswith("BCC"):
            shape = "star"
        else:
            shape = "dot"
        g.add_node(i, label=label, color=color, size=size, shape=shape,
                   title=f"{label}<br>community C{cid} · degree {deg.get(i, 0)}")

    for a, b in G.edges():
        g.add_edge(a, b, color="#30363d", width=1.2)

    g.set_options(json.dumps({
        "nodes": {"borderWidth": 1.5, "font": {"size": 13, "face": "Tahoma"},
                  "shadow": {"enabled": True, "size": 6}},
        "edges": {"smooth": {"type": "continuous"}, "color": {"inherit": False}},
        "physics": {"enabled": True,
                    "stabilization": {"iterations": 300, "updateInterval": 25}},
        "interaction": {"hover": True, "tooltipDelay": 120, "dragNodes": True},
    }))
    g.show("BCC_TWO_SPHERE_NETWORK.html", notebook=False)
    print(f"wrote BCC_TWO_SPHERE_NETWORK.html "
          f"({G.number_of_nodes()} nodes, {G.number_of_edges()} edges, "
          f"+{len(added)} person-layer edges)")


if __name__ == "__main__":
    main()