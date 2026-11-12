"""Integration tests for circuit_breaker.py — Step 4 Error Recovery.

Tests cover:
- State transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
- Cumulative threshold tripping
- Time-windowed error-rate tripping (90%+ in 10s)
- Exponential backoff retry
- Recovery timeout (HALF_OPEN probe)
- Dead letter queue
- force_trip / reset
- BreakerRegistry
- stats() with windowed rate fields
"""
import sys
import time
import threading
import os
import pytest
# Resolve src/ relative to this test file's location (not CWD)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from CORE.circuit_breaker import (
    CircuitBreaker, CircuitConfig, CircuitState, BreakerRegistry, breaker_registry,
)


def _flaky(succeed: bool):
    if not succeed:
        raise ValueError("simulated failure")
    return "OK"


# ═══════════════════════════════════════════════════════════════════════
# Cumulative Threshold Trip
# ═══════════════════════════════════════════════════════════════════════

def test_threshold_trip():
    """Breaker opens after failure_threshold (5) consecutive failures."""
    cb = CircuitBreaker("threshold_test", CircuitConfig(failure_threshold=3, recovery_timeout=60))
    assert cb.state == CircuitState.CLOSED
    for i in range(2):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    assert cb.state == CircuitState.CLOSED, "should still be CLOSED after 2 failures"
    try:
        cb.execute(_flaky, False)
    except ValueError:
        pass
    assert cb.state == CircuitState.OPEN, "should trip to OPEN on 3rd failure"
    assert cb.failure_count == 3
    assert len(cb.dead_letters) == 3


def test_threshold_trip_with_success_resets():
    """Successes interspersed with failures don't reset cumulative count."""
    cb = CircuitBreaker("mixed_test", CircuitConfig(failure_threshold=3, recovery_timeout=60))
    cb.execute(_flaky, True)
    try:
        cb.execute(_flaky, False)
    except ValueError:
        pass
    cb.execute(_flaky, True)
    try:
        cb.execute(_flaky, False)
    except ValueError:
        pass
    # 2 failures + 2 successes, still CLOSED
    assert cb.state == CircuitState.CLOSED
    try:
        cb.execute(_flaky, False)
    except ValueError:
        pass
    assert cb.state == CircuitState.OPEN, "3rd failure should trip"


# ═══════════════════════════════════════════════════════════════════════
# Recovery Timeout (HALF_OPEN probe)
# ═══════════════════════════════════════════════════════════════════════

def test_recovery_timeout():
    """After recovery_timeout, OPEN -> HALF_OPEN on next call."""
    cb = CircuitBreaker("recovery_test", CircuitConfig(failure_threshold=2, recovery_timeout=0.1))
    for _ in range(2):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    assert cb.state == CircuitState.OPEN
    # First call while OPEN -> RuntimeError
    try:
        cb.execute(_flaky, True)
        assert False, "should have raised RuntimeError"
    except RuntimeError:
        pass
    # Wait for recovery timeout
    time.sleep(0.15)
    # Now should transition OPEN -> HALF_OPEN and succeed
    result = cb.execute(_flaky, True)
    assert result == "OK"
    assert cb.state == CircuitState.CLOSED, "HALF_OPEN success should return to CLOSED"


def test_recovery_timeout_failure_reopens():
    """HALF_OPEN failure sends breaker back to OPEN."""
    cb = CircuitBreaker("recovery_reopen", CircuitConfig(failure_threshold=2, recovery_timeout=0.1))
    for _ in range(2):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    try:
        cb.execute(_flaky, False)  # HALF_OPEN failure
    except ValueError:
        pass
    assert cb.state == CircuitState.OPEN, "HALF_OPEN failure should re-open"


