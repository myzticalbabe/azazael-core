# coding: utf-8
"""Presence is not freshness: the threat tier must age its own inputs.

`ThreatIntelMonitorWorm.check_health()` computed `healthy = feed_rows > 0`.
That is a *presence* check, so a feed job that stopped left the row count at
its last value and the tier reported healthy indefinitely on frozen data.

Measured 2026-09-14: the six feed files under
`G:/source-of-truth/azazael_models` were **13.6-14.6 h old** against an hourly
pull schedule, `feed_rows` was 19074, and `healthy` was `True`.

The snapshot also stamped `timestamp` with the *read* time, so a picture built
from 14-hour-old files was dated "now". It now carries `feed_at` / `feed_age_s`
(the data's own age) alongside it, and the verdict requires both data present
and data current.
"""
import json
import os
import sys
import tempfile
import time
import unittest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_project_root, "src"))

from CORE.WORMS.expansion import threatmon as tm  # noqa: E402


def _write(models_dir, name, payload):
    with open(os.path.join(models_dir, name), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


class _FeedFixture(unittest.TestCase):
    """Point the worm's MODELS dir at a temp tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = tm.MODELS
        tm.MODELS = self.tmp.name

    def tearDown(self):
        tm.MODELS = self._saved
        self.tmp.cleanup()

    def _age_all(self, seconds):
        when = time.time() - seconds
        for name in tm.FEED_FILES:
            p = os.path.join(self.tmp.name, name)
            if os.path.exists(p):
                os.utime(p, (when, when))

    def _seed(self, rows=100):
        _write(self.tmp.name, "threat_triage_v2.json", {"n": rows})


class TestFeedFreshnessHelper(_FeedFixture):
    def test_no_files_is_unknown_not_zero(self):
        at, age = tm.feed_freshness()
        self.assertEqual(at, "")
        self.assertEqual(age, -1.0,
                         "an unreadable timestamp must not read as fresh")

    def test_the_newest_file_defines_the_age(self):
        _write(self.tmp.name, "threat_triage_v2.json", {"n": 1})
        time.sleep(0.02)
        _write(self.tmp.name, "urlscan_corrob.json", {"hosts": {}})
        self._age_all(60)
        # both are ~60s old; the newest wins
        _, age = tm.feed_freshness()
        self.assertAlmostEqual(age, 60.0, delta=2.0)


class TestHealthyRequiresFreshness(_FeedFixture):
    def test_fresh_feed_is_healthy(self):
        self._seed()
        self._age_all(60)
        snap, _ = tm.ThreatIntelMonitorWorm().check_health()
        self.assertTrue(snap.healthy)
        self.assertAlmostEqual(snap.feed_age_s, 60.0, delta=2.0)

    def test_frozen_feed_is_not_healthy(self):
        """The regression: 14 h old, rows present, and the tier said healthy."""
        self._seed()
        self._age_all(14 * 3600)
        snap, alerts = tm.ThreatIntelMonitorWorm().check_health()
        self.assertFalse(snap.healthy,
                         "rows present is not the same as data current")
        self.assertIn("threat-stalefeed", [a.alert_id for a in alerts])

    def test_the_stale_alert_names_the_age_and_the_limit(self):
        self._seed()
        self._age_all(14 * 3600)
        _, alerts = tm.ThreatIntelMonitorWorm().check_health()
        stale = [a for a in alerts if a.alert_id == "threat-stalefeed"][0]
        self.assertIn("14.0h", stale.message)
        self.assertIn("stalled", stale.message)

    def test_just_inside_the_window_is_still_healthy(self):
        self._seed()
        self._age_all(tm.FEED_STALE_AFTER_S - 600)
        snap, _ = tm.ThreatIntelMonitorWorm().check_health()
        self.assertTrue(snap.healthy)

    def test_no_feed_at_all_is_unhealthy(self):
        snap, alerts = tm.ThreatIntelMonitorWorm().check_health()
        self.assertFalse(snap.healthy)
        self.assertEqual(snap.feed_age_s, -1.0)
        self.assertIn("threat-nofeed", [a.alert_id for a in alerts])


class TestTheSnapshotSeparatesReadTimeFromDataTime(_FeedFixture):
    def test_feed_at_is_the_data_time_not_the_read_time(self):
        import datetime
        self._seed()
        self._age_all(3 * 3600)
        snap, _ = tm.ThreatIntelMonitorWorm().check_health()
        read = datetime.datetime.fromisoformat(snap.timestamp)
        data = datetime.datetime.fromisoformat(snap.feed_at)
        self.assertLess(data, read,
                        "the data predates the read; both must be recorded")
        self.assertGreater((read - data).total_seconds(), 3 * 3600 - 60)

    def test_the_snapshot_serialises_the_new_fields(self):
        self._seed()
        self._age_all(60)
        snap, _ = tm.ThreatIntelMonitorWorm().check_health()
        doc = json.loads(json.dumps(snap.__dict__, default=str))
        self.assertIn("feed_at", doc)
        self.assertIn("feed_age_s", doc)


if __name__ == "__main__":
    unittest.main()
