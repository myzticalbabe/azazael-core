# coding: utf-8
"""
Tests for AI/ML/RL Protocol Evolution components:
- ABTestRunner DPO preference learning
- GRPOTrainer
- Cross-Agent Knowledge Graph (LocalMemoryStore)
"""

import os
import sys
import json
import math
import unittest
import tempfile
import threading

# Add project root and src to path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_src_dir = os.path.join(_project_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from CORE.WORMS.expansion.learning import (
    ABTestRunner,
    GRPOTrainer,
)
from CORE.local_memory import LocalMemoryStore


class TestABTestRunnerDPO(unittest.TestCase):
    """Tests for ABTestRunner DPO preference learning."""

    def test_create_experiment_with_preferences(self):
        runner = ABTestRunner()
        runner.create_experiment("test_exp", ["A", "B", "C"])
        self.assertIn("test_exp", runner._preferences)

    def test_record_preference(self):
        runner = ABTestRunner()
        runner.create_experiment("test", ["A", "B"])
        entry = runner.record_preference(
            "test", chosen="A", rejected="B",
            context={"task": "monitor"}, chosen_reward=0.9, rejected_reward=0.3,
        )
        self.assertEqual(entry["chosen"], "A")
        self.assertEqual(entry["rejected"], "B")
        self.assertAlmostEqual(entry["reward_margin"], 0.6)
        self.assertEqual(len(runner._preferences["test"]), 1)

    def test_get_preference_pairs(self):
        runner = ABTestRunner()
        runner.create_experiment("test", ["A", "B"])
        for i in range(10):
            runner.record_preference(
                "test", chosen="A", rejected="B",
                chosen_reward=0.8, rejected_reward=0.2,
            )
        pairs = runner.get_preference_pairs("test", limit=5)
        self.assertEqual(len(pairs), 5)

    def test_compute_preference_scores(self):
        runner = ABTestRunner()
        runner.create_experiment("test", ["A", "B", "C"])
        # A is chosen 3 times, rejected 1 time
        runner.record_preference("test", "A", "B", chosen_reward=0.9, rejected_reward=0.3)
        runner.record_preference("test", "A", "C", chosen_reward=0.8, rejected_reward=0.2)
        runner.record_preference("test", "A", "B", chosen_reward=0.7, rejected_reward=0.4)
        runner.record_preference("test", "B", "A", chosen_reward=0.9, rejected_reward=0.1)

        scores = runner.compute_preference_scores("test")
        self.assertIn("A", scores)
        self.assertEqual(scores["A"]["chosen_count"], 3)
        self.assertEqual(scores["A"]["rejected_count"], 1)
        self.assertAlmostEqual(scores["A"]["preference_score"], 0.75)

    def test_compute_dpo_loss(self):
        runner = ABTestRunner()
        runner.create_experiment("test", ["A", "B"])
        # Perfect preferences (large margin) should give low loss
        runner.record_preference("test", "A", "B", chosen_reward=1.0, rejected_reward=0.0)
        runner.record_preference("test", "A", "B", chosen_reward=0.9, rejected_reward=0.1)
        result = runner.compute_dpo_loss("test", beta=0.1)
        self.assertIsNotNone(result["loss"])
        self.assertEqual(result["n_pairs"], 2)
        # Loss should be small (close to 0) for large margins
        self.assertLess(result["loss"], 1.0)

    def test_compute_dpo_loss_empty(self):
        runner = ABTestRunner()
        runner.create_experiment("test", ["A", "B"])
        result = runner.compute_dpo_loss("test")
        self.assertIsNone(result["loss"])
        self.assertIn("error", result)

    def test_get_results_includes_preferences(self):
        runner = ABTestRunner()
        runner.create_experiment("test", ["A", "B"])
        runner.track("test", "A", converted=True)
        runner.track("test", "B", converted=False)
        runner.record_preference("test", "A", "B", chosen_reward=0.9, rejected_reward=0.3)
        results = runner.get_results("test")
        self.assertIn("preference_score", results["variants"]["A"])
        self.assertEqual(results["variants"]["A"]["preference_count"], 1)

    def test_version_bumped(self):
        runner = ABTestRunner()
        self.assertEqual(runner.VERSION, "2.0.0")


