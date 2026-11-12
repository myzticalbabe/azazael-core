#!/usr/bin/env python3
"""Tests for CORE/metrics.py — metrics, health check, drift alert."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestMetricsCollector(unittest.TestCase):
    """Test the in-memory metrics collector."""

    def setUp(self):
        from CORE.metrics import MetricsCollector
        self.collector = MetricsCollector(history_size=100)

    def test_record_and_list(self):
        """Recording a metric should make it listable."""
        self.collector.record("test_metric", 42.0, {"source": "test"})
        metrics = self.collector.list_metrics()
        self.assertIn("test_metric", metrics)

    def test_query(self):
        """Querying a metric should return data points."""
        self.collector.record("query_metric", 10.0)
        self.collector.record("query_metric", 20.0)
        points = self.collector.query("query_metric")
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].value, 10.0)

    def test_query_with_tags(self):
        """Querying with tags should filter correctly."""
        self.collector.record("tagged_metric", 1.0, {"worm": "A"})
        self.collector.record("tagged_metric", 2.0, {"worm": "B"})
        points = self.collector.query("tagged_metric", tags={"worm": "A"})
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].value, 1.0)

    def test_aggregate_empty(self):
        """Aggregating a non-existent metric should return zeros."""
        agg = self.collector.aggregate("nonexistent")
        self.assertEqual(agg["count"], 0)

    def test_aggregate_single(self):
        """Aggregating a single point should match."""
        self.collector.record("single", 42.0)
        agg = self.collector.aggregate("single")
        self.assertEqual(agg["count"], 1)
        self.assertEqual(agg["sum"], 42.0)
        self.assertEqual(agg["avg"], 42.0)
        self.assertEqual(agg["min"], 42.0)
        self.assertEqual(agg["max"], 42.0)

    def test_aggregate_multiple(self):
        """Aggregating multiple points should give correct stats."""
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            self.collector.record("multi", v)
        agg = self.collector.aggregate("multi")
        self.assertEqual(agg["count"], 5)
        self.assertEqual(agg["sum"], 15.0)
        self.assertEqual(agg["avg"], 3.0)
        self.assertEqual(agg["min"], 1.0)
        self.assertEqual(agg["max"], 5.0)

    def test_clear(self):
        """Clearing metrics should remove them."""
        self.collector.record("clear_me", 1.0)
        self.collector.clear("clear_me")
        self.assertNotIn("clear_me", self.collector.list_metrics())

    def test_clear_all(self):
        """Clearing all metrics should empty the collector."""
        self.collector.record("a", 1.0)
        self.collector.record("b", 2.0)
        self.collector.clear()
        self.assertEqual(len(self.collector.list_metrics()), 0)

    def test_snapshot(self):
        """Snapshot should return aggregate for each metric."""
        self.collector.record("snap", 10.0)
        self.collector.record("snap", 20.0)
        snap = self.collector.snapshot()
        self.assertIn("snap", snap)
        self.assertEqual(snap["snap"]["count"], 2)

    def test_history_limit(self):
        """History size should be limited."""
        for i in range(200):
            self.collector.record("bounded", float(i))
        points = self.collector.query("bounded")
        self.assertLessEqual(len(points), 100)


class TestHealthCheck(unittest.TestCase):
    """Test the health check system."""

    def setUp(self):
        from CORE.metrics import HealthCheck
        self.health = HealthCheck()

    def test_register_and_check(self):
        """Registering a health check should allow checking it."""
        self.health.register("test_comp", lambda: type("HC", (), {
            "name": "test_comp", "status": "ok", "message": "OK",
            "details": {}, "last_check": "",
        })())
        comp = self.health.check_component("test_comp")
        self.assertEqual(comp.status, "ok")

    def test_unregistered_check(self):
        """Checking an unregistered component should return 'down'."""
        comp = self.health.check_component("nope")
        self.assertEqual(comp.status, "down")

    def test_unregister(self):
        """Unregistering should remove the check."""
        self.health.register("temp", lambda: type("HC", (), {
            "name": "temp", "status": "ok", "message": "",
            "details": {}, "last_check": "",
        })())
        self.health.unregister("temp")
        comp = self.health.check_component("temp")
        self.assertEqual(comp.status, "down")

    def test_report_aggregation(self):
        """Report should aggregate all component statuses."""
        self.health.register("ok_comp", lambda: type("HC", (), {
            "name": "ok_comp", "status": "ok", "message": "",
            "details": {}, "last_check": "",
        })())
        self.health.register("fail_comp", lambda: type("HC", (), {
            "name": "fail_comp", "status": "down", "message": "FAIL",
            "details": {}, "last_check": "",
        })())
        report = self.health.report()
        self.assertEqual(report["overall"], "down")
        self.assertEqual(report["counts"]["ok"], 1)
        self.assertEqual(report["counts"]["down"], 1)

    def test_is_healthy_all_ok(self):
        """is_healthy should return True when all ok."""
        self.health.register("only_ok", lambda: type("HC", (), {
            "name": "only_ok", "status": "ok", "message": "",
            "details": {}, "last_check": "",
        })())
        self.assertTrue(self.health.is_healthy())

    def test_is_healthy_with_failure(self):
        """is_healthy should return False when any component is down."""
        self.health.register("ok_comp", lambda: type("HC", (), {
            "name": "ok_comp", "status": "ok", "message": "",
            "details": {}, "last_check": "",
        })())
        self.health.register("bad_comp", lambda: type("HC", (), {
            "name": "bad_comp", "status": "down", "message": "FAIL",
            "details": {}, "last_check": "",
        })())
        self.assertFalse(self.health.is_healthy())

    def test_check_function_exception(self):
        """A health check that throws should be caught."""
        def failing_check():
            raise RuntimeError("Kaboom!")
        self.health.register("exploder", failing_check)
        comp = self.health.check_component("exploder")
        self.assertEqual(comp.status, "down")
        self.assertIn("Kaboom", comp.message)


class TestDriftAlert(unittest.TestCase):
    """Test the drift alert system."""

    def setUp(self):
        from CORE.metrics import DriftAlert
        self.drift = DriftAlert()

    def test_check_no_registry(self):
        """Check should handle missing registry gracefully."""
        # Reset known worms so it doesn't detect drift from previous test runs
        self.drift.reset()
        issues = self.drift.check()
        self.assertIsInstance(issues, list)

    def test_reset(self):
        """Reset should clear known state."""
        self.drift._known_worms["test_worm"] = "1.0.0"
        self.drift.reset()
        self.assertEqual(len(self.drift._known_worms), 0)


if __name__ == "__main__":
    unittest.main()
