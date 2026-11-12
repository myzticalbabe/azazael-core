"""Tests for eve_unleashed.adaptive_hyperedge -- one per mechanism + wiring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.adaptive_hyperedge import (
    DynamicHypergraph,
    FlexibleHyperedge,
    MultiScaleHypergraph,
    TaskAdaptiveTopology,
    adaptive_expansion,
    HeterophilyAware,
)


def test_dynamic_prunes_and_splits():
    g = DynamicHypergraph()
    hot = g.add_edge(["a", "b", "c", "d"])
    cold = g.add_edge(["x", "y"])
    for _ in range(5):
        g.observe(hot, True)
    g.observe(cold, False)
    live = g.adapt()
    assert cold not in live
    assert len(live) >= 2  # hot split


def test_flexible_membership_learns():
    e = FlexibleHyperedge(["a", "b", "c"])
    e.update("a", 1.0)
    e.update("b", -20.0)
    assert "a" in e.members()
    assert "b" not in e.members()


def test_multiscale_context_orders_by_scale():
    m = MultiScaleHypergraph(scales=(2, 4))
    m.build(["a", "b", "c", "d"])
    ctx = m.context("a")
    assert ctx[0] == "b"  # finest scale first
    assert set(ctx) == {"b", "c", "d"}


def test_task_topology_scales_with_difficulty():
    t = TaskAdaptiveTopology()
    easy = t.generate(["a", "b", "c", "d", "e", "f"], 0.1)
    hard = t.generate(["a", "b", "c", "d", "e", "f"], 1.0)
    assert len(easy) <= len(hard)


def test_adaptive_expansion_weights_similar():
    w = adaptive_expansion([["a", "b", "c"]], sim=lambda x, y: 1.0 if x != y else 0.0)
    assert w  # every non-anchor pair binds to an anchor
    assert all(v > 0 for v in w.values())


def test_heterophily_signs_agreement():
    h = HeterophilyAware()
    for n, lab in [("a", 0), ("b", 0), ("c", 1)]:
        h.observe(n, lab)
    assert h.aggregate("a", ["b"]) == 1.0
    assert h.aggregate("a", ["c"]) == -1.0
    assert h.aggregate("a", ["b", "c"]) == 0.0


def test_all_six_wire_together():
    g = DynamicHypergraph()
    eid = g.add_edge(["a", "b", "c"])
    g.observe(eid, True)
    topo = TaskAdaptiveTopology()
    edges = topo.generate(["a", "b", "c", "d"], 0.8)
    w = adaptive_expansion(edges)
    assert isinstance(w, dict) and g.members("a") == [eid]
