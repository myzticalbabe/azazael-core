#!/usr/bin/env python3
"""Integration tests for CORE/health_server.py — health + Prometheus endpoints."""
import os
import sys
import json
import socket
import threading
import unittest
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestHealthEndpointIntegration(unittest.TestCase):
    """Integration tests for the HealthServer TCP endpoint."""

    HEALTH_PORT = 18425  # Unique port to avoid conflicts
    PROM_PORT = 18426

    @classmethod
    def setUpClass(cls):
        """Start both servers in background threads."""
        from CORE.health_server import HealthServer, PrometheusServer

        # Health server
        cls.health_server = HealthServer(port=cls.HEALTH_PORT)
        cls.health_thread = threading.Thread(
            target=cls.health_server.start, daemon=True
        )
        cls.health_thread.start()
        time.sleep(0.5)

        # Prometheus server
        cls.prom_server = PrometheusServer(port=cls.PROM_PORT)
        cls.prom_thread = threading.Thread(
            target=cls.prom_server.start, daemon=True
        )
        cls.prom_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        """Stop both servers."""
        cls.health_server.stop()
        cls.prom_server.stop()

    def _query_health(self):
        """Query the health endpoint and return parsed JSON."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(("127.0.0.1", self.HEALTH_PORT))
            chunks = []
            while True:
                part = s.recv(65535)
                if not part:
                    break
                chunks.append(part)
            data = b"".join(chunks)
            return json.loads(data.decode("utf-8"))
        finally:
            s.close()

    def _query_prometheus(self):
        """Query the Prometheus endpoint and return raw text."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(("127.0.0.1", self.PROM_PORT))
            data = s.recv(65535)
            return data.decode("utf-8")
        finally:
            s.close()

    # ── Health Endpoint Tests ──

    def test_health_endpoint_responds(self):
        """Health endpoint should return data."""
        data = self._query_health()
        self.assertIsInstance(data, dict)

    def test_health_endpoint_has_all_sections(self):
        """Health endpoint should have all expected sections."""
        data = self._query_health()
        expected = ["timestamp", "health", "metrics", "drift", "dead_letters",
                    "config", "session", "metacluster", "workflows", "scheduling", "breakers"]
        for section in expected:
            with self.subTest(section=section):
                self.assertIn(section, data, f"Missing section: {section}")

    def test_health_endpoint_health_ok(self):
        """Health section should report overall status."""
        data = self._query_health()
        health = data.get("health", {})
        self.assertIn("overall", health)
        self.assertIn(health["overall"], ["ok", "down"])

    def test_health_endpoint_no_top_level_errors(self):
        """No top-level error key should be present."""
        data = self._query_health()
        self.assertNotIn("error", data)

    def test_health_endpoint_session_has_memories(self):
        """Session section should have total memories."""
        data = self._query_health()
        session = data.get("session", {})
        self.assertIn("total_memories", session)
        self.assertIsInstance(session["total_memories"], (int, float))

    def test_health_endpoint_break_present(self):
        """Breakers section should be a list."""
        data = self._query_health()
        self.assertIn("breakers", data)
        self.assertIsInstance(data["breakers"], list)

    # ── Prometheus Endpoint Tests ──

    def test_prometheus_endpoint_responds(self):
        """Prometheus endpoint should return text."""
        data = self._query_prometheus()
        self.assertGreater(len(data), 0)

    def test_prometheus_has_help_lines(self):
        """Prometheus output should have # HELP lines for all families."""
        data = self._query_prometheus()
        self.assertIn("# HELP azazael_health_ok", data)
        self.assertIn("# HELP azazael_metric_count", data)
        self.assertIn("# HELP azazael_total_memories", data)
        self.assertIn("# HELP azazael_training_runs_total", data)
        self.assertIn("# HELP azazael_worms_total", data)
        self.assertIn("# HELP azazael_orchestration_runs_total", data)

    def test_prometheus_has_type_lines(self):
        """Prometheus output should have # TYPE lines for all families."""
        data = self._query_prometheus()
        self.assertIn("# TYPE azazael_health_ok gauge", data)
        self.assertIn("# TYPE azazael_metric_count gauge", data)
        self.assertIn("# TYPE azazael_metric_avg gauge", data)
        self.assertIn("# TYPE azazael_total_memories gauge", data)
        self.assertIn("# TYPE azazael_total_sessions gauge", data)
        self.assertIn("# TYPE azazael_training_runs_total counter", data)
        self.assertIn("# TYPE azazael_training_reward_avg gauge", data)
        self.assertIn("# TYPE azazael_worms_total gauge", data)
        self.assertIn("# TYPE azazael_orchestration_runs_total counter", data)

    def test_prometheus_has_metric_values(self):
        """Prometheus output should have actual metric values."""
        data = self._query_prometheus()
        self.assertIn("azazael_health_ok ", data)
        self.assertIn("azazael_total_memories ", data)

    def test_prometheus_health_value_is_binary(self):
        """azazael_health_ok should be 0 or 1."""
        data = self._query_prometheus()
        for line in data.splitlines():
            if line.startswith("azazael_health_ok "):
                value = line.split(" ")[-1].strip()
                self.assertIn(value, ["0", "1"])
                return
        self.fail("azazael_health_ok line not found")

    def test_prometheus_training_lines(self):
        """Prometheus should have training metric lines."""
        data = self._query_prometheus()
        lines = data.splitlines()
        training_lines = [l for l in lines if l.startswith("azazael_training_")]
        self.assertGreater(len(training_lines), 0, "No training metric lines found")

    def test_prometheus_worm_lines(self):
        """Prometheus should have worm/orchestration metric lines."""
        data = self._query_prometheus()
        lines = data.splitlines()
        worm_lines = [l for l in lines if l.startswith("azazael_worms_") or l.startswith("azazael_orchestration_")]
        self.assertGreaterEqual(len(worm_lines), 2, "Missing worm/orchestration lines")

    def test_prometheus_format_has_blank_lines(self):
        """Prometheus format should have blank lines between families."""
        data = self._query_prometheus()
        lines = data.splitlines()
        blank_positions = [i for i, l in enumerate(lines) if l == ""]
        self.assertGreaterEqual(len(blank_positions), 3,
                                f"Expected >=3 blank lines, got {len(blank_positions)}")

    def test_prometheus_components_line(self):
        """Prometheus should have components_total with status label."""
        data = self._query_prometheus()
        self.assertIn("azazael_components_total{", data)


