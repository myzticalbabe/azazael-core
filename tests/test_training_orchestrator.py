#!/usr/bin/env python3
"""
Tests for TrainingOrchestrator, SB3 training dashboard card, and dashboard integration.
"""

import os
import sys
import json
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestTrainingOrchestratorImport(unittest.TestCase):
    """Test TrainingOrchestrator imports and basic instantiation."""

    def setUp(self):
        from CORE.worm_registry import discover_worms
        discover_worms()

    def test_import_orchestrator(self):
        """TrainingOrchestrator should import from scheduling module."""
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator
        self.assertIsNotNone(TrainingOrchestrator)

    def test_import_orchestrator_config(self):
        """OrchestratorConfig should import correctly."""
        from CORE.WORMS.expansion.scheduling import OrchestratorConfig
        config = OrchestratorConfig()
        self.assertEqual(config.algo, "PPO")
        self.assertEqual(config.total_timesteps, 5000)
        self.assertEqual(config.interval_hours, 24.0)
        self.assertTrue(config.register_model)

    def test_import_orchestrator_run(self):
        """OrchestratorRun should import correctly."""
        from CORE.WORMS.expansion.scheduling import OrchestratorRun
        run = OrchestratorRun(
            run_id="test", started_at="now", completed_at="later",
            duration_ms=100.0, algo="PPO", total_timesteps=500,
            records_used=50, data_source="test", trained=True,
        )
        self.assertEqual(run.algo, "PPO")
        self.assertTrue(run.trained)

    def test_create_orchestrator(self):
        """TrainingOrchestrator should instantiate with defaults."""
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator
        orch = TrainingOrchestrator()
        self.assertEqual(orch.VERSION, "1.0.0")
        self.assertEqual(orch.config.algo, "PPO")

    def test_create_with_custom_config(self):
        """TrainingOrchestrator should accept custom OrchestratorConfig."""
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator, OrchestratorConfig
        config = OrchestratorConfig(
            algo="DQN", total_timesteps=1000, interval_hours=48.0,
            source_filter="SystemZ",
        )
        orch = TrainingOrchestrator(config)
        self.assertEqual(orch.config.algo, "DQN")
        self.assertEqual(orch.config.total_timesteps, 1000)
        self.assertEqual(orch.config.source_filter, "SystemZ")

    def test_export_from_init(self):
        """TrainingOrchestrator should be exportable from expansion package."""
        from CORE.WORMS.expansion import TrainingOrchestrator as TO
        self.assertIsNotNone(TO)


class TestTrainingOrchestratorSmoke(unittest.TestCase):
    """Smoke tests that don't require SB3 training."""

    def setUp(self):
        from CORE.worm_registry import discover_worms
        discover_worms()

    def test_get_status_empty(self):
        """get_status should return correct state before any runs."""
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator
        orch = TrainingOrchestrator()
        status = orch.get_status()
        self.assertEqual(status["total_runs"], 0)
        self.assertEqual(status["trained_runs"], 0)
        self.assertFalse(status["scheduler_running"])
        self.assertIn("config", status)
        self.assertIn("recent_runs", status)

    def test_get_run_history_empty(self):
        """get_run_history should return empty list before any runs."""
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator
        orch = TrainingOrchestrator()
        history = orch.get_run_history()
        self.assertEqual(len(history), 0)

    def test_get_status_config(self):
        """get_status should reflect the configured parameters."""
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator, OrchestratorConfig
        config = OrchestratorConfig(algo="A2C", total_timesteps=3000, interval_hours=12.0)
        orch = TrainingOrchestrator(config)
        status = orch.get_status()
        cfg = status["config"]
        self.assertEqual(cfg["algo"], "A2C")
        self.assertEqual(cfg["total_timesteps"], 3000)
        self.assertEqual(cfg["interval_hours"], 12.0)

    def test_start_stop_scheduler(self):
        """start_scheduler creates thread; stop_scheduler sets stop_event."""
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator
        orch = TrainingOrchestrator()
        orch.start_scheduler(interval_hours=8760)  # 1 year = won't actually loop
        # Verify thread was created
        self.assertIsNotNone(orch._scheduler_thread)
        # Stop scheduler — stop_event set immediately
        orch.stop_scheduler()
        self.assertTrue(orch._stop_event.is_set())

    def test_double_start_scheduler(self):
        """start_scheduler should be idempotent (same thread)."""
        from CORE.WORMS.expansion.scheduling import TrainingOrchestrator
        orch = TrainingOrchestrator()
        orch.start_scheduler(interval_hours=8760)
        first_thread = orch._scheduler_thread
        orch.start_scheduler(interval_hours=8760)  # Second call should no-op
        self.assertIs(orch._scheduler_thread, first_thread)  # Same thread ref
        orch.stop_scheduler()


