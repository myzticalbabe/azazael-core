#!/usr/bin/env python3
"""Convert graphify graph.json to GEXF (Gephi) and GraphML formats."""
import json
import networkx as nx
import os
import sys

# numpy 2.x removed np.float_ (networkx 2.8.8 GEXF/GraphML writers still use it)
try:
    import numpy as _np
    if not hasattr(_np, "float_"):
        _np.float_ = _np.float64
except ImportError:
    pass

graph_path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/c/Azazael/azazael/graphify-out/graph.json"
out_dir = sys.argv[2] if len(sys.argv) > 2 else "/mnt/c/Azazael/azazael/graphify-out"

print(f"Loading graph from {graph_path} ...")
with open(graph_path, encoding="utf-8") as f:
    data = json.load(f)

print(f"Nodes: {len(data.get('nodes', []))}, Edges: {len(data.get('links', []))}")

G = nx.node_link_graph(data)
print(f"NetworkX graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# GEXF doesn't allow None attribute values — replace with empty strings
def sanitize_attr(attr_val):
    if attr_val is None:
        return ""
    return attr_val

def sanitize_dict(d):
    if d is None:
        return {}
    return {k: sanitize_attr(v) for k, v in d.items() if v is not None}

for n, d in G.nodes(data=True):
    for k, v in list(d.items()):
        if v is None:
            d[k] = ""
for u, v, d in G.edges(data=True):
    for k, val in list(d.items()):
        if val is None:
            d[k] = ""

# GEXF for Gephi
gexf_path = os.path.join(out_dir, "graph.gexf")
print(f"Writing GEXF -> {gexf_path} ...")
nx.write_gexf(G, gexf_path, encoding="utf-8")
print(f"  GEXF: {os.path.getsize(gexf_path):,} bytes")

# GraphML (may fail on nested dict attributes; non-fatal)
gml_path = os.path.join(out_dir, "graph.graphml")
print(f"Writing GraphML -> {gml_path} ...")
try:
    nx.write_graphml(G, gml_path)
    print(f"  GraphML: {os.path.getsize(gml_path):,} bytes")
except Exception as e:
    print(f"  GraphML skipped (lxml/dict attr issue on 3.14t): {e}")

# Standalone copies
desktop_gexf = os.path.join(out_dir, "azazael-graph.gexf")
print(f"Copying to {desktop_gexf} ...")
nx.write_gexf(G, desktop_gexf, encoding="utf-8")
print(f"  Done: {os.path.getsize(desktop_gexf):,} bytes")

print("All graph formats exported.")
