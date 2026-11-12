# coding: utf-8
"""
Unit tests for Neural Advancement features:
- EvolutionEngine: adaptive mutation rate, convergence detection
- MetaCognitionWorm: cross-worm correlation, anomaly detection

Persistence tests (checkpoint/restore, save/load state) require
store injection and are covered by integration tests.

Run with:
    python -m pytest tests/test_neural_advancement.py -v
    python tests/test_neural_advancement.py  # Direct runner
"""

import sys
import unittest
from pathlib import Path

_src_dir = Path(__file__).resolve().parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from CORE.WORMS.expansion.evolution import EvolutionEngine, Genome
from CORE.WORMS.expansion.meta_cognition import MetaCognitionWorm, _PERSISTENCE_DISABLED


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _make_engine(pop_size=20) -> EvolutionEngine:
    """Create a minimal EvolutionEngine for testing."""
    evo = EvolutionEngine(
        population_size=pop_size, elitism_count=2,
        mutation_rate=0.1, early_stop_patience=5,
    )
    evo.initialize_population({
        "lr": (0.0001, 0.1),
        "batch_size": (8, 256),
        "gamma": (0.8, 0.999),
    })
    return evo


# ══════════════════════════════════════════════════════════════════════
# Adaptive Mutation Rate Tests
# ══════════════════════════════════════════════════════════════════════

class TestAdaptiveMutationRate(unittest.TestCase):
    """Tests for EvolutionEngine adaptive mutation rate."""

    def test_base_mutation_rate_set(self):
        """Base mutation rate should equal constructor value."""
        evo = EvolutionEngine(mutation_rate=0.15)
        self.assertEqual(evo.base_mutation_rate, 0.15)

    def test_max_mutation_rate_bounded(self):
        """Max mutation rate should be 4x base, capped at 0.5."""
        evo = EvolutionEngine(mutation_rate=0.1)
        self.assertEqual(evo.max_mutation_rate, 0.4)
        self.assertLessEqual(evo.max_mutation_rate, 0.5)

    def test_max_mutation_rate_capped(self):
        """High base rate should cap max at 0.5."""
        evo = EvolutionEngine(mutation_rate=0.3)
        self.assertEqual(evo.max_mutation_rate, 0.5)

    def test_min_mutation_rate_is_fraction(self):
        """Min mutation rate should be 20% of base."""
        evo = EvolutionEngine(mutation_rate=0.1)
        self.assertAlmostEqual(evo.min_mutation_rate, 0.02, delta=0.001)

    def test_compute_diversity_returns_float(self):
        """_compute_population_diversity should return float 0-1."""
        evo = _make_engine()
        diversity = evo._compute_population_diversity()
        self.assertIsInstance(diversity, float)
        self.assertGreaterEqual(diversity, 0.0)
        self.assertLessEqual(diversity, 1.0)

    def test_diversity_nonzero_with_random_population(self):
        """Random population should have non-zero diversity."""
        evo = _make_engine(20)
        diversity = evo._compute_population_diversity()
        self.assertGreater(diversity, 0.0)

    def test_diversity_zero_with_identical_genomes(self):
        """Identical genomes should yield near-zero diversity."""
        evo = _make_engine()
        genes = {"lr": 0.05, "batch_size": 128.0, "gamma": 0.9}
        evo._population = [Genome(genes=dict(genes)) for _ in range(10)]
        diversity = evo._compute_population_diversity()
        self.assertAlmostEqual(diversity, 0.0, delta=0.01)

    def test_adapt_mutation_rate_increases_on_low_diversity(self):
        """Mutation rate should increase when diversity is very low."""
        evo = _make_engine()
        genes = {"lr": 0.05, "batch_size": 128.0, "gamma": 0.9}
        evo._population = [
            Genome(genes={k: v + (i * 0.000001) for k, v in genes.items()})
            for i in range(10)
        ]
        original_rate = evo.mutation_rate
        new_rate = evo._adapt_mutation_rate()
        self.assertGreater(new_rate, original_rate,
                          f"Expected {new_rate} > {original_rate} on low diversity")

    def test_adapt_mutation_rate_decays_on_high_diversity(self):
        """Mutation rate should decay toward base when diversity is high."""
        evo = _make_engine()
        # Create diverse population
        evo._population = []
        for i in range(20):
            genes = {
                "lr": 0.0001 + 0.0999 * (i / 19),
                "batch_size": 8.0 + 248.0 * ((19 - i) / 19),
                "gamma": 0.8 + 0.199 * (i / 19),
            }
            evo._population.append(Genome(genes=genes))
        evo.mutation_rate = 0.3  # Start high
        new_rate = evo._adapt_mutation_rate()
        self.assertLess(new_rate, 0.3,
                       f"Expected {new_rate} < 0.3 on high diversity")

    def test_mutation_rate_stays_in_bounds(self):
        """Mutation rate should always stay within [min, max]."""
        evo = _make_engine()
        for _ in range(20):
            evo._population = [Genome(genes={
                "lr": 0.0001 + 0.0999 * (i / 19),
                "batch_size": 128.0,
                "gamma": 0.9,
            }) for i in range(10)]
            evo.mutation_rate = 0.01 + 0.4 * (_ / 20)
            new_rate = evo._adapt_mutation_rate()
            self.assertGreaterEqual(new_rate, evo.min_mutation_rate,
                                   f"Rate {new_rate} below min {evo.min_mutation_rate}")
            self.assertLessEqual(new_rate, evo.max_mutation_rate,
                                f"Rate {new_rate} above max {evo.max_mutation_rate}")

    def test_diversity_with_single_element(self):
        """Single-element population returns 0 diversity."""
        evo = _make_engine()
        evo._population = [Genome(genes={"lr": 0.05})]
        diversity = evo._compute_population_diversity()
        self.assertEqual(diversity, 0.0)

    def test_diversity_accepts_external_population(self):
        """_compute_population_diversity should accept external population."""
        evo = _make_engine(5)
        external = [Genome(genes={"lr": 0.01 + i * 0.02}) for i in range(5)]
        diversity = evo._compute_population_diversity(
            population=external,
            gene_bounds={"lr": (0.0, 0.1)},
        )
        self.assertGreater(diversity, 0.0)


