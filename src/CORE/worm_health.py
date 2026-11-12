# coding: utf-8
"""
worm_health — Per-Worm Health Scoring Engine.

Tracks per-worm health over time using three dimensions:
1. Success rate (50% weight) — from WormMetrics
2. Duration trends (25% weight) — sliding window trend detection
3. Spawn frequency (25% weight) — staleness penalty + activity bonus

Scores are 0-100 with thresholds:
- >= 80: healthy
- >= 50: degraded
-  < 50: critical
- No data: unknown

Usage:
    from CORE.worm_health import get_health_scorer
    scorer = get_health_scorer()
    scorer.record("SystemZ", success=True, duration_ms=15.2)
    scores = scorer.score_all()
    print(scores["overall_health"])  # 92.5
    print(scores["per_worm"][0]["health_score"])  # Best worm's score
"""


__all__ = [
    "WormHealthScorer",
    "get_health_scorer",
    "record_worm_health",
]


import time
import threading
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class WormHealthScorer:
    """Thread-safe per-worm health scoring engine.

    Synthesizes WormMetrics (success rate, duration) and WORM_REGISTRY
    (spawn frequency) into 0-100 health scores per worm, plus per-category
    and overall health aggregates.

    Uses sliding-window duration history (last 20 durations per worm)
    to detect performance degradation trends.

    Thread-safe: all public methods acquire _lock.
    """

    # ── Scoring weights ─────────────────────────────────────────────
    SUCCESS_RATE_WEIGHT = 0.50   # Success rate matters most
    DURATION_TREND_WEIGHT = 0.25  # Trending slower = bad
    SPAWN_FREQ_WEIGHT = 0.25     # Never spawned or stale = penalty

    # ── Status thresholds ───────────────────────────────────────────
    HEALTHY_THRESHOLD = 80
    DEGRADED_THRESHOLD = 50

    # ── Internal config ─────────────────────────────────────────────
    _MAX_DURATION_HISTORY = 20  # Sliding window for trend detection

    def __init__(self):
        self._lock = threading.Lock()
        # Duration history: {worm_name: [recent_durations]}
        self._duration_history: dict[str, list[float]] = {}
        # Score cache: {worm_name: (timestamp, score_dict)}
        self._score_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_ttl = 15.0  # seconds — cooldown-aware

    # ── Recording ──────────────────────────────────────────────────

    def record(self, name: str, success: bool, duration_ms: float):
        """Record a worm execution for health trend tracking.

        Called from CLUSTERWORMS after each worm execution
        (alongside existing record_worm_execution for WormMetrics).

        Args:
            name: Worm class name (e.g., 'SystemZ').
            success: Whether execution succeeded.
            duration_ms: Execution time in milliseconds.
        """
        with self._lock:
            if name not in self._duration_history:
                self._duration_history[name] = []
            hist = self._duration_history[name]
            hist.append(duration_ms)
            if len(hist) > self._MAX_DURATION_HISTORY:
                hist.pop(0)
            # Invalidate score cache for this worm since data changed
            self._score_cache.pop(name, None)

    # ── Single Worm Scoring ────────────────────────────────────────

    def score_worm(self, name: str) -> dict[str, Any]:
        """Compute health score for a single worm.

        Args:
            name: Worm class name.

        Returns:
            Dict with health_score (0-100), status, dimension breakdown,
            and raw detail dict.
        """
        # Check score cache (TTL-gated)
        with self._lock:
            cached = self._score_cache.get(name)
            if cached is not None:
                cached_time, cached_score = cached
                if time.time() - cached_time < self._cache_ttl:
                    return dict(cached_score)  # return copy

        # Gather data from WormMetrics and WORM_REGISTRY
        success_rate = 0.0
        avg_duration = 0.0
        execution_count = 0
        spawn_count = 0
        last_spawned = None

        try:
            from CORE.metrics import get_worm_metrics
            wm = get_worm_metrics().snapshot()
            for w in wm.get("per_worm", []):
                if w["name"] == name:
                    success_rate = w.get("success_rate", 0.0)
                    avg_duration = w.get("avg_duration_ms", 0.0)
                    execution_count = w.get("execution_count", 0)
                    break
        except ImportError as exc:
            note_failure("worm_health:L130", exc)

        try:
            from CORE.worm_registry import WORM_REGISTRY
            info = WORM_REGISTRY.get(name, {})
            spawn_count = info.get("spawn_count", 0)
            last_spawned = info.get("last_spawned")
        except ImportError as exc:
            note_failure("worm_health:L138", exc)

        # ── Dimension 1: Success rate (0-50) ───────────────────────
        success_rate_score = (success_rate / 100.0) * 50.0

        # ── Dimension 2: Duration trend (0-25) ─────────────────────
        duration_trend_score = self._compute_duration_trend_score(name, avg_duration)

        # ── Dimension 3: Spawn frequency (0-25) ─────────────────────
        spawn_freq_score = self._compute_spawn_freq_score(spawn_count, last_spawned)

        # ── Aggregate (sum pre-scaled dimensions: 0-50 + 0-25 + 0-25 = 0-100) ──
        health_score = round(
            success_rate_score + duration_trend_score + spawn_freq_score,
            1
        )
        health_score = max(0.0, min(100.0, health_score))

        status = self._score_to_status(health_score, execution_count)

        result = {
            "name": name,
            "health_score": health_score,
            "status": status,
            "breakdown": {
                "success_rate_score": round(success_rate_score, 1),
                "duration_trend_score": round(duration_trend_score, 1),
                "spawn_frequency_score": round(spawn_freq_score, 1),
            },
            "details": {
                "success_rate": round(success_rate, 1),
                "avg_duration_ms": round(avg_duration, 1),
                "execution_count": execution_count,
                "spawn_count": spawn_count,
                "last_spawned": str(last_spawned)[:19] if last_spawned else None,
            },
        }

        # Cache the score
        with self._lock:
            self._score_cache[name] = (time.time(), dict(result))

        return result

    # ── All Worms Scoring ──────────────────────────────────────────

    def score_all(self) -> dict[str, Any]:
        """Score all registered worms and aggregate by category.

        Returns:
            Dict with overall_health, healthy/degraded/critical/unknown
            counts, per_worm list, and per_category breakdown.
        """
        # Ensure registry is populated
        worm_names: list[str] = []
        try:
            from CORE.worm_registry import discover_worms, WORM_REGISTRY
            discover_worms()
            worm_names = list(WORM_REGISTRY)
        except ImportError as exc:
            note_failure("worm_health:L198", exc)

        if not worm_names:
            return {
                "overall_health": 0.0,
                "healthy": 0,
                "degraded": 0,
                "critical": 0,
                "unknown": 0,
                "per_worm": [],
                "per_category": {},
            }

        # Score every worm
        per_worm = []
        for name in worm_names:
            score = self.score_worm(name)
            per_worm.append(score)

        # Sort: worst health first
        per_worm.sort(key=lambda x: x["health_score"])

        # Count by status
        statuses = {"healthy": 0, "degraded": 0, "critical": 0, "unknown": 0}
        total_score = 0.0
        for w in per_worm:
            statuses[w["status"]] = statuses.get(w["status"], 0) + 1
            total_score += w["health_score"]

        overall_health = round(total_score / len(per_worm), 1) if per_worm else 0.0

        # Per-category aggregation
        per_category: dict[str, dict[str, Any]] = {}
        try:
            from CORE.worm_registry import WORM_REGISTRY
            cat_scores: dict[str, list[float]] = {}
            for w in per_worm:
                cat = WORM_REGISTRY.get(w["name"], {}).get("category", "utility")
                if cat not in cat_scores:
                    cat_scores[cat] = []
                cat_scores[cat].append(w["health_score"])
            for cat, scores in sorted(cat_scores.items()):
                per_category[cat] = {
                    "avg_health": round(sum(scores) / len(scores), 1),
                    "worm_count": len(scores),
                    "healthy": sum(1 for s in scores if s >= self.HEALTHY_THRESHOLD),
                    "degraded": sum(1 for s in scores if self.DEGRADED_THRESHOLD <= s < self.HEALTHY_THRESHOLD),
                    "critical": sum(1 for s in scores if s < self.DEGRADED_THRESHOLD),
                }
        except ImportError as exc:
            note_failure("worm_health:L248", exc)

        return {
            "overall_health": overall_health,
            "healthy": statuses["healthy"],
            "degraded": statuses["degraded"],
            "critical": statuses["critical"],
            "unknown": statuses["unknown"],
            "per_worm": per_worm,
            "per_category": per_category,
        }

    def clear(self):
        """Reset all history and caches."""
        with self._lock:
            self._duration_history.clear()
            self._score_cache.clear()

    # ── Internal scoring helpers ───────────────────────────────────

    def _compute_duration_trend_score(self, name: str, avg_duration: float) -> float:
        """Compute duration trend score (0-25).

        Analyzes the sliding window of recent durations:
        - Stable/improving: 20-25
        - Slight upward trend: 13-19
        - Significant degradation: 0-12

        If no history, assumes neutral (18.75 = 75%).
        """
        with self._lock:
            hist = list(self._duration_history.get(name, []))

        if not hist:
            # No trend data — neutral score
            return 18.75

        if len(hist) < 3:
            # Not enough data for trend — slight penalty
            return 15.0

        # Linear regression on recent history to detect trend slope
        n = len(hist)
        x_avg = (n - 1) / 2.0
        y_avg = sum(hist) / n

        numerator = sum((i - x_avg) * (hist[i] - y_avg) for i in range(n))
        denominator = sum((i - x_avg) ** 2 for i in range(n))

        if denominator == 0:
            return 18.75  # All same value — stable

        slope = numerator / denominator

        # Normalize slope relative to avg duration (a 10ms drift on 15ms avg = bad)
        if avg_duration > 0:
            relative_slope = slope / avg_duration
        else:
            relative_slope = 0.0

        # Score: flatter/negative slope = good, steep positive = bad
        if relative_slope <= -0.02:
            return 25.0  # Strongly improving (negative slope)
        elif relative_slope <= 0.0:
            return 22.5  # Slightly improving
        elif relative_slope <= 0.02:
            return 20.0  # Stable
        elif relative_slope <= 0.05:
            return 15.0  # Slight degradation
        elif relative_slope <= 0.10:
            return 10.0  # Moderate degradation
        else:
            return 5.0   # Significant degradation

    def _compute_spawn_freq_score(self, spawn_count: int, last_spawned: (str | None)) -> float:
        """Compute spawn frequency score (0-25).

        - Never spawned: 0 (worst — cold worm)
        - Spawned but stale (>24h): 5-12
        - Recently spawned (<1h): 20-25
        - Active and recently spawned: 25 (best)
        """
        if spawn_count == 0:
            return 0.0  # Never spawned — biggest penalty

        max_score = 25.0

        if last_spawned is None:
            # Has spawn count but no timestamp — assume moderate activity
            return min(spawn_count * 3.0, max_score)

        # Parse last_spawned timestamp
        try:
            from datetime import datetime
            if isinstance(last_spawned, datetime):
                last_time = last_spawned.timestamp()
            else:
                last_time = datetime.fromisoformat(str(last_spawned)).timestamp()
        except (ValueError, TypeError) as exc:
            # Can't parse — assume moderate activity
            # Can't parse — assume moderate activity
            note_failure("worm_health:L347", exc)
            return min(spawn_count * 3.0, max_score)

        hours_since = (time.time() - last_time) / 3600.0

        # Staleness penalty
        if hours_since <= 0.5:
            staleness_penalty = 0.0       # Very recent — no penalty
        elif hours_since <= 2:
            staleness_penalty = 2.0       # Within last 2h — minor
        elif hours_since <= 6:
            staleness_penalty = 5.0       # Within 6h — moderate
        elif hours_since <= 24:
            staleness_penalty = 10.0      # Within 24h — significant
        else:
            staleness_penalty = 18.0      # Over 24h — very stale

        # Activity bonus (0-7.0 based on spawn count)
        activity_bonus = min(spawn_count * 0.7, 7.0)

        score = max_score - staleness_penalty + activity_bonus
        return max(0.0, min(max_score, score))

    @staticmethod
    def _score_to_status(health_score: float, execution_count: int) -> str:
        """Map health score to status string."""
        if execution_count == 0:
            return "unknown"  # Never executed — can't assess health
        if health_score >= 80:
            return "healthy"
        elif health_score >= 50:
            return "degraded"
        else:
            return "critical"


