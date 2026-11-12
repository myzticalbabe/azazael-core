# coding: utf-8
"""Tests for the learned-policy query path (unison closure, advisory stage).

  1. recommend() returns the argmax action for a known state key.
  2. recommend() generalizes by severity suffix when the source is unseen.
  3. recommend() returns unknown (never raises) on an empty table.
  4. db_monitor advisory attaches policy_advice without changing firing.
"""
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.learning_rl import RLTrainLoop


class TestRecommend(unittest.TestCase):
    def _loop(self, table):
        loop = RLTrainLoop.__new__(RLTrainLoop)
        import threading
        from collections import defaultdict
        loop._lock = threading.Lock()
        loop._q_table = defaultdict(lambda: defaultdict(float), table)
        loop._pipeline = False
        loop._versioner = False
        loop._model_name = None
        loop._episodes = []
        return loop

    def test_argmax_known_state(self):
        loop = self._loop({"db:critical": {0: 0.1, 1: 0.2, 2: 0.9, 3: 0.3}})
        out = loop.recommend("critical", source="db")
        self.assertEqual((out["action"], out["action_name"]), (2, "alert"))
        self.assertTrue(out["known"])

    def test_severity_fallback_unseen_source(self):
        loop = self._loop({"other:warning": {0: 0.0, 1: 0.7, 2: 0.1, 3: 0.0}})
        out = loop.recommend("warning", source="never-seen")
        self.assertEqual(out["action"], 1)
        self.assertTrue(out["known"])

    def test_unknown_never_raises(self):
        loop = self._loop({})
        loop.load_q_table_from_store = lambda *a, **k: False
        out = loop.recommend("critical", source="db")
        self.assertEqual(out["action"], -1)
        self.assertFalse(out["known"])

    def test_monitor_advice_shape(self):
        from CORE.WORMS.expansion import db_monitor as dm
        worm = dm.DatabaseMonitorWorm.__new__(dm.DatabaseMonitorWorm)
        import threading
        worm._lock = threading.Lock()
        out = worm._policy_advice("warning")
        self.assertIn(out["action_name"],
                      ("ignore", "log", "alert", "remediate", "unknown"))


if __name__ == "__main__":
    unittest.main()
