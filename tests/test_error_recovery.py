# coding: utf-8
"""
Error Recovery Tests — P1-12
=============================

Tests for the two currently untested error recovery modules:
1. ErrorRecoveryOrchestrator (CORE/WORMS/expansion/error_recovery.py)
   — Retry lifecycle, fallback, circuit breaking, memory leak prevention
2. DeadLetterQueue standalone (CORE/WORMS/dead_letter_queue.py)
   — Disk persistence, concurrent replay, analytics, pruning

Prioritized per Gemini analysis: these 12 tests target production-critical
paths not covered by existing test_dlq.py or test_circuit_breaker_integration.py.
"""

import sys
import os
import time
import threading
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from CORE.WORMS.expansion.error_recovery import (
    ErrorRecoveryOrchestrator, RecoveryEntry,
)
from CORE.WORMS.dead_letter_queue import DeadLetterQueue, DeadLetter


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

passed = 0
failed = 0
errors_list = []


def _make_mock_worm(has_get_status=True, status_result=None):
    """Create a simple mock worm object for fallback testing."""
    if has_get_status:
        class MockWorm:
            def get_status(self):
                if status_result is not None:
                    return status_result
                return {"status": "degraded_fallback", "source": "get_status"}
        return MockWorm()
    else:
        # No get_status at all — not on class, not on instance
        class NoStatusWorm:
            pass
        return NoStatusWorm()


def _make_flaky_fn(fail_count=2, success_result="OK"):
    """Return a callable that fails `fail_count` times then succeeds."""
    attempts = [0]
    def fn():
        attempts[0] += 1
        if attempts[0] <= fail_count:
            raise ValueError(f"Simulated failure #{attempts[0]}")
        return success_result
    return fn


# ═══════════════════════════════════════════════════════════════════
# ErrorRecoveryOrchestrator Tests
# ═══════════════════════════════════════════════════════════════════

def test_ero_retry_until_success():
    """Fails twice, succeeds on 3rd attempt. Verifies backoff + result."""
    ero = ErrorRecoveryOrchestrator(max_retries=4, base_backoff_ms=1)
    flaky = _make_flaky_fn(fail_count=2, success_result={"data": "recovered"})

    result = ero.execute_with_recovery("TestWorm", _make_mock_worm(),
                                        execute_fn=flaky)

    assert result["outcome"] == "success", f"Expected success, got {result['outcome']}"
    assert result["recovery"]["attempts"] == 3, f"Expected 3 attempts, got {result['recovery']['attempts']}"
    assert result["result"] == {"data": "recovered"}, f"Wrong result: {result['result']}"
    assert ero.get_stats()["worm_failure_counts"].get("TestWorm", 0) == 0, \
        "Failure count should reset on success"


def test_ero_exhaustion_and_fallback():
    """Always-failing worm exhausts retries → returns degraded fallback."""
    ero = ErrorRecoveryOrchestrator(max_retries=2, base_backoff_ms=1)

    def always_fail():
        raise RuntimeError("Permanent failure")

    worm = _make_mock_worm(has_get_status=True,
                           status_result={"status": "minimal", "alive": False})
    result = ero.execute_with_recovery("BrokenWorm", worm, execute_fn=always_fail)

    assert result["outcome"] == "degraded", f"Expected degraded, got {result['outcome']}"
    assert result["recovery"]["strategy"] == "fallback"
    assert result["recovery"]["last_error"] == "Permanent failure"
    # Should have gotten the worm's get_status() fallback
    assert result["result"] == {"status": "minimal", "alive": False}


def test_ero_fallback_no_get_status():
    """Worm without get_status() returns safe degraded dict."""
    ero = ErrorRecoveryOrchestrator(max_retries=1, base_backoff_ms=1)

    def always_fail():
        raise RuntimeError("No recovery possible")

    # Use a class WITHOUT get_status — hasattr must return False
    class BareWorm:
        pass
    worm = BareWorm()
    result = ero.execute_with_recovery("NoStatusWorm", worm, execute_fn=always_fail)

    assert result["outcome"] == "degraded"
    assert result["result"]["fallback"] is True
    assert result["result"]["worm_type"] == "NoStatusWorm"
    assert "No recovery possible" in str(result["result"]["error"])


