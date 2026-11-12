# coding: utf-8
"""PSYCHE Phase 4 gate: the construct graph estimates, propagates, and is editable.

- Edges are ridge-shrunk partial correlations, not declared weights: a planted
  conditional dependency is recovered, and an independent pair does NOT get one
  (negative control).
- Too few observations yields NO edges plus a recorded reason, never a guess.
- Message passing respects edge sign: an inhibitory edge damps its neighbour
  while an equal excitatory edge raises it (the discriminating pair).
- Centrality carries the support it rests on.
- The artifact round-trips and is editable.
- NO demographic inputs anywhere in the call graph (introspected).
"""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PSYCHE import network as N


def _co_moving_observations(n=40):
    """Two constructs that move together; a third that moves independently."""
    out = []
    for i in range(n):
        x = ((i % 10) - 5) / 5.0
        z = (((i * 7) % 10) - 5) / 5.0
        out.append({"pressure": x, "tension": 0.9 * x, "noise_node": z})
    return out


def _net_with(observations):
    net = N.PersonNetwork(nodes=["pressure", "tension", "noise_node"])
    for i, obs in enumerate(observations):
        net.observe(obs, at_s=float(i))
    return net


def test_partial_correlation_recovers_planted_dependency():
    net = _net_with(_co_moving_observations())
    edges = {(e.a, e.b): e for e in net.fit_edges()}
    key = ("pressure", "tension")
    assert key in edges, sorted(edges)
    assert edges[key].weight > 0.3
    # support is recorded, so a strong edge can be told from a strong edge on
    # one observation
    assert edges[key].support == 40


def test_independent_construct_gets_no_strong_edge():
    """Negative control: the estimator must not manufacture structure."""
    net = _net_with(_co_moving_observations())
    net.fit_edges()
    for edge in net.edges():
        if "noise_node" in (edge.a, edge.b):
            assert abs(edge.weight) < 0.5, edge.to_dict()


def test_fit_edges_refuses_to_guess_from_two_observations():
    net = N.PersonNetwork(nodes=["a", "b", "c"])
    net.observe({"a": 1.0, "b": 0.5}, at_s=0.0)
    net.observe({"a": -1.0, "b": -0.5}, at_s=1.0)
    assert net.fit_edges() == []
    assert net.diagnostics()["errors"]
    assert "need 3 and 3" in net.diagnostics()["errors"][-1]


def test_constant_columns_do_not_crash_the_estimator():
    """Ridge is what makes an unidentifiable graph invertible instead of fatal."""
    net = N.PersonNetwork(nodes=["a", "b", "c"])
    for i in range(6):
        net.observe({"a": 1.0, "b": 1.0, "c": 1.0}, at_s=float(i))
    assert isinstance(net.fit_edges(), list)


def test_message_passing_respects_edge_sign():
    excit = N.PersonNetwork(nodes=["a", "b"])
    excit.add_edge("a", "b", 0.8)
    inhib = N.PersonNetwork(nodes=["a", "b"])
    inhib.add_edge("a", "b", -0.8)
    up = excit.propagate({"a": 1.0}, steps=1)
    down = inhib.propagate({"a": 1.0}, steps=1)
    assert up["b"] > 0.0
    assert down["b"] < 0.0, down


def test_degree_normalisation_stops_a_hub_shouting():
    hub = N.PersonNetwork(nodes=["hub", "a", "b", "c", "d"])
    for other in ("a", "b", "c", "d"):
        hub.add_edge("hub", other, 0.9)
    leaf = N.PersonNetwork(nodes=["hub", "a"])
    leaf.add_edge("hub", "a", 0.9)
    many = hub.propagate({"hub": 1.0}, steps=1)["a"]
    few = leaf.propagate({"hub": 1.0}, steps=1)["a"]
    assert many < few


def test_centrality_carries_its_support():
    net = _net_with(_co_moving_observations())
    net.fit_edges()
    rows = net.centrality()
    assert set(rows) == {"pressure", "tension", "noise_node"}
    assert rows["pressure"]["support"] == 40
    assert rows["pressure"]["strength"] >= rows["noise_node"]["strength"]


def test_isolated_node_reports_zero_not_a_missing_key():
    net = N.PersonNetwork(nodes=["a", "b", "lonely"])
    net.add_edge("a", "b", 0.5, support=5)
    rows = net.centrality()
    assert rows["lonely"]["edges"] == 0
    assert rows["lonely"]["strength"] == 0.0


def test_explain_names_the_two_hop_path():
    net = N.PersonNetwork(nodes=["a", "b", "c"])
    net.add_edge("a", "b", 0.7, support=9)
    net.add_edge("b", "c", 0.6, support=9)
    paths = net.explain("c")
    assert paths[0]["from"] == "b"
    assert paths[0]["via"]["from"] == "a"
    assert paths[0]["path_strength"] == pytest.approx(0.42, abs=1e-6)


def test_artifact_round_trips():
    net = _net_with(_co_moving_observations())
    net.fit_edges()
    clone = N.PersonNetwork.from_dict(net.to_dict())
    assert clone.nodes == net.nodes
    assert [(e.a, e.b, round(e.weight, 6)) for e in clone.edges()] == \
           [(e.a, e.b, round(e.weight, 6)) for e in net.edges()]
    assert clone.diagnostics()["observations"] == net.diagnostics()["observations"]


def test_edges_are_editable_by_the_person():
    net = N.PersonNetwork()
    net.add_node("a")
    net.add_node("b")
    net.add_edge("a", "b", 0.99)
    net.add_edge("a", "b", -0.4)
    assert len([e for e in net.edges() if e.key() == ("a", "b")]) == 1
    assert net.edges()[0].weight == pytest.approx(-0.4)


def test_fit_is_deterministic():
    first = _net_with(_co_moving_observations())
    second = _net_with(_co_moving_observations())
    first.fit_edges()
    second.fit_edges()
    assert [e.to_dict() for e in first.edges()] == [e.to_dict() for e in second.edges()]


def test_activation_from_pad_is_directional():
    happy = N.activation_from_pad((0.9, 0.2, 0.6))
    unhappy = N.activation_from_pad((-0.9, 0.2, -0.6))
    assert happy["approach"] > unhappy["approach"]
    assert happy["avoidance"] < unhappy["avoidance"]
    anxious = N.activation_from_pad((0.0, 0.9, -0.4), families=("anxiety",))
    assert anxious["threat_appraisal"] > 0.3


def test_no_demographic_inputs_exist():
    banned = ("sex", "gender", "race", "ethnic", "age", "income", "religion",
              "nationality", "diagnosis", "disorder")
    for name, fn in vars(N).items():
        if not inspect.isfunction(fn) or fn.__module__ != N.__name__:
            continue
        for param in inspect.signature(fn).parameters:
            assert not any(word in param.lower() for word in banned), (name, param)
    for method in ("observe", "add_edge", "propagate", "fit_edges", "explain"):
        for param in inspect.signature(getattr(N.PersonNetwork, method)).parameters:
            assert not any(word in param.lower() for word in banned), (method, param)
