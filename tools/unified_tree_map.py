#!/usr/bin/env python3
"""unified_tree_map.py — Unified tree map/index for all azazael trees.

Creates a single JSON file that maps all trees, their relationships, imports,
exports, and communication channels. Like a mycelium network connecting all
trees so they're visible to each other.

Nature-inspired patterns:
- Mycelium: decentralized data transport (memorybus)
- Neural network: weighted connections (import frequency)
- Ant colony: stigmergy (indirect coordination via shared state)
- Slime mold: shortest path optimization (graph traversal)

Usage:
    python unified_tree_map.py build --root src/
    python unified_tree_map.py query "CORE"
    python unified_tree_map.py visualize
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import defaultdict, Counter
from pathlib import Path

# ── Tree definitions (from census) ────────────────────────────────────────

KNOWN_TREES = {
    "AOA", "AZAZAEL_TOOLKIT", "CLI", "CODEX", "CORE", "DAEMONS",
    "HYDRA", "INTERPRETERS", "META", "ML", "ORCHESTRATION", "PSYCHE",
    "RENDERING", "SCRIPTS", "SHARDS", "SHORTCUTS", "TOOLS", "UTILITIES",
    "eve_unleashed", "tests",
}

# Nature-inspired communication channels
CHANNELS = {
    "direct_import": "Direct Python import (tree A imports from tree B)",
    "memorybus": "Shared state via memorybus_queue (SQLite)",
    "event_bus": "Pub/sub event system (AOA/core/event_bus.py)",
    "graph_traversal": "Codegraph MCP traversal (BFS/DFS)",
    "shared_state": "Shared files/state on disk",
    "mcp_tools": "MCP tool calls (engram, pmb, codegraph)",
}


def discover_tree(path):
    """Discover a tree's structure: modules, classes, functions, exports."""
    tree = {
        "name": path.name,
        "path": str(path),
        "modules": [],
        "classes": 0,
        "functions": 0,
        "lines": 0,
        "imports_from": defaultdict(int),  # tree_name -> count
        "imported_by": defaultdict(int),   # tree_name -> count
        "exports": [],  # public symbols
        "channels": [],  # communication channels used
    }
    for dirpath, dirs, files in os.walk(path):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            fp = Path(dirpath) / f
            try:
                source = fp.read_text(encoding="utf-8", errors="replace")
                tree_obj = ast.parse(source)
            except SyntaxError as exc:
                note_failure("tools/unified_tree_map.py", exc)
                continue
            tree["modules"].append(str(fp.relative_to(path.parent)))
            tree["lines"] += source.count("\n") + 1
            for node in ast.walk(tree_obj):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    tree["functions"] += 1
                    if not node.name.startswith("_"):
                        tree["exports"].append(node.name)
                elif isinstance(node, ast.ClassDef):
                    tree["classes"] += 1
                    if not node.name.startswith("_"):
                        tree["exports"].append(node.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        mod = alias.name.split(".")[0]
                        if mod in KNOWN_TREES:
                            tree["imports_from"][mod] += 1
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        mod = node.module.split(".")[0]
                        if mod in KNOWN_TREES:
                            tree["imports_from"][mod] += 1
    # Deduplicate exports
    tree["exports"] = sorted(set(tree["exports"]))[:50]
    tree["imports_from"] = dict(tree["imports_from"])
    return tree


def build_unified_map(root):
    """Build the unified tree map."""
    root = Path(root)
    src = root / "src" if (root / "src").exists() else root

    # Discover all trees
    trees = {}
    for child in sorted(src.iterdir()):
        if child.is_dir() and child.name in KNOWN_TREES:
            trees[child.name] = discover_tree(child)

    # Build reverse imports (imported_by)
    for name, tree in trees.items():
        for imported in tree["imports_from"]:
            if imported in trees:
                trees[imported]["imported_by"][name] = trees[imported]["imported_by"].get(name, 0) + tree["imports_from"][imported]

    # Determine communication channels
    for name, tree in trees.items():
        channels = []
        if tree["imports_from"]:
            channels.append("direct_import")
        # Check for memorybus usage
        for mod in tree["modules"]:
            try:
                src_text = (src / name / mod).read_text(encoding="utf-8", errors="replace")
                if "memorybus" in src_text.lower() or "memory_bus" in src_text.lower():
                    channels.append("memorybus")
                if "event_bus" in src_text.lower() or "publish" in src_text.lower():
                    channels.append("event_bus")
            except Exception as exc:
                note_failure("tools/unified_tree_map.py", exc)
                pass
        tree["channels"] = list(set(channels))
        tree["imported_by"] = dict(tree["imported_by"])

    # Build connection matrix (nature-inspired: weighted like neural network)
    connections = []
    for name, tree in trees.items():
        for imported, count in tree["imports_from"].items():
            connections.append({
                "from": name,
                "to": imported,
                "weight": count,
                "type": "direct_import",
                "channel": "direct_import",
            })

    # Build mycelium network (all trees connected through shared channels)
    mycelium = {
        "nodes": list(trees.keys()),
        "edges": connections,
        "channels": CHANNELS,
        "hub_trees": [],  # trees that connect to many others
    }
    # Identify hub trees (connected to 3+ others)
    connection_count = Counter()
    for c in connections:
        connection_count[c["from"]] += 1
        connection_count[c["to"]] += 1
    mycelium["hub_trees"] = [t for t, c in connection_count.most_common() if c >= 3]

    # Environment awareness
    environments = {
        "windows": {"path": "C:/Azazael/azazael", "python": "3.15.0rc3", "status": "primary"},
        "ubuntu": {"path": "/home/clint/azazael", "python": "3.14.x", "status": "synced"},
        "kali": {"path": "/root/azazael", "python": "3.14.6", "status": "synced"},
    }

    # Build unified map

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    unified = {
        "schema": "unified_tree_map_v2",
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "root": str(src),
        "environments": environments,
        "totals": {
            "trees": len(trees),
            "modules": sum(t["modules"].__len__() for t in trees.values()),
            "lines": sum(t["lines"] for t in trees.values()),
            "classes": sum(t["classes"] for t in trees.values()),
            "functions": sum(t["functions"] for t in trees.values()),
            "connections": len(connections),
            "channels": len(CHANNELS),
        },
        "trees": {name: {
            "name": t["name"],
            "path": t["path"],
            "modules": len(t["modules"]),
            "classes": t["classes"],
            "functions": t["functions"],
            "lines": t["lines"],
            "imports_from": t["imports_from"],
            "imported_by": t["imported_by"],
            "exports": t["exports"],
            "channels": t["channels"],
        } for name, t in trees.items()},
        "connections": connections,
        "mycelium": mycelium,
        "nature_patterns": {
            "mycelium": "Decentralized data transport via memorybus (shared SQLite)",
            "neural_network": "Weighted connections via import frequency",
            "ant_colony": "Stigmergy via shared state files",
            "slime_mold": "Shortest path via graph traversal (BFS/Dijkstra)",
            "swarm": "Multi-agent parallel processing (rayon/goroutines)",
            "neural_plasticity": "Trees adapt connections based on usage frequency",
            "symbiosis": "Trees share resources (CORE provides paths, logging, inference to all)",
        },
        "swarm": {
            "roles": ["scout", "worker", "forager", "queen", "soldier"],
            "patterns": ["mycelium", "stigmergy", "slime_mold", "bee_colony", "neural_network", "ant_colony"],
            "environments": ["windows", "ubuntu", "kali"],
            "communication": "Hybrid: direct imports + memorybus + event bus + graph traversal + MCP tools",
        },
    }
    return unified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=["build", "query", "visualize", "stats"])
    parser.add_argument("--root", default="C:/Azazael/azazael")
    parser.add_argument("--output", default="C:/Azazael/azazael/docs/UNIFIED_TREE_MAP.json")
    parser.add_argument("--tree", help="Tree name to query")
    args = parser.parse_args()

    if args.cmd == "build":
        unified = build_unified_map(args.root)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(unified, f, indent=2)
        print(f"Built unified tree map: {unified['totals']['trees']} trees, "
              f"{unified['totals']['modules']} modules, {unified['totals']['lines']} lines, "
              f"{unified['totals']['connections']} connections")
        print(f"Hub trees: {unified['mycelium']['hub_trees']}")
        print(f"Saved to {args.output}")

    elif args.cmd == "query":
        unified = json.load(open(args.output, encoding="utf-8"))
        tree_name = args.tree or "CORE"
        tree = unified["trees"].get(tree_name)
        if not tree:
            print(f"Tree not found: {tree_name}")
            return
        print(f"\n=== {tree_name} ===")
        print(f"  Modules: {tree['modules']}, Lines: {tree['lines']}")
        print(f"  Classes: {tree['classes']}, Functions: {tree['functions']}")
        print(f"  Imports from: {tree['imports_from']}")
        print(f"  Imported by: {tree['imported_by']}")
        print(f"  Channels: {tree['channels']}")
        print(f"  Exports: {tree['exports'][:10]}...")
        # Show connections
        conns = [c for c in unified["connections"] if c["from"] == tree_name or c["to"] == tree_name]
        print(f"  Connections: {len(conns)}")
        for c in conns[:10]:
            print(f"    {c['from']} -> {c['to']} (weight={c['weight']})")

    elif args.cmd == "stats":
        unified = json.load(open(args.output, encoding="utf-8"))
        t = unified["totals"]
        print(f"Trees: {t['trees']}")
        print(f"Modules: {t['modules']}")
        print(f"Lines: {t['lines']}")
        print(f"Classes: {t['classes']}")
        print(f"Functions: {t['functions']}")
        print(f"Connections: {t['connections']}")
        print(f"Channels: {t['channels']}")
        print(f"Hub trees: {unified['mycelium']['hub_trees']}")
        print(f"\nNature patterns:")
        for k, v in unified["nature_patterns"].items():
            print(f"  {k}: {v}")

    elif args.cmd == "visualize":
        unified = json.load(open(args.output, encoding="utf-8"))
        # Generate a simple text visualization
        print("\n" + "=" * 60)
        print("UNIFIED TREE MAP (Mycelium Network)")
        print("=" * 60)
        for name, tree in sorted(unified["trees"].items()):
            imports = ", ".join(f"{k}({v})" for k, v in tree["imports_from"].items()) or "none"
            channels = ", ".join(tree["channels"]) or "none"
            print(f"\n  {name} [{tree['modules']}m, {tree['lines']}L]")
            print(f"    imports: {imports}")
            print(f"    channels: {channels}")
            print(f"    exports: {len(tree['exports'])} symbols")
        print("\n" + "=" * 60)
        print("HUB TREES (connected to 3+ others):")
        for h in unified["mycelium"]["hub_trees"]:
            print(f"  - {h}")
        print("=" * 60)


if __name__ == "__main__":
    main()