def test_ero_circuit_breaker_opens():
    """Consecutive failures above threshold open the circuit."""
    # max_retries=1 means range(1,2) = 1 attempt only, NO actual retry.
    # So each execute_with_recovery call = exactly 1 failure.
    # circuit_threshold=3 → trips on the 3rd call.
    ero = ErrorRecoveryOrchestrator(
        max_retries=1, base_backoff_ms=1,
        circuit_threshold=3, circuit_reset_seconds=60,
    )

    def always_fail():
        raise RuntimeError("fail")

    worm = _make_mock_worm()

    # Execute 3 times — circuit should trip during the 3rd call
    for i in range(3):
        result = ero.execute_with_recovery("FlakyWorm", worm, execute_fn=always_fail)
        if i < 2:
            assert result["outcome"] in ("degraded",), \
                f"Call {i+1}: expected degraded, got {result['outcome']}"

    # Now the circuit should be open
    assert ero._is_circuit_open("FlakyWorm"), "Circuit should be open after 3 failures"

    # Next call should return circuit_open immediately
    result = ero.execute_with_recovery("FlakyWorm", worm, execute_fn=always_fail)
    assert result["outcome"] == "circuit_open"
    assert result["recovery"]["strategy"] == "skip"


def test_ero_circuit_auto_reset():
    """Circuit resets after reset_seconds pass."""
    ero = ErrorRecoveryOrchestrator(
        max_retries=1, base_backoff_ms=1,
        circuit_threshold=2, circuit_reset_seconds=0.1,  # very short
    )

    def always_fail():
        raise RuntimeError("fail")

    worm = _make_mock_worm()
    for _ in range(2):
        ero.execute_with_recovery("ResettingWorm", worm, execute_fn=always_fail)

    assert ero._is_circuit_open("ResettingWorm"), "Circuit should be open"

    # Wait for auto-reset
    time.sleep(0.15)

    assert not ero._is_circuit_open("ResettingWorm"), \
        "Circuit should auto-reset after reset_seconds"


def test_ero_manual_reset():
    """reset_circuit() clears failure counts and circuit state."""
    ero = ErrorRecoveryOrchestrator(
        max_retries=1, base_backoff_ms=1,
        circuit_threshold=2, circuit_reset_seconds=300,
    )

    def always_fail():
        raise RuntimeError("fail")

    worm = _make_mock_worm()
    for _ in range(2):
        ero.execute_with_recovery("ManualResetWorm", worm, execute_fn=always_fail)

    assert ero._is_circuit_open("ManualResetWorm")

    result = ero.reset_circuit("ManualResetWorm")
    assert result["reset"] is True
    assert result["worm_type"] == "ManualResetWorm"

    assert not ero._is_circuit_open("ManualResetWorm")
    assert ero._failure_counts.get("ManualResetWorm", 0) == 0


def test_ero_periodic_cleanup_prevents_memory_leak():
    """Stale worm types are cleaned from internal dicts to prevent OOM."""
    ero = ErrorRecoveryOrchestrator(
        max_retries=1, base_backoff_ms=1,
        circuit_threshold=10,  # high — don't trip
        circuit_reset_seconds=0.05,  # very short cleanup window
    )

    def always_fail():
        raise RuntimeError("fail")

    worm = _make_mock_worm()

    # Add failures for several worm types
    for i in range(3):
        ero.execute_with_recovery(f"LeakyWorm_{i}", worm, execute_fn=always_fail)

    # All 3 should be in failure_windows
    assert len(ero._failure_windows) >= 3, \
        f"Expected >= 3 entries in failure_windows, got {len(ero._failure_windows)}"

    # Wait for the cleanup window to expire
    time.sleep(0.10)

    # Trigger execution to run periodic cleanup
    ero.execute_with_recovery("TriggerWorm", worm,
                              execute_fn=lambda: {"status": "ok"})

    # Stale worm types (outside the window) should be cleaned
    # LeakyWorm_0/1/2 should be gone since their failures are outside the 0.05s window
    stale_cleaned = all(
        f"LeakyWorm_{i}" not in ero._failure_windows or
        len(ero._failure_windows.get(f"LeakyWorm_{i}", [])) == 0
        for i in range(3)
    )
    assert stale_cleaned, \
        f"Stale worms should be cleaned. Remaining: {list(ero._failure_windows.keys())}"


