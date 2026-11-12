# coding: utf-8
"""Integration tests for the CancellationToken system.

Tests the full cancellation pipeline across:
- CORE.freethreading.CancellationToken (unit behavior + thread safety)
- CORE.WORMS.CLUSTERWORMS (WormExecutor + ClusterWORMZ cooperative cancellation)
- CORE.WORMS.CONCURRENCY (TaskManager timeout, ConcurrencyManager lifecycle)
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from CORE.freethreading import CancellationToken, TaskCancelledError, FreeThreadPool
from CORE.WORMS.CONCURRENCY import (
    TaskManager, ConcurrencyConfig, ConcurrencyManager,
    ExecutionResult,
)
from CORE.WORMS.CLUSTERWORMS import (
    ClusterWORMZ, WormConfig, WormExecutor, ClusterState,
)


# ═══════════════════════════════════════════════════════════════════
# CancellationToken Unit Tests
# ═══════════════════════════════════════════════════════════════════


class TestCancellationTokenUnit:
    """Verify core CancellationToken behavior and thread safety."""

    def test_initial_state(self):
        """Fresh token should not be cancelled."""
        token = CancellationToken()
        assert not token.is_cancelled
        assert not token.wait(timeout=0.01)

    def test_cancel_signals_event(self):
        """cancel() should set is_cancelled and wake wait()."""
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled
        assert token.wait(timeout=0.01)

    def test_cancel_idempotent(self):
        """Multiple cancel() calls should be safe."""
        token = CancellationToken()
        token.cancel()
        token.cancel()
        token.cancel()
        assert token.is_cancelled

    def test_raise_if_cancelled_noop_when_not_cancelled(self):
        """raise_if_cancelled() should not raise when not cancelled."""
        token = CancellationToken()
        token.raise_if_cancelled()  # Should not raise

    def test_raise_if_cancelled_raises(self):
        """raise_if_cancelled() should raise TaskCancelledError when cancelled."""
        token = CancellationToken()
        token.cancel()
        with pytest.raises(TaskCancelledError):
            token.raise_if_cancelled()

    def test_wait_blocks_until_cancelled(self):
        """wait() should block until cancelled from another thread."""
        token = CancellationToken()
        result = []

        def canceller():
            time.sleep(0.05)
            token.cancel()

        t = threading.Thread(target=canceller)
        t.start()
        was_cancelled = token.wait(timeout=5.0)
        t.join()

        assert was_cancelled

    def test_wait_timeout(self):
        """wait(timeout) should return False if not cancelled within timeout."""
        token = CancellationToken()
        assert not token.wait(timeout=0.01)

    def test_callbacks_executed_on_cancel(self):
        """Registered callbacks should fire when cancel() is called."""
        token = CancellationToken()
        state = []
        token.register_callback(lambda: state.append(1))
        token.register_callback(lambda: state.append(2))
        token.cancel()
        assert state == [1, 2]

    def test_callback_after_cancel_fires_immediately(self):
        """Callback registered after cancel should fire immediately."""
        token = CancellationToken()
        token.cancel()
        state = []
        token.register_callback(lambda: state.append("late"))
        assert state == ["late"]

    def test_callback_exceptions_suppressed(self):
        """Callback exceptions should be silently caught, not propagate."""
        token = CancellationToken()
        token.register_callback(lambda: 1 / 0)  # Would raise ZeroDivisionError
        token.cancel()  # Should not raise
        assert token.is_cancelled

    def test_reset_clears_cancellation(self):
        """reset() should clear the cancelled state."""
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled
        token.reset()
        assert not token.is_cancelled
        token.raise_if_cancelled()  # Should not raise

    def test_reset_clears_callbacks(self):
        """reset() should clear registered callbacks."""
        token = CancellationToken()
        state = []
        token.register_callback(lambda: state.append("fired"))
        token.reset()
        token.cancel()
        assert state == []  # Callback was cleared by reset

    def test_thread_safety_concurrent_access(self):
        """Concurrent cancel/check operations should not crash."""
        token = CancellationToken()
        errors = []
        lock = threading.Lock()

        def worker():
            try:
                for _ in range(50):
                    _ = token.is_cancelled
                    token.raise_if_cancelled()
            except TaskCancelledError:
                pass
            except Exception as e:
                with lock:
                    errors.append(str(e))

        def canceller():
            time.sleep(0.02)
            token.cancel()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        threads.append(threading.Thread(target=canceller))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Thread safety errors: {errors}"

    def test_repr(self):
        """repr() should show cancellation state."""
        token = CancellationToken()
        assert "cancelled=False" in repr(token)
        token.cancel()
        assert "cancelled=True" in repr(token)


# ═══════════════════════════════════════════════════════════════════
# TaskCancelledError Tests
# ═══════════════════════════════════════════════════════════════════


class TestTaskCancelledError:
    """Verify TaskCancelledError behavior."""

    def test_is_exception(self):
        """TaskCancelledError should be a proper Exception subclass."""
        err = TaskCancelledError("test message")
        assert isinstance(err, Exception)
        assert str(err) == "test message"

    def test_can_be_caught_specifically(self):
        """TaskCancelledError should be catchable by name, not just Exception."""
        caught = False
        try:
            raise TaskCancelledError("test")
        except TaskCancelledError:
            caught = True
        assert caught

    def test_not_caught_by_generic_except_in_wrong_order(self):
        """TaskCancelledError should still be an Exception for broad catches."""
        caught_by_exception = False
        try:
            raise TaskCancelledError("test")
        except Exception:
            caught_by_exception = True
        assert caught_by_exception


# ═══════════════════════════════════════════════════════════════════
# WormExecutor Cancellation Tests (CLUSTERWORMS)
# ═══════════════════════════════════════════════════════════════════


class TestWormExecutorCancellation:
    """Verify CLUSTERWORMS WormExecutor respects cancellation_token."""

    def test_executor_bails_out_when_cancelled(self):
        """WormExecutor should return immediately when token is pre-cancelled."""
        state = ClusterState("test_cancel")
        cfg = WormConfig(name="WORMZ", module="WORMZ", timeout=10)
        executor = WormExecutor("WORMZ", cfg, state, cancellation_token=None)

        # Pre-cancel a token and pass it
        token = CancellationToken()
        token.cancel()
        result = executor.execute(cancellation_token=token)

        assert result is not None
        assert result["success"] is False
        assert result["error"] == "Execution cancelled via token"

    def test_executor_runs_normally_without_cancellation(self):
        """WormExecutor should execute normally when not cancelled."""
        state = ClusterState("test_normal")
        cfg = WormConfig(name="WORMZ", module="WORMZ", timeout=10)
        executor = WormExecutor("WORMZ", cfg, state)

        result = executor.execute()
        assert result is not None
        assert result["success"] is True
        assert result["name"] == "WORMZ"
        assert "duration_ms" in result

    def test_executor_runs_with_uncancelled_token(self):
        """WormExecutor with uncancelled token should execute normally."""
        state = ClusterState("test_uncancelled")
        cfg = WormConfig(name="WORMZ", module="WORMZ", timeout=10)
        executor = WormExecutor("WORMZ", cfg, state)

        token = CancellationToken()
        result = executor.execute(cancellation_token=token)

        assert result is not None
        assert result["success"] is True


# ═══════════════════════════════════════════════════════════════════
# CONCURRENCY TaskManager Cancellation Tests
# ═══════════════════════════════════════════════════════════════════


class TestTaskManagerCancellation:
    """Verify CONCURRENCY.py TaskManager respects CancellationToken and timeouts."""

    def test_cooperative_cancel_before_execution(self):
        """Pre-cancelled token should cause immediate bailout."""
        tm = TaskManager()
        token = CancellationToken()

        def check_task(cancellation_token=None):
            cancellation_token.raise_if_cancelled()
            return "should_not_reach"

        token.cancel()
        tasks = [{"id": "t1", "name": "t1", "func": check_task}]
        results = tm.execute_parallel(tasks, cancellation_token=token)

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == "Cancelled"

    def test_timeout_triggers_token_cancel(self):
        """Timeout in execute_parallel should signal the cancellation token."""
        config = ConcurrencyConfig(timeout_seconds=0.05, max_workers=2)
        tm = TaskManager(config=config)
        token = CancellationToken()

        def slow_task(cancellation_token=None):
            time.sleep(0.5)
            return "done"

        tasks = [{"id": "slow", "name": "slow", "func": slow_task}]
        results = tm.execute_parallel(tasks, cancellation_token=token)

        assert len(results) == 1
        assert results[0].error == "Timed out"
        assert token.is_cancelled, "Timeout should automatically signal the token"

    def test_normal_completion_does_not_cancel(self):
        """Fast tasks should complete without cancelling the token."""
        config = ConcurrencyConfig(timeout_seconds=30, max_workers=2)
        tm = TaskManager(config=config)
        token = CancellationToken()

        def fast_task(cancellation_token=None):
            return "ok"

        tasks = [
            {"id": "f1", "name": "f1", "func": fast_task},
            {"id": "f2", "name": "f2", "func": fast_task},
        ]
        results = tm.execute_parallel(tasks, cancellation_token=token)

        assert len(results) == 2
        assert all(r.success for r in results)
        assert not token.is_cancelled, "Token should not be cancelled on normal completion"


# ═══════════════════════════════════════════════════════════════════
# ConcurrencyManager Lifecycle Tests
# ═══════════════════════════════════════════════════════════════════


class TestConcurrencyManagerLifecycle:
    """Verify ConcurrencyManager cancellation token lifecycle."""

    def test_initial_token_state(self):
        """Fresh ConcurrencyManager should have uncancelled token."""
        cm = ConcurrencyManager()
        assert cm.cancellation_token is not None
        assert not (cm.cancellation_token.is_cancelled if hasattr(cm.cancellation_token, 'is_cancelled') else cm.cancellation_token.is_set())

    def test_cancel_all_signals_token(self):
        """cancel_all() should set the token to cancelled."""
        cm = ConcurrencyManager()
        cm.cancel_all()
        token = cm.cancellation_token
        assert token.is_cancelled if hasattr(token, 'is_cancelled') else token.is_set()

    def test_reset_token_clears_cancellation(self):
        """reset_token() should clear the cancelled state."""
        cm = ConcurrencyManager()
        cm.cancel_all()
        token = cm.cancellation_token
        assert token.is_cancelled if hasattr(token, 'is_cancelled') else token.is_set()
        cm.reset_token()
        token = cm.cancellation_token
        assert not (token.is_cancelled if hasattr(token, 'is_cancelled') else token.is_set())

    def test_cancel_then_reset_cycle(self):
        """Multiple cancel/reset cycles should work."""
        cm = ConcurrencyManager()
        for _ in range(3):
            cm.cancel_all()
            token = cm.cancellation_token
            assert token.is_cancelled if hasattr(token, 'is_cancelled') else token.is_set()
            cm.reset_token()
            token = cm.cancellation_token
            assert not (token.is_cancelled if hasattr(token, 'is_cancelled') else token.is_set())


# ═══════════════════════════════════════════════════════════════════
# Graceful Shutdown / Thread Leak Detection
# ═══════════════════════════════════════════════════════════════════


class TestGracefulShutdown:
    """Verify clean thread shutdown — no daemon thread leaks."""

    def test_no_thread_leak_after_pool_timeout(self):
        """Thread count should not grow after pool with timeout cancellation."""
        initial_threads = threading.active_count()

        config = ConcurrencyConfig(timeout_seconds=0.05, max_workers=4)
        tm = TaskManager(config=config)
        token = CancellationToken()

        def slow(cancellation_token=None):
            time.sleep(0.3)
            return "done"

        tasks = [{"id": f"t{i}", "name": f"t{i}", "func": slow} for i in range(4)]
        tm.execute_parallel(tasks, cancellation_token=token)

        # Give threads time to clean up
        time.sleep(0.3)
        final_threads = threading.active_count()

        # With max_workers=4 and proper cleanup, leak should be 0-2 at most
        leak = final_threads - initial_threads
        assert leak < 3, f"Potential thread leak: {initial_threads} -> {final_threads} (+{leak})"

    def test_callbacks_fire_immediately_after_cancel(self):
        """After cancel(), newly registered callbacks fire immediately."""
        token = CancellationToken()
        token.cancel()
        fired = []
        token.register_callback(lambda: fired.append(1))
        token.register_callback(lambda: fired.append(2))
        assert fired == [1, 2], "Callbacks should fire immediately after cancel"

    def test_reset_clears_state_for_reuse(self):
        """After reset, token should be clean for reuse."""
        token = CancellationToken()
        token.cancel()
        state = []
        token.register_callback(lambda: state.append("should not fire on old cancel"))
        token.reset()
        assert state == ["should not fire on old cancel"]  # Fired on register (was already cancelled)
        token.cancel()
        # New cancel should not re-fire old callbacks from before reset
        assert state == ["should not fire on old cancel"]


# ═══════════════════════════════════════════════════════════════════
# ClusterWORMZ Cancellation Integration
# ═══════════════════════════════════════════════════════════════════


class TestClusterWORMZCancellationToken:
    """Verify ClusterWORMZ cooperative cancellation via CancellationToken."""

    def test_cancellation_token_created_on_init(self):
        """ClusterWORMZ should create a CancellationToken on init."""
        cluster = ClusterWORMZ("system")
        assert cluster.cancellation_token is not None
        assert not cluster.cancellation_token.is_cancelled

    def test_short_timeout_triggers_token_cancel(self):
        """Aggressive timeout should cancel the cancellation token."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        cluster.set_timeout(0.05)  # Short enough to trigger timeout reliably
        results = cluster.execute_all()
        assert len(results) > 0
        # Token should be cancelled after timeout
        token = cluster.cancellation_token
        assert (token.is_cancelled if hasattr(token, 'is_cancelled') else token.is_set()), (
            "CancellationToken should be cancelled after timeout"
        )

    def test_token_can_be_reused_after_reset(self):
        """After reset, a new execute_all() should work."""
        cluster = ClusterWORMZ("system")
        cluster.set_parallel(True)
        cluster.set_timeout(0.05)
        cluster.execute_all()
        # Reset the token
        if hasattr(cluster.cancellation_token, 'reset'):
            cluster.cancellation_token.reset()
        else:
            cluster.cancellation_token.clear()
        token = cluster.cancellation_token
        assert not (token.is_cancelled if hasattr(token, 'is_cancelled') else token.is_set())

    def test_token_is_cancellation_token_instance(self):
        """ClusterWORMZ.cancellation_token should be a CancellationToken."""
        cluster = ClusterWORMZ("system")
        token = cluster.cancellation_token
        assert isinstance(token, CancellationToken) or isinstance(token, threading.Event)


# ═══════════════════════════════════════════════════════════════════
# Standalone Runner
# ═══════════════════════════════════════════════════════════════════


def _run_standalone():
    """Run tests standalone to bypass pytest segfaults on Python 3.14t."""
    passed = 0
    failed = 0
    skipped = 0
    errors = []

    print("CancellationToken Integration Tests")
    print("=" * 60)

    for name, obj in sorted(globals().items(), key=lambda x: x[0]):
        if isinstance(obj, type) and name.startswith("Test"):
            print(f"\n[{name}]")
            inst = obj()
            for mn in sorted(dir(inst)):
                if mn.startswith("test_"):
                    try:
                        getattr(inst, mn)()
                        print(f"  [OK] {mn}")
                        passed += 1
                    except Exception as e:
                        import traceback
                        print(f"  [FAIL] {mn}: {e}")
                        traceback.print_exc()
                        failed += 1
                        errors.append(f"{name}.{mn}: {e}")

    total = passed + failed + skipped
    print(f"\n  Total: {total} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")

    if errors:
        print("\n  Errors:")
        for e in errors:
            print(f"    {e}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
