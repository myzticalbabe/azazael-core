# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: NEURAL ADVANCEMENT (1 worm)
MetaCognitionWorm — Self-reflection, error learning, and strategy adaptation engine.

Enables the Azazael ecosystem to introspect on its own execution patterns,
learn from failures, and adapt strategies without human intervention.

Part of GOSP (Gossamer Oxford Semantical Parser) DoctoralThesisEngine —
Tier 5: DISTILLATION — Knowledge synthesis and pattern consolidation.

Features:
- Error pattern recognition across worm executions
- Strategy adaptation based on failure analysis  
- Confidence calibration for decision-making
- Introspective audit trails to PAMS/LocalMemoryStore
- Self-correction loops with CurativePool integration

Created: June 4, 2026 — Neural Advancement Realignment Session

Reflection / error-learning / strategy engine; not ML.step_monitor (the
step-stream stall watchdog).
"""

import json
import time
import threading
from datetime import datetime
from typing import Any, List
from collections import defaultdict

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Sentinel for disabled persistence (safer than string "none") --
_PERSISTENCE_DISABLED = object()

# -- Module-level imports for 3.14t thread safety (Lesson #29) --
# NEVER call importlib.import_module() from background threads — deadlocks under free-threading
try:
    from CORE.local_memory import get_memory_store as _get_memory_store_global
    _LOCAL_MEMORY_OK = True
except ImportError as exc:
    note_failure("meta_cognition:L37", exc)
    _LOCAL_MEMORY_OK = False
    _get_memory_store_global = None

try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("meta_cognition:L44", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5,
                      cooldown_seconds: float = 30.0, description: str = "",
                      tags: (set[str] | None) = None) -> Any:
        return lambda cls: cls

#: EP191's acceptance half lives in the verification layer
#: (`verification_gate.verify_before_accept`). Imported GUARDED, exactly like the
#: cross-module deps above: when it is not importable the exchange still runs and
#: reports that no gate was available, rather than claiming an acceptance that
#: never happened. A circular import surfaces here as ImportError (a partially
#: initialised module cannot supply the name), so the fallback covers that too.
try:
    from CORE.verification_gate import verify_before_accept as _verify_before_accept
    _VERIFICATION_OK = True
except ImportError as exc:
    note_failure("meta_cognition:verification-gate-import", exc)
    _VERIFICATION_OK = False
    _verify_before_accept = None


def _acceptance_gate():
    """The verification layer's acceptance gate, or None when absent."""
    return _verify_before_accept if _VERIFICATION_OK else None


def internalize(observation: dict[str, Any], *, worm: str = "global",
                persist: bool = False, store: Any = None) -> dict[str, Any]:
    """Run analyse -> recommend -> adapt over one observation (EP435).

    EP435's fourth phase is Internalize (Explore -> Evaluate -> Reflect ->
    Internalize). This engine already had Evaluate (`analyze_patterns`), Reflect
    (`recommend_from_evidence`) and Adapt (`adapt_strategy`) -- and nothing ran
    the three in sequence, so the technique existed as parts with no chain. This
    is that chain, in one fail-open call.

    The confidence gate is the engine's own `ADOPT_CONFIDENCE`: a recommendation
    below it is REPORTED but not adopted, so a self-test invocation cannot churn
    the active strategy on thin evidence.

    Read-mostly by default: `persist=False` builds the engine with persistence
    disabled, so running this from a self-test never writes a live store. A
    caller that wants the adaptation remembered passes `persist=True` (or an
    explicit `store`).

    Args:
        observation: {"worm_type", "action", "outcome" (required keys of the
            observe contract), plus optional "context", "duration_ms",
            "error".
        worm: fallback worm_type when the observation omits one.
        persist: allow the engine to read/write persistent state.
        store: an explicit memory store (overrides `persist`).

    Returns:
        dict with the chain's verdict: error_rate, recommended, confidence,
        adopted, strategy, reason. On failure: {"error", "adopted": False}.
    """
    try:
        engine = MetaCognitionWorm(
            memory_store=(store if store is not None
                          else (None if persist else _PERSISTENCE_DISABLED)))
        obs = dict(observation or {})
        target = str(obs.get("worm_type") or worm)
        engine.observe(
            worm_type=target,
            action=str(obs.get("action", "unknown")),
            outcome=str(obs.get("outcome", "success")),
            context=obs.get("context") or {},
            duration_ms=float(obs.get("duration_ms", 0.0) or 0.0),
            error=obs.get("error"),
        )
        analysis = engine.analyze_patterns(worm_type=target)
        rec = engine.recommend_from_evidence(target)
        recommended = rec.get("recommended")
        confidence = float(rec.get("confidence", 0.0) or 0.0)
        adopted = False
        if recommended and confidence >= engine.ADOPT_CONFIDENCE:
            engine.adapt_strategy(worm_type=target, recommended=recommended,
                                  confidence=confidence)
            adopted = True
        return {"engine": "meta_cognition", "worm_type": target,
                "error_rate": analysis.get("error_rate"),
                "recommended": recommended, "confidence": confidence,
                "adopted": adopted,
                "strategy": engine.get_current_strategy(target),
                "reason": rec.get("reason")}
    except Exception as exc:
        note_failure("meta_cognition:L110", exc)
        return {"error": f"{type(exc).__name__}: {exc}", "adopted": False}


