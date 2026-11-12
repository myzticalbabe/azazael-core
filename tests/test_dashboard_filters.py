#!/usr/bin/env python3
"""Tests for CORE/dashboard.py — _parse_since() and since-filtered get_report()."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestParseSince(unittest.TestCase):
    """Test the _parse_since time filter parser."""

    def setUp(self):
        from CORE.dashboard import _parse_since
        self._parse_since = _parse_since

    def test_none_returns_none(self):
        """None should return None."""
        result = self._parse_since(None)
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        result = self._parse_since("")
        self.assertIsNone(result)

    def test_invalid_string_returns_none(self):
        """Invalid string should return None."""
        result = self._parse_since("not_a_time")
        self.assertIsNone(result)

    def test_garbage_string_returns_none(self):
        """Garbage input should return None."""
        result = self._parse_since("xyz")
        self.assertIsNone(result)

    def test_hours_format(self):
        """24h should return a timestamp ~24 hours ago."""
        import time
        now = time.time()
        result = self._parse_since("24h")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, now - 24 * 3600, delta=2)

    def test_minutes_format(self):
        """30m should return a timestamp ~30 minutes ago."""
        import time
        now = time.time()
        result = self._parse_since("30m")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, now - 30 * 60, delta=2)

    def test_seconds_format(self):
        """3600s should return a timestamp ~3600 seconds ago."""
        import time
        now = time.time()
        result = self._parse_since("3600s")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, now - 3600, delta=2)

    def test_iso_datetime_format(self):
        """ISO datetime string should parse correctly."""
        result = self._parse_since("2026-01-15T10:30:00")
        self.assertIsNotNone(result)
        from datetime import datetime
        expected = datetime.fromisoformat("2026-01-15T10:30:00").timestamp()
        self.assertEqual(result, expected)

    def test_date_only_format(self):
        """Date-only string should parse (start of day)."""
        result = self._parse_since("2026-01-15")
        self.assertIsNotNone(result)
        from datetime import datetime
        expected = datetime.fromisoformat("2026-01-15").timestamp()
        self.assertEqual(result, expected)

    def test_single_hour(self):
        """1h should return a timestamp ~1 hour ago."""
        import time
        now = time.time()
        result = self._parse_since("1h")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, now - 3600, delta=2)

    def test_decimal_hour(self):
        """0.5h should return a timestamp ~30 minutes ago."""
        import time
        now = time.time()
        result = self._parse_since("0.5h")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, now - 1800, delta=2)


class TestGetReportWithSince(unittest.TestCase):
    """Test get_report() with the since parameter."""

    def test_get_report_no_since(self):
        """get_report() without since should not have 'since' in report."""
        from CORE.dashboard import get_report
        report = get_report()
        self.assertNotIn("since", report)

    def test_get_report_with_since(self):
        """get_report() with since should include 'since' field."""
        from CORE.dashboard import get_report
        report = get_report(since="24h")
        self.assertIn("since", report)
        self.assertEqual(report["since"], "24h")

    def test_get_report_with_since_has_timestamp(self):
        """Report with since should still have timestamp."""
        from CORE.dashboard import get_report
        report = get_report(since="24h")
        self.assertIn("timestamp", report)

    def test_get_report_with_since_has_metrics(self):
        """Report with since should have metrics section."""
        from CORE.dashboard import get_report
        report = get_report(since="24h")
        self.assertIn("metrics", report)

    def test_get_report_with_since_has_health(self):
        """Report with since should have health section."""
        from CORE.dashboard import get_report
        report = get_report(since="24h")
        self.assertIn("health", report)

    def test_get_report_multiple_calls(self):
        """Multiple calls with different since values should work."""
        from CORE.dashboard import get_report
        r1 = get_report(since="1h")
        r2 = get_report(since="24h")
        self.assertEqual(r1["since"], "1h")
        self.assertEqual(r2["since"], "24h")

    def test_get_report_returns_all_sections(self):
        """Report should have all expected sections."""
        from CORE.dashboard import get_report
        report = get_report(since="24h")
        expected = ["timestamp", "since", "health", "metrics", "drift",
                    "dead_letters", "config", "session", "metacluster",
                    "workflows", "scheduling"]
        for section in expected:
            with self.subTest(section=section):
                self.assertIn(section, report, f"Missing section: {section}")


if __name__ == "__main__":
    unittest.main()
