# coding: utf-8
"""
Regression tests for the autonomous learning loop (fixed 2026-09-07).

Covers the six defects fixed in the session plus the learning.py split:
  1. Reward gradient across hyperparameters (was flat -> evolution had no fitness)
  2. Q-learning policy learns severity-appropriate actions
  3. Evidence-driven strategy recommendations (metacog -> global)
  4. ADOPT_CONFIDENCE gate (no churn on low-confidence suggestions)
  5. State persistence across instances (save_state/load_state)
  6. Strategy steering reaches the dreamer (run_dreaming_pipeline)
  7. learning.py split: shim re-exports keep every import path working

Run:  python -m pytest src/tests/test_learning_loop.py -v
   or: python -m unittest src.tests.test_learning_loop -v
"""
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure azazael/src is on sys.path and point memory at a scratch DB BEFORE
# any CORE import (the LocalMemoryStore singleton binds to the env at init).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Shared scratch dir (same fixed path as test_evolution_accuracy.py) so the
# process-global store singleton binds to ONE db across the whole suite.
# NOTE: AZAZAEL_MEMORY_DIR is a *directory* in production (the store
# appends memory_store.db), so the scratch var must be a dir and the db a
# file inside it — passing the bare path as db_path makes sqlite fail
# with "unable to open database file" (2026-09-08 fix).
_SCRATCH_DIR = str(Path(tempfile.gettempdir()) / "azazael_test_scratch_shared")
_SCRATCH = str(Path(_SCRATCH_DIR) / "memory_store.db")
os.environ["AZAZAEL_MEMORY_DIR"] = _SCRATCH_DIR

from CORE.local_memory import get_memory_store  # noqa: E402
from CORE.WORMS.expansion.learning import (  # noqa: E402
    RLTrainLoop, ModelVersioner, DataPipelineWorm,
    SchemaAwareFeatureExtractor, FeatureEngineer, ABTestRunner,
    refresh_dynamic_shapes, DATA_SHAPES, DYNAMIC_SHAPES)
from CORE.WORMS.expansion.meta_cognition import MetaCognitionWorm  # noqa: E402
from CORE.WORMS.expansion.dreaming import DreamingModule  # noqa: E402
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

STORE = get_memory_store()
AVAIL = ['default', 'conservative', 'defensive', 'aggressive', 'adaptive',
         'batched', 'staged', 'measured', 'sustained', 'targeted']


def setUpModule():
    """Wipe the shared scratch DB so persisted metacog state from a previous
    run (or another module) can't leak into these tests."""
    import CORE.local_memory as _lm
    _lm._global_store = None
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_SCRATCH + suffix)
        except OSError as exc:
            note_failure("test_learning_loop:L60", exc)
    _lm._global_store = _lm.LocalMemoryStore(db_path=_SCRATCH)
    globals()["STORE"] = _lm._global_store


def seed_records(source="SystemZ", n=12):
    for i in range(n):
        STORE.record_event(
            source=source, event_type="monitor",
            severity="error" if i % 2 == 0 else "warning",
            data={"duration_ms": 20, "success": False}, tags=["test"])


class TestRewardGradient(unittest.TestCase):
    """Defect 6: reward must depend on the chosen action, giving evolution a
    fitness gradient across hyperparameters (was flat 50.0 for every genome)."""

    def test_epsilon_changes_avg_reward(self):
        seed_records()
        rewards = {}
        for eps in (0.05, 0.5):
            random.seed(7)
            loop = RLTrainLoop()
            stats = loop.train_from_memory(
                source="SystemZ", n_episodes=15, max_steps=20,
                learning_rate=0.1, discount_factor=0.9, epsilon=eps)
            rewards[eps] = round(stats.get("avg_reward", 0.0), 4)
        self.assertGreaterEqual(len(set(rewards.values())), 2,
                                f"no gradient across epsilon: {rewards}")

    def test_policy_learns_severity_appropriate_actions(self):
        seed_records()
        random.seed(7)
        loop = RLTrainLoop()
        loop.train_from_memory(source="SystemZ", n_episodes=20, max_steps=30,
                               learning_rate=0.1, discount_factor=0.9,
                               epsilon=0.1)
        policy = {k: loop.ACTION_NAMES[max(v, key=lambda qk: v[qk])]
                  for k, v in loop._q_table.items()}
        self.assertEqual(policy.get("SystemZ:error"), "remediate")
        self.assertEqual(policy.get("SystemZ:warning"), "alert")

    def test_episode_records_action_distribution(self):
        seed_records()
        random.seed(7)
        loop = RLTrainLoop()
        loop.train_from_memory(source="SystemZ", n_episodes=4, max_steps=10,
                               learning_rate=0.1, discount_factor=0.9,
                               epsilon=0.3)
        self.assertIn("actions", loop._episodes[-1])
        self.assertTrue(loop._episodes[-1]["actions"])


