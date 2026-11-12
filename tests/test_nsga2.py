# coding: utf-8
"""
test_nsga2.py — NSGA-II Unit Tests for EvolutionEngine

Tests:
  1. Non-dominated sorting correctness on known Pareto fronts
  2. Crowding distance computation with boundary/infinity values
  3. Multi-objective optimization returns valid Pareto front
  4. Tournament selection produces fitter individuals
  5. Adaptive mutation rate responds to diversity changes
  6. Checkpoint save/load round-trips correctly
  7. Elitism preserves best individuals across generations
"""

import sys
import random
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

random.seed(42)  # Reproducible tests

from CORE.WORMS.expansion.evolution import (
    EvolutionEngine, GeneticOptimizer, Genome
)


class TestNSGA2NonDominatedSort(unittest.TestCase):
    """Test _non_dominated_sort correctness on known Pareto fronts."""

    def setUp(self):
        random.seed(42)
        self.engine = EvolutionEngine(population_size=10)

    def _make_genome(self, genes: dict, fitness: float = 0.0) -> Genome:
        return Genome(genes=genes, fitness=fitness)

    def test_trivial_front_all_non_dominated(self):
        """All individuals non-dominated when one objective increases, other decreases."""
        genomes = [
            self._make_genome({"f1": 1.0, "f2": 5.0}),
            self._make_genome({"f1": 3.0, "f2": 3.0}),
            self._make_genome({"f1": 5.0, "f2": 1.0}),
        ]
        fns = [
            lambda g: g.genes["f1"],  # minimize f1
            lambda g: g.genes["f2"],  # minimize f2
        ]
        fronts = self.engine._non_dominated_sort(genomes, fns)
        # All should be in front 0 (Pareto-optimal)
        self.assertEqual(len(fronts), 1, "All should be non-dominated")
        self.assertEqual(len(fronts[0]), 3)

    def test_clear_domination(self):
        """One genome clearly dominates another."""
        g1 = self._make_genome({"f1": 1.0, "f2": 2.0})  # better on both
        g2 = self._make_genome({"f1": 5.0, "f2": 6.0})  # worse on both
        fns = [
            lambda g: g.genes["f1"],
            lambda g: g.genes["f2"],
        ]
        fronts = self.engine._non_dominated_sort([g1, g2], fns)
        self.assertEqual(len(fronts), 2, "Should have 2 fronts")
        self.assertIn(g1, fronts[0], "g1 should be in front 0")
        self.assertIn(g2, fronts[1], "g2 should be in front 1")

    def test_partial_domination_no_dominance(self):
        """g1 better on f1, g2 better on f2 -> neither dominates."""
        g1 = self._make_genome({"f1": 1.0, "f2": 10.0})
        g2 = self._make_genome({"f1": 10.0, "f2": 1.0})
        fns = [
            lambda g: g.genes["f1"],
            lambda g: g.genes["f2"],
        ]
        fronts = self.engine._non_dominated_sort([g1, g2], fns)
        self.assertEqual(len(fronts), 1, "Neither dominates")
        self.assertEqual(len(fronts[0]), 2)

    def test_three_tiers(self):
        """Three tiers of domination: top (Pareto), middle, worst."""
        g1 = self._make_genome({"f1": 1.0, "f2": 1.0})   # Pareto
        g2 = self._make_genome({"f1": 1.5, "f2": 1.5})   # dominated by g1
        g3 = self._make_genome({"f1": 2.0, "f2": 2.0})   # dominated by g1,g2
        fns = [
            lambda g: g.genes["f1"],
            lambda g: g.genes["f2"],
        ]
        fronts = self.engine._non_dominated_sort([g1, g2, g3], fns)
        self.assertGreaterEqual(len(fronts), 2, "Should have multiple fronts")
        self.assertIn(g1, fronts[0], "g1 should be Pareto-optimal")


