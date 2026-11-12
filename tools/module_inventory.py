#!/usr/bin/env python3
# coding: utf-8
"""Module inventory scanner for shard_intake.py atlas format.

Scans Python source files and produces the modules dict format expected by
shard_intake.py: per-module class inventories with tree, lines, public_api,
top_classes, and classes counts.
"""

import argparse
import ast
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional


# Source roots to scan
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

SOURCE_ROOTS = [
    PROJECT_ROOT / "src",
    PROJECT_ROOT / "tools",
    PROJECT_ROOT / "scripts",
    PROJECT_ROOT / "Project_ELIZA",
]

# Directories to skip
SKIP_DIRS = {
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".venv", ".venvs", "venv", "node_modules", "target", "dist",
    "build", ".build", "site-packages", "vendor", "third_party",
    "third-party", "chromedriver-win64", "graphify-out", "externals",
    ".cargo", ".rustup", ".npm", ".conda", ".m2", ".gradle",
    ".nuget", ".dotnet", ".vscode", ".cursor", "Packages",
    "archived_unified", "archived", ".hypothesis",
}


def is_python_file(path: Path) -> bool:
    """Check if a file is a Python source file."""
    return path.suffix in {".py", ".pyi", ".pyx", ".pxd", ".pxi"}


def should_skip(path: Path) -> bool:
    """Check if a path should be skipped."""
    parts = set(path.parts)
    return bool(parts & SKIP_DIRS)


def get_tree_for_module(module_path: Path) -> str:
    """Determine the tree name for a module path."""
    # Find which source root this module belongs to
    for root in SOURCE_ROOTS:
        try:
            rel = module_path.relative_to(root)
            # First component after root is the tree
            if rel.parts:
                return rel.parts[0]
        except ValueError:
            continue
    # Fallback: use the first directory under any source root
    for root in SOURCE_ROOTS:
        try:
            rel = module_path.relative_to(root)
            if len(rel.parts) > 1:
                return rel.parts[0]
        except ValueError:
            continue
    return "(root)"


def scan_module(module_path: Path) -> Tuple[int, List[str], int, int]:
    """Scan a Python module for classes and public API.

    Returns:
        (lines, top_classes, public_api_count, total_classes)
    """
    try:
        content = module_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0, [], 0, 0

    lines = len(content.splitlines())

    try:
        tree = ast.parse(content, filename=str(module_path))
    except SyntaxError:
        return lines, [], 0, 0

    all_classes = []
    top_classes = []
    public_api = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Only top-level classes (not nested)
            if isinstance(node.parent, ast.Module) if hasattr(node, 'parent') else True:
                all_classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Public functions (not starting with _)
            if not node.name.startswith("_"):
                public_api += 1
        elif isinstance(node, ast.ClassDef):
            # Public methods in classes
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not item.name.startswith("_"):
                        public_api += 1

    # Limit top_classes to 6 (as shard_intake.py expects)
    top_classes = sorted(all_classes)[:6]
    total_classes = len(all_classes)

    return lines, top_classes, public_api, total_classes


def scan_all_modules() -> Dict:
    """Scan all modules and return the modules dict."""
    modules = {}

    for root in SOURCE_ROOTS:
        if not root.exists():
            continue

        for py_file in root.rglob("*.py"):
            if should_skip(py_file):
                continue

            # Get module name (relative to root, without .py)
            try:
                rel = py_file.relative_to(root)
            except ValueError:
                continue

            module_name = str(rel.with_suffix("")).replace(os.sep, "/") + ".py"
            if module_name.endswith("/__init__.py"):
                module_name = module_name[:-9]

            tree = get_tree_for_module(py_file)
            lines, top_classes, public_api, total_classes = scan_module(py_file)

            modules[module_name] = {
                "tree": tree,
                "lines": lines,
                "public_api": public_api,
                "top_classes": top_classes,
                "classes": total_classes,
            }

    return modules


def build_atlas(modules: Dict) -> Dict:
    """Build the full atlas in shard_intake.py expected format."""
    totals = {
        "trees": len(set(m["tree"] for m in modules.values())),
        "modules": len(modules),
        "lines": sum(m["lines"] for m in modules.values()),
        "classes": sum(m["classes"] for m in modules.values()),
    }

    return {
        "schema": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(PROJECT_ROOT),
        "modules": modules,
        "totals": totals,
    }


def main():
    ap = argparse.ArgumentParser(description="Module inventory scanner for shard_intake.py")
    ap.add_argument("--out", default="atlas_modules.json", help="Output atlas file")
    ap.add_argument("--check", action="store_true", help="Validate against existing shards")
    args = ap.parse_args()

    print("Scanning modules...", file=sys.stderr)
    modules = scan_all_modules()
    print(f"Found {len(modules)} modules", file=sys.stderr)

    atlas = build_atlas(modules)

    Path(args.out).write_text(json.dumps(atlas, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}", file=sys.stderr)
    print(f"  trees: {atlas['totals']['trees']}", file=sys.stderr)
    print(f"  modules: {atlas['totals']['modules']}", file=sys.stderr)
    print(f"  lines: {atlas['totals']['lines']}", file=sys.stderr)
    print(f"  classes: {atlas['totals']['classes']}", file=sys.stderr)

    if args.check:
        # Validate against SHARD_INDEX.json
        shard_index = Path("Project_ELIZA/shards/SHARD_INDEX.json")
        if shard_index.exists():
            with open(shard_index, "r") as f:
                idx = json.load(f)
            indexed_shards = {s["shard_id"] for s in idx.get("shards", [])}
            print(f"Indexed shards: {len(indexed_shards)}", file=sys.stderr)

            # Generate expected shard IDs from atlas
            from Project_ELIZA.tools.shard_intake import units as intake_units
            expected_shards = {entry["shard_id"] for entry in intake_units(atlas)}
            print(f"Expected shards: {len(expected_shards)}", file=sys.stderr)

            missing = expected_shards - indexed_shards
            extra = indexed_shards - expected_shards
            if missing:
                print(f"MISSING: {len(missing)} shards", file=sys.stderr)
                for m in sorted(missing)[:10]:
                    print(f"  {m}", file=sys.stderr)
            if extra:
                print(f"EXTRA: {len(extra)} shards", file=sys.stderr)
                for e in sorted(extra)[:10]:
                    print(f"  {e}", file=sys.stderr)
            if not missing and not extra:
                print("MATCH: Atlas and shards are in sync", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())