class TestEvidenceRecommendations(unittest.TestCase):
    """Defect 5 + metacog wiring: real outcomes produce high-confidence
    recommendations that clear the adoption bar."""

    def _scenario(self, outcomes):
        mc = MetaCognitionWorm()
        for wt, outcome in outcomes:
            mc.observe(worm_type=wt, action="cluster_execute", outcome=outcome,
                       context={"cluster_id": f"c{outcome}{len(outcomes)}"})
        mc.analyze_patterns(worm_type=outcomes[0][0])
        rec = mc.recommend_from_evidence(outcomes[0][0])
        adapted = None
        if rec.get("recommended"):
            adapted = mc.adapt_strategy("global", "default",
                                        available_strategies=AVAIL,
                                        recommended=rec["recommended"],
                                        confidence=rec["confidence"])
        return rec, adapted, mc

    def test_error_heavy_recommends_defensive_and_adopts(self):
        rec, adapted, mc = self._scenario(
            [("security", "error")] * 8 + [("security", "success")] * 2)
        self.assertEqual(rec["recommended"], "defensive")
        self.assertGreaterEqual(rec["confidence"], 0.5)
        if adapted is None:
            self.fail("no strategy adapted despite confident recommendation")
        self.assertTrue(adapted["should_adapt"])
        self.assertEqual(mc.get_current_strategy("global"), "defensive")

    def test_success_heavy_recommends_aggressive(self):
        rec, adapted, _ = self._scenario(
            [("security", "success")] * 9 + [("security", "error")])
        self.assertEqual(rec["recommended"], "aggressive")
        if adapted is None:
            self.fail("no strategy adapted despite confident recommendation")
        self.assertTrue(adapted["should_adapt"])

    def test_insufficient_evidence_holds(self):
        rec, adapted, _ = self._scenario([("security", "error")] * 3)
        self.assertIsNone(rec["recommended"])
        self.assertIsNone(adapted)


class TestAdoptConfidenceGate(unittest.TestCase):
    """Defect 5: tepid suggestions must not flip the active strategy."""

    def test_low_confidence_holds(self):
        mc = MetaCognitionWorm()
        adapted = mc.adapt_strategy("global", "default",
                                    available_strategies=AVAIL,
                                    recommended="aggressive", confidence=0.1)
        self.assertFalse(adapted["should_adapt"])
        self.assertEqual(mc.get_current_strategy("global"), "default")

    def test_high_confidence_switches(self):
        mc = MetaCognitionWorm()
        adapted = mc.adapt_strategy("global", "default",
                                    available_strategies=AVAIL,
                                    recommended="aggressive", confidence=0.8)
        self.assertTrue(adapted["should_adapt"])
        self.assertEqual(mc.get_current_strategy("global"), "aggressive")


class TestStatePersistence(unittest.TestCase):
    """Defect 1: state must survive fresh instances (was lost to FTS ranking)."""

    def test_save_then_load_restores_strategy(self):
        mc = MetaCognitionWorm()
        mc.adapt_strategy("global", "default", available_strategies=AVAIL,
                          recommended="batched", confidence=0.9)
        mc.save_state()

        mc2 = MetaCognitionWorm()  # load_state runs in __init__
        self.assertEqual(mc2.get_current_strategy("global"), "batched")


