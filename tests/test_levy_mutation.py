# coding: utf-8
"""test_levy_mutation.py — heavy-tailed mutation operator + adaptive fallback.

Falsifiable claims (F-P0-1):
  1. levy steps are heavy-tailed: over N draws the max |step| exceeds the
     Gaussian max with overwhelming probability (seeded).
  2. levy respects bounds (clamped) and resamples discrete genes.
  3. adaptive_levy engages only after patience//2 stagnant gens, is counted
     in evolve() stats, and never fires when improving.
"""
import random
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.evolution import EvolutionEngine

BOUNDS = {"x": (0.0, 1.0), "y": (-5.0, 5.0)}


def _max_step(engine, operator, draws=400, seed=7):
    rng_state = random.getstate()
    random.seed(seed)
    try:
        biggest = 0.0
        for _ in range(draws):
            g = {"x": 0.5, "y": 0.0}
            out = dict(g)
            if operator == "levy":
                out = engine._levy_mutate(dict(g), BOUNDS)
            else:
                out = engine._gaussian_mutate(dict(g), BOUNDS)
            biggest = max(biggest, abs(out["x"] - 0.5), abs(out["y"] - 0.0))
        return biggest
    finally:
        random.setstate(rng_state)


class TestLevyMutation(unittest.TestCase):
    def test_heavy_tail_exceeds_gaussian(self):
        evo = EvolutionEngine(mutation_rate=1.0, levy_alpha=1.5)
        self.assertGreater(_max_step(evo, "levy"), _max_step(evo, "gaussian"))

    def test_bounds_clamped(self):
        evo = EvolutionEngine(mutation_rate=1.0, levy_alpha=0.5)
        random.seed(1)
        for _ in range(200):
            out = evo._levy_mutate({"x": 0.5, "y": 0.0}, BOUNDS)
            self.assertGreaterEqual(out["x"], 0.0)
            self.assertLessEqual(out["x"], 1.0)
            self.assertGreaterEqual(out["y"], -5.0)
            self.assertLessEqual(out["y"], 5.0)

    def test_dispatch_by_operator_name(self):
        evo = EvolutionEngine(mutation_rate=1.0, mutation_operator="levy")
        random.seed(3)
        out = evo._mutate({"x": 0.5}, {"x": (0.0, 1.0)})
        self.assertIn("x", out)

    def test_stagnant_gens_counts(self):
        evo = EvolutionEngine()
        evo._generation_history = [
            {"best_fitness": 1.0}, {"best_fitness": 1.0},
            {"best_fitness": 1.2}, {"best_fitness": 1.2},
            {"best_fitness": 1.2},
        ]
        self.assertEqual(evo._stagnant_gens(), 2)
        evo._generation_history.append({"best_fitness": 1.5})
        self.assertEqual(evo._stagnant_gens(), 0)

    def test_adaptive_engages_only_when_stagnant(self):
        bounds = {"x": (0.0, 1.0)}
        evo = EvolutionEngine(population_size=6, elitism_count=1,
                              mutation_rate=1.0, early_stop_patience=6,
                              adaptive_levy=True, memory_store="none")
        evo.initialize_population(bounds)
        evo.evaluate_population(lambda g: g.genes["x"])
        # Improving history -> polynomial, no levy counted.
        evo._generation_history = [{"best_fitness": float(i)} for i in range(6)]
        random.seed(11)
        stats = evo.evolve()
        self.assertEqual(stats["mutation_operator"], "polynomial")
        self.assertEqual(stats["levy_children"], 0)
        # Flat history -> levy engages and counts children.
        evo._generation_history = [{"best_fitness": 1.0}] * 8
        random.seed(11)
        stats = evo.evolve()
        self.assertEqual(stats["mutation_operator"], "levy")
        self.assertGreater(stats["levy_children"], 0)
        self.assertGreaterEqual(evo._levy_gens, 1)

    def test_adaptive_off_never_engages(self):
        bounds = {"x": (0.0, 1.0)}
        evo = EvolutionEngine(population_size=6, elitism_count=1,
                              mutation_rate=1.0, early_stop_patience=6,
                              adaptive_levy=False, memory_store="none")
        evo.initialize_population(bounds)
        evo.evaluate_population(lambda g: g.genes["x"])
        evo._generation_history = [{"best_fitness": 1.0}] * 8
        random.seed(11)
        stats = evo.evolve()
        self.assertEqual(stats["mutation_operator"], "polynomial")


if __name__ == "__main__":
    unittest.main()
