#!/usr/bin/env python3
# coding: utf-8
"""
P1-11 Cluster Integration Tests — Production Readiness Validation
=================================================================

Covers the three highest-value gaps in cluster integration test coverage:
1. Full 89-worm orchestration validation (smoke test on all registered worms)
2. PAMS mock testing (verify PamsRecorder calls happen during cluster execution)
3. _is_circular_category() edge cases (from T18/T19 sessions)

Existing coverage: 195 tests across 6 files. This adds ~20 more targeting
production-critical paths not covered by the existing suite.

Run standalone (3.14t compatible):
    python3 tests/test_cluster_integration_p1.py
"""

import sys
import os
import re
import time
import threading
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

# ═══════════════════════════════════════════════════════════════════
# Test Infrastructure
# ═══════════════════════════════════════════════════════════════════

passed = 0
failed = 0
errors_list: list[str] = []


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


# ═══════════════════════════════════════════════════════════════════
# Section 1: 89-Worm Full Orchestration Validation
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.slow  # executes 200+ worms incl. daemons/threads: must run in its
# own process (see --slow-solo). Leftover worker state AVs later parallel
# tests in the same process on 3.15 (measured).
def test_all_worms_execute_without_crash():
    """Every registered worm should execute via dynamic resolution without crashing."""
    from CORE.worm_registry import WORM_REGISTRY, discover_worms
    from CORE.WORMS.CLUSTERWORMS import _resolve_worm_dynamic

    discover_worms()
    worm_names = sorted(WORM_REGISTRY.keys())

    assert len(worm_names) >= 200, f"Expected >= 200 worms, got {len(worm_names)}"

    failed_worms = []
    crash_worms = []

    for name in worm_names:
        try:
            result = _resolve_worm_dynamic(name)
            if result is None:
                failed_worms.append(name)
            elif not result.get("success", False):
                failed_worms.append(name)
        except Exception as e:
            crash_worms.append(f"{name}: {type(e).__name__}: {e}")

    # Allow some worms to fail resolution (missing deps, etc.)
    # but NO worm should crash the resolver
    assert len(crash_worms) == 0, f"Worms crashed resolver: {crash_worms}"

    # At least 80% of worms should resolve successfully
    success_rate = (len(worm_names) - len(failed_worms)) / max(len(worm_names), 1)
    assert success_rate >= 0.80, (
        f"Only {success_rate:.0%} worms resolved. Failed: {failed_worms[:10]}..."
    )


def test_clusterworms_execute_all_system():
    """ClusterWORMZ('system') should execute all 3 system worms without errors."""
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ

    cluster = ClusterWORMZ("system")
    assert len(cluster.configs) >= 3, f"Expected >= 3 worms, got {len(cluster.configs)}"

    results = cluster.execute_all()

    assert len(results) >= 3
    success_count = len([r for r in results if r.get("success")])
    assert success_count >= 2, f"Expected >= 2 successful worms, got {success_count}"


def test_clusterworms_aggregate_results():
    """aggregate_results() should return valid ClusterResult after execution."""
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ

    cluster = ClusterWORMZ("system")
    cluster.execute_all()
    result = cluster.aggregate_results()

    assert result.cluster_id
    assert result.success_count >= 2
    assert result.duration_ms > 0
    assert result.overall_status in ("success", "partial_success", "failure")


def test_clusterworms_partial_failure_threshold():
    """Partial failure threshold should classify partial_success correctly."""
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ

    cluster = ClusterWORMZ("system")
    cluster.set_partial_failure_threshold(0.5)  # 50% is enough

    assert cluster._partial_failure_threshold == 0.5

    cluster.execute_all()
    result = cluster.aggregate_results()

    # With threshold 0.5, even 2/3 should be "success" or "partial_success"
    assert result.overall_status in ("success", "partial_success")


def test_clusterworms_get_status():
    """get_status() should report all expected keys including watchdog info."""
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ

    cluster = ClusterWORMZ("system")
    status = cluster.get_status()

    assert re.match(r"^\d+\.\d+\.\d+$", status["version"]), status["version"]
    assert status["cluster_type"] == "system"
    assert status["worm_count"] > 0
    assert "free_threaded" in status
    assert "registry_ok" in status
    assert "pams_ok" in status
    assert "breaker_ok" in status
    assert "partial_failure_threshold" in status


