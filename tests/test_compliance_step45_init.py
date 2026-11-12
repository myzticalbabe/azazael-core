# coding: utf-8
"""
Tests for azazael-system-startup Step 4.5 — ComplianceOrchestrator initialization.

Verifies that the exact Step 4.5 code from the startup skill executes without
errors: import ComplianceOrchestrator, init, state transition to GATE_ASSERT,
and that the compliance infrastructure (check_rule_0x, check_rule_0y) is usable
immediately after initialization.

Step 4.5 reference (azazael/skills/azazael-system-startup.md):
    import sys; sys.path.insert(0, 'azazael/src')
    from CORE.compliance.orchestrator import ComplianceOrchestrator
    from CORE.compliance.state_machine import ComplianceState

    co = ComplianceOrchestrator()
    co.state_machine.transition_to(ComplianceState.GATE_ASSERT)
    # Compliance infrastructure is now hot: check_rule_0x(), check_rule_0y() ready
"""

import sys
from pathlib import Path

import pytest  # type: ignore[import-not-found]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ── Exact Step 4.5 code execution ──────────────────────────────────


class TestStep45ExactCode:
    """Verify the exact Step 4.5 code from azazael-system-startup skill."""

    def test_imports_execute_without_error(self):
        """The exact Step 4.5 import statements should execute cleanly."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        assert ComplianceOrchestrator is not None
        assert ComplianceState is not None
        assert hasattr(ComplianceState, "GATE_ASSERT")
        assert hasattr(ComplianceState, "IDLE")
        assert hasattr(ComplianceState, "CODE_EDIT")

    def test_init_and_transition_execute_without_error(self):
        """The exact Step 4.5 init + transition code should execute cleanly."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        assert co is not None
        assert co.state_machine.state == ComplianceState.GATE_ASSERT, (
            f"Expected state GATE_ASSERT after transition, got {co.state_machine.state}"
        )

    def test_full_step45_as_single_block(self):
        """The entire Step 4.5 code block should execute without raising."""
        # Exact code from azazael-system-startup.md Step 4.5:
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        # Verify the comment in Step 4.5 is true
        assert hasattr(co, "check_rule_0x"), "check_rule_0x should be available"
        assert hasattr(co, "check_rule_0y"), "check_rule_0y should be available"
        assert hasattr(co, "check_all"), "check_all should be available"
        assert hasattr(co, "get_status"), "get_status should be available"
        assert hasattr(co, "reset"), "reset should be available"


# ── State machine transitions ──────────────────────────────────────


class TestStateTransitions:
    """Verify the state machine transitions correctly from Step 4.5 init."""

    def test_starts_pre_initialized_to_code_edit(self):
        """__init__ walks IDLE -> GATE_ASSERT -> CODE_EDIT so a fresh instance is ready to edit."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        assert co.state_machine.state == ComplianceState.CODE_EDIT, (
            f"Expected CODE_EDIT, got {co.state_machine.state}"
        )

    def test_idle_to_gate_assert_is_legal(self):
        """IDLE -> GATE_ASSERT should be a legal transition."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        result = co.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        assert result is True
        assert co.state_machine.state == ComplianceState.GATE_ASSERT

    def test_transition_count_increments(self):
        """Transition count should increment after transition_to."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        assert co.state_machine.transition_count == 2  # GATE_ASSERT + CODE_EDIT on __init__
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        assert co.state_machine.transition_count == 3

    def test_double_transition_to_gate_assert_raises_error(self):
        """Transitioning from GATE_ASSERT -> GATE_ASSERT should raise StateMachineError."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState, StateMachineError

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        with pytest.raises(StateMachineError):
            co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

    def test_gate_assert_to_code_edit_is_legal(self):
        """GATE_ASSERT -> CODE_EDIT should be a legal transition."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        co.state_machine.transition_to(ComplianceState.CODE_EDIT)
        assert co.state_machine.state == ComplianceState.CODE_EDIT

    def test_state_machine_summary_after_transition(self):
        """get_summary should reflect current state after transition."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        summary = co.state_machine.get_summary()

        assert summary["current_state"] == "GATE_ASSERT"
        assert summary["transitions"] == 3
        assert summary["history_length"] >= 1