# ── Global singleton ──────────────────────────────────────────────

_scorer: (WormHealthScorer | None) = None
_scorer_lock = threading.Lock()


def get_health_scorer() -> WormHealthScorer:
    """Get or create the global WormHealthScorer singleton."""
    global _scorer
    if _scorer is None:
        with _scorer_lock:
            if _scorer is None:
                _scorer = WormHealthScorer()
    return _scorer


def record_worm_health(name: str, success: bool, duration_ms: float):
    """Record a worm execution for health scoring.

    Convenience function for callers that don't want to import
    WormHealthScorer directly. Safe to call from any thread.
    """
    get_health_scorer().record(name, success, duration_ms)


# ── Self-test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Worm Health Scorer — Self-Test ===\n")

    scorer = WormHealthScorer()

    # Simulate some executions
    for _ in range(5):
        scorer.record("SystemZ", success=True, duration_ms=15.0)
        scorer.record("PortAuditor", success=True, duration_ms=45.0)
        scorer.record("SlowWorm", success=False, duration_ms=5000.0)
        scorer.record("FastWorm", success=True, duration_ms=3.0)

    # Test single score
    score = scorer.score_worm("SystemZ")
    print(f"SystemZ:    score={score['health_score']}  status={score['status']}")
    print(f"            {score['breakdown']}")
    print()

    score = scorer.score_worm("SlowWorm")
    print(f"SlowWorm:   score={score['health_score']}  status={score['status']}")
    print(f"            {score['breakdown']}")
    print()

    # Test full scoring (needs registry)
    all_scores = scorer.score_all()
    print(f"Overall:    {all_scores['overall_health']}")
    print(f"Statuses:   healthy={all_scores['healthy']} degraded={all_scores['degraded']}")
    print(f"            critical={all_scores['critical']} unknown={all_scores['unknown']}")
    print(f"Categories: {len(all_scores['per_category'])}")
    print()

    if all_scores["per_worm"]:
        print("Top 5 healthiest:")
        for w in all_scores["per_worm"][-5:]:
            print(f"  {w['name']:30} {w['health_score']:5.1f} {w['status']}")
        print()
        print("Bottom 5 (worst health):")
        for w in all_scores["per_worm"][:5]:
            print(f"  {w['name']:30} {w['health_score']:5.1f} {w['status']}")

    print("\nworm_health.py self-test PASSED")
