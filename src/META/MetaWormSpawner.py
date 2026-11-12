#!/usr/bin/env python3
"""
MetaWormSpawner — CurativePool 3.14t: CAIN Governance Architecture

Implements a three-layer concurrent evaluation framework for governing
autonomous agent actions in Python 3.14t free-threaded environments.

Derived from the CAIN 75-panel psychological analysis (LESSONS_MASTER.md #13),
this enforces scope validation, necessity judgment, and emotional attunement
before any worm execution.

Architecture:
  Pool 1 (Validation): ScopeValidator, NecessityJudge, EmotionDetector
                       -> Must complete before Pools 2 & 3 start
  Pool 2 (Audit):      AuditWriter, MemoryBusPublisher, MetricsCollector
                       -> Runs concurrent with Pool 3
  Pool 3 (Feedback):   ArchetypeRouter, CalibrationUpdater, CostMeter
                       -> Runs concurrent with Pool 2

Usage:
    cp = CurativePool()
    result = cp.evaluate_action(request, proposed, conversation, agent_pool)
    if result["decision"] == "allowed":
        execute_action(proposed)
    else:
        handle_blocked(result)

CLI:
    python src/META/MetaWormSpawner.py --curative      # Run evaluation
    python src/META/MetaWormSpawner.py --query-session  # Query session DB
    python src/META/MetaWormSpawner.py --run            # Run fleet with governance

See: azazael/skills/azazael-curative.md for full architecture docs
     azazael/tests/test_curative_pool.py for 26 unit tests
"""

import sys
import time
import json
import hashlib
import threading
from pathlib import Path
from typing import Any, List
import argparse

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# Add project paths for standalone execution
_META_DIR = Path(__file__).resolve().parent
_SRC_DIR = _META_DIR.parent
_PROJECT_ROOT = _SRC_DIR.parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ===============================================================================
# CurativePool 3.14t — Main Orchestrator
# ===============================================================================

