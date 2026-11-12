# coding: utf-8
"""Delegation spec — the 4-tuple contract across the router boundary.

The router chose arm X for capability Y with reason R, then called
``arm.execute(capability, payload)`` with a bare payload: the decision's
context never reached the arm and the result never recorded the
delegation. ``DelegationSpec`` (instruction, context, tools, model)
crosses that boundary without changing any existing signature.
"""

import os
import sys
import unittest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AOA = os.path.join(_project_root, "src", "AOA")
for _p in (_project_root, _AOA, os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import router as router_mod  # noqa: E402
from core.delegation_spec import (  # noqa: E402
    ENVELOPE_KEY,
    DelegationSpec,
    from_route,
)


class _Cap:
    def __init__(self, name):
        self.name = name


class _Arm:
    is_initialized = True

    def __init__(self, arm_id, caps, priority=5):
        self._arm_id = arm_id
        self._caps = [_Cap(c) for c in caps]
        self.metadata = type("M", (), {"priority": priority,
                                       "arm_id": arm_id})()
        self.seen_payloads = []

    def initialize(self):
        self.is_initialized = True
        return True

    def execute(self, capability, payload=None):
        self.seen_payloads.append(dict(payload or {}))
        return {"status": "ok", "data": {"arm": self._arm_id}}

    def get_capabilities(self):
        return list(self._caps)


class _Registry:
    def __init__(self, arms):
        self._arms = {a._arm_id: a for a in arms}

    @property
    def arms(self):
        return self._arms

    def get_arm(self, arm_id):
        return self._arms.get(arm_id)

    def list_arms(self):
        return [{"arm_id": a, "category": "q", "priority": 5}
                for a in self._arms]


class _Spine:
    def get_q_weight(self, nature, worm):
        return 0.0

    def get_q_updates(self, nature, worm):
        return 0

    def record_outcome(self, nature, worm, success, duration_ms,
                       censored=False):
        return True


class _SpecFixture(unittest.TestCase):
    def setUp(self):
        # 2026-09-28: module-level _spine hook is gone from core.router;
        # tolerate its absence (getattr) so the fixture works with or
        # without the spine-coupled design. Presence is asserted nowhere;
        # the delegation envelope is what's under test.
        self._orig = getattr(router_mod, "_spine", None)
        self._had_spine = hasattr(router_mod, "_spine")
        router_mod._spine = lambda: _Spine()

    def tearDown(self):
        if self._had_spine:
            router_mod._spine = self._orig
        else:
            try:
                del router_mod._spine
            except AttributeError:
                pass

    @staticmethod
    def router(arms):
        from core.router import CrossArmRouter
        return CrossArmRouter(registry=_Registry(arms))


class TestSpecContract(_SpecFixture):

    def test_from_route_names_all_four(self):
        spec = from_route("get_status", {"a": 1},
                          {"reason": "exploit",
                           "evidence": {"x": {"q": 1.0, "pulls": 9}},
                           "degraded": False}, "arm1")
        assert spec.instruction == "get_status [exploit]"
        assert spec.context["a"] == 1
        assert spec.context["_routing_evidence"]["x"] == {"q": 1.0,
                                                          "pulls": 9}
        assert spec.tools == ["arm1"]
        assert spec.model is None
        assert spec.reason == "exploit"

    def test_from_route_never_raises(self):
        spec = from_route(None, None, None, "")
        assert isinstance(spec, DelegationSpec)
        # Pinned (no decision) is not degraded — it simply has no reason.
        assert spec.degraded is False
        # A hostile payload degrades to an empty context, never a raise.
        spec = from_route(None, object(), None, "")
        assert isinstance(spec, DelegationSpec)
        assert spec.context == {}

    def test_payload_roundtrip_keeps_caller_keys(self):
        spec = DelegationSpec(instruction="i", context={}, tools=["a"],
                              model="m", reason="r")
        out = spec.to_payload({"k": "v"})
        assert out["k"] == "v"
        assert out[ENVELOPE_KEY]["instruction"] == "i"
        assert out[ENVELOPE_KEY]["tools"] == ["a"]

    def test_spec_is_frozen(self):
        import dataclasses
        spec = DelegationSpec()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            spec.reason = "x"


class TestRouterCarriesSpec(_SpecFixture):

    def test_arm_receives_envelope(self):
        arm = _Arm("a", ["task"])
        r = self.router([arm])
        out = r.route_task(None, "task", {"k": "v"})
        assert out["status"] == "ok"
        assert len(arm.seen_payloads) == 1
        env = arm.seen_payloads[0].get(ENVELOPE_KEY)
        assert env is not None
        assert env["tools"] == ["a"]
        assert "task" in env["instruction"]
        # Caller payload untouched alongside the envelope.
        assert arm.seen_payloads[0]["k"] == "v"

    def test_result_carries_provenance(self):
        arm = _Arm("a", ["task"])
        r = self.router([arm])
        out = r.route_task(None, "task", {})
        assert out.get("delegation", {}).get("tools") == ["a"]
        assert "reason" in out.get("delegation", {})

    def test_pinned_route_also_specified(self):
        arm = _Arm("a", ["task"])
        r = self.router([arm])
        out = r.route_task("a", "task", {})
        assert out["status"] == "ok"
        assert out.get("delegation", {}).get("tools") == ["a"]

    def test_legacy_arm_ignoring_envelope_unaffected(self):
        arm = _Arm("a", ["task"])
        r = self.router([arm])
        out = r.route_task(None, "task", {})
        assert out["status"] == "ok"
        assert out["data"] == {"arm": "a"}


if __name__ == "__main__":
    unittest.main()
