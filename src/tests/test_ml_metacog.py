# coding: utf-8
"""
Unit tests for ML.step_monitor - online stall/loop monitor.
"""

import sys
import unittest
from pathlib import Path

# Ensure azazael/src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ML.step_monitor import StepEvent, StepMonitor
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


class TestSpinCapture(unittest.TestCase):
    def test_226_empty_spin_fires_at_five(self):
        """Replay of the 2026-09-20 failure: 205 empty steps must trip by 5."""
        mon = StepMonitor()
        verdict = None
        for _ in range(226):
            verdict = mon.check(StepEvent(kind="empty"))
            if verdict.status == "stall":
                break
        assert verdict is not None
        self.assertEqual(verdict.status, "stall")
        self.assertLessEqual(verdict.step, 5)
        self.assertIn("empty", verdict.reason)

    def test_normal_stream_stays_ok(self):
        mon = StepMonitor()
        events = [
            StepEvent(kind="text", text="First thought"),
            StepEvent(kind="tool", tool="read"),
            StepEvent(kind="text", text="Second thought"),
            StepEvent(kind="tool", tool="bash"),
            StepEvent(kind="text", text="Third thought"),
        ]
        for event in events:
            verdict = mon.check(event)
        self.assertEqual(verdict.status, "ok")

    def test_error_streak_stalls(self):
        mon = StepMonitor()
        verdict = None
        for _ in range(5):
            verdict = mon.check(StepEvent(kind="tool", tool="bash", is_error=True))
        assert verdict is not None
        self.assertEqual(verdict.status, "stall")

    def test_identical_output_loops(self):
        mon = StepMonitor()
        verdict = None
        for _ in range(5):
            verdict = mon.check(StepEvent(kind="text", text="same again"))
        assert verdict is not None
        self.assertEqual(verdict.status, "stall")

    def test_warn_before_stall(self):
        mon = StepMonitor()
        mon.check(StepEvent(kind="empty"))
        mon.check(StepEvent(kind="empty"))
        verdict = mon.check(StepEvent(kind="empty"))
        self.assertEqual(verdict.status, "warn")

    def test_reset(self):
        mon = StepMonitor()
        for _ in range(5):
            mon.check(StepEvent(kind="empty"))
        mon.reset()
        self.assertEqual(mon.check(StepEvent(kind="text", text="fresh")).status, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
