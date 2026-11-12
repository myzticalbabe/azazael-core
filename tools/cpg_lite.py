#!/usr/bin/env python3
"""cpg_lite.py — Code Property Graph (lite) for Python codebases.

Extends AST analysis with control-flow and data-flow edges, complementing
the import-graph in yggdrasil_census.py and the symbol-graph in graph_rag.py.

Graph layers:
  1. AST layer: functions, classes, statements (from ast module)
  2. CFG layer: control flow (if/for/while/try/with blocks, branches)
  3. PDG layer: data flow (variable definitions and uses)
  4. Call graph: function/method calls

Usage:
    python cpg_lite.py analyze --file src/CORE/worm_registry.py
    python cpg_lite.py stats --root src/
    python cpg_lite.py find-pattern --pattern "bare_except" --root src/
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path

# ── CPG node types ────────────────────────────────────────────────────────

NODE_TYPES = {
    "module": "Module/file level",
    "function": "Function definition",
    "class": "Class definition",
    "statement": "Executable statement",
    "control": "Control flow node (if/for/while/try)",
    "variable": "Variable definition/use",
    "call": "Function/method call",
    "import": "Import statement",
}

EDGE_TYPES = {
    "contains": "Parent contains child",
    "calls": "Function calls function",
    "data_flow": "Variable definition flows to use",
    "control_flow": "Control flow edge",
    "import": "Module imports module",
}


def analyze_file(filepath):
    """Build a CPG for a single Python file."""
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"error": str(e), "path": str(filepath)}

    nodes = []
    edges = []
    node_id = 0
    id_map = {}

    def new_node(ntype, label, line, extra=None):
        nonlocal node_id
        nid = f"n{node_id}"
        node_id += 1
        n = {"id": nid, "type": ntype, "label": label, "line": line}
        if extra:
            n.update(extra)
        nodes.append(n)
        return nid

    # Module node
    mod_id = new_node("module", Path(filepath).name, 1)

    # Track variable definitions for data flow
    var_defs = defaultdict(list)  # var_name -> [node_id, ...]
    var_uses = defaultdict(list)

    def visit(node, parent_id=None):
        nonlocal node_id
        if parent_id is None:
            parent_id = mod_id

        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            fid = new_node("function", node.name, node.lineno,
                           {"args": [a.arg for a in node.args.args]})
            edges.append({"from": parent_id, "to": fid, "type": "contains"})
            for stmt in node.body:
                visit(stmt, fid)
            return

        if isinstance(node, ast.ClassDef):
            cid = new_node("class", node.name, node.lineno,
                           {"bases": [ast.unparse(b) if hasattr(ast, "unparse") else str(b) for b in node.bases]})
            edges.append({"from": parent_id, "to": cid, "type": "contains"})
            for stmt in node.body:
                visit(stmt, cid)
            return

        if isinstance(node, ast.If):
            ctrl_id = new_node("control", "if", node.lineno)
            edges.append({"from": parent_id, "to": ctrl_id, "type": "contains"})
            # Test expression
            test_id = new_node("statement", ast.unparse(node.test) if hasattr(ast, "unparse") else "test", node.lineno)
            edges.append({"from": ctrl_id, "to": test_id, "type": "control_flow"})
            for stmt in node.body:
                visit(stmt, ctrl_id)
            for stmt in node.orelse:
                visit(stmt, ctrl_id)
            return

        if isinstance(node, (ast.For, ast.While)):
            ctrl_id = new_node("control", type(node).__name__.lower(), node.lineno)
            edges.append({"from": parent_id, "to": ctrl_id, "type": "contains"})
            for stmt in node.body:
                visit(stmt, ctrl_id)
            for stmt in node.orelse:
                visit(stmt, ctrl_id)
            return

        if isinstance(node, ast.Try):
            ctrl_id = new_node("control", "try", node.lineno)
            edges.append({"from": parent_id, "to": ctrl_id, "type": "contains"})
            for stmt in node.body:
                visit(stmt, ctrl_id)
            for handler in node.handlers:
                h_id = new_node("control", "except", handler.lineno)
                edges.append({"from": ctrl_id, "to": h_id, "type": "control_flow"})
                for stmt in handler.body:
                    visit(stmt, h_id)
            for stmt in node.finalbody:
                visit(stmt, ctrl_id)
            return

        if isinstance(node, ast.With):
            ctrl_id = new_node("control", "with", node.lineno)
            edges.append({"from": parent_id, "to": ctrl_id, "type": "contains"})
            for stmt in node.body:
                visit(stmt, ctrl_id)
            return

        if isinstance(node, ast.Assign):
            stmt_id = new_node("statement", "assign", node.lineno)
            edges.append({"from": parent_id, "to": stmt_id, "type": "contains"})
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_id = new_node("variable", target.id, node.lineno, {"role": "def"})
                    edges.append({"from": stmt_id, "to": var_id, "type": "data_flow"})
                    var_defs[target.id].append(var_id)
            # Track uses in value
            for child in ast.walk(node.value):
                if isinstance(child, ast.Name):
                    var_uses[child.id].append(stmt_id)
            return

        if isinstance(node, ast.Call):
            call_id = new_node("call", "call", node.lineno)
            edges.append({"from": parent_id, "to": call_id, "type": "contains"})
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name:
                new_node("statement", func_name, node.lineno, {"role": "call_target"})
            return

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imp_id = new_node("import", "import", node.lineno)
            edges.append({"from": parent_id, "to": imp_id, "type": "contains"})
            return

        # Generic statement
        if isinstance(node, ast.stmt):
            sid = new_node("statement", type(node).__name__, node.lineno)
            edges.append({"from": parent_id, "to": sid, "type": "contains"})

        # Recurse into children
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                visit(child, parent_id)

    for stmt in tree.body:
        visit(stmt)

    # Build data flow edges (def -> use)
    for var_name, defs in var_defs.items():
        uses = var_uses.get(var_name, [])
        for d in defs:
            for u in uses:
                edges.append({"from": d, "to": u, "type": "data_flow"})


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    return {
        "path": str(filepath),
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "functions": sum(1 for n in nodes if n["type"] == "function"),
            "classes": sum(1 for n in nodes if n["type"] == "class"),
            "controls": sum(1 for n in nodes if n["type"] == "control"),
            "variables": sum(1 for n in nodes if n["type"] == "variable"),
            "calls": sum(1 for n in nodes if n["type"] == "call"),
            "data_flow_edges": sum(1 for e in edges if e["type"] == "data_flow"),
            "control_flow_edges": sum(1 for e in edges if e["type"] == "control_flow"),
        },
    }


def find_pattern(root, pattern):
    """Find code patterns across a codebase."""
    results = []
    pattern_lower = pattern.lower()

    for dirpath, dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if not f.endswith(".py"):
                continue
            fp = Path(dirpath) / f
            try:
                cpg = analyze_file(fp)
                if "error" in cpg:
                    continue
                # Check for pattern in node labels
                for node in cpg["nodes"]:
                    if pattern_lower in node["label"].lower() or pattern_lower in node["type"].lower():
                        results.append({
                            "file": str(fp),
                            "line": node["line"],
                            "type": node["type"],
                            "label": node["label"],
                        })
                # Check for bare except
                if pattern == "bare_except":
                    source = fp.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(source.splitlines(), 1):
                        if "except:" in line.replace(" ", "") or "except :" in line:
                            results.append({"file": str(fp), "line": i, "type": "bare_except", "label": line.strip()[:80]})
            except Exception as exc:
                note_failure("tools/cpg_lite.py", exc)
                pass

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    p_analyze = sub.add_parser("analyze", help="Analyze a single file")
    p_analyze.add_argument("--file", required=True, help="Python file to analyze")

    p_stats = sub.add_parser("stats", help="Show CPG stats for a codebase")
    p_stats.add_argument("--root", default="src", help="Root directory")

    p_pattern = sub.add_parser("find-pattern", help="Find code patterns")
    p_pattern.add_argument("--pattern", required=True, help="Pattern to search for")
    p_pattern.add_argument("--root", default="src", help="Root directory")

    args = parser.parse_args()

    if args.cmd == "analyze":
        cpg = analyze_file(args.file)
        if "error" in cpg:
            print(f"Error: {cpg['error']}")
            sys.exit(1)
        print(json.dumps(cpg["stats"], indent=2))
        print(f"\nNodes: {len(cpg['nodes'])}, Edges: {len(cpg['edges'])}")

    elif args.cmd == "stats":
        total_nodes = 0
        total_edges = 0
        total_files = 0
        agg = defaultdict(int)
        for dirpath, dirs, files in os.walk(args.root):
            if "__pycache__" in dirpath:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                fp = Path(dirpath) / f
                cpg = analyze_file(fp)
                if "error" in cpg:
                    continue
                total_files += 1
                total_nodes += cpg["stats"]["total_nodes"]
                total_edges += cpg["stats"]["total_edges"]
                for k, v in cpg["stats"].items():
                    agg[k] += v
        print(f"Files: {total_files}")
        print(f"Total nodes: {total_nodes}")
        print(f"Total edges: {total_edges}")
        for k in sorted(agg):
            print(f"  {k}: {agg[k]}")

    elif args.cmd == "find-pattern":
        results = find_pattern(args.root, args.pattern)
        print(f"Found {len(results)} matches for '{args.pattern}'")
        for r in results[:20]:
            print(f"  {r['file']}:{r['line']} [{r['type']}] {r['label'][:60]}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
