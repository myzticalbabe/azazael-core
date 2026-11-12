#!/usr/bin/env python3
"""Deeper tests for EveArm mesh-driving capabilities."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "AOA"))

import pytest

@pytest.fixture(scope="module")
def arm():
    from AOA.arms.eve_arm import EveArm
    a = EveArm()
    assert a.initialize() is True
    return a


def test_arm_init_status(arm):
    r = arm.execute("get_status", {})
    assert r["status"] == "ok"
    d = r["data"]
    assert d["initialized"] is True
    assert d["mesh_available"] is True
    assert d["consciousness_available"] is True
    assert d["decisions_available"] is True


def test_arm_capabilities_list(arm):
    caps = [c.name for c in arm.get_capabilities()]
    assert "conscious_act" in caps
    assert "introspect" in caps
    assert "orchestrate" in caps
    assert "drive_mesh" in caps
    assert len(caps) == 15


def test_arm_mesh_status(arm):
    r = arm.execute("get_mesh_status", {})
    assert r["status"] == "ok"
    assert r["data"]["mesh_available"] is True
    assert r["data"]["total_worms"] > 0


def test_arm_discover_worms(arm):
    r = arm.execute("discover_worms", {})
    assert r["status"] == "ok"
    assert r["data"]["worm_count"] > 0


def test_arm_drive_mesh_orchestrate(arm):
    # mark a component unhealthy to force orchestrate proposal path
    name = list(arm._self_model.components.keys())[0]
    arm._self_model.update_health(name, 0.1, "degraded")
    r = arm.execute("drive_mesh", {"action": "orchestrate", "nature": "monitor", "confidence": 0.85})
    assert r["status"] == "ok", r
    # restore
    arm._self_model.update_health(name, 1.0, "healthy")


def test_arm_conscious_act_routine(arm):
    r = arm.execute("conscious_act", {"action": "run_metacluster", "nature": "monitor"})
    assert r["status"] == "ok"
    assert r["data"]["proposed"] is not None
    assert r["data"]["execution"]["status"] == "ok"


def test_arm_conscious_act_unhealthy(arm):
    name = list(arm._self_model.components.keys())[0]
    arm._self_model.update_health(name, 0.2, "degraded")
    r = arm.execute("conscious_act", {"action": "run_metacluster", "nature": "monitor"})
    assert r["status"] == "ok"
    assert r["data"]["proposed"]["action"] == "orchestrate"
    assert r["data"]["execution"]["status"] == "ok"
    arm._self_model.update_health(name, 1.0, "healthy")


def test_arm_introspect(arm):
    r = arm.execute("introspect", {})
    assert r["status"] == "ok"
    assert "cycle" in r["data"]
    assert r["data"]["unhealthy"] == []


def test_arm_spawn_worm(arm):
    r = arm.execute("spawn_worm", {"worm_type": "monitor"})
    assert r["status"] == "ok"
    assert r["data"]["results_count"] > 0


def test_arm_run_cluster(arm):
    r = arm.execute("run_cluster", {"nature": "monitor"})
    assert r["status"] == "ok"
    assert r["data"]["results_count"] > 0


def test_arm_orchestrate(arm):
    r = arm.execute("orchestrate", {})
    assert r["status"] == "ok"
    assert r["data"]["total_worms_fail"] == 0


def test_arm_interpreter_status(arm):
    r = arm.execute("get_interpreter_status", {})
    assert r["status"] == "ok"
    assert "interpreter_available" in r["data"]


def test_arm_run_interpreter_unavailable(arm):
    # interpreter may or may not be available; just ensure no crash
    r = arm.execute("run_interpreter", {"instruction": "status"})
    assert isinstance(r, dict)
    assert "status" in r


def test_arm_unknown_capability(arm):
    r = arm.execute("nope_xyz", {})
    assert r["status"] == "error"


def test_arm_shutdown(arm):
    assert arm.shutdown() is True
