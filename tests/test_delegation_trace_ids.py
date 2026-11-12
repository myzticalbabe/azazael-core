# coding: utf-8
"""Delegation tracing identity — additive, and provably so.

AOA could not answer "was this delegation the right one?" because the
trace died at the router. ``DelegationSpec`` now carries optional
``trace_id`` / ``span_id`` / ``parent_span_id``.

The point of these tests is the CONTROL. Adding tracing to a wire
contract is only safe if an untraced spec still serialises exactly as it
did before -- so ``test_untraced_payload_is_byte_identical`` asserts
against a literal expected envelope, not against whatever the code now
happens to produce. A test that derives its expectation from the
implementation cannot fail when the implementation changes shape.
"""

import json
import os
import sys
import unittest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AOA = os.path.join(_project_root, "src", "AOA")
for _p in (_project_root, _AOA, os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.delegation_spec import (  # noqa: E402
    ENVELOPE_KEY,
    DelegationSpec,
    from_route,
)

#: The envelope as it was before tracing existed, written out by hand.
#: Frozen on purpose: this is the compatibility contract.
_BASELINE_ENVELOPE = {
    "instruction": "i",
    "tools": ["a"],
    "model": "m",
    "reason": "r",
    "degraded": False,
}


def _baseline_spec(**kwargs):
    fields = dict(instruction="i", context={}, tools=["a"],
                  model="m", reason="r")
    fields.update(kwargs)
    return DelegationSpec(**fields)


class TestUntracedIsUnchanged(unittest.TestCase):

    def test_untraced_payload_is_byte_identical(self):
        out = _baseline_spec().to_payload({"k": "v"})
        assert out == {"k": "v", ENVELOPE_KEY: dict(_BASELINE_ENVELOPE)}

    def test_untraced_payload_is_byte_identical_as_json(self):
        out = _baseline_spec().to_payload({"k": "v"})
        expected = {"k": "v", ENVELOPE_KEY: dict(_BASELINE_ENVELOPE)}
        assert json.dumps(out, sort_keys=True) == json.dumps(
            expected, sort_keys=True)

    def test_untraced_envelope_has_no_tracing_keys(self):
        env = _baseline_spec().to_payload()[ENVELOPE_KEY]
        assert set(env) == set(_BASELINE_ENVELOPE)
        for key in ("trace_id", "span_id", "parent_span_id"):
            assert key not in env, "untraced envelope grew key %r" % key

    def test_untraced_attach_is_unchanged(self):
        result = _baseline_spec().attach({"status": "ok"})
        assert result == {"status": "ok",
                          "delegation": dict(_BASELINE_ENVELOPE)}

    def test_defaults_are_none(self):
        spec = DelegationSpec()
        assert spec.trace_id is None
        assert spec.span_id is None
        assert spec.parent_span_id is None


class TestTracingIsAdditive(unittest.TestCase):

    def test_ids_add_exactly_three_keys_and_nothing_else(self):
        plain = _baseline_spec().to_payload()[ENVELOPE_KEY]
        traced = _baseline_spec(
            trace_id="T", span_id="S", parent_span_id="P",
        ).to_payload()[ENVELOPE_KEY]

        added = set(traced) - set(plain)
        assert added == {"trace_id", "span_id", "parent_span_id"}

        # Removing the tracing keys restores the baseline exactly: the
        # existing five keys were not reshaped.
        stripped = {k: v for k, v in traced.items() if k in plain}
        assert stripped == _BASELINE_ENVELOPE

    def test_tracing_values_survive_the_roundtrip(self):
        env = _baseline_spec(
            trace_id="T", span_id="S", parent_span_id="P",
        ).to_payload()[ENVELOPE_KEY]
        assert env["trace_id"] == "T"
        assert env["span_id"] == "S"
        assert env["parent_span_id"] == "P"

    def test_untraced_keys_stay_absent_when_only_one_is_set(self):
        # Each key is present independently: setting a trace_id must not
        # drag span keys into the envelope as nulls.
        env = _baseline_spec(trace_id="T").to_payload()[ENVELOPE_KEY]
        assert env["trace_id"] == "T"
        assert "span_id" not in env
        assert "parent_span_id" not in env

    def test_payload_wire_shape_is_json_serialisable(self):
        out = _baseline_spec(trace_id="T", span_id="S").to_payload(
            {"k": "v"})
        assert json.loads(json.dumps(out)) == out


class TestParentChildChain(unittest.TestCase):

    def test_child_links_to_parent_span(self):
        parent = _baseline_spec(trace_id="T", span_id="S1")
        child = parent.spawn_child(span_id="S2")

        assert child.trace_id == "T"          # inherited verbatim
        assert child.parent_span_id == "S1"   # names its caller
        assert child.span_id == "S2"          # distinct identity
        assert child.span_id != parent.span_id

    def test_spawn_child_does_not_mutate_the_parent(self):
        parent = _baseline_spec(trace_id="T", span_id="S1")
        child = parent.spawn_child(span_id="S2")
        assert child is not parent
        assert parent.span_id == "S1"
        assert parent.parent_span_id is None
        # The parent is still a frozen value.
        import dataclasses
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parent.span_id = "S9"

    def test_child_inherits_every_other_field(self):
        parent = _baseline_spec(trace_id="T", span_id="S1")
        child = parent.spawn_child(span_id="S2")
        assert child.instruction == parent.instruction
        assert child.context == parent.context
        assert child.tools == parent.tools
        assert child.model == parent.model
        assert child.reason == parent.reason
        assert child.degraded == parent.degraded

    def test_fresh_span_id_when_not_supplied(self):
        parent = _baseline_spec(trace_id="T", span_id="S1")
        a = parent.spawn_child()
        b = parent.spawn_child()
        assert a.span_id and b.span_id
        assert a.span_id != b.span_id
        assert a.span_id != "S1"

    def test_three_deep_chain_stays_in_one_trace(self):
        root = _baseline_spec(trace_id="T", span_id="S1")
        mid = root.spawn_child(span_id="S2")
        leaf = mid.spawn_child(span_id="S3")

        assert {root.trace_id, mid.trace_id, leaf.trace_id} == {"T"}
        assert leaf.parent_span_id == mid.span_id == "S2"
        assert mid.parent_span_id == root.span_id == "S1"
        assert leaf.parent_span_id != root.span_id

    def test_child_of_untraced_parent_reports_absent_link(self):
        # No parent span means no invented parent link.
        child = _baseline_spec().spawn_child(span_id="S2")
        assert child.parent_span_id is None
        assert child.trace_id is None
        assert child.span_id == "S2"


class TestFromRouteCarriesNoIDSByDefault(unittest.TestCase):

    def test_from_route_is_untraced_and_unchanged(self):
        spec = from_route("get_status", {"a": 1},
                          {"reason": "exploit", "degraded": False}, "arm1")
        assert spec.trace_id is None
        assert spec.span_id is None
        assert spec.parent_span_id is None
        env = spec.to_payload()[ENVELOPE_KEY]
        assert set(env) == set(_BASELINE_ENVELOPE)


if __name__ == "__main__":
    unittest.main()