class TestSchedulingCardWebDashboard(unittest.TestCase):
    """Test that the SB3 training card renders in the web dashboard."""

    def test_build_html_contains_sb3_training_section(self):
        """Web dashboard HTML should contain SB3 Training section."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page()
        self.assertIn("SB3 Training", html)

    def test_build_html_sb3_section_structure(self):
        """SB3 Training section should have proper HTML structure."""
        from unittest import mock
        from CORE.web_dashboard import build_html_page
        with mock.patch("CORE.dashboard.get_report", return_value={}):
            html = build_html_page()
        # Should contain the section header
        self.assertIn("SB3 Training", html)
        # Should have the sub text for no data
        self.assertIn("No SB3 training runs yet", html)

    def test_export_html_contains_sb3_section(self):
        """Exported HTML should contain SB3 Training section."""
        from CORE.web_dashboard import export_html
        tmp_path = os.path.join(os.path.dirname(__file__), "_test_sb3_export.html")
        try:
            path = export_html(tmp_path)
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("SB3 Training", content)
            self.assertIn("<!DOCTYPE html>", content)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestDashboardReport(unittest.TestCase):
    """Test that dashboard.get_report() includes sb3_training section."""

    def test_report_contains_sb3_training_key(self):
        """get_report should include sb3_training key."""
        from CORE.dashboard import get_report
        report = get_report()
        self.assertIn("sb3_training", report)

    def test_sb3_training_structure(self):
        """sb3_training section should have expected structure."""
        from CORE.dashboard import get_report
        report = get_report()
        sb3 = report["sb3_training"]
        self.assertIn("recent_runs", sb3)
        self.assertIn("total_runs", sb3)
        self.assertIsInstance(sb3["recent_runs"], list)
        self.assertIsInstance(sb3["total_runs"], int)

    def test_sb3_training_runs_have_expected_fields(self):
        """Each sb3_training run should have the expected fields."""
        from CORE.dashboard import get_report
        report = get_report()
        for run in report["sb3_training"]["recent_runs"]:
            self.assertIn("run_id", run)
            self.assertIn("algo", run)
            self.assertIn("total_timesteps", run)
            self.assertIn("records_used", run)
            self.assertIn("data_source", run)


class TestSchedulingCardDataFlow(unittest.TestCase):
    """Test that the scheduling card renders with realistic data."""

    def test_build_html_all_cards_present(self):
        """All dashboard cards should be present in HTML."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page()
        sections = [
            "Health Check", "DB Monitor", "Metrics",
            "Drift Alerts", "Dead Letter", "Session",
            "MetaCluster", "Workflows", "Training",
            "SB3 Training", "Configuration",
        ]
        for section in sections:
            with self.subTest(section=section):
                self.assertIn(section, html)

    def test_build_html_not_empty(self):
        """HTML page should be substantial."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page()
        self.assertGreater(len(html), 2000)

    def test_build_html_valid_structure(self):
        """HTML should have proper document structure."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page()
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("</html>", html)
        self.assertIn("<head>", html)
        self.assertIn("<body>", html)

    def test_build_html_health_badge(self):
        """HTML should have health status badge."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page()
        self.assertIn("badge", html)
        self.assertIn("Health:", html)


class TestTrainingOrchestratorRegistry(unittest.TestCase):
    """Test TrainingOrchestrator is in worm registry."""

    def setUp(self):
        from CORE.worm_registry import discover_worms
        discover_worms()

    def test_registered_in_worm_registry(self):
        """TrainingOrchestrator should be in WORM_REGISTRY."""
        from CORE.worm_registry import WORM_REGISTRY
        self.assertIn("TrainingOrchestrator", WORM_REGISTRY)

    def test_registry_category(self):
        """TrainingOrchestrator should be in learning category."""
        from CORE.worm_registry import WORM_REGISTRY
        info = WORM_REGISTRY["TrainingOrchestrator"]
        self.assertEqual(info["category"], "learning")
        # Priority is adaptive (homeostat/reflex mutate + journal replay in
        # discover_worms changes 5->4), so assert validity, not an exact value.
        self.assertIn(int(info["priority"]), range(1, 11))

    def test_registry_category_list(self):
        """TrainingOrchestrator should appear in learning category worms."""
        from CORE.worm_registry import get_worms_by_category
        learning_worms = get_worms_by_category("learning")
        self.assertIn("TrainingOrchestrator", learning_worms)


class TestSB3AgentShapeFix(unittest.TestCase):
    """Test the SB3Agent shape fix for variable-length feature vectors."""

    def setUp(self):
        try:
            import stable_baselines3
            self.HAS_SB3 = True
        except ImportError:
            self.HAS_SB3 = False

    def test_create_env_with_variable_shapes(self):
        """_create_env_from_records should pad variable-length records."""
        if not self.HAS_SB3:
            self.skipTest("SB3 not installed")
        from CORE.sb3_agent import SB3Agent
        agent = SB3Agent(algo="PPO")

        records = [
            {"source": "Sys", "severity": "info", "data": {"cpu": 50}},
            {"source": "Net", "severity": "error", "data": {"cpu": 90, "latency": 200, "packets": 1500}},
            {"source": "Disk", "severity": "warning", "data": {"usage": 75, "io_wait": 30}},
        ]
        env = agent._create_env_from_records(records)
        self.assertIsNotNone(env)
        # All should be padded to same dimension (max fields)
        self.assertEqual(agent._obs_dim, 4)  # severity + cpu + latency + packets = 4
        self.assertEqual(agent._action_dim, 3)

        # Verify reset gives consistent shape
        obs, info = env.reset()
        self.assertEqual(len(obs), agent._obs_dim)

        # Verify step works
        obs2, reward, done, truncated, info = env.step(0)
        self.assertEqual(len(obs2), agent._obs_dim)


if __name__ == '__main__':
    unittest.main()