def test_ero_get_stats_and_status():
    """get_stats() and get_circuit_breaker_status() report correctly."""
    ero = ErrorRecoveryOrchestrator(max_retries=3, circuit_threshold=2)

    stats = ero.get_stats()
    assert stats["version"] == "1.0.0"
    assert stats["total_attempts"] == 0
    assert stats["success_rate"] == 0.0
    assert "circuit_breaker" in stats
    assert stats["circuit_breaker"]["total_open"] == 0

    # Execute a successful worm
    ero.execute_with_recovery("StatsWorm", _make_mock_worm(),
                              execute_fn=lambda: {"ok": True})

    stats2 = ero.get_stats()
    assert stats2["total_attempts"] == 1
    assert stats2["total_successes"] == 1
    assert stats2["success_rate"] == 1.0


def test_ero_recovery_history_capped():
    """Recovery history is capped at 500 entries to prevent memory leak."""
    ero = ErrorRecoveryOrchestrator(max_retries=1, base_backoff_ms=1)

    def always_fail():
        raise RuntimeError("fail")

    worm = _make_mock_worm()
    # Generate 600 failure entries
    for i in range(600):
        ero.execute_with_recovery(f"HistoryWorm_{i % 10}", worm, execute_fn=always_fail)

    with ero._lock:
        assert len(ero._recovery_history) <= 500, \
            f"History should be capped at 500, got {len(ero._recovery_history)}"


# ═══════════════════════════════════════════════════════════════════
# DeadLetterQueue Standalone Tests
# ═══════════════════════════════════════════════════════════════════