class TestPrometheusMetrics(unittest.TestCase):
    """Test get_prometheus_metrics function directly."""

    def test_prometheus_contains_all_metrics(self):
        """get_prometheus_metrics should contain all expected metrics."""
        from CORE.health_server import get_prometheus_metrics
        metrics = get_prometheus_metrics()
        required = [
            "azazael_health_ok ",
            "azazael_total_memories ",
            "azazael_total_sessions ",
            "azazael_training_runs_total ",
            "azazael_worms_total ",
            "azazael_orchestration_runs_total ",
        ]
        for key in required:
            with self.subTest(key=key):
                self.assertIn(key, metrics, f"Missing metric: {key}")

    def test_prometheus_correct_line_count(self):
        """get_prometheus_metrics should have reasonable line count."""
        from CORE.health_server import get_prometheus_metrics
        metrics = get_prometheus_metrics()
        lines = [l for l in metrics.splitlines() if l]
        self.assertGreater(len(lines), 15, f"Too few non-empty lines: {len(lines)}")


class TestPrometheusServer(unittest.TestCase):
    """Test PrometheusServer lifecycle."""

    def test_init(self):
        """PrometheusServer should initialize with defaults."""
        from CORE.health_server import PrometheusServer
        server = PrometheusServer()
        self.assertEqual(server.host, "127.0.0.1")
        self.assertEqual(server.port, 18421)

    def test_init_custom_port(self):
        """PrometheusServer should accept custom port."""
        from CORE.health_server import PrometheusServer
        server = PrometheusServer(port=18430)
        self.assertEqual(server.port, 18430)

    def test_start_stop(self):
        """PrometheusServer should start and stop cleanly."""
        from CORE.health_server import PrometheusServer
        server = PrometheusServer(port=18431)
        t = threading.Thread(target=server.start, daemon=True)
        t.start()
        time.sleep(0.5)

        self.assertTrue(server._running.is_set())

        # Query the server
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect(("127.0.0.1", 18431))
            data = s.recv(65535)
            s.close()
            self.assertGreater(len(data), 0)
            self.assertIn("azazael_health_ok", data.decode("utf-8"))
        except Exception as e:
            self.fail(f"Prometheus query failed: {e}")
        finally:
            server.stop()
            time.sleep(0.2)
            self.assertFalse(server._running.is_set())

    def test_start_background(self):
        """start_background should start daemon thread."""
        from CORE.health_server import PrometheusServer
        server = PrometheusServer(port=18432)
        t = server.start_background()
        time.sleep(0.3)
        self.assertTrue(t.is_alive())
        self.assertTrue(t.daemon)
        server.stop()


if __name__ == "__main__":
    unittest.main()
