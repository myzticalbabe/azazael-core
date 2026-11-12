# coding: utf-8
"""
test_worm_lifecycle.py — Worm Lifecycle Validation Framework

P0 Critical: Automated execute() lifecycle tests for ALL registered worms.
Addresses the Mistral finding that no automated lifecycle validation exists.

Architecture:
  Phase 1: Load WORM_REGISTRY via pickle cache (<0.2s, avoids 38-45s cold start)
  Phase 2: For each registered worm: instantiate → call execute() → validate result
  Phase 3: Aggregate results into a comprehensive health report

Fallback strategy: If execute() is not available, tries get_summary(),
get_status(), run(), scan() in priority order — same as CLUSTERWORMS resolution.

IMPORTANT: Run standalone (python test_worm_lifecycle.py), NOT via pytest.
pytest on Python 3.14t free-threading can segfault due to C extension
conflicts (psutil, pandas). This file is designed for `python -m unittest`
or direct `python test_worm_lifecycle.py` execution.

Usage:
    python azazael/tests/test_worm_lifecycle.py
    python azazael/tests/test_worm_lifecycle.py --sample 20   # Test 20 random worms
    python azazael/tests/test_worm_lifecycle.py --category monitor  # Test one category
"""

import sys
import os
import time
import threading
import unittest
import random
import argparse
from pathlib import Path
from typing import Any

# ── Path Setup ──────────────────────────────────────────────────────
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "CORE" / "WORMS"))

# Disable langchain/langsmith tracing to avoid noise
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")

from CORE.worm_registry import (
    WORM_REGISTRY, discover_worms, get_worms_by_category,
    get_all_categories, mark_spawned,
)

# ── Standard fallback methods (same as CLUSTERWORMS resolution) ────
_STANDARD_METHODS = [
    "execute", "get_summary", "get_status", "run", "scan",
    "check", "analyze", "audit", "collect", "probe", "profile",
    "inspect", "query", "fetch", "start", "monitor",
]

# All worms must be tested. C-extension crashes caught by timeout+exception handler.
# psutil was fixed via cp314t wheel (ERROR_LOG.md Error #49).
_KNOWN_SKIP = {}

_DEFAULT_TIMEOUT = 5.0  # seconds per worm
_MAX_SAMPLE = 30  # default sample size for fast CI runs


class WormLifecycleResult:
    """Result from a single worm lifecycle test."""

    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.success: bool = False
        self.method_called: str = ""
        self.result_type: str = ""
        self.duration_ms: float = 0.0
        self.error: str = ""
        self.skipped: bool = False
        self.skip_reason: str = ""
        self.timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "success": self.success,
            "method_called": self.method_called,
            "result_type": self.result_type,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error[:200] if self.error else None,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "timed_out": self.timed_out,
        }