# ══════════════════════════════════════════════════════════════════════
# Convergence Detection Tests
# ══════════════════════════════════════════════════════════════════════

class TestConvergenceDetection(unittest.TestCase):
    """Tests for EvolutionEngine.has_converged()."""

    def test_not_converged_low_generation(self):
        """Should not converge below patience threshold."""
        evo = _make_engine()
        self.assertFalse(evo.has_converged())

    def test_converged_with_stable_history(self):
        """Stable best fitness should trigger convergence."""
        evo = _make_engine()
        evo._generation = 6
        evo._generation_history = [
            {"best_fitness": 0.5 + i * 0.0001} for i in range(6)
        ]
        self.assertTrue(evo.has_converged())

    def test_not_converged_with_improvement(self):
        """Improving fitness should not trigger convergence."""
        evo = _make_engine()
        evo._generation = 6
        evo._generation_history = [
            {"best_fitness": 0.5 + i * 0.05} for i in range(6)
        ]
        self.assertFalse(evo.has_converged())

    def test_converged_with_zero_improvement(self):
        """Zero improvement should converge."""
        evo = _make_engine()
        evo._generation = 6
        evo._generation_history = [
            {"best_fitness": 0.8} for _ in range(6)
        ]
        self.assertTrue(evo.has_converged())


# ══════════════════════════════════════════════════════════════════════
# Cross-Worm Correlation Tests
# ══════════════════════════════════════════════════════════════════════

class TestCrossWormCorrelation(unittest.TestCase):
    """Tests for MetaCognitionWorm cross-worm correlation."""

    def setUp(self):
        self.mc = MetaCognitionWorm(memory_store=_PERSISTENCE_DISABLED)

    def test_correlation_needs_two_worm_types(self):
        """Correlation with <2 worm types returns empty."""
        self.mc.observe("SystemZ", "scan", "error",
                        error="timeout", duration_ms=1000)
        result = self.mc.compute_cross_worm_correlation()
        self.assertEqual(result["correlations"], [])
        self.assertIn("Need at least 2", result.get("message", ""))

    def test_correlation_returns_list(self):
        """Correlation results should include a correlations list."""
        self._seed_correlated_data()
        result = self.mc.compute_cross_worm_correlation()
        self.assertIsInstance(result["correlations"], list)

    def test_correlation_detects_pattern(self):
        """Should detect correlation when errors co-occur."""
        self._seed_correlated_data()
        result = self.mc.compute_cross_worm_correlation(min_correlation=0.1)
        self.assertGreater(len(result["correlations"]), 0,
                          f"Expected correlations, got {result}")

    def test_correlation_fields_present(self):
        """Each correlation entry should have expected fields."""
        self._seed_correlated_data()
        result = self.mc.compute_cross_worm_correlation(min_correlation=0.1)
        if result["correlations"]:
            corr = result["correlations"][0]
            for field in ["worm_a", "worm_b", "correlation", "direction", "strength"]:
                self.assertIn(field, corr, f"Missing field: {field}")

    def test_correlation_value_in_range(self):
        """Correlation coefficient should be between -1 and 1."""
        self._seed_correlated_data()
        result = self.mc.compute_cross_worm_correlation(min_correlation=0.0)
        for corr in result["correlations"]:
            self.assertGreaterEqual(corr["correlation"], -1.0)
            self.assertLessEqual(corr["correlation"], 1.0)

    def test_correlation_excluded_no_data(self):
        """Specified worm types without error data appear in excluded_no_data."""
        self._seed_correlated_data()
        result = self.mc.compute_cross_worm_correlation(
            worm_types=["SystemZ", "NETWORXZ", "NonExistent"],
            min_correlation=0.1,
        )
        if "excluded_no_data" in result:
            self.assertEqual(result["excluded_no_data"], ["NonExistent"])

    def test_correlation_respects_threshold(self):
        """Higher min_correlation returns fewer results."""
        self._seed_correlated_data()
        result_low = self.mc.compute_cross_worm_correlation(min_correlation=0.0)
        result_high = self.mc.compute_cross_worm_correlation(min_correlation=0.9)
        self.assertGreaterEqual(
            result_low.get("significant_pairs", 0),
            result_high.get("significant_pairs", 0),
        )

    def test_correlation_has_recommendations(self):
        """Results should include recommendations list."""
        self._seed_correlated_data()
        result = self.mc.compute_cross_worm_correlation(min_correlation=0.1)
        self.assertIsInstance(result.get("recommendations"), list)

    def _seed_correlated_data(self):
        """Seed observations where SystemZ errors correlate with NETWORXZ errors."""
        for window in range(22):
            sys_err = window % 3 == 0  # error every 3 windows
            sev_a = "error" if sys_err else "info"
            sev_b = "error" if sys_err else "info"
            self.mc.observe("SystemZ", "scan", sev_a,
                            error="timeout" if sys_err else None,
                            duration_ms=100)
            self.mc.observe("NETWORXZ", "poll", sev_b,
                            error="connection" if sys_err else None,
                            duration_ms=200)


