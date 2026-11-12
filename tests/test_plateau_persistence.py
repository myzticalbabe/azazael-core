# coding: utf-8
"""Tests for plateau-skip persistence (F-P0-3).

Falsifiable claims:
  1. Identical record counts + reward within 2% (same episodes) -> skipped,
     and a `training_plateau` row with exploration_trigger=True is recorded
     to the same store the signal was read from.
  2. Different record counts -> no skip, no plateau row.
  3. record_to_memory=False -> no skip (no history to compare) and no writes.
"""
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.scheduling import ScheduleConfig, ScheduledTrainingWorm


class FakeStore:
    def __init__(self, rows):
        self._rows = list(rows)
        self.recorded = []

    def query_memories(self, event_type=None, limit=2):
        return [r for r in self._rows
                if r.get("event_type", "training_run_complete") == (event_type or "training_run_complete")][:limit]

    def record_event(self, source=None, event_type=None, data=None,
                     severity=None, tags=None):
        self.recorded.append({"source": source, "event_type": event_type,
                              "data": data or {}, "severity": severity,
                              "tags": tags or []})


def _row(episodes, records, reward):
    return {"event_type": "training_run_complete",
            "data": {"episodes": episodes, "records": records,
                     "avg_reward": reward}}


class TestPlateauPersistence(unittest.TestCase):
    def _worm(self, store):
        worm = ScheduledTrainingWorm(
            config=ScheduleConfig(n_episodes=5, record_to_memory=True))
        worm._get_store = lambda: store
        return worm

    def test_plateau_skips_and_records(self):
        store = FakeStore([_row(5, 200, 1.00), _row(5, 200, 1.01)])
        worm = self._worm(store)
        run = worm.execute()
        self.assertTrue(run.skipped)
        self.assertFalse(run.success)
        plateau = [r for r in store.recorded
                   if r["event_type"] == "training_plateau"]
        self.assertEqual(len(plateau), 1)
        self.assertTrue(plateau[0]["data"]["exploration_trigger"])
        self.assertEqual(plateau[0]["data"]["episodes"], 5)

    def test_changed_records_no_skip_no_row(self):
        store = FakeStore([_row(5, 200, 1.00), _row(5, 150, 1.01)])
        worm = self._worm(store)
        self.assertIsNone(worm._plateau_skip_reason())
        self.assertEqual(
            [r for r in store.recorded if r["event_type"] == "training_plateau"], [])

    def test_recording_off_no_skip_no_writes(self):
        store = FakeStore([_row(5, 200, 1.00), _row(5, 200, 1.01)])
        worm = ScheduledTrainingWorm(
            config=ScheduleConfig(n_episodes=5, record_to_memory=False))
        worm._get_store = lambda: store
        self.assertIsNone(worm._plateau_skip_reason())
        run = worm.execute()
        self.assertFalse(run.skipped)
        self.assertEqual(store.recorded, [])


if __name__ == "__main__":
    unittest.main()