class WormLifecycleValidator:
    """Validates that all registered worms can be instantiated and executed.

    Implements the P0 gap identified by Mistral: no automated execute()
    lifecycle tests for the full 77-worm registry.
    """

    VERSION = "1.0.0"

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT):
        self._timeout = timeout
        self._lock = threading.Lock()
        self._results: list[WormLifecycleResult] = []
        self._start_time: float = 0.0

    def discover(self) -> int:
        """Discover all registered worms. Uses pickle cache for speed."""
        return discover_worms()

    def validate_all(self, categories: (list[str] | None) = None,
                     sample_size: (int | None) = None) -> list[WormLifecycleResult]:
        """Validate all (or a sample of) registered worms.

        Args:
            categories: Optional filter — only test these categories.
            sample_size: Optional — test only N random worms (for fast CI).

        Returns:
            List of WormLifecycleResult objects.
        """
        # Ensure registry is loaded
        if not WORM_REGISTRY:
            self.discover()

        self._start_time = time.time()
        self._results = []

        # Filter by category if requested
        worms_to_test = dict(WORM_REGISTRY)
        if categories:
            worms_to_test = {
                name: info for name, info in worms_to_test.items()
                if info.get("category") in categories
            }

        # Sample if requested
        worm_names = list(worms_to_test.keys())
        if sample_size and sample_size < len(worm_names):
            random.seed(42)  # Reproducible sampling
            worm_names = random.sample(worm_names, sample_size)

        # Sort by priority (highest first) for deterministic ordering
        worm_names.sort(
            key=lambda n: worms_to_test.get(n, {}).get("priority", 5),
            reverse=True,
        )

        for name in worm_names:
            info = worms_to_test[name]
            result = self._validate_single(name, info)
            with self._lock:
                self._results.append(result)

        return self._results

    def _validate_single(self, name: str, info: dict[str, Any]) -> WormLifecycleResult:
        """Validate a single worm: instantiate → execute → check result."""
        category = info.get("category", "utility")
        result = WormLifecycleResult(name, category)

        # Check skip list
        if name in _KNOWN_SKIP:
            result.skipped = True
            result.skip_reason = _KNOWN_SKIP[name]
            return result

        worm_cls = info.get("class")
        if not worm_cls:
            result.error = "No class found in registry"
            return result

        # ── Phase 1: Instantiate ──
        start = time.time()
        try:
            # Some worms take args — try no-arg first, then with name
            try:
                instance = worm_cls()
            except TypeError:
                try:
                    instance = worm_cls(name=name)
                except TypeError:
                    instance = worm_cls(worm_name=name)
        except Exception as e:
            result.error = f"Instantiation failed: {e}"
            result.duration_ms = (time.time() - start) * 1000
            return result

        # ── Phase 2: Execute (with timeout) ──
        exec_result = None
        method_used = ""

        def _try_execute():
            nonlocal exec_result, method_used
            for method_name in _STANDARD_METHODS:
                if hasattr(instance, method_name) and callable(getattr(instance, method_name)):
                    try:
                        method = getattr(instance, method_name)
                        exec_result = method()
                        method_used = method_name
                        return
                    except Exception:
                        continue  # Try next method

        # Run with timeout via thread
        thread = threading.Thread(target=_try_execute, daemon=True)
        thread.start()
        thread.join(timeout=self._timeout)

        if thread.is_alive():
            result.timed_out = True
            result.error = f"Timed out after {self._timeout}s"
            result.duration_ms = (time.time() - start) * 1000
            return result

        result.method_called = method_used
        result.duration_ms = (time.time() - start) * 1000

        # ── Phase 3: Validate result ──
        if exec_result is None and not method_used:
            result.error = f"No standard method found (tried: {', '.join(_STANDARD_METHODS[:6])})"
            return result

        if exec_result is None:
            result.error = f"{method_used}() returned None"
            return result

        # Check result type and content
        if isinstance(exec_result, dict):
            result.result_type = "dict"
            # Flag dicts with error keys as degraded
            if "error" in exec_result and exec_result.get("success") is not True:
                result.result_type = "dict(error)"
                result.success = False
                result.error = f"Worm returned error: {str(exec_result.get('error', ''))[:100]}"
            else:
                result.success = True
        elif isinstance(exec_result, (list, tuple, str, int, float, bool)):
            result.result_type = type(exec_result).__name__
            result.success = True
        elif hasattr(exec_result, "__dict__"):
            result.result_type = "dataclass/object"
            result.success = True
        else:
            result.result_type = type(exec_result).__name__
            result.success = True  # Returned something — count as success

        # Mark as spawned in registry
        try:
            mark_spawned(name)
        except Exception:
            pass

        return result

    def get_report(self) -> dict[str, Any]:
        """Generate a comprehensive health report."""
        total = len(self._results)
        succeeded = sum(1 for r in self._results if r.success)
        skipped = sum(1 for r in self._results if r.skipped)
        failed = total - succeeded - skipped
        timed_out = sum(1 for r in self._results if r.timed_out)

        elapsed = time.time() - self._start_time

        # Group by category
        by_category: dict[str, Dict[str, int]] = {}
        for r in self._results:
            cat = r.category
            if cat not in by_category:
                by_category[cat] = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
            by_category[cat]["total"] += 1
            if r.success:
                by_category[cat]["success"] += 1
            elif r.skipped:
                by_category[cat]["skipped"] += 1
            else:
                by_category[cat]["failed"] += 1

        # Method usage stats
        method_stats: dict[str, int] = {}
        for r in self._results:
            if r.method_called:
                method_stats[r.method_called] = method_stats.get(r.method_called, 0) + 1

        # Slowest worms
        slowest = sorted(
            [r for r in self._results if r.success],
            key=lambda r: -r.duration_ms,
        )[:5]

        return {
            "version": self.VERSION,
            "summary": {
                "total_tested": total,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "timed_out": timed_out,
                "success_rate": round(succeeded / max(total, 1), 3),
                "elapsed_seconds": round(elapsed, 1),
            },
            "by_category": by_category,
            "method_usage": method_stats,
            "slowest_worms": [
                {"name": r.name, "duration_ms": r.duration_ms}
                for r in slowest
            ],
            "failures": [
                r.to_dict() for r in self._results
                if not r.success and not r.skipped
            ],
            "skipped": [
                r.to_dict() for r in self._results if r.skipped
            ],
        }