class TestSteeringWiring(unittest.TestCase):
    """Defect 2/4: the tracked strategy must change what the dreamer does."""

    def test_aggressive_strategy_steers_pipeline(self):
        mc = MetaCognitionWorm()
        mc.adapt_strategy("global", "default", available_strategies=AVAIL,
                          recommended="aggressive", confidence=0.9)
        mc.save_state()

        dm = DreamingModule()
        result = dm.run_dreaming_pipeline(num_episodes=8, scenario_count=10,
                                          policy_fn=None)
        self.assertIsNotNone(result)
        rows = STORE.query_memories(event_type="strategy_steered", limit=1)
        self.assertTrue(rows, "no strategy_steered row recorded")
        data = rows[0].get("data", {})
        if isinstance(data, str):
            import json
            data = json.loads(data)
        self.assertEqual(data.get("strategy"), "aggressive")
        params = data.get("params") or data.get("tuning") or {}
        self.assertEqual(params.get("num_episodes"), 8)

    def test_dream_tuning_for_mapping(self):
        mc = MetaCognitionWorm()
        mc.adapt_strategy("global", "default", available_strategies=AVAIL,
                          recommended="conservative", confidence=0.9)
        tuning, strategy = mc.dream_tuning_for("global")
        self.assertEqual(strategy, "conservative")
        self.assertEqual(tuning.get("num_episodes"), 2)


class TestLearningSplitShim(unittest.TestCase):
    """Audit AR-2: learning.py split must keep every import path working."""

    def test_public_api_identities(self):
        from CORE.WORMS.expansion.learning_rl import RLTrainLoop as RL2
        from CORE.WORMS.expansion.learning_versioner import ModelVersioner as MV2
        from CORE.WORMS.expansion.learning_pipeline import DataPipelineWorm as DP2
        from CORE.WORMS.expansion.learning_features import SchemaAwareFeatureExtractor as SFE2
        from CORE.WORMS.expansion.learning_grpo import ABTestRunner as AB2
        from CORE.WORMS.expansion.learning_ab import ABTestRunner as AB_FALLBACK
        self.assertIs(RL2, RLTrainLoop)
        self.assertIs(MV2, ModelVersioner)
        self.assertIs(DP2, DataPipelineWorm)
        self.assertIs(SFE2, SchemaAwareFeatureExtractor)
        # The shim intentionally prefers the v2 DPO-capable ABTestRunner from
        # learning_grpo, falling back to the v1 stub in learning_ab (learning.py).
        self.assertIs(AB2, ABTestRunner)
        self.assertIsNot(AB_FALLBACK, ABTestRunner)
        self.assertIn("training_complete", DATA_SHAPES)
        self.assertIsInstance(DYNAMIC_SHAPES, dict)

    def test_classes_instantiate(self):
        for cls in (RLTrainLoop, ModelVersioner, DataPipelineWorm,
                    SchemaAwareFeatureExtractor, FeatureEngineer, ABTestRunner):
            self.assertIsNotNone(cls())

    def test_ab_runner(self):
        ab = ABTestRunner()
        ab.create_experiment("e1", ["a", "b"])
        ab.track("e1", "a", True)
        ab.track("e1", "b", False)
        r = ab.get_results("e1")
        self.assertEqual(r["variants"]["a"]["conversion_rate"], 1.0)
        self.assertEqual(r["variants"]["b"]["conversion_rate"], 0.0)


def tearDownModule():
    # Reset the process-global store singleton so the next test module in the
    # suite rebinds to its own scratch dir (first-import wins otherwise, and
    # the loser's tests hit "unable to open database file" after the winner
    # deletes its scratch).
    try:
        STORE.close() if hasattr(STORE, "close") else None
    except Exception as exc:
        note_failure("test_learning_loop:L261", exc)
    try:
        import CORE.local_memory as _lm
        _lm._global_store = None
    except Exception as exc:
        note_failure("test_learning_loop:L266", exc)
    # Do NOT rmtree the shared scratch dir here: another test module in the
    # suite may still be using it. OS temp cleans it up eventually.


if __name__ == "__main__":
    unittest.main(verbosity=2)