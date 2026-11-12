# coding: utf-8
"""
Tests for dlq_watch.py utilities.

Tests:
- bucket_errors() grouping logic
- _color() ANSI/no-color mode switching
- Empty error list handling
- Single bucket behavior
- Multiple buckets with same timestamp
- Large bucket (many errors)
"""

import sys
import os
import time

# The scripts/ directory is at the project root, not in src/
_test_dir = os.path.dirname(__file__)
_project_root = os.path.dirname(_test_dir)
sys.path.insert(0, _project_root)

import scripts.dlq_watch as watch

passed = 0
failed = 0
errors = []


def test_bucket_errors_empty():
    """bucket_errors with empty list should return empty list."""
    result = watch.bucket_errors([], bucket_seconds=30)
    assert isinstance(result, list), f"Expected list, got {type(result)}"
    assert len(result) == 0, f"Expected 0 buckets, got {len(result)}"


def test_bucket_errors_single():
    """bucket_errors with single error should create one bucket."""
    errors_list = [{
        "timestamp_epoch": time.time(),
        "worm": "TestWorm",
        "category": "test",
        "error": "Something failed",
    }]
    result = watch.bucket_errors(errors_list, bucket_seconds=30)
    assert len(result) == 1, f"Expected 1 bucket, got {len(result)}"
    assert result[0]["count"] == 1
    assert "TestWorm" in result[0]["worms"]


def test_bucket_errors_multiple_same_bucket():
    """Multiple errors in same time bucket should be grouped."""
    now = time.time()
    errors_list = []
    for i in range(5):
        errors_list.append({
            "timestamp_epoch": now,
            "worm": f"Worm{i}",
            "category": "test",
            "error": f"Error #{i}",
        })
    result = watch.bucket_errors(errors_list, bucket_seconds=30)
    assert len(result) == 1, f"Expected 1 bucket, got {len(result)}"
    assert result[0]["count"] == 5, f"Expected count=5, got {result[0]['count']}"
    assert len(result[0]["worms"]) == 5, "Should have 5 unique worms"


def test_bucket_errors_multiple_buckets():
    """Errors spread across multiple time buckets."""
    now = time.time()
    errors_list = [
        {"timestamp_epoch": now - 60, "worm": "Worm1", "category": "test", "error": "Old"},
        {"timestamp_epoch": now, "worm": "Worm2", "category": "test", "error": "New"},
    ]
    result = watch.bucket_errors(errors_list, bucket_seconds=30)
    assert len(result) == 2, f"Expected 2 buckets, got {len(result)}"
    assert result[0]["count"] == 1  # Most recent first (sorted by -time)
    assert result[1]["count"] == 1


def test_bucket_errors_examples_capped():
    """bucket_errors should cap examples at 2 per bucket."""
    now = time.time()
    errors_list = []
    for i in range(10):
        errors_list.append({
            "timestamp_epoch": now,
            "worm": "WormA",
            "category": "test",
            "error": f"Error #{i} with lots of detail",
        })
    result = watch.bucket_errors(errors_list, bucket_seconds=30)
    assert len(result[0]["examples"]) <= 2, f"Max 2 examples, got {len(result[0]['examples'])}"


def test_bucket_errors_worm_category_counting():
    """Worm and category counts should be accurate."""
    now = time.time()
    errors_list = [
        {"timestamp_epoch": now, "worm": "A", "category": "nat1", "error": "err"},
        {"timestamp_epoch": now, "worm": "B", "category": "nat1", "error": "err"},
        {"timestamp_epoch": now, "worm": "C", "category": "nat2", "error": "err"},
    ]
    result = watch.bucket_errors(errors_list, bucket_seconds=30)
    assert result[0]["count"] == 3
    assert len(result[0]["worms"]) == 3
    assert len(result[0]["categories"]) == 2


def test_color_mode_switch():
    """_color should return Colors class in color mode, stripped in no-color."""
    c_color = watch._color(True)
    c_nocolor = watch._color(False)
    assert c_color.RED != "", "Color mode should have ANSI codes"
    assert c_nocolor.RED == "", "No-color mode should have empty strings"
    assert c_color.GREEN != ""
    assert c_nocolor.GREEN == ""


def test_format_timestamp():
    """format_timestamp should return HH:MM:SS format."""
    now = time.time()
    result = watch.format_timestamp(now)
    assert len(result) == 8, f"Expected HH:MM:SS (8 chars), got '{result}'"
    parts = result.split(":")
    assert len(parts) == 3, f"Expected 3 colon-separated parts, got {len(parts)}"


def test_bucket_errors_varied_timestamps():
    """Errors at varied timestamps should all appear in order."""
    now = time.time()
    errors_list = [
        {"timestamp_epoch": now - 90, "worm": "W1", "category": "n1", "error": "e"},
        {"timestamp_epoch": now - 60, "worm": "W2", "category": "n2", "error": "e"},
        {"timestamp_epoch": now - 30, "worm": "W3", "category": "n3", "error": "e"},
        {"timestamp_epoch": now,       "worm": "W4", "category": "n4", "error": "e"},
    ]
    result = watch.bucket_errors(errors_list, bucket_seconds=30)
    # Each error should be in its own bucket (30s apart)
    assert len(result) == 4, f"Expected 4 buckets, got {len(result)}"
    # Should be sorted most recent first
    times = [b["time"] for b in result]
    assert times == sorted(times, reverse=True), "Buckets should be sorted most recent first"


# ── Test Runner ──────────────────────────────────────────────────

def run_test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  [OK] {name}")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        failed += 1
        errors.append(f"{name}: {e}")


def main():
    global passed, failed, errors
    print("=" * 60)
    print("  DLQ Watch Utility Tests")
    print("=" * 60)
    print()
    print(f"  Module: scripts.dlq_watch")
    print()
    print(f"  Tests:")
    print()

    tests = [
        ("Empty error list", test_bucket_errors_empty),
        ("Single error bucket", test_bucket_errors_single),
        ("Multiple errors same bucket", test_bucket_errors_multiple_same_bucket),
        ("Multiple time buckets", test_bucket_errors_multiple_buckets),
        ("Examples capped at 2", test_bucket_errors_examples_capped),
        ("Worm/category counting", test_bucket_errors_worm_category_counting),
        ("Color mode switch", test_color_mode_switch),
        ("format_timestamp", test_format_timestamp),
        ("Varied timestamps ordering", test_bucket_errors_varied_timestamps),
    ]

    for name, fn in tests:
        run_test(name, fn)

    print()
    print(f"  Results: {passed} passed, {failed} failed")
    if errors:
        print("  Errors:")
        for e in errors:
            print(f"    {e}")
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
