"""EP152 — a holistic 0X/0Y denial decomposes into targeted checks.

Host: `CORE/compliance/pre_action_gate.py :: PreActionGate` (the acceptance
path `_check_rule_0x_0y`, reached from `validate_str_replace` /
`validate_write_file` on every edit). The delta is a RELOCATION: the
decomposition already exists as `verification_gate.decompose_rubric`; these
tests assert the acceptance path now consumes it, and that the deny itself is
unaffected when the decomposition is unavailable.

Repo convention: tests insert src/ themselves (tests/conftest.py does not).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import CORE.compliance.pre_action_gate as pagmod
from CORE.compliance.pre_action_gate import PreActionGate


def test_multi_criterion_denial_decomposes_into_atomic_subquestions():
    gate = PreActionGate()
    out = gate._decompose_denial(["0X:policy_gate", "0Y:dual_ledger"])
    assert out["ok"] is True
    assert len(out["subquestions"]) == 2
    assert all(q.startswith("Does the artifact satisfy:")
               for q in out["subquestions"])
    assert out["by_subquestion"]                       # a failure class each
    assert set(out["by_subquestion"]) == {0, 1}
    assert out["classes"]                              # taxonomy available


def test_empty_denial_is_a_named_gap_not_a_crash():
    gate = PreActionGate()
    out = gate._decompose_denial([])
    assert out["subquestions"] == []
    assert out["ok"] is False
    assert "no-failing-criteria" in out["gaps"]


def test_duplicate_criteria_collapse_to_one_subquestion():
    gate = PreActionGate()
    out = gate._decompose_denial(["0X:policy_gate", "0X:policy_gate"])
    assert len(out["subquestions"]) == 1


def test_missing_gate_module_falls_back_to_one_check_per_criterion():
    gate = PreActionGate()
    original = pagmod._decompose_rubric
    pagmod._decompose_rubric = None
    try:
        out = gate._decompose_denial(["0X:policy_gate", "0Y:dual_ledger"])
    finally:
        pagmod._decompose_rubric = original
    assert out["subquestions"] == ["does 0X:policy_gate pass?",
                                   "does 0Y:dual_ledger pass?"]
    assert any(g.startswith("verification-gate-unavailable")
               for g in out["gaps"])


def test_blocking_denial_message_carries_the_targeted_checks():
    """The gate must still DENY, with repair targets instead of a bare list."""
    gate = PreActionGate()

    class _FakeOrchestrator:
        def check_all(self, action, role, context):
            return SimpleNamespace(
                rule_0x={"components": {"policy_gate": {"passed": False}}},
                rule_0y={"components": {}},
                summary="policy_gate failed",
            )

    gate.orchestrator = _FakeOrchestrator()
    original = pagmod._ox_component_passed
    pagmod._ox_component_passed = lambda name, data: False
    try:
        ok, msg = gate._check_rule_0x_0y("str_replace", "C:/tmp/x.py")
    finally:
        pagmod._ox_component_passed = original

    assert ok is False
    assert "0X:policy_gate" in msg
    assert "targeted checks:" in msg
    assert "Does the artifact satisfy:" in msg
