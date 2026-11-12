#!/usr/bin/env python3
"""
gated_edit.py — Basher-compatible single-command wrapper for gate_str_replace.py

Eliminates the multi-step printf | pipe workflow. Agents pass base64-encoded
old/new strings on the command line (avoiding all shell word-splitting issues),
and this script handles temp file creation, delegation to gate_str_replace.py,
cleanup, and exit code propagation.

Usage:
  # Primary: base64-encoded strings (no shell escaping issues)
  python gated_edit.py <filepath> <old_b64> <new_b64>

  # Pass-through flags forwarded to gate_str_replace.py:
  python gated_edit.py <filepath> <old_b64> <new_b64> --dry-run
  python gated_edit.py <filepath> <old_b64> <new_b64> --all

  # For use in basher:
  python -c "import base64; print(base64.b64encode(b'old string').decode())"
  # → b2xkIHN0cmluZw==
  python azazael/src/SCRIPTS/gated_edit.py file.py b2xkIHN0cmluZw== bmV3IHN0cmluZw==

Exit codes: 0 = AUTHORIZED, 1 = DENIED/REVERTED/ERROR
"""

import argparse
import atexit
import base64
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List
import tempfile as tmpmod

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here
# from typing import List  # removed — all names have native equivalents

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_GATE_SCRIPT = _SCRIPT_DIR / "gate_str_replace.py"


def _cleanup_temp_files(paths: list[str]):
    """Delete temp files. Called by atexit and signal handlers."""
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError as exc:
            note_failure("gated_edit:L49", exc)


def run(filepath: str, old_b64: str, new_b64: str,
        dry_run: bool = False, replace_all: bool = False) -> int:
    """Decode base64 strings, write temp files, delegate to gate_str_replace.py.

    Args:
        filepath: Target file path
        old_b64: Base64-encoded old_string
        new_b64: Base64-encoded new_string
        dry_run: Forward --dry-run to gate_str_replace.py
        replace_all: Forward --all to gate_str_replace.py

    Returns:
        Exit code from gate_str_replace.py (0 = success, 1 = failure)
    """
    # Validate gate_str_replace.py exists
    if not _GATE_SCRIPT.exists():
        print(f"ERROR: gate_str_replace.py not found at {_GATE_SCRIPT}",
              file=sys.stderr)
        return 1

    # Decode base64 strings
    try:
        old_str = base64.b64decode(old_b64).decode("utf-8")
    except Exception as e:
        print(f"ERROR: Failed to decode old_b64: {e}", file=sys.stderr)
        return 1

    try:
        new_str = base64.b64decode(new_b64).decode("utf-8")
    except Exception as e:
        print(f"ERROR: Failed to decode new_b64: {e}", file=sys.stderr)
        return 1

    # Write to temp files
    temp_files: list[str] = []
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".old", delete=False, encoding="utf-8"
        ) as f:
            f.write(old_str)
            old_file = f.name
        temp_files.append(old_file)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".new", delete=False, encoding="utf-8"
        ) as f:
            f.write(new_str)
            new_file = f.name
        temp_files.append(new_file)
    except OSError as e:
        _cleanup_temp_files(temp_files)
        print(f"ERROR: Cannot create temp files: {e}", file=sys.stderr)
        return 1

    # Register cleanup for all exit paths
    atexit.register(_cleanup_temp_files, temp_files)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda s, f: (_cleanup_temp_files(temp_files),
                                              sys.exit(1)))
        except (ValueError, OSError) as exc:
            note_failure("gated_edit:L113", exc) # Signal handlers not available in all environments

    # Build command for gate_str_replace.py
    cmd = [
        sys.executable, str(_GATE_SCRIPT),
        filepath,
        old_file,
        new_file,
    ]
    if dry_run:
        cmd.append("--dry-run")
    if replace_all:
        cmd.append("--all")

    # Delegate
    result = subprocess.run(cmd, capture_output=False, timeout=1800)
    return result.returncode


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Basher-compatible single-command wrapper for "
                    "gate_str_replace.py. Accepts base64-encoded old/new "
                    "strings to avoid shell word-splitting."
    )
    parser.add_argument(
        "filepath", help="Target file path"
    )
    parser.add_argument(
        "old_b64", help="Base64-encoded old_string"
    )
    parser.add_argument(
        "new_b64", help="Base64-encoded new_string"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate without applying changes"
    )
    parser.add_argument(
        "--all", action="store_true", dest="replace_all",
        help="Replace ALL occurrences (forwarded to gate_str_replace.py)"
    )
    parser.add_argument(
        "--test", action="store_true", help="Run self-test"
    )

    args = parser.parse_args()

    if args.test:
        sys.exit(self_test())

    exit_code = run(
        args.filepath, args.old_b64, args.new_b64,
        dry_run=args.dry_run,
        replace_all=args.replace_all,
    )
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def self_test() -> int:
    """End-to-end test of base64 encode → decode → delegate pipeline."""

    print("=" * 60)
    print("  GATED EDIT SELF-TEST")
    print("=" * 60)

    passed_all = True

    # Create test file
    with tmpmod.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write("class Foo:\n    def bar(self):\n        return 1\n")
        test_path = f.name

    cleanup = [Path(test_path)]

    try:
        # Test 1: Valid replacement via base64
        print("\n[1/3] Valid base64 replacement...")
        old_b64 = base64.b64encode(b"        return 1").decode()
        new_b64 = base64.b64encode(b"        return 2").decode()

        result = run(test_path, old_b64, new_b64)
        if result != 0:
            print(f"  FAIL: Exit code {result}")
            passed_all = False
        else:
            content = Path(test_path).read_text(encoding="utf-8")
            if "        return 2" in content:
                print("  PASS: Valid base64 replacement applied")
            else:
                print("  FAIL: Replacement not applied")
                passed_all = False

        # Test 2: 0-indent catastrophe via base64
        print("[2/3] 0-indent rejection via base64...")
        old_b64 = base64.b64encode(
            b"    def bar(self):\n        return 2"
        ).decode()
        new_b64 = base64.b64encode(
            b"def bar(self):\n    return 2"
        ).decode()

        result = run(test_path, old_b64, new_b64)
        if result != 1:
            print(f"  FAIL: Should have DENIED, got exit {result}")
            passed_all = False
        else:
            content = Path(test_path).read_text(encoding="utf-8")
            if "    def bar(self):" in content:
                print("  PASS: 0-indent correctly denied")
            else:
                print("  FAIL: File modified despite denial")
                passed_all = False

        # Test 3: Bad base64
        print("[3/3] Bad base64 handling...")
        result = run(test_path, "!!!not-base64!!!", new_b64)
        if result != 1:
            print(f"  FAIL: Bad base64 should exit 1, got {result}")
            passed_all = False
        else:
            print("  PASS: Bad base64 correctly rejected")

    finally:
        for p in cleanup:
            p.unlink(missing_ok=True)
            Path(str(p) + ".pre_gate_backup").unlink(missing_ok=True)

    print("\n" + "=" * 60)
    if passed_all:
        print("  ALL GATED EDIT TESTS PASSED!")
    else:
        print("  GATED EDIT TESTS FAILED!")
    print("=" * 60)
    return 0 if passed_all else 1


if __name__ == "__main__":
    main()
