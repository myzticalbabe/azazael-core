#!/usr/bin/env python3
"""Tests for CORE/logging.py — structured JSON logging."""
import os
import sys
import json
import unittest
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestStructuredLogger(unittest.TestCase):
    """Test basic logger creation and message formatting."""

    def test_get_logger(self):
        """Getting a logger should return a StructuredLogger."""
        from CORE.logging import get_logger
        log = get_logger("test_logger")
        self.assertIsNotNone(log)
        self.assertEqual(log.name, "test_logger")

    def test_logger_cached(self):
        """Getting the same logger twice should return the same instance."""
        from CORE.logging import get_logger
        log1 = get_logger("cached_test")
        log2 = get_logger("cached_test")
        self.assertIs(log1, log2)

    def test_configure_logging(self):
        """Configuring logging should not raise."""
        from CORE.logging import configure_logging
        try:
            configure_logging(level="DEBUG")
        except Exception as e:
            self.fail(f"configure_logging raised: {e}")

    def test_log_levels(self):
        """All log level methods should work without error."""
        from CORE.logging import get_logger
        log = get_logger("level_test")
        try:
            log.debug("debug_msg", {"key": "val"})
            log.info("info_msg", {"key": "val"})
            log.warning("warning_msg", {"key": "val"})
            log.error("error_msg", {"key": "val"})
            log.critical("critical_msg", {"key": "val"})
        except Exception as e:
            self.fail(f"Log level method raised: {e}")

    def test_log_with_kwargs(self):
        """Logging with **kwargs should work."""
        from CORE.logging import get_logger
        log = get_logger("kwargs_test")
        try:
            log.info("kwargs_msg", worm="SystemZ", duration_ms=150)
        except Exception as e:
            self.fail(f"Log with kwargs raised: {e}")

    def test_log_with_no_data(self):
        """Logging without data should work."""
        from CORE.logging import get_logger
        log = get_logger("no_data_test")
        try:
            log.info("no_data_msg")
        except Exception as e:
            self.fail(f"Log without data raised: {e}")


class TestJsonFormatter(unittest.TestCase):
    """Test that JSON formatting works correctly."""

    def test_formatter_creates_valid_json(self):
        """The formatter should produce valid JSON."""
        from CORE.logging import StructuredLogger
        import io
        log = StructuredLogger("json_test")
        # Capture log output
        import logging
        handler = log._logger.handlers[0]
        handler.setStream(stream := io.StringIO())

        log.info("test_message", {"status": "ok"})
        output = stream.getvalue()

        # Should be valid JSON
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as e:
            self.fail(f"Formatter did not produce valid JSON: {e}")

        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["source"], "json_test")
        self.assertEqual(parsed["message"], "test_message")
        self.assertIn("timestamp", parsed)
        self.assertIn("thread_id", parsed)
        self.assertIn("pid", parsed)


if __name__ == "__main__":
    unittest.main()
