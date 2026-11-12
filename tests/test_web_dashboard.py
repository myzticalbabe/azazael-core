#!/usr/bin/env python3
"""Tests for CORE/web_dashboard.py — HTTP web dashboard, HTML export."""
import os
import sys
import json
import socket
import threading
import unittest
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestBuildHtmlPage(unittest.TestCase):
    """Test the HTML page builder functions."""

    def test_build_html_page_returns_string(self):
        """build_html_page should return a string."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page()
        self.assertIsInstance(html, str)
        self.assertGreater(len(html), 500)

    def test_build_html_contains_expected_sections(self):
        """HTML page should contain expected section headers."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page()
        self.assertIn("Azazael Observability", html)
        self.assertIn("Health Check", html)
        self.assertIn("Metrics", html)
        self.assertIn("Dead Letter", html)
        self.assertIn("MetaCluster", html)
        self.assertIn("Training", html)

    def test_build_html_contains_no_refresh_when_not_watch(self):
        """HTML page without watch should not have auto-refresh meta."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page(watch=False)
        self.assertNotIn("Auto-refresh", html)

    def test_build_html_contains_refresh_when_watch(self):
        """HTML page with watch should have auto-refresh info."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page(watch=True, interval=10)
        self.assertIn("Auto-refresh", html)
        self.assertIn("10s", html)

    def test_build_html_health_status_ok_on_success(self):
        """HTML should contain health OK status."""
        from CORE.web_dashboard import build_html_page
        html = build_html_page()
        self.assertIn("Health:", html)


