# coding: utf-8
"""
ECOSYSTEM EXPANSION — Category: NEURAL ADVANCEMENT (2 worms)
DreamingModule — Offline simulation, scenario replay, and creative synthesis.
HypnagogicSynthesizer — Cross-domain pattern blending for novel solution generation.

Enables the Azazael ecosystem to run offline simulations ("dreaming") on
past execution data, generate novel solutions through cross-domain synthesis,
and improve policies without live environment interaction.

Part of GOSP (Gossamer Oxford Semantical Parser) DoctoralThesisEngine —
Tier 6: DREAMING — Offline simulation and creative synthesis for
continuous policy improvement.

Features:
- Offline scenario replay from LocalMemoryStore execution logs
- Dream-based policy improvement without live environment risk
- Cross-domain pattern blending (hypnagogic synthesis)
- Novel solution generation through recombination
- Dream quality scoring and selection
- CurativePool-governed dream validation
- PAMS/LocalMemoryStore integration for dream persistence

Created: June 4, 2026 — Neural Advancement Realignment Session

Scenario replay / synthesis engine; not ML.trace_consolidator (the digest
writer).
"""

import json
import time
import random
import threading
from datetime import datetime
from typing import Any, Callable, List
from collections import defaultdict
from dataclasses import dataclass, field

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# -- Module-level imports for 3.14t thread safety (Lesson #29) --
try:
    from CORE.local_memory import get_memory_store as _get_memory_store_global
    _LOCAL_MEMORY_OK = True
except ImportError as exc:
    note_failure("dreaming:L40", exc)
    _LOCAL_MEMORY_OK = False
    _get_memory_store_global = None

try:
    from CORE.worm_registry import register_worm
    _REGISTRY = True
except ImportError as exc:
    note_failure("dreaming:L47", exc)
    _REGISTRY = False
    def register_worm(category: str = "utility", priority: int = 5, cooldown_seconds: float = 30.0, description: str = "", tags=None):
        return lambda cls: cls


@dataclass
class Dream:
    """A single dream — an offline simulation episode."""
    id: str = field(default_factory=lambda: f"dream_{int(time.time()*1000)}_{random.randint(1000,9999)}")
    source_scenarios: list[str] = field(default_factory=list)
    state_sequence: list[dict] = field(default_factory=list)
    policy_suggestions: list[dict] = field(default_factory=list)
    novel_patterns: list[dict] = field(default_factory=list)
    quality_score: float = 0.0
    creativity_score: float = 0.0
    coherence_score: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    tags: list[str] = field(default_factory=list)


