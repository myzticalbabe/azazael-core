#!/usr/bin/env python3
"""Tests for CORE/health_server.py — health endpoint JSON report."""
import os
import sys
import json
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestGetHealthJson(unittest.TestCase):
    """Test get_health_json function."""

    def test_get_health_json_returns_valid_json(self):
        """get_health_json should return valid JSON string."""
        from CORE.health_server import get_health_json
        result = get_health_json()
        self.assertIsInstance(result, str)
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError as e:
            self.fail(f"Invalid JSON: {e}")
        self.assertIsInstance(parsed, dict)

    def test_get_health_json_contains_health(self):
        """Health report should contain health section."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        self.assertIn("health", parsed)

    def test_get_health_json_contains_metrics(self):
        """Health report should contain metrics section."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        self.assertIn("metrics", parsed)

    def test_get_health_json_contains_drift(self):
        """Health report should contain drift section."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        self.assertIn("drift", parsed)

    def test_get_health_json_contains_breakers(self):
        """Health report should contain breakers section."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        self.assertIn("breakers", parsed)

    def test_get_health_json_contains_metacluster(self):
        """Health report should contain metacluster section."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        self.assertIn("metacluster", parsed)

    def test_get_health_json_contains_session(self):
        """Health report should contain session section (from dashboard.get_report)."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        self.assertIn("session", parsed)

    def test_health_overall_status(self):
        """Health report should have overall status."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        health = parsed["health"]
        self.assertIn("overall", health)
        self.assertIn(health["overall"], ["ok", "down"])

    def test_health_contains_counts(self):
        """Health report should have component counts."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        counts = parsed["health"].get("counts", {})
        self.assertIn("ok", counts)

    def test_dead_letters_contains_total(self):
        """Dead letters section should have total_dead_letters."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        dead = parsed.get("dead_letters", {})
        self.assertIn("total_dead_letters", dead)

    def test_session_contains_total_memories(self):
        """Session section should have total_memories."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        session = parsed.get("session", {})
        self.assertIn("total_memories", session)
        self.assertIsInstance(session["total_memories"], (int, float))

    def test_health_sections_not_error(self):
        """No section should contain an 'error' key at top level."""
        from CORE.health_server import get_health_json
        parsed = json.loads(get_health_json())
        for section in ["health", "metrics", "drift"]:
            self.assertNotIn("error", parsed.get(section, {}),
                             f"Section '{section}' has an error: {parsed[section]}")


class TestHealthServer(unittest.TestCase):
    """Test HealthServer lifecycle."""

    def test_init(self):
        """HealthServer should initialize with defaults."""
        from CORE.health_server import HealthServer
        server = HealthServer()
        self.assertEqual(server.host, "127.0.0.1")
        self.assertEqual(server.port, 18420)

    def test_init_custom_port(self):
        """HealthServer should accept custom port."""
        from CORE.health_server import HealthServer
        server = HealthServer(port=18422)
        self.assertEqual(server.port, 18422)

    def test_server_start_stop(self):
        """Server should start and stop cleanly."""
        from CORE.health_server import HealthServer
        import socket
        import threading
        import time

        server = HealthServer(port=18423)
        t = threading.Thread(target=server.start, daemon=True)
        t.start()
        time.sleep(0.5)

        self.assertTrue(server._running.is_set())

        # Query the server
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect(("127.0.0.1", 18423))
            data = s.recv(65535)
            s.close()
            self.assertGreater(len(data), 0)
            parsed = json.loads(data.decode("utf-8"))
            self.assertIn("health", parsed)
        except Exception as e:
            self.fail(f"Server query failed: {e}")
        finally:
            server.stop()
            time.sleep(0.2)
            self.assertFalse(server._running.is_set())

    def test_start_background(self):
        """start_background should start in daemon thread."""
        from CORE.health_server import HealthServer
        server = HealthServer(port=18424)
        t = server.start_background()
        import time
        time.sleep(0.3)
        self.assertTrue(t.is_alive())
        self.assertTrue(t.daemon)
        server.stop()


if __name__ == "__main__":
    unittest.main()