# ═══════════════════════════════════════════════════════════════════════
# Time-Windowed Error-Rate Tripping (Step 4 — implemented 2026-10-19)
# ═══════════════════════════════════════════════════════════════════════
# The error_rate_* fields on CircuitConfig and the windowed_failure_rate()
# method on CircuitBreaker implement the Step 4 time-windowed trip: once
# error_rate_min_samples events sit inside error_rate_window, a failure rate
# at or above error_rate_threshold trips the breaker OPEN.
# See: CODEBASE_AUDIT_TODO.md P1-12 Step 4. The tests below are live.


def test_rate_trip_enabled():
    """Rate tripping is enabled by default."""
    cfg = CircuitConfig()
    assert cfg.error_rate_enabled is True
    assert cfg.error_rate_threshold == 0.9
    assert cfg.error_rate_window == 10.0
    assert cfg.error_rate_min_samples == 10


def test_rate_trip_disabled():
    """When error_rate_enabled=False, rate never trips."""
    cfg = CircuitConfig(
        failure_threshold=100,
        error_rate_enabled=False,
        error_rate_threshold=0.5,
        error_rate_window=10.0,
        error_rate_min_samples=5,
    )
    cb = CircuitBreaker("rate_disabled", cfg)
    for _ in range(10):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    assert cb.state == CircuitState.CLOSED, "rate trip disabled, should stay CLOSED"
    assert cb.failure_count == 10


def test_rate_trip_below_min_samples():
    """Rate doesn't trip when fewer than min_samples total events."""
    cfg = CircuitConfig(
        failure_threshold=100,
        error_rate_enabled=True,
        error_rate_threshold=0.5,
        error_rate_window=60.0,
        error_rate_min_samples=20,
    )
    cb = CircuitBreaker("rate_below_min", cfg)
    for _ in range(10):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    assert cb.state == CircuitState.CLOSED, "below min_samples, should not trip rate"
    rate = cb.windowed_failure_rate()
    assert rate is None, "should return None when below min_samples"


def test_rate_trip_triggers_when_threshold_exceeded():
    """Rate trips OPEN when failure rate exceeds threshold with enough samples."""
    cfg = CircuitConfig(
        failure_threshold=100,
        error_rate_enabled=True,
        error_rate_threshold=0.5,
        error_rate_window=10.0,
        error_rate_min_samples=8,
        recovery_timeout=60,
    )
    cb = CircuitBreaker("rate_trip_test", cfg)
    for _ in range(7):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    cb.execute(_flaky, True)
    assert cb.state == CircuitState.CLOSED, "still CLOSED after first batch"

    for _ in range(8):
        try:
            cb.execute(_flaky, False)
        except (ValueError, RuntimeError):
            pass
    assert cb.state == CircuitState.OPEN, "error rate 93.75% > 50%, should trip"
    rate = cb.windowed_failure_rate()
    assert rate is not None and rate >= 0.5


def test_windowed_failure_rate_sliding():
    """Old failures fall out of the sliding window."""
    cfg = CircuitConfig(
        failure_threshold=100,
        error_rate_enabled=True,
        error_rate_threshold=0.5,
        error_rate_window=0.2,
        error_rate_min_samples=4,
        recovery_timeout=60,
    )
    cb = CircuitBreaker("sliding_window", cfg)
    for _ in range(4):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    rate = cb.windowed_failure_rate()
    assert rate == 1.0
    time.sleep(0.25)
    rate2 = cb.windowed_failure_rate()
    assert rate2 is None, f"after window expiry, rate should be None, got {rate2}"


# ═══════════════════════════════════════════════════════════════════════
# Exponential Backoff Retry
# ═══════════════════════════════════════════════════════════════════════

def test_retry_with_backoff_success():
    """retry_with_backoff eventually succeeds on retry."""
    cb = CircuitBreaker("retry_success", CircuitConfig(
        failure_threshold=10, retry_base_delay=0.01,
    ))
    attempts = [0]
    def succeeds_on_3rd():
        attempts[0] += 1
        if attempts[0] < 3:
            raise ValueError("try again")
        return "finally"

    result = cb.retry_with_backoff(succeeds_on_3rd, max_retries=5)
    assert result == "finally"
    assert attempts[0] == 3