class TestGRPOTrainer(unittest.TestCase):
    """Tests for GRPOTrainer."""

    def test_compute_advantages(self):
        trainer = GRPOTrainer()
        rewards = [1.0, 2.0, 3.0, 4.0, 5.0]
        advantages = trainer.compute_advantages(rewards)
        self.assertEqual(len(advantages), 5)
        # Mean should be 0
        mean_adv = sum(advantages) / len(advantages)
        self.assertAlmostEqual(mean_adv, 0.0, places=5)

    def test_compute_advantages_empty(self):
        trainer = GRPOTrainer()
        self.assertEqual(trainer.compute_advantages([]), [])

    def test_compute_advantages_uniform(self):
        trainer = GRPOTrainer()
        # All same rewards -> all zero advantages
        advantages = trainer.compute_advantages([5.0, 5.0, 5.0])
        for a in advantages:
            self.assertAlmostEqual(a, 0.0)

    def test_update_policy(self):
        trainer = GRPOTrainer(lr=0.1)
        group = ["worm_A", "worm_B", "worm_C"]
        rewards = [1.0, 0.5, 0.0]
        trainer.update_policy(group, rewards, context_key="test")
        policy = trainer.get_policy("test")
        # worm_A (highest reward) should have highest score
        self.assertGreater(policy["worm_A"], policy["worm_B"])
        self.assertGreater(policy["worm_B"], policy["worm_C"])

    def test_update_policy_multiple_steps(self):
        trainer = GRPOTrainer(lr=0.05)
        for _ in range(10):
            trainer.update_policy(
                ["A", "B"], [1.0, 0.0], context_key="test"
            )
        policy = trainer.get_policy("test")
        self.assertGreater(policy["A"], policy["B"])

    def test_select_best(self):
        trainer = GRPOTrainer(lr=0.1)
        trainer.update_policy(["A", "B"], [1.0, 0.0], context_key="test")
        best = trainer.select_best("test")
        self.assertEqual(best, "A")

    def test_select_best_no_policy(self):
        trainer = GRPOTrainer()
        self.assertIsNone(trainer.select_best("nonexistent"))

    def test_get_policy_all_contexts(self):
        trainer = GRPOTrainer()
        trainer.update_policy(["A", "B"], [1.0, 0.0], context_key="ctx1")
        trainer.update_policy(["C", "D"], [0.5, 0.5], context_key="ctx2")
        all_policies = trainer.get_policy()
        self.assertIn("ctx1", all_policies)
        self.assertIn("ctx2", all_policies)

    def test_get_stats(self):
        trainer = GRPOTrainer(load_policy=False)
        for i in range(5):
            trainer.update_policy(["A", "B"], [1.0, 0.0], context_key=f"ctx{i}")
        stats = trainer.get_stats()
        self.assertEqual(stats["n_updates"], 5)
        self.assertEqual(stats["n_contexts"], 5)
        self.assertGreater(stats["total_worms_scored"], 0)

    def test_score_drift_prevention(self):
        trainer = GRPOTrainer(lr=1.0)  # Very high lr to trigger drift
        for _ in range(100):
            trainer.update_policy(["A", "B"], [100.0, -100.0], context_key="test")
        policy = trainer.get_policy("test")
        # Scores should be bounded (drift prevention)
        for score in policy.values():
            self.assertLessEqual(abs(score), 15.0)

    def test_generate_group(self):
        trainer = GRPOTrainer()
        group = trainer.generate_group({"category": "monitor"}, group_size=5)
        # Should return a list (may be empty if WORM_REGISTRY not available)
        self.assertIsInstance(group, list)