# ═══════════════════════════════════════════════════════════════════
# Section 2: _is_circular_category() Edge Cases
# ═══════════════════════════════════════════════════════════════════

def test_is_circular_category_self_reference():
    """A nature that feeds into itself should be detected as circular."""
    from CORE.WORMS.METACLUSTER import MetaCluster

    mc = MetaCluster()
    assert hasattr(mc, '_is_circular_category'), (
        "_is_circular_category method missing — regressed from T18/T19"
    )
    assert mc._is_circular_category("self_ref", {"self_ref": ["self_ref"]})


def test_is_circular_category_two_node_cycle():
    """A -> B -> A should be detected as circular."""
    from CORE.WORMS.METACLUSTER import MetaCluster

    mc = MetaCluster()

    assert hasattr(mc, '_is_circular_category'), "_is_circular_category method missing"
    feeds = {"A": ["B"], "B": ["A"]}
    assert mc._is_circular_category("A", feeds)
    assert mc._is_circular_category("B", feeds)


def test_is_circular_category_no_cycle():
    """A -> B -> C with no back-edges should NOT be circular."""
    from CORE.WORMS.METACLUSTER import MetaCluster

    mc = MetaCluster()

    assert hasattr(mc, '_is_circular_category'), "_is_circular_category method missing"
    feeds = {"A": ["B"], "B": ["C"], "C": []}
    assert not mc._is_circular_category("A", feeds)
    assert not mc._is_circular_category("B", feeds)
    assert not mc._is_circular_category("C", feeds)


def test_is_circular_category_disconnected():
    """A nature with no connections should never be circular."""
    from CORE.WORMS.METACLUSTER import MetaCluster

    mc = MetaCluster()

    assert hasattr(mc, '_is_circular_category'), "_is_circular_category method missing"
    feeds: dict[str, list[str]] = {}
    assert not mc._is_circular_category("orphan", feeds)


def test_is_circular_category_deep_cycle():
    """A -> B -> C -> D -> B (6-node chain with a cycle at the end)."""
    from CORE.WORMS.METACLUSTER import MetaCluster

    mc = MetaCluster()

    assert hasattr(mc, '_is_circular_category'), "_is_circular_category method missing"
    feeds = {
        "A": ["B"],
        "B": ["C"],
        "C": ["D"],
        "D": ["E"],
        "E": ["B"],  # back edge creates cycle
    }
    # A-B-C-D-E-B is a cycle, so A is part of it
    assert mc._is_circular_category("A", feeds)


def test_is_circular_category_large_dag():
    """A 10-node DAG with no cycles should have zero circular natures."""
    from CORE.WORMS.METACLUSTER import MetaCluster

    mc = MetaCluster()

    assert hasattr(mc, '_is_circular_category'), "_is_circular_category method missing"
    feeds = {
        "N0": ["N1", "N2"],
        "N1": ["N3", "N4"],
        "N2": ["N4", "N5"],
        "N3": ["N6"],
        "N4": ["N6", "N7"],
        "N5": ["N7"],
        "N6": ["N8"],
        "N7": ["N8"],
        "N8": ["N9"],
        "N9": [],
    }
    for nature in feeds:
        assert not mc._is_circular_category(nature, feeds), (
            f"Nature {nature} falsely detected as circular"
        )


# ═══════════════════════════════════════════════════════════════════
# Section 3: PAMS Mock Testing
# ═══════════════════════════════════════════════════════════════════

def test_pams_record_event_called_on_execute_all():
    """PamsRecorder.record_event should be called during cluster execution."""
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ, _get_pams, _pams, _pams_lock

    # Force-reset PAMS singleton to use mock
    with _pams_lock:
        original = _pams
        try:
            mock_pams = MagicMock()
            mock_pams.record_event = MagicMock()
            # We need to inject mock without breaking the double-checked lock
            # Use the module-level variable directly
            import CORE.WORMS.CLUSTERWORMS as cw
            cw._pams = mock_pams

            cluster = ClusterWORMZ("system")
            cluster.execute_all()

            # Should have recorded at least cluster_created, execution_started, execution_completed
            call_args = [c[0] for c in mock_pams.record_event.call_args_list if c[0]]
            event_types = [str(a[0]) if a else "" for a in call_args]

            assert any("cluster_created" in e for e in event_types), (
                f"Expected cluster_created event, got: {event_types[:5]}"
            )
            assert any("cluster_execution_started" in e for e in event_types), (
                f"Expected execution_started event, got: {event_types[:5]}"
            )
            assert any("cluster_execution_completed" in e for e in event_types), (
                f"Expected execution_completed event, got: {event_types[:5]}"
            )
        finally:
            cw._pams = original


