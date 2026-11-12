#!/usr/bin/env python3
"""graph_rag.py — GraphRAG: AST-derived knowledge graph retrieval for codebases.

Combines structural graph traversal (codegraph) with semantic embedding search
to provide more precise code retrieval than chunk-based embedding alone.

Architecture:
  1. Symbol-level indexing: extract functions/classes/modules from AST
  2. Graph-aware retrieval: traverse dependency graph from query seeds
  3. Bidirectional expansion: find paths in both directions
  4. Context assembly: gather all related code symbols

Usage:
    python graph_rag.py build --root src/          # Build symbol graph
    python graph_rag.py search "how does X work"     # Graph-aware search
    python graph_rag.py stats                        # Show graph stats
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path

# ── Symbol extraction ──────────────────────────────────────────────────────

def extract_symbols(filepath):
    """Extract functions, classes, and imports from a Python file via AST."""
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except SyntaxError as exc:
        note_failure("tools/graph_rag.py", exc)
        return None

    symbols = {
        "path": str(filepath),
        "functions": [],
        "classes": [],
        "imports": [],
        "calls": [],
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            symbols["functions"].append({
                "name": node.name,
                "line": node.lineno,
                "args": [a.arg for a in node.args.args],
                "docstring": ast.get_docstring(node) or "",
            })
        elif isinstance(node, ast.ClassDef):
            symbols["classes"].append({
                "name": node.name,
                "line": node.lineno,
                "bases": [ast.unparse(b) if hasattr(ast, "unparse") else str(b) for b in node.bases],
                "docstring": ast.get_docstring(node) or "",
            })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                symbols["imports"].append(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                symbols["calls"].append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                symbols["calls"].append(node.func.attr)

    return symbols


def build_symbol_graph(root):
    """Build a symbol-level dependency graph from a codebase."""
    root = Path(root)
    files = []
    for dirpath, dirs, filenames in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for f in filenames:
            if f.endswith(".py"):
                files.append(Path(dirpath) / f)

    graph = {
        "nodes": {},  # symbol_id -> {type, name, file, line, ...}
        "edges": defaultdict(list),  # symbol_id -> [target_symbol_id, ...]
        "files": {},
    }

    # First pass: collect all symbols
    file_symbols = {}
    for fp in files:
        syms = extract_symbols(fp)
        if syms is None:
            continue
        file_symbols[str(fp)] = syms
        graph["files"][str(fp)] = {
            "functions": len(syms["functions"]),
            "classes": len(syms["classes"]),
            "imports": len(syms["imports"]),
        }

        # Register function/class symbols
        for func in syms["functions"]:
            sid = f"{fp}::{func['name']}"
            graph["nodes"][sid] = {
                "type": "function",
                "name": func["name"],
                "file": str(fp),
                "line": func["line"],
                "args": func["args"],
                "docstring": func["docstring"][:200],
            }
        for cls in syms["classes"]:
            sid = f"{fp}::{cls['name']}"
            graph["nodes"][sid] = {
                "type": "class",
                "name": cls["name"],
                "file": str(fp),
                "line": cls["line"],
                "bases": cls["bases"],
                "docstring": cls["docstring"][:200],
            }

    # Second pass: build edges (calls, imports)
    for fp, syms in file_symbols.items():
        for call in syms["calls"]:
            # Find the caller (nearest enclosing function/class)
            # For simplicity, link file-level to called symbols
            caller_id = f"{fp}::__module__"
            if caller_id not in graph["nodes"]:
                graph["nodes"][caller_id] = {
                    "type": "module",
                    "name": Path(fp).name,
                    "file": fp,
                    "line": 1,
                }
            # Try to resolve call to a known symbol
            for target_sid, target_info in graph["nodes"].items():
                if target_info["name"] == call and target_info["file"] != fp:
                    graph["edges"][caller_id].append(target_sid)
                    break

        # Import edges: file imports module
        for imp in syms["imports"]:
            for target_fp in file_symbols:
                if imp.replace(".", "/") in target_fp or Path(target_fp).stem == imp.split(".")[-1]:
                    target_id = f"{target_fp}::__module__"
                    if target_id in graph["nodes"]:
                        caller_id = f"{fp}::__module__"
                        if caller_id not in graph["nodes"]:
                            graph["nodes"][caller_id] = {
                                "type": "module",
                                "name": Path(fp).name,
                                "file": fp,
                                "line": 1,
                            }
                        graph["edges"][caller_id].append(target_id)
                    break

    return graph


def search_graph(graph, query, top_k=10, bidirectional=True):
    """Search the symbol graph using bidirectional expansion from query seeds."""
    query_lower = query.lower()
    query_tokens = set(query_lower.split())

    # Find seed nodes (name matches query tokens)
    seeds = []
    for sid, info in graph["nodes"].items():
        name_lower = info["name"].lower()
        # Match if any query token is in the symbol name
        if any(tok in name_lower for tok in query_tokens if len(tok) > 2):
            seeds.append(sid)
        # Also match docstrings
        elif any(tok in info.get("docstring", "").lower() for tok in query_tokens if len(tok) > 3):
            seeds.append(sid)

    if not seeds:
        # Fallback: match file paths
        for sid, info in graph["nodes"].items():
            if any(tok in info["file"].lower() for tok in query_tokens if len(tok) > 3):
                seeds.append(sid)
                if len(seeds) >= 20:
                    break

    # Bidirectional BFS from seeds
    visited = set(seeds)
    queue = deque([(s, 0) for s in seeds[:20]])
    results = []


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    while queue and len(results) < top_k * 3:
        sid, depth = queue.popleft()
        if sid not in graph["nodes"]:
            continue
        info = graph["nodes"][sid]
        results.append({
            "symbol": sid,
            "type": info["type"],
            "name": info["name"],
            "file": info["file"],
            "line": info.get("line", 0),
            "docstring": info.get("docstring", "")[:100],
            "depth": depth,
            "score": 1.0 / (depth + 1),
        })

        if depth >= 2:
            continue

        # Traverse edges (both directions if bidirectional)
        neighbors = list(graph["edges"].get(sid, []))
        if bidirectional:
            # Find in-edges (nodes that point to this one)
            for src, targets in graph["edges"].items():
                if sid in targets and src not in visited:
                    neighbors.append(src)

        for nb in neighbors:
            if nb not in visited:
                visited.add(nb)
                queue.append((nb, depth + 1))

    # Sort by score, then by type (classes/functions before modules)
    type_priority = {"class": 0, "function": 1, "module": 2}
    results.sort(key=lambda r: (-r["score"], type_priority.get(r["type"], 3)))

    return results[:top_k]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    p_build = sub.add_parser("build", help="Build symbol graph")
    p_build.add_argument("--root", default="src", help="Root directory")
    p_build.add_argument("--output", default="graph_rag_index.json", help="Output file")

    p_search = sub.add_parser("search", help="Graph-aware search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--index", default="graph_rag_index.json", help="Index file")
    p_search.add_argument("--top-k", type=int, default=10, help="Results")

    p_stats = sub.add_parser("stats", help="Show graph stats")

    args = parser.parse_args()

    if args.cmd == "build":
        graph = build_symbol_graph(args.root)
        # Convert defaultdict to regular dict for JSON
        graph["edges"] = {k: v for k, v in graph["edges"].items()}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2)
        print(f"Built symbol graph: {len(graph['nodes'])} nodes, "
              f"{sum(len(v) for v in graph['edges'].values())} edges, "
              f"{len(graph['files'])} files")
        print(f"Saved to {args.output}")

    elif args.cmd == "search":
        if not Path(args.index).exists():
            print(f"Index not found: {args.index}. Run 'build' first.")
            sys.exit(1)
        with open(args.index, encoding="utf-8") as f:
            graph = json.load(f)
        graph["edges"] = {k: v for k, v in graph["edges"].items()}
        results = search_graph(graph, args.query, top_k=args.top_k)
        print(f"\nTop {len(results)} results for: {args.query}\n")
        for r in results:
            print(f"  [{r['score']:.2f}] {r['type']}: {r['name']} ({r['file']}:{r['line']})")
            if r["docstring"]:
                print(f"         {r['docstring'][:80]}")
            print()

    elif args.cmd == "stats":
        if not Path("graph_rag_index.json").exists():
            print("No index found. Run 'build' first.")
            sys.exit(1)
        with open("graph_rag_index.json", encoding="utf-8") as f:
            graph = json.load(f)
        print(f"Nodes: {len(graph['nodes'])}")
        print(f"Edges: {sum(len(v) for v in graph['edges'].values())}")
        print(f"Files: {len(graph['files'])}")
        types = defaultdict(int)
        for n in graph["nodes"].values():
            types[n["type"]] += 1
        for t, c in sorted(types.items()):
            print(f"  {t}: {c}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
