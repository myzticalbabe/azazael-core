# coding: utf-8
"""Tests for F-P1-1 last-mile pieces: replay_failed contract + promotion event.

  1. Empty queue -> {"replayed": 0} without needing an executor.
  2. Pending letters + no executor -> RuntimeError (actionable), never TypeError.
  3. Pending letters + executor -> delegates with keywords, counts results.
"""
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.NeuralPipeline import NeuralPipeline  # noqa: E402


class FakeDLQ:
    def __init__(self, pending):
        self._pending = list(pending)
        self.calls = []

    def get_pending(self):
        return list(self._pending)

    def replay_all(self, execute_fn, max_concurrent=4):
        out = []
        for letter in self._pending:
            out.append({"worm": letter, "success": bool(execute_fn(letter, {}))})
        self.calls.append(max_concurrent)
        return out


def _pipe(pending):
    pipe = object.__new__(NeuralPipeline)
    pipe.dlq = FakeDLQ(pending)
    return pipe


class TestReplayFailed(unittest.TestCase):
    def test_empty_queue_needs_nothing(self):
        out = _pipe([]).replay_failed()
        self.assertEqual(out, {"replayed": 0, "results": []})

    def test_pending_without_executor_raises_actionable(self):
        with self.assertRaises(RuntimeError) as ctx:
            _pipe(["w1", "w2"]).replay_failed()
        self.assertIn("execute_fn", str(ctx.exception))
        self.assertIn("2 dead letter", str(ctx.exception))

    def test_pending_with_executor_delegates(self):
        pipe = _pipe(["w1"])
        out = pipe.replay_failed(execute_fn=lambda w, c: {"ok": True},
                                 max_concurrent=1)
        self.assertEqual(out["replayed"], 1)
        self.assertTrue(out["results"][0]["success"])
        self.assertEqual(pipe.dlq.calls, [1])


if __name__ == "__main__":
    unittest.main()
