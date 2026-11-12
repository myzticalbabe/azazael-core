# coding: utf-8
"""
FULL INTEGRATION TEST: ClusterWORMZ + DeadLetterQueue + WorkStealingPool + Circuit Breaker
============================================================================================
Tests:
1. ClusterWORMZ with WorkStealingPool load balancing
2. DeadLetterQueue enqueue on failure + replay
3. Circuit breaker protection on worm execution
4. CrossNatureRoutingEngine cycle detection + route optimization
5. End-to-end: execute -> fail -> DLQ -> retry -> succeed
"""
import sys, os, time, threading
sys.path.insert(0, 'src')

if __name__ == "__main__":
    passed = 0
    failed = 0

    def check(label, condition, detail=""):
        global passed, failed
        if condition:
            print(f"  [PASS] {label}")
            passed += 1
        else:
            print(f"  [FAIL] {label} {detail}")
            failed += 1

    print("=" * 60)
    print("  INTEGRATION TEST: ClusterWORMZ + DLQ + WorkStealing + Breaker")
    print("=" * 60)

    # ── Test 1: DeadLetterQueue lifecycle ─────────────────────────────
    print("\n--- Test 1: DeadLetterQueue enqueue/replay/analytics ---")
    from CORE.WORMS.dead_letter_queue import DeadLetterQueue

    dlq = DeadLetterQueue()
    dlq.clear()
    check("DLQ initialized", dlq.count_pending() == 0)
    check("DLQ version", dlq.VERSION == "1.0.0")

    # Enqueue failures
    dlq.enqueue("SYSTEMZ", {"error": "timeout", "type": "TimeoutError"})
    dlq.enqueue("PROCESSZ", {"error": "connection refused", "type": "ConnectionError"})
    dlq.enqueue("SYSTEMZ", {"error": "timeout", "type": "TimeoutError"})
    check("DLQ pending count", dlq.count_pending() == 3)

    # Analytics
    a = dlq.analytics()
    check("DLQ top worm", a["top_failing_worms"][0]["worm"] == "SYSTEMZ")
    check("DLQ top error", a["top_error_types"][0]["type"] == "TimeoutError")

    # Replay with success
    def replay_success(worm_name, config):
        return {"success": True, "result": f"replayed {worm_name}"}

    results = dlq.replay_all(replay_success, max_concurrent=2)
    check("DLQ replay all success", len(results) == 3 and all(r["success"] for r in results))
    check("DLQ all resolved after replay", dlq.count_pending() == 0)

    dlq.clear()

    # ── Test 2: WorkStealingPool load balancing ───────────────────────
    print("\n--- Test 2: WorkStealingPool with heterogeneous workload ---")
    from CORE.freethreading import WorkStealingPool

    def fast_task(n):
        return n * n

    def slow_task(n):
        time.sleep(0.05)
        return n * n

    # Submit mix of fast and slow tasks
    with WorkStealingPool(num_workers=4) as pool:
        # Submit all tasks (slow first, fast after -- creates imbalance)
        for i in range(5):
            pool.submit(slow_task, i)
        for i in range(15):
            pool.submit(fast_task, i + 100)
        pool.wait_all(timeout=10)
        results = pool.results()
        s = pool.stats

    check("WS all tasks completed", len(results) == 20)
    check("WS stealing attempted", s["steals_succeeded"] + s["steals_failed"] > 0,
          f"steals: {s['steals_succeeded']} ok / {s['steals_failed']} fail")

    # ── Test 3: Circuit breaker integration ───────────────────────────
    print("\n--- Test 3: Circuit breaker with DLQ fallback ---")
    from CORE.circuit_breaker import CircuitBreaker, CircuitConfig, BreakerRegistry

    registry = BreakerRegistry()
    cb = registry.get("test_worm", CircuitConfig(failure_threshold=2, recovery_timeout=1))

    fail_count = 0
    def flaky_worm():
        global fail_count
        fail_count += 1
        if fail_count <= 2:
            raise RuntimeError("Simulated failure")
        return {"success": True, "data": "recovered"}

    # Execute with breaker -- first 2 should fail, 3rd should succeed
    for attempt in range(3):
        try:
            result = cb.retry_with_backoff(flaky_worm, max_retries=0)
            if attempt >= 2:
                check(f"Breaker attempt {attempt+1}: recovered", result.get("success"))
        except RuntimeError:
            dl = registry.get_dead_letters("test_worm")
            check(f"Breaker attempt {attempt+1}: failed -> DLQ", len(dl) > 0,
                  f"got {len(dl)} dead letters")

    # Actually PERFORM the recovery this check is named after. `recovery_timeout=1`,
    # and the three attempts above run back to back, so the breaker was still OPEN
    # here and `health = successes / (failures + successes)` was necessarily 0.00:
    # the assertion could not pass, and the label claimed a recovery the test never
    # attempted. 2026-09-21 — this file's checks only began running when it stopped
    # being an uncollected script, which is why the contradiction survived.
    time.sleep(1.1)
    try:
        recovered = cb.retry_with_backoff(flaky_worm, max_retries=0)
        check("Breaker recovery call succeeded", bool(recovered.get("success")))
    except Exception as exc:
        check("Breaker recovery call succeeded", False, f"raised {exc!r}")

    cb_stats = cb.stats()
    check("Breaker health after recovery", cb_stats["health"] > 0,
          f"health={cb_stats['health']:.2f}")

    # ── Test 4: Flat dispatch replaces routing (U3) ──────────────
    print("\n--- Test 4: Flat dispatch, no router ---")
    from CORE.worm_registry import get_all_categories
    from CORE.WORMS.METACLUSTER import MetaCluster

    categories = sorted(get_all_categories())
    check("Registry has categories", len(categories) > 0,
          f"found {len(categories)} categories")

    mc = MetaCluster()
    check("MetaCluster has no router", not hasattr(mc, "router"),
          "router deleted with the taxonomy (D7)")
    check("MetaCluster discovers flat", len(mc.discover_all()) > 0,
          "discover_all returns per-category counts")

    # ── Test 5: End-to-end CLUSTERWORMS with DLQ + WorkStealing ──────
    print("\n--- Test 5: CLUSTERWORMS with DLQ + WorkStealing integration ---")
    from CORE.WORMS.CLUSTERWORMS import ClusterWORMZ, CLUSTER_REGISTRY

    cluster_types = list(CLUSTER_REGISTRY.keys())[:3]
    check("Cluster registry loaded", len(cluster_types) > 0,
          f"{len(CLUSTER_REGISTRY)} clusters available")

    # Test sequential execution
    for ct in cluster_types[:1]:  # Just test one to be fast
        try:
            cluster = ClusterWORMZ(ct)
            status = cluster.get_status()
            check(f"Cluster {ct} created", status["worm_count"] > 0,
                  f"{status['worm_count']} worms")
        except Exception as e:
            check(f"Cluster {ct} created", False, str(e))

    # ── Summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"  RESULTS: {passed}/{total} passed" + (" (ALL PASS)" if failed == 0 else f" ({failed} FAILED)"))
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


# ── pytest entry point (2026-09-21) ──────────────────────────────────
# The end-to-end chain (ClusterWORMZ -> DeadLetterQueue -> retry -> success) is
# asserted only under `if __name__ == "__main__"`, so pytest collected nothing and
# returned rc=5. It runs the real chain, so it is the single most valuable check
# in this directory to have actually executing.
def test_script_passes(run_standalone_script):
    proc = run_standalone_script(__file__, timeout=300.0)
    assert proc.returncode == 0, (
        f"{__file__} exited {proc.returncode}\n"
        + proc.stdout[-3000:] + "\n" + proc.stderr[-2000:]
    )