def test_pams_mock_never_called_when_unavailable():
    """When PAMS is unavailable, _record() should not crash."""
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ, _pams, _pams_lock

    with _pams_lock:
        original = _pams
        try:
            import CORE.WORMS.CLUSTERWORMS as cw
            cw._pams = False  # Simulate unavailable

            cluster = ClusterWORMZ("system")
            # Should not raise — _record() handles None/False
            cluster._record("test_event", {"key": "value"})

            # execute_all should also not crash without PAMS
            results = cluster.execute_all()
            assert len(results) >= 3
        finally:
            cw._pams = original


def test_direct_memory_record_called_on_execute():
    """DirectMemory should receive clusterwormz_execute_start/complete events."""
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ, _dm, _dm_lock

    with _dm_lock:
        original = _dm
        try:
            import CORE.WORMS.CLUSTERWORMS as cw
            mock_dm = MagicMock()
            mock_dm.record = MagicMock()
            cw._dm = mock_dm

            cluster = ClusterWORMZ("system")
            cluster.execute_all()

            call_events = [
                c[0][0] if c[0] else ""
                for c in mock_dm.record.call_args_list
                if c[0]
            ]
            assert any("clusterwormz_execute_start" in e for e in call_events), (
                f"Expected start event, got: {call_events[:5]}"
            )
            assert any("clusterwormz_execute_complete" in e for e in call_events), (
                f"Expected complete event, got: {call_events[:5]}"
            )
        finally:
            cw._dm = original


# ═══════════════════════════════════════════════════════════════════
# Section 4: Watchdog Tests
# ═══════════════════════════════════════════════════════════════════

def test_watchdog_class_exists():
    """_WatchdogThread should be importable and instantiable."""
    from CORE.WORMS.CLUSTERWORMS import _WatchdogThread

    wd = _WatchdogThread(poll_interval=0.1)
    assert wd is not None
    assert wd._poll_interval == 0.1
    assert not wd._running


def test_watchdog_track_and_untrack():
    """Watchdog should track and untrack futures correctly."""
    from CORE.WORMS.CLUSTERWORMS import _WatchdogThread
    from unittest.mock import MagicMock

    wd = _WatchdogThread(poll_interval=0.1)
    mock_future = MagicMock()

    wd.track(mock_future, "TestWorm", 30)
    with wd._lock:
        assert mock_future in wd._futures

    wd.untrack(mock_future)
    with wd._lock:
        assert mock_future not in wd._futures


def test_watchdog_start_stop():
    """Watchdog should start and stop cleanly without errors."""
    from CORE.WORMS.CLUSTERWORMS import _WatchdogThread

    wd = _WatchdogThread(poll_interval=0.05)
    wd.start()
    assert wd._running
    assert wd._thread is not None
    assert wd._thread.is_alive()

    timeouts = wd.stop()
    assert not wd._running
    assert isinstance(timeouts, list)


def test_watchdog_detects_timeout():
    """Watchdog should detect and report a future that exceeds its timeout."""
    from CORE.WORMS.CLUSTERWORMS import _WatchdogThread
    from unittest.mock import MagicMock

    # Speed up for test
    wd = _WatchdogThread(poll_interval=0.05)
    mock_future = MagicMock()

    # Track with a 0.03s timeout (expires fast)
    wd.track(mock_future, "HungWorm", 0.03)

    # Start watchdog and wait for it to detect the timeout
    wd.start()
    time.sleep(0.15)  # Allow 3+ poll cycles

    timeouts = wd.stop()
    assert "HungWorm" in timeouts, f"Expected HungWorm in timeouts, got {timeouts}"
    assert mock_future.cancel.called, "Expected future.cancel() to be called"


# ═══════════════════════════════════════════════════════════════════
# Section 5: Cross-Nature Integration Smoke Tests
# ═══════════════════════════════════════════════════════════════════

def test_ncm_discover_all_natures():
    """Registry should cover >= 35 categories (U3: was NCM natures >= 17)."""
    from CORE.worm_registry import discover_worms, get_all_categories, get_worms_by_category

    discover_worms()
    categories = sorted(get_all_categories())

    assert len(categories) >= 35, f"Expected >= 35 categories, got {len(categories)}"

    # Each category should have worms
    empty = [c for c in categories if len(get_worms_by_category(c)) == 0]
    assert not empty, f"Categories with no worms: {empty}"