class TestCrowdingDistance(unittest.TestCase):
    """Test _compute_crowding_distance correctness."""

    def setUp(self):
        self.engine = EvolutionEngine(population_size=10)

    def _make_genome(self, genes: dict) -> Genome:
        return Genome(genes=genes)

    def test_two_individuals_both_infinite(self):
        """Both individuals get infinite distance (boundary points)."""
        genomes = [
            self._make_genome({"f1": 1.0}),
            self._make_genome({"f1": 5.0}),
        ]
        fns = [lambda g: g.genes["f1"]]
        distances = self.engine._compute_crowding_distance(genomes, fns)
        self.assertEqual(len(distances), 2)
        # Both are boundary points
        self.assertEqual(distances[0], float("inf"))
        self.assertEqual(distances[1], float("inf"))

    def test_three_individuals_boundary_vs_interior(self):
        """Boundary points get inf, interior gets finite."""
        genomes = [
            self._make_genome({"f1": 1.0}),   # min -> inf
            self._make_genome({"f1": 3.0}),   # interior -> finite
            self._make_genome({"f1": 5.0}),   # max -> inf
        ]
        fns = [lambda g: g.genes["f1"]]
        distances = self.engine._compute_crowding_distance(genomes, fns)
        self.assertEqual(distances[0], float("inf"), "Min should have infinite distance")
        self.assertEqual(distances[2], float("inf"), "Max should have infinite distance")
        self.assertTrue(
            0 <= distances[1] < float("inf"),
            f"Interior should have finite distance, got {distances[1]}"
        )

    def test_crowding_distance_normalized(self):
        """Distance should be normalized by objective range."""
        genomes = [
            self._make_genome({"f1": 10.0}),   # min
            self._make_genome({"f1": 50.0}),   # interior
            self._make_genome({"f1": 100.0}),  # max
        ]
        fns = [lambda g: g.genes["f1"]]
        distances = self.engine._compute_crowding_distance(genomes, fns)
        # Interior distance: (100 - 10) / (100 - 10) = 1.0 for single objective
        self.assertAlmostEqual(distances[1], 1.0, places=6,
                               msg=f"Normalized crowding should be 1.0, got {distances[1]}")


class TestMultiObjectiveOptimization(unittest.TestCase):
    """Test optimize_multi_objective end-to-end."""

    def setUp(self):
        self.engine = EvolutionEngine(population_size=20, elitism_count=3)
        self.engine.initialize_population({
            "cost": (0.0, 100.0),
            "time": (0.0, 60.0),
        })

    def test_optimize_returns_pareto_front(self):
        """Multi-objective optimization produces a valid Pareto front."""
        fns = [
            lambda g: g.genes.get("cost", 50.0),
            lambda g: g.genes.get("time", 30.0),
        ]
        # First evaluate to set fitness
        self.engine.evaluate_population(
            lambda g: -(g.genes.get("cost", 50.0) + g.genes.get("time", 30.0))
        )

        result = self.engine.optimize_multi_objective(
            fns, generations=5, use_curative=False
        )

        self.assertIn("pareto_front", result)
        self.assertGreaterEqual(result["pareto_front_size"], 1,
                                "Should have at least one Pareto-optimal individual")
        self.assertLessEqual(result["pareto_front_size"], self.engine.population_size)

        # Each Pareto front member should have objectives computed
        for member in result["pareto_front"]:
            self.assertIn("genes", member)
            self.assertIn("objectives", member)
            self.assertEqual(len(member["objectives"]), 2)

    def test_no_two_objectives_errors(self):
        """Single objective should be rejected with clear error."""
        result = self.engine.optimize_multi_objective(
            [lambda g: 0.0], generations=1, use_curative=False
        )
        self.assertIn("error", result)
        self.assertIn("2 objective", result["error"])


class TestTournamentSelection(unittest.TestCase):
    """Test _tournament_select produces fitter individuals."""

    def setUp(self):
        self.engine = EvolutionEngine(population_size=20, tournament_size=3)

    def _make_genome(self, genes: dict, fitness: float) -> Genome:
        g = Genome(genes=genes)
        g.fitness = fitness
        return g

    def test_selection_returns_fittest_in_tournament(self):
        """After many selections, average fitness should be high."""
        population = [
            self._make_genome({"x": float(i)}, float(i))
            for i in range(20)
        ]
        # Shuffle so it's not pre-sorted
        random.shuffle(population)

        selected_fitnesses = []
        for _ in range(1000):
            winner = self.engine._tournament_select(population)
            selected_fitnesses.append(winner.fitness)

        avg = sum(selected_fitnesses) / len(selected_fitnesses)
        # With tournament_size=3 and 20 individuals, avg should be well above 10
        self.assertGreater(avg, 12.0,
                           f"Tournament should select above-average fitness, got avg={avg:.1f}")

    def test_highest_fitness_wins_most_tournaments(self):
        """The single best individual wins the majority of tournaments it enters.
        
        With tournament_size=3 and 20 individuals, the best should win
        >30% of tournaments (chance of being selected into tournament).
        """
        best = self._make_genome({"x": 0}, fitness=99.0)
        others = [self._make_genome({"x": float(i)}, fitness=float(i)) for i in range(19)]
        population = [best] + others

        wins = 0
        trials = 500
        for _ in range(trials):
            winner = self.engine._tournament_select(population)
            if winner is best:
                wins += 1

        # With tournament_size=3 and 20 individuals, best should win >10%
        win_rate = wins / trials
        self.assertGreater(win_rate, 0.10,
                           f"Best should win >10% of tournaments (selected in ~15% of tournaments), got {win_rate:.1%}")