@register_worm(
    category="learning", priority=5, cooldown_seconds=180.0,
    description="Dreaming engine — offline simulation, scenario replay, policy improvement without live risk"
)
class DreamingModule:
    """Offline dreaming and simulation engine for continuous policy improvement.

    Operates on past execution data from LocalMemoryStore to:
    1. Replay historical scenarios in offline simulation
    2. Generate policy improvements without live environment risk
    3. Score and select high-quality dreams for policy updates
    4. Produce novel patterns through recombination

    The dreaming loop operates in 4 phases:
    RECALL -> SIMULATE -> EVALUATE -> INTEGRATE

    Usage:
        dreamer = DreamingModule()
        dreamer.recall_scenarios(source="SystemZ", event_type="monitor", limit=100)
        dreams = dreamer.dream(num_episodes=5)
        best = dreamer.evaluate_dreams(dreams)
        dreamer.integrate_best(best, policy_fn=my_policy_update)
    """

    VERSION = "1.0.0"

    def __init__(self, dream_capacity: int = 50):
        self._lock = threading.Lock()
        self._scenario_memory: list[dict] = []
        self._dreams: list[Dream] = []
        self._dream_capacity = dream_capacity
        self._integrated_count = 0
        self._dream_stats: dict[str, Any] = {
            "total_dreams": 0,
            "integrated": 0,
            "avg_quality": 0.0,
        }
        self._curative_pool = None  # Lazy CurativePool singleton

    # -- RECALL Phase: Load scenarios from memory ----------------------

    def recall_scenarios(self, source: (str | None) = None,
                         event_type: (str | None) = None,
                         limit: int = 200,
                         max_age_days: (int | None) = 7) -> dict[str, Any]:
        """Recall historical execution scenarios from LocalMemoryStore.

        Args:
            source: Filter by source component
            event_type: Filter by event type
            limit: Maximum scenarios to recall
            max_age_days: Only recall scenarios within N days

        Returns:
            dict with recall stats
        """
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            records = store.query_memories(
                source=source, event_type=event_type,
                limit=limit, max_age_days=max_age_days,
            )
        except Exception as exc:
            note_failure("dreaming:L131", exc)
            records = []

        if not records:
            return {"recalled": 0, "message": "No scenarios found"}

        # Stratify errors in (2026-09-28): the unfiltered window is ~99%
        # info, so the error->recovery miner starves on samples with zero
        # errors. A bounded error top-up guarantees material; dedup by id.
        errors_added = 0
        try:
            from CORE.local_memory import get_memory_store as _gs
            _store = _gs()
            _have = {str(r.get("id")) for r in records if r.get("id")}
            _errs = _store.query_memories(
                source=source, event_type=event_type, severity="error",
                limit=25, max_age_days=max_age_days)
            for _e in _errs:
                if str(_e.get("id")) not in _have:
                    records.append(_e)
                    _have.add(str(_e.get("id")))
                    errors_added += 1
        except Exception as exc:
            note_failure("dreaming:L153", exc)

        with self._lock:
            self._scenario_memory = records
            if len(self._scenario_memory) > 500:
                self._scenario_memory = self._scenario_memory[-500:]

        return {
            "recalled": len(records),
            "errors_added": errors_added,
            "sources": list(set(r.get("source", "unknown") for r in records)),
            "event_types": list(set(r.get("event_type", "unknown") for r in records)),
            "timestamp": datetime.now().isoformat(),
        }

    # -- DREAM Phase: Run offline simulations --------------------------

    def _anchor_error_neighbors(self, scenarios: list[dict], limit: int = 5) -> list[dict]:
        """Fetch chronological neighbors of sampled errors (stratified recall).

        For each error in scenarios, pulls the next same-source event so the
        miner sees genuine error->success adjacencies. Read-only, fails open.
        """
        anchored: list[dict] = list(scenarios)
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            errors = [s for s in scenarios if s.get("severity") == "error"][:limit]
            for err in errors:
                nxt = store.query_memories(
                    source=err.get("source"), limit=5, max_age_days=90,
                )
                for cand in nxt:
                    if cand.get("timestamp", "") > err.get("timestamp", "") and cand not in anchored:
                        anchored.append(cand)
                        break
        except Exception as exc:
            note_failure("dreaming:L190", exc)
        return anchored

    def dream(self, num_episodes: int = 5,
              scenario_count: int = 20,
              creativity_factor: float = 0.3,
              use_curative: bool = True,
              persist: bool = True) -> list[Dream]:
        """Run offline dreaming episodes on recalled scenarios.

        Each dream:
        1. Samples random scenarios from memory
        2. Replays them in simulated execution
        3. Generates policy improvement suggestions
        4. Detects novel cross-scenario patterns

        Args:
            num_episodes: Number of dreams to generate
            scenario_count: Scenarios per dream
            creativity_factor: How much random variation to inject (0.0-1.0)
            use_curative: Govern dreaming through CurativePool
            persist: Write the session to the store. GA-style evaluators
                (e.g. the DREAMTUNE track, dozens of micro-evals per run)
                pass persist=False so scoring doesn't bloat dream history
                with synthetic eval rows.

        Returns:
            list of Dream objects
        """
        if use_curative:
            self._govern_dreaming_gate("dream", {
                "num_episodes": num_episodes,
                "creativity_factor": creativity_factor,
            })

        dreams = []
        for ep in range(num_episodes):
            with self._lock:
                if not self._scenario_memory:
                    scenarios = []
                else:
                    # G12: Temporal discounting — recent scenarios get higher weight
                    # G9: Curriculum learning — sort by difficulty (error severity)
                    age_weights = []
                    for i, s in enumerate(self._scenario_memory):
                        age_frac = i / max(1, len(self._scenario_memory))
                        recency = 0.5 + 0.5 * age_frac  # recent = higher weight
                        severity_boost = 1.5 if s.get("severity") == "error" else 1.0
                        age_weights.append(recency * severity_boost)
                    total_w = sum(age_weights) or 1.0
                    probs = [w / total_w for w in age_weights]
                    indices = []
                    n = min(scenario_count, len(self._scenario_memory))
                    # Weighted sampling without replacement
                    available = list(range(len(self._scenario_memory)))
                    for _ in range(n):
                        if not available:
                            break
                        avail_probs = [probs[i] for i in available]
                        p_sum = sum(avail_probs) or 1.0
                        r = random.random() * p_sum
                        cum = 0.0
                        chosen_idx = 0
                        for j, idx in enumerate(available):
                            cum += avail_probs[j]
                            if cum >= r:
                                chosen_idx = j
                                break
                        indices.append(available.pop(chosen_idx))
                    scenarios = [self._scenario_memory[i] for i in indices]
                    scenarios = self._anchor_error_neighbors(scenarios)

            # Build state sequence from scenarios
            state_sequence = self._build_state_sequence(scenarios, creativity_factor)

            # Generate policy suggestions
            policy_suggestions = self._generate_policy_suggestions(
                scenarios, state_sequence, creativity_factor
            )

            # Detect novel cross-scenario patterns
            novel_patterns = self._detect_novel_patterns(
                scenarios, policy_suggestions
            )

            # Score the dream
            quality = self._score_dream_quality(state_sequence, policy_suggestions, novel_patterns)
            creativity = self._score_dream_creativity(policy_suggestions, novel_patterns)
            coherence = self._score_dream_coherence(state_sequence)

            dream = Dream(
                source_scenarios=[s.get("id", f"scenario_{i}") for i, s in enumerate(scenarios)],
                state_sequence=state_sequence,
                policy_suggestions=policy_suggestions,
                novel_patterns=novel_patterns,
                quality_score=quality,
                creativity_score=creativity,
                coherence_score=coherence,
                tags=self._extract_tags(scenarios),
            )

            dreams.append(dream)

        # Store dreams
        with self._lock:
            self._dreams.extend(dreams)
            self._dream_stats["total_dreams"] += len(dreams)
            if len(self._dreams) > self._dream_capacity:
                self._dreams = self._dreams[-self._dream_capacity:]

        # Persist dreaming session (skipped for no-write GA evals)
        if persist:
            self._persist_dreaming_session(dreams)

        return dreams

    def _build_state_sequence(self, scenarios: list[dict],
                               creativity_factor: float) -> list[dict]:
        """Build a state transition sequence from scenarios."""
        sequence = []
        for i, scenario in enumerate(scenarios):
            data = scenario.get("data", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except (json.JSONDecodeError, TypeError) as exc:
                    note_failure("dreaming:L316", exc)
                    data = {}

            severity = scenario.get("severity", "info")
            source = scenario.get("source", "unknown")

            # Simulate state transition with creativity injection
            next_state = {
                "step": i,
                "source": source,
                "severity": severity,
                "state_vector": self._extract_state_vector(data),
                "transition_confidence": 1.0 - creativity_factor * random.random(),
                "simulated_outcome": "success" if random.random() > 0.2 * creativity_factor else "error",
            }
            sequence.append(next_state)

        return sequence

    def _extract_state_vector(self, data: dict) -> list[float]:
        """Extract a numeric state vector from scenario data."""
        vector = []
        if isinstance(data, dict):  # type: ignore[reportUnnecessaryIsInstance]
            for v in data.values():
                if isinstance(v, (int, float)):
                    vector.append(float(v))
                elif isinstance(v, bool):
                    vector.append(1.0 if v else 0.0)
                elif isinstance(v, str) and v.replace(".", "", 1).isdigit():
                    vector.append(float(v))
        if not vector:
            vector = [random.random()]
        return vector[:10]  # Cap at 10 features

    def _generate_policy_suggestions(self, scenarios: list[dict],
                                      state_sequence: list[dict],
                                      creativity_factor: float) -> list[dict]:
        """Generate data-driven policy improvement suggestions from scenarios.

        Unlike random/synthetic suggestions, this method mines actual
        error-then-success patterns from:
        1. Scenario sequences where error was followed by success
        2. Historical error-recovery patterns from LocalMemoryStore
        3. Cross-source pattern analysis

        Args:
            scenarios: Historical scenario records
            state_sequence: Simulated state transitions from dreaming
            creativity_factor: Inject creative variation into suggestions

        Returns:
            list of evidence-based policy suggestion dicts
        """
        suggestions = []

        # -- 1. Mine error->success sequences from scenarios --
        error_success_pairs = self._mine_error_recovery_patterns(scenarios)
        for es in error_success_pairs[:5]:
            suggestions.append({
                "type": "error_mitigation",
                "source": es.get("source", "unknown"),
                "worm": es.get("worm"),
                "nature": es.get("nature"),
                "error_event": es.get("error", ""),
                "recovery_action": es.get("recovery", "retry_with_backoff"),
                "confidence": round(min(0.95, 0.5 + es.get("evidence_count", 1) * 0.1), 3),
                "rationale": (
                    f"Observed {es.get('evidence_count', 1)} cases where {es.get('recovery', 'retry')} "
                    f"resolved '{es.get('error', 'unknown')[:60]}' errors from {es.get('source', 'unknown')}"
                ),
                "evidence_count": es.get("evidence_count", 1),
            })

        # -- 2. Mine historical error-recovery from LocalMemoryStore --
        historical_patterns = self._mine_historical_recovery_patterns()
        for hp in historical_patterns[:3]:
            if not any(s.get("error_event") == hp.get("error", "") for s in suggestions):
                suggestions.append({
                    "type": "historical_insight",
                    "source": hp.get("source", "unknown"),
                    "error_event": hp.get("error", ""),
                    "recovery_action": hp.get("recovery_strategy", "retry_with_backoff"),
                    "confidence": round(hp.get("confidence", 0.6), 3),
                    "rationale": (
                        f"Historical: {hp.get('recovery_strategy', 'retry')} succeeded in "
                        f"{hp.get('success_rate', 0):.0%} of past {hp.get('source', 'unknown')} recoveries"
                    ),
                    "evidence_count": hp.get("total_attempts", 1),
                })

        # -- 3. Creative strategy exploration -------------------------------
        # Low creativity must REDUCE exploration, not eliminate it: if the
        # gate above is the only suggestion source (no error records to mine)
        # then conservative/defensive tuning would silence every suggestion
        # and the loop could never adapt away from them. Fire whenever novel
        # strategies exist; creativity scales how many get proposed and their
        # confidence (conservative -> 1 tepid suggestion, aggressive -> 2 bold).
        strategies = ["conservative", "adaptive", "aggressive", "batched", "staged"]
        # Bias toward strategies that worked in data
        used_strategies = {s.get("recovery_action", "") for s in suggestions}
        novel_strategies = [s for s in strategies if s not in used_strategies]
        if novel_strategies:
            n_propose = min(2, max(1, round(creativity_factor * 4)), len(novel_strategies))
            suggestions.append({
                "type": "strategy_exploration",
                "strategies": random.sample(novel_strategies, n_propose),
                "confidence": round(max(0.05, creativity_factor) * random.uniform(0.2, 0.5), 3),
                "rationale": (
                    f"Exploring untried strategies ({', '.join(novel_strategies[:2])}) "
                    f"during offline dreaming — safe to test without live impact"
                ),
            })

        # -- 4. Pattern-grounded guidance from distilled knowledge --
        try:
            from CORE.pattern_memory import query_patterns
            srcs = sorted({s.get("source", "") for s in scenarios if s.get("source")})
            qtext = "error recovery policy " + " ".join(srcs[:3])
            for hit in query_patterns(qtext, top_k=2):
                md = hit.get("metadata", {}) or {}
                suggestions.append({
                    "type": "pattern_grounded",
                    "source": "pattern_memory",
                    "pattern": md.get("key", "") or hit.get("pattern_type", ""),
                    "confidence": round(hit.get("confidence", 0.5), 3),
                    "rationale": (
                        f"Distilled knowledge (score {hit.get('score', 0)}): "
                        f"{(md.get('text', '') or json.dumps(md))[:140]}"
                    ),
                })
        except Exception as exc:
            note_failure("dreaming:L446", exc)

        return suggestions
    def _mine_error_recovery_patterns(self, scenarios: list[dict]) -> list[dict]:
        """Mine error->success sequences from scenario data.

        Analyzes chronological scenario sequences to find pairs where
        an error event was followed within a short window by a successful
        event from the same source, suggesting an effective recovery
        strategy. Non-adjacent by design (2026-09-28): requiring list
        adjacency lost pairs to unrelated interleaved records, so each
        error scans forward for its first same-source success.

        Returns:
            list of dicts with source, worm, nature, error, recovery,
            evidence_count. worm/nature are the ORGAN (from the error's
            data), distinct from source (the emitter).
        """
        if len(scenarios) < 2:
            return []

        # Sort scenarios by timestamp if available
        sorted_scenarios = sorted(
            scenarios,
            key=lambda s: s.get("timestamp", s.get("created_at", "")),
        )

        def _data(ev: dict) -> dict:
            d = ev.get("data", {})
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except (json.JSONDecodeError, TypeError) as exc:
                    note_failure("dreaming:L479", exc)
                    d = {}
            return d if isinstance(d, dict) else {}

        def _ts(ev: dict) -> float:
            for key in ("timestamp", "created_at"):
                raw = ev.get(key)
                if raw:
                    try:
                        from datetime import datetime as _dt
                        return _dt.fromisoformat(str(raw)).timestamp()
                    except (ValueError, TypeError) as exc:
                        note_failure("dreaming:L490", exc)
                        continue
            return 0.0

        # 5-minute pairing window: recovery follows failure promptly; a
        # same-source success hours later is a different story.
        WINDOW_S = 300.0
        recovery_patterns: dict[tuple[str, str], dict] = {}

        for i, current in enumerate(sorted_scenarios):
            if current.get("severity", "") != "error":
                continue
            curr_source = current.get("source", "unknown")
            curr_data = _data(current)
            t0 = _ts(current)
            for nxt in sorted_scenarios[i + 1:]:
                if nxt.get("source", "unknown") != curr_source:
                    continue
                if nxt.get("severity", "") != "info":
                    continue
                if t0 and _ts(nxt) - t0 > WINDOW_S:
                    break
                next_data = _data(nxt)
                error_msg = curr_data.get("error", curr_data.get("msg", "unknown")) or "unknown"
                recovery_action = next_data.get("action", next_data.get("strategy", "retry_with_backoff")) or "retry_with_backoff"
                key = (curr_source, error_msg[:800])
                if key not in recovery_patterns:
                    recovery_patterns[key] = {
                        "source": curr_source,
                        "worm": curr_data.get("worm"),
                        "nature": curr_data.get("nature"),
                        "error": error_msg,
                        "recovery": recovery_action,
                        "evidence_count": 1,
                    }
                else:
                    recovery_patterns[key]["evidence_count"] += 1
                    recovery_patterns[key]["recovery"] = recovery_action  # Latest recovery
                break

        return sorted(
            recovery_patterns.values(),
            key=lambda p: -p["evidence_count"],
        )

    def _mine_historical_recovery_patterns(self) -> list[dict]:
        """Query LocalMemoryStore for historical error-recovery patterns.

        Looks for sequences where errors were followed by successful retries
        across the entire execution history, not just current session.

        Returns:
            list of dicts with source, error, recovery_strategy, confidence, success_rate
        """
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()

            # Query recent errors
            errors = store.query_memories(
                severity="error", limit=100, max_age_days=30,
            )
            if not errors:
                return []

            # For each error source, find subsequent info events (recoveries)
            recovery_stats: dict[str, dict] = {}
            for err in errors:
                source = err.get("source", "unknown")
                err_data = err.get("data", {})
                if isinstance(err_data, str):
                    try:
                        err_data = json.loads(err_data)
                    except (json.JSONDecodeError, TypeError) as exc:
                        note_failure("dreaming:L563", exc)
                        err_data = {}
                error_msg = err_data.get("error", err_data.get("msg", "")) if isinstance(err_data, dict) else str(err_data)

                key = f"{source}:{error_msg[:80]}"
                if key not in recovery_stats:
                    recovery_stats[key] = {
                        "source": source,
                        "error": error_msg,
                        "total_errors": 0,
                        "recoveries": 0,
                        "strategies": defaultdict(int),
                    }
                recovery_stats[key]["total_errors"] += 1

            # Query subsequent successes for same sources
            successes = store.query_memories(
                severity="info", limit=200, max_age_days=30,
            )
            for suc in successes:
                source = suc.get("source", "unknown")
                suc_data = suc.get("data", {})
                if isinstance(suc_data, str):
                    try:
                        suc_data = json.loads(suc_data)
                    except (json.JSONDecodeError, TypeError) as exc:
                        note_failure("dreaming:L588", exc)
                        suc_data = {}
                action = suc_data.get("action", suc_data.get("strategy", "")) if isinstance(suc_data, dict) else ""

                for key, stats in recovery_stats.items():
                    if stats["source"] == source and action:
                        stats["recoveries"] += 1
                        stats["strategies"][action] += 1

            # Build results
            results = []
            for key, stats in recovery_stats.items():
                if stats["recoveries"] > 0 and stats["total_errors"] > 0:
                    best_strategy = max(stats["strategies"], key=stats["strategies"].get) if stats["strategies"] else "retry_with_backoff"
                    success_rate = stats["recoveries"] / stats["total_errors"]
                    results.append({
                        "source": stats["source"],
                        "error": stats["error"],
                        "recovery_strategy": best_strategy,
                        "confidence": round(min(0.9, 0.4 + success_rate * 0.5), 3),
                        "success_rate": round(success_rate, 3),
                        "total_attempts": stats["total_errors"],
                    })

            return sorted(results, key=lambda r: -r["confidence"])[:5]
        except Exception as exc:
            note_failure("dreaming:L613", exc)
            return []

    def _detect_novel_patterns(self, scenarios: list[dict],
                                policy_suggestions: list[dict]) -> list[dict]:
        """Detect novel cross-scenario patterns through recombination."""
        patterns = []

        # Cross-source pattern detection
        sources = list(set(s.get("source", "unknown") for s in scenarios))
        if len(sources) >= 2:
            patterns.append({
                "type": "cross_source",
                "sources": sources,
                "pattern": f"Cross-source pattern between {sources[0]} and {sources[1]}",
                "novelty": round(random.uniform(0.3, 0.9), 3),
            })

        # Sequence pattern detection
        if len(scenarios) >= 3:
            patterns.append({
                "type": "sequence_pattern",
                "length": len(scenarios),
                "pattern": "Recurring state transition pattern detected in dream",
                "novelty": round(random.uniform(0.2, 0.7), 3),
            })

        return patterns

    def _score_dream_quality(self, state_sequence: list[dict],
                              policy_suggestions: list[dict],
                              novel_patterns: list[dict]) -> float:
        """Score the overall quality of a dream."""
        # State coverage: more states = better
        state_score = min(len(state_sequence) / 20.0, 1.0)

        # Policy richness: more suggestions = better (to a point)
        policy_score = min(len(policy_suggestions) / 5.0, 1.0)

        # Novelty value
        novelty_score = min(len(novel_patterns) / 3.0, 1.0)

        return round(0.4 * state_score + 0.3 * policy_score + 0.3 * novelty_score, 4)

    def _score_dream_creativity(self, policy_suggestions: list[dict],
                                 novel_patterns: list[dict]) -> float:
        """Score the creativity of a dream."""
        creativity = 0.0
        for s in policy_suggestions:
            if s.get("type") == "strategy_exploration":
                creativity += 0.3
        for p in novel_patterns:
            creativity += p.get("novelty", 0.0) * 0.5
        return round(min(creativity, 1.0), 4)

    def _score_dream_coherence(self, state_sequence: list[dict]) -> float:
        """Score the internal coherence of a dream's state sequence."""
        if not state_sequence:
            return 0.0

        # Check for consistent source transitions
        sources = [s.get("source", "") for s in state_sequence]
        unique_sources = len(set(sources))
        if unique_sources == 1:
            return 0.9  # High coherence for single-source dreams
        return round(max(0.3, 1.0 - unique_sources * 0.15), 4)

    def _extract_tags(self, scenarios: list[dict]) -> list[str]:
        """Extract meaningful tags from scenarios."""
        tags = []
        sources = set(s.get("source", "") for s in scenarios if s.get("source"))
        severities = set(s.get("severity", "") for s in scenarios if s.get("severity"))
        tags.extend(["dream", f"sources:{len(sources)}"])
        if "error" in severities:
            tags.append("error_learning")
        if len(sources) >= 3:
            tags.append("cross_domain")
        return tags

    # -- EVALUATE Phase: Score and rank dreams -------------------------

    def evaluate_dreams(self, dreams: (list[Dream] | None) = None) -> dict[str, Any]:
        """Evaluate and rank dreams by quality, creativity, and coherence.

        Args:
            dreams: Dreams to evaluate (default: all stored dreams)

        Returns:
            dict with ranked dreams and summary stats
        """
        target = dreams if dreams is not None else self._dreams
        if not target:
            return {"ranked": [], "best": None, "count": 0}

        # Compute composite score
        scored = []
        for dream in target:
            composite = (
                0.4 * dream.quality_score
                + 0.3 * dream.creativity_score
                + 0.3 * dream.coherence_score
            )
            scored.append((dream, composite))

        # Sort by composite score descending
        ranked = sorted(scored, key=lambda x: -x[1])

        best = ranked[0][0] if ranked else None

        return {
            "ranked": [
                {
                    "dream_id": d.id,
                    "quality": d.quality_score,
                    "creativity": d.creativity_score,
                    "coherence": d.coherence_score,
                    "composite": round(comp, 4),
                    "tags": d.tags,
                }
                for d, comp in ranked
            ],
            "best": {
                "dream_id": best.id,
                "quality": best.quality_score,
                "creativity": best.creativity_score,
                "coherence": best.coherence_score,
                "policy_suggestions": len(best.policy_suggestions),
                "novel_patterns": len(best.novel_patterns),
            } if best else None,
            "count": len(ranked),
            "avg_quality": round(sum(d.quality_score for d in target) / len(target), 4),
        }

    # -- INTEGRATE Phase: Apply best dreams to policies ----------------

    def integrate_best(self, dream: Dream,
                       policy_fn: (Callable | None) = None) -> dict[str, Any]:
        """Integrate the best dream's insights into active policies.

        Args:
            dream: The dream to integrate
            policy_fn: Optional callback(suggestions, patterns) to update policies

        Returns:
            dict with integration results
        """
        policy_result = None
        if policy_fn:
            try:
                policy_result = policy_fn(dream.policy_suggestions, dream.novel_patterns)
            except Exception as e:
                note_failure("dreaming:L763", e)
                return {"error": f"Policy integration failed: {e}"}

        with self._lock:
            self._integrated_count += 1
            self._dream_stats["integrated"] = self._integrated_count

        # Update average quality
        with self._lock:
            if self._dreams:
                avg_q = sum(d.quality_score for d in self._dreams) / len(self._dreams)
                self._dream_stats["avg_quality"] = round(avg_q, 4)

        # Persist integration
        self._persist_integration(dream)

        return {
            "dream_id": dream.id,
            "integrated": True,
            "total_integrated": self._integrated_count,
            "quality": dream.quality_score,
            "suggestions_applied": len(dream.policy_suggestions),
            "patterns_applied": len(dream.novel_patterns),
            "policy_result": policy_result,
            "timestamp": datetime.now().isoformat(),
        }

    # -- Full Dreaming Pipeline ----------------------------------------

    def run_dreaming_pipeline(self, source: (str | None) = None,
                               event_type: (str | None) = None,
                               num_episodes: int = 5,
                               scenario_count: int = 20,
                               creativity_factor: float = 0.3,
                               auto_integrate: bool = True,
                               policy_fn=None,
                               steer_by_strategy: bool = True) -> dict[str, Any]:
        """Run the complete RECALL->DREAM->EVALUATE->INTEGRATE pipeline.

        Args:
            source: Filter scenarios by source
            event_type: Filter scenarios by event type
            num_episodes: Number of dreams to generate
            scenario_count: Scenarios per dream
            creativity_factor: Creative variation factor
            auto_integrate: Integrate best dream automatically

        Returns:
            dict with full pipeline results
        """
        start = time.perf_counter()

        # STEER: apply the active strategy to dream intensity so recorded
        # policy (adapt_strategy) changes real behavior even when callers
        # invoke the pipeline directly with fixed parameters.
        steering = None
        if steer_by_strategy:
            try:
                from CORE.WORMS.expansion.meta_cognition import MetaCognitionWorm
                tuning, active_strategy = MetaCognitionWorm().dream_tuning_for(source or "global")
            except Exception as exc:
                note_failure("dreaming:L823", exc)
                tuning, active_strategy = {}, None
            if tuning:
                num_episodes = tuning.get("num_episodes", num_episodes)
                scenario_count = tuning.get("scenario_count", scenario_count)
                creativity_factor = tuning.get("creativity_factor", creativity_factor)
                steering = {"worm": source or "global",
                            "strategy": active_strategy,
                            "params": dict(tuning)}
                self._record_steering(steering)

        # RECALL
        recall_result = self.recall_scenarios(
            source=source, event_type=event_type, limit=scenario_count * num_episodes
        )

        # DREAM (isolated: a failed generation must not kill the pipeline)
        dream_error = None
        try:
            dreams = self.dream(
                num_episodes=num_episodes,
                scenario_count=scenario_count,
                creativity_factor=creativity_factor,
            )
        except Exception as e:
            note_failure("dreaming:L847", e)
            dreams = []
            dream_error = str(e)[:200]

        # EVALUATE
        eval_result = self.evaluate_dreams(dreams)

        # Default policy channel: route suggestions through the dream
        # policy bridge unless the caller supplied one.
        if policy_fn is None:
            try:
                from CORE.dream_policy_bridge import apply_dream_policy
                policy_fn = apply_dream_policy
            except Exception as exc:
                note_failure("dreaming:L860", exc)
                policy_fn = None

        # INTEGRATE
        integration = None
        if auto_integrate and eval_result.get("best"):
            best_dream = None
            for d in dreams:
                if d.id == eval_result["best"]["dream_id"]:
                    best_dream = d
                    break
            if best_dream:
                integration = self.integrate_best(best_dream, policy_fn=policy_fn)

        duration_ms = (time.perf_counter() - start) * 1000

        return {
            "version": self.VERSION,
            "pipeline": "RECALL->DREAM->EVALUATE->INTEGRATE",
            "recall": recall_result,
            "dream_count": len(dreams),
            "evaluation": eval_result,
            "integration": integration,
            "steering": steering,
            "errors": ([dream_error] if dream_error else []),
            "duration_ms": round(duration_ms, 2),
            "timestamp": datetime.now().isoformat(),
        }

    # -- Governance & Persistence --------------------------------------

    def _govern_dreaming_gate(self, phase: str, context: dict) -> None:
        """Govern dreaming operations through CurativePool — double-checked locking for 3.14t."""
        try:
            if self._curative_pool is None:
                with self._lock:
                    if self._curative_pool is None:
                        from META.MetaWormSpawner import CurativePool
                        self._curative_pool = CurativePool()
            result = self._curative_pool.evaluate_action(
                {"context": f"dreaming_{phase}", "params": context},
                {"action_type": "execute", "output_size": 100, "feature": f"dreaming_{phase}"},
                [{"role": "user", "content": f"run dreaming phase: {phase}"}],
                [{"id": "dream_analyst", "archetype": "The Artist"}],
            )
            if result["decision"] == "blocked":
                print(f"[DreamingModule] CurativePool blocked {phase}: {result['block_reasons']}")
        except ImportError as exc:
            note_failure("dreaming:L907", exc)

    def _persist_dreaming_session(self, dreams: list[Dream]) -> None:
        """Persist dreaming session to LocalMemoryStore.

        Skips substance-free dreams (no suggestions and no patterns) so
        failed generations don't become permanent noise.
        """
        dreams = [d for d in dreams
                  if d.policy_suggestions or d.novel_patterns]
        if not dreams:
            return
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            for dream in dreams:
                store.record_event(
                    source="DreamingModule",
                    event_type="dream_generated",
                    data={
                        "dream_id": dream.id,
                        "quality": dream.quality_score,
                        "creativity": dream.creativity_score,
                        "coherence": dream.coherence_score,
                        "policy_suggestions": len(dream.policy_suggestions),
                        "novel_patterns": len(dream.novel_patterns),
                        "tags": dream.tags,
                    },
                    severity="info",
                    tags=dream.tags,
                )
        except Exception as exc:
            note_failure("dreaming:L939", exc)

    def _persist_integration(self, dream: Dream) -> None:
        """Persist dream integration to LocalMemoryStore."""
        try:
            from CORE.local_memory import get_memory_store
            store = get_memory_store()
            store.record_event(
                source="DreamingModule",
                event_type="dream_integrated",
                data={
                    "dream_id": dream.id,
                    "quality": dream.quality_score,
                    "creativity": dream.creativity_score,
                    "integrated_count": self._integrated_count,
                },
                severity="info",
                tags=["dream", "integration"],
            )
        except Exception as exc:
            note_failure("dreaming:L959", exc)

    def get_dream_stats(self) -> dict[str, Any]:
        """Get dreaming statistics."""
        with self._lock:
            return dict(self._dream_stats)

    def execute(self) -> dict[str, Any]:
        """Standard worm execute — runs the dreaming pipeline; the
        pipeline itself applies active-strategy steering."""
        return self.run_dreaming_pipeline(num_episodes=3, scenario_count=10)

    def _record_steering(self, steering: dict) -> None:
        """Persist a strategy_steered event for observability."""
        try:
            from CORE.local_memory import get_memory_store
            get_memory_store().record_event(
                source="DreamingModule",
                event_type="strategy_steered",
                data=steering,
                severity="info",
                tags=["dream", "steering", steering.get("strategy", "")],
            )
        except Exception as exc:
            note_failure("dreaming:L983", exc)
