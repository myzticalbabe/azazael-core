# coding: utf-8
"""
2026-09-14 remediation, third pass — state durability, telemetry, invariants.

Companion to `test_spine_mesh.py` and `test_spine_mesh_wiring.py`. Covers the
items the audit's own roadmap left open after the orphan sweep:

* G10 — the policy journal is a bounded FIFO ring, and
        `worm_registry._replay_policy_journal()` reconstructs adapted cooldowns
        from it. Once the ring wrapped, a worm whose last adaptation had been
        evicted silently reverted to its decorator default. A compacting
        latest-per-worm map is now the replay source.
* §7  — the mapper published `"routes": []` on every node with a comment
        claiming they were "filled below"; nothing filled them. Per-worm edges
        and per-route usage are now observed telemetry.
* G5/G13 — the anomaly autoencoder's decoder was never updated (frozen at
        `Random(42)`), it was never persisted, its alert list grew unbounded,
        and `observe()` had zero callers. All four are fixed.
* G16 — no declared fleet-authority invariant existed, so regulators living in
        probed natures (HomeostatWorm/synapse, SpineMapperWorm/mesh,
        AutoencoderAnomalyWorm/immune) were themselves being probed.
* G18 — `q_order()` was O(n^2) via `list.index()` in a comprehension.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE import spine_registry as sr

# The mapper and the Red Queen both read WORM_REGISTRY, so discovery has to
# have run before those tests mean anything. `discover_worms()` is idempotent
# (a latecomer call returns 0 on `_discovery_done`) and hits the pickle cache.
from CORE.worm_registry import discover_worms

discover_worms()


def _seed_recent_outcomes(n=12, nature="mesh"):
    for i in range(n):
        sr.record_outcome(nature, f"T{i % 3}", success=(i % 3 != 0),
                          duration_ms=50.0 * (i + 1))


# ── G10: the journal is an audit log, not state ──────────────────────

def test_policy_latest_survives_journal_ring_wrap():
    """G10: an adaptation must not evaporate when the audit ring wraps."""
    from CORE.worm_registry import WORM_REGISTRY, _replay_policy_journal

    sr.spine_reset()
    target = "HomeostatWorm"
    assert target in WORM_REGISTRY, "fixture worm missing from the registry"
    info = WORM_REGISTRY[target]
    original = info.get("cooldown_seconds")
    try:
        sr.journal_policy("test", "policy_adjust", target, {
            "cooldown_seconds": {"from": original, "to": 123.0}})
        # Flood well past the cap so the target's entry is evicted from the ring.
        for i in range(int(sr._POLICY_JOURNAL_MAX) + 50):
            sr.journal_policy("test", "policy_adjust", f"Filler{i}", {
                "cooldown_seconds": {"from": 1.0, "to": 2.0}})

        assert all(e.get("worm") != target for e in sr._POLICY_JOURNAL), \
            "the ring did not wrap — the test is not exercising the bug"
        assert target in sr.get_policy_latest(), \
            "the compacting map lost an adaptation the ring evicted"

        info["cooldown_seconds"] = original  # simulate a fresh process
        _replay_policy_journal()
        assert info["cooldown_seconds"] == 123.0, (
            "learned cooldown reverted to the decorator default after the "
            "journal ring wrapped (G10 regression)")
    finally:
        info["cooldown_seconds"] = original
        sr.spine_reset()


def test_replay_refuses_unratified_retirement():
    """White gate (§5.7): replay must not undo a Black-Queen retirement.

    The journal row survives; only its replay is skipped, so a stale policy
    cannot resurrect an adjustment for a retired organ.
    """
    from CORE.worm_registry import WORM_REGISTRY, _replay_policy_journal

    target = "HomeostatWorm"
    assert target in WORM_REGISTRY, "fixture worm missing from the registry"
    info = WORM_REGISTRY[target]
    original = info.get("cooldown_seconds")
    saved_state = info.get("lifecycle_state")
    try:
        sr.journal_policy("test", "policy_adjust", target, {
            "cooldown_seconds": {"from": original, "to": 123.0}})
        info["lifecycle_state"] = "retired"
        info["cooldown_seconds"] = original  # simulate a fresh process
        _replay_policy_journal()
        assert info["cooldown_seconds"] == original, (
            "replay applied a journaled change to a retired worm "
            "(White-gate regression)")
    finally:
        info["lifecycle_state"] = saved_state
        info["cooldown_seconds"] = original
        sr.spine_reset()


def test_policy_latest_does_not_clobber_live_memory():
    """load_spine_state() merges, so a mid-process load cannot roll back."""
    sr.spine_reset()
    try:
        sr.journal_policy("test", "policy_adjust", "LiveWorm", {
            "cooldown_seconds": {"from": 10.0, "to": 99.0}})
        # A stale load must not overwrite the newer in-memory adaptation.
        with sr._POLICY_LOCK:
            sr._POLICY_LATEST.setdefault("LiveWorm", {
                "actor": "disk", "action": "policy_adjust", "worm": "LiveWorm",
                "changes": {"cooldown_seconds": {"from": 1.0, "to": 5.0}},
                "at": "2020-01-01T00:00:00"})
        assert sr.get_policy_latest()["LiveWorm"]["changes"][
            "cooldown_seconds"]["to"] == 99.0
    finally:
        sr.spine_reset()


# ── §7: per-edge route telemetry ─────────────────────────────────────

def test_mapper_publishes_route_and_edge_telemetry():
    """§7: `routes: []` was a placeholder that nothing ever filled."""
    from CORE.WORMS.expansion.spine import SpineMapperWorm, PlasticityEngineWorm

    sr.spine_reset()
    try:
        _seed_recent_outcomes()
        # A route has to be observed before it can be reported.
        sr.record_outcome("route", "windows>mesh", True, 12.0)
        sr.record_outcome("route", "windows>mesh", False, 30.0)

        engine = PlasticityEngineWorm()
        engine.learn(list(reversed(sr.get_outcome_events())))

        report = SpineMapperWorm(edge_source=engine).map_topology()
        assert report["success"]
        assert report["route_edges"] >= 1, "route usage was not published"
        assert report["worm_edges"], "per-edge telemetry from the store is empty"

        spine_map = sr.get_spine_map()
        # The route's target env becomes a real node with its usage attached.
        env_nodes = [v for k, v in spine_map.items() if k.endswith(":env:mesh")]
        assert env_nodes, "no node was recorded for the route's destination"
        usage = env_nodes[0]["routes"][0]
        # Counts are >= rather than ==: _ensure_loaded() merges the persisted
        # ledger, so an earlier run's observations are legitimately included.
        assert usage["total"] >= 2
        assert usage["successes"] >= 1 and usage["failures"] >= 1
        assert "effective_weight" in usage and "q" in usage

        # Per-worm edges are no longer an empty placeholder: a worm whose
        # nature feeds into others reports those edges.
        mesh_worms = [v for k, v in spine_map.items()
                      if v.get("category") == "mesh" and k.endswith(":SpineMapperWorm")]
        assert mesh_worms, "SpineMapperWorm node missing from the spine map"
        assert "routes" in mesh_worms[0]
        assert "q" in mesh_worms[0], "learned Q is missing from the node"

        events = [e.get("kind") for e in sr.get_spine_events()]
        assert "route_edge" in events, "no route_edge event was recorded"
        assert "spine_mapped" in events
    finally:
        sr.spine_reset()


# ── G5/G13: a detector that actually detects ─────────────────────────

def test_autoencoder_trains_both_layers():
    """G13: the decoder was frozen at Random(42) forever."""
    from CORE.WORMS.expansion.immune import AutoencoderAnomalyWorm

    ae = AutoencoderAnomalyWorm()
    before_dec = [list(r) for r in ae._W_dec]
    before_enc = [list(r) for r in ae._W_enc]
    for _ in range(20):
        ae.observe([0.2, 0.4, 0.6, 0.8, 0.1, 0.3, 0.5, 0.7])
    assert ae._W_enc != before_enc, "encoder did not move"
    assert ae._W_dec != before_dec, "decoder is still frozen (G13 regression)"


def test_autoencoder_reconstruction_error_decreases():
    """Training on a stationary signal must lower the reconstruction error."""
    from CORE.WORMS.expansion.immune import AutoencoderAnomalyWorm

    ae = AutoencoderAnomalyWorm(lr=0.1)
    vec = [0.15, 0.35, 0.55, 0.75, 0.25, 0.45, 0.65, 0.85]
    first = ae.observe(vec)["recon_error"]
    for _ in range(400):
        last = ae.observe(vec)["recon_error"]
    assert last < first, f"error did not decrease: {first} -> {last}"


def test_autoencoder_alerts_are_bounded():
    """The alert list grew without bound in a long-lived process."""
    from CORE.WORMS.expansion.immune import AutoencoderAnomalyWorm

    ae = AutoencoderAnomalyWorm()
    for i in range(ae.ALERTS_MAX + 60):
        # Alternate between an in-distribution and a wildly off vector so the
        # detector raises alerts far more times than ALERTS_MAX.
        vec = [0.1] * 8 if i % 2 else [50.0] * 8
        ae.observe(vec)
    assert len(ae._alerts) <= ae.ALERTS_MAX, "alert list is unbounded again"


def test_autoencoder_model_round_trips_and_shape_checks():
    """G13: the fitted model must persist, and bad shapes must be refused."""
    from CORE.WORMS.expansion.immune import AutoencoderAnomalyWorm

    trained = AutoencoderAnomalyWorm()
    for _ in range(30):
        trained.observe([0.3, 0.3, 0.6, 0.6, 0.2, 0.2, 0.5, 0.5])
    model = trained.export_model()

    fresh = AutoencoderAnomalyWorm()
    assert fresh.import_model(model), "a valid model was refused"
    assert fresh._W_dec == trained._W_dec, "decoder weights did not round-trip"

    # Wrong dimensions must be refused WITHOUT half-loading a matrix.
    bad = dict(model)
    bad["hidden_dim"] = 99
    victim = AutoencoderAnomalyWorm()
    snapshot = [list(r) for r in victim._W_enc]
    assert victim.import_model(bad) is False
    assert victim._W_enc == snapshot, "a rejected model still mutated the net"

    # Malformed payloads must not raise out of import_model.
    assert victim.import_model({}) is False
    assert victim.import_model("not a model") is False


def test_cycle_observes_anomaly_metrics():
    """`observe()` had zero callers; the cycle must now feed it real metrics."""
    from CORE.WORMS.expansion.spine import SpineCycleWorm

    sr.spine_reset()
    try:
        _seed_recent_outcomes()
        w = SpineCycleWorm()
        w._last_run_at = 0.0
        report = w.run_cycle(apply=False)
        anom = report["anomaly"]
        assert anom["success"], anom
        assert anom.get("skipped") is None, f"anomaly stage skipped: {anom}"
        assert len(anom["metrics"]) == 8
        assert all(0.0 <= v <= 1.0 for v in anom["metrics"]), anom["metrics"]
        assert anom["updates"] >= 1, "no gradient step was taken"
    finally:
        sr.spine_reset()


# ── G16: fleet-authority invariant ──────────────────────────────────

def test_protected_natures_are_disjoint_from_the_probe_set():
    """The invariant only means something if the sets cannot overlap."""
    from CORE.worm_registry import PROTECTED_NATURES
    from CORE.WORMS.expansion.spine import RedQueenWorm

    assert PROTECTED_NATURES, "no protected natures are declared"
    assert PROTECTED_NATURES.isdisjoint(set(RedQueenWorm.PROBE_NATURES)), (
        "a nature is both protected and probed — the invariant is vacuous")


def test_registration_refuses_to_refile_a_fleet_governor():
    """G16: `_guess_category`'s keyword chains must not re-file a governor."""
    import pytest
    from CORE.worm_registry import register_worm, FLEET_GOVERNORS

    assert "SpineMapperWorm" in FLEET_GOVERNORS, "fixture governor missing"
    with pytest.raises(ValueError, match="Fleet-authority invariant"):
        @register_worm(category="utility", priority=5, cooldown_seconds=30.0)
        class SpineMapperWorm:  # noqa: F811 — deliberately shadowing the name
            VERSION = "0.0.0"


