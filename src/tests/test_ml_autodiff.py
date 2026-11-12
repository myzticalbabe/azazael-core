# coding: utf-8
"""
Unit tests for ML.autodiff - first-principles automatic differentiation.
"""

import sys
import unittest
from pathlib import Path

# Ensure azazael/src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from ML.autodiff import (
    Dual,
    backprop_grad,
    complex_step_grad,
    forward_mode_grad,
    hvp_finite_difference,
    minimize,
)
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def _quad(x):
    # f = sum(x^2) + 3*x0 ; works for mygrad tensors and complex arrays.
    return (x**2).sum() + 3.0 * x.flat[0] if isinstance(x, np.ndarray) else (x**2).sum() + 3.0 * x[0]


class TestBackprop(unittest.TestCase):
    def test_matches_analytic(self):
        def fn(x):
            return (x**2).sum() + 3.0 * x[0]

        g = backprop_grad(fn, np.array([2.0, -1.0, 0.5]))
        np.testing.assert_allclose(g, [7.0, -2.0, 1.0], rtol=1e-6)


class TestForward(unittest.TestCase):
    def test_dual_arithmetic(self):
        a, b = Dual(3.0, 1.0), Dual(2.0, 0.0)
        self.assertEqual((a * b).real, 6.0)
        self.assertEqual((a * b).dual, 2.0)
        self.assertAlmostEqual((a**2).dual, 6.0)
        self.assertAlmostEqual((a / b).dual, 0.5)

    def test_dual_matches_analytic(self):
        def fn(ds):
            return ds[0] ** 2 + 3.0 * ds[0] + ds[1] * ds[0]

        g = forward_mode_grad(fn, [2.0, -1.0])
        self.assertAlmostEqual(g[0], 2 * 2.0 + 3.0 + (-1.0))
        self.assertAlmostEqual(g[1], 2.0)

    def test_dual_transcendentals(self):
        def fn(ds):
            return ds[0].exp() + ds[0].sin()

        g = forward_mode_grad(fn, [0.0])
        self.assertAlmostEqual(g[0], 1.0 + 1.0)

    def test_complex_step_matches_analytic(self):
        g = complex_step_grad(_quad, np.array([2.0, -1.0]))
        np.testing.assert_allclose(g, [7.0, -2.0], rtol=1e-9)


class TestOptimizers(unittest.TestCase):
    def test_adam_converges_quadratic(self):
        def fn(x):
            return ((x - 3.0) ** 2).sum()

        x_star, hist = minimize(fn, np.array([0.0, -5.0]), method="adam", lr=0.1, steps=500)
        np.testing.assert_allclose(x_star, [3.0, 3.0], atol=1e-2)
        self.assertLess(hist[-1], hist[0])

    def test_sgd_and_momentum_descend(self):
        def fn(x):
            return (x**2).sum()

        for method in ("sgd", "momentum"):
            x_star, hist = minimize(fn, np.array([4.0]), method=method, lr=0.05, steps=300)
            self.assertLess(hist[-1], hist[0])
            self.assertLess(abs(x_star[0]), 1.0)

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            minimize(lambda x: x.sum(), np.array([1.0]), method="newton")


class TestHVP(unittest.TestCase):
    def test_hvp_quadratic(self):
        # f = x0^2 + 3*x0*x1 ; H = [[2,3],[3,0]] ; H @ [1,1] = [5,3]
        def fn(x):
            return x[0] ** 2 + 3.0 * x[0] * x[1]

        hvp = hvp_finite_difference(fn, np.array([1.0, 2.0]), np.array([1.0, 1.0]))
        np.testing.assert_allclose(hvp, [5.0, 3.0], rtol=1e-4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
