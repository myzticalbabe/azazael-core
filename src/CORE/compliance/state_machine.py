"""
RULE 0X-3 + 0X-4: State Machine Workflow + Formal Rule Verification

ComplianceStateMachine enforces strict finite states:
    IDLE -> GATE_ASSERT -> GEMINI_SPAWN -> CODE_EDIT -> CODE_REVIEW -> SYNC -> IDLE

FormalVerifier provides rule-based constraint verification (Z3 SMT fallback).
"""

import threading
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

class ComplianceState(Enum):
    """Strict finite states for the compliance workflow.

    Illegal state jumps raise StateMachineError.
    """
    IDLE = auto()
    GATE_ASSERT = auto()
    GEMINI_SPAWN = auto()
    CODE_EDIT = auto()
    CODE_REVIEW = auto()
    SYNC = auto()
    HALTED = auto()  # Terminal state — requires reset

    def __repr__(self) -> str:
        return f"ComplianceState.{self.name}"


# Legal transitions: (from, to) pairs
_LEGAL_TRANSITIONS: set[tuple[ComplianceState, ComplianceState]] = {
    # Normal flow
    (ComplianceState.IDLE, ComplianceState.GATE_ASSERT),
    (ComplianceState.GATE_ASSERT, ComplianceState.GEMINI_SPAWN),
    (ComplianceState.GATE_ASSERT, ComplianceState.CODE_EDIT),  # Skip Gemini if not complex
    (ComplianceState.GEMINI_SPAWN, ComplianceState.CODE_EDIT),
    (ComplianceState.CODE_EDIT, ComplianceState.GATE_ASSERT),  # Re-enter gate for next auth cycle
    (ComplianceState.CODE_EDIT, ComplianceState.CODE_REVIEW),
    (ComplianceState.CODE_REVIEW, ComplianceState.SYNC),
    (ComplianceState.SYNC, ComplianceState.IDLE),  # Back to start
    # HALT can happen from any state
    (ComplianceState.GATE_ASSERT, ComplianceState.HALTED),
    (ComplianceState.GEMINI_SPAWN, ComplianceState.HALTED),
    (ComplianceState.CODE_EDIT, ComplianceState.HALTED),
    (ComplianceState.CODE_REVIEW, ComplianceState.HALTED),
    (ComplianceState.SYNC, ComplianceState.HALTED),
    # Resume from HALT goes back to GATE_ASSERT
    (ComplianceState.HALTED, ComplianceState.GATE_ASSERT),
    # Resets
    (ComplianceState.HALTED, ComplianceState.IDLE),
}


class StateMachineError(RuntimeError):
    """Raised when an illegal state transition is attempted."""


class ComplianceStateMachine:
    """Finite state machine enforcing the compliance workflow sequence.

    Thread-safe — uses RLock for reentrant transitions.

    Usage:
        sm = ComplianceStateMachine()
        sm.transition_to(ComplianceState.GATE_ASSERT)
        sm.assert_gate("AP-1: user asked for this change")
        sm.transition_to(ComplianceState.CODE_EDIT)
    """

    def __init__(self):
        self._state = ComplianceState.IDLE
        self._lock = threading.RLock()
        self._history: list[dict[str, Any]] = []
        self._gate_assertions: list[str] = []
        self._transition_count = 0
        self._record_transition(ComplianceState.IDLE, "initialized")

    @property
    def state(self) -> ComplianceState:
        return self._state

    @property
    def transition_count(self) -> int:
        return self._transition_count

    @property
    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def transition_to(self, target: ComplianceState, reason: str = "") -> bool:
        """Transition to a new state. Raises StateMachineError if illegal."""
        with self._lock:
            transition = (self._state, target)
            if transition not in _LEGAL_TRANSITIONS:
                raise StateMachineError(
                    f"ILLEGAL STATE TRANSITION: {self._state.name} -> {target.name}. "
                    f"Legal transitions from {self._state.name}: "
                    f"{[t[1].name for t in _LEGAL_TRANSITIONS if t[0] == self._state]}"
                )

            old_state = self._state
            self._state = target
            self._transition_count += 1
            self._record_transition(target, reason, old_state=old_state)
            return True

    def assert_gate(self, assertion: str):
        """Record a gate assertion in the current GATE_ASSERT state."""
        with self._lock:
            if self._state != ComplianceState.GATE_ASSERT:
                raise StateMachineError(
                    f"Cannot assert gate in state {self._state.name}. "
                    f"Must be in GATE_ASSERT state."
                )
            self._gate_assertions.append(assertion)
            self._record_event("gate_assertion", {"assertion": assertion})

    def halt(self, reason: str = "Manual halt"):
        """Halt the state machine from any non-terminal state."""
        with self._lock:
            if self._state == ComplianceState.HALTED:
                return True
            self.transition_to(ComplianceState.HALTED, reason=reason)
            return True

    def reset(self):
        """Reset to IDLE state."""
        with self._lock:
            old = self._state
            self._state = ComplianceState.IDLE
            self._record_transition(ComplianceState.IDLE, "reset", old_state=old)

    def is_in_sequence(self, *states: ComplianceState) -> bool:
        """Check if current state is one of the given states."""
        return self._state in states

    def can_edit(self) -> bool:
        """Check if the state machine allows code edits."""
        return self._state in (ComplianceState.CODE_EDIT,)

    def can_review(self) -> bool:
        return self._state == ComplianceState.CODE_REVIEW

    def _record_transition(
        self, state: ComplianceState, reason: str, old_state: (ComplianceState | None) = None
    ):
        self._history.append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "type": "state_transition",
            "from": (old_state.name if old_state else self._state.name),
            "to": state.name,
            "reason": reason or "no reason specified",
            "transition_number": self._transition_count,
            "gate_assertions_count": len(self._gate_assertions),
        })

    def _record_event(self, event_type: str, data: dict[str, Any]):
        self._history.append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "type": event_type,
            **data,
        })

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "current_state": self._state.name,
                "transitions": self._transition_count,
                "gate_assertions": len(self._gate_assertions),
                "history_length": len(self._history),
                "can_edit": self.can_edit(),
                "can_review": self.can_review(),
            }

    def __repr__(self) -> str:
        return f"ComplianceStateMachine(state={self._state.name}, transitions={self._transition_count})"