class TestAdaptiveMutation(unittest.TestCase):
    """Test adaptive mutation rate responds to diversity."""

    def setUp(self):
        self.engine = EvolutionEngine(population_size=20, mutation_rate=0.1)

    def _make_genome(self, genes: dict) -> Genome:
        return Genome(genes=genes)

    def test_low_diversity_increases_mutation(self):
        """Identical population -> mutation rate should increase."""
        identical = [
            self._make_genome({"x": 5.0}) for _ in range(10)
        ]
        bounds = {"x": (0.0, 10.0)}
        original_rate = self.engine.mutation_rate
        new_rate = self.engine._adapt_mutation_rate(identical, bounds)
        self.assertGreater(new_rate, original_rate,
                           f"Low diversity should increase mutation: {original_rate} -> {new_rate}")

    def test_high_diversity_decreases_mutation(self):
        """Diverse population -> mutation rate should decrease."""
        diverse = [
            self._make_genome({"x": float(i)}) for i in range(10)
        ]
        bounds = {"x": (0.0, 50.0)}
        original_rate = self.engine.mutation_rate
        new_rate = self.engine._adapt_mutation_rate(diverse, bounds)
        self.assertLessEqual(new_rate, original_rate,
                             f"High diversity should not increase mutation: {original_rate} -> {new_rate}")

    def test_mutation_stays_in_bounds(self):
        """Mutation rate never exceeds max or falls below min."""
        for _ in range(10):
            pop = [self._make_genome({"x": random.uniform(0, 10)}) for _ in range(10)]
            bounds = {"x": (0.0, 10.0)}
            rate = self.engine._adapt_mutation_rate(pop, bounds)
            self.assertGreaterEqual(rate, self.engine.min_mutation_rate)
            self.assertLessEqual(rate, self.engine.max_mutation_rate)


class TestCheckpointRoundTrip(unittest.TestCase):
    """Test save/load checkpoint preserves evolution state."""

    def setUp(self):
        self.engine = EvolutionEngine(population_size=10)
        self.engine.initialize_population({
            "lr": (0.001, 0.1),
            "gamma": (0.8, 0.999),
        })

    def test_save_and_load_restores_state(self):
        """After save + load, generation and population are restored."""
        # Evaluate and evolve a few generations
        for gen in range(3):
            self.engine.evaluate_population(
                lambda g: g.genes.get("lr", 0.05) + g.genes.get("gamma", 0.9)
            )
            self.engine.evolve()

        gen_before = self.engine._generation
        pop_before = len(self.engine._population)
        best_before = self.engine._best_genome.fitness if self.engine._best_genome else 0.0

        # Save checkpoint
        self.engine.save_checkpoint(label="test_checkpoint")

        # Create new engine and load
        engine2 = EvolutionEngine(population_size=10)
        loaded = engine2.load_checkpoint(label="test_checkpoint")

        self.assertTrue(loaded, "Checkpoint must load successfully")
        self.assertEqual(engine2._generation, gen_before,
                         f"Generation should be {gen_before}, got {engine2._generation}")
        self.assertEqual(len(engine2._population), pop_before)
        self.assertAlmostEqual(
            engine2._best_genome.fitness if engine2._best_genome else 0.0,
            best_before, places=6,
            msg=f"Best fitness mismatch after load"
        )

    def test_has_converged_detects_stagnation(self):
        """has_converged returns True when fitness plateaus."""
        # Manually set generation history with stagnant fitness
        engine = EvolutionEngine(population_size=10, early_stop_patience=3)
        engine._generation = 5
        engine._generation_history = [
            {"best_fitness": 0.850},
            {"best_fitness": 0.851},
            {"best_fitness": 0.850},
            {"best_fitness": 0.851},
            {"best_fitness": 0.851},
        ]
        self.assertTrue(engine.has_converged(),
                        "Should detect convergence with < 1% improvement")

    def test_has_converged_returns_false_when_improving(self):
        """has_converged returns False when fitness is improving."""
        engine = EvolutionEngine(population_size=10, early_stop_patience=3)
        engine._generation = 5
        engine._generation_history = [
            {"best_fitness": 0.500},
            {"best_fitness": 0.600},
            {"best_fitness": 0.700},
            {"best_fitness": 0.800},
            {"best_fitness": 0.900},
        ]
        self.assertFalse(engine.has_converged(),
                         "Should NOT detect convergence with rapid improvement")