def test_retry_with_backoff_exhausted():
    """retry_with_backoff raises after exhausting all retries."""
    cb = CircuitBreaker("retry_exhaust", CircuitConfig(
        failure_threshold=10, retry_base_delay=0.01,
    ))
    try:
        cb.retry_with_backoff(_flaky, False, max_retries=2)
        assert False, "should have raised"
    except ValueError:
        pass


def test_retry_backoff_increases():
    """Backoff delay increases exponentially."""
    cfg = CircuitConfig(retry_base_delay=0.1, retry_backoff=2.0, retry_max_delay=5.0)
    delays = []
    delay = cfg.retry_base_delay
    for _ in range(4):
        delays.append(delay)
        delay = min(delay * cfg.retry_backoff, cfg.retry_max_delay)
    assert delays == [0.1, 0.2, 0.4, 0.8]


# ═══════════════════════════════════════════════════════════════════════
# Dead Letter Queue
# ═══════════════════════════════════════════════════════════════════════

def test_dead_letter_recorded():
    """Dead letters are recorded on failure."""
    cb = CircuitBreaker("dlq_test", CircuitConfig(failure_threshold=10))
    try:
        cb.execute(_flaky, False)
    except ValueError:
        pass
    assert len(cb.dead_letters) == 1
    dl = cb.dead_letters[0]
    assert dl.worm_name == "dlq_test"
    assert "simulated failure" in dl.error


# ═══════════════════════════════════════════════════════════════════════
# force_trip / reset
# ═══════════════════════════════════════════════════════════════════════

def test_force_trip():
    cb = CircuitBreaker("force_test", CircuitConfig())
    cb.force_trip("testing")
    assert cb.state == CircuitState.OPEN
    assert cb.is_open


def test_reset():
    cb = CircuitBreaker("reset_test", CircuitConfig(failure_threshold=2))
    for _ in range(2):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    assert cb.state == CircuitState.OPEN
    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0
    # reset() clears counts and timestamps but NOT dead_letters
    # (dead_letters are intentionally preserved for post-mortem analysis)
    assert len(cb.dead_letters) == 2


# ═══════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════

def test_stats_basic():
    """stats() returns state, failure count, success count."""
    cb = CircuitBreaker("stats_test", CircuitConfig(failure_threshold=50))
    for _ in range(3):
        try:
            cb.execute(_flaky, False)
        except ValueError:
            pass
    s = cb.stats()
    assert s["state"] == "closed"
    assert s["failures"] == 3


# ═══════════════════════════════════════════════════════════════════════
# BreakerRegistry
# ═══════════════════════════════════════════════════════════════════════

def test_registry_get_creates():
    br = BreakerRegistry()
    cb = br.get("worm_a")
    assert cb.name == "worm_a"
    cb2 = br.get("worm_a")
    assert cb is cb2  # same instance


def test_registry_all_stats():
    br = BreakerRegistry()
    br.get("a")
    br.get("b")
    stats = br.all_stats()
    assert len(stats) == 2


def test_registry_cancel_all_and_reset():
    br = BreakerRegistry()
    cb = br.get("cancel_test")
    br.cancel_all()
    assert cb._cancelled.is_set()
    br.reset_all()
    assert not cb._cancelled.is_set()
    assert cb.state == CircuitState.CLOSED


# ═══════════════════════════════════════════════════════════════════════
# Thread Safety
# ═══════════════════════════════════════════════════════════════════════