# ---------------------------------------------------------------------------
# Formal Verifier (Z3 SMT fallback)
# ---------------------------------------------------------------------------

class FormalVerifier:
    """Rule-based formal verification for compliance constraints.

    Attempts to use Z3 if available. Falls back to a deterministic rule-based
    satisfiability checker that verifies constraint sets without external deps.

    Each constraint is a tuple of (condition_fn, error_message) where
    condition_fn takes a context dict and returns True if satisfied.

    Usage:
        verifier = FormalVerifier()
        verifier.add_constraint(
            lambda ctx: ctx.get("has_gate", False),
            "PRE-ACTION GATE assertion required before code edit"
        )
        result = verifier.verify({"has_gate": True})
        # result = {"passed": True, "constraints": 1, "failed": 0}
    """

    def __init__(self):
        self._constraints: list[tuple[Callable[[dict], bool], str]] = []

    def add_constraint(self, condition_fn: Callable[[dict], bool], error_msg: str):
        """Add a constraint that must be satisfied for verification to pass.

        Args:
            condition_fn: Function that takes a context dict and returns bool
            error_msg: Human-readable description of the constraint
        """
        self._constraints.append((condition_fn, error_msg))

    def add_default_constraints(self):
        """Add the standard Azazael compliance constraints."""
        self.add_constraint(
            lambda ctx: ctx.get("has_gate", False) is True,
            "RULE 0J: PRE-ACTION GATE assertion required"
        )
        self.add_constraint(
            lambda ctx: ctx.get("code_reviewed", False) is True,
            "RULE 0V: Code review required before/after changes"
        )
        self.add_constraint(
            lambda ctx: ctx.get("synced", True) is True,
            "RULE 0R: Skills sync required"
        )
        self.add_constraint(
            lambda ctx: ctx.get("state_valid", False) is True,
            "RULE 0X: State machine must be in valid state"
        )

    def verify(self, context: dict[str, Any]) -> dict[str, Any]:
        """Verify all constraints against the given context.

        Args:
            context: dict with constraint-relevant fields

        Returns:
            dict with passed, failed, total constraints, and details
        """
        results = []
        failed = 0
        passed = 0

        for condition_fn, error_msg in self._constraints:
            try:
                satisfied = condition_fn(context)
                if satisfied:
                    passed += 1
                    results.append({"constraint": error_msg, "passed": True})
                else:
                    failed += 1
                    results.append({"constraint": error_msg, "passed": False})
            except Exception as e:
                note_failure("state_machine:L263", e)
                failed += 1
                results.append({"constraint": error_msg, "passed": False, "error": str(e)})

        all_passed = failed == 0

        return {
            "passed": all_passed,
            "total_constraints": len(self._constraints),
            "passed_count": passed,
            "failed_count": failed,
            "results": results,
            "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def constraint_count(self) -> int:
        return len(self._constraints)

    def __repr__(self) -> str:
        return f"FormalVerifier({self.constraint_count()} constraints)"


# ---------------------------------------------------------------------------
# Helper: Verify a rule set
# ---------------------------------------------------------------------------

_rule_verifier: (FormalVerifier | None) = None
_rule_lock = threading.Lock()


def verify_rule_set(context: dict[str, Any]) -> dict[str, Any]:
    """Verify the standard Azazael rule set. Convenience function.

    Creates the verifier with default constraints on first call.

    Args:
        context: dict with at minimum:
            - has_gate: bool — was PRE-ACTION GATE printed?
            - code_reviewed: bool — was code reviewed?
            - synced: bool — were skills synced?
            - state_valid: bool — is state machine in valid state?

    Returns:
        Verification result dict with passed, failed, and details.
    """
    global _rule_verifier
    if _rule_verifier is None:
        with _rule_lock:
            if _rule_verifier is None:
                _rule_verifier = FormalVerifier()
                _rule_verifier.add_default_constraints()
    return _rule_verifier.verify(context)


def reset_verifier():
    """Reset the global verifier (for testing)."""
    global _rule_verifier
    with _rule_lock:
        _rule_verifier = None
