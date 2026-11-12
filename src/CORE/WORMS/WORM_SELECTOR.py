# coding: utf-8
"""
WormSelectorWorm — GRPO-based intelligent worm selection.

Uses GRPOTrainer and knowledge graph patterns to select the optimal
worm for any given task context. Learns from execution history and
cross-agent patterns.

Usage:
    selector = WormSelectorWorm()
    result = selector.execute()
    best = selector.select_worm({"category": "monitor", "source": "SystemZ"})
"""

import json
import random
import threading
import zlib
from typing import Any, List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

# ── Worm Registry Import ─────────────────────────────────────────────
try:
    from CORE.worm_registry import register_worm, WORM_REGISTRY, discover_worms  # pyright: ignore[reportAssignmentType]
    _REGISTRY = True
except ImportError as exc:
    note_failure("WORM_SELECTOR:L25", exc)
    _REGISTRY = False
    WORM_REGISTRY = {}
    def discover_worms(): return 0
    def register_worm(**kw):
        return lambda cls: cls


@register_worm(category="learning", priority=5, cooldown_seconds=60.0,
    description="GRPO-based worm selector -- learns optimal worm selection from execution history")
class WormSelectorWorm:
    """GRPO-based intelligent worm selector.

    Combines GRPOTrainer (group-relative policy optimization) with
    cross-agent knowledge graph patterns to select the best worm
    for any task context.

    Features:
    - Learns worm preferences from execution history
    - Queries knowledge graph for cross-agent patterns
    - Falls back to SpawnDiversityEngine for cold-start
    - Records selections as DPO preferences for continuous learning
    """

    VERSION = "1.0.0"

    def __init__(self):
        self._grpo = None
        self._ab_runner = None
        self._lock = threading.Lock()

        # Ensure full registry is populated (not just early-imported worms)
        # discover_worms() is idempotent — skips already-registered entries
        if _REGISTRY:
            discover_worms()

    def _get_grpo(self):
        if self._grpo is None:
            try:
                from CORE.WORMS.expansion.learning_grpo import GRPOTrainer
                self._grpo = GRPOTrainer()
            except ImportError as exc:
                note_failure("WORM_SELECTOR:L66", exc)
                self._grpo = False
        return self._grpo if self._grpo else None

    def _get_ab_runner(self):
        if self._ab_runner is None:
            try:
                from CORE.WORMS.expansion.learning_grpo import ABTestRunner
                self._ab_runner = ABTestRunner()
            except ImportError as exc:
                note_failure("WORM_SELECTOR:L75", exc)
                self._ab_runner = False
        return self._ab_runner if self._ab_runner else None

    def _get_store(self):
        try:
            from CORE.local_memory import get_memory_store
            return get_memory_store()
        except ImportError as exc:
            note_failure("WORM_SELECTOR:L83", exc)
            return None

    def select_worm(self, task_context: dict) -> dict[str, Any]:
        """Select the best worm for a given task context.

        Uses a priority chain:
        1. GRPOTrainer learned policy (if available)
        2. Knowledge graph cross-agent patterns (if available)
        3. SpawnDiversityEngine weighted selection (fallback)

        Args:
            task_context: dict with category, task_type, source, etc.

        Returns:
            dict with selected worm name, method used, and confidence.
        """
        category = task_context.get("category", "")

        # Method 1: GRPO learned policy
        grpo = self._get_grpo()
        if grpo:
            best = grpo.select_best(context_key=category or "default")  # pyright: ignore[reportAttributeAccessIssue]
            if best:
                policy = grpo.get_policy(category or "default")  # pyright: ignore[reportAttributeAccessIssue]
                score = policy.get(best, 0.0)
                return {
                    "worm": best,
                    "method": "grpo_policy",
                    "confidence": min(1.0, max(0.0, (score + 1.0) / 2.0)),
                    "context_key": category or "default",
                }

        # Method 2: Knowledge graph patterns
        store = self._get_store()
        if store:
            try:
                # Build a simple embedding from task context
                embedding = self._context_to_embedding(task_context)
                patterns = store.query_similar_patterns(
                    embedding, pattern_type="worm_selection", top_k=3
                )
                if patterns:
                    best_pattern = patterns[0]
                    meta = best_pattern.get("metadata", {})
                    if isinstance(meta, str):
                        meta = json.loads(meta)
                    selected = meta.get("best_worm", "")
                    if selected:
                        return {
                            "worm": selected,
                            "method": "knowledge_graph",
                            "confidence": best_pattern.get("similarity", 0.5),
                            "pattern_id": best_pattern.get("id"),
                        }
            except Exception as exc:
                note_failure("WORM_SELECTOR:L138", exc)

        # Method 3: SpawnDiversityEngine fallback
        try:
            # Get top candidate by category
            candidates = []
            for name, info in WORM_REGISTRY.items():
                if info.get("category") == category:
                    candidates.append(name)
            if candidates:
                selected = random.choice(candidates)
                return {
                    "worm": selected,
                    "method": "diversity_fallback",
                    "confidence": 0.3,
                    "candidates": len(candidates),
                }
        except Exception as exc:
            note_failure("WORM_SELECTOR:L156", exc)

        # Final fallback: any worm from the category
        if category:
            for name, info in WORM_REGISTRY.items():
                if info.get("category") == category:
                    return {"worm": name, "method": "category_first", "confidence": 0.1}

        return {"worm": None, "method": "none", "confidence": 0.0, "error": "No worms found"}

    def record_selection_result(self, worm_name: str, task_context: dict,
                                 success: bool, reward: float):
        """Record the result of a worm selection for learning.

        Stores as a DPO preference if we have a previous selection
        to compare against, and updates the knowledge graph.

        Args:
            worm_name: The worm that was selected.
            task_context: The task context.
            success: Whether the worm execution succeeded.
            reward: The reward from the execution.
        """
        store = self._get_store()
        if not store:
            return

        # Record to knowledge graph
        embedding = self._context_to_embedding(task_context)
        store.register_pattern(
            source_agent="WormSelectorWorm",
            pattern_type="worm_selection",
            embedding=embedding,
            metadata={
                "best_worm": worm_name,
                "success": success,
                "reward": reward,
                "category": task_context.get("category", ""),
            },
            confidence=1.0 if success else 0.3,
        )

        # Update GRPO policy
        grpo = self._get_grpo()
        if grpo:
            category = task_context.get("category", "default")
            # Get recent selections in this category
            recent = store.query_memories(
                source="WormSelectorWorm", event_type="worm_selection",
                limit=10
            )
            if recent:
                # Build a group from recent selections
                worm_rewards = {}
                for r in recent:
                    data = r.get("data", {})
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except Exception as exc:
                            note_failure("WORM_SELECTOR:L216", exc)
                            continue
                    if isinstance(data, dict):
                        w = data.get("best_worm", "")
                        rw = data.get("reward", 0.0)
                        if w:
                            if w not in worm_rewards:
                                worm_rewards[w] = []
                            worm_rewards[w].append(rw)

                if len(worm_rewards) >= 2:
                    group = list(worm_rewards)[:8]
                    avg_rewards = [sum(worm_rewards[w]) / len(worm_rewards[w]) for w in group]
                    grpo.update_policy(group, avg_rewards, context_key=category)  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    def _context_to_embedding(task_context: dict) -> list[float]:
        """Convert a task context dict to a simple numeric embedding.

        Uses a fixed-size vector based on known context fields.
        Deterministic across Python processes (uses adler32, not hash()).
        """
        def _det_hash(s: str) -> float:
            """Deterministic hash: same input always gives same output."""
            return (zlib.adler32(s.encode()) % 100) / 100.0

        # 8-dimensional embedding from common context fields
        embedding = [0.0] * 8

        # Dim 0: category hash
        cat = task_context.get("category", "")
        embedding[0] = _det_hash(cat)

        # Dim 1: task_type hash
        tt = task_context.get("task_type", "")
        embedding[1] = _det_hash(tt)

        # Dim 2: source hash
        src = task_context.get("source", "")
        embedding[2] = _det_hash(src)

        # Dim 3: priority (normalized)
        priority = task_context.get("priority", 5)
        embedding[3] = min(1.0, priority / 10.0)

        # Dim 4: has_timeout flag
        embedding[4] = 1.0 if task_context.get("timeout") else 0.0

        # Dim 5: complexity indicator
        embedding[5] = min(1.0, task_context.get("complexity", 0.5))

        # Dim 6: parallel flag
        embedding[6] = 1.0 if task_context.get("parallel") else 0.0

        # Dim 7: severity (if present)
        sev = task_context.get("severity", "info")
        sev_map = {"info": 0.25, "warning": 0.5, "error": 0.75, "critical": 1.0}
        embedding[7] = sev_map.get(sev, 0.25)

        return embedding

    def execute(self) -> dict[str, Any]:
        """Standard worm execute() — reports selector status."""
        grpo = self._get_grpo()
        grpo_stats = grpo.get_stats() if grpo else {"n_updates": 0}  # pyright: ignore[reportAttributeAccessIssue]

        store = self._get_store()
        kg_stats = {}
        if store:
            try:
                kg_stats = store.get_knowledge_graph_stats()
            except Exception as exc:
                note_failure("WORM_SELECTOR:L287", exc)

        return {
            "version": self.VERSION,
            "grpo": grpo_stats,
            "knowledge_graph": kg_stats,
            "registry_size": len(WORM_REGISTRY),
        }


if __name__ == "__main__":
    print("=" * 60)
    print("  WormSelectorWorm - Self-test")
    print("=" * 60)

    selector = WormSelectorWorm()

    # Test 1: Select worm for a monitor task
    print("\n  [1] Select worm for monitor task...")
    result = selector.select_worm({"category": "monitor", "task_type": "system_check"})
    print(f"      Result: {result['worm']} (method={result['method']}, conf={result['confidence']:.2f})")

    # Test 2: Select worm for a security task
    print("\n  [2] Select worm for security task...")
    result = selector.select_worm({"category": "security", "task_type": "vuln_scan"})
    print(f"      Result: {result['worm']} (method={result['method']}, conf={result['confidence']:.2f})")

    # Test 3: Execute status
    print("\n  [3] Status...")
    status = selector.execute()
    print(f"      GRPO updates: {status['grpo'].get('n_updates', 0)}")
    print(f"      Registry: {status['registry_size']} worms")

    print("\n" + "=" * 60)
    print("  WormSelectorWorm self-test complete.")
    print("=" * 60)