class TestKnowledgeGraph(unittest.TestCase):
    """Tests for Cross-Agent Knowledge Graph in LocalMemoryStore."""

    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_kg_{os.getpid()}.db")
        self.store = LocalMemoryStore(db_path=self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        try:
            os.unlink(self.db_path)
        except Exception:
            pass
        # Clean up WAL/SHM files
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(self.db_path + suffix)
            except Exception:
                pass

    def test_register_pattern(self):
        pid = self.store.register_pattern(
            source_agent="SYSTEMZ_ML",
            pattern_type="anomaly",
            embedding=[0.1, 0.2, 0.3, 0.4],
            metadata={"description": "CPU spike pattern"},
            confidence=0.85,
        )
        self.assertIsNotNone(pid)
        self.assertGreater(pid, 0)

    def test_query_similar_patterns(self):
        # Register several patterns
        self.store.register_pattern("agent_A", "anomaly", [1.0, 0.0, 0.0], confidence=0.9)
        self.store.register_pattern("agent_B", "anomaly", [0.0, 1.0, 0.0], confidence=0.8)
        self.store.register_pattern("agent_C", "anomaly", [0.9, 0.1, 0.0], confidence=0.7)

        # Query similar to [1.0, 0.0, 0.0] — should match agent_A best
        results = self.store.query_similar_patterns([1.0, 0.0, 0.0], top_k=3)
        self.assertEqual(len(results), 3)
        # First result should be agent_A (most similar)
        self.assertEqual(results[0]["source_agent"], "agent_A")
        self.assertGreater(results[0]["similarity"], 0.9)

    def test_query_similar_with_type_filter(self):
        self.store.register_pattern("A", "anomaly", [1.0, 0.0])
        self.store.register_pattern("B", "preference", [0.9, 0.1])
        self.store.register_pattern("C", "anomaly", [0.0, 1.0])

        results = self.store.query_similar_patterns([1.0, 0.0], pattern_type="anomaly")
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r["pattern_type"], "anomaly")

    def test_get_patterns_by_agent(self):
        self.store.register_pattern("agent_A", "anomaly", [1.0, 0.0])
        self.store.register_pattern("agent_A", "preference", [0.5, 0.5])
        self.store.register_pattern("agent_B", "anomaly", [0.0, 1.0])

        results = self.store.get_patterns_by_agent("agent_A")
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r["source_agent"], "agent_A")

    def test_get_patterns_by_agent_with_type(self):
        self.store.register_pattern("agent_A", "anomaly", [1.0, 0.0])
        self.store.register_pattern("agent_A", "preference", [0.5, 0.5])
        results = self.store.get_patterns_by_agent("agent_A", pattern_type="anomaly")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["pattern_type"], "anomaly")

    def test_knowledge_graph_stats(self):
        self.store.register_pattern("agent_A", "anomaly", [1.0, 0.0], confidence=0.9)
        self.store.register_pattern("agent_A", "preference", [0.5, 0.5], confidence=0.7)
        self.store.register_pattern("agent_B", "anomaly", [0.0, 1.0], confidence=0.8)

        stats = self.store.get_knowledge_graph_stats()
        self.assertEqual(stats["total_patterns"], 3)
        self.assertEqual(stats["by_agent"]["agent_A"], 2)
        self.assertEqual(stats["by_agent"]["agent_B"], 1)
        self.assertEqual(stats["by_type"]["anomaly"], 2)
        self.assertEqual(stats["by_type"]["preference"], 1)
        self.assertGreater(stats["avg_confidence"], 0.0)

    def test_cosine_similarity(self):
        # Identical vectors -> similarity = 1.0
        sim = LocalMemoryStore._cosine_similarity([1.0, 0.0], [1.0, 0.0])
        self.assertAlmostEqual(sim, 1.0)
        # Orthogonal vectors -> similarity = 0.0
        sim = LocalMemoryStore._cosine_similarity([1.0, 0.0], [0.0, 1.0])
        self.assertAlmostEqual(sim, 0.0)
        # Opposite vectors -> similarity = -1.0
        sim = LocalMemoryStore._cosine_similarity([1.0, 0.0], [-1.0, 0.0])
        self.assertAlmostEqual(sim, -1.0)
        # Empty vectors -> 0.0
        sim = LocalMemoryStore._cosine_similarity([], [])
        self.assertAlmostEqual(sim, 0.0)
        # Different lengths -> 0.0
        sim = LocalMemoryStore._cosine_similarity([1.0], [1.0, 0.0])
        self.assertAlmostEqual(sim, 0.0)

    def test_hit_count_increments(self):
        pid = self.store.register_pattern("A", "anomaly", [1.0, 0.0])
        self.store.register_pattern("B", "anomaly", [0.0, 1.0])
        # Query should increment hit count
        self.store.query_similar_patterns([1.0, 0.0], top_k=1)
        patterns = self.store.get_patterns_by_agent("A")
        self.assertGreaterEqual(patterns[0]["hit_count"], 1)


# ═══════════════════════════════════════════════════════════════════
# Edge Cases (added June 15 2026)
# ═══════════════════════════════════════════════════════════════════


