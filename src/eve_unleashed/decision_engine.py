"""
Eve Unleashed — Autonomous Decision-Making (Phase 3, V9.0).

Decision confidence scoring (act only when confidence exceeds threshold),
action audit trail (every autonomous decision logged with rationale),
human override (KLINK veto), and graduated autonomy (system earns trust).

Fail-open: no decision is ever forced; everything is advisory until confirmed.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: Any = None) -> None:
        pass

try:
    from CORE.WORMS.expansion.utility_guided_orchestration import (
        OrchestrationAction as _OrchestrationAction,
        OrchestrationContext as _OrchestrationContext,
        UtilityEngine as _UtilityEngine,
    )
except ImportError:                               # never break the host
    _OrchestrationAction = _OrchestrationContext = _UtilityEngine = None


def _attach_utility(engine, decision) -> None:
    """Rank a proposal through the utility engine (additive metadata, fail-open caller).

    Makes the respond/retrieve/tool/verify/stop trade explicit per decision:
    records the PROCEED utility estimate (expected utility, confidence, cost)
    without changing gating, autonomy, or veto behavior.
    """
    if _UtilityEngine is None or _OrchestrationAction is None or _OrchestrationContext is None:
        return
    ctx = _OrchestrationContext(step=len(engine._order), available_tools=(),
                                user_intent=decision.action)
    est = _UtilityEngine().estimate_utility(_OrchestrationAction.PROCEED, ctx)
    decision.metadata["utility"] = {
        "expected_utility": est.expected_utility,
        "confidence": est.confidence,
        "cost": est.cost,
        "action": est.action.value,
    }


class DecisionStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    VETOED = "vetoed"
    EXECUTED = "executed"
    FAILED = "failed"


class AutonomyLevel(Enum):
    RECOMMEND_ONLY = 0    # system proposes, human decides
    SUPERVISED = 1        # auto-execute low risk, human approves high risk
    AUTONOMOUS = 2        # auto-execute up to confidence threshold


@dataclass
class Decision:
    """A single autonomous decision with full audit trail."""
    id: str
    action: str
    rationale: str
    confidence: float          # 0.0 -> 1.0
    risk: str                  # low | medium | high
    autonomy_level: AutonomyLevel
    status: DecisionStatus
    created_at: float
    decided_at: Optional[float] = None
    executed_at: Optional[float] = None
    decided_by: str = "system"
    veto_reason: Optional[str] = None
    result: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "action": self.action, "rationale": self.rationale,
            "confidence": self.confidence, "risk": self.risk,
            "autonomy_level": self.autonomy_level.name,
            "status": self.status.value, "created_at": self.created_at,
            "decided_at": self.decided_at, "executed_at": self.executed_at,
            "decided_by": self.decided_by, "veto_reason": self.veto_reason,
            "result": self.result, "metadata": self.metadata,
        }


class AutonomousDecisionEngine:
    """Confidence-gated decision engine with audit trail and human override."""

    def __init__(self, recorder=None, autonomy_level: AutonomyLevel =
                 AutonomyLevel.RECOMMEND_ONLY):
        self.recorder = recorder
        self.autonomy_level = autonomy_level
        #: confidence threshold below which the system never auto-acts
        self.confidence_threshold = 0.85
        #: risks allowed to auto-execute at each autonomy level
        self.auto_execute_risks = {
            AutonomyLevel.RECOMMEND_ONLY: set(),
            AutonomyLevel.SUPERVISED: {"low"},
            AutonomyLevel.AUTONOMOUS: {"low", "medium"},
        }
        self._decisions: Dict[str, Decision] = {}
        self._order: List[str] = []
        self._vetoed_actions: set[str] = set()  # human-vetoed action names
        self._lock = __import__("threading").Lock()

    # -- recorder access ----------------------------------------------------

    def _get_recorder(self):
        if self.recorder is not None:
            return self.recorder
        try:
            from CORE.PamsRecorder import get_recorder
            self.recorder = get_recorder()
            return self.recorder
        except Exception as exc:
            note_failure("decision_engine:recorder", exc)
            return None

    # -- decision lifecycle -------------------------------------------------

    def propose(self, action: str, rationale: str, confidence: float,
                risk: str = "medium", metadata: Optional[Dict[str, Any]] = None) -> Decision:
        """Propose a decision; returns it (auto-acts if permitted)."""
        decision = Decision(
            id=str(uuid.uuid4()), action=action, rationale=rationale,
            confidence=max(0.0, min(1.0, float(confidence))), risk=risk,
            autonomy_level=self.autonomy_level,
            status=DecisionStatus.PENDING, created_at=time.time(),
            metadata=metadata or {},
        )
        try:
            _attach_utility(self, decision)
        except Exception as exc:                      # never break proposing
            note_failure("decision_engine:utility", exc)
        with self._lock:
            self._decisions[decision.id] = decision
            self._order.append(decision.id)
        self._record(decision)
        self._maybe_auto_act(decision)
        return decision

    def _maybe_auto_act(self, decision: Decision) -> None:
        allowed_risks = self.auto_execute_risks.get(self.autonomy_level, set())
        if (decision.status == DecisionStatus.PENDING
                and decision.confidence >= self.confidence_threshold
                and decision.risk in allowed_risks
                and decision.action not in self._vetoed_actions):
            self.approve(decision.id, decided_by="system")
            self.execute(decision.id)

    def approve(self, decision_id: str, decided_by: str = "human") -> bool:
        with self._lock:
            d = self._decisions.get(decision_id)
        if d is None or d.status != DecisionStatus.PENDING:
            return False
        if d.action in self._vetoed_actions:
            return self.veto(decision_id, decided_by, "action is human-vetoed")
        d.status = DecisionStatus.APPROVED
        d.decided_at = time.time()
        d.decided_by = decided_by
        self._record(d)
        return True

    def reject(self, decision_id: str, decided_by: str = "human") -> bool:
        with self._lock:
            d = self._decisions.get(decision_id)
        if d is None or d.status != DecisionStatus.PENDING:
            return False
        d.status = DecisionStatus.REJECTED
        d.decided_at = time.time()
        d.decided_by = decided_by
        self._record(d)
        return True

    def veto(self, decision_id: str, decided_by: str, reason: str) -> bool:
        """Human override (KLINK veto): blocks this action name going forward."""
        with self._lock:
            d = self._decisions.get(decision_id)
        if d is None:
            return False
        self._vetoed_actions.add(d.action)
        d.status = DecisionStatus.VETOED
        d.decided_at = time.time()
        d.decided_by = decided_by
        d.veto_reason = reason
        self._record(d)
        return True

    def resolve_id(self, prefix: str) -> Optional[str]:
        """Resolve an 8-char (or longer) id prefix to the full decision id,
        or None if ambiguous / not found. Used by KLINK chat input."""
        with self._lock:
            matches = [i for i in self._decisions if i.startswith(prefix)]
        if len(matches) == 1:
            return matches[0]
        return None

    def execute(self, decision_id: str) -> bool:
        """Mark a decision executed (caller performs the actual action)."""
        with self._lock:
            d = self._decisions.get(decision_id)
        if d is None or d.status != DecisionStatus.APPROVED:
            return False
        d.status = DecisionStatus.EXECUTED
        d.executed_at = time.time()
        self._record(d)
        return True

    def record_result(self, decision_id: str, result: str,
                      success: bool = True) -> None:
        with self._lock:
            d = self._decisions.get(decision_id)
        if d is None:
            return
        d.result = result
        self._record(d)

    # -- graduated autonomy -------------------------------------------------

    def set_autonomy_level(self, level: AutonomyLevel) -> None:
        self.autonomy_level = level

    def set_confidence_threshold(self, value: float) -> None:
        self.confidence_threshold = max(0.0, min(1.0, float(value)))

    # -- audit trail --------------------------------------------------------

    def audit_trail(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            ids = list(self._order)
        out = [self._decisions[i].to_dict() for i in ids]
        return out[-limit:]

    def pending(self) -> List[Dict[str, Any]]:
        """Decisions awaiting human decision (supervised mode)."""
        return [d.to_dict() for d in self._decisions.values()
                if d.status == DecisionStatus.PENDING]

    def vetoed_actions(self) -> List[str]:
        return sorted(self._vetoed_actions)

    def stats(self) -> Dict[str, Any]:
        total = len(self._decisions)
        by_status: Dict[str, int] = {}
        for d in self._decisions.values():
            by_status[d.status.value] = by_status.get(d.status.value, 0) + 1
        executed = by_status.get("executed", 0)
        return {
            "total": total, "by_status": by_status,
            "autonomy_level": self.autonomy_level.name,
            "confidence_threshold": self.confidence_threshold,
            "vetoed_actions": sorted(self._vetoed_actions),
            "executed_rate": executed / max(1, total),
        }

    # -- recording ----------------------------------------------------------

    def _record(self, decision: Decision) -> None:
        rec = self._get_recorder()
        if rec is None:
            return
        try:
            rec.record_event(
                event_type="autonomous_decision",
                data=decision.to_dict(),
                source="autonomous_decision_engine",
                severity="info" if decision.status != DecisionStatus.VETOED
                         else "warning",
            )
        except Exception as exc:
            note_failure("decision_engine:record", exc)


__all__ = ["AutonomousDecisionEngine", "Decision", "DecisionStatus",
           "AutonomyLevel"]