# ── Component availability after init ──────────────────────────────


class TestComponentAvailability:
    """Verify all 13 components are accessible after Step 4.5 init."""

    def test_rule_0x_components_available(self):
        """All 8 RULE 0X components should be accessible after init."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        assert hasattr(co, "policy_engine")
        assert hasattr(co, "state_machine")
        assert hasattr(co, "verifier")
        assert hasattr(co, "schema_validator")
        assert hasattr(co, "scorer")
        assert hasattr(co, "ast_scanner")
        assert hasattr(co, "telemetry")

    def test_rule_0y_components_available(self):
        """All 5 RULE 0Y components should be accessible after init."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        assert hasattr(co, "judge")
        assert hasattr(co, "confidence_gate")
        assert hasattr(co, "graph_analyzer")
        assert hasattr(co, "dual_ledger")
        assert hasattr(co, "audit_pipeline")

    def test_check_rule_0x_returns_dict_with_8_components(self):
        """check_rule_0x should return a dict with 8 components."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        result = co.check_rule_0x(action="test", role="developer", context={"state": "GATE_ASSERT"})
        assert isinstance(result, dict)
        assert result["component_count"] == 8
        assert "all_passed" in result
        assert "components" in result
        # All 8 component keys should be present
        expected_keys = [
            "policy_gate", "broker_interception", "formal_verification",
            "state_machine", "schema_validation", "compliance_scoring",
            "ast_scanning", "telemetry",
        ]
        for key in expected_keys:
            assert key in result["components"], f"Missing component: {key}"

    def test_check_rule_0y_returns_dict_with_5_components(self):
        """check_rule_0y should return a dict with 5 components."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        result = co.check_rule_0y(action="test", role="developer", context={})
        assert isinstance(result, dict)
        assert result["component_count"] == 5
        assert "all_passed" in result
        assert "components" in result
        # All 5 component keys should be present
        expected_keys = [
            "judge_consensus", "confidence_gate", "graph_analysis",
            "dual_ledger", "audit_pipeline",
        ]
        for key in expected_keys:
            assert key in result["components"], f"Missing component: {key}"

    def test_check_all_returns_compliance_result(self):
        """check_all should return a ComplianceResult with both rule results."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator, ComplianceResult
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        co.state_machine.transition_to(ComplianceState.CODE_EDIT)

        result = co.check_all(
            action="test",
            role="developer",
            context={
                "state": "CODE_EDIT",
                "can_edit": True,
                "has_gate": True,
                "code_reviewed": True,
                "synced": True,
                "gate_decision": "PROCEED",
            },
        )

        assert isinstance(result, ComplianceResult)
        assert hasattr(result, "all_passed")
        assert hasattr(result, "rule_0x")
        assert hasattr(result, "rule_0y")
        assert hasattr(result, "score")
        assert hasattr(result, "summary")
        assert result.rule_0x["component_count"] == 8
        assert result.rule_0y["component_count"] == 5
        assert result.checked_at != ""

    def test_get_status_returns_dict(self):
        """get_status should return a dict with runtime information."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        status = co.get_status()
        assert isinstance(status, dict)
        assert "check_count" in status
        assert "state_machine" in status
        assert "score" in status
        assert "telemetry" in status
        assert "graph" in status
        assert "rules" in status


# ── Multiple orchestrator instances ────────────────────────────────


class TestMultipleInstances:
    """Verify that creating multiple orchestrator instances works cleanly."""

    def test_two_instances_are_independent(self):
        """Two ComplianceOrchestrator instances should have independent state."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co1 = ComplianceOrchestrator()
        co1.state_machine.transition_to(ComplianceState.GATE_ASSERT)

        co2 = ComplianceOrchestrator()
        # co2 is pre-initialized by __init__ as well
        assert co2.state_machine.state == ComplianceState.CODE_EDIT, (
            "Second orchestrator should also be pre-initialized to CODE_EDIT"
        )

    def test_second_instance_can_transition_independently(self):
        """A second orchestrator should be independently usable."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co1 = ComplianceOrchestrator()
        co1.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        co1.state_machine.transition_to(ComplianceState.CODE_EDIT)

        co2 = ComplianceOrchestrator()
        co2.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        co2.state_machine.transition_to(ComplianceState.CODE_EDIT)

        assert co1.state_machine.state == ComplianceState.CODE_EDIT
        assert co2.state_machine.state == ComplianceState.CODE_EDIT
        assert co1.state_machine.transition_count == 4  # 2 on init + 2 explicit
        assert co2.state_machine.transition_count == 4  # 2 on init + 2 explicit