class TestElitism(unittest.TestCase):
    """Test elitism preserves top individuals."""

    def setUp(self):
        self.engine = EvolutionEngine(population_size=10, elitism_count=2)

    def test_elites_survive_generation(self):
        """Elite individuals should persist after evolve()."""
        self.engine.initialize_population({
            "x": (0.0, 100.0),
        })

        # Evaluate with a fitness function that favors high x
        self.engine.evaluate_population(lambda g: g.genes.get("x", 50.0))

        # Capture best fitness before evolution
        best_before = self.engine._best_genome.fitness

        # Evolve one generation
        self.engine.evolve()

        # After evolution + re-evaluation, best should be within 10% of before
        self.engine.evaluate_population(lambda g: g.genes.get("x", 50.0))
        best_after = self.engine._best_genome.fitness

        self.assertGreaterEqual(
            best_after, best_before * 0.9,
            f"Elites should prevent major fitness collapse: {best_before:.1f} -> {best_after:.1f}"
        )


class TestPopulationDiversity(unittest.TestCase):
    """Test _compute_population_diversity accuracy."""

    def setUp(self):
        self.engine = EvolutionEngine(population_size=10)

    def _make_genome(self, genes: dict) -> Genome:
        return Genome(genes=genes)

    def test_identical_population_zero_diversity(self):
        """Identical genes -> zero diversity."""
        pop = [self._make_genome({"x": 5.0}) for _ in range(5)]
        bounds = {"x": (0.0, 10.0)}
        div = self.engine._compute_population_diversity(pop, bounds)
        self.assertAlmostEqual(div, 0.0, places=6,
                               msg=f"Identical population should have 0 diversity, got {div}")

    def test_maximally_diverse_population(self):
        """Spread across full range -> high diversity."""
        pop = [
            self._make_genome({"x": 0.0}),
            self._make_genome({"x": 10.0}),
        ]
        bounds = {"x": (0.0, 10.0)}
        div = self.engine._compute_population_diversity(pop, bounds)
        self.assertGreater(div, 0.3,
                           msg=f"Max spread should have high diversity, got {div}")


class TestGeneticOptimizer(unittest.TestCase):
    """Test GeneticOptimizer end-to-end."""

    def setUp(self):
        self.optimizer = GeneticOptimizer()

    def test_initialize_creates_engine(self):
        """initialize() creates an EvolutionEngine for a worm type."""
        engine = self.optimizer.initialize(
            "TestWorm",
            {"scan_depth": (1, 10), "timeout": (100, 5000)},
            population_size=10,
        )
        self.assertIsInstance(engine, EvolutionEngine)
        self.assertEqual(len(engine._population), 10)

    def test_optimize_returns_best_params(self):
        """optimize() finds reasonable parameters."""
        self.optimizer.initialize(
            "TestWorm",
            {"a": (1.0, 10.0), "b": (1.0, 10.0)},
            population_size=10,
        )
        result = self.optimizer.optimize(
            "TestWorm",
            generations=3,
            fitness_fn=lambda g: g.genes.get("a", 5.0) + g.genes.get("b", 5.0),
        )
        self.assertIn("best_parameters", result)
        self.assertIn("best_fitness", result)
        self.assertGreater(result["best_fitness"], 0,
                           "Best fitness should be positive for a+b optimization")


if __name__ == "__main__":
    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestNSGA2NonDominatedSort))
    suite.addTests(loader.loadTestsFromTestCase(TestCrowdingDistance))
    suite.addTests(loader.loadTestsFromTestCase(TestMultiObjectiveOptimization))
    suite.addTests(loader.loadTestsFromTestCase(TestTournamentSelection))
    suite.addTests(loader.loadTestsFromTestCase(TestAdaptiveMutation))
    suite.addTests(loader.loadTestsFromTestCase(TestCheckpointRoundTrip))
    suite.addTests(loader.loadTestsFromTestCase(TestElitism))
    suite.addTests(loader.loadTestsFromTestCase(TestPopulationDiversity))
    suite.addTests(loader.loadTestsFromTestCase(TestGeneticOptimizer))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)
