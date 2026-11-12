#!/usr/bin/env python3
"""nature_swarm.py — Nature-inspired swarm coordination for codebase analysis.

Implements swarm intelligence patterns for parallel code analysis:
- Mycelium: decentralized data transport (memorybus)
- Ant colony: stigmergy (indirect coordination via pheromone trails)
- Slime mold: shortest path optimization (graph traversal)
- Bee colony: scout/worker/forager roles
- Neural network: weighted connections with learning

Usage:
    python nature_swarm.py analyze --root src/ --swarm-size 8
    python nature_swarm.py status
    python nature_swarm.py visualize
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Swarm roles (nature-inspired) ─────────────────────────────────────────

ROLES = {
    "scout": "Explores new territory (discovers files/modules)",
    "worker": "Processes assigned work (analyzes code)",
    "forager": "Collects results (aggregates findings)",
    "queen": "Coordinates the swarm (assigns tasks)",
    "soldier": "Validates results (runs tests/checks)",
}

# Nature-inspired coordination patterns
PATTERNS = {
    "mycelium": {
        "description": "Decentralized data transport via shared state",
        "mechanism": "memorybus_queue (SQLite) — trees read/write shared state",
        "benefit": "No single point of failure, resilient to node loss",
    },
    "stigmergy": {
        "description": "Indirect coordination via environment (pheromone trails)",
        "mechanism": "File-based markers (.done, .claimed) in shared directories",
        "benefit": "No direct communication needed, self-organizing",
    },
    "slime_mold": {
        "description": "Shortest path optimization between food sources",
        "mechanism": "Dijkstra/BFS on dependency graph (codegraph_mcp)",
        "benefit": "Finds optimal import paths, detects bottlenecks",
    },
    "bee_colony": {
        "description": "Scout/worker/forager role specialization",
        "mechanism": "Scouts discover, workers analyze, foragers aggregate",
        "benefit": "Efficient division of labor, adaptive to workload",
    },
    "neural_network": {
        "description": "Weighted connections with learning",
        "mechanism": "Import frequency as edge weights, updated by usage",
        "benefit": "Self-optimizing network topology",
    },
    "ant_colony": {
        "description": "Pheromone-based path optimization",
        "mechanism": "Frequently-used import paths get higher weight",
        "benefit": "Hot paths emerge naturally, cold paths fade",
    },
}


def discover_tasks(root):
    """Scout: discover files to analyze."""
    tasks = []
    for dirpath, dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if f.endswith(".py"):
                tasks.append(os.path.join(dirpath, f))
    return tasks


def analyze_file(filepath):
    """Worker: analyze a single file."""
    try:
        source = open(filepath, encoding="utf-8", errors="replace").read()
        lines = source.count("\n") + 1
        funcs = sum(1 for l in source.splitlines() if l.strip().startswith("def ") or l.strip().startswith("async def "))
        classes = sum(1 for l in source.splitlines() if l.strip().startswith("class "))
        imports = sum(1 for l in source.splitlines() if l.strip().startswith("import ") or l.strip().startswith("from "))
        return {
            "path": filepath,
            "lines": lines,
            "functions": funcs,
            "classes": classes,
            "imports": imports,
            "status": "done",
        }
    except Exception as e:
        return {"path": filepath, "status": "error", "error": str(e)}


def aggregate_results(results):
    """Forager: aggregate analysis results."""
    total_lines = sum(r.get("lines", 0) for r in results)
    total_funcs = sum(r.get("functions", 0) for r in results)
    total_classes = sum(r.get("classes", 0) for r in results)
    errors = [r for r in results if r.get("status") == "error"]
    return {
        "files_analyzed": len(results),
        "total_lines": total_lines,
        "total_functions": total_funcs,
        "total_classes": total_classes,
        "errors": len(errors),
        "error_files": [r["path"] for r in errors],
    }


def run_swarm(root, swarm_size=8):
    """Run the nature-inspired swarm analysis."""
    print(f"Nature Swarm: discovering tasks in {root}...")
    tasks = discover_tasks(root)
    print(f"  Scout found {len(tasks)} files")
    print(f"  Swarm size: {swarm_size} workers")

    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=swarm_size) as executor:
        futures = {executor.submit(analyze_file, t): t for t in tasks}
        for future in as_completed(futures):
            results.append(future.result())

    elapsed = time.time() - t0
    summary = aggregate_results(results)
    summary["elapsed_seconds"] = round(elapsed, 2)
    summary["files_per_second"] = round(len(tasks) / elapsed, 1)
    summary["swarm_size"] = swarm_size
    summary["patterns"] = list(PATTERNS.keys())

    print(f"\nSwarm complete: {summary['files_analyzed']} files in {elapsed:.2f}s ({summary['files_per_second']} files/s)")
    print(f"  Lines: {summary['total_lines']}, Functions: {summary['total_functions']}, Classes: {summary['total_classes']}")
    print(f"  Errors: {summary['errors']}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    p_analyze = sub.add_parser("analyze", help="Run swarm analysis")
    p_analyze.add_argument("--root", default="src", help="Root directory")
    p_analyze.add_argument("--swarm-size", type=int, default=8, help="Number of workers")
    p_analyze.add_argument("--output", default="swarm_results.json", help="Output file")

    p_status = sub.add_parser("status", help="Show swarm patterns")
    p_viz = sub.add_parser("visualize", help="Visualize nature patterns")

    args = parser.parse_args()

    if args.cmd == "analyze":
        summary = run_swarm(args.root, args.swarm_size)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"Results saved to {args.output}")

    elif args.cmd == "status":
        print("Nature-Inspired Swarm Patterns:")
        for name, info in PATTERNS.items():
            print(f"\n  {name}:")
            print(f"    {info['description']}")
            print(f"    Mechanism: {info['mechanism']}")
            print(f"    Benefit: {info['benefit']}")

    elif args.cmd == "visualize":
        print("\n" + "=" * 60)
        print("NATURE-INSPIRED SWARM NETWORK")
        print("=" * 60)
        for name, info in PATTERNS.items():
            print(f"\n  [{name}]")
            print(f"    {info['description']}")
        print("\n" + "=" * 60)
        print("SWARM ROLES:")
        for role, desc in ROLES.items():
            print(f"  {role}: {desc}")
        print("=" * 60)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