# ── Per-component pass/fail checkers ───────────────────────────────


class TestComponentCheckers:
    """Verify _ox_component_passed and _oy_component_passed work correctly."""

    def test_ox_component_passed_policy_gate(self):
        """_ox_component_passed should check 'allowed' key for policy_gate."""
        from CORE.compliance.orchestrator import _ox_component_passed

        assert _ox_component_passed("policy_gate", {"allowed": True}) is True
        assert _ox_component_passed("policy_gate", {"allowed": False}) is False
        assert _ox_component_passed("policy_gate", {}) is False  # fail-closed

    def test_ox_component_passed_state_machine(self):
        """_ox_component_passed should check 'valid' key for state_machine."""
        from CORE.compliance.orchestrator import _ox_component_passed

        assert _ox_component_passed("state_machine", {"valid": True}) is True
        assert _ox_component_passed("state_machine", {"valid": False}) is False
        assert _ox_component_passed("state_machine", {}) is False  # fail-closed

    def test_ox_component_passed_compliance_scoring(self):
        """_ox_component_passed should check 'should_halt' is False for scoring."""
        from CORE.compliance.orchestrator import _ox_component_passed

        assert _ox_component_passed("compliance_scoring", {"should_halt": False}) is True
        assert _ox_component_passed("compliance_scoring", {"should_halt": True}) is False
        assert _ox_component_passed("compliance_scoring", {}) is False  # fail-closed: missing should_halt defaults to True → check fails

    def test_oy_component_passed_judge_consensus(self):
        """_oy_component_passed should check 'approved' key for judge_consensus."""
        from CORE.compliance.orchestrator import _oy_component_passed

        assert _oy_component_passed("judge_consensus", {"approved": True}) is True
        assert _oy_component_passed("judge_consensus", {"approved": False}) is False
        assert _oy_component_passed("judge_consensus", {}) is False  # fail-closed

    def test_oy_component_passed_graph_analysis(self):
        """_oy_component_passed should check anomalies_detected == 0."""
        from CORE.compliance.orchestrator import _oy_component_passed

        assert _oy_component_passed("graph_analysis", {"anomalies_detected": 0}) is True
        assert _oy_component_passed("graph_analysis", {"anomalies_detected": 2}) is False
        assert _oy_component_passed("graph_analysis", {}) is False  # missing key = default 1


# ── Edge cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    """Verify edge cases and error handling."""

    def test_init_with_explicit_path(self):
        """Explicit sys.path insert before init should work (as in Step 4.5)."""
        # Simulate the exact sys.path pattern from Step 4.5
        src_path = str(Path(__file__).resolve().parent.parent / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        assert co.state_machine.state == ComplianceState.GATE_ASSERT

    def test_reset_returns_to_idle(self):
        """reset() should return state machine to IDLE."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator
        from CORE.compliance.state_machine import ComplianceState

        co = ComplianceOrchestrator()
        co.state_machine.transition_to(ComplianceState.GATE_ASSERT)
        co.state_machine.transition_to(ComplianceState.CODE_EDIT)

        co.reset()
        assert co.state_machine.state == ComplianceState.IDLE

    def test_check_all_with_empty_context(self):
        """check_all should handle empty context gracefully (no crash)."""
        from CORE.compliance.orchestrator import ComplianceOrchestrator

        co = ComplianceOrchestrator()
        result = co.check_all(action="test", role="developer", context={})
        assert hasattr(result, "all_passed")
        assert isinstance(result.all_passed, bool)
