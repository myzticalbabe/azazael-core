"""Concept 4: Utility-guided orchestration.

Runs orchestration decisions guided by utility estimation, enabling
lazy evaluation, cost-aware scheduling, and early exit when expected
marginal utility falls below a configurable threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class OrchestrationAction(Enum):
    """Possible orchestration actions with associated utility signs."""

    PROCEED = "proceed"
    DEFER = "defer"
    SPLIT = "split"
    TERMINATE = "terminate"


@dataclass
class UtilityEstimate:
    """Estimated utility of taking a given action."""

    action: OrchestrationAction
    expected_utility: float
    confidence: float  # 0-1, how confident we are in the estimate
    cost: float  # estimated computational cost
    estimated_benefit: float  # expected benefit if action succeeds

    def should_execute(self, cost_threshold: float, utility_threshold: float) -> bool:
        """Check if this action should be executed given thresholds."""
        return (
            self.expected_utility >= utility_threshold
            and self.cost <= cost_threshold
            and self.confidence >= 0.5
        )


@dataclass
class OrchestrationContext:
    """Context passed between orchestration decision points."""

    step: int
    available_tools: Tuple[str, ...]
    constraints: Dict[str, Any] = field(default_factory=dict)
    history: List[str] = field(default_factory=list)
    user_intent: Optional[str] = None


@dataclass
class UtilityEngine:
    """Computes utility estimates for orchestration decisions."""

    default_cost: float = 1.0
    default_utility: float = 0.5
    utility_decay: float = 0.95
    min_utility_threshold: float = 0.3
    max_cost_threshold: float = 5.0

    def estimate_utility(
        self,
        action: OrchestrationAction,
        context: OrchestrationContext,
        **extra: Any,
    ) -> UtilityEstimate:
        """Estimate the utility of an action given context.

        Subclasses should override this with domain-specific logic.
        """
        # Default: utility decreases with step (diminishing returns)
        # and increases with available tools
        base_utility = self.default_utility
        confidence = 0.5 + 0.3 * min(1.0, len(context.available_tools) / 10.0)
        cost = self.default_cost * (self.utility_decay ** context.step)

        # Action-specific adjustments
        if action == OrchestrationAction.PROCEED:
            expected_utility = base_utility * confidence
        elif action == OrchestrationAction.DEFER:
            expected_utility = base_utility * 0.5 * confidence
        elif action == OrchestrationAction.SPLIT:
            expected_utility = base_utility * 0.8 * confidence
        else:  # TERMINATE
            expected_utility = 0.0

        return UtilityEstimate(
            action=action,
            expected_utility=max(0.0, min(1.0, expected_utility)),
            confidence=confidence,
            cost=cost,
            estimated_benefit=expected_utility * 2,  # benefit ≈ 2x utility
        )

    def decide(
        self,
        candidates: List[OrchestrationAction],
        context: OrchestrationContext,
        cost_threshold: Optional[float] = None,
        utility_threshold: Optional[float] = None,
    ) -> Tuple[Optional[OrchestrationAction], UtilityEstimate]:
        """Select the best action from candidates.

        Returns (chosen_action, estimate), chosen_action may be None
        if no action clears the thresholds.
        """
        cost_threshold = cost_threshold or self.max_cost_threshold
        utility_threshold = utility_threshold or self.min_utility_threshold

        best_estimate: Optional[UtilityEstimate] = None
        best_action: Optional[OrchestrationAction] = None

        for action in candidates:
            estimate = self.estimate_utility(action, context)
            if estimate.should_execute(cost_threshold, utility_threshold):
                if best_estimate is None or estimate.expected_utility > best_estimate.expected_utility:
                    best_estimate = estimate
                    best_action = action

        if best_estimate is None:
            # No action cleared thresholds - default to DEFER
            fallback = self.estimate_utility(OrchestrationAction.DEFER, context)
            return OrchestrationAction.DEFER, fallback

        return best_action, best_estimate


class UtilityGuidedOrchestrator:
    """Orchestrates tool/agent execution guided by utility estimates."""

    def __init__(self, utility_engine: UtilityEngine, cost_threshold: float = 5.0):
        self.utility_engine = utility_engine
        self.cost_threshold = cost_threshold
        self._step = 0
        self._history: List[Dict[str, Any]] = []

    def _advance_step(self) -> None:
        self._step += 1

    def orchestrate(
        self,
        available_actions: List[OrchestrationAction],
        context: OrchestrationContext,
    ) -> Tuple[Optional[OrchestrationAction], UtilityEstimate]:
        """Decide the next action given available actions and context.

        Returns (chosen_action, utility_estimate).
        """
        self._advance_step()
        context.step = self._step

        chosen, estimate = self.utility_engine.decide(
            available_actions,
            context,
            cost_threshold=self.cost_threshold,
        )

        # Record decision in history
        self._history.append(
            {
                "step": self._step,
                "available": [a.value for a in available_actions],
                "chosen": chosen.value if chosen else None,
                "estimate": {
                    "expected_utility": estimate.expected_utility,
                    "confidence": estimate.confidence,
                    "cost": estimate.cost,
                },
            }
        )

        return chosen, estimate

    def reset(self) -> None:
        """Reset orchestrator state."""
        self._step = 0
        self._history = []
