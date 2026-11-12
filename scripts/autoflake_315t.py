"""
autoflake_315t.py — Batch autoflake wrapper for project-wide import cleanup.

Provides a robust batch runner that detects and removes unused imports across
the Azazael project. Works on any Python 3.x (including 3.14t free-threading)
— autoflake is a pure-Python tool with no C extensions.

Note: This is an enhancement, NOT a 3.14t workaround. Autoflake works natively
on 3.14t. This wrapper adds batch scanning, recursive traversal, per-file error
isolation, and summary reporting.

Usage:
    # Dry-run (check only, no modifications):
    python scripts/autoflake_315t.py --check src/

    # Remove unused imports (modifies files in-place):
    python scripts/autoflake_315t.py --fix src/

    # Check a single file:
    python scripts/autoflake_315t.py --check src/CORE/Agent.py

    # Recursive scan with verbose output:
    python scripts/autoflake_315t.py --check src/ --recursive --verbose

Features:
    - Batch scanning: check or fix entire directories
    - Per-file error isolation: one bad file won't crash the batch
    - Recursive directory scanning with .py file filtering
    - Summary report: files checked, clean, issues found, errors
    - Full path normalization for Windows compatibility
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path
from typing import List, Tuple


def check_autoflake_available() -> bool:
    """Check if autoflake is installed and functional."""
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'autoflake', '--version'],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace"
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return False


def scan_py_files(paths: List[str], recursive: bool = False) -> List[str]:
    """Collect .py files from given paths."""
    files: List[str] = []
    for p in paths:
        path = Path(p)
        if path.is_file() and path.suffix == '.py':
            files.append(str(path.resolve()))
        elif path.is_dir():
            pattern = '**/*.py' if recursive else '*.py'
            for py_file in sorted(path.glob(pattern)):
                files.append(str(py_file.resolve()))
    return files


def run_autoflake(filepath: str, fix: bool = False) -> Tuple[str, int, str, str]:
    """Run autoflake on a single file. Returns (filepath, returncode, stdout, stderr)."""
    cmd = [sys.executable, '-m', 'autoflake']
    if fix:
        cmd.extend(['--in-place', '--remove-all-unused-imports', '--remove-unused-variables'])
    else:
        cmd.append('--check')

    # --expand-star-import is safe for 3.14t
    cmd.append('--expand-star-import')
    cmd.append(filepath)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace"
        )
        return (filepath, result.returncode, result.stdout, result.stderr)
    except subprocess.TimeoutExpired:
        return (filepath, -1, '', 'TIMEOUT')
    except Exception as e:
        return (filepath, -2, '', str(e))


def main():
    parser = argparse.ArgumentParser(
        description='Batch autoflake wrapper for project-wide import cleanup',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/autoflake_315t.py --check src/ --recursive
  python scripts/autoflake_315t.py --fix src/CORE/
  python scripts/autoflake_315t.py --check src/CORE/Agent.py --verbose
        """
    )
    parser.add_argument('paths', nargs='+', help='Files or directories to scan')
    parser.add_argument('--check', action='store_true', default=True,
                        dest='check_mode', help='Dry-run: check only (default)')
    parser.add_argument('--fix', action='store_true', dest='fix_mode',
                        help='Fix in-place: remove unused imports')
    parser.add_argument('--recursive', '-r', action='store_true',
                        help='Recursively scan directories')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detailed output per file')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress summary, only show issues')

    args = parser.parse_args()

    # Validate autoflake availability
    if not check_autoflake_available():
        print("ERROR: autoflake is not installed or not functional.", file=sys.stderr)
        print("Install: pip install autoflake", file=sys.stderr)
        sys.exit(1)

    # Resolve mode
    fix_mode = args.fix_mode
    action_label = "Checking" if not fix_mode else "Fixing"

    # Collect files
    files = scan_py_files(args.paths, recursive=args.recursive)
    if not files:
        print("No .py files found in specified paths.")
        sys.exit(0)

    if not args.quiet:
        print(f"autoflake_314t — {action_label} unused imports in {len(files)} files")
        print(f"  Python: {sys.version.split()[0]} ({'free-threaded' if hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled() else 'GIL-enabled'})")
        print(f"  Autoflake: autoflake via {sys.executable}")
        print()

    # Run autoflake on each file
    clean_count = 0
    issues_count = 0
    error_count = 0
    issue_files: List[str] = []

    for filepath in files:
        fp, returncode, stdout, stderr = run_autoflake(filepath, fix=fix_mode)

        if returncode == 0:
            clean_count += 1
            if args.verbose and not args.quiet:
                print(f"  [OK] {fp}")
        elif returncode == 1:
            issues_count += 1
            issue_files.append(fp)
            if not args.quiet or args.verbose:
                print(f"  [ISSUES] {fp}")
                if stdout:
                    for line in stdout.strip().split('\n'):
                        print(f"    {line}")
        else:
            error_count += 1
            if not args.quiet:
                print(f"  [ERROR] {fp}: {stderr[:200]}")

    # Summary
    if not args.quiet:
        total = len(files)
        print(f"\n  Summary: {total} files, {clean_count} clean, "
              f"{issues_count} with issues, {error_count} errors")

    if fix_mode:
        print(f"\n  Fixed {issues_count} files in-place.")
        for f in issue_files:
            print(f"    FIXED: {f}")

    # Exit with meaningful code
    if error_count > 0:
        sys.exit(2)
    if issues_count > 0 and not fix_mode:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
