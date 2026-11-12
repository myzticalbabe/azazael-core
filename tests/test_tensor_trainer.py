# coding: utf-8
"""Tests for scripts/train_tensor_model.py (pure parts; torch optional)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def _mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "train_tensor_model",
        os.path.join(os.path.dirname(__file__), "..", "scripts",
                     "train_tensor_model.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestDataset(unittest.TestCase):
    def test_margin_math(self):
        m = _mod()
        rows = [
            {"data": {"best_genes": {"learning_rate": 0.3, "epsilon": 0.1,
                                     "discount": 0.9, "epsilon_decay": 0.05},
                      "best_total": 25.0, "baseline": 24.0, "pool_id": "e1"}},
            {"data": {"best_genes": {"learning_rate": 0.3, "epsilon": 0.1,
                                     "discount": 0.9, "epsilon_decay": 0.05},
                      "best_total": 20.0, "baseline": 0.0, "pool_id": "e1"}},
            {"data": {"best_genes": {"learning_rate": 0.3},  # incomplete
                      "best_total": 25.0, "baseline": 24.0, "pool_id": "e1"}},
            {"data": {"best_genes": {"learning_rate": 0.3, "epsilon": 0.1,
                                     "discount": 0.9, "epsilon_decay": 0.05},
                      "best_total": 25.0, "pool_id": "e1"}},  # no baseline
        ]
        samples = m.build_dataset(rows)
        self.assertEqual(len(samples), 2)
        self.assertAlmostEqual(samples[0][1], 1.0)
        self.assertAlmostEqual(samples[1][1], 20.0)

    def test_split_eras_accumulates(self):
        m = _mod()
        mk = lambda p, n: [([0.1 * i] * 4, float(i), p) for i in range(n)]
        samples = mk("e1", 50) + mk("e2", 6) + mk("e3", 2)
        label, train, test = m.split_eras(samples)
        # 2 + 6 < 10, so it keeps walking into e1 (main() then refuses
        # the thin train side via min_rows — the split itself is honest).
        self.assertEqual(label, "e3+e2+e1")
        self.assertEqual(len(test), 58)
        self.assertEqual(len(train), 0)

    def test_split_eras_caps_at_three(self):
        m = _mod()
        mk = lambda p, n: [([0.0] * 4, 1.0, p) for _ in range(n)]
        samples = mk("e1", 50) + mk("e2", 2) + mk("e3", 2) + mk("e4", 2)
        label, train, test = m.split_eras(samples)
        self.assertEqual(label, "e4+e3+e2")
        self.assertEqual(len(test), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