class TestExportHtml(unittest.TestCase):
    """Test the HTML export function."""

    def setUp(self):
        self.tmp_path = os.path.join(os.path.dirname(__file__), "_test_export.html")

    def tearDown(self):
        if os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)

    def test_export_html_creates_file(self):
        """export_html should create a file at the given path."""
        from CORE.web_dashboard import export_html
        path = export_html(self.tmp_path)
        self.assertTrue(os.path.exists(path))
        self.assertIn("_test_export.html", path)

    def test_export_html_contains_valid_html(self):
        """Exported HTML should be valid."""
        from CORE.web_dashboard import export_html
        export_html(self.tmp_path)
        with open(self.tmp_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("<!DOCTYPE html>", content)
        self.assertIn("Azazael Observability", content)
        self.assertIn("</html>", content)

    def test_export_html_with_since(self):
        """export_html should accept since parameter."""
        from CORE.web_dashboard import export_html
        path = export_html(self.tmp_path, since="24h")
        self.assertTrue(os.path.exists(path))

    def test_export_html_reasonable_size(self):
        """Exported HTML should be reasonably sized (>1KB, <10MB)."""
        from CORE.web_dashboard import export_html
        export_html(self.tmp_path)
        size = os.path.getsize(self.tmp_path)
        self.assertGreater(size, 1000)
        self.assertLess(size, 10 * 1024 * 1024)


class TestWebDashboardServer(unittest.TestCase):
    """Test WebDashboardServer lifecycle and endpoint responses."""

    PORT = 18435

    @classmethod
    def setUpClass(cls):
        """Start web dashboard server in background."""
        # Pre-warm worm registry to avoid 38s cold-start timeout inside HTTP handler
        from CORE.worm_registry import discover_worms
        discover_worms()

        from CORE.web_dashboard import WebDashboardServer
        cls.server = WebDashboardServer(port=cls.PORT)
        cls.thread = threading.Thread(target=cls.server.start, daemon=True)
        cls.thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        """Stop web dashboard server."""
        cls.server.stop()

    def _query(self, path="/"):
        """Query the web dashboard and return response text."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(30)
        try:
            s.connect(("127.0.0.1", self.PORT))
            req = f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n"
            s.sendall(req.encode("utf-8"))
            # Read until EOF (HTTP/1.0 closes after the response). A single
            # recv() can return only part of the response depending on TCP
            # segmentation, making assertions flaky.
            chunks = []
            while True:
                chunk = s.recv(65535)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
            # Strip HTTP headers
            body = data.decode("utf-8").split("\r\n\r\n", 1)[-1]
            return body
        finally:
            s.close()

    def test_root_returns_html(self):
        """GET / should return HTML page."""
        body = self._query("/")
        self.assertIn("<!DOCTYPE html>", body)
        self.assertIn("Azazael Observability", body)

    def test_health_endpoint_returns_json(self):
        """GET /health should return JSON."""
        body = self._query("/health")
        parsed = json.loads(body)
        self.assertIn("timestamp", parsed)
        self.assertIn("health", parsed)

    def test_prometheus_endpoint_returns_text(self):
        """GET /prometheus should return prometheus format."""
        body = self._query("/prometheus")
        self.assertIn("azazael_health_ok", body)
        self.assertIn("azazael_total_memories", body)


class TestNotifier(unittest.TestCase):
    """Test notifier module functions."""

    def test_get_webhook_url_none(self):
        """get_webhook_url should return None when env var is unset."""
        if "SLACK_WEBHOOK_URL" in os.environ:
            del os.environ["SLACK_WEBHOOK_URL"]
        if "AZAZAEL_SLACK_WEBHOOK" in os.environ:
            del os.environ["AZAZAEL_SLACK_WEBHOOK"]
        from CORE.notifier import get_webhook_url
        self.assertIsNone(get_webhook_url())

    def test_get_webhook_url_from_env(self):
        """get_webhook_url should return value from env var."""
        os.environ["SLACK_WEBHOOK_URL"] = "https://hooks.slack.com/test"
        from CORE.notifier import get_webhook_url
        self.assertEqual(get_webhook_url(), "https://hooks.slack.com/test")
        del os.environ["SLACK_WEBHOOK_URL"]

    def test_send_slack_alert_no_webhook(self):
        """send_slack_alert should return False when no webhook set."""
        from CORE.notifier import send_slack_alert
        result = send_slack_alert("test message")
        self.assertFalse(result)

    def test_check_health_and_alert_runs(self):
        """check_health_and_alert should run without error."""
        from CORE.notifier import check_health_and_alert
        state = check_health_and_alert()
        self.assertIsNotNone(state)
        self.assertIn("overall", state)

    def test_check_drift_runs(self):
        """check_drift_and_alert should run without error."""
        from CORE.notifier import check_drift_and_alert
        count = check_drift_and_alert()
        self.assertIsInstance(count, int)

    def test_check_breakers_runs(self):
        """check_breakers_and_alert should run without error."""
        from CORE.notifier import check_breakers_and_alert
        count = check_breakers_and_alert()
        self.assertIsInstance(count, int)

    def test_run_check_returns_state(self):
        """run_check should return health state dict."""
        from CORE.notifier import run_check
        state = run_check()
        self.assertIsNotNone(state)
        self.assertIn("overall", state)
        self.assertIn("down_components", state)


class TestCliExportSubcommand(unittest.TestCase):
    """Test the `export` subcommand via subprocess."""

    CLI_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "cli_observability.py")
    PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")

    def setUp(self):
        self.tmp_json = os.path.join(os.path.dirname(__file__), "_test_export.json")
        self.tmp_html = os.path.join(os.path.dirname(__file__), "_test_export.html")

    def tearDown(self):
        for p in [self.tmp_json, self.tmp_html]:
            if os.path.exists(p):
                os.remove(p)

    def _run_cli(self, *args, timeout=30):
        import subprocess
        cmd = [sys.executable, self.CLI_SCRIPT] + list(args)
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=self.PROJECT_ROOT,
        )
        return result

    def test_export_json(self):
        """export output.json should write JSON file."""
        result = self._run_cli("export", self.tmp_json)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(self.tmp_json))
        with open(self.tmp_json, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("timestamp", data)
        self.assertIn("health", data)

    def test_export_help(self):
        """export --help should show available flags."""
        result = self._run_cli("export", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--json", result.stdout)
        self.assertIn("--html", result.stdout)

    def test_export_html(self):
        """export --html output.html should write HTML file."""
        result = self._run_cli("export", "--html", self.tmp_html)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(self.tmp_html))
        with open(self.tmp_html, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("<!DOCTYPE html>", content)
        self.assertIn("Azazael Observability", content)


if __name__ == "__main__":
    unittest.main()
