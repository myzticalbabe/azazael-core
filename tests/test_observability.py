#!/usr/bin/env python3
"""
test_observability.py — Convenience script that runs all observability tests.

Usage:
    python tests/test_observability.py            # Run all observability tests (fast)
    python tests/test_observability.py --slow     # Include slow integration tests
    python tests/test_observability.py --verbose  # Verbose output
    python tests/test_observability.py --list     # List which tests will be run
"""
import os
import sys
import subprocess
import argparse

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
RUN_TESTS = os.path.join(PROJECT_ROOT, "run_tests.py")

# Observability test files and their descriptions
OBSERVABILITY_TESTS = {
    "fast": [
        "test_cli_observability.py",      # CLI subprocess tests
        "test_dashboard_filters.py",      # _parse_since and since-filtered get_report
        "test_health_server.py",           # Health endpoint JSON and server lifecycle
        "test_scheduling.py",              # ScheduledTrainingWorm execute and scheduler
        "test_workflow.py",                # WorkflowEngine, DependencyGraph, templates
    ],
    "slow": [
        "test_server_integration.py",      # Health + Prometheus live TCP endpoints
    ],
}


def main():
    parser = argparse.ArgumentParser(
        description="Run all Azazael observability tests",
    )
    parser.add_argument("--slow", action="store_true", help="Include slow integration tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--list", action="store_true", help="List test files that will be run")

    args = parser.parse_args()

    if args.list:
        print("Observability test files:")
        for f in OBSERVABILITY_TESTS["fast"]:
            print(f"  [fast] {f}")
        if args.slow:
            for f in OBSERVABILITY_TESTS["slow"]:
                print(f"  [slow] {f}")
        return

    # Use run_tests.py's --observability flag for the fast set
    cmd = [sys.executable, RUN_TESTS, "--observability"]
    if args.verbose:
        cmd.append("-v")

    print("=" * 60)
    print("  Azazael Observability Test Suite")
    print("=" * 60)
    print()

    if args.slow:
        print("Running fast + slow observability tests...")
        # Run fast tests via run_tests.py --observability, then slow tests
        result_fast = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=PROJECT_ROOT
        )
        print(result_fast.stdout)
        if result_fast.returncode != 0:
            print(result_fast.stderr)
            sys.exit(result_fast.returncode)

        for f in OBSERVABILITY_TESTS["slow"]:
            print(f"Running slow test: {f}")
            result = subprocess.run(
                [sys.executable, os.path.join(PROJECT_ROOT, "tests", f)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=PROJECT_ROOT,
            )
            print(result.stdout)
            if result.returncode != 0:
                print(result.stderr)
                print(f"  [FAIL] {f}")
                sys.exit(result.returncode)
            print(f"  [OK] {f}")
            print()
    else:
        print("Running fast observability tests (use --slow for integration tests)...")
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=PROJECT_ROOT
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()


# ── pytest entry point (2026-09-21) ──────────────────────────────────
# This file is a RUNNER, not a test module: pytest collected nothing from it
# (rc=5), and `main()` shells out to a whole suite, so wrapping it the way the
# other standalone scripts in this directory are wrapped would mean the suite
# running itself. What is worth asserting instead is the MANIFEST: the runner
# declares which files make up the observability set, and a declared test file
# that does not exist is a silent hole -- `--list` would print a name that runs
# nothing, and the "observability suite passed" claim would be about fewer files
# than it names.
def test_observability_manifest_is_real():
    here = os.path.dirname(os.path.abspath(__file__))
    missing = [name for group in OBSERVABILITY_TESTS.values() for name in group
               if not os.path.isfile(os.path.join(here, name))]
    assert not missing, f"manifest names test files that do not exist: {missing}"


def test_runner_it_delegates_to_exists():
    assert os.path.isfile(RUN_TESTS), (
        f"test_observability.py delegates to {RUN_TESTS}, which does not exist"
    )