def test_dlq_disk_persistence_and_recovery():
    """Enqueue letters, save to disk, new instance from same path recovers them."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq1 = DeadLetterQueue(storage_path=tmp_path)
        dlq1.clear()  # Start fresh

        dlq1.enqueue("SystemZ", {"error": "timeout", "type": "TimeoutError"})
        dlq1.enqueue("DiskZ", {"error": "disk full", "type": "OSError"})
        dlq1.enqueue("ProcessZ", {"error": "OOM", "type": "MemoryError"})

        assert dlq1.count_pending() == 3
        assert dlq1.count_total() == 3

        # Create a NEW instance pointing to the same file
        dlq2 = DeadLetterQueue(storage_path=tmp_path)

        assert dlq2.count_pending() == 3, \
            f"Expected 3 pending after reload, got {dlq2.count_pending()}"
        assert dlq2.count_total() == 3

        worms = {l.worm_name for l in dlq2.get_pending()}
        assert worms == {"SystemZ", "DiskZ", "ProcessZ"}

        dlq2.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_concurrent_replay_all():
    """Thread-safe replay_all with semaphore-gated concurrency."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq = DeadLetterQueue(storage_path=tmp_path)
        dlq.clear()

        # Enqueue 10 dead letters
        for i in range(10):
            dlq.enqueue(f"Worm_{i}", {"error": f"failure_{i}"})

        replay_results = []
        replay_lock = threading.Lock()

        def execute_fn(worm_name, config):
            with replay_lock:
                replay_results.append(worm_name)
            return {"success": True, "worm": worm_name}

        results = dlq.replay_all(execute_fn, max_concurrent=3)

        assert len(results) == 10, f"Expected 10 replay results, got {len(results)}"
        assert all(r["success"] for r in results), \
            f"All replays should succeed. Failed: {[r for r in results if not r['success']]}"
        assert dlq.count_pending() == 0, \
            f"All should be resolved, got {dlq.count_pending()} pending"

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_analytics():
    """analytics() correctly groups by worm, error type, and resolution."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq = DeadLetterQueue(storage_path=tmp_path)
        dlq.clear()

        # Enqueue with different error types
        dlq.enqueue("SystemZ", {"error": "timeout", "type": "TimeoutError"})
        dlq.enqueue("SystemZ", {"error": "timeout again", "type": "TimeoutError"})
        dlq.enqueue("DiskZ", {"error": "disk full", "type": "OSError"})
        dlq.enqueue("ProcessZ", {"error": "OOM", "type": "MemoryError"})

        analytics = dlq.analytics()

        assert analytics["total_letters"] == 4
        assert analytics["pending"] == 4
        assert analytics["resolved"] == 0

        # Top failing worms
        top_worms = {w["worm"]: w["count"] for w in analytics["top_failing_worms"]}
        assert top_worms.get("SystemZ") == 2
        assert top_worms.get("DiskZ") == 1

        # Top error types
        top_errors = {e["type"]: e["count"] for e in analytics["top_error_types"]}
        assert top_errors.get("TimeoutError") == 2

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_backoff_delay():
    """Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s... capped at 120s."""
    dl = DeadLetter(
        worm_name="TestWorm",
        error_message="test",
    )

    expected = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 120.0, 120.0, 120.0]
    for i, exp in enumerate(expected):
        dl.retry_count = i
        assert dl.backoff_delay() == exp, \
            f"retry_count={i}: expected {exp}, got {dl.backoff_delay()}"


def test_dlq_replay_resolves_success():
    """replay() marks resolved='retry_success' on successful execution."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq = DeadLetterQueue(storage_path=tmp_path)
        dlq.clear()

        letter = dlq.enqueue("RecoverableWorm", {"error": "transient"})
        assert letter.retry_count == 0

        result = dlq.replay(letter, lambda name, cfg: {"success": True, "data": "fixed"})

        assert result["success"] is True
        assert letter.retry_count == 1
        assert letter.resolved is True
        assert letter.resolution == "retry_success"

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_replay_exhausts_retries():
    """replay() marks resolved='permanent_failure' when max retries hit."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq = DeadLetterQueue(storage_path=tmp_path)
        dlq.clear()

        # Enqueue with max_retries=1 (only one retry)
        letter = dlq.enqueue(
            "HopelessWorm", {"error": "permanent"},
            max_retries=1,
        )
        assert letter.retry_count == 0
        assert letter.can_retry() is True

        # First replay — fails
        result = dlq.replay(letter, lambda name, cfg: {"success": False, "error": "still broken"})
        assert result["success"] is False
        assert letter.retry_count == 1

        # Now max retries exhausted
        assert letter.can_retry() is False
        assert letter.resolved is True
        assert letter.resolution == "permanent_failure"

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_enqueue_result():
    """enqueue_result() only enqueues failures, returns None for success."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq = DeadLetterQueue(storage_path=tmp_path)
        dlq.clear()

        # Successful result — should NOT enqueue
        result = dlq.enqueue_result("OkayWorm", {"success": True, "data": "fine"})
        assert result is None
        assert dlq.count_pending() == 0

        # Failed result — SHOULD enqueue
        result = dlq.enqueue_result("BadWorm", {"success": False, "error": "broke"})
        assert result is not None
        assert result.worm_name == "BadWorm"
        assert dlq.count_pending() == 1

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_discard_operations():
    """discard() and discard_all() mark letters as resolved."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq = DeadLetterQueue(storage_path=tmp_path)
        dlq.clear()

        dlq.enqueue("W1", {"error": "e1"})
        dlq.enqueue("W2", {"error": "e2"})
        dlq.enqueue("W3", {"error": "e3"})

        assert dlq.count_pending() == 3

        # Discard one
        pending = dlq.get_pending()
        dlq.discard(pending[0], reason="manual")

        assert dlq.count_pending() == 2

        # Discard all remaining
        dlq.discard_all(reason="cleanup")
        assert dlq.count_pending() == 0

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_max_queue_pruning():
    """When queue exceeds MAX_QUEUE_SIZE, oldest resolved letters are removed."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq = DeadLetterQueue(storage_path=tmp_path)
        dlq.clear()

        # Fill queue to exactly max (1000). Pruning only removes RESOLVED letters,
        # so to test pruning we need some resolved ones. Enqueue 995 pending +
        # 5 resolved, then overflow → old resolved ones get removed.
        max_size = dlq.MAX_QUEUE_SIZE

        # Enqueue 995 pending
        for i in range(max_size - 5):
            dlq.enqueue(f"Pending_{i}", {"error": f"err_{i}"})

        # Enqueue and immediately resolve 5 letters
        for i in range(5):
            letter = dlq.enqueue(f"Resolved_{i}", {"error": f"err_{i}"})
            letter.mark_resolved("retry_success")
        dlq._save()

        assert dlq.count_total() == max_size

        # Add one more — should trigger pruning of resolved letters
        dlq.enqueue("OverflowWorm", {"error": "overflow"})

        # After pruning, total should be <= max_size
        assert dlq.count_total() <= max_size, \
            f"Queue should not exceed {max_size}, got {dlq.count_total()}"
        # OverflowWorm should be in the queue
        assert any(l.worm_name == "OverflowWorm" for l in dlq.get_all()), \
            "OverflowWorm should be in the queue"

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_clear_resolved():
    """clear_resolved() removes resolved letters, keeps pending."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        dlq = DeadLetterQueue(storage_path=tmp_path)
        dlq.clear()

        letter1 = dlq.enqueue("ResolvedWorm", {"error": "e1"})
        dlq.enqueue("PendingWorm", {"error": "e2"})

        # Mark first as resolved
        letter1.mark_resolved("retry_success")
        dlq._save()

        assert dlq.count_total() == 2
        assert dlq.count_pending() == 1

        dlq.clear_resolved()

        assert dlq.count_total() == 1
        assert dlq.count_pending() == 1
        pending = dlq.get_pending()
        assert pending[0].worm_name == "PendingWorm"

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


def test_dlq_corrupt_file_recovery():
    """Corrupt JSON file should not crash — starts fresh."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # Write corrupt content
        tmp_path.write_text("{ this is not valid JSON !!! }", encoding="utf-8")

        # Should not crash
        dlq = DeadLetterQueue(storage_path=tmp_path)
        assert dlq.count_total() == 0, "Corrupt file should result in empty queue"

        dlq.clear()
    finally:
        tmp_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════
