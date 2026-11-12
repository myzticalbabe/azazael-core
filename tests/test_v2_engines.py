# coding: utf-8
"""
Tests for the v2.0 evolution engines (audit gap G-4, 2026-09-11).

Covers what tests/test_nsga2.py does not: SBX + polynomial mutation,
mixed-type genomes, gene-hash fitness cache, SepCMAES, Differential
Evolution, TPESurrogate, NSGA-III reference
points, hypervolume, and the GeneticOptimizer cma/de wrappers.

Run:  python -m pytest tests/test_v2_engines.py -q   (from repo root)
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.WORMS.expansion.evolution import (  # noqa: E402
    DifferentialEvolution,
    EvolutionEngine,
    GeneticOptimizer,
    SepCMAES,
    TPESurrogate,
    generate_reference_points,
    hypervolume_2d,
)
from CORE.WORMS.expansion import evolution as evmod  # noqa: E402

BOUNDS2 = {"x": (-5.0, 5.0), "y": (-5.0, 5.0)}


def sphere(genes):
    return -(float(genes["x"]) ** 2 + float(genes["y"]) ** 2)


class TestSBXPoly(unittest.TestCase):
    def setUp(self):
        random.seed(7)
        self.e = EvolutionEngine(population_size=8, memory_store="none")
        self.e.initialize_population(dict(BOUNDS2))

    def test_children_within_bounds(self):
        for _ in range(50):
            p1 = {"x": random.uniform(-5, 5), "y": random.uniform(-5, 5)}
            p2 = {"x": random.uniform(-5, 5), "y": random.uniform(-5, 5)}
            c = self.e._crossover(p1, p2, dict(BOUNDS2))
            self.assertTrue(-5.0 <= c["x"] <= 5.0)
            self.assertTrue(-5.0 <= c["y"] <= 5.0)
            m = self.e._mutate(dict(c), dict(BOUNDS2))
            self.assertTrue(-5.0 <= m["x"] <= 5.0)
            self.assertTrue(-5.0 <= m["y"] <= 5.0)

    def test_discrete_passthrough(self):
        e = EvolutionEngine(population_size=6, memory_store="none")
        e.initialize_population({
            "f": {"type": "float", "lo": 0.0, "hi": 1.0},
            "b": {"type": "bool"},
            "c": {"type": "choice", "choices": ["a", "bb"]}})
        for g in e._population:
            self.assertIsInstance(g.genes["b"], bool)
            self.assertIn(g.genes["c"], ("a", "bb"))
        e.evaluate_population(lambda g: 1.0, use_curative=False)
        e.evolve()  # must not raise on mixed types
        kinds = {type(g.genes["b"]).__name__ for g in e._population}
        self.assertEqual(kinds, {"bool"})

    def test_legacy_operators_still_work(self):
        e = EvolutionEngine(population_size=6, crossover_operator="uniform",
                            mutation_operator="uniform", memory_store="none")
        e.initialize_population({"x": (0.0, 1.0)})
        e.evaluate_population(lambda g: g.genes["x"], use_curative=False)
        e.evolve()
        self.assertEqual(e._generation, 1)


class TestHashCache(unittest.TestCase):
    def test_cross_cycle_reuse(self):
        e = EvolutionEngine(population_size=6, memory_store="none")
        e.initialize_population({"x": (0.0, 1.0)})
        calls = []

        def fit(genome):
            calls.append(genome.id)
            return float(genome.genes["x"])

        e.evaluate_population(fit, use_curative=False)
        n1 = len(calls)
        # new UUIDs, same genes -> hash cache must hit, no new evals
        for g in e._population:
            import uuid as _u
            g.id = f"g_{_u.uuid4().hex}"
        e._fitness_cache.clear()
        stats = e.evaluate_population(fit, use_curative=False)
        self.assertEqual(len(calls), n1)
        self.assertGreater(stats.get("cache_hits", 0), 0)


class TestSepCMAES(unittest.TestCase):
    def test_sphere_improves(self):
        random.seed(11)
        c = SepCMAES(dict(BOUNDS2))
        r = c.optimize(sphere, generations=8)
        self.assertGreater(r["best_fitness"], -5.0)
        self.assertEqual(r["evals"], c.lam * 8)

    def test_ask_tell_shapes(self):
        c = SepCMAES({"x": (0.0, 1.0)})
        pop = c.ask()
        self.assertEqual(len(pop), c.lam)
        for g in pop:
            self.assertTrue(0.0 <= g["x"] <= 1.0)
        stat = c.tell([float(i) for i in range(len(pop))])
        self.assertIn("best_fitness", stat)


class TestDE(unittest.TestCase):
    def test_sphere_improves(self):
        random.seed(13)
        d = DifferentialEvolution(dict(BOUNDS2), popsize=10)
        r = d.optimize(sphere, generations=8)
        self.assertGreater(r["best_fitness"], -5.0)
        self.assertIn("x", r["best_genes"])


class TestTPE(unittest.TestCase):
    def test_suggest_in_bounds(self):
        t = TPESurrogate({"x": (0.0, 1.0), "y": (-5.0, 5.0)})
        self.assertIn("x", t.suggest())  # random before history
        for i in range(12):
            t.observe({"x": i / 12.0, "y": 0.0}, float(i))
        for _ in range(5):
            s = t.suggest()
            self.assertTrue(0.0 <= s["x"] <= 1.0)
            self.assertTrue(-5.0 <= s["y"] <= 5.0)


class TestManyObjectiveHelpers(unittest.TestCase):
    def test_reference_points_2obj(self):
        pts = generate_reference_points(2, 2)
        self.assertEqual(len(pts), 3)
        for p in pts:
            self.assertAlmostEqual(sum(p), 1.0)

    def test_hypervolume_known(self):
        hv = hypervolume_2d([(0.2, 0.8), (0.5, 0.5), (0.8, 0.2)], (1.0, 1.0))
        self.assertAlmostEqual(hv, 0.37, places=6)


class TestOptimizerWrappers(unittest.TestCase):
    def test_optimize_cma(self):
        random.seed(17)
        opt = GeneticOptimizer()
        r = opt.optimize_cma("T", dict(BOUNDS2), sphere, generations=3)
        self.assertEqual(r["method"], "sep-cma-es")
        self.assertIn("best_genes", r)

    def test_optimize_de(self):
        random.seed(19)
        opt = GeneticOptimizer()
        r = opt.optimize_de("T", dict(BOUNDS2), sphere, generations=3)
        self.assertEqual(r["method"], "de")
        self.assertIn("best_genes", r)


class TestMutationKills(unittest.TestCase):
    """One targeted test per mutant that survived the 2026-09-11 run.

    Each test pins the exact semantics a single-operator fault would
    break. If any of these fail on unmutated code, the test (not the
    code) is wrong — verify before "fixing" the source.
    """

    def test_cma_mean_exact_update(self):
        # kills: arith-flip Div->Mult @1398 (mean shift / rng).
        # Fully deterministic: crafted population, hand-computed weights,
        # explicit /rng in the expectation. The mutant scales by rng
        # instead and misses by ~100x on a width-10 gene.
        import math
        c = SepCMAES({"x": (0.0, 10.0)})
        c._pop = [{"x": 9.0}, {"x": 8.5}, {"x": 8.0},
                  {"x": 0.0}, {"x": 0.5}, {"x": 1.0}]
        fits = [9.0, 8.5, 8.0, 0.0, 0.5, 1.0]
        w = [math.log(c.mu + 0.5) - math.log(i + 1) for i in range(c.mu)]
        s = sum(w)
        weights = [x / s for x in w]
        old, rng = 5.0, 10.0
        exp = old + sum(weights[r] * ([9.0, 8.5, 8.0][r] - old) / rng
                        for r in range(c.mu))
        c.tell(fits)
        self.assertAlmostEqual(c.mean["x"], exp, places=9)

    def test_cma_diag_exact_update(self):
        # kills: arith-flip Div->Mult @1414 (rank-mu term / rng / sigma).
        # Same crafted tell; the diagonal is recomputed explicitly here.
        # The mutant multiplies by rng and overshoots ~100x.
        import math
        c = SepCMAES({"x": (0.0, 10.0)})
        c._pop = [{"x": 9.0}, {"x": 8.5}, {"x": 8.0},
                  {"x": 0.0}, {"x": 0.5}, {"x": 1.0}]
        fits = [9.0, 8.5, 8.0, 0.0, 0.5, 1.0]
        w = [math.log(c.mu + 0.5) - math.log(i + 1) for i in range(c.mu)]
        s = sum(w)
        weights = [x / s for x in w]
        old, rng, sig = 5.0, 10.0, c.sigma
        yw = sum(weights[r] * ([9.0, 8.5, 8.0][r] - old) / rng / sig
                 for r in range(c.mu))
        pc = ((c.cc * (2.0 - c.cc) * c.mueff) ** 0.5) * yw  # pc starts at 0
        rank1 = pc ** 2
        rankmu = sum(weights[r] * (([9.0, 8.5, 8.0][r] - old) / rng / sig) ** 2
                     for r in range(c.mu))
        exp = max(1e-8, (1.0 - c.c1 - c.cmu) * 1.0 + c.c1 * rank1 + c.cmu * rankmu)
        c.tell(fits)
        self.assertAlmostEqual(c.diagC["x"], exp, places=9)

    def test_cma_strategy_params_sane(self):
        # Strategy rates are probabilities-ish: strictly inside (0, 1).
        c = SepCMAES({"x": (0.0, 10.0), "y": (0.0, 10.0)})
        for v in (c.cc, c.cs, c.c1, c.cmu):
            self.assertGreater(v, 0.0)
            self.assertLess(v, 1.0)

    def test_cma_strategy_params_exact(self):
        # kills: arith-flip Div->Mult @1344 (the / self.n inside cc).
        # Bounds can't see this one: correct cc ~= 0.625, mutant ~= 0.355
        # (both inside (0, 1)). Recompute the Hansen-2016 defaults
        # explicitly; any operator fault in the four rates misses.
        import math
        c = SepCMAES({"x": (0.0, 10.0), "y": (0.0, 10.0)})
        n, mu = c.n, c.mu
        w = [math.log(mu + 0.5) - math.log(i + 1) for i in range(mu)]
        mueff = sum(w) ** 2 / sum(x * x for x in w)
        self.assertAlmostEqual(c.cc, (4.0 + mueff / n) / (n + 4.0 + 2.0 * mueff / n))
        self.assertAlmostEqual(c.cs, (mueff + 2.0) / (n + mueff + 5.0))
        self.assertAlmostEqual(c.c1, 2.0 / ((n + 1.3) ** 2 + mueff))
        self.assertAlmostEqual(
            c.cmu, min(1.0 - c.c1, 2.0 * (mueff - 2.0 + 1.0 / mueff) / ((n + 2.0) ** 2 + mueff)))

    def test_cma_sigma_init(self):
        # kills: arith-flip Add->Sub @1354 (sigma0 fraction).
        # sigma0 is a fraction of mean range: 10 * 0.3 == 3.0 exactly.
        # The mutant subtracts the fraction instead: 9.7.
        c = SepCMAES({"x": (0.0, 10.0), "y": (0.0, 10.0)})
        self.assertAlmostEqual(c.sigma, 3.0)

    def test_cma_pc_exact_update(self):
        # kills: arith-flip Sub->Add @1411 ((1 - cc) decay on old pc).
        # Col 26 is the decay term, which vanishes when pc starts at 0 —
        # so seed pc nonzero, then recompute the full update explicitly.
        # The mutant retains (1 + cc) of the old path instead of (1 - cc).
        import math
        c = SepCMAES({"x": (0.0, 10.0)})
        c.pc["x"] = 1.0
        c._pop = [{"x": 9.0}, {"x": 8.5}, {"x": 8.0},
                  {"x": 0.0}, {"x": 0.5}, {"x": 1.0}]
        fits = [9.0, 8.5, 8.0, 0.0, 0.5, 1.0]
        w = [math.log(c.mu + 0.5) - math.log(i + 1) for i in range(c.mu)]
        s = sum(w)
        weights = [x / s for x in w]
        yw = sum(weights[r] * ([9.0, 8.5, 8.0][r] - 5.0) / 10.0 / c.sigma
                 for r in range(c.mu))
        exp = (1.0 - c.cc) * 1.0 + ((c.cc * (2.0 - c.cc) * c.mueff) ** 0.5) * yw
        c.tell(fits)
        self.assertAlmostEqual(c.pc["x"], exp, places=9)

    def test_sbx_children_track_parents(self):
        # kills: arith-flip Mult->Div @469 (SBX child blend).
        # With an enormous distribution index beta->1, every child must
        # land within 0.5 of one parent; the mutant collapses to ~0.1.
        e = EvolutionEngine(population_size=6, memory_store="none")
        e.eta_c = 1e9
        e.initialize_population({"x": (0.0, 10.0)})
        p1, p2 = {"x": 2.0}, {"x": 8.0}
        for _ in range(50):
            c = e._sbx_crossover(p1, p2, {"x": (0.0, 10.0)})
            self.assertTrue(abs(c["x"] - 2.0) <= 0.5 or abs(c["x"] - 8.0) <= 0.5)

    def test_poly_mutation_moves(self):
        # kills: arith-flip Mult->Div @520 (delta * range).
        # The mutant divides by range: steps shrink 100x on a width-10
        # gene and nothing ever moves. Correct code strides visibly.
        e = EvolutionEngine(population_size=6, mutation_rate=1.0,
                            mutation_operator="polynomial", memory_store="none")
        e.eta_m = 0.5
        e.initialize_population({"x": (0.0, 10.0)})
        moved = False
        for _ in range(30):
            m = e._polynomial_mutate({"x": 5.0}, {"x": (0.0, 10.0)})
            if abs(m["x"] - 5.0) > 0.5:
                moved = True
                break
        self.assertTrue(moved)

    def test_legacy_uniform_mutate_direction(self):
        # kills: arith-flip Add->Sub @488 (missing-gene fill + perturbation).
        # The legacy path draws random.uniform(-0.1, 0.1) and scales by
        # range: mocked to 0.04, the fill must land exactly at mid + 0.4;
        # the mutant mirrors it to mid - 0.4.
        from unittest import mock
        e = EvolutionEngine(population_size=6, mutation_rate=1.0,
                            mutation_operator="uniform", memory_store="none")
        with mock.patch("random.random", return_value=0.0), \
                mock.patch("random.uniform", return_value=0.04):
            m = e._mutate({}, {"x": (0.0, 10.0)})
        self.assertAlmostEqual(m["x"], 5.4)

    def test_legacy_discrete_resample_rate(self):
        # kills: compare-flip Lt->Gt @491 (random < mutation_rate).
        # At rate 1.0 a bool gene must resample (change observed over 20
        # draws); the mutant never resamples (random > 1.0 impossible).
        e = EvolutionEngine(population_size=6, mutation_rate=1.0,
                            mutation_operator="uniform", memory_store="none")
        e.initialize_population({"b": {"type": "bool"}})
        seen = {e._mutate({"b": True}, {})["b"] for _ in range(20)}
        self.assertEqual(seen, {True, False})

    def test_init_accepts_seed_genomes(self):
        # kills: arith-flip Add->Sub @236 (seed key-union list + list).
        # The mutant turns list concatenation into subtraction: TypeError
        # on ANY seeded init. Correct code merges bound keys + spec keys.
        e = EvolutionEngine(population_size=6, memory_store="none")
        pop = e.initialize_population(
            {"x": (0.0, 1.0)},
            seed_genomes=[{"x": 0.25}],
            gene_specs={"x": {"type": "float", "lo": 0.0, "hi": 1.0},
                        "flag": {"type": "bool"}})
        self.assertEqual(len(pop), 6)
        self.assertTrue(all("flag" in g.genes for g in pop))

    def test_eval_failure_print_capped(self):
        # kills: compare-flip LtE->GtE @316 (eval_failures <= 3).
        # Only the first three failures may print; the mutant prints
        # every failure from the fourth onward (inverts the cap).
        import io
        from contextlib import redirect_stdout
        e = EvolutionEngine(population_size=6, memory_store="none")
        e.initialize_population({"x": (0.0, 1.0)})

        def boom(genome):
            raise RuntimeError("nope")

        buf = io.StringIO()
        with redirect_stdout(buf):
            e.evaluate_population(boom, use_curative=False)
        self.assertEqual(buf.getvalue().count("failed for"), 3)

    def test_crossover_respects_passed_bounds(self):
        # kills: bool-flip Or->And @420 (gene_bounds or self._gene_bounds).
        # Passed bounds must win for clamping; the mutant clamps to the
        # engine bounds instead. Parents live in (100, 200), engine in (0, 1).
        e = EvolutionEngine(population_size=6, memory_store="none")
        e.initialize_population({"x": (0.0, 1.0)})
        for _ in range(20):
            c = e._crossover({"x": 150.0}, {"x": 160.0}, {"x": (100.0, 200.0)})
            self.assertGreaterEqual(c["x"], 100.0)
            self.assertLessEqual(c["x"], 200.0)

    def test_tpe_suggest_respects_nonzero_bounds(self):
        # kills: arith-flip Sub->Add @1649 (denormalizer hi - lo).
        # Candidates live in normalized [0, 1] and map back via
        # lo + c*(hi - lo). The mutant adds (hi + lo): identical on
        # unit bounds, out-of-range everywhere else. Nonzero bounds
        # expose it: every suggestion must land inside [2, 4].
        random.seed(4)
        t = TPESurrogate({"x": (2.0, 4.0)})
        for _ in range(6):
            t.observe({"x": 3.8}, 10.0)
        for _ in range(6):
            t.observe({"x": 2.2}, 0.0)
        for _ in range(10):
            s = t.suggest()["x"]
            self.assertGreaterEqual(s, 2.0)
            self.assertLessEqual(s, 4.0)

    def test_optimizer_persist_gate(self):
        # kills: bool-flip And->Or @1245 (store-resolution guard).
        # With the memory backend flagged unavailable, no store may be
        # touched even if a getter exists; the mutant calls it anyway.
        import CORE.WORMS.expansion.evolution as evmod

        class FakeStore:
            def __init__(self):
                self.events = []

            def record_event(self, **kw):
                self.events.append(kw)

        fake = FakeStore()
        old_ok, old_get = evmod._LOCAL_MEMORY_OK, evmod._get_memory_store_global
        evmod._LOCAL_MEMORY_OK, evmod._get_memory_store_global = False, lambda: fake
        try:
            opt = GeneticOptimizer()
            opt.initialize("KillT", {"x": (0.0, 1.0)}, population_size=4)
            opt.optimize("KillT", generations=1, fitness_fn=lambda g: 1.0)
        finally:
            evmod._LOCAL_MEMORY_OK, evmod._get_memory_store_global = old_ok, old_get
        self.assertEqual(fake.events, [])


class TestWireOrCutVerdict(unittest.TestCase):
    """ROADMAP 3.2/3.3: every engine is SERVED or explicitly declared UNSERVED.

    An unwired engine that looks wired is exercised by nothing, so the verdict is
    enforced rather than documented: the unserved helpers must not be referenced by
    the production driver, and every declared name must actually exist.
    """

    def test_an_engine_is_served_xor_unserved(self):
        served = set(evmod.SERVED_ENGINES)
        unserved = set(evmod.UNSERVED_ENGINES)
        self.assertFalse(served & unserved, "an engine cannot be both")
        self.assertTrue(served, "nothing served?")

    def test_every_declared_engine_exists_and_unserved_ones_say_why(self):
        for name in evmod.SERVED_ENGINES:
            self.assertTrue(hasattr(evmod, name), "%s is not defined" % name)
        for name, why in evmod.UNSERVED_ENGINES.items():
            self.assertTrue(hasattr(evmod, name), "%s is not defined" % name)
            self.assertTrue(str(why).strip(), "%s has no reason" % name)

    def test_the_production_driver_references_only_served_engines(self):
        """A half-wiring must fail HERE, not pass unnoticed."""
        cycle = (Path(__file__).resolve().parent.parent
                 / "scripts" / "evolution_cycle.py").read_text(encoding="utf-8")
        for name in evmod.UNSERVED_ENGINES:
            self.assertNotIn(name, cycle,
                             "unserved engine %s IS referenced by the cycle; "
                             "either wire it and move it to SERVED_ENGINES, or "
                             "stop referencing it" % name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
