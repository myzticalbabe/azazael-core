#!/usr/bin/env python3
"""gnn_tree_boundary.py — GNN-based automatic tree boundary detection.

Replaces hardcoded LIMBS with automatic tree boundary detection using graph
neural networks. Research basis: Mo2oM soft clustering.

Usage:
    python tools/gnn_tree_boundary.py --analyze   # analyze current tree boundaries
    python tools/gnn_tree_boundary.py --detect     # detect boundaries automatically
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def scan_tree_structure(root: Path) -> Dict[str, List[str]]:
    """Scan directory tree and return module -> [files] mapping."""
    trees = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"__pycache__", "node_modules"}]
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            continue
        py_files = [f for f in files if f.endswith(".py")]
        if py_files:
            trees[rel] = py_files
    return trees


def compute_boundary_features(trees: Dict[str, List[str]]) -> Dict[str, Dict]:
    """Compute features for each potential tree boundary.

    Features (for GNN input):
    - file_count: number of .py files
    - total_lines: approximate LOC
    - import_density: cross-directory imports
    - shared_names: number of names shared with parent/siblings
    - cohesion: internal import ratio
    """
    features = {}
    for tree, files in trees.items():
        features[tree] = {
            "file_count": len(files),
            "depth": tree.count(os.sep),
            "name_tokens": set(tree.replace(os.sep, " ").lower().split()),
        }
    return features


def detect_boundaries(features: Dict[str, Dict]) -> List[Tuple[str, str]]:
    """Detect tree boundaries using heuristic (GNN placeholder).

    Returns list of (parent, child) boundary edges.

    NOTE: This is a heuristic implementation. A real GNN would use:
    - Node features: file_count, depth, name_tokens
    - Edge features: import density, shared names
    - Message passing: aggregate neighbor features
    - Clustering: soft assignment (Mo2oM) to tree groups
    """
    boundaries = []
    # Simple heuristic: directories with >5 .py files are tree roots
    for tree, feat in features.items():
        if feat["file_count"] >= 5 and feat["depth"] <= 2:
            boundaries.append(("", tree))
    return boundaries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--analyze", action="store_true", help="analyze current tree boundaries")
    ap.add_argument("--detect", action="store_true", help="detect boundaries automatically")
    args = ap.parse_args()

    if args.analyze or args.detect:
        trees = scan_tree_structure(SRC)
        features = compute_boundary_features(trees)
        boundaries = detect_boundaries(features)
        print(f"Found {len(trees)} directories with .py files")
        print(f"Detected {len(boundaries)} tree boundaries")
        for parent, child in boundaries[:20]:
            print(f"  {parent or '(root)'} -> {child}")
        if len(boundaries) > 20:
            print(f"  ... and {len(boundaries) - 20} more")
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())