# ══════════════════════════════════════════════════════════════════════
# Anomaly Detection Tests
# ══════════════════════════════════════════════════════════════════════

class TestAnomalyDetection(unittest.TestCase):
    """Tests for MetaCognitionWorm.detect_anomalies()."""

    def setUp(self):
        self.mc = MetaCognitionWorm(memory_store=_PERSISTENCE_DISABLED)

    def test_anomaly_insufficient_data(self):
        """Fewer than 10 observations returns insufficient data message."""
        for _ in range(5):
            self.mc.observe("SystemZ", "scan", "info", duration_ms=100)
        result = self.mc.detect_anomalies()
        self.assertEqual(result["anomalies"], [])
        self.assertIn("Insufficient", result.get("message", ""))

    def test_anomaly_normal_data_no_flags(self):
        """Stable, consistent data should produce minimal anomalies."""
        self._seed_stable_data(50)
        result = self.mc.detect_anomalies()
        self.assertLess(len(result["anomalies"]), 3,
                       f"Expected <3 anomalies from stable data, got {len(result['anomalies'])}")

    def test_anomaly_detects_error_spike(self):
        """Sudden error spike should be flagged."""
        self._seed_stable_data(30)
        for _ in range(5):
            self.mc.observe("SystemZ", "scan", "error",
                            error="timeout", duration_ms=100)
        result = self.mc.detect_anomalies()
        self.assertGreater(len(result["anomalies"]), 0,
                          "Expected anomalies from error spike")

    def test_anomaly_has_severity_label(self):
        """Each anomaly should have a valid severity label."""
        self._seed_stable_data(20)
        self.mc.observe("SystemZ", "scan", "error",
                        error="critical", duration_ms=50000)
        self.mc.observe("SystemZ", "scan", "error",
                        error="critical", duration_ms=50000)
        for _ in range(8):
            self.mc.observe("SystemZ", "scan", "info", duration_ms=100)
        result = self.mc.detect_anomalies(error_sigma=1.0)
        valid = {"medium", "high", "critical"}
        for anomaly in result["anomalies"]:
            self.assertIn(anomaly["severity"], valid)

    def test_anomaly_baseline_present(self):
        """Result should include baseline statistics."""
        self._seed_stable_data(20)
        result = self.mc.detect_anomalies()
        self.assertIn("baseline", result)
        for key in ["mean_error_rate", "error_stddev"]:
            self.assertIn(key, result["baseline"], f"Missing {key}")

    def test_anomaly_filter_by_worm_type(self):
        """Filtering by worm_type only reports that type's anomalies."""
        for _ in range(20):
            self.mc.observe("SystemZ", "scan", "info", duration_ms=100)
        for i in range(20):
            sev = "error" if i >= 15 else "info"
            self.mc.observe("NETWORXZ", "poll", sev,
                            error="err" if sev == "error" else None,
                            duration_ms=100)
        result = self.mc.detect_anomalies(worm_type="SystemZ")
        for anomaly in result["anomalies"]:
            self.assertEqual(anomaly["worm_type"], "SystemZ")

    def _seed_stable_data(self, count: int):
        """Seed consistent, low-error observations."""
        for i in range(count):
            outcome = "error" if i % 20 == 0 else "info"
            self.mc.observe("SystemZ", "scan", outcome,
                            error="occasional" if outcome == "error" else None,
                            duration_ms=100)


# ── Direct runner ───────────────────────────────────────────────────
if __name__ == "__main__":
    p = f = 0
    for name, obj in sorted(globals().items()):
        if isinstance(obj, type) and issubclass(obj, unittest.TestCase) and name.startswith("Test"):
            suite = unittest.TestLoader().loadTestsFromTestCase(obj)
            runner = unittest.TextTestRunner(verbosity=2)
            result = runner.run(suite)
            p += result.testsRun - len(result.failures) - len(result.errors)
            f += len(result.failures) + len(result.errors)
    print(f"\n  Passed: {p}, Failed: {f}")
