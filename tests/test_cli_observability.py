#!/usr/bin/env python3
"""Integration tests for cli_observability.py — subprocess CLI tests."""
import os
import sys
import subprocess
import unittest
import json
from pathlib import Path

# Path to the CLI script
CLI_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "cli_observability.py")
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestCliDashboardSubcommand(unittest.TestCase):
    """Test the `dashboard` subcommand via subprocess."""

    def _run_cli(self, *args, timeout=15):
        """Run cli_observability.py with given args and return result."""
        cmd = [sys.executable, CLI_SCRIPT] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
        return result

    def test_dashboard_help(self):
        """dashboard --help should show available flags."""
        result = self._run_cli("dashboard", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--health", result.stdout)
        self.assertIn("--metrics", result.stdout)
        self.assertIn("--drift", result.stdout)
        self.assertIn("--since", result.stdout)
        self.assertIn("--watch", result.stdout)
        self.assertIn("--json", result.stdout)

    def test_dashboard_json_output(self):
        """dashboard --json should output valid JSON."""
        result = self._run_cli("dashboard", "--json", timeout=20)
        self.assertEqual(result.returncode, 0)
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            self.fail(f"Invalid JSON output: {e}\nOutput: {result.stdout[:500]}")
        self.assertIn("timestamp", parsed)
        self.assertIn("health", parsed)
        self.assertIn("metrics", parsed)

    def test_dashboard_json_has_all_sections(self):
        """dashboard --json should contain all expected sections."""
        result = self._run_cli("dashboard", "--json", timeout=20)
        parsed = json.loads(result.stdout)
        expected = ["timestamp", "health", "metrics", "drift", "dead_letters",
                    "config", "session", "metacluster", "workflows", "scheduling"]
        for section in expected:
            with self.subTest(section=section):
                self.assertIn(section, parsed, f"Missing section: {section}")

    def test_dashboard_json_with_since(self):
        """dashboard --json --since 24h should include since field."""
        result = self._run_cli("dashboard", "--json", "--since", "24h", timeout=20)
        self.assertEqual(result.returncode, 0)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed.get("since"), "24h")

    def test_dashboard_json_health_ok(self):
        """dashboard --json health should have overall status."""
        result = self._run_cli("dashboard", "--json", timeout=20)
        parsed = json.loads(result.stdout)
        health = parsed.get("health", {})
        self.assertIn("overall", health)
        self.assertIn(health["overall"], ["ok", "down"])

    def test_dashboard_json_session_has_memories(self):
        """dashboard --json session should have total_memories."""
        result = self._run_cli("dashboard", "--json", timeout=20)
        parsed = json.loads(result.stdout)
        session = parsed.get("session", {})
        self.assertIn("total_memories", session)
        self.assertIsInstance(session["total_memories"], (int, float))


class TestCliHealthSubcommand(unittest.TestCase):
    """Test the `health` subcommand via subprocess."""

    def _run_cli(self, *args, timeout=15):
        cmd = [sys.executable, CLI_SCRIPT] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
        return result

    def test_health_help(self):
        """health --help should show available flags."""
        result = self._run_cli("health", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--check", result.stdout)


class TestCliTrainSubcommand(unittest.TestCase):
    """Test the `train` subcommand via subprocess."""

    def _run_cli(self, *args, timeout=30):
        cmd = [sys.executable, CLI_SCRIPT] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
        return result

    def test_train_help(self):
        """train --help should show available flags."""
        result = self._run_cli("train", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--episodes", result.stdout)
        self.assertIn("--limit", result.stdout)
        self.assertIn("--source", result.stdout)

    def test_train_runs_successfully(self):
        """train should execute one training pipeline and complete."""
        result = self._run_cli("train", "--episodes", "5", "--limit", "50", timeout=60)
        self.assertEqual(result.returncode, 0)
        self.assertIn("[OK]", result.stdout)


class TestCliHelp(unittest.TestCase):
    """Test top-level CLI help."""

    def _run_cli(self, *args, timeout=15):
        cmd = [sys.executable, CLI_SCRIPT] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
        return result

    def test_no_args_shows_help(self):
        """Running without args should print usage."""
        result = self._run_cli()
        self.assertIn("usage:", result.stdout.lower())

    def test_help_shows_subcommands(self):
        """--help should list all subcommands."""
        result = self._run_cli("--help")
        self.assertIn("dashboard", result.stdout)
        self.assertIn("health", result.stdout)
        self.assertIn("train", result.stdout)
        self.assertIn("all", result.stdout)


if __name__ == "__main__":
    unittest.main()
