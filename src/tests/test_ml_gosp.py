# coding: utf-8
"""
Unit tests for ML.gosp (online session-outcome learner) and
ML.torch_bridge (lazy optional torch).
"""

import sys
import unittest
from pathlib import Path

# Ensure azazael/src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ML.gosp import LABEL_KEY, SessionOutcomeLearner
from ML.torch_bridge import to_numpy, to_torch, torch_available
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _rows(n, offset=0):
    # Linearly separable: label follows events >= 5.
    return [
        {"events": i + offset, "errors": (i + offset) % 3, LABEL_KEY: int(i + offset >= 5)}
        for i in range(n)
    ]


class TestGosp(unittest.TestCase):
    def test_learns_and_predicts(self):
        learner = SessionOutcomeLearner()
        self.assertFalse(learner.fitted)
        learner.update(_rows(12))
        self.assertTrue(learner.fitted)
        preds = learner.predict([{"events": 2, "errors": 0}, {"events": 9, "errors": 1}])
        self.assertEqual(list(preds), [0, 1])

    def test_online_update_keeps_working(self):
        learner = SessionOutcomeLearner().update(_rows(12))
        learner.update(_rows(6, offset=12))
        preds = learner.predict([{"events": 1, "errors": 0}, {"events": 15, "errors": 2}])
        self.assertEqual(list(preds), [0, 1])

    def test_missing_label_raises(self):
        with self.assertRaises(KeyError):
            SessionOutcomeLearner().update([{"events": 3}])

    def test_unfitted_predict_raises(self):
        with self.assertRaises(RuntimeError):
            SessionOutcomeLearner().predict([{"events": 3}])

    def test_proba_shape(self):
        learner = SessionOutcomeLearner().update(_rows(12))
        proba = learner.predict_proba([{"events": 9, "errors": 0}])
        self.assertEqual(len(proba), 1)
        self.assertEqual(len(proba[0]), 2)
        self.assertAlmostEqual(sum(proba[0]), 1.0)


class TestTorchBridge(unittest.TestCase):
    def test_unavailable_is_informative(self):
        if torch_available():
            self.skipTest("torch present on this box")
        with self.assertRaises(ImportError) as ctx:
            to_torch([1.0])
        self.assertIn("torch is unavailable", str(ctx.exception))
        with self.assertRaises(ImportError):
            to_numpy(object())


if __name__ == "__main__":
    unittest.main(verbosity=2)
