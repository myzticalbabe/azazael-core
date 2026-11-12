# coding: utf-8
"""The Arm's own verdict — recorded, labelled, and never merged with the router's.

``status`` says what came back. ``outcome.verdict`` says whether the router
accepted it. Neither says whether the answer was RIGHT, and only the Arm knows:
a search that finds nothing returns ``ok``, and so does a lookup that resolves to
the wrong record.

These tests hold three lines that are easy to blur later:

1. an Arm that says nothing is unchanged, byte for byte, in ``to_dict()``;
2. the claim is labelled ``judged_by: arm:<id>`` and an Arm cannot relabel it;
3. a disagreement between the claim and the router is *preserved* in the span,
   never resolved by picking one.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AOA = os.path.join(_project_root, "src", "AOA")
for _p in (_project_root, _AOA, os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import CORE.cd3_exporter as cd3  # noqa: E402
from core import router as router_mod  # noqa: E402
from core.contracts import (  # noqa: E402
    ARM_VERDICTS,
    ArmResult,
    normalize_arm_verdict,
    verdict_agreement,
)


class TestNormalisation(unittest.TestCase):

    def test_bare_string_is_accepted(self):
        assert normalize_arm_verdict("incorrect", "search") == {
            "verdict": "incorrect", "judged_by": "arm:search"}

    def test_dict_keeps_confidence_and_evidence(self):
        out = normalize_arm_verdict(
            {"verdict": "correct", "confidence": 0.75,
             "evidence": {"rows": 3}}, "lookup")
        assert out["verdict"] == "correct"
        assert out["confidence"] == 0.75
        assert out["evidence"] == {"rows": 3}
        assert out["judged_by"] == "arm:lookup"

    def test_case_and_whitespace_are_normalised(self):
        assert normalize_arm_verdict("  CORRECT ", "a")["verdict"] == "correct"

    def test_an_unknown_verdict_is_dropped_not_coerced(self):
        # The control for the whole feature: an unrecognised claim yields None
        # rather than being rounded to "correct" or "undetermined", because a
        # claim the reader cannot interpret is worse than no claim.
        assert normalize_arm_verdict("pretty sure", "a") is None
        assert normalize_arm_verdict("", "a") is None

    def test_out_of_range_confidence_is_dropped_not_clamped(self):
        out = normalize_arm_verdict({"verdict": "correct", "confidence": 1.7}, "a")
        assert out == {"verdict": "correct", "judged_by": "arm:a"}
        assert "confidence" not in out

    def test_a_negative_confidence_is_also_dropped(self):
        assert "confidence" not in normalize_arm_verdict(
            {"verdict": "incorrect", "confidence": -0.2}, "a")

    def test_an_arm_cannot_name_its_own_judge(self):
        out = normalize_arm_verdict(
            {"verdict": "correct", "judged_by": "aoa.router"}, "search")
        assert out["judged_by"] == "arm:search"

    def test_non_dict_non_string_is_dropped(self):
        for bad in ([], 3, object()):
            assert normalize_arm_verdict(bad, "a") is None

    def test_the_enum_is_closed(self):
        assert set(ARM_VERDICTS) == {"correct", "incorrect", "undetermined"}


class TestAgreement(unittest.TestCase):

    def test_the_four_decidable_pairs(self):
        assert verdict_agreement("accepted", {"verdict": "correct"}) == "agree"
        assert verdict_agreement("accepted", {"verdict": "incorrect"}) == "disagree"
        assert verdict_agreement("rejected", {"verdict": "incorrect"}) == "agree"
        assert verdict_agreement("rejected", {"verdict": "correct"}) == "disagree"

    def test_unknown_when_nobody_claimed(self):
        assert verdict_agreement("accepted", None) == "unknown"
        assert verdict_agreement("accepted", {}) == "unknown"

    def test_unknown_when_the_arm_declined_to_say(self):
        assert verdict_agreement("accepted", {"verdict": "undetermined"}) == "unknown"

    def test_unverified_is_not_evidence_either_way(self):
        # A stale answer is not an accepted answer, so there is nothing to agree
        # with; calling this "agree" would let staleness read as correctness.
        assert verdict_agreement("unverified", {"verdict": "correct"}) == "unknown"


class TestArmResultCarriesTheClaim(unittest.TestCase):

    def test_silent_arm_produces_no_new_key(self):
        out = ArmResult(status="ok", arm_id="a", data={"x": 1}).to_dict()
        assert "arm_verdict" not in out

    def test_plain_dict_output_is_read_before_it_is_discarded(self):
        result = ArmResult.from_arm_output(
            {"status": "ok", "data": {}, "arm_verdict": "incorrect"}, "lookup",
            "find")
        assert result.arm_verdict == {"verdict": "incorrect",
                                      "judged_by": "arm:lookup"}
        assert result.to_dict()["arm_verdict"]["verdict"] == "incorrect"

    def test_envelope_output_is_read_too(self):
        raw = {"status": "ok", "produced_at": "2026-11-02T05:30:00",
               "arm_id": "lookup", "arm_verdict": {"verdict": "correct",
                                                   "confidence": 0.4}}
        result = ArmResult.from_arm_output(raw, "lookup", "find")
        assert result.arm_verdict["verdict"] == "correct"
        assert result.arm_verdict["confidence"] == 0.4

    def test_roundtrip_keeps_the_claim(self):
        original = ArmResult(status="ok", arm_id="a", data=None,
                             arm_verdict={"verdict": "undetermined",
                                          "judged_by": "arm:a"})
        again = ArmResult.from_dict(original.to_dict())
        assert again.arm_verdict == original.arm_verdict

    def test_a_bad_claim_does_not_break_the_result(self):
        result = ArmResult.from_arm_output(
            {"status": "ok", "data": {"k": 1}, "arm_verdict": "nonsense"},
            "a", "c")
        assert result.status == "ok"
        assert result.data == {"k": 1}
        assert result.arm_verdict is None


# -- the router/span side ----------------------------------------------------

class _Cap:
    def __init__(self, name):
        self.name = name


class _VerdictArm:
    """Declares one capability and reports a self-assessment."""

    def __init__(self, arm_id="arm1", verdict=None):
        self._arm_id = arm_id
        self._verdict = verdict
        self.is_initialized = False
        self.metadata = type("M", (), {"priority": 5, "arm_id": arm_id})()

    def initialize(self):
        self.is_initialized = True
        return True

    def execute(self, capability, payload=None):
        out = {"status": "ok", "data": {"arm": self._arm_id}}
        if self._verdict is not None:
            out["arm_verdict"] = self._verdict
        return out

    def get_capabilities(self):
        return [_Cap("find")]


class _Registry:
    def __init__(self, arms):
        self._arms = {a._arm_id: a for a in arms}

    @property
    def arms(self):
        return self._arms

    def get_arm(self, arm_id):
        return self._arms.get(arm_id)

    def list_arms(self):
        return [{"arm_id": a, "category": "q", "priority": 5} for a in self._arms]


class _Spine:
    def get_q_weight(self, nature, worm):
        return 0.0

    def get_q_updates(self, nature, worm):
        return 0


try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where, exc=None):
        pass  # ledger missing; the swallow is still named at the call site

    def record_outcome(self, nature, worm, success, duration_ms, censored=False):
        return True


class _SpanFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "telemetry_store.db")
        self._orig_path = cd3.TELEMETRY_DB_PATH
        self._orig_env = os.environ.get("AZAZAEL_AOA_TRACING")
        cd3.TELEMETRY_DB_PATH = Path(self.db)
        cd3._schema_ready.discard(self.db)
        self._had_spine = hasattr(router_mod, "_spine")
        self._orig_spine = getattr(router_mod, "_spine", None)
        router_mod._spine = lambda: _Spine()

    def tearDown(self):
        cd3.TELEMETRY_DB_PATH = self._orig_path
        cd3._schema_ready.discard(self.db)
        if self._orig_env is None:
            os.environ.pop("AZAZAEL_AOA_TRACING", None)
        else:
            os.environ["AZAZAEL_AOA_TRACING"] = self._orig_env
        if self._had_spine:
            router_mod._spine = self._orig_spine
        else:
            try:
                del router_mod._spine
            except AttributeError as exc:
                note_failure("tests/test_arm_verdict.py", exc)
                pass
        self._tmp.cleanup()

    def attrs(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM otel_spans")]
        finally:
            conn.close()
        assert len(rows) == 1, "expected one span, got %d" % len(rows)
        return json.loads(rows[0]["attributes"])

    def route(self, arm):
        from core.router import CrossArmRouter
        return CrossArmRouter(registry=_Registry([arm])).route_task(None, "find")


class TestTheSpanRecordsCorrectness(_SpanFixture):

    def test_an_arm_that_admits_it_was_wrong_is_recorded_as_a_disagreement(self):
        flat = self.route(_VerdictArm(verdict="incorrect"))
        # The router only ever saw "ok":
        assert flat["outcome"]["verdict"] == "accepted"
        assert flat["status"] == "ok"
        # The arm's own reading survives, and is not swallowed by that "ok":
        assert flat["arm_verdict"]["verdict"] == "incorrect"
        attrs = self.attrs()
        assert attrs["agent.delegation.status"] == "ok"
        assert attrs["agent.delegation.verdict"] == "accepted"
        assert attrs["agent.delegation.arm_verdict"] == "incorrect"
        assert attrs["agent.delegation.verdict_agreement"] == "disagree"
        assert attrs["agent.delegation.arm_verdict_judged_by"] == "arm:arm1"

    def test_confidence_travels_with_the_claim(self):
        self.route(_VerdictArm(verdict={"verdict": "correct",
                                        "confidence": 0.25}))
        attrs = self.attrs()
        assert attrs["agent.delegation.arm_verdict_confidence"] == 0.25
        assert attrs["agent.delegation.verdict_agreement"] == "agree"

    def test_control_a_silent_arm_records_no_claim(self):
        self.route(_VerdictArm(verdict=None))
        attrs = self.attrs()
        for key in ("agent.delegation.arm_verdict",
                    "agent.delegation.arm_verdict_confidence",
                    "agent.delegation.arm_verdict_judged_by"):
            assert key not in attrs, "%s should be absent for a silent arm" % key
        # ...and the agreement field still exists, reading "unknown" rather than
        # implying the arm was right.
        assert attrs["agent.delegation.verdict_agreement"] == "unknown"

    def test_a_nonsense_claim_does_not_reach_the_span(self):
        flat = self.route(_VerdictArm(verdict="trust me"))
        assert "arm_verdict" not in flat
        attrs = self.attrs()
        assert "agent.delegation.arm_verdict" not in attrs
        assert attrs["agent.delegation.verdict_agreement"] == "unknown"

    def test_readback_keeps_the_two_verdicts_apart(self):
        from core.delegation_tracer import delegation_outcomes
        flat = self.route(_VerdictArm(verdict="incorrect"))
        rows = delegation_outcomes(flat["trace"]["trace_id"])
        assert len(rows) == 1
        assert rows[0]["verdict"] == "accepted"        # router
        assert rows[0]["arm_verdict"] == "incorrect"   # arm
        assert rows[0]["arm_judged_by"] == "arm:arm1"
        assert rows[0]["agreement"] == "disagree"


if __name__ == "__main__":
    unittest.main()
