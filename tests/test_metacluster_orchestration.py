#!/usr/bin/env python3
"""Tests for MetaCluster orchestration — config wiring, timeout handling, circuit breaker integration."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestMetaClusterDiscovery(unittest.TestCase):
    """Test MetaCluster discovery and initialization."""

    def test_meta_cluster_import(self):
        """MetaCluster should import without error."""
        from CORE.WORMS.METACLUSTER import MetaCluster
        self.assertIsNotNone(MetaCluster)

    def test_meta_cluster_init(self):
        """MetaCluster should initialize with correct defaults."""
        from CORE.WORMS.METACLUSTER import MetaCluster
        mc = MetaCluster()
        self.assertIsNotNone(mc)
        self.assertEqual(mc.VERSION, "2.0.0")
        self.assertIn("pre_category", mc._hooks)
        self.assertIn("post_category", mc._hooks)

    def test_discover_all(self):
        """Discover all should return category counts dict."""
        from CORE.WORMS.METACLUSTER import MetaCluster
        mc = MetaCluster()
        counts = mc.discover_all()
        self.assertIsInstance(counts, dict)

    def test_discover_all_returns_counts(self):
        """Discovery should return per-category worm counts."""
        from CORE.WORMS.METACLUSTER import MetaCluster
        mc = MetaCluster()
        counts = mc.discover_all()
        if counts:
            for category, count in counts.items():
                self.assertGreater(count, 0, f"Category '{category}' has 0 worms")


class TestMetaClusterHooks(unittest.TestCase):
    """Test MetaCluster hook system."""

    def setUp(self):
        from CORE.WORMS.METACLUSTER import MetaCluster
        self.mc = MetaCluster()

    def test_register_hook(self):
        """Registering a hook should add it."""
        calls = []
        self.mc.on("pre_category", lambda **kw: calls.append(kw))
        self.mc._run_hooks("pre_category", category="test")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["category"], "test")

    def test_register_invalid_hook(self):
        """Registering an invalid hook name should be ignored."""
        self.mc.on("nonexistent", lambda: None)
        # Should not raise

    def test_multiple_hooks(self):
        """Multiple hooks on the same event should all fire."""
        calls = []
        self.mc.on("on_insight", lambda **kw: calls.append("a"))
        self.mc.on("on_insight", lambda **kw: calls.append("b"))
        self.mc._run_hooks("on_insight", insight="test")
        self.assertEqual(len(calls), 2)

    def test_hook_exception_does_not_crash(self):
        """A hook that raises should not crash the system."""
        self.mc.on("on_error", lambda **kw: 1 / 0)
        self.mc.on("on_error", lambda **kw: None)
        try:
            self.mc._run_hooks("on_error", error="test")
        except Exception:
            self.fail("Hook exception propagated!")


class TestMetaClusterOrchestration(unittest.TestCase):
    """Test MetaCluster orchestration workflow."""

    def setUp(self):
        from CORE.WORMS.METACLUSTER import MetaCluster
        self.mc = MetaCluster()
        self.mc.discover_all()

    def test_get_status(self):
        """Status should return expected keys."""
        status = self.mc.get_status()
        self.assertIn("version", status)
        self.assertIn("ncm", status)
        self.assertIn("routing_paths", status)
        self.assertIn("cross_category_routes", status)
        self.assertIn("free_threaded", status)

    def test_orchestrate_category(self):
        """Orchestrate a single category should succeed (may route through downstream categories)."""
        if not self.mc.categories:
            self.skipTest("No categories discovered")
        category = list(self.mc.categories.keys())[0]
        result = self.mc.orchestrate_category(category)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.categories_executed, 1, "At least 1 category should execute")
        self.assertIn("orchestration_id", result.__dict__)

    def test_orchestrate_returns_meta_result(self):
        """Orchestrate should return MetaClusterResult."""
        if not self.mc.categories:
            self.skipTest("No categories discovered")
        result = self.mc.orchestrate(categories=list(self.mc.categories.keys())[:1])
        self.assertTrue(hasattr(result, "orchestration_id"))
        self.assertTrue(hasattr(result, "categories_executed"))
        self.assertTrue(hasattr(result, "total_worms_ok"))
        self.assertTrue(hasattr(result, "insights"))

    def test_orchestrate_records_duration(self):
        """Orchestrate duration should be positive."""
        if not self.mc.categories:
            self.skipTest("No categories discovered")
        result = self.mc.orchestrate(categories=list(self.mc.categories.keys())[:1])
        self.assertGreater(result.duration_ms, 0)

    def test_get_last_result(self):
        """Last result should be None before first orchestration."""
        self.assertIsNone(self.mc.get_last_result())

    def test_orchestrate_updates_last_result(self):
        """After orchestration, last result should be set."""
        if not self.mc.categories:
            self.skipTest("No categories discovered")
        category = list(self.mc.categories.keys())[0]
        self.mc.orchestrate_category(category)
        self.assertIsNotNone(self.mc.get_last_result())

    def test_export_state(self):
        """Export state should write a JSON file."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        try:
            exported = self.mc.export_state(tmp_path)
            self.assertTrue(os.path.exists(exported))
            import json
            with open(exported, encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("version", data)
            self.assertIn("routes", data)
            self.assertIn("orchestration_history", data)
        finally:
            os.unlink(tmp_path)


class TestMetaClusterMermaid(unittest.TestCase):
    """Test MetaCluster Mermaid graph generation."""

    def setUp(self):
        from CORE.WORMS.METACLUSTER import MetaCluster
        self.mc = MetaCluster()
        self.mc.discover_all()

    def test_to_mermaid(self):
        """Mermaid graph should return a non-empty string."""
        graph = self.mc.to_mermaid()
        self.assertIsInstance(graph, str)
        self.assertTrue(graph.startswith("graph"))

    def test_to_mermaid_execution_flow_empty(self):
        """Execution flow with no results should show empty state."""
        graph = self.mc.to_mermaid_execution_flow()
        self.assertIn("No orchestration", graph)

    def test_to_mermaid_routing_topology(self):
        """Routing topology should return a valid graph."""
        graph = self.mc.to_mermaid_routing_topology()
        self.assertIsInstance(graph, str)
        self.assertTrue(graph.startswith("graph"))


class TestConfigWiring(unittest.TestCase):
    """Test that config is properly wired into MetaCluster."""

    def test_get_config_lazy(self):
        """Lazy config function should return config or None."""
        from CORE.WORMS.METACLUSTER import _get_config
        cfg = _get_config()
        self.assertIsNotNone(cfg)

    def test_config_has_cluster_settings(self):
        """Config should have cluster settings."""
        from CORE.WORMS.METACLUSTER import _get_config
        cfg = _get_config()
        if cfg:
            self.assertTrue(hasattr(cfg, "clusters"))
            self.assertGreater(cfg.clusters.default_timeout, 0)

    def test_get_breakers_lazy(self):
        """Lazy breaker registry should return registry or None."""
        from CORE.WORMS.METACLUSTER import _get_breakers
        breakers = _get_breakers()
        self.assertIsNotNone(breakers)

    def test_get_log_lazy(self):
        """Lazy logger should return logger or None."""
        from CORE.WORMS.METACLUSTER import _get_log
        log = _get_log()
        self.assertIsNotNone(log)


class TestCircuitBreakerIntegration(unittest.TestCase):
    """Test circuit breaker integration with cluster execution."""

    def test_breaker_registry_available(self):
        """Breaker registry should be available from lazy import."""
        from CORE.WORMS.METACLUSTER import _get_breakers
        breakers = _get_breakers()
        self.assertIsNotNone(breakers)
        stats = breakers.all_stats()
        self.assertIsInstance(stats, list)


if __name__ == "__main__":
    unittest.main()