# ═══════════════════════════════════════════════════════════════════════
# Unit Tests
# ═══════════════════════════════════════════════════════════════════════

class TestWormLifecycleValidator(unittest.TestCase):
    """Tests for the WormLifecycleValidator itself."""

    def setUp(self):
        self.validator = WormLifecycleValidator(timeout=3.0)

    def test_discover_loads_registry(self):
        """discover() should populate WORM_REGISTRY.

        `discover()` forwards `discover_worms()`, whose return is a DELTA, so it
        is 0 whenever another test module already ran discovery in this process.
        The registry is the invariant that discovery promises.
        """
        self.validator.discover()
        self.assertGreater(len(WORM_REGISTRY), 0,
                           "Should discover at least one worm")

    def test_validate_sample_small(self):
        """validate_all(sample_size=5) should test 5 worms."""
        results = self.validator.validate_all(sample_size=5)
        self.assertEqual(len(results), 5, "Should test exactly 5 worms")
        # At least some should succeed
        succeeded = sum(1 for r in results if r.success)
        self.assertGreater(succeeded, 0, "At least one worm should succeed")

    def test_validate_by_category(self):
        """validate_all(categories=['monitor']) should only test monitors."""
        results = self.validator.validate_all(
            categories=["monitor"], sample_size=5
        )
        for r in results:
            self.assertEqual(r.category, "monitor",
                             f"{r.name} should be in monitor category, got {r.category}")
        # Verify no worms from other categories leaked in
        self.assertGreater(len(results), 0)

    def test_report_generation(self):
        """get_report() should return a valid health report."""
        self.validator.validate_all(sample_size=5)
        report = self.validator.get_report()
        self.assertEqual(report["version"], "1.0.0")
        self.assertIn("summary", report)
        self.assertIn("by_category", report)
        self.assertIn("method_usage", report)
        self.assertGreaterEqual(report["summary"]["total_tested"], 5)

    def test_skip_list_respected(self):
        """Worms in _KNOWN_SKIP should be marked as skipped."""
        self.validator.validate_all(sample_size=10)
        report = self.validator.get_report()
        for skipped in report.get("skipped", []):
            self.assertTrue(skipped["skipped"])
            self.assertIn(skipped["skip_reason"], _KNOWN_SKIP.values())

    def test_each_result_has_category(self):
        """Every result should have a non-empty category."""
        self.validator.validate_all(sample_size=5)
        for r in self.validator._results:
            self.assertIsNotNone(r.category)
            self.assertNotEqual(r.category, "")

    def test_result_to_dict_structure(self):
        """to_dict() should return expected keys."""
        r = WormLifecycleResult("TestWorm", "monitor")
        d = r.to_dict()
        expected_keys = {
            "name", "category", "success", "method_called",
            "result_type", "duration_ms", "error",
            "skipped", "skip_reason", "timed_out",
        }
        self.assertEqual(set(d.keys()), expected_keys)


# ═══════════════════════════════════════════════════════════════════════
# Standalone Runner (NOT pytest — 3.14t segfault workaround)
# ═══════════════════════════════════════════════════════════════════════

def _parse_args():
    parser = argparse.ArgumentParser(
        description="Worm Lifecycle Validator v1.0.0 — execute() on all registered worms"
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help=f"Test only N random worms (default: all, fast CI: {_MAX_SAMPLE})"
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Test only worms in this category (e.g., monitor, diagnostic)"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Test ALL registered worms (no sampling)"
    )
    parser.add_argument(
        "--timeout", type=float, default=_DEFAULT_TIMEOUT,
        help=f"Timeout per worm in seconds (default: {_DEFAULT_TIMEOUT})"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-worm results"
    )
    return parser.parse_args()


