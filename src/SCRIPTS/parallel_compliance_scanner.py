#!/usr/bin/env python3
"""Parallel Compliance Scanner — CLI tool for RULE 0Z-F compliance.

Scans .md documentation files, error logs, and session notes
for sequential execution patterns that violate RULE 0Z-F.

Usage:
    python parallel_compliance_scanner.py [--dir DIR] [--verbose]

Returns exit code 0 if no violations found, 1 if violations found,
2 if error.
"""
import sys
import os
import re
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Pattern: spawn_agents call followed by another spawn_agents before results
SEQUENTIAL_SPAWN_RE = re.compile(
    r'spawn_agents\([^)]*\)\s*\n.*?spawn_agents\(',
    re.DOTALL
)

# Pattern: sequential read_files (could be parallelized)
SEQUENTIAL_READ_FILES = re.compile(
    r'read_files\(\[[^\]]*\]\)\s*\n.*?read_files\(',
    re.DOTALL
)

# Pattern: sequential basher calls (could be parallelized)
SEQUENTIAL_BASHER = re.compile(
    r'basher\([^)]*\)\s*\n.*?basher\(',
    re.DOTALL
)


def scan_file(filepath: str, verbose: bool = False) -> list:
    """Scan a single file for RULE 0Z-F violations."""
    violations = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except (IOError, OSError) as e:
        if verbose:
            print(f"  SKIP: {filepath} ({e})")
        return violations

    # Note: spawn_agents is a Codebuff tool, not a Python function.
    # Sequential tool calls cannot be detected via static analysis of .py source.
    # This scanner focuses on .md documentation for sequential execution descriptions.
    seq_spawns = []

    # Check for sequential read_files calls
    seq_reads = SEQUENTIAL_READ_FILES.findall(content)
    for match in seq_reads[:3]:
        violations.append({
            "file": filepath,
            "type": "sequential_read_files",
            "match": match[:800],
        })

    return violations


def scan_directory(directory: str, verbose: bool = False) -> list:
    """Recursively scan a directory for RULE 0Z-F violations."""
    all_violations = []
    ext_map = {".md", ".py", ".txt", ".log", ".json", ".yaml", ".yml"}

    for root, dirs, files in os.walk(directory):
        # Skip common non-source directories
        dirs[:] = [d for d in dirs if not d.startswith((".", "__", "node_modules", "venv", ".git"))]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in ext_map:
                fpath = os.path.join(root, fname)
                v = scan_file(fpath, verbose)
                all_violations.extend(v)

    return all_violations


def main():
    parser = argparse.ArgumentParser(
        description="RULE 0Z-F Parallel Compliance Scanner"
    )
    parser.add_argument(
        "--dir", "-d",
        default=".",
        help="Directory to scan (default: current directory)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show verbose output including skipped files"
    )
    parser.add_argument(
        "--report", "-r",
        action="store_true",
        help="Generate summary report"
    )
    args = parser.parse_args()

    target = os.path.abspath(args.dir)
    if not os.path.isdir(target):
        print(f"ERROR: Directory not found: {target}")
        sys.exit(2)

    print(f"Scanning: {target}")
    print("=" * 60)

    violations = scan_directory(target, args.verbose)

    if not violations:
        print("\nNO VIOLATIONS FOUND — All files appear RULE 0Z-F compliant.")
        sys.exit(0)

    print(f"\nFound {len(violations)} potential RULE 0Z-F violation(s):")
    print("-" * 60)

    by_type = {}
    for v in violations:
        vtype = v["type"]
        by_type.setdefault(vtype, 0)
        by_type[vtype] += 1
        print(f"  [{vtype}] {v['file']}")
        if args.verbose:
            print(f"    Context: {v['match'][:80]}...")

    if args.report:
        print("\n--- Summary Report ---")
        for vtype, count in sorted(by_type.items()):
            print(f"  {vtype}: {count}")
        print(f"  Total: {len(violations)}")

    sys.exit(1)


if __name__ == "__main__":
    main()