@pytest.mark.slow  # full-fleet parallel orchestration: solo process (see --slow-solo).
# Leftover worker threads from earlier tests AV inside torch C code on 3.15.
def test_metacluster_orchestrate_no_circularity_error():
    """MetaCluster orchestrate() should not crash on circularity check."""
    from CORE.worm_registry import discover_worms
    from CORE.WORMS.METACLUSTER import MetaCluster

    discover_worms()
    mc = MetaCluster()
    mc.discover_all()

    # Orchestrate all natures — should not crash on circular detection
    result = mc.orchestrate(parallel=True)
    assert result is not None
    assert result.categories_executed >= 1
    assert result.total_worms_ok + result.total_worms_fail > 0


@pytest.mark.slow  # same solo-process rationale as the orchestrate test above.
def test_metacluster_to_mermaid_after_orchestration():
    """Mermaid graphs should work after orchestration."""
    from CORE.worm_registry import discover_worms
    from CORE.WORMS.METACLUSTER import MetaCluster

    discover_worms()
    mc = MetaCluster()
    mc.discover_all()
    mc.orchestrate(max_categories=3)

    graph = mc.to_mermaid()
    assert "graph" in graph
    assert len(graph) > 100

    flow = mc.to_mermaid_execution_flow()
    assert "graph LR" in flow

    topo = mc.to_mermaid_routing_topology()
    assert "graph" in topo


# ═══════════════════════════════════════════════════════════════════
# Test Runner
# ═══════════════════════════════════════════════════════════════════

def main():
    global passed, failed, errors_list
    print("=" * 64)
    print("  P1-11 Cluster Integration Tests")
    print("=" * 64)
    print()
    print("  Sections:")
    print("    1. 89-Worm Full Orchestration Validation")
    print("    2. _is_circular_category() Edge Cases")
    print("    3. PAMS Mock Testing")
    print("    4. Watchdog Tests")
    print("    5. Cross-Nature Integration Smoke Tests")
    print()
    print("  Tests:")
    print()

    tests = [
        # Section 1: 89-worm orchestration
        ("89-worm dynamic resolution (no crashes)", test_all_worms_execute_without_crash),
        ("ClusterWORMZ execute_all system cluster", test_clusterworms_execute_all_system),
        ("aggregate_results after execution", test_clusterworms_aggregate_results),
        ("partial failure threshold classification", test_clusterworms_partial_failure_threshold),
        ("get_status reports all expected keys", test_clusterworms_get_status),

        # Section 2: Circular nature edge cases
        ("is_circular: self-reference", test_is_circular_category_self_reference),
        ("is_circular: two-node cycle", test_is_circular_category_two_node_cycle),
        ("is_circular: no cycle (DAG)", test_is_circular_category_no_cycle),
        ("is_circular: disconnected/orphan", test_is_circular_category_disconnected),
        ("is_circular: deep cycle (6-node)", test_is_circular_category_deep_cycle),
        ("is_circular: 10-node DAG all pass", test_is_circular_category_large_dag),

        # Section 3: PAMS mocking
        ("PAMS record_event called during execute_all", test_pams_record_event_called_on_execute_all),
        ("PAMS unavailable: no crash on _record()", test_pams_mock_never_called_when_unavailable),
        ("DirectMemory receives start/complete events", test_direct_memory_record_called_on_execute),

        # Section 4: Watchdog
        ("_WatchdogThread instantiable", test_watchdog_class_exists),
        ("Watchdog track/untrack futures", test_watchdog_track_and_untrack),
        ("Watchdog start/stop lifecycle", test_watchdog_start_stop),
        ("Watchdog detects hung future timeout", test_watchdog_detects_timeout),

        # Section 5: Cross-nature smoke
        ("NCM discovers >= 17 natures", test_ncm_discover_all_natures),
        ("MetaCluster orchestrate no circularity error", test_metacluster_orchestrate_no_circularity_error),
        ("Mermaid graphs work after orchestration", test_metacluster_to_mermaid_after_orchestration),
    ]

    for name, fn in tests:
        run_test(name, fn)

    print()
    total = passed + failed
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    if errors_list:
        print("  Errors:")
        for e in errors_list:
            print(f"    {e}")
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