def main():
    args = _parse_args()

    sample_size = args.sample
    if args.all:
        sample_size = None
    elif sample_size is None:
        sample_size = _MAX_SAMPLE

    categories = [args.category] if args.category else None

    print("=" * 70)
    print("  WORM LIFECYCLE VALIDATOR v1.0.0")
    print("  Automated execute() lifecycle tests for all registered worms")
    print("=" * 70)
    sys.stdout.flush()
    print(f"  Timeout: {args.timeout}s/worm | Sample: {sample_size or 'ALL'}"
          f" | Category: {args.category or 'ALL'}")
    print()

    validator = WormLifecycleValidator(timeout=args.timeout)

    # Phase 1: Discover
    print("[Phase 1] Loading WORM_REGISTRY...")
    count = validator.discover()
    categories = get_all_categories()
    print(f"  Loaded {count} worms across {len(categories)} categories")
    print(f"  Categories: {', '.join(sorted(categories))}")
    print()

    # Phase 2: Validate
    test_count = sample_size if sample_size else count
    print(f"[Phase 2] Validating {test_count} worms...")
    print()

    results = validator.validate_all(
        categories=[args.category] if args.category else None,
        sample_size=sample_size,
    )

    # Per-worm output
    for r in results:
        if r.skipped:
            icon = "SKIP"
        elif r.success:
            icon = "PASS"
        elif r.timed_out:
            icon = "TIME"
        else:
            icon = "FAIL"

        if args.verbose or not r.success:
            details = ""
            if r.success:
                details = f" [{r.method_called}() → {r.result_type}] {r.duration_ms:.0f}ms"
            elif r.skipped:
                details = f" [{r.skip_reason}]"
            elif r.timed_out:
                details = f" [TIMEOUT] {r.error}"
            else:
                details = f" [{r.error[:80]}]"
            print(f"  [{icon}] {r.name:25s} ({r.category:15s}){details}")

    # Phase 3: Report
    print()
    print("[Phase 3] Generating health report...")
    print()

    report = validator.get_report()
    s = report["summary"]

    print("-" * 70)
    print("  HEALTH REPORT")
    print("-" * 70)
    print(f"  Total tested:   {s['total_tested']}")
    print(f"  Succeeded:      {s['succeeded']}")
    print(f"  Failed:         {s['failed']}")
    print(f"  Skipped:        {s['skipped']}")
    print(f"  Timed out:      {s['timed_out']}")
    print(f"  Success rate:   {s['success_rate']:.1%}")
    print(f"  Elapsed:        {s['elapsed_seconds']:.1f}s")
    print()

    # Category breakdown
    print("  By Category:")
    for cat, stats in sorted(report["by_category"].items()):
        rate = stats["success"] / max(stats["total"], 1)
        bar = "#" * int(rate * 20)
        print(f"    {cat:20s} {stats['success']:3d}/{stats['total']:<3d} "
              f"{int(rate*100):3d}% {bar}")

    # Method usage
    if report["method_usage"]:
        print()
        print("  Method Usage:")
        for method, count in sorted(report["method_usage"].items(),
                                     key=lambda x: -x[1]):
            print(f"    {method:20s} {count} worms")

    # Slowest
    if report["slowest_worms"]:
        print()
        print("  Slowest (successful):")
        for w in report["slowest_worms"]:
            print(f"    {w['name']:25s} {w['duration_ms']:.0f}ms")

    # Failures detail
    if report["failures"]:
        print()
        print("  Failures:")
        for f in report["failures"]:
            print(f"    [{f['category']}] {f['name']}: {f.get('error', 'unknown')[:100]}")

    print()
    print("=" * 70)
    if s["failed"] == 0:
        print(f"  ALL {s['succeeded']} WORMS PASSED LIFECYCLE VALIDATION")
    else:
        print(f"  {s['succeeded']} passed, {s['failed']} failed, {s['skipped']} skipped"
              f" — success rate {s['success_rate']:.1%}")
    print("=" * 70)

    # Run unit tests if no args given
    if not any([args.sample, args.all, args.category]):
        print()
        print("-" * 70)
        print("  Running self-tests (WormLifecycleValidator unit tests)...")
        print("-" * 70)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(TestWormLifecycleValidator)
        runner = unittest.TextTestRunner(verbosity=2)
        test_result = runner.run(suite)

        if not test_result.wasSuccessful():
            sys.exit(1)

    sys.exit(0 if s["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