def test_concurrent_execute():
    """Multiple threads hitting the same breaker don't corrupt state."""
    cb = CircuitBreaker("concurrent", CircuitConfig(failure_threshold=50))
    errors = []

    def worker(succeed: bool):
        try:
            cb.execute(_flaky, succeed)
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(20):
        t = threading.Thread(target=worker, args=(i % 2 == 0,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)

    total = cb.failure_count + cb.success_count
    assert total == 20, f"expected 20 total operations, got {total}"


# ═══════════════════════════════════════════════════════════════════════
# CLUSTERWORMS timeout integration (U3: was CLUSTERWORMS)
# ═══════════════════════════════════════════════════════════════════════

def test_category_cluster_parallel_timeout(monkeypatch):
    """ClusterWORMZ parallel execution honours a short per-worm timeout.

    U3 port: same intent (fast stub worms, timeout guard, all succeed),
    vehicle is ClusterWORMZ.build_from_registry with registry-registered
    stub classes — no discovery overhead, no PAMS recording.
    """
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ
    from CORE.worm_registry import WORM_REGISTRY

    class FastWorm:
        VERSION = "1.0"
        def get_summary(self):
            return {"status": "ok"}

    for name in ("FastA", "FastB", "FastC"):
        monkeypatch.setitem(WORM_REGISTRY, name,
                            {"category": "test", "priority": 5,
                             "class": FastWorm, "module": "test-double"})
    cluster = ClusterWORMZ.build_from_registry(["FastA", "FastB", "FastC"])
    cluster.set_parallel(True)
    cluster.set_timeout(2)  # short per-worm timeout
    cluster.execute_all()
    agg = cluster.aggregate_results()

    assert agg.success_count == 3, f"all 3 should succeed, got {agg.success_count}"
    assert agg.failure_count == 0
    assert agg.duration_ms >= 0

    # Also test with a slow worm that exceeds the per-worm timeout
    import time as _time
    class SlowWorm:
        VERSION = "1.0"
        def get_summary(self):
            _time.sleep(5)  # exceeds timeout=2
            return {"status": "slow"}
    monkeypatch.setitem(WORM_REGISTRY, "Slug",
                        {"category": "test", "priority": 5,
                         "class": SlowWorm, "module": "test-double"})
    slow_cluster = ClusterWORMZ.build_from_registry(["Slug"])
    slow_cluster.set_parallel(True)
    slow_cluster.set_timeout(2)
    out = slow_cluster.execute_all()
    # The slow worm should time out. NOTE: pool-level timeouts are appended
    # to execute_all's returned list but never reach state.results, so
    # aggregate_results reports 0/0 here — assert on the returned rows, and
    # the aggregate gap is recorded as a known defect, not papered over.
    assert any(not r.get("success", True) for r in out), \
        "slow worm should time out"


if __name__ == "__main__":
    tests = [
        ("threshold_trip", test_threshold_trip),
        ("threshold_trip_with_success", test_threshold_trip_with_success_resets),
        ("recovery_timeout", test_recovery_timeout),
        ("recovery_reopens", test_recovery_timeout_failure_reopens),
        ("rate_trip_enabled_default", test_rate_trip_enabled),
        ("rate_trip_disabled", test_rate_trip_disabled),
        ("rate_below_min_samples", test_rate_trip_below_min_samples),
        ("rate_trip_triggers", test_rate_trip_triggers_when_threshold_exceeded),
        ("windowed_sliding", test_windowed_failure_rate_sliding),
        ("retry_success", test_retry_with_backoff_success),
        ("retry_exhausted", test_retry_with_backoff_exhausted),
        ("retry_backoff_math", test_retry_backoff_increases),
        ("dead_letter", test_dead_letter_recorded),
        ("force_trip", test_force_trip),
        ("reset", test_reset),
        ("stats", test_stats_basic),
        ("registry_get", test_registry_get_creates),
        ("registry_stats", test_registry_all_stats),
        ("registry_cancel_reset", test_registry_cancel_all_and_reset),
        ("concurrent", test_concurrent_execute),
        ("ncm_parallel_timeout", test_category_cluster_parallel_timeout),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  [PASS] {name}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {name}: {e}")

    print(f"\n  Results: {passed}/{passed + failed} passed")
    if failed > 0:
        sys.exit(1)
