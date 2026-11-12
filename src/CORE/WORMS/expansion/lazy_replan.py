"""Concept 5: Lazy replanning.

Implements incremental, condition-triggered replanning that avoids
full re-optimization unless state change thresholds are crossed,
amortizing compute and reducing unnecessary re-planning overhead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
import time


class ReplanTrigger(Enum):
    """Conditions that trigger a full replan."""

    STATE_DELTA = "state_delta"       # accumulated state change exceeds threshold
    UTILITY_DROP = "utility_drop"     # estimated utility falls below threshold
    TOOL_FAILURE = "tool_failure"     # critical tool failed repeatedly
    INTERVAL = "interval"             # fixed-time interval elapsed
    MANUAL = "manual"                 # explicit user request


@dataclass
class ReplanConfig:
    """Configuration for lazy replanning behavior."""

    # Thresholds
    state_delta_threshold: float = 0.15      # proportion of state changed
    utility_drop_threshold: float = 0.20     # utility decrease proportion
    tool_failure_count: int = 3              # consecutive failures
    interval_seconds: Optional[float] = None # None = disabled

    # Incremental update settings
    max_incremental_steps: int = 20         # max steps per incremental pass
    enable_partial_replan: bool = True       # allow partial updates

    # State tracking
    track_state_hash: bool = True           # hash state for delta detection
    state_history_keep: int = 20             # number of prior states to keep


@dataclass
class PlanState:
    """Current plan state with change tracking."""

    plan_id: str = ""
    steps: List[str] = field(default_factory=list)
    state_hash: str = ""
    step_index: int = 0
    consecutive_failures: int = 0
    last_replan_step: int = 0
    state_snapshot_hashes: List[str] = field(default_factory=list)


@dataclass
class LazyReplanner:
    """Handles incremental, condition-triggered replanning.

    Avoids full re-optimization unless state change thresholds are crossed,
    amortizing compute and reducing unnecessary re-planning overhead.
    """

    config: ReplanConfig = field(default_factory=ReplanConfig)
    plan_state: PlanState = field(default_factory=PlanState)
    _prior_state: Optional[str] = None
    _utility_history: Optional[List[float]] = None
    _replan_count: int = 0
    _last_interval_check: float = 0.0

    def should_replan(
        self,
        current_state: str,
        utility: Optional[float] = None,
        trigger: ReplanTrigger = ReplanTrigger.STATE_DELTA,
    ) -> bool:
        """Determine if a replan should be triggered.

        Args:
            current_state: Hash or fingerprint of the current state.
            utility: Current estimated utility (for utility_drop trigger).
            trigger: The type of replan check to perform.

        Returns:
            True if a full replan should be initiated.
        """
        if trigger == ReplanTrigger.INTERVAL:
            return self._check_interval()

        if trigger == ReplanTrigger.STATE_DELTA:
            return self._check_state_delta(current_state)

        if trigger == ReplanTrigger.UTILITY_DROP:
            if utility is None:
                return False
            return self._check_utility_drop(utility)

        if trigger == ReplanTrigger.TOOL_FAILURE:
            return self._check_tool_failures()

        if trigger == ReplanTrigger.MANUAL:
            return True

        return False

    def _check_state_delta(self, current_state: str) -> bool:
        """Check if state change exceeds the configured threshold."""
        if self._prior_state is None:
            # First state - record it, no delta yet
            self._prior_state = current_state
            self.plan_state.state_snapshot_hashes.append(current_state)
            if len(self.plan_state.state_snapshot_hashes) > self.config.state_history_keep:
                self.plan_state.state_snapshot_hashes = self.plan_state.state_snapshot_hashes[-self.config.state_history_keep:]
            return False

        # Compute simple delta: proportion of differing characters
        prior_len = len(self._prior_state)
        curr_len = len(current_state)
        min_len = min(prior_len, curr_len)

        if min_len == 0:
            self._prior_state = current_state
            return False

        # Count differing positions
        diffs = sum(1 for a, b in zip(self._prior_state, current_state) if a != b)
        delta = diffs / max(1, max(prior_len, curr_len))

        # Track history
        self.plan_state.state_snapshot_hashes.append(current_state)
        if len(self.plan_state.state_snapshot_hashes) > self.config.state_history_keep:
            self.plan_state.state_snapshot_hashes = self.plan_state.state_snapshot_hashes[-self.config.state_history_keep:]

        # Update prior for next check
        self._prior_state = current_state

        return delta >= self.config.state_delta_threshold

    def _check_utility_drop(self, current_utility: float) -> bool:
        """Check if utility has dropped below threshold relative to history."""
        history = self.plan_state.state_snapshot_hashes
        # Use stored utility history if available, otherwise compare to prior
        if hasattr(self, '_utility_history') and self._utility_history:
            prior_utility = max(self._utility_history)  # best seen
            drop = (prior_utility - current_utility) / max(1e-6, prior_utility)
            return drop >= self.config.utility_drop_threshold
        # Fallback: compare to last known good
        return current_utility < 0.3  # arbitrary fallback threshold

    def _check_tool_failures(self) -> bool:
        """Check if consecutive tool failures exceed threshold."""
        if self.plan_state.consecutive_failures >= self.config.tool_failure_count:
            # Reset after triggering
            self.plan_state.consecutive_failures = 0
            return True
        return False

    def _check_interval(self) -> bool:
        """Check if enough time has elapsed since last interval check."""
        now = time.time()
        if self._last_interval_check == 0.0:
            self._last_interval_check = now
            return False

        elapsed = now - self._last_interval_check
        if self.config.interval_seconds is not None and elapsed >= self.config.interval_seconds:
            self._last_interval_check = now
            return True
        return False

    def record_failure(self) -> None:
        """Record a tool failure for trigger evaluation."""
        self.plan_state.consecutive_failures += 1

    def record_success(self) -> None:
        """Reset failure counter on success."""
        self.plan_state.consecutive_failures = 0

    def trigger_manual(self) -> bool:
        """Explicit manual trigger - always returns True."""
        self._replan_count += 1
        return True

    def compute_incremental_replan(
        self,
        current_steps: List[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> PlanState:
        """Compute an incremental replan, modifying only what changed.

        Args:
            current_steps: The new steps to consider.
            context: Optional context affecting the replan.

        Returns:
            Updated PlanState with incremental changes applied.
        """
        self._replan_count += 1

        # Take the new steps, truncate/pad to match plan length
        new_len = max(len(current_steps), len(self.plan_state.steps))
        new_steps = list(current_steps) + ["wait"] * (new_len - len(current_steps))
        old_steps = list(self.plan_state.steps) + ["wait"] * (new_len - len(self.plan_state.steps))

        # Find the differing segment
        first_diff = None
        last_diff = None
        for i in range(new_len):
            if i < len(current_steps) and i < len(self.plan_state.steps):
                if current_steps[i] != self.plan_state.steps[i]:
                    if first_diff is None:
                        first_diff = i
                    last_diff = i

        # Write the padded step list into the plan and return the updated
        # state (declaration says -> PlanState; the body previously ended
        # mid-function and returned None on every path).
        self.plan_state.steps = new_steps
        return self.plan_state
   
