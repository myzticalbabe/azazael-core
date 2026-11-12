#!/usr/bin/env python3
"""Deeper tests for Eve Unleashed consciousness subsystem."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


def test_self_model_register_and_health():
    from eve_unleashed.consciousness import SelfModel
    sm = SelfModel()
    sm.register("w1", "worm", capabilities=["monitor"], version="2.0.0")
    assert "w1" in sm.components
    assert sm.components["w1"].health == 1.0
    sm.update_health("w1", 0.3, "degraded")
    assert sm.components["w1"].health == 0.3
    assert sm.components["w1"].status == "degraded"
    assert sm.get_unhealthy(0.7) == ["w1"]
    # clamp
    sm.update_health("w1", 5.0, "x")
    assert sm.components["w1"].health == 1.0
    sm.update_health("w1", -1.0, "x")
    assert sm.components["w1"].health == 0.0
    # unknown component no-op
    sm.update_health("nope", 0.5, "x")
    assert "nope" not in sm.components


def test_self_model_propose_mesh_action():
    from eve_unleashed.consciousness import SelfModel
    sm = SelfModel()
    sm.total_worms = 0
    p = sm.propose_mesh_action("run_metacluster", "monitor")
    assert p["action"] == "discover_worms"
    sm.total_worms = 5
    sm.register("bad", "worm")
    sm.update_health("bad", 0.2, "degraded")
    p2 = sm.propose_mesh_action("run_metacluster", "monitor")
    assert p2["action"] == "orchestrate"
    assert "bad" in p2["reason"]
    # healthy routine
    sm2 = SelfModel()
    sm2.total_worms = 5
    sm2.register("ok", "worm")
    p3 = sm2.propose_mesh_action("run_metacluster", "monitor")
    assert p3["action"] == "run_metacluster"
    assert p3["confidence"] == 0.7


def test_self_model_snapshot():
    from eve_unleashed.consciousness import SelfModel
    sm = SelfModel()
    sm.register("c", "service")
    snap = sm.snapshot()
    assert snap["components"]["c"]["name"] == "c"
    assert "total_worms" in snap


def test_introspection_run_once_failopen():
    from eve_unleashed.consciousness import SelfModel, IntrospectionLoop

    class BadBridge:
        def get_bridge_status(self):
            raise RuntimeError("boom")

        def send_to_llama(self, p):
            return "x"

    class BadDistill:
        def distill(self, x):
            raise ValueError("no")

    sm = SelfModel()
    sm.total_worms = 1
    loop = IntrospectionLoop(self_model=sm, bridge=BadBridge(), distill=BadDistill())
    loop._get_bridge = lambda: loop._bridge
    ins = loop.run_once()
    assert "unavailable" in str(ins.get("bridge_analysis"))
    assert ins.get("distilled") is None
    assert ins["cycle"] == 1
    assert sm.introspection_count == 1


def test_introspection_act_fn_called():
    from eve_unleashed.consciousness import SelfModel, IntrospectionLoop
    sm = SelfModel()
    sm.total_worms = 5
    sm.register("bad", "worm")
    sm.update_health("bad", 0.1, "degraded")
    calls = []

    def act(proposed):
        calls.append(proposed)

    loop = IntrospectionLoop(self_model=sm, act_fn=act)
    loop._get_bridge = lambda: None
    loop.run_once()
    assert len(calls) == 1
    assert calls[0]["action"] == "orchestrate"


def test_introspection_no_act_fn():
    from eve_unleashed.consciousness import SelfModel, IntrospectionLoop
    sm = SelfModel()
    sm.total_worms = 5
    loop = IntrospectionLoop(self_model=sm)
    loop._get_bridge = lambda: None
    ins = loop.run_once()
    assert "cycle" in ins
    assert loop.recent_insights(5) == [ins]


def test_introspection_thread_start_stop():
    from eve_unleashed.consciousness import SelfModel, IntrospectionLoop
    sm = SelfModel()
    sm.total_worms = 5
    loop = IntrospectionLoop(self_model=sm, interval_s=10.0)
    loop._get_bridge = lambda: None
    loop.start()
    assert loop._running is True
    assert loop._thread is not None and loop._thread.is_alive()
    t0 = time.time()
    loop.stop()
    assert (time.time() - t0) < 5.0
    assert loop._thread.is_alive() is False


def test_goal_setting():
    from eve_unleashed.consciousness import GoalSetting
    gs = GoalSetting()
    ins = {"bridge_analysis": "latency high error rate health", "distilled": "coverage gap"}
    goals = gs.propose_from_introspection(ins)
    assert len(goals) == 4
    titles = {g.title for g in goals}
    assert "Reduce inference latency" in titles
    # pams
    g2 = gs.propose_from_pams([{"x": "error"}, {"y": "error"}, {"z": "error"}])
    assert len(g2) == 1
    assert gs.propose_from_pams([]) == []
    assert gs.propose_from_pams([{"a": "ok"}]) == []
    # accept
    assert gs.accept(goals[0].id) is True
    assert gs.accept(goals[0].id) is False
    assert gs.accept("missing") is False
    snap = gs.snapshot()
    assert len(snap) == 5


def test_reflection():
    from eve_unleashed.consciousness import Reflection
    r = Reflection()
    rep = r.reflect({"best_fitness": 1.0, "generation": 1}, {"best_fitness": 2.0, "generation": 3})
    assert rep["improved"] is True
    assert rep["generation_delta"] == 2
    rep2 = r.reflect({"best_fitness": 2.0, "generation": 3}, {"best_fitness": 2.0, "generation": 4})
    assert rep2["improved"] is False
    assert len(r.snapshot()) == 2
    # missing keys
    rep3 = r.reflect({}, {})
    assert rep3["improved"] is False


def test_component_model_defaults():
    from eve_unleashed.consciousness import ComponentModel
    c = ComponentModel(name="x", kind="worm")
    assert c.health == 1.0
    assert c.capabilities == []
    assert c.status == "unknown"


def test_improvement_goal_accept_status():
    from eve_unleashed.consciousness import GoalSetting, ImprovementGoal
    gs = GoalSetting()
    g = ImprovementGoal(id="g1", title="t", rationale="r", source="x", priority=0.5)
    gs.goals.append(g)
    assert gs.accept("g1") is True
    assert g.status == "accepted"
    # already accepted cannot re-accept
    assert gs.accept("g1") is False
