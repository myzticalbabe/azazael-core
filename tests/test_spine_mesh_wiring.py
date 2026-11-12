# coding: utf-8
"""
2026-09-14 remediation tests — orphan wiring, credit assignment, tracing.

Companion to `test_spine_mesh.py`. Covers the second remediation pass:

* G7  — plasticity results are now STORED (the weight store exists).
* F2  — TD(lambda) eligibility traces give delayed credit along the edge graph.
* G3  — `MeshRouterWorm.record_route_result()` finally has a caller, so the
        route Q-table is no longer permanently empty.
* Orphan guards — every method that previously had zero callers must still
  have a real consumer, or the organ goes inert again (the whole point).
* OTel — a cluster run exports a parent span + one span per worm, and the
  exporter's default batch size no longer raises.
"""

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from CORE import spine_registry as sr


def _seed_outcomes(n=10, nature="mesh"):
    """Seed a chronological outcome window with varied timing."""
    for i in range(n):
        sr.record_outcome(nature, f"Seed{i % 3}",
                          success=(i % 3 != 0), duration_ms=100.0 * (i + 1))
        time.sleep(0.005)


def test_plasticity_engine_persists_weights():
    """G7: w_new is STORED, not computed and discarded.

    The old `_apply_plasticity` called `hebbian.update(0.0, pre, post)` — a
    hardcoded zero weight every cycle — so every delta was measured from zero
    and the result thrown away. The engine must carry weight across calls.
    """
    from CORE.WORMS.expansion.spine import PlasticityEngineWorm
    sr.spine_reset()
    try:
        _seed_outcomes()
        eng = PlasticityEngineWorm()
        events = list(reversed(sr.get_outcome_events()))
        r1 = eng.learn(events)
        assert r1["success"] and r1["edges_touched"] > 0
        w1 = eng.weight("mesh", "mesh")
        assert w1 != 0.0, "weight was not stored (G7 regression)"
        # A second pass must continue FROM the stored value, not from zero.
        eng.learn(events)
        w2 = eng.weight("mesh", "mesh")
        assert w2 != w1, "second pass did not build on the stored weight"
        assert eng.summary()["result"]["edges"] >= 1
    finally:
        sr.spine_reset()


def test_eligibility_trace_gives_delayed_credit():
    """F2: reward reaches edges that fired earlier, and traces decay."""
    from CORE.WORMS.expansion.spine import EligibilityTraceTable
    t = EligibilityTraceTable(gamma=0.9, lam=0.8, lr=0.5)
    t.accumulate("a", "b", amount=1.0)
    first = t.credit()
    assert first[("a", "b")] == 0.5, "immediate credit wrong"
    # The trace survived the reward (decayed, not cleared), so a LATER
    # reward still reaches this earlier edge — that is delayed credit.
    second = t.credit()
    assert ("a", "b") in second
    assert abs(second[("a", "b")] - 0.5 * 0.9 * 0.8) < 1e-9
    assert second[("a", "b")] < first[("a", "b")], "trace did not decay"
    # gamma*lambda < 1 means traces provably vanish rather than blow up.
    for _ in range(40):
        t.credit()
    assert t.snapshot()["active_traces"] == 0, "trace never vanished"


def test_eligibility_trace_untouched_edges_get_no_credit():
    """Credit must not leak to edges that never fired."""
    from CORE.WORMS.expansion.spine import PlasticityEngineWorm
    sr.spine_reset()
    try:
        _seed_outcomes()
        eng = PlasticityEngineWorm()
        eng.learn(list(reversed(sr.get_outcome_events())))
        assert eng.weight("security", "threatmon") == 0.0
    finally:
        sr.spine_reset()


def test_stdp_kernel_uses_real_inter_event_dt():
    """The STDP kernel is evaluated at the true dt, not a fabricated latency."""
    from CORE.WORMS.expansion.spine import PlasticityEngineWorm
    eng = PlasticityEngineWorm()
    # pre-before-post (positive dt) potentiates; a huge gap decays to ~0.
    assert eng._stdp.kernel(5.0) > eng._stdp.kernel(100.0) > 0.0
    # post-before-pre (negative dt) depresses.
    assert eng._stdp.kernel(-5.0) < 0.0
    # Real timestamps convert to a sane millisecond dt.
    dt = eng._dt_ms("2026-09-14T00:00:00", "2026-09-14T00:00:01")
    assert abs(dt - 1000.0) < 1.0


def test_thalamus_gate_is_advisory_never_starves_learning():
    """The salience gate must not be able to starve learning entirely.

    An over-aggressive gate is a silent failure mode: the mesh would look
    healthy while learning nothing, forever.
    """
    from CORE.WORMS.expansion.spine import SpineCycleWorm
    sr.spine_reset()
    try:
        w = SpineCycleWorm()
        # Fast, clean, identical events (worst case for a salience gate).
        for _ in range(6):
            sr.record_outcome("mesh", "Quiet", True, 0.5)
        result = w._apply_plasticity()
        assert result.get("success") is True
        assert result.get("skipped") != "all_below_salience"
        assert result.get("gate", {}).get("input") == 6
    finally:
        sr.spine_reset()


