#!/usr/bin/env python3
"""dependency_graph.py — G1: build tool→worm + tool→memory dependency graph.

Reads WORM_REGISTRY (read-only) + scans TOOLS scripts. Edges = which tool
consumes which output (registry metadata + record_event/query_memories call
sites). Emits state/tool_dependency_graph.json with node/edge counts measured
against registry totals. Pure builder: no store writes, no registry
modification.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CORE.worm_registry import WORM_REGISTRY, discover_worms  # noqa: E402

TOOLS_DIR = os.path.join(_ROOT, "tools")
OUT_PATH = os.path.join(_ROOT, "state", "tool_dependency_graph.json")

MEMORY_CALL_RE = re.compile(
    r"\b(?:record_event|query_memories)\s*\([^)]*\)",
    re.DOTALL,
)
SOURCE_ARG_RE = re.compile(r"source\s*=\s*[\"']([^\"']+)[\"']")


def scan_tool(path):
    """Return imports/worm-refs/memory-calls found in a tool script."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=path)

    imports_worm_registry = False
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                if alias.name in ("CORE.worm_registry", "worm_registry"):
                    imports_worm_registry = True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.add(module)
            for alias in node.names:
                if module in ("CORE.worm_registry", "worm_registry") or alias.name == "WORM_REGISTRY":
                    imports_worm_registry = True

    worm_refs = set()
    for name in WORM_REGISTRY:
        if re.search(r"\b" + re.escape(name) + r"\b", text):
            worm_refs.add(name)

    memory_calls = []
    for m in MEMORY_CALL_RE.finditer(text):
        snippet = m.group(0)
        sm = SOURCE_ARG_RE.search(snippet)
        memory_calls.append(sm.group(1) if sm else "<no-source>")

    return {
        "imports": sorted(imports),
        "imports_worm_registry": imports_worm_registry,
        "worm_refs": sorted(worm_refs),
        "memory_calls": memory_calls,
    }


def main():
    # Populate the registry read-only (cache hit <0.2s when warm).
    discover_worms()
    registry_total = len(WORM_REGISTRY)

    if not os.path.isdir(TOOLS_DIR):
        print("NO TOOLS DIR: %s" % TOOLS_DIR)
        return 3

    tool_nodes = []
    edges = []  # [src, dst, kind]

    for f in sorted(Path(TOOLS_DIR).glob("*.py")):
        tool_name = f.name
        info = scan_tool(str(f))
        tool_nodes.append(tool_name)

        # tool consumes all worm registry metadata
        if info["imports_worm_registry"]:
            for worm_name in WORM_REGISTRY:
                edges.append([tool_name, worm_name, "imports_registry"])

        # tool references a specific worm class
        for worm_name in info["worm_refs"]:
            edges.append([tool_name, worm_name, "references_class"])

        # tool records/queries memories (consumes/produces memory output)
        for src in info["memory_calls"]:
            edges.append([tool_name, "memory_store", "memory_call:%s" % src])

    worm_nodes = sorted(WORM_REGISTRY.keys())

    graph = {
        "schema": "tool_dependency_graph_v1",
        "registry_total": registry_total,
        "tool_count": len(tool_nodes),
        "worm_count": len(worm_nodes),
        "edge_count": len(edges),
        "tools": tool_nodes,
        "worms": worm_nodes,
        "edges": edges,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)

    print("REGISTRY_TOTAL=%d TOOLS=%d WORMS=%d EDGES=%d WROTE=%s"
          % (registry_total, len(tool_nodes), len(worm_nodes), len(edges), OUT_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