@register_worm(
    category="learning", priority=7, cooldown_seconds=90.0,
    description="MetaCognition engine — self-reflection, error pattern learning, strategy adaptation for autonomous improvement"
)
class MetaCognitionWorm:
    """Meta-cognitive engine for autonomous system improvement.

    Analyzes execution patterns across worm executions to:
    1. Detect recurring error patterns and failure modes
    2. Build confidence models for decision-making
    3. Adapt strategies based on historical performance
    4. Generate introspective audit trails

    The meta-cognition loop operates in 4 phases:
    OBSERVE -> ANALYZE -> ADAPT -> VALIDATE

    Usage:
        mc = MetaCognitionWorm()
        episode = mc.observe(worm_type="SystemZ", action="scan", outcome="error", context={...})
        patterns = mc.analyze_patterns()
        adaptation = mc.adapt_strategy(worm_type="SystemZ", current_strategy="default")
        report = mc.generate_introspection_report()
    """

    VERSION = "1.0.0"

    # Maps an active strategy to dreaming-loop parameter tuning so
    # recorded policy (adapt_strategy) steers real dream behavior.
    # Minimum confidence a recommendation must carry before it flips the
    # active strategy. Exploration suggestions (0.05-0.45 band) get recorded
    # but cannot churn the strategy without stronger evidence.
    ADOPT_CONFIDENCE = 0.5

    DREAM_STRATEGY_TUNING: dict[str, dict[str, Any]] = {
        "default": {},
        "conservative": {"num_episodes": 2, "scenario_count": 10, "creativity_factor": 0.15},
        "defensive": {"num_episodes": 2, "scenario_count": 30, "creativity_factor": 0.1},
        "aggressive": {"num_episodes": 8, "scenario_count": 20, "creativity_factor": 0.6},
        "adaptive": {"num_episodes": 4, "scenario_count": 15, "creativity_factor": 0.35},
        "batched": {"num_episodes": 5, "scenario_count": 30, "creativity_factor": 0.4},
        "staged": {"num_episodes": 4, "scenario_count": 15, "creativity_factor": 0.3},
    }

    def __init__(self, memory_store=None):
        self._lock = threading.Lock()
        # memory_store: None=use global, LocalMemoryStore instance=injected, _PERSISTENCE_DISABLED=skip all I/O
        self._memory_store = memory_store
        self._persistence_enabled = memory_store is not _PERSISTENCE_DISABLED
        self._observations: list[dict[str, Any]] = []
        self._error_patterns: dict[str, list[dict]] = defaultdict(list)
        self._confidence_models: dict[str, dict[str, float]] = {}
        self._strategy_scores: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._adaptations: list[dict] = []
        self._dialectics: list[dict[str, Any]] = []
        self._current_strategies: dict[str, str] = {}
        self._episode_count = 0

        # Auto-restore state from previous session (fail-safe, never blocks)
        if self._persistence_enabled:
            self.load_state()

    # -- OBSERVE Phase --------------------------------------------------

    def observe(self, worm_type: str, action: str, outcome: str,
                context: (dict | None) = None, duration_ms: float = 0.0,
                error: (str | None) = None) -> dict[str, Any]:
        """Record an execution observation for pattern analysis.

        Args:
            worm_type: Type of worm that executed
            action: Action taken
            outcome: "success", "error", "timeout", "blocked"
            context: Additional execution context
            duration_ms: Execution duration
            error: Error message if outcome is "error"

        Returns:
            dict with observation_id and current pattern state
        """
        obs = {
            "id": f"mc_{self._episode_count}_{int(time.time() * 1000)}",
            "timestamp": datetime.now().isoformat(),
            "worm_type": worm_type,
            "action": action,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "error": error,
            "context": context or {},
            "free_threaded": True,
        }

        with self._lock:
            self._observations.append(obs)
            self._episode_count += 1
            if len(self._observations) > 1000:
                self._observations = self._observations[-1000:]

            # Update error patterns if this was an error
            if outcome == "error" and error:
                self._error_patterns[worm_type].append({
                    "timestamp": obs["timestamp"],
                    "action": action,
                    "error": error,
                    "duration_ms": duration_ms,
                })
                if len(self._error_patterns[worm_type]) > 50:
                    self._error_patterns[worm_type] = self._error_patterns[worm_type][-50:]

        # Persist to memory store
        self._persist_observation(obs)

        return {
            "observation_id": obs["id"],
            "total_observations": self._episode_count,
            "error_count": sum(len(v) for v in self._error_patterns.values()),
        }

    def _get_store(self):
        """Resolve memory store: injected -> global -> None.

        Thread-safe under 3.14t free-threading — all imports happen at module level.
        Uses sys.modules lookup pattern from Lesson #29 to avoid import deadlocks.
        """
        if not self._persistence_enabled:
            return None
        if self._memory_store is not None:
            return self._memory_store
        if _LOCAL_MEMORY_OK and _get_memory_store_global is not None:
            try:
                return _get_memory_store_global()
            except Exception as exc:
                note_failure("meta_cognition:L243", exc)
                return None
        return None

    def _persist_observation(self, obs: dict) -> None:
        """Persist observation to LocalMemoryStore."""
        store = self._get_store()
        if store is None:
            return
        try:
            store.record_event(
                source="MetaCognitionWorm",
                event_type="meta_cognition_observation",
                data=obs,
                severity="error" if obs["outcome"] == "error" else "info",
                tags=["meta_cognition", obs["worm_type"], obs["outcome"]],
            )
        except Exception as exc:
            note_failure("meta_cognition:L260", exc)

    # -- ANALYZE Phase --------------------------------------------------

    def analyze_patterns(self, worm_type: (str | None) = None,
                         limit: int = 50) -> dict[str, Any]:
        """Analyze error patterns and compute failure probabilities.

        Args:
            worm_type: Filter to specific worm type or None for all
            limit: Maximum observations to analyze

        Returns:
            dict with error patterns, failure rates, and recurrence analysis
        """
        with self._lock:
            if worm_type:
                obs_list = [o for o in self._observations[-limit:] if o["worm_type"] == worm_type]
                errors = self._error_patterns.get(worm_type, [])
            else:
                obs_list = self._observations[-limit:]
                errors = []
                for errs in self._error_patterns.values():
                    errors.extend(errs)

        if not obs_list:
            return {"total": 0, "error_rate": 0.0, "patterns": []}

        total = len(obs_list)
        error_count = sum(1 for o in obs_list if o["outcome"] == "error")
        error_rate = error_count / total if total > 0 else 0.0

        # Find recurring error patterns (same error message appearing >2 times)
        error_msgs: dict[str, int] = defaultdict(int)
        for err in errors[-limit:]:
            msg = err.get("error", "unknown")
            error_msgs[msg] += 1

        recurring = [
            {"error": msg, "count": count, "recurring": count > 2}
            for msg, count in sorted(error_msgs.items(), key=lambda x: -x[1])
        ]

        # Compute confidence per worm type
        confidence = self._compute_confidence(worm_type, obs_list)

        # Update confidence models
        if worm_type:
            self._confidence_models[worm_type] = confidence

        result = {
            "total": total,
            "error_count": error_count,
            "error_rate": round(error_rate, 4),
            "recurring_errors": recurring[:10],
            "confidence": confidence,
            "worm_type": worm_type or "all",
        }
        # Production caller for compute_cross_worm_correlation: every
        # pattern analysis also reports whether errors in one worm predict
        # errors in another (additive key, fail-open; thin data returns
        # early inside the callee, never raises here).
        try:
            result["cross_worm_correlation"] = (
                self.compute_cross_worm_correlation(
                    worm_types=[worm_type] if worm_type else None))
        except Exception as exc:
            note_failure("meta_cognition:L348", exc)
            result["cross_worm_correlation"] = {"correlations": []}
        return result

    def _compute_confidence(self, worm_type: (str | None),
                            observations: list[dict]) -> dict[str, float]:
        """Compute confidence scores based on historical performance."""
        if not observations:
            return {"overall": 0.5, "success_rate": 0.0, "stability": 0.5}

        total = len(observations)
        successes = sum(1 for o in observations if o["outcome"] == "success")
        errors = sum(1 for o in observations if o["outcome"] == "error")
        timeouts = sum(1 for o in observations if o["outcome"] == "timeout")

        success_rate = successes / total if total > 0 else 0.0

        # Stability: inversely proportional to error variance
        durations = [o["duration_ms"] for o in observations if o["duration_ms"] > 0]
        if durations:
            avg_dur = sum(durations) / len(durations)
            variance = sum((d - avg_dur) ** 2 for d in durations) / len(durations)
            stability = 1.0 / (1.0 + variance / 10000.0)
        else:
            stability = 0.5

        # Overall confidence: weighted blend
        overall = success_rate * 0.5 + stability * 0.3 + (1.0 - errors / max(total, 1)) * 0.2

        return {
            "overall": round(overall, 4),
            "success_rate": round(success_rate, 4),
            "stability": round(stability, 4),
            "error_ratio": round(errors / max(total, 1), 4),
            "n": total,
        }

    # -- ADAPT Phase ----------------------------------------------------

    def recommend_from_evidence(self, worm_type: str) -> dict[str, Any]:
        """Derive an evidence-backed strategy recommendation from real outcomes.

        Uses the confidence model built by analyze_patterns() (actual observed
        outcomes) to recommend how the learning loop should behave next:

          * high error_ratio  -> defensive / conservative (stabilize, pull back)
          * high success_rate -> aggressive (bolder exploration)
          * mixed / noisy     -> adaptive (moderate)

        The returned confidence reflects evidence strength so it either clears
        ADOPT_CONFIDENCE (a real signal -- e.g. a worm failing often) or stays
        below it (noise) -- which is what keeps the policy channel from
        churning on weak dream suggestions. Callers pass recommended +
        confidence straight into adapt_strategy().
        """
        conf = self._confidence_models.get(worm_type)
        if conf is None:
            obs = [o for o in self._observations if o["worm_type"] == worm_type]
            conf = self._compute_confidence(worm_type, obs)

        n = int(conf.get("n", 0) or 0)
        err = float(conf.get("error_ratio", 0.0) or 0.0)
        ok = float(conf.get("success_rate", 0.0) or 0.0)

        if n < 5:
            return {"recommended": None, "confidence": 0.0,
                    "evidence": {"n": n, "error_ratio": round(err, 4),
                                 "success_rate": round(ok, 4)},
                    "reason": f"insufficient evidence ({n} observations)"}

        if err >= 0.5:
            strat, val = "defensive", 0.5 + err * 0.5
            reason = f"error_ratio {err:.2f} >= 0.5 -- stabilize"
        elif err >= 0.3:
            strat, val = "conservative", 0.5 + err * 0.5
            reason = f"error_ratio {err:.2f} in [0.3, 0.5) -- pull back"
        elif ok >= 0.85:
            strat, val = "aggressive", 0.5 + ok * 0.3
            reason = f"success_rate {ok:.2f} >= 0.85 -- explore harder"
        else:
            strat, val = "adaptive", 0.45
            reason = f"mixed outcomes (err {err:.2f} / ok {ok:.2f}) -- moderate"

        return {"recommended": strat,
                "confidence": round(min(val, 0.99), 3),
                "evidence": {"n": n, "error_ratio": round(err, 4),
                             "success_rate": round(ok, 4)},
                "reason": reason}

    def get_current_strategy(self, worm_type: str) -> str:
        """Return the active strategy for a worm type ('default' when unset)."""
        return self._current_strategies.get(worm_type, "default")

    def dream_tuning_for(self, worm_type: str = "global") -> tuple[dict[str, Any], str]:
        """Return dream-loop parameter tuning for the active strategy.

        Maps the tracked strategy (set via adapt_strategy) onto concrete
        dreaming knobs so recorded policy steers real behavior:
          conservative/defensive -> fewer, tamer dreams (less exploration)
          aggressive/batched     -> more/bolder dreams (more exploration)
        Unmapped strategies return ({}, strategy): caller keeps defaults.
        """
        strategy = self.get_current_strategy(worm_type)
        return dict(self.DREAM_STRATEGY_TUNING.get(strategy, {})), strategy

    def adapt_strategy(self, worm_type: str,
                       current_strategy: (str | None) = None,
                       available_strategies: (list[str] | None) = None,
                       recommended: (str | None) = None,
                       confidence: float = 0.0) -> dict[str, Any]:
        """Adapt execution strategy based on historical performance.

        Two evidence channels drive adaptation:

        1. Policy channel -- an explicit ``recommended`` strategy from a
           governed caller (e.g. dream_policy_bridge). Callers apply their
           own confidence/governance gates before calling; when a different
           strategy is recommended it is adopted, the per-worm current
           strategy is switched, and the switch is persisted via
           save_state()/load_state() so later runs see the new current.
        2. Observation channel -- confidence-weighted scoring of stored
           alternatives (``_strategy_scores`` / ``_confidence_models``),
           switching only when an alternative exceeds the current score by
           the 15% improvement threshold.

        Args:
            worm_type: Worm type to adapt for
            current_strategy: Currently active strategy name. When omitted,
                or when the caller passes the generic marker 'default' but a
                tracked strategy exists, the tracked strategy is used.
            available_strategies: list of alternative strategies
            recommended: Optional explicit strategy to adopt (policy channel)
            confidence: Confidence carried by the recommendation (0.0-1.0)

        Returns:
            dict with recommended_strategy and adaptation rationale
        """
        if available_strategies is None:
            available_strategies = ["default", "conservative", "aggressive", "defensive"]

        # Resolve the actual current strategy: tracked state wins over a
        # hard-coded 'default' marker passed by callers without state.
        tracked = self._current_strategies.get(worm_type)
        if current_strategy is None:
            current_strategy = tracked or "default"
        elif tracked and tracked != "default" and current_strategy == "default":
            current_strategy = tracked

        # Confidence for the current strategy (unknown = 0.5)
        conf_model = self._confidence_models.get(worm_type, {})
        current_score = conf_model.get("overall")
        if current_score is None:
            current_score = self._strategy_scores[worm_type].get(current_strategy, 0.5)
        try:
            current_score = float(current_score) or 0.5
        except (TypeError, ValueError) as exc:
            note_failure("meta_cognition:L471", exc)
            current_score = 0.5

        # Score each available strategy from stored history (default 0.5)
        scores = {current_strategy: current_score}
        for alt in available_strategies:
            if alt == current_strategy or alt in scores:
                continue
            stored = self._strategy_scores[worm_type].get(alt)
            try:
                scores[alt] = float(stored) if stored is not None else 0.5
            except (TypeError, ValueError) as exc:
                note_failure("meta_cognition:L482", exc)
                scores[alt] = 0.5

        should_adapt = False
        best_strategy = current_strategy
        best_score = current_score

        if recommended and recommended != current_strategy and recommended in available_strategies:
            # Policy channel: governed recommendation -- adopt only with real
            # evidence (confidence >= ADOPT_CONFIDENCE). Low-confidence dream
            # exploration suggestions are recorded but do NOT flip the active
            # strategy, which prevents a random walk where each cycle adopts
            # whichever strategy the latest dream happened to name.
            if confidence >= self.ADOPT_CONFIDENCE:
                rec_score = max(scores.get(recommended, 0.5), confidence)
                scores[recommended] = max(scores.get(recommended, 0.5), rec_score)
                should_adapt = True
                best_strategy = recommended
                best_score = scores[recommended]
                rationale = (
                    f"Apply recommended {recommended}: policy suggestion "
                    f"(confidence {confidence:.3f})"
                )
            else:
                rationale = (
                    f"Hold {current_strategy}: recommendation for "
                    f"{recommended} below adoption bar "
                    f"(confidence {confidence:.3f} < {self.ADOPT_CONFIDENCE})"
                )
        else:
            # Observation channel: 15% improvement threshold.
            # Empty scores (cold start, no observations yet) hold steady
            # instead of max()-on-empty crashing the cycle.
            if not scores:
                best_strategy = current_strategy
                best_score = current_score
                rationale = (
                    f"Keep {current_strategy}: no scores yet (cold start)"
                )
            else:
                best_strategy = max(scores, key=lambda s: scores[s])
                best_score = scores[best_strategy]
            if best_strategy != current_strategy and best_score > current_score * 1.15:
                should_adapt = True
                rationale = (
                    f"Switch to {best_strategy}: score {best_score:.3f} > "
                    f"{current_score * 1.15:.3f}"
                )
            else:
                rationale = (
                    f"Keep {current_strategy}: no better alternative "
                    f"(best: {best_strategy} at {best_score:.3f})"
                )

        adaptation = {
            "worm_type": worm_type,
            "current_strategy": current_strategy,
            "current_score": round(current_score, 4),
            "recommended_strategy": best_strategy,
            "recommended_score": round(best_score, 4),
            "should_adapt": should_adapt,
            "rationale": rationale,
            "all_scores": {k: round(v, 4) for k, v in scores.items()},
            "channel": "recommendation" if recommended else "observation",
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            self._adaptations.append(adaptation)
            if len(self._adaptations) > 100:
                self._adaptations = self._adaptations[-100:]

            # Commit the switch so later runs see the new current strategy.
            if should_adapt and best_strategy != current_strategy:
                self._current_strategies[worm_type] = best_strategy
                self._strategy_scores[worm_type][best_strategy] = best_score

        # Persist adaptation
        self._persist_adaptation(adaptation)

        return adaptation

    def _persist_adaptation(self, adaptation: dict) -> None:
        """Persist strategy adaptation to LocalMemoryStore."""
        store = self._get_store()
        if store is None:
            return
        try:
            store.record_event(
                source="MetaCognitionWorm",
                event_type="strategy_adaptation",
                data=adaptation,
                severity="warning" if adaptation.get("should_adapt") else "info",
                tags=["meta_cognition", "adaptation", adaptation.get("worm_type", "unknown")],
            )
        except Exception as exc:
            note_failure("meta_cognition:L577", exc)

    # -- VALIDATE Phase -------------------------------------------------

    def validate_adaptation(self, worm_type: str, strategy: str,
                            before_confidence: float, current_confidence: float) -> dict[str, Any]:
        """Validate whether a strategy adaptation improved performance.

        Args:
            worm_type: Worm type being validated
            strategy: The new strategy being validated
            before_confidence: Confidence score before adaptation
            current_confidence: Confidence score after adaptation

        Returns:
            dict with validation result and improvement metrics
        """
        improvement = current_confidence - before_confidence
        improved = improvement > 0.05

        validation = {
            "worm_type": worm_type,
            "strategy": strategy,
            "before_confidence": round(before_confidence, 4),
            "current_confidence": round(current_confidence, 4),
            "improvement": round(improvement, 4),
            "improved": improved,
            "verdict": "successful" if improved else "regression" if improvement < -0.05 else "neutral",
            "timestamp": datetime.now().isoformat(),
        }

        # Update strategy scores based on validation
        with self._lock:
            if improved:
                self._strategy_scores[worm_type][strategy] = (
                    self._strategy_scores[worm_type].get(strategy, 0.5) * 1.1
                )
            elif improvement < -0.05:
                self._strategy_scores[worm_type][strategy] = (
                    self._strategy_scores[worm_type].get(strategy, 0.5) * 0.9
                )

        return validation

    # -- DIALECTIC Phase (EP191 ReTAS dialectical alignment) ------------

    #: Same evidence bar `recommend_from_evidence` uses: below it a claim is
    #: reported as UNEVIDENCED rather than judged on a thin sample.
    DIALECTIC_MIN_EVIDENCE = 5

    def dialectic(self, claim, *, worm_type: str = "global", support=None,
                  evidence=None, persist: bool = True,
                  acceptance_fn=None) -> dict[str, Any]:
        """Put one claim through a thesis/antithesis/synthesis exchange (EP191).

        ReTAS's mechanism is dialectical alignment: a candidate is not accepted
        on its own assertion, it has to survive a rebuttal built from the
        system's own evidence. This engine already HELD that evidence -- error
        patterns and confidence models -- and never argued with it: `observe`
        records, `analyze_patterns` summarises, `adapt_strategy` switches, and
        nothing put a claim itself on trial. This is that missing exchange.

        The two sides are built from real state, not invented prose:

          * THESIS      -- the claim, whatever support its caller states, and
                           the confidence the caller attaches to it.
          * ANTITHESIS  -- the engine's OWN record for `worm_type`: observed
                           success rate, error ratio, and every recurring error
                           whose message matches the claim's subject. A record
                           thinner than DIALECTIC_MIN_EVIDENCE yields the
                           rebuttal "not enough evidence", which is a finding,
                           not a pass.
          * SYNTHESIS   -- a verdict from the stated blend below, so the
                           arithmetic is inspectable instead of magic:
                           ``claimed*0.35 + observed*0.45 + stability*0.20
                           - rebuttal``, where `rebuttal` is the error ratio
                           raised by matched recurring errors (capped at 0.5).

        Verdict bands: >= 0.60 supported, >= 0.35 weakened, else unsupported;
        an insufficient record gives "unevidenced"; and a rebuttal of 0.5 or
        more caps the verdict at "weakened", because a subject that fails half
        the time cannot be called supported by a confident claim.

        Args:
            claim: the candidate as a string, or a dict carrying
                "claim"/"statement" plus optional "support", "confidence".
            worm_type: whose recorded behaviour argues against the claim.
            support: caller-stated support (overrides a dict claim's own).
            evidence: optional extra context stored with the exchange.
            persist: write the exchange to the store (default True; pass False
                from a self-test so a dry run writes nothing).
            acceptance_fn: optional ``callable(claim) -> dict``, e.g.
                `verification_gate.verify_before_accept`. When omitted, the
                guarded module-level import is used if it loaded.

        Returns:
            dict with thesis, antithesis, synthesis, verdict, confidence,
            acceptance (None when no gate ran) and recorded.
        """
        claimed = 0.5
        if isinstance(claim, dict):
            text = str(claim.get("claim") or claim.get("statement") or "").strip()
            if support is None:
                support = claim.get("support")
            try:
                claimed = float(claim.get("confidence", 0.5) or 0.5)
            except (TypeError, ValueError) as exc:
                note_failure("meta_cognition:dialectic/claim-confidence", exc)
                claimed = 0.5
        else:
            text = str(claim or "").strip()

        if not text:
            return {"error": "empty claim", "verdict": "unsupported",
                    "confidence": 0.0, "recorded": False}

        try:
            claimed = max(0.0, min(1.0, float(claimed)))
        except (TypeError, ValueError) as exc:
            note_failure("meta_cognition:dialectic/claim-clamp", exc)
            claimed = 0.5

        # -- ANTITHESIS: the engine's own record for this subject.
        with self._lock:
            subject_obs = [o for o in self._observations
                           if o.get("worm_type") == worm_type]
            recurring = list(self._error_patterns.get(worm_type, []))

        model = self._confidence_models.get(worm_type)
        if model is None:
            model = self._compute_confidence(worm_type, subject_obs)

        n = int(model.get("n", 0) or 0)
        observed = float(model.get("success_rate", 0.0) or 0.0)
        stability = float(model.get("stability", 0.5) or 0.5)
        error_ratio = float(model.get("error_ratio", 0.0) or 0.0)

        needle = text.lower()
        matched = [err for err in recurring
                   if needle and needle in str(err.get("error", "")).lower()]
        rebuttal = min(0.5, error_ratio + 0.1 * len(matched))

        if n < self.DIALECTIC_MIN_EVIDENCE:
            argument = (f"no evidence for {worm_type}: {n} observation(s), "
                        f"below the {self.DIALECTIC_MIN_EVIDENCE} bar")
        else:
            argument = (f"{worm_type} succeeds {observed:.0%} of {n} "
                        f"observation(s), error ratio {error_ratio:.2f}")
            if matched:
                argument += (f", {len(matched)} recurring error(s) match "
                             f"the claim")

        antithesis = {
            "worm_type": worm_type,
            "observations": n,
            "success_rate": round(observed, 4),
            "error_ratio": round(error_ratio, 4),
            "matched_recurring_errors": len(matched),
            "rebuttal_weight": round(rebuttal, 4),
            "argument": argument,
        }

        # -- SYNTHESIS: the blend, then the bands.
        score = claimed * 0.35 + observed * 0.45 + stability * 0.20 - rebuttal
        score = max(0.0, min(1.0, score))
        if n < self.DIALECTIC_MIN_EVIDENCE:
            verdict = "unevidenced"
        elif rebuttal >= 0.5:
            verdict = "weakened" if score >= 0.35 else "unsupported"
        elif score >= 0.60:
            verdict = "supported"
        elif score >= 0.35:
            verdict = "weakened"
        else:
            verdict = "unsupported"

        # -- The acceptance gate, when the verification layer is reachable.
        gate = acceptance_fn if acceptance_fn is not None else _acceptance_gate()
        acceptance = None
        if gate is not None:
            try:
                result = gate(text)
                acceptance = result if isinstance(result, dict) else {"result": result}
            except Exception as exc:
                note_failure("meta_cognition:dialectic/acceptance", exc)
                acceptance = {"error": f"{type(exc).__name__}: {exc}"}

        exchange = {
            "claim": text,
            "worm_type": worm_type,
            "thesis": {"claim": text,
                       "stated_support": str(support or ""),
                       "claimed_confidence": round(claimed, 4)},
            "antithesis": antithesis,
            "synthesis": {"score": round(score, 4), "verdict": verdict,
                          "blend": "claim 0.35 / observed 0.45 / stability "
                                   "0.20 - rebuttal"},
            "acceptance": acceptance,
            "verdict": verdict,
            "confidence": round(score, 4),
            "evidence": evidence or {},
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            self._dialectics.append(exchange)
            if len(self._dialectics) > 50:
                self._dialectics = self._dialectics[-50:]

        recorded = self._persist_dialectic(exchange) if persist else False
        exchange["recorded"] = recorded
        return exchange

    def _persist_dialectic(self, exchange: dict) -> bool:
        """Record one exchange. True only when the store accepted it."""
        store = self._get_store()
        if store is None:
            return False
        try:
            store.record_event(
                source="MetaCognitionWorm",
                event_type="dialectic_exchange",
                data=exchange,
                severity="warning" if exchange.get("verdict") == "unsupported" else "info",
                tags=["meta_cognition", "dialectic",
                      exchange.get("worm_type", "unknown")],
            )
            return True
        except Exception as exc:
            note_failure("meta_cognition:dialectic/persist", exc)
            return False

    def recent_dialectics(self, limit: int = 5) -> list[dict[str, Any]]:
        """The last few exchanges, so the channel has a reader (not write-only)."""
        with self._lock:
            return [dict(d) for d in self._dialectics[-limit:]]

    # -- Introspection Report -------------------------------------------

    def generate_introspection_report(self) -> dict[str, Any]:
        """Generate a comprehensive meta-cognition report.

        Includes: observation summary, error patterns, confidence models,
        strategy adaptations, and recommendations.
        """
        with self._lock:
            total_obs = len(self._observations)
            total_errors = sum(len(v) for v in self._error_patterns.values())
            total_adaptations = len(self._adaptations)
            total_dialectics = len(self._dialectics)

        # Compute global error rate
        error_rate = total_errors / max(total_obs, 1)

        # Find most error-prone worms
        worm_error_counts = {
            wt: len(errs) for wt, errs in self._error_patterns.items()
        }
        top_offenders = sorted(worm_error_counts.items(), key=lambda x: -x[1])[:5]

        # Strategy summary (skip empty score dicts: cold-start worms record
        # a key before any observation lands — same max()-on-empty class)
        strategy_summary = {}
        for wt, strategies in self._strategy_scores.items():
            if not strategies:
                continue
            best = max(strategies, key=lambda s: strategies[s])
            strategy_summary[wt] = {
                "best_strategy": best,
                "best_score": round(strategies[best], 4),
                "alternatives": len(strategies),
            }

        # Confidence summary
        confidence_summary = {}
        for wt, conf in self._confidence_models.items():
            confidence_summary[wt] = conf.get("overall", 0.5)

        # Generate recommendations
        recommendations = []
        if error_rate > 0.2:
            recommendations.append(
                f"High error rate ({error_rate:.1%}) — consider CurativePool scope tightening"
            )
        for worm, count in top_offenders:
            if count > 10:
                recommendations.append(
                    f"High error count for {worm} ({count} errors) — review strategy"
                )
        if total_adaptations == 0:
            recommendations.append(
                "No strategy adaptations yet — run adapt_strategy() for top offenders"
            )

        return {
            "version": self.VERSION,
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_observations": total_obs,
                "total_errors": total_errors,
                "error_rate": round(error_rate, 4),
                "total_adaptations": total_adaptations,
                "total_dialectic_exchanges": total_dialectics,
                "worm_types_tracked": len(self._error_patterns),
            },
            "top_error_worms": [
                {"worm_type": wt, "errors": count}
                for wt, count in top_offenders
            ],
            "strategy_scores": strategy_summary,
            "confidence_models": confidence_summary,
            "recent_adaptations": self._adaptations[-5:] if self._adaptations else [],
            "recent_dialectics": [
                {"claim": str(d.get("claim", ""))[:120],
                 "worm_type": d.get("worm_type"),
                 "verdict": d.get("verdict"),
                 "confidence": d.get("confidence")}
                for d in self._dialectics[-5:]
            ],
            "recommendations": recommendations,
        }

    # -- Worm execute() -------------------------------------------------

    def execute(self) -> dict[str, Any]:
        """Standard worm execution — runs introspection report and auto-saves state."""
        report = self.generate_introspection_report()
        store = self._get_store()
        if store is not None:
            try:
                store.record_event(
                    source="MetaCognitionWorm",
                    event_type="introspection_report",
                    data=report,
                    severity="info",
                    tags=["meta_cognition", "report", "introspection"],
                )
            except Exception as exc:
                note_failure("meta_cognition:L713", exc)
        # Auto-save state for session-to-session persistence
        if self._persistence_enabled:
            self.save_state()
        return report

    # -- Persistence -----------------------------------------------

    def save_state(self) -> dict[str, Any]:
        """Serialize all internal state and persist to LocalMemoryStore.

        Returns a dict with serialized observations, error patterns,
        confidence models, strategy scores, and adaptations.
        """
        with self._lock:
            state = {
                "version": self.VERSION,
                "episode_count": self._episode_count,
                "observations": self._observations[-200:],  # Keep last 200
                "error_patterns": dict(self._error_patterns),
                "confidence_models": dict(self._confidence_models),
                "strategy_scores": {k: dict(v) for k, v in self._strategy_scores.items()},
                "adaptations": self._adaptations[-50:],  # Keep last 50
                "dialectics": self._dialectics[-50:],  # Keep last 50
                "current_strategies": dict(self._current_strategies),
                "saved_at": datetime.now().isoformat(),
                "free_threaded": True,
            }

        # Persist to LocalMemoryStore
        store = self._get_store()
        if store is not None:
            try:
                store.record_event(
                    source="MetaCognitionWorm",
                    event_type="state_saved",
                    data=state,
                    severity="info",
                    tags=["meta_cognition", "persistence", "save"],
                )
            except Exception as exc:
                note_failure("meta_cognition:L753", exc)

        return state

    def load_state(self) -> bool:
        """Load the most recent saved state from LocalMemoryStore.

        Restores observations, error patterns, confidence models,
        strategy scores, and adaptations from the last save_state() call.

        Returns:
            True if state was loaded successfully, False otherwise.
        """
        store = self._get_store()
        if store is None:
            return False
        try:
            # query_memories orders newest-first (id DESC); FTS
            # search_memories ranks by relevance and can surface old
            # rows, so it must not be used for "load latest state".
            events = store.query_memories(
                event_type="state_saved", source="MetaCognitionWorm", limit=5
            )
            if not events:
                return False

            # Find the most recent state_saved event
            for ev in events:
                data = ev.get("data", {})
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except (json.JSONDecodeError, TypeError) as exc:
                        note_failure("meta_cognition:L786", exc)
                        continue
                if not isinstance(data, dict) or "episode_count" not in data:
                    continue

                # Restore state
                with self._lock:
                    self._episode_count = data.get("episode_count", 0)
                    self._observations = data.get("observations", [])
                    self._error_patterns = defaultdict(list, data.get("error_patterns", {}))
                    self._confidence_models = data.get("confidence_models", {})
                    self._strategy_scores = defaultdict(
                        lambda: defaultdict(float),
                        {k: defaultdict(float, v) for k, v in data.get("strategy_scores", {}).items()},
                    )
                    self._adaptations = data.get("adaptations", [])
                    self._dialectics = data.get("dialectics", [])
                    self._current_strategies = data.get("current_strategies", {}) or {}

                return True

            return False
        except Exception as exc:
            note_failure("meta_cognition:L807", exc)
            return False

    # -- Anomaly Detection --------------------------------------------

    def detect_anomalies(self, worm_type: (str | None) = None,
                         limit: int = 100,
                         error_sigma: float = 2.0,
                         duration_sigma: float = 3.0) -> dict[str, Any]:
        """Detect behavioral anomalies in execution patterns.

        Uses rolling statistics to flag observations that deviate
        significantly from historical norms:
        - Error rate exceeding error_sigma standard deviations
        - Duration exceeding duration_sigma standard deviations

        Args:
            worm_type: Filter to specific worm or None for all
            limit: Recent observations to analyze
            error_sigma: Stddev threshold for error rate anomalies
            duration_sigma: Stddev threshold for duration anomalies

        Returns:
            dict with anomalous episodes and severity scores
        """
        with self._lock:
            if worm_type:
                observations = [o for o in self._observations[-limit:] if o["worm_type"] == worm_type]
            else:
                observations = self._observations[-limit:]

        if len(observations) < 10:
            return {"anomalies": [], "total_analyzed": len(observations), "message": "Insufficient data (need >=10)"}

        window_size = 10
        anomalies = []
        error_rates = []
        durations = []

        for i in range(window_size, len(observations)):
            window = observations[i - window_size:i]
            window_errors = sum(1 for o in window if o["outcome"] == "error")
            window_error_rate = window_errors / len(window)
            window_durations = [o["duration_ms"] for o in window if o["duration_ms"] > 0]
            sum(window_durations) / len(window_durations) if window_durations else 0.0
            current = observations[i]
            current_duration = current.get("duration_ms", 0.0)
            error_rates.append(window_error_rate)
            durations.append(current_duration)

        if not error_rates:
            return {"anomalies": [], "total_analyzed": len(observations)}

        mean_error_rate = sum(error_rates) / len(error_rates)
        mean_duration = sum(durations) / len(durations)
        error_variance = sum((r - mean_error_rate) ** 2 for r in error_rates) / len(error_rates)
        error_stddev = error_variance ** 0.5
        duration_variance = sum((d - mean_duration) ** 2 for d in durations) / len(durations)
        duration_stddev = duration_variance ** 0.5 if duration_variance > 0 else 0.0

        for i in range(window_size, len(observations)):
            window = observations[i - window_size:i]
            window_errors = sum(1 for o in window if o["outcome"] == "error")
            window_error_rate = window_errors / len(window)
            current = observations[i]
            current_duration = current.get("duration_ms", 0.0)

            error_anomaly = False
            error_severity = 0.0
            if error_stddev > 0 and window_error_rate > mean_error_rate + error_sigma * error_stddev:
                error_anomaly = True
                error_severity = (window_error_rate - mean_error_rate) / max(error_stddev, 0.001)

            duration_anomaly = False
            duration_severity = 0.0
            if duration_stddev > 0 and current_duration > mean_duration + duration_sigma * duration_stddev:
                duration_anomaly = True
                duration_severity = (current_duration - mean_duration) / max(duration_stddev, 0.001)

            if error_anomaly or duration_anomaly:
                severity = max(error_severity, duration_severity)
                severity_label = ("critical" if severity > 5.0 else "high" if severity > 3.0 else "medium")
                anomalies.append({
                    "observation_index": i,
                    "observation_id": current.get("id", f"obs_{i}"),
                    "worm_type": current.get("worm_type", "unknown"),
                    "outcome": current.get("outcome", "unknown"),
                    "duration_ms": current_duration,
                    "window_error_rate": round(window_error_rate, 4),
                    "mean_error_rate": round(mean_error_rate, 4),
                    "severity": severity_label,
                    "severity_score": round(severity, 2),
                    "trigger": ("error_rate" if error_anomaly else "") + ("+" if error_anomaly and duration_anomaly else "")
                                + ("duration" if duration_anomaly else ""),
                    "timestamp": current.get("timestamp", ""),
                })

        return {
            "anomalies": anomalies,
            "total_analyzed": len(observations),
            "window_size": window_size,
            "baseline": {
                "mean_error_rate": round(mean_error_rate, 4),
                "error_stddev": round(error_stddev, 4),
                "mean_duration_ms": round(mean_duration, 2),
                "duration_stddev_ms": round(duration_stddev, 2),
            },
            "error_sigma_threshold": error_sigma,
            "duration_sigma_threshold": duration_sigma,
            "timestamp": datetime.now().isoformat(),
        }

    # -- Cross-Worm Correlation --------------------------------------

    def compute_cross_worm_correlation(self, worm_types: (list[str] | None) = None,
                                       window_count: int = 20,
                                       min_correlation: float = 0.3) -> dict[str, Any]:
        """Compute error-rate correlation matrix across worm types.

        Detects if errors in one worm type predict errors in another.
        Uses rolling-window error rates and Pearson correlation.

        Args:
            worm_types: Specific worm types to compare, or None for all tracked
            window_count: Number of rolling windows to use for time series
            min_correlation: Minimum |r| to report as significant

        Returns:
            dict with correlation matrix, significant pairs, and recommendations
        """
        with self._lock:
            excluded = []
            if worm_types is None:
                worm_types = list(self._error_patterns.keys())
            else:
                excluded = [w for w in worm_types if w not in self._error_patterns]
                worm_types = [w for w in worm_types if w in self._error_patterns]

            if len(worm_types) < 2:
                return {
                    "correlations": [],
                    "message": "Need at least 2 worm types with error data",
                    "excluded_no_data": excluded,
                }

            # Build time-series of error rates for each worm type
            # Use the most recent observations to create rolling windows
            all_obs = self._observations[-500:]

        # Partition observations into time windows (include tail in last window)
        if len(all_obs) < window_count:
            return {"correlations": [], "total_analyzed": len(all_obs), "message": f"Insufficient data (need >= {window_count} windows)"}

        obs_per_window = max(1, len(all_obs) // window_count)
        windows = []
        for i in range(window_count - 1):
            windows.append(all_obs[i * obs_per_window: (i + 1) * obs_per_window])
        # Last window gets all remaining observations (avoids dropping tail)
        windows.append(all_obs[(window_count - 1) * obs_per_window:])

        # Compute error-rate time series per worm type
        worm_series: dict[str, list[float]] = {}
        for wt in worm_types:
            series = []
            for window in windows:
                wt_obs = [o for o in window if o["worm_type"] == wt]
                if wt_obs:
                    error_rate = sum(1 for o in wt_obs if o["outcome"] == "error") / len(wt_obs)
                else:
                    error_rate = 0.0
                series.append(error_rate)
            worm_series[wt] = series

        # Compute Pearson correlation for each pair
        def pearson_r(x: list[float], y: list[float]) -> float:
            n = len(x)
            if n < 3:
                return 0.0
            mean_x = sum(x) / n
            mean_y = sum(y) / n
            cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
            std_x = (sum((xi - mean_x) ** 2 for xi in x)) ** 0.5
            std_y = (sum((yi - mean_y) ** 2 for yi in y)) ** 0.5
            if std_x == 0 or std_y == 0:
                return 0.0
            return cov / (std_x * std_y)

        correlations = []
        for i, wt_a in enumerate(worm_types):
            for wt_b in worm_types[i + 1:]:
                r = pearson_r(worm_series[wt_a], worm_series[wt_b])
                if abs(r) >= min_correlation:
                    direction = "positive" if r > 0 else "negative"
                    strength = ("strong" if abs(r) > 0.7 else "moderate" if abs(r) > 0.5 else "weak")
                    correlations.append({
                        "worm_a": wt_a,
                        "worm_b": wt_b,
                        "correlation": round(r, 4),
                        "direction": direction,
                        "strength": strength,
                        "interpretation": (
                            f"{wt_a} errors {'co-occur with' if r > 0 else 'are inversely related to'} {wt_b} errors"
                        ),
                    })

        # Sort by correlation magnitude
        correlations.sort(key=lambda c: -abs(c["correlation"]))

        # Generate recommendations from strong correlations
        recommendations = []
        for corr in correlations:
            if abs(corr["correlation"]) > 0.5:
                if corr["direction"] == "positive":
                    recommendations.append(
                        f"Consider joint CurativePool governance for {corr['worm_a']} and {corr['worm_b']} "
                        f"(r={corr['correlation']:.2f}) — errors propagate together"
                    )
                else:
                    recommendations.append(
                        f"Investigate inverse relationship between {corr['worm_a']} and {corr['worm_b']} "
                        f"(r={corr['correlation']:.2f}) — may indicate resource contention"
                    )

        return {
            "correlations": correlations,
            "significant_pairs": len(correlations),
            "worm_types_analyzed": len(worm_types),
            "total_windows": window_count,
            "min_correlation_threshold": min_correlation,
            "recommendations": recommendations,
            "timestamp": datetime.now().isoformat(),
        }


# -- MetaCognitionAgent (FreeThreadedAgent wrapper) -------------------------

try:
    from CORE.Agent import FreeThreadedAgent, AgentConfig
    from CORE.agent_registry import register_agent

    @register_agent(category="rl", priority=9, description="Meta-cognitive agent with self-reflection and strategy adaptation")
    class MetaCognitionAgent(FreeThreadedAgent):
        """FreeThreadedAgent wrapper for MetaCognitionWorm.

        Provides persistent meta-cognitive capabilities to any agent
        in the ecosystem through standard agent interface.
        """

        VERSION = "1.0.0"

        def __init__(self, config=None):
            super().__init__(config or AgentConfig(agent_name="MetaCognition"))
            self._mc = MetaCognitionWorm()

        def _run(self, task, pool=None):
            action = task.get("action", "introspect")
            payload = task.get("payload", task)

            if action == "observe":
                return self._mc.observe(**payload)
            elif action == "analyze":
                return self._mc.analyze_patterns(**payload)
            elif action == "adapt":
                return self._mc.adapt_strategy(**payload)
            elif action == "validate":
                return self._mc.validate_adaptation(**payload)
            elif action == "dialectic":
                return self._mc.dialectic(**payload)
            elif action == "introspect":
                return self._mc.generate_introspection_report()
            else:
                return {"error": f"Unknown meta-cognition action: {action}"}

except ImportError as exc:
    note_failure("meta_cognition:L1078", exc)
