#!/usr/bin/env python3
"""build_tree_network_3d.py — Build 3D visualization data for unified tree map.

Creates a 3D force-graph compatible JSON file showing all trees as nodes
and their import connections as edges, colored by tree type.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

unified = json.load(open("C:/Azazael/azazael/docs/UNIFIED_TREE_MAP.json", encoding="utf-8"))
trees = unified["trees"]
connections = unified["connections"]

# Tree color palette (nature-inspired)
TREE_COLORS = {
    "CORE": "#7df9ff", "CODEX": "#ffd479", "ORCHESTRATION": "#ff6b6b",
    "META": "#a29bfe", "UTILITIES": "#4ecdc4", "ML": "#96ceb4",
    "HYDRA": "#fd79a8", "TOOLS": "#6c5ce7", "SCRIPTS": "#00b894",
    "eve_unleashed": "#e17055", "PSYCHE": "#0984e3", "INTERPRETERS": "#636e72",
    "DAEMONS": "#b2bec3", "SHARDS": "#dfe6e9", "AOA": "#ffeaa7",
    "AZAZAEL_TOOLKIT": "#81ecec", "CLI": "#55efc4", "RENDERING": "#fdcb6e",
    "SHORTCUTS": "#ffeaa7", "tests": "#a29bfe",
}

# Build nodes (one per tree)
nodes = []
for name, tree in trees.items():
    nodes.append({
        "id": name,
        "label": name,
        "val": tree["lines"] / 1000,  # size by lines
        "group": name,
        "modules": tree["modules"],
        "classes": tree["classes"],
        "functions": tree["functions"],
        "lines": tree["lines"],
        "imports_from": tree["imports_from"],
        "imported_by": tree["imported_by"],
        "channels": tree["channels"],
        "color": TREE_COLORS.get(name, "#888888"),
    })

# Build links (connections between trees)
links = []
for c in connections:
    links.append({
        "source": c["from"],
        "target": c["to"],
        "weight": c["weight"],
        "relation": c["type"],
    })

result = {
    "nodes": nodes,
    "links": links,
    "node_count": len(nodes),
    "link_count": len(links),
    "communities": list(trees.keys()),
    "format": "tree-network-3d",
}

out_path = "C:/Users/clint/Desktop/YGGDRASIL_RENDERS_NEW/tree_network_3d.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)
print(f"Built tree network: {len(nodes)} trees, {len(links)} connections")
print(f"Saved to {out_path}")