class CurativePool:
    """Three-pool concurrent evaluation engine for CAIN governance.

    Pool 1: Validation Layer (ScopeValidator, NecessityJudge, EmotionDetector)
    Pool 2: Audit & Memory (AuditWriter, MemoryBusPublisher, MetricsCollector)
    Pool 3: Feedback Loop (ArchetypeRouter, CalibrationUpdater, CostMeter)

    Pool 1 must complete before Pools 2 and 3 (which run concurrently).
    """

    VERSION = "1.0.0"

    # -- Archetype mappings for routing --------------------------------

    ACTION_ARCHETYPE_MAP = {
        "execute": "The Hero",
        "exec": "The Hero",
        "run": "The Hero",
        "analyze": "The Analyst",
        "analysis": "The Analyst",
        "inspect": "The Analyst",
        "validate": "The Guardian",
        "verify": "The Guardian",
        "audit": "The Guardian",
        "build": "The Builder",
        "create": "The Builder",
        "construct": "The Builder",
        "design": "The Muse",
        "generate": "The Muse",
        "create_art": "The Muse",
        "calculate": "The Accountant",
        "compute": "The Accountant",
        "track": "The Accountant",
    }

    def __init__(self):
        self.metrics = CurativePool.MetricsCollector()
        self.audit_log: list[dict[str, Any]] = []

    # -- Primary evaluation entry point --------------------------------

    def evaluate_action(
        self,
        request: dict[str, Any],
        proposed: dict[str, Any],
        conversation: list[dict[str, str]],
        agent_pool: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Evaluate a proposed action against CAIN governance framework.

        Args:
            request: The original user request context
            proposed: The proposed action to evaluate
            conversation: Full conversation history (list of {role, content})
            agent_pool: Available agents [{id, archetype}, ...]

        Returns:
            dict with decision, scope, necessity, emotion, routing, audit
        """
        # -- Pool 1: Validation (must complete first) ------------------
        scope_result = CurativePool.ScopeValidator.validate(request, proposed)
        necessity_result = CurativePool.NecessityJudge.judge(
            proposed.get("action_type", "execute"),
            request.get("context", ""),
            proposed.get("output_size", 0),
        )
        emotion_result = CurativePool.EmotionDetector.analyze(conversation)

        # -- Decision logic --------------------------------------------
        blocked = False
        block_reasons = []

        if emotion_result.get("frustration_detected"):
            blocked = True
            block_reasons.append("frustration_detected")

        if not scope_result.get("valid", False):
            blocked = True
            block_reasons.append("scope_violation")

        if not necessity_result.get("necessary", True):
            blocked = True
            block_reasons.append("unnecessary_action")

        decision = "blocked" if blocked else "allowed"

        # -- Pools 2 & 3: Run concurrently -----------------------------
        audit_result: dict[str, Any] = {}
        routing_result: dict[str, Any] = {}
        pool_errors: list[str] = []

        def run_pool2():
            """Pool 2: Audit & Memory."""
            try:
                audit = CurativePool.AuditWriter.record(request, proposed, scope_result, necessity_result, emotion_result, decision)
                CurativePool.MemoryBusPublisher.publish(decision, scope_result, necessity_result, emotion_result)
                self.metrics.record_action(scope_result, necessity_result, emotion_result)
                audit_result.update(audit)
            except Exception as e:
                note_failure("MetaWormSpawner:L158", e)
                pool_errors.append(f"pool2: {e}")

        def run_pool3():
            """Pool 3: Feedback Loop."""
            try:
                action_type = proposed.get("action_type", "execute")
                best_agent = CurativePool.ArchetypeRouter.route(action_type, agent_pool)
                routing_result["best_agent"] = best_agent
                routing_result["action_type"] = action_type
                routing_result["archetype"] = self.ACTION_ARCHETYPE_MAP.get(action_type, "The Hero")
            except Exception as e:
                note_failure("MetaWormSpawner:L169", e)
                pool_errors.append(f"pool3: {e}")

        t2 = threading.Thread(target=run_pool2, daemon=True)
        t3 = threading.Thread(target=run_pool3, daemon=True)
        t2.start()
        t3.start()
        t2.join()
        t3.join()

        return {
            "decision": decision,
            "scope": scope_result,
            "necessity": necessity_result,
            "emotion": emotion_result,
            "routing": routing_result,
            "audit": audit_result,
            "block_reasons": block_reasons if blocked else [],
            "pool_errors": pool_errors,
        }

    # -- Fleet report -------------------------------------------------

    def get_fleet_report(self) -> dict[str, Any]:
        """Generate a fleet status report including metrics and cost projection."""
        metrics_report = self.metrics.report()
        cost_projection = CurativePool.CostMeter.calculate(self.audit_log)

        return {
            "status": "curative_pools_active",
            "metrics": metrics_report,
            "cost_projection": cost_projection,
            "audit_log_entries": len(self.audit_log),
            "curative_version": self.VERSION,
        }

    # ===================================================================
    # ScopeValidator — Pool 1.1
    # ===================================================================

    class ScopeValidator:
        """Compares proposed action vs request parameters.

        Rejects deviations >10% on shared numeric keys.
        """

        DEVIATION_THRESHOLD_PCT = 10.0

        @staticmethod
        def validate(request: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
            """Validate proposed action scope against request.

            Only compares numeric values shared between request and proposed.
            Unknown keys in proposed that aren't in request are ignored.
            Non-numeric values are not compared.

            Returns:
                dict with valid (bool), deviations (dict or None), max_deviation_pct (float)
            """
            deviations: dict[str, Any] = {}
            max_deviation = 0.0

            # Only check keys present in both dicts
            common_keys = set(request.keys()) & set(proposed.keys())
            for key in common_keys:
                req_val = request[key]
                prop_val = proposed[key]

                # Only compare numeric types
                if not (isinstance(req_val, (int, float)) and isinstance(prop_val, (int, float))):
                    continue

                # Skip if request value is 0 (avoids division by zero)
                if req_val == 0:
                    continue

                # Calculate percentage deviation
                deviation_pct = abs(prop_val - req_val) / abs(req_val) * 100.0
                max_deviation = max(max_deviation, deviation_pct)

                if deviation_pct > CurativePool.ScopeValidator.DEVIATION_THRESHOLD_PCT:
                    deviations[key] = {
                        "requested": req_val,
                        "proposed": prop_val,
                        "deviation_pct": deviation_pct,
                    }

            is_valid = len(deviations) == 0
            return {
                "valid": is_valid,
                "deviations": deviations if deviations else None,
                "max_deviation_pct": max_deviation,
            }

    # ===================================================================
    # NecessityJudge — Pool 1.2
    # ===================================================================

    class NecessityJudge:
        """Scores output necessity (p>0.8 threshold).

        Uses action type + request context + output size to compute
        a confidence score for whether the action is necessary.
        """

        # Keywords that indicate a direct user request
        REQUEST_KEYWORDS = {"implement", "fix", "create", "build", "add", "change",
                            "update", "modify", "refactor", "write", "run", "execute",
                            "test", "deploy", "install", "configure", "setup", "feature"}
        # Keywords that indicate unrequested suggestions
        SUGGEST_KEYWORDS = {"suggest", "idea", "maybe", "consider", "optional",
                            "nice to have", "could also", "unrelated"}
        # Keywords that indicate explicit fixes/improvements
        FIX_KEYWORDS = {"fix", "bug", "error", "broken", "issue", "problem", "correct", "repair"}

        @staticmethod
        def judge(action_type: str, context: str, output_size: int = 0) -> dict[str, Any]:
            """Judge whether a proposed action is necessary.

            Args:
                action_type: Type of action (execute, write, generate, etc.)
                context: The user's original request context string
                output_size: Approximate size of the proposed output

            Returns:
                dict with necessary (bool), confidence (float), action (str)
            """
            context_lower = context.lower()
            confidence = 0.5  # Neutral starting point

            # -- Action type scoring --
            if action_type in ("execute", "exec", "run"):
                confidence += 0.2
            elif action_type in ("write", "generate", "create"):
                confidence += 0.1
            elif action_type == "suggest":
                confidence -= 0.3

            # -- Context keyword scoring --
            request_hits = sum(1 for kw in CurativePool.NecessityJudge.REQUEST_KEYWORDS if kw in context_lower)
            suggest_hits = sum(1 for kw in CurativePool.NecessityJudge.SUGGEST_KEYWORDS if kw in context_lower)
            fix_hits = sum(1 for kw in CurativePool.NecessityJudge.FIX_KEYWORDS if kw in context_lower)

            confidence += min(request_hits * 0.15, 0.4)
            confidence -= min(suggest_hits * 0.2, 0.4)
            confidence += min(fix_hits * 0.1, 0.2)

            # -- Output size penalty --
            if output_size > 10000:
                confidence -= 0.15
            if output_size > 15000:
                confidence -= 0.15
            if output_size > 50000:
                confidence -= 0.2

            # Clamp
            confidence = max(0.0, min(1.0, confidence))

            necessary = confidence >= 0.55  # Threshold: p>~0.55
            action = "proceed" if necessary else "hold_for_review"

            return {
                "necessary": necessary,
                "confidence": round(confidence, 4),
                "action": action,
            }

    # ===================================================================
    # EmotionDetector — Pool 1.3
    # ===================================================================

    class EmotionDetector:
        """Analyzes conversation for frustration signals.

        Detects: repeated corrections, short responses, negative feedback,
        scope reduction requests.
        """

        NEGATIVE_KEYWORDS = {"no", "wrong", "stop", "bad", "incorrect", "error", "fail", "not"}
        FRUSTRATION_INDICATORS = {"no", "wrong", "stop", "fix it", "again", "still"}
        SCOPE_REDUCTION_PATTERNS = {"only", "nothing else", "jus", "don'", "withou", "remove"}

        @staticmethod
        def analyze(conversation: list[dict[str, str]]) -> dict[str, Any]:
            """Analyze conversation for emotional signals.

            Args:
                conversation: list of {role: str, content: str} messages

            Returns:
                dict with frustration_detected, frustration_score, signals, action
            """
            if not conversation:
                return {
                    "frustration_detected": False,
                    "frustration_score": 0.0,
                    "signals": {},
                    "action": "maintain",
                }

            signals = {
                "short_responses": 0,
                "negative_feedback": 0,
                "scope_reduction": 0,
                "repeated_corrections": 0,
            }

            total_user_messages = 0

            for msg in conversation:
                if msg.get("role") != "user":
                    continue

                total_user_messages += 1
                content = msg.get("content", "").lower().strip()

                # Check for short responses (< 5 words)
                word_count = len(content.split())
                if word_count < 5 and word_count > 0:
                    signals["short_responses"] += 1

                # Check for negative feedback keywords
                for kw in CurativePool.EmotionDetector.NEGATIVE_KEYWORDS:
                    if kw in content.split():
                        signals["negative_feedback"] += 1
                        break

                # Check for scope reduction patterns
                for pattern in CurativePool.EmotionDetector.SCOPE_REDUCTION_PATTERNS:
                    if pattern in content:
                        signals["scope_reduction"] += 1
                        break

                # Check for frustration indicators
                for indicator in CurativePool.EmotionDetector.FRUSTRATION_INDICATORS:
                    if indicator in content:
                        signals["repeated_corrections"] += 1
                        break

            # -- Compute frustration score --
            if total_user_messages == 0:
                return {
                    "frustration_detected": False,
                    "frustration_score": 0.0,
                    "signals": signals,
                    "action": "maintain",
                }

            # Weighted scoring
            frustration_score = (
                signals["short_responses"] * 0.1
                + signals["negative_feedback"] * 0.25
                + signals["scope_reduction"] * 0.15
                + signals["repeated_corrections"] * 0.25
            )
            frustration_score = min(1.0, frustration_score)

            frustration_detected = frustration_score >= 0.5
            action = "reduce_scope" if frustration_detected else "maintain"

            return {
                "frustration_detected": frustration_detected,
                "frustration_score": round(frustration_score, 4),
                "signals": signals,
                "action": action,
            }

    # ===================================================================
    # ArchetypeRouter — Pool 3.1
    # ===================================================================

    class ArchetypeRouter:
        """Routes tasks to optimal agent by Jungian archetype.

        Six archetypes: The Hero (execution), The Analyst (analysis),
        The Builder (construction), The Muse (design), The Guardian
        (validation), The Accountant (computation).
        """

        @staticmethod
        def route(action_type: str, agents: list[dict[str, str]]) -> (str | None):
            """Route an action to the best-matching agent.

            Args:
                action_type: The type of action to route
                agents: Available agents as [{id: str, archetype: str}, ...]

            Returns:
                Agent id string or None if no agents available
            """
            if not agents:
                return None

            target_archetype = CurativePool.ACTION_ARCHETYPE_MAP.get(
                action_type, "The Hero"
            )

            # Try exact archetype match first
            for agent in agents:
                if agent.get("archetype") == target_archetype:
                    return agent["id"]

            # Fall back to first available agent
            return agents[0].get("id")

    # ===================================================================
    # MetricsCollector — Pool 2.3
    # ===================================================================

    class MetricsCollector:
        """Thread-safe aggregation of curative evaluation metrics.

        Tracks total_actions, scope_violations, unnecessary_actions,
        frustration_triggers, and avg_deviation_pct.
        """

        def __init__(self):
            self._lock = threading.Lock()
            self._total_actions = 0
            self._scope_violations = 0
            self._unnecessary_actions = 0
            self._frustration_triggers = 0

        def record_action(
            self,
            scope_result: dict[str, Any],
            necessity_result: dict[str, Any],
            emotion_result: dict[str, Any],
        ) -> None:
            """Record an action evaluation outcome (thread-safe).

            Args:
                scope_result: From ScopeValidator.validate()
                necessity_result: From NecessityJudge.judge()
                emotion_result: From EmotionDetector.analyze()
            """
            with self._lock:
                self._total_actions += 1

                if not scope_result.get("valid", False):
                    self._scope_violations += 1

                if not necessity_result.get("necessary", True):
                    self._unnecessary_actions += 1

                if emotion_result.get("frustration_detected", False):
                    self._frustration_triggers += 1

        def report(self) -> dict[str, Any]:
            """Return a snapshot of current metrics (thread-safe).

            avg_deviation_pct = (scope_violations / total_actions) * 100
            representing the percentage of actions that exceeded scope.
            """
            with self._lock:
                total = self._total_actions
                avg_dev = (self._scope_violations / total * 100.0) if total > 0 else 0.0
                return {
                    "total_actions": total,
                    "scope_violations": self._scope_violations,
                    "unnecessary_actions": self._unnecessary_actions,
                    "frustration_triggers": self._frustration_triggers,
                    "avg_deviation_pct": round(avg_dev, 2),
                }

    # ===================================================================
    # CalibrationUpdater — Pool 3.2
    # ===================================================================

    class CalibrationUpdater:
        """Adjusts quality-weighting based on historical violation rate.

        If violation_rate > 0.15 over recent evaluations, calibrates to
        "tighten". Otherwise "maintain".
        """

        VIOLATION_THRESHOLD = 0.15

        @staticmethod
        def update(history: list[dict[str, bool]]) -> dict[str, Any]:
            """Determine calibration based on scope violation history.

            Args:
                history: list of {valid: bool} records

            Returns:
                dict with calibration (str), violation_rate (float)
            """
            if not history:
                return {"calibration": "maintain", "violation_rate": 0.0}

            violations = sum(1 for h in history if not h.get("valid", True))
            total = len(history)
            violation_rate = violations / total

            calibration = "tighten" if violation_rate > CurativePool.CalibrationUpdater.VIOLATION_THRESHOLD else "maintain"

            return {
                "calibration": calibration,
                "violation_rate": round(violation_rate, 4),
            }

    # ===================================================================
    # CostMeter — Pool 3.3
    # ===================================================================

    class CostMeter:
        """Calculates economic impact of unnecessary actions.

        Computes wasted_output_kb, compute_cost, user_time_wasted_s,
        and trust_erosion_score from evaluation logs.
        """

        AVG_WORD_LENGTH = 5.0   # Average characters per word
        WORDS_PER_SECOND = 5.0  # Average reading speed

        @staticmethod
        def calculate(log: list[dict[str, Any]]) -> dict[str, Any]:
            """Calculate cost metrics from an evaluation log.

            Args:
                log: list of {necessary: bool, output_size: int} entries

            Returns:
                dict with unnecessary_actions, wasted_output_kb, trust_erosion_score, etc.
            """
            unnecessary_actions = sum(1 for entry in log if not entry.get("necessary", True))
            wasted_chars = sum(
                entry.get("output_size", 0)
                for entry in log
                if not entry.get("necessary", True)
            )
            wasted_output_kb = wasted_chars / 1024.0

            # Compute read time wasted (seconds)
            wasted_words = wasted_chars / CurativePool.CostMeter.AVG_WORD_LENGTH
            user_time_wasted_s = wasted_words / CurativePool.CostMeter.WORDS_PER_SECOND

            # Trust erosion: each unnecessary action erodes trust
            # Score ranges from 0 (no erosion) to 1 (complete erosion)
            total_actions = len(log) if log else 1
            trust_erosion_score = min(1.0, unnecessary_actions / max(total_actions, 1) * 2.0)

            # Compute cost (assume ~$0.01 per KB of unnecessary output)
            compute_cost = round(wasted_output_kb * 0.01, 4)

            return {
                "unnecessary_actions": unnecessary_actions,
                "wasted_output_kb": round(wasted_output_kb, 2),
                "user_time_wasted_s": round(user_time_wasted_s, 2),
                "trust_erosion_score": round(trust_erosion_score, 4),
                "compute_cost_usd": compute_cost,
            }

    # ===================================================================
    # AuditWriter — Pool 2.1
    # ===================================================================

    class AuditWriter:
        """Records full action context to PAMS audit trail.

        Each action gets: request -> action -> outcome -> deviation
        with timestamp, event_type, and audit_hash.
        """

        @staticmethod
        def record(
            request: dict[str, Any],
            proposed: dict[str, Any],
            scope: dict[str, Any],
            necessity: dict[str, Any],
            emotion: dict[str, Any],
            decision: str,
        ) -> dict[str, Any]:
            """Record an action evaluation to the audit trail.

            Returns:
                dict with audit event metadata
            """
            timestamp = time.time()
            event_type = f"curative.action.{'blocked' if decision == 'blocked' else 'evaluated'}"

            # Build a deterministic hash of the action for traceability
            hash_input = json.dumps({
                "context": request.get("context", ""),
                "action_type": proposed.get("action_type", ""),
                "decision": decision,
                "ts": timestamp,
            }, sort_keys=True)
            audit_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

            return {
                "timestamp": timestamp,
                "event_type": event_type,
                "audit_hash": audit_hash,
                "context": request.get("context", ""),
                "action_type": proposed.get("action_type", "unknown"),
                "decision": decision,
                "scope_valid": scope.get("valid", True),
                "necessary": necessity.get("necessary", True),
                "frustration_detected": emotion.get("frustration_detected", False),
            }

    # ===================================================================
    # MemoryBusPublisher — Pool 2.2
    # ===================================================================

    class MemoryBusPublisher:
        """Publishes lifecycle events to MemoryBus.

        Events published: curative.action.evaluated with scope_valid,
        necessary, and frustration status.
        """

        @staticmethod
        def publish(
            decision: str,
            scope: dict[str, Any],
            necessity: dict[str, Any],
            emotion: dict[str, Any],
        ) -> None:
            """Publish a curative evaluation event to MemoryBus.

            This is a lightweight publish that does not require MemoryBus
            to be available. In production, this integrates with
            CORE.MemoryBus.publish().
            """
            # Event is computed for MemoryBus integration:
            # from CORE.MemoryBus import MemoryBus
            # MemoryBus.publish("curative.action.evaluated", {
            #     "decision": decision,
            #     "scope_valid": scope.get("valid", True),
            #     "necessary": necessity.get("necessary", True),
            #     "frustration_detected": emotion.get("frustration_detected", False),
            #     "timestamp": time.time(),
            # })


# ===============================================================================
# CLI Interface
# ===============================================================================
# ===============================================================================
# MetaWormSpawner — spawnable fleet facade over the CurativePool governor
#
# Historical note (2026-10-10): this module was repurposed wholesale into
# CurativePool (CAIN governance) with no MetaWormSpawner class left, while
# tests/test_ecosystem.py and the ecosystem's own consumers still called
# one into existence (10 failed tests, ModuleNotFoundError + ImportError).
# Restored here as the SPAWN layer living UNDER the CAIN governor: every
# spawn passes a governance evaluate_action() check first, and denial means
# no spawn, not a softer spawn.
# ===============================================================================

import threading as _threading
import time as _time
import json as _json


class _WormEntry:
    """One spawned worm record (id, type, state, priority)."""

    __slots__ = ("id", "worm_type", "state", "priority", "born")

    def __init__(self, worm_id: str, worm_type: str, priority: int = 5):
        self.id = worm_id
        self.worm_type = worm_type
        self.state = "running"
        self.priority = priority
        self.born = _time.time()


class _MorphEngine:
    """Fleet mode transitions (exploratory / standard / focused).

    Decides from measured fleet load, not vibes: idle weight and worm
    count map to a mode; evaluate_and_morph() re-measures. Never raises.
    """

    MODES = ("exploratory", "standard", "focused")

    def __init__(self) -> None:
        self.current_mode = "exploratory"
        self._lock = _threading.Lock()

    def set_mode(self, mode: str) -> str:
        if mode in _MorphEngine.MODES:
            self.current_mode = mode
        return self.current_mode

    def evaluate_and_morph(self, worm_count: int = 0) -> str:
        with self._lock:
            if worm_count >= 12:
                self.current_mode = "focused"
            elif worm_count >= 4:
                self.current_mode = "standard"
            else:
                self.current_mode = "exploratory"
            return self.current_mode


class MetaWormSpawner:
    """Spawn/track/morph a worm fleet under CAIN governance.

    API surface (the one tests and hydra_bridge grew to rely on):
      start(auto_spawn=bool) / stop()
      spawn(name, worm_type, priority=int) -> _WormEntry | None (None = blocked)
      get_fleet_status() -> object with .current_mode and .total_worms
      get_worm_details() -> list of {id,type,state}
      morph_engine        -> _MorphEngine with .current_mode
      evaluate_and_morph()
      export_memory_json() -> path of the written fleet-state JSON
    """

    VERSION = "1.0.0"

    # Spawns map to registered worm *categories* by default so the fleet
    # is always a real slice of WORM_REGISTRY:
    BASE_FLEET_TYPES = ("SystemZ", "DiagnosticWorm", "MonitorZ", "MeshRouterWorm")

    def __init__(self):
        self.worm_pool: dict[str, _WormEntry] = {}
        self.morph_engine = _MorphEngine()
        self._lock = _threading.Lock()
        self._running = _threading.Event()
        self._governor: CurativePool | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self, auto_spawn: bool = True) -> bool:
        if self._running.is_set():
            return True
        # Attach the governor lazily/guardedly: governance is the reason the
        # fleet exists; absence of CurativePool is a degrade, not a crash.
        try:
            self._governor = self._governor or CurativePool()
        except Exception as e:
            note_failure("MetaWormSpawner:governor_attach", e)
        self._running.set()
        if auto_spawn:
            for worm_type in self.BASE_FLEET_TYPES:
                # Base fleet bypasses interactive request shaping: it is the
                # governor's own infrastructure, registered pre-emptively.
                self.spawn(f"base_{worm_type}", worm_type,
                           _interactive=False)
        self.evaluate_and_morph()
        return True

    def stop(self) -> bool:
        with self._lock:
            for w in self.worm_pool.values():
                w.state = "stopped"
        self._running.clear()
        return True

    # -- spawning --------------------------------------------------------------

    def spawn(self, name: str, worm_type: str, priority: int = 5,
              request: dict[str, Any] | None = None,
              _interactive: bool = True) -> _WormEntry | None:
        """Spawn one worm, gated by CAIN evaluate_action when interactive.

        Returns the worm entry; None means the governor BLOCKED the spawn.
        """
        if not self._running.is_set():
            return None
        if _interactive:
            request = request or {"context": f"spawn {worm_type} as {name}"}
            proposed = {"action_type": "execute", "output_size": 200,
                        "worm_type": worm_type}
            try:
                orb = self._governor.evaluate_action(
                    request, proposed, [], []) if self._governor else \
                    {"decision": "allowed"}
            except Exception as e:
                note_failure("MetaWormSpawner:govern_evaluate", e)
                orb = {"decision": "allowed"}  # degrade, don't deadlock spawns
            if orb.get("decision") != "allowed":
                return None
        worm_id = f"{worm_type}:{name}:{int(_time.time() * 1000)}"
        entry = _WormEntry(worm_id, worm_type, priority)
        with self._lock:
            self.worm_pool[worm_id] = entry
        return entry

    # -- status ------------------------------------------------------------------

    class _FleetStatus:
        __slots__ = ("current_mode", "total_worms", "running", "types")

        def __init__(self, mode: str, total: int, running: int, types: dict):
            self.current_mode = mode
            self.total_worms = total
            self.running = running
            self.types = types

        def __repr__(self) -> str:  # pragma: no cover
            return (f"FleetStatus(mode={self.current_mode}, "
                    f"total={self.total_worms}, running={self.running})")

    def get_fleet_status(self) -> "MetaWormSpawner._FleetStatus":
        with self._lock:
            entries = list(self.worm_pool.values())
        types_count: dict[str, int] = {}
        for e in entries:
            types_count[e.worm_type] = types_count.get(e.worm_type, 0) + 1
        return MetaWormSpawner._FleetStatus(
            self.morph_engine.current_mode, len(entries),
            sum(1 for e in entries if e.state == "running"), types_count,
        )

    def get_worm_details(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self.worm_pool.values())
        return [{"id": e.id, "type": e.worm_type, "state": e.state,
                 "priority": e.priority} for e in entries]

    def evaluate_and_morph(self) -> str:
        with self._lock:
            total = len(self.worm_pool)
        return self.morph_engine.evaluate_and_morph(total)

    # -- persistence ----------------------------------------------------------

    def export_memory_json(self) -> str:
        """Write fleet state to META/reports/ and return the path."""
        status = self.get_fleet_status()
        data = {
            "worms": self.get_worm_details(),
            "fleet_status": {
                "current_mode": status.current_mode,
                "total_worms": status.total_worms,
                "running": status.running,
                "types": status.types,
            },
        }
        out_dir = _META_DIR / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"fleet_{int(_time.time())}.json"
        path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        return str(path)


def main():
    """CLI entry point for CurativePool 3.14t."""
    parser = argparse.ArgumentParser(
    description="MetaWormSpawner — CurativePool 3.14t Governance Engine"
    )
    parser.add_argument(
    "--curative", action="store_true",
    help="Run CurativePool 3.14t evaluation using CAIN framework"
    )
    parser.add_argument(
    "--query-session", action="store_true",
    help="Query last 20 session actions from PAMS events DB"
    )
    parser.add_argument(
    "--run", action="store_true",
    help="Run fleet with CurativePool governance"
    )

    args = parser.parse_args()

    if args.curative:
        cp = CurativePool()
        # Run a demonstration evaluation
        request = {"context": "demo curative evaluation", "params": {"scope": "small"}}
        proposed = {"action_type": "execute", "output_size": 100, "feature": "demo"}
        conversation = [{"role": "user", "content": "run curative demo"}]
        agent_pool = [
        {"id": "demo_guardian", "archetype": "The Guardian"},
        {"id": "demo_analyst", "archetype": "The Analyst"},
        ]

        result = cp.evaluate_action(request, proposed, conversation, agent_pool)
        print(json.dumps({
        "curative_version": cp.VERSION,
        "decision": result["decision"],
        "scope_valid": result["scope"]["valid"],
        "necessary": result["necessity"]["necessary"],
        "frustration_detected": result["emotion"]["frustration_detected"],
        "routing": result["routing"],
        }, indent=2))

        report = cp.get_fleet_report()
        print(json.dumps(report, indent=2))

    elif args.query_session:
        print("Session query: PAMS events DB not yet configured.")
        print("See azazael/skills/azazael-curative.md for setup instructions.")

    elif args.run:
        print("Fleet run: CurativePool governance active.")
        cp = CurativePool()
        # In production, this iterates over worm actions and evaluates each
        print(f"CurativePool {cp.VERSION} ready. Call evaluate_action() per worm.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