def test_spine_cycle_closes_route_learning_g3():
    """G3: route learning has a real writer, and it is not the cycle.

    Before this, the route Q-table held NO `route:*` key -- ever, so
    `effective_weight` always equalled the static weight and route learning
    was dead-ended despite being fully implemented.

    The first fix closed it in the wrong place. The cycle settled the route it
    had proposed the previous cycle from `_nature_success_rate(dst)` -- the
    health of the WORMS on the far side -- and wrote that as the route's
    verdict. Because the cycle always proposes to the LEAST-healthy nature and
    never delivers on it, that manufactured a standing verdict for a route that
    had never been used, and it was the dominant writer of the live ledger
    (forensic 2026-09-29).

    The loop is now closed where the observation actually happens:
    `MeshRouterWorm.send()` writes, after a real delivery attempt. So the
    regression is pinned on the data plane writing a `route:*` key, and on the
    cycle NOT writing one it did not observe.
    """
    from CORE.WORMS.expansion.mesh import MeshRouterWorm
    from CORE.WORMS.expansion.spine import SpineCycleWorm
    sr.spine_reset()
    try:
        for i in range(4):
            sr.record_outcome("mesh", f"W{i}", success=(i % 2 == 0),
                              duration_ms=10.0)
        w = SpineCycleWorm()
        w.run_cycle(apply=False)
        assert w._pending_route is not None, "no route was proposed"

        # The cycle must not manufacture a verdict for a route it never used.
        w._last_run_at = 0.0
        r2 = w.run_cycle(apply=False)
        assert r2["mesh_routes"]["success"] is True
        assert "settled" in r2["mesh_routes"], "route was never reported"
        assert [k for k in sr.get_q_weights() if k.startswith("route:")] == [], \
            "the cycle wrote a route verdict it did not observe"

        # A real delivery attempt is what closes the loop.
        MeshRouterWorm().record_route_result("wsl2-ubuntu", True, 12.0)
        routes = [k for k in sr.get_q_weights() if k.startswith("route:")]
        assert routes, "no route:* Q key was ever written (G3 regression)"
    finally:
        sr.spine_reset()


def test_spine_cycle_reports_new_organs():
    """Arousal budget and gossip-sync must actually be driven."""
    from CORE.WORMS.expansion.spine import SpineCycleWorm
    sr.spine_reset()
    try:
        sr.record_outcome("mesh", "W", True, 5.0)
        w = SpineCycleWorm()
        w._last_run_at = 0.0
        rep = w.run_cycle(apply=False)
        mr = rep["mesh_routes"]
        assert mr["arousal"]["mode"] in ("wake", "sleep", "recon")
        assert mr["arousal"]["budgets"]["train"] >= 0.0
        assert mr["sync"]["success"] is True
        assert rep["plasticity"]["success"] is True
    finally:
        sr.spine_reset()


def test_formerly_orphaned_organs_have_real_consumers():
    """Regression guard for the orphan-wiring work.

    These methods were registered but had ZERO callers anywhere in src/.
    A static scan over the tree must find each one called from outside its
    own defining file, otherwise the organ is inert again.
    """
    root = Path(__file__).resolve().parent.parent / "src"
    all_text = {p: p.read_text(encoding="utf-8", errors="replace")
                for p in root.rglob("*.py")}
    checks = [
        ("mesh.py", "record_route_result"),
        ("mesh.py", "gossip"),
        ("thalamus.py", "filter"),
        ("thalamus.py", "get_mode"),
        ("plasticity.py", "kernel"),
        ("plasticity.py", "anchor_task"),
        ("plasticity.py", "penalty"),
        ("plasticity.py", "next_review_hours"),
        ("nerve.py", "modulations"),
    ]
    for fname, method in checks:
        assert any(p.name == fname for p in all_text), f"{fname} not found"
        seen = any(
            p.name != fname and f".{method}(" in text
            for p, text in all_text.items()
        )
        assert seen, (
            f"{method}() in {fname} has no caller outside its own file — "
            f"the organ is inert again")


def test_otel_exporter_configures_with_default_batch_size():
    """Regression: configure_otel_tracing() used to ALWAYS raise.

    BatchSpanProcessor validates max_export_batch_size <= max_queue_size
    while defaulting max_export_batch_size to 512, so passing only
    max_queue_size=batch_size (default 64) raised for every call — the
    project's entire OTel path was unreachable.
    """
    pytest.importorskip("opentelemetry.sdk.trace")
    from CORE.cd3_exporter import configure_otel_tracing, SQLiteSpanExporter
    tracer = configure_otel_tracing(
        service_name="azazael-test", exporter=SQLiteSpanExporter())
    assert tracer is not None
    with tracer.start_as_current_span("test.span") as span:
        span.set_attribute("ok", True)


# U3: test_nature_cluster_emits_spans deleted with the layer. Its behavior
# (OTel parent+child spans per cluster run) lived in ClusterWORMZ.execute;
# ClusterWORMZ emits no spans. If span-per-run observability returns, it
# belongs in ClusterWORMZ with a test here — not resurrected from NCM.
