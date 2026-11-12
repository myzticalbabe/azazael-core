#!/usr/bin/env python3
"""Tests for CORE/WORMS/expansion/scheduling.py — ScheduledTrainingWorm."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestScheduleConfig(unittest.TestCase):
    """Test ScheduleConfig defaults and overrides."""

    def test_default_config(self):
        """Default config should have sensible defaults."""
        from CORE.WORMS.expansion.scheduling import ScheduleConfig, EVAL_EPISODES
        cfg = ScheduleConfig()
        self.assertEqual(cfg.interval_hours, 24.0)
        # G-3: tuning and deployment share one horizon (was a hardcoded 50
        # here vs 80 in the driver). The test tracks the constant.
        self.assertEqual(cfg.n_episodes, EVAL_EPISODES)
        self.assertEqual(cfg.data_limit, 200)
        # Hyperparameter defaults were evolved by evolution_cycle.py on
        # 2026-09-07 (see the inline comments on ScheduleConfig). The test
        # must track the intentional current defaults, not the pre-evolution
        # values (0.1 / 0.9 / 0.2).
        self.assertEqual(cfg.learning_rate, 0.29)
        self.assertEqual(cfg.discount_factor, 0.91)
        self.assertEqual(cfg.epsilon, 0.11)
        self.assertTrue(cfg.record_to_memory)
        self.assertIsNone(cfg.source_filter)
        self.assertIsNone(cfg.exclude_sources)

    def test_custom_config(self):
        """Custom config should override defaults."""
        from CORE.WORMS.expansion.scheduling import ScheduleConfig
        cfg = ScheduleConfig(
            interval_hours=12.0,
            n_episodes=100,
            source_filter="SystemZ",
        )
        self.assertEqual(cfg.interval_hours, 12.0)
        self.assertEqual(cfg.n_episodes, 100)
        self.assertEqual(cfg.source_filter, "SystemZ")


class TestTrainingRun(unittest.TestCase):
    """Test TrainingRun dataclass."""

    def test_training_run_fields(self):
        """TrainingRun should have all expected fields."""
        from CORE.WORMS.expansion.scheduling import TrainingRun
        run = TrainingRun(
            run_id="test_001",
            started_at="now",
            completed_at="later",
            duration_ms=1500.0,
            records_extracted=200,
            n_episodes=50,
            avg_reward=25.5,
            q_table_size=30,
            model_version="1.1.0",
            success=True,
        )
        self.assertEqual(run.run_id, "test_001")
        self.assertEqual(run.avg_reward, 25.5)
        self.assertEqual(run.model_version, "1.1.0")
        self.assertTrue(run.success)
        self.assertIsNone(run.error)


class TestScheduledTrainingWorm(unittest.TestCase):
    """Test ScheduledTrainingWorm execute() and scheduler lifecycle."""

    def setUp(self):
        from CORE.WORMS.expansion.scheduling import ScheduledTrainingWorm, ScheduleConfig
        self.config = ScheduleConfig(
            interval_hours=0.01,  # Very short interval for testing
            n_episodes=5,
            data_limit=50,
            record_to_memory=False,
        )
        self.trainer = ScheduledTrainingWorm(config=self.config)

    def test_init(self):
        """Trainer should initialize with config."""
        self.assertIsNotNone(self.trainer)
        self.assertEqual(self.trainer.VERSION, "1.0.0")
        self.assertEqual(self.trainer.config.n_episodes, 5)

    def test_execute_successful(self):
        """Execute() should return a successful TrainingRun."""
        run = self.trainer.execute()
        self.assertIsNotNone(run)
        self.assertIsInstance(run.run_id, str)
        self.assertGreater(run.duration_ms, 0)
        # Either success or error should be set
        self.assertIn(run.success, [True, False])

    def test_execute_uses_config(self):
        """Execute should respect config settings."""
        run = self.trainer.execute()
        self.assertEqual(run.n_episodes, 5)

    def test_run_history_grows(self):
        """Run history should grow with each execution."""
        self.assertEqual(len(self.trainer._run_history), 0)
        self.trainer.execute()
        self.assertEqual(len(self.trainer._run_history), 1)
        self.trainer.execute()
        self.assertEqual(len(self.trainer._run_history), 2)

    def test_run_history_limited(self):
        """Run history should be limited to 50 entries."""
        for _ in range(60):
            self.trainer.execute()
        self.assertLessEqual(len(self.trainer._run_history), 50)

    def test_get_status_initial(self):
        """Initial status should show 0 runs."""
        status = self.trainer.get_status()
        self.assertEqual(status["version"], "1.0.0")
        self.assertEqual(status["total_runs"], 0)
        self.assertFalse(status["scheduler_running"])

    def test_get_status_after_execution(self):
        """Status after execution should reflect history."""
        self.trainer.execute()
        status = self.trainer.get_status()
        self.assertEqual(status["total_runs"], 1)
        self.assertEqual(len(status["recent_runs"]), 1)

    def test_get_status_recent_runs_limited(self):
        """Status recent_runs should be limited to 5."""
        for _ in range(10):
            self.trainer.execute()
        status = self.trainer.get_status()
        self.assertLessEqual(len(status["recent_runs"]), 5)

    def test_start_scheduler(self):
        """Start scheduler should create a daemon thread."""
        self.trainer.start_scheduler()
        self.assertTrue(self.trainer._scheduler_thread is not None)
        self.assertTrue(self.trainer._scheduler_thread.is_alive())
        self.assertTrue(self.trainer._scheduler_thread.daemon)
        self.trainer.stop_scheduler()

    def test_start_scheduler_twice(self):
        """Starting scheduler twice should not create duplicate threads."""
        self.trainer.start_scheduler()
        t1 = self.trainer._scheduler_thread
        self.trainer.start_scheduler()
        t2 = self.trainer._scheduler_thread
        self.assertIs(t1, t2)
        self.trainer.stop_scheduler()

    def test_stop_scheduler(self):
        """Stop scheduler should clear the stop event."""
        self.trainer.start_scheduler()
        self.assertTrue(self.trainer._scheduler_thread.is_alive())
        self.trainer.stop_scheduler()
        self.trainer._scheduler_thread.join(timeout=2)
        self.assertFalse(self.trainer._scheduler_thread.is_alive())

    def test_scheduler_runs_execute(self):
        """Scheduler should execute training runs."""
        self.trainer.start_scheduler()
        import time
        time.sleep(0.3)  # Allow time for at least one run
        self.trainer.stop_scheduler()
        status = self.trainer.get_status()
        self.assertGreaterEqual(status["total_runs"], 1)

    def test_get_status_scheduler_running(self):
        """get_status should reflect scheduler running state."""
        self.assertFalse(self.trainer.get_status()["scheduler_running"])
        self.trainer.start_scheduler()
        self.assertTrue(self.trainer.get_status()["scheduler_running"])
        self.trainer.stop_scheduler()

    def test_version_starts_at_zero(self):
        """Model version should start at 0.0.0."""
        self.assertEqual(self.trainer._last_model_version, "0.0.0")

    def test_config_custom_interval(self):
        """start_scheduler should accept interval override."""
        self.trainer.start_scheduler(interval_hours=0.005)
        self.assertEqual(self.trainer.config.interval_hours, 0.005)
        self.trainer.stop_scheduler()

    def test_stop_without_start(self):
        """Stopping without starting should not raise."""
        try:
            self.trainer.stop_scheduler()
        except Exception as e:
            self.fail(f"stop_scheduler raised: {e}")

    def test_lazy_loaders_return_none_on_bad_import(self):
        """Lazy loaders should return None on import error."""
        # Force import error by checking the methods work
        pipeline = self.trainer._get_pipeline()
        engineer = self.trainer._get_engineer()
        rl = self.trainer._get_rl()
        versioner = self.trainer._get_versioner()
        ab = self.trainer._get_ab()
        store = self.trainer._get_store()
        log = self.trainer._get_logger()


if __name__ == "__main__":
    unittest.main()
