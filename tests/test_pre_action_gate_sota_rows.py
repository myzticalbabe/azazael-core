"""EP269 (Agent libOS) — task authority at the PreActionGate choke point.

The rescore named this gate as the real host for EP269 and recorded the
missing symbol: no task-authority/capability concept existed in the gate or in
`compliance/policy.py` — the rule engine was invoked with a hardcoded
`role="developer"` literal, so every subject implicitly held every action.

These tests pin the grant side of that concept and, critically, that the
DEFAULT gate is unchanged: with no table installed no edit can be rejected on
capability, so this is adoption rather than a new deny path.

Repo convention: tests insert src/ themselves (tests/conftest.py does not).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import CORE.compliance.pre_action_gate as pagmod
from CORE.compliance.pre_action_gate import PreActionGate


def test_no_table_installed_grants_every_action():
    """Fail-open is the contract, not an oversight: nothing installed means
    the gate cannot deny, which is exactly its pre-EP269 behaviour."""
    gate = PreActionGate()
    assert gate.authorize("developer", "write_file", "src/CORE/anything.py") == (True, "")
    assert gate.authorize("nobody", "delete_file", "C:/elsewhere/x.py") == (True, "")


def test_installed_table_denies_a_subject_with_no_grant():
    gate = PreActionGate()
    assert gate.install_capabilities({"developer": ["read_file:*"]})["ok"] is True
    ok, reason = gate.authorize("intern", "write_file", "src/CORE/x.py")
    assert ok is False
    assert "no task authority" in reason


def test_glob_grant_matches_action_and_path():
    gate = PreActionGate()
    gate.install_capabilities({"developer": ["write_file:src/CORE/*"]})
    allowed, _ = gate.authorize("developer", "write_file", "src/CORE/x.py")
    assert allowed is True
    denied, _ = gate.authorize("developer", "write_file", "src/PSYCHE/x.py")
    assert denied is False
    wrong_action, _ = gate.authorize("developer", "read_file", "src/CORE/x.py")
    assert wrong_action is False


def test_windows_backslashes_match_a_forward_slash_pattern():
    """One table must work on the Windows host and the WSL mirror."""
    gate = PreActionGate()
    gate.install_capabilities({"developer": ["write_file:src/CORE/*"]})
    ok, _ = gate.authorize("developer", "write_file", "src\\CORE\\x.py")
    assert ok is True


def test_catch_all_pattern_grants_any_action_on_any_path():
    gate = PreActionGate()
    gate.install_capabilities({"root": ["*"]})
    assert gate.authorize("root", "anything", "anywhere")[0] is True


def test_malformed_table_installs_nothing():
    """A half-applied authority table is worse than none: the malformed input
    must leave the fail-open state intact and name the gap."""
    gate = PreActionGate()
    out = gate.install_capabilities({"": ["write_file:*"]})
    assert out["ok"] is False
    assert "empty-subject" in out["gaps"]
    assert gate.authorize("developer", "write_file", "x.py")[0] is True


def test_capability_denial_precedes_the_rule_engine():
    """An unheld capability denies before the orchestrator is consulted — which
    also proves the check does not depend on a live orchestrator."""
    gate = PreActionGate()
    gate.install_capabilities({"developer": ["read_file:*"]})
    assert gate.orchestrator is None
    ok, msg = gate._check_rule_0x_0y("write_file", "src/CORE/x.py")
    assert ok is False
    assert "RULE 0X/0Y DENIED (capability)" in msg
    assert "no grant" in msg


def test_granted_subject_still_reaches_the_rule_engine_unchanged():
    """Regression guard: with a grant in hand the pre-existing blocking-deny
    path and its message are untouched."""
    gate = PreActionGate()
    # Grant the action this test actually exercises: action matching is exact by
    # design (see test_glob_grant_matches_action_and_path), and production gates
    # `validate_str_replace` under "str_replace", not "write_file".
    gate.install_capabilities({"developer": ["str_replace:src/CORE/*"]})

    class _FakeOrchestrator:
        def check_all(self, action, role, context):
            # The gate must forward its subject, not a hardcoded literal.
            assert role == "developer"
            return SimpleNamespace(
                rule_0x={"components": {"policy_gate": {"passed": False}}},
                rule_0y={"components": {}},
                summary="policy_gate failed",
            )

    gate.orchestrator = _FakeOrchestrator()
    original = pagmod._ox_component_passed
    pagmod._ox_component_passed = lambda name, data: False
    try:
        ok, msg = gate._check_rule_0x_0y("str_replace", "src/CORE/x.py")
    finally:
        pagmod._ox_component_passed = original

    assert ok is False
    assert "0X:policy_gate" in msg
    assert "targeted checks:" in msg


def test_authorize_uses_the_installed_subject_by_default():
    gate = PreActionGate()
    gate.install_capabilities({"reviewer": ["read_file:*"]}, subject="reviewer")
    assert gate.subject == "reviewer"
    assert gate.authorize(action="read_file", filepath="x.py")[0] is True
    assert gate.authorize(action="write_file", filepath="x.py")[0] is False