class TestKnowledgeGraphDeletePattern(unittest.TestCase):
    """Tests for delete_pattern() in LocalMemoryStore."""

    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_kg_del_{os.getpid()}.db")
        self.store = LocalMemoryStore(db_path=self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        try:
            os.unlink(self.db_path)
        except Exception:
            pass
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(self.db_path + suffix)
            except Exception:
                pass

    def test_delete_by_id(self):
        """Delete a specific pattern by ID should remove exactly that pattern."""
        pid1 = self.store.register_pattern("A", "test", [1.0, 0.0])
        pid2 = self.store.register_pattern("B", "test", [0.0, 1.0])
        deleted = self.store.delete_pattern(pattern_id=pid1)
        self.assertEqual(deleted, 1)
        patterns = self.store.get_patterns_by_agent("A")
        self.assertEqual(patterns, [])
        # agent_B's pattern should remain
        patterns = self.store.get_patterns_by_agent("B")
        self.assertEqual(len(patterns), 1)

    def test_delete_by_agent(self):
        """Delete all patterns for a specific agent."""
        self.store.register_pattern("A", "type1", [1.0, 0.0])
        self.store.register_pattern("A", "type2", [0.5, 0.5])
        self.store.register_pattern("B", "type1", [0.0, 1.0])
        deleted = self.store.delete_pattern(source_agent="A")
        self.assertEqual(deleted, 2)
        self.assertEqual(self.store.get_patterns_by_agent("A"), [])
        self.assertEqual(len(self.store.get_patterns_by_agent("B")), 1)

    def test_delete_by_type(self):
        """Delete all patterns of a specific type across all agents."""
        self.store.register_pattern("A", "anomaly", [1.0, 0.0])
        self.store.register_pattern("B", "anomaly", [0.0, 1.0])
        self.store.register_pattern("A", "preference", [0.5, 0.5])
        deleted = self.store.delete_pattern(pattern_type="anomaly")
        self.assertEqual(deleted, 2)
        stats = self.store.get_knowledge_graph_stats()
        self.assertEqual(stats["total_patterns"], 1)

    def test_delete_by_agent_and_type(self):
        """Compound filter: delete by agent + type."""
        self.store.register_pattern("A", "anomaly", [1.0, 0.0])
        self.store.register_pattern("A", "preference", [0.5, 0.5])
        self.store.register_pattern("B", "anomaly", [0.0, 1.0])
        deleted = self.store.delete_pattern(source_agent="A", pattern_type="anomaly")
        self.assertEqual(deleted, 1)
        # A's preference should remain
        self.assertEqual(len(self.store.get_patterns_by_agent("A", pattern_type="preference")), 1)
        # B's anomaly should remain
        self.assertEqual(len(self.store.get_patterns_by_agent("B", pattern_type="anomaly")), 1)

    def test_delete_nonexistent_id(self):
        """Deleting a non-existent pattern ID should return 0."""
        deleted = self.store.delete_pattern(pattern_id=99999)
        self.assertEqual(deleted, 0)

    def test_delete_nonexistent_agent(self):
        """Deleting patterns for a non-existent agent should return 0."""
        self.store.register_pattern("A", "test", [1.0, 0.0])
        deleted = self.store.delete_pattern(source_agent="NONEXISTENT")
        self.assertEqual(deleted, 0)
        # Existing pattern should remain
        self.assertEqual(len(self.store.get_patterns_by_agent("A")), 1)

    def test_delete_no_filter_raises(self):
        """Calling delete_pattern with no arguments should raise ValueError."""
        with self.assertRaises(ValueError):
            self.store.delete_pattern()

    def test_delete_then_query_returns_empty(self):
        """After deletion, query_similar_patterns should not return deleted patterns."""
        self.store.register_pattern("A", "test", [1.0, 0.0], confidence=0.9)
        self.store.register_pattern("B", "test", [0.0, 1.0], confidence=0.9)
        self.store.delete_pattern(source_agent="A")
        results = self.store.query_similar_patterns([1.0, 0.0], top_k=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_agent"], "B")

    def test_delete_all_patterns_empty_graph(self):
        """Delete all patterns should leave an empty graph."""
        self.store.register_pattern("A", "test", [1.0, 0.0])
        self.store.register_pattern("B", "test", [0.0, 1.0])
        self.store.delete_pattern(source_agent="A")
        self.store.delete_pattern(source_agent="B")
        stats = self.store.get_knowledge_graph_stats()
        self.assertEqual(stats["total_patterns"], 0)
        self.assertEqual(stats["by_agent"], {})
        self.assertEqual(stats["by_type"], {})

    def test_delete_idempotent(self):
        """Deleting the same pattern twice should succeed the first time and return 0 the second."""
        pid = self.store.register_pattern("A", "test", [1.0, 0.0])
        deleted1 = self.store.delete_pattern(pattern_id=pid)
        self.assertEqual(deleted1, 1)
        deleted2 = self.store.delete_pattern(pattern_id=pid)
        self.assertEqual(deleted2, 0)


class TestKnowledgeGraphEdgeCases(unittest.TestCase):
    """Edge case tests for cross-agent knowledge graph patterns."""

    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_kg_edge_{os.getpid()}.db")
        self.store = LocalMemoryStore(db_path=self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        try:
            os.unlink(self.db_path)
        except Exception:
            pass
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(self.db_path + suffix)
            except Exception:
                pass

    def test_empty_embedding(self):
        """Registering with an empty embedding list should not crash."""
        pid = self.store.register_pattern("A", "test", [], confidence=0.5)
        self.assertGreater(pid, 0)
        # Querying with empty embedding should return 0.0 similarity
        results = self.store.query_similar_patterns([], top_k=5)
        for r in results:
            self.assertAlmostEqual(r["similarity"], 0.0)

    def test_zero_vector_embedding(self):
        """A zero vector should have 0.0 similarity with non-zero vectors."""
        self.store.register_pattern("A", "anomaly", [0.0, 0.0, 0.0], confidence=0.8)
        self.store.register_pattern("B", "anomaly", [1.0, 1.0, 1.0], confidence=0.8)
        # Query with zero vector
        results = self.store.query_similar_patterns([0.0, 0.0, 0.0], top_k=5)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertAlmostEqual(r["similarity"], 0.0)

    def test_high_dimensional_embedding(self):
        """Register and query with a 1024-dimensional embedding."""
        emb = [float(i % 100) / 100.0 for i in range(1024)]
        pid = self.store.register_pattern("A", "highdim", emb, confidence=0.9)
        self.assertGreater(pid, 0)
        results = self.store.query_similar_patterns(emb, top_k=5)
        self.assertGreaterEqual(len(results), 1)
        self.assertAlmostEqual(results[0]["similarity"], 1.0, places=4)

    def test_negative_embeddings(self):
        """Negative values in embeddings should work correctly."""
        self.store.register_pattern("A", "neg", [-0.5, -0.5], confidence=1.0)
        self.store.register_pattern("B", "neg", [0.5, 0.5], confidence=1.0)
        # Query with negative vector — should match A best
        results = self.store.query_similar_patterns([-0.5, -0.5], top_k=5)
        self.assertEqual(results[0]["source_agent"], "A")
        self.assertAlmostEqual(results[0]["similarity"], 1.0, places=4)

    def test_confidence_boundary_values(self):
        """Confidence exactly 0.0 and 1.0 should be accepted."""
        pid0 = self.store.register_pattern("A", "test", [1.0], confidence=0.0)
        self.assertGreater(pid0, 0)
        pid1 = self.store.register_pattern("B", "test", [1.0], confidence=1.0)
        self.assertGreater(pid1, 0)
        # Retrieve and verify
        patterns = self.store.get_patterns_by_agent("A")
        self.assertAlmostEqual(patterns[0]["confidence"], 0.0)
        patterns = self.store.get_patterns_by_agent("B")
        self.assertAlmostEqual(patterns[0]["confidence"], 1.0)

    def test_confidence_above_one(self):
        """Confidence > 1.0 should be stored as-is (no clamping)."""
        pid = self.store.register_pattern("A", "test", [1.0], confidence=99.9)
        self.assertGreater(pid, 0)
        patterns = self.store.get_patterns_by_agent("A")
        self.assertAlmostEqual(patterns[0]["confidence"], 99.9)

    def test_long_source_agent_string(self):
        """Very long source agent names should be accepted."""
        long_name = "AGENT_" + "X" * 200
        pid = self.store.register_pattern(long_name, "test", [1.0, 0.0], confidence=0.5)
        self.assertGreater(pid, 0)
        patterns = self.store.get_patterns_by_agent(long_name)
        self.assertEqual(len(patterns), 1)

    def test_long_pattern_type_string(self):
        """Very long pattern type strings should be accepted."""
        long_type = "super_long_pattern_type_" + "Y" * 200
        pid = self.store.register_pattern("A", long_type, [1.0, 0.0], confidence=0.5)
        self.assertGreater(pid, 0)
        patterns = self.store.get_patterns_by_agent("A", pattern_type=long_type)
        self.assertEqual(len(patterns), 1)

    def test_duplicate_registration(self):
        """Registering the same pattern twice should create two entries."""
        pid1 = self.store.register_pattern("A", "anomaly", [1.0, 0.0], confidence=0.5)
        pid2 = self.store.register_pattern("A", "anomaly", [1.0, 0.0], confidence=0.5)
        self.assertNotEqual(pid1, pid2)
        patterns = self.store.get_patterns_by_agent("A", pattern_type="anomaly")
        self.assertEqual(len(patterns), 2)

    def test_large_metadata_dict(self):
        """Register with a large metadata dictionary should work."""
        large_meta = {f"key_{i}": f"value_{i}" for i in range(100)}
        pid = self.store.register_pattern("A", "bigmeta", [1.0], metadata=large_meta)
        self.assertGreater(pid, 0)
        patterns = self.store.get_patterns_by_agent("A")
        self.assertEqual(len(patterns[0]["metadata"]), 100)

    def test_null_metadata(self):
        """Register with None metadata should not crash."""
        pid = self.store.register_pattern("A", "nullmeta", [1.0], metadata=None)
        self.assertGreater(pid, 0)
        patterns = self.store.get_patterns_by_agent("A")
        self.assertEqual(patterns[0]["metadata"], {})

    def test_cosine_similarity_zero_norm(self):
        """Cosine similarity with zero-norm vectors should return 0.0."""
        sim = LocalMemoryStore._cosine_similarity([0.0, 0.0], [0.0, 0.0])
        self.assertAlmostEqual(sim, 0.0)
        sim = LocalMemoryStore._cosine_similarity([0.0, 0.0], [1.0, 0.0])
        self.assertAlmostEqual(sim, 0.0)
        sim = LocalMemoryStore._cosine_similarity([1.0, 0.0], [0.0, 0.0])
        self.assertAlmostEqual(sim, 0.0)

    def test_cosine_similarity_partial_overlap(self):
        """Cosine similarity with partial overlap should return middle values."""
        sim = LocalMemoryStore._cosine_similarity([1.0, 0.0], [0.5, 0.5])
        expected = 0.5 / (1.0 * (0.5**2 + 0.5**2)**0.5)
        self.assertAlmostEqual(sim, expected, places=4)

    def test_cosine_similarity_single_element(self):
        """Single-element vectors: same sign -> 1.0, opposite -> -1.0."""
        self.assertAlmostEqual(LocalMemoryStore._cosine_similarity([5.0], [5.0]), 1.0)
        self.assertAlmostEqual(LocalMemoryStore._cosine_similarity([5.0], [-5.0]), -1.0)
        self.assertAlmostEqual(LocalMemoryStore._cosine_similarity([5.0], [0.0]), 0.0)


# ═══════════════════════════════════════════════════════════════════
# Batch Operations (added June 15 2026)
# ═══════════════════════════════════════════════════════════════════


class TestKnowledgeGraphBatchOps(unittest.TestCase):
    """Batch operation tests for cross-agent knowledge graph."""

    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_kg_batch_{os.getpid()}.db")
        self.store = LocalMemoryStore(db_path=self.db_path)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        try:
            os.unlink(self.db_path)
        except Exception:
            pass
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(self.db_path + suffix)
            except Exception:
                pass

    def test_bulk_register_100_patterns(self):
        """Registering 100 patterns in sequence should work."""
        for i in range(100):
            self.store.register_pattern(
                f"agent_{i % 10}",
                "bulk",
                [float(i) / 100.0, float(i % 5) / 5.0],
                confidence=0.5 + (i % 5) * 0.1,
            )
        stats = self.store.get_knowledge_graph_stats()
        self.assertEqual(stats["total_patterns"], 100)
        self.assertEqual(len(stats["by_agent"]), 10)
        for agent_count in stats["by_agent"].values():
            self.assertEqual(agent_count, 10)  # 100 / 10 agents

    def test_query_empty_result_set(self):
        """Query with a pattern_type that has no matches should return empty."""
        self.store.register_pattern("A", "anomaly", [1.0, 0.0])
        results = self.store.query_similar_patterns([1.0, 0.0], pattern_type="nonexistent")
        self.assertEqual(results, [])

    def test_get_patterns_nonexistent_agent(self):
        """Getting patterns for an agent with no records should return empty."""
        self.store.register_pattern("A", "anomaly", [1.0, 0.0])
        results = self.store.get_patterns_by_agent("NONEXISTENT")
        self.assertEqual(results, [])

    def test_get_patterns_nonexistent_type(self):
        """Getting patterns with a nonexistent type+agent combo should return empty."""
        self.store.register_pattern("A", "anomaly", [1.0, 0.0])
        results = self.store.get_patterns_by_agent("A", pattern_type="nonexistent")
        self.assertEqual(results, [])

    def test_stats_empty_knowledge_graph(self):
        """Stats on empty knowledge graph should return zeros."""
        stats = self.store.get_knowledge_graph_stats()
        self.assertEqual(stats["total_patterns"], 0)
        self.assertEqual(stats["by_agent"], {})
        self.assertEqual(stats["by_type"], {})
        self.assertEqual(stats["avg_confidence"], 0.0)

    def test_query_top_k_larger_than_available(self):
        """Query with top_k larger than total patterns should return all."""
        for i in range(3):
            self.store.register_pattern("A", "test", [float(i), 0.0])
        results = self.store.query_similar_patterns([0.0, 0.0], top_k=100)
        self.assertEqual(len(results), 3)

    def test_query_top_k_zero(self):
        """Query with top_k=0 should return empty list."""
        self.store.register_pattern("A", "test", [1.0, 0.0])
        results = self.store.query_similar_patterns([1.0, 0.0], top_k=0)
        self.assertEqual(results, [])

    def test_query_top_k_negative(self):
        """Query with negative top_k should return empty list."""
        self.store.register_pattern("A", "test", [1.0, 0.0])
        results = self.store.query_similar_patterns([1.0, 0.0], top_k=-5)
        self.assertEqual(results, [])

    def test_multiple_type_filtering(self):
        """Register patterns with different types and filter correctly."""
        self.store.register_pattern("A", "type_1", [1.0, 0.0])
        self.store.register_pattern("A", "type_1", [0.8, 0.2])
        self.store.register_pattern("A", "type_2", [0.0, 1.0])
        self.store.register_pattern("B", "type_1", [0.5, 0.5])

        # Filter by A + type_1
        results = self.store.get_patterns_by_agent("A", pattern_type="type_1")
        self.assertEqual(len(results), 2)

        # Filter by type_2 across all agents
        results = self.store.query_similar_patterns([0.0, 1.0], pattern_type="type_2")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_agent"], "A")

        # Filter by type_1 across all agents
        results = self.store.query_similar_patterns([1.0, 0.0], pattern_type="type_1")
        self.assertEqual(len(results), 3)


# ═══════════════════════════════════════════════════════════════════
# Concurrency (added June 15 2026)
# ═══════════════════════════════════════════════════════════════════


class TestKnowledgeGraphConcurrency(unittest.TestCase):
    """Concurrency tests for cross-agent knowledge graph."""

    def setUp(self):
        self.db_path = os.path.join(tempfile.gettempdir(), f"test_kg_conc_{os.getpid()}.db")
        self.store = LocalMemoryStore(db_path=self.db_path)
        self.errors: list[str] = []

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        try:
            os.unlink(self.db_path)
        except Exception:
            pass
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(self.db_path + suffix)
            except Exception:
                pass

    def test_concurrent_registration(self):
        """Multiple threads registering patterns should not race."""
        def register_many(agent: str, count: int):
            try:
                for i in range(count):
                    self.store.register_pattern(
                        agent,
                        "concurrent",
                        [float(i) / 100.0, float(i % 10) / 10.0],
                        confidence=0.5,
                    )
            except Exception as e:
                self.errors.append(f"{agent}: {e}")

        threads = [
            threading.Thread(target=register_many, args=("A", 50)),
            threading.Thread(target=register_many, args=("B", 50)),
            threading.Thread(target=register_many, args=("C", 50)),
            threading.Thread(target=register_many, args=("D", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(self.errors, [])
        stats = self.store.get_knowledge_graph_stats()
        self.assertEqual(stats["total_patterns"], 200)
        self.assertEqual(stats["by_agent"]["A"], 50)
        self.assertEqual(stats["by_agent"]["B"], 50)

    def test_concurrent_read_and_write(self):
        """Concurrent registration and querying should not crash."""
        event = threading.Event()

        def writer():
            try:
                for i in range(30):
                    if i == 0:
                        event.set()  # Signal reader ASAP for true overlap
                    self.store.register_pattern(
                        "writer",
                        "rw_test",
                        [float(i), 0.0],
                        confidence=0.5,
                    )
            except Exception as e:
                self.errors.append(f"writer: {e}")

        def reader():
            try:
                event.wait(timeout=10)
                for _ in range(20):
                    self.store.query_similar_patterns([1.0, 0.0], top_k=10)
                    self.store.get_patterns_by_agent("writer")
                    self.store.get_knowledge_graph_stats()
            except Exception as e:
                self.errors.append(f"reader: {e}")

        w = threading.Thread(target=writer)
        r = threading.Thread(target=reader)
        w.start()
        r.start()
        w.join(timeout=15)
        r.join(timeout=15)

        self.assertEqual(self.errors, [])
        stats = self.store.get_knowledge_graph_stats()
        self.assertGreaterEqual(stats["total_patterns"], 30)

    def test_concurrent_similarity_queries_with_hit_updates(self):
        """Multiple similarity queries incrementing hit counts concurrently."""
        # Seed some patterns
        for i in range(5):
            self.store.register_pattern(
                f"agent_{i}",
                "hit_test",
                [float(i) / 5.0, 1.0 - float(i) / 5.0],
                confidence=0.8,
            )

        def query_loop():
            try:
                for _ in range(50):
                    self.store.query_similar_patterns(
                        [0.5, 0.5], top_k=10, pattern_type="hit_test"
                    )
            except Exception as e:
                self.errors.append(str(e))

        threads = [threading.Thread(target=query_loop) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(self.errors, [])
        # Hit counts should have been incremented
        for i in range(5):
            patterns = self.store.get_patterns_by_agent(f"agent_{i}", pattern_type="hit_test")
            if patterns:
                self.assertGreaterEqual(patterns[0]["hit_count"], 1)

    def test_concurrent_same_agent_registration(self):
        """Multiple threads registering for the same agent should be safe."""
        def register_for_agent(instance: int, count: int):
            try:
                for i in range(count):
                    self.store.register_pattern(
                        "shared_agent",
                        "shared",
                        [float(instance), float(i)],
                        confidence=0.5,
                    )
            except Exception as e:
                self.errors.append(f"t{instance}: {e}")

        threads = [
            threading.Thread(target=register_for_agent, args=(1, 25)),
            threading.Thread(target=register_for_agent, args=(2, 25)),
            threading.Thread(target=register_for_agent, args=(3, 25)),
            threading.Thread(target=register_for_agent, args=(4, 25)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(self.errors, [])
        patterns = self.store.get_patterns_by_agent("shared_agent")
        self.assertEqual(len(patterns), 100)

    def test_concurrent_query_while_reading_stats(self):
        """Concurrent queries to get_knowledge_graph_stats and query_similar_patterns."""
        # Seed
        for i in range(20):
            self.store.register_pattern(f"A{i % 5}", "stats_test", [float(i), 0.0])

        barrier = threading.Barrier(4, timeout=10)

        def query_and_stats():
            try:
                barrier.wait()
                for _ in range(25):
                    self.store.query_similar_patterns([0.5, 0.0], top_k=5)
                    self.store.get_knowledge_graph_stats()
                    self.store.get_patterns_by_agent("A0")
            except Exception as e:
                self.errors.append(str(e))

        threads = [threading.Thread(target=query_and_stats) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(self.errors, [])
        stats = self.store.get_knowledge_graph_stats()
        self.assertEqual(stats["total_patterns"], 20)


if __name__ == "__main__":
    unittest.main()