def test_red_queen_does_not_probe_fleet_governors():
    """G16: probing a regulator writes into the ledger it regulates."""
    from CORE.WORMS.expansion.spine import RedQueenWorm
    from CORE.worm_registry import WORM_REGISTRY, is_protected_worm

    assert is_protected_worm(WORM_REGISTRY["HomeostatWorm"])
    assert is_protected_worm(WORM_REGISTRY["SpineMapperWorm"])
    assert is_protected_worm(WORM_REGISTRY["AutoencoderAnomalyWorm"])

    sr.spine_reset()
    try:
        res = RedQueenWorm().pressure_probe("immune", limit=1)
        assert "AutoencoderAnomalyWorm" in res["protected_skipped"], (
            "the Red Queen probed the anomaly detector it feeds")
        probed = {r["worm"] for r in res["results"]}
        assert not (probed & {"AutoencoderAnomalyWorm"})
    finally:
        sr.spine_reset()


# ── G18: q_order must not be quadratic ──────────────────────────────

def test_q_order_preserves_ordering_semantics():
    """G18: dropping `list.index()` must not change the ranking.

    2026-09-16: the sort KEY moved from the raw EMA to the evidence-shrunk
    value (playing-field TODO G1). 2026-09-28: it moved AGAIN to
    `selection_rank` -- scar band, then value band, then conservative score --
    so the reference below now calls the SAME public key the selector calls
    (`selection_rank` is exposed for exactly this). What stays under test is
    the O(n^2) -> O(n log n) rewrite: that precomputed first-seen positions
    reproduce the `names.index(n)` tie-break, and that duplicate names keep
    input order. Pinning a stale key here is how the guard came to assert a
    plateau -- it failed on a correct selector, not the reverse.
    """
    sr.spine_reset()
    try:
        names = [f"W{i}" for i in range(60)]
        names += ["W1", "W1", "W30"]  # duplicates must keep input order
        for i, n in enumerate(names):
            if i % 7 == 0:
                sr.record_outcome("mesh", n, False, 5.0)
        sr._Q_WEIGHTS["mesh:W30"] = 0.9
        sr._SCARS["mesh:W5"] = {"since": "now", "reason": "test"}

        # Reference implementation: the original O(n^2) `names.index(n)`
        # expression, evaluated on the SAME public sort key q_order uses.
        # Lock order mirrors q_order (homeostat outer, scar inner).
        with sr._HOMEOSTAT_LOCK:
            with sr._SCAR_LOCK:
                reference = [(names.index(n),
                              sr.selection_rank(
                                  sr._Q_WEIGHTS.get(f"mesh:{n}", 0.0),
                                  sr._evidence_unlocked(f"mesh:{n}"),
                                  sr._Q_VARIANCE.get(f"mesh:{n}", 0.0),
                                  scarred=f"mesh:{n}" in sr._SCARS),
                              n)
                             for n in names]
        reference.sort(key=lambda t: (-t[1][0], -t[1][1], -t[1][2], t[0]))
        expected = [n for _, _, n in reference]

        assert sr.q_order("mesh", names) == expected
    finally:
        sr.spine_reset()


def test_q_order_scales_linearly():
    """A timing guard with generous headroom (the old form is ~n^2)."""
    sr.spine_reset()
    try:
        names = [f"W{i}" for i in range(4000)]
        t0 = time.perf_counter()
        sr.q_order("mesh", names)
        elapsed = time.perf_counter() - t0
        # 4000 names was ~1.6s with `index()` in a comprehension on 3.14t;
        # well under a second is the whole point of the change.
        assert elapsed < 1.0, f"q_order(4000) took {elapsed:.3f}s — still slow"
    finally:
        sr.spine_reset()