# Test Runner
# ═══════════════════════════════════════════════════════════════════

def run_test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  [OK] {name}")
        passed += 1
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        failed += 1
        errors_list.append(f"{name}: {e}")


def main():
    global passed, failed, errors_list
    print("=" * 64)
    print("  Error Recovery Tests — P1-12")
    print("=" * 64)
    print()
    print("  Modules:")
    print("    CORE/WORMS/expansion/error_recovery.py  (ErrorRecoveryOrchestrator)")
    print("    CORE/WORMS/dead_letter_queue.py         (DeadLetterQueue)")
    print()
    print("  Tests:")
    print()

    tests = [
        # ErrorRecoveryOrchestrator
        ("Retry until success", test_ero_retry_until_success),
        ("Exhaustion → fallback (get_status)", test_ero_exhaustion_and_fallback),
        ("Fallback without get_status()", test_ero_fallback_no_get_status),
        ("Circuit breaker opens on threshold", test_ero_circuit_breaker_opens),
        ("Circuit auto-reset after timeout", test_ero_circuit_auto_reset),
        ("Manual circuit reset", test_ero_manual_reset),
        ("Periodic cleanup prevents memory leak", test_ero_periodic_cleanup_prevents_memory_leak),
        ("get_stats / get_circuit_breaker_status", test_ero_get_stats_and_status),
        ("Recovery history capped at 500", test_ero_recovery_history_capped),

        # DeadLetterQueue standalone
        ("DLQ disk persistence + recovery", test_dlq_disk_persistence_and_recovery),
        ("DLQ concurrent replay_all", test_dlq_concurrent_replay_all),
        ("DLQ analytics grouping", test_dlq_analytics),
        ("DLQ exponential backoff", test_dlq_backoff_delay),
        ("DLQ replay resolves success", test_dlq_replay_resolves_success),
        ("DLQ replay exhausts retries", test_dlq_replay_exhausts_retries),
        ("DLQ enqueue_result filtering", test_dlq_enqueue_result),
        ("DLQ discard operations", test_dlq_discard_operations),
        ("DLQ max queue pruning", test_dlq_max_queue_pruning),
        ("DLQ clear_resolved", test_dlq_clear_resolved),
        ("DLQ corrupt file recovery", test_dlq_corrupt_file_recovery),
    ]

    for name, fn in tests:
        run_test(name, fn)

    print()
    print(f"  Results: {passed} passed, {failed} failed")
    if errors_list:
        print("  Errors:")
        for e in errors_list:
            print(f"    {e}")
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
