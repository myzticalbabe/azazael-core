# coding: utf-8
"""
Tests for the PROTEUS expansion organs (2026-09-11).

Covers the modules added in response to the 50-run gaps:
  morph    — metamorphic genomes, viability, gene economics
  arena    — POET co-evolution, minimal criterion, transfer
  loom     — intrinsic-dimension novelty drive
  nerve    — neuromodulated plasticity
  chorus   — structural proposer (fail-open)
  allocation — PROTEUS arm set + outcome weighting

Every organ must be importable and functional under BOTH the GIL interpreter
(where torch/xpu and numpy load) and the free-threaded one (where they do not).
The numpy-free paths are asserted explicitly.
"""

import os
import sys
import json
import math
import random
import tempfile
import unittest
import unittest.mock

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_src_dir = os.path.join(_project_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from CORE.WORMS.expansion.morph import (
    GeneSpec, Genome, structural_mutate, viability, prune_zero_variance,
    dimension_report, seed_pool_from_bounds, TRAINER_KWARGS, NON_GENE_KWARGS,
    save_genome, load_genome,
)
from CORE.WORMS.expansion.arena import (
    Environment, ArenaWorld, Arena, keeper_split,
)
from CORE.WORMS.expansion.loom import (
    participation_ratio, jacobi_eigenvalues, intrinsic_dimension,
    spectral_summary, NoveltyDrive, descriptors_from_archive,
)
from CORE.WORMS.expansion.nerve import (
    Neuromodulator, context_vector, modulated_q_update,
    MODULATION_LO, MODULATION_HI,
)
from CORE.WORMS.expansion.chorus import (
    Chorus, FailureDigest, parse_proposals,
)
from CORE.WORMS.expansion.allocation import (
    ThompsonAllocator, PROTEUS_ARMS, reward_for_outcome,
)


# ══════════════════════════════════════════════════════════════════════
# Morph
# ══════════════════════════════════════════════════════════════════════

class TestMorphGeneSpec(unittest.TestCase):

    def test_roundtrip_dict(self):
        s = GeneSpec("lr", "float", 0.01, 0.9, kwargs="learning_rate")
        again = GeneSpec.from_dict(s.to_dict())
        self.assertEqual(again.name, "lr")
        self.assertEqual(again.kind, "float")
        self.assertEqual(again.kwargs, "learning_rate")
        self.assertAlmostEqual(again.hi, 0.9)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            GeneSpec("x", "quantum")

    def test_contains_and_clamp(self):
        s = GeneSpec("lr", "float", 0.0, 1.0)
        self.assertTrue(s.contains(0.5))
        self.assertFalse(s.contains(1.5))
        self.assertFalse(s.contains("nope"))
        self.assertEqual(s.clamp(9.0), 1.0)
        self.assertEqual(s.clamp(-9.0), 0.0)

    def test_bool_spec_rejects_numbers(self):
        b = GeneSpec("flag", "bool")
        self.assertTrue(b.contains(True))
        self.assertFalse(b.contains(1.5))

    def test_choice_spec(self):
        c = GeneSpec("mode", "choice", choices=["a", "b"])
        self.assertTrue(c.contains("b"))
        self.assertFalse(c.contains("z"))
        self.assertIn(c.sample(random.Random(0)), ["a", "b"])

    def test_schedule_spec_shape(self):
        s = GeneSpec("lr", "schedule", 0.01, 0.9, points=3)
        v = s.sample(random.Random(0))
        self.assertEqual(len(v), 3)
        self.assertTrue(s.contains(v))
        self.assertFalse(s.contains([0.01, 5.0, 0.9]))


class TestMorphGenome(unittest.TestCase):

    def setUp(self):
        self.bounds = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5)}
        self.pool = seed_pool_from_bounds(self.bounds)
        self.g = Genome.from_flat(self.bounds, {"learning_rate": 0.7, "epsilon": 0.3})

    def test_dimension_and_names(self):
        self.assertEqual(self.g.dimension, 2)
        self.assertEqual(sorted(self.g.names()), ["epsilon", "learning_rate"])

    def test_to_trainer_kwargs_maps_names(self):
        kw = self.g.to_trainer_kwargs()
        self.assertEqual(kw["learning_rate"], 0.7)
        self.assertEqual(kw["epsilon"], 0.3)

    def test_non_gene_kwargs_never_emitted(self):
        """n_episodes/max_steps would create fake selection pressure."""
        g = Genome(
            [GeneSpec("n_episodes", "int", 10, 500, kwargs="n_episodes"),
             GeneSpec("max_steps", "int", 10, 500, kwargs="max_steps"),
             GeneSpec("lr", "float", 0.01, 0.9, kwargs="learning_rate")],
            [400, 400, 0.5],
        )
        kw = g.to_trainer_kwargs()
        self.assertNotIn("n_episodes", kw)
        self.assertNotIn("max_steps", kw)
        self.assertEqual(kw["learning_rate"], 0.5)
        ok, reason = viability(g)
        self.assertFalse(ok)
        self.assertIn("non_gene_kwarg", reason)

    def test_schedule_emits_scalar_and_sequence(self):
        g = Genome([GeneSpec("lr", "schedule", 0.01, 0.9, kwargs="learning_rate", points=3)],
                   [[0.01, 0.455, 0.9]])
        kw = g.to_trainer_kwargs()
        self.assertEqual(kw["learning_rate"], 0.01)
        self.assertEqual(kw["learning_rate_schedule"], [0.01, 0.455, 0.9])

    def test_vector_is_numeric(self):
        g = Genome([GeneSpec("f", "float", 0, 1),
                    GeneSpec("i", "int", 0, 5),
                    GeneSpec("b", "bool"),
                    GeneSpec("c", "choice", choices=["a", "b"]),
                    GeneSpec("s", "schedule", 0, 1, points=3)],
                   [0.5, 3, True, "b", [0.0, 0.5, 1.0]])
        v = g.vector()
        self.assertTrue(all(isinstance(x, float) for x in v))
        self.assertEqual(len(v), 1 + 1 + 1 + 1 + 3)

    def test_copy_is_independent(self):
        c = self.g.copy()
        c.values[0] = 0.123
        self.assertNotEqual(self.g.values[0], 0.123)


class TestMorphStructuralOperators(unittest.TestCase):

    def setUp(self):
        self.bounds = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5)}
        self.pool = seed_pool_from_bounds(self.bounds)
        self.g = Genome.from_flat(self.bounds, {"learning_rate": 0.7, "epsilon": 0.3})

    def test_structural_mutation_returns_viable_child_eventually(self):
        rng = random.Random(7)
        grew, ops = False, set()
        for _ in range(60):
            child, op = structural_mutate(self.g, self.pool, rng)
            ops.add(op)
            ok, reason = viability(child)
            self.assertTrue(ok, "child failed viability: %s" % reason)
            if child.dimension > self.g.dimension:
                grew = True
        self.assertIn("add_gene", ops)
        self.assertTrue(grew, "structural mutation never grew the genome")

    def test_drop_gene_never_empties(self):
        rng = random.Random(3)
        g = self.g
        for _ in range(40):
            child, _ = structural_mutate(g, self.pool, rng)
            self.assertGreaterEqual(child.dimension, 1)

    def test_viability_catches_duplicates(self):
        g = Genome([GeneSpec("lr", "float", 0, 1), GeneSpec("lr", "float", 0, 1)], [0.5, 0.5])
        ok, reason = viability(g)
        self.assertFalse(ok)
        self.assertEqual(reason, "duplicate_gene_name")

    def test_viability_catches_unknown_kwarg(self):
        g = Genome([GeneSpec("x", "float", 0, 1, kwargs="totally_made_up")], [0.5])
        ok, reason = viability(g)
        self.assertFalse(ok)
        self.assertIn("unknown_kwarg", reason)

    def test_viability_catches_out_of_range(self):
        g = Genome([GeneSpec("lr", "float", 0.0, 0.5, kwargs="learning_rate")], [0.99])
        ok, reason = viability(g)
        self.assertFalse(ok)
        self.assertIn("out_of_range", reason)

    def test_viability_catches_non_finite(self):
        g = Genome([GeneSpec("lr", "float", 0.0, 1.0, kwargs="learning_rate")], [float("nan")])
        ok, reason = viability(g)
        self.assertFalse(ok)
        self.assertIn("non_finite", reason)

    def test_viability_catches_non_finite_inside_schedule(self):
        g = Genome([GeneSpec("lr", "schedule", 0.0, 1.0, kwargs="learning_rate", points=3)],
                   [[0.0, float("inf"), 1.0]])
        ok, reason = viability(g)
        self.assertFalse(ok)
        self.assertIn("non_finite", reason)

    def test_nest_promotes_scalar_to_schedule(self):
        rng = random.Random(11)
        found = False
        for _ in range(80):
            child, op = structural_mutate(self.g, self.pool, rng)
            if op == "nest":
                found = True
                self.assertTrue(any(s.kind == "schedule" for s in child.specs))
                break
        self.assertTrue(found, "nest operator never fired in 80 attempts")

    def test_structural_mutation_never_raises(self):
        rng = random.Random(5)
        weird = Genome([GeneSpec("a", "choice", choices=["only"])], ["only"])
        for _ in range(50):
            child, _ = structural_mutate(weird, self.pool, rng)
            self.assertIsNotNone(child)


class TestMorphGeneEconomics(unittest.TestCase):

    def test_prune_finds_collapsed_gene(self):
        specs = [GeneSpec("frozen", "float", 0, 1), GeneSpec("varying", "float", 0, 1)]
        genomes = [Genome(list(specs), [0.5, 0.1]),
                   Genome(list(specs), [0.5, 0.5]),
                   Genome(list(specs), [0.5, 0.9])]
        prune, keep = prune_zero_variance(genomes)
        self.assertEqual(prune, ["frozen"])
        self.assertIn("varying", keep)

    def test_prune_keeps_partially_present_gene(self):
        """A gene still being explored must not be pruned."""
        a = Genome([GeneSpec("x", "float", 0, 1), GeneSpec("y", "float", 0, 1)], [0.5, 0.1])
        b = Genome([GeneSpec("x", "float", 0, 1)], [0.5])
        prune, keep = prune_zero_variance([a, b])
        self.assertIn("x", prune)
        self.assertIn("y", keep)

    def test_prune_empty_is_safe(self):
        self.assertEqual(prune_zero_variance([]), ([], []))

    def test_dimension_report_groups(self):
        rows = [{"dimension": 4, "fitness": 25.0}, {"dimension": 4, "fitness": 23.0},
                {"dimension": 5, "fitness": 26.0}, {"fitness": 99.0}]
        rep = dimension_report(rows)
        self.assertIn("4", rep["by_dimension"])
        self.assertEqual(rep["by_dimension"]["4"]["n"], 2)
        self.assertAlmostEqual(rep["by_dimension"]["4"]["mean"], 24.0)
        self.assertEqual(rep["best_dimension"], 5)
        self.assertAlmostEqual(rep["best_fitness"], 26.0)

    def test_dimension_report_ignores_garbage(self):
        rep = dimension_report([{"dimension": "x", "fitness": "y"},
                                {"dimension": 3, "fitness": float("nan")}])
        self.assertEqual(rep["by_dimension"], {})

    def test_dimension_report_requires_explicit_dimension(self):
        """A row with no recorded dimension must not be merged into bucket 0."""
        rep = dimension_report([{"fitness": 99.0}, {"dimension": 4, "fitness": 25.0}])
        self.assertNotIn("0", rep["by_dimension"])
        self.assertEqual(rep["best_dimension"], 4)
        self.assertAlmostEqual(rep["best_fitness"], 25.0)

    def test_production_gene_name_resolves_to_trainer_kwarg(self):
        """Regression: 'discount' is the production gene name, 'discount_factor'
        the trainer kwarg. An unresolved name made every structural child lethal."""
        from CORE.WORMS.expansion.morph import (GENE_KWARG_ALIASES,
                                                resolve_kwarg)
        self.assertEqual(resolve_kwarg("discount"), "discount_factor")
        self.assertIn("discount", GENE_KWARG_ALIASES)
        g = Genome.from_flat({"discount": (0.5, 0.999)}, {"discount": 0.8033})
        self.assertEqual(g.specs[0].kwargs, "discount_factor")
        kw = g.to_trainer_kwargs()
        self.assertIn("discount_factor", kw)
        self.assertNotIn("discount", kw)
        ok, why = viability(g)
        self.assertTrue(ok, why)

    def test_children_of_production_genome_are_viable(self):
        """The full production gene set must survive structural mutation."""
        prod = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5),
                "discount": (0.5, 0.999), "epsilon_decay": (0.0, 0.3)}
        pool = seed_pool_from_bounds(prod)
        g = Genome.from_flat(prod, {"learning_rate": 0.5, "epsilon": 0.296,
                                    "discount": 0.8033, "epsilon_decay": 0.0859})
        rng = random.Random(0)
        for _ in range(120):
            child, _ = structural_mutate(g, pool, rng)
            ok, why = viability(child)
            self.assertTrue(ok, "lethal child escaped: %s (%s)" % (why, child.names()))

    def test_genome_persistence(self):
        pool = seed_pool_from_bounds({"lr": (0.0, 1.0)})
        g = Genome.random(pool, random.Random(0))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.json")
            save_genome(p, g, extra={"fitness": 25.0})
            back = load_genome(p)
            self.assertIsNotNone(back)
            self.assertEqual(back.names(), g.names())
            self.assertEqual(back.dimension, g.dimension)

    def test_load_genome_missing_file_returns_none(self):
        self.assertIsNone(load_genome(os.path.join(tempfile.gettempdir(), "nope_xyz.json")))


# ══════════════════════════════════════════════════════════════════════
# Arena
# ══════════════════════════════════════════════════════════════════════

class TestArenaEnvironment(unittest.TestCase):

    def test_severity_mix_normalised(self):
        e = Environment([2, 2, 4], 0.5, 1.0)
        self.assertAlmostEqual(sum(e.severity_mix), 1.0)
        self.assertAlmostEqual(e.severity_mix[-1], 0.5)

    def test_descriptor_width(self):
        e = Environment([1, 1, 1], 0.2, 3.0)
        self.assertEqual(len(e.descriptor()), 5)

    def test_mutation_changes_world(self):
        rng = random.Random(1)
        e = Environment([1, 1, 1], 0.5, 1.0, 1000, 0)
        m = e.mutate(rng)
        self.assertNotEqual(e.fingerprint(), m.fingerprint())
        self.assertTrue(0.0 <= m.hard_ratio <= 1.0)
        self.assertGreaterEqual(m.n_alerts, 10)

    def test_roundtrip(self):
        e = Environment([1, 2, 3], 0.4, 2.0, 500, 42)
        back = Environment.from_dict(e.to_dict())
        self.assertEqual(e.fingerprint(), back.fingerprint())


class TestArenaCore(unittest.TestCase):

    @staticmethod
    def _constant_solver(genome, cfg):
        return 50.0

    @staticmethod
    def _canyon_solver(genome, cfg):
        # optimum at hard_ratio 0.5; falls off smoothly either side
        return 100.0 * (1.0 - abs(cfg["hard_ratio"] - 0.5))

    def test_novelty_infinite_on_empty(self):
        a = Arena(self._constant_solver, seed=0)
        self.assertEqual(a.novelty(Environment([1, 1, 1], 0.5, 1.0)), float("inf"))

    def test_populate_admits_solvable_worlds(self):
        a = Arena(self._canyon_solver, mc_low=0.5, mc_high=0.95, archive_cap=16, seed=2)
        seed_env = Environment([1, 1, 1], 0.5, 1.0, 1000, 0)
        admitted = a.populate(seed_env, genome=None, n=6)
        self.assertTrue(admitted)
        self.assertEqual(len(a.worlds), len(admitted))

    def test_solver_exception_does_not_propagate(self):
        def boom(genome, cfg):
            raise RuntimeError("solver down")
        a = Arena(boom, seed=0)
        env = Environment([1, 1, 1], 0.5, 1.0)
        self.assertEqual(a.solve(None, env), float("-inf"))
        self.assertEqual(a.populate(env, None, n=3), [])

    def test_transfer_ratio_bounded(self):
        a = Arena(self._canyon_solver, seed=0)
        e1 = Environment([1, 1, 1], 0.5, 1.0)
        e2 = Environment([1, 1, 1], 0.9, 1.0)
        w1 = ArenaWorld(e1, None, 100.0)
        w2 = ArenaWorld(e2, None, 60.0)
        r = a.transfer(None, w1, w2)
        self.assertGreaterEqual(r["transfer_ratio"], 0.0)
        self.assertLessEqual(r["transfer_ratio"], 1.0)
        self.assertLess(r["transfer_ratio"], 1.0, "foreign world should score lower")

    def test_minimal_criterion_rejects_easy_worlds(self):
        """A world every solution already solves must not be admitted (POET MC)."""
        a = Arena(self._constant_solver, mc_low=0.35, mc_high=0.85, archive_cap=8, seed=1)
        seed_env = Environment([1, 1, 1], 0.5, 1.0, 1000, 0)
        a.populate(seed_env, None, n=3)
        row = a.step(propose_genome=lambda w: None, n_mutations=3)
        self.assertEqual(row["admitted"], 0)
        self.assertGreater(row["rejected_easy"], 0)
        self.assertEqual(sum(k for k in (row["admitted"],)), 0)

    def test_step_on_empty_arena_is_safe(self):
        a = Arena(self._canyon_solver, seed=0)
        row = a.step(propose_genome=lambda w: None)
        self.assertEqual(row["admitted"], 0)
        self.assertEqual(row["archive"], 0)

    def test_archive_respects_cap(self):
        a = Arena(self._canyon_solver, mc_low=0.1, mc_high=0.99, archive_cap=5, seed=4)
        seed_env = Environment([1, 1, 1], 0.5, 1.0, 1000, 0)
        a.populate(seed_env, None, n=8)
        for _ in range(4):
            a.step(propose_genome=lambda w: None, n_mutations=3)
        self.assertLessEqual(len(a.worlds), 5)

    def test_keeper_split_disjoint_by_construction(self):
        worlds = [ArenaWorld(Environment([1, 1, 1], i / 10.0, 1.0), None, 1.0)
                  for i in range(8)]
        search, keeper = keeper_split(worlds, keeper_frac=0.25)
        self.assertTrue(keeper)
        s_keys = {w.key for w in search}
        k_keys = {w.key for w in keeper}
        self.assertFalse(s_keys & k_keys, "keeper worlds must be disjoint from search worlds")

    def test_keeper_split_empty(self):
        self.assertEqual(keeper_split([], 0.25), ([], []))

    def test_persistence_roundtrip(self):
        a = Arena(self._canyon_solver, seed=0)
        env = Environment([1, 1, 1], 0.5, 1.0, 1000, 0)
        a.populate(env, None, n=3)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "arena.json")
            a.save(p)
            b = Arena(self._canyon_solver, seed=0)
            b.load(p)
            self.assertEqual(len(b.worlds), len(a.worlds))


# ══════════════════════════════════════════════════════════════════════
# Loom
# ══════════════════════════════════════════════════════════════════════

class TestLoomSpectral(unittest.TestCase):

    def test_participation_ratio_isotropic(self):
        self.assertAlmostEqual(participation_ratio([1.0, 1.0, 1.0]), 3.0, places=6)

    def test_participation_ratio_rank_one(self):
        self.assertAlmostEqual(participation_ratio([10.0, 0.0, 0.0]), 1.0, places=6)

    def test_participation_ratio_degenerate(self):
        self.assertEqual(participation_ratio([]), 0.0)
        self.assertEqual(participation_ratio([0.0, 0.0]), 0.0)

    def test_jacobi_known_2x2(self):
        eigs = jacobi_eigenvalues([[2.0, 1.0], [1.0, 2.0]])
        self.assertAlmostEqual(eigs[0], 3.0, places=6)
        self.assertAlmostEqual(eigs[1], 1.0, places=6)

    def test_jacobi_descending_and_symmetric_input(self):
        eigs = jacobi_eigenvalues([[4.0, 1.0, 0.0], [1.0, 3.0, 1.0], [0.0, 1.0, 2.0]])
        self.assertEqual(eigs, sorted(eigs, reverse=True))
        self.assertAlmostEqual(sum(eigs), 9.0, places=5)

    def test_jacobi_edge_sizes(self):
        self.assertEqual(jacobi_eigenvalues([]), [])
        self.assertEqual(jacobi_eigenvalues([[7.0]]), [7.0])

    def test_intrinsic_dimension_spread_vs_collapsed(self):
        rng = random.Random(0)
        spread = [[rng.gauss(0, 1) for _ in range(4)] for _ in range(40)]
        collapsed = [[0.5, 0.5, 0.5, 0.5] for _ in range(40)]
        self.assertGreater(intrinsic_dimension(spread), 2.5)
        self.assertLess(intrinsic_dimension(collapsed), 0.5)

    def test_intrinsic_dimension_needs_two_points(self):
        self.assertEqual(intrinsic_dimension([]), 0.0)
        self.assertEqual(intrinsic_dimension([[1.0, 2.0]]), 0.0)

    def test_spectral_summary_keys(self):
        pts = [[0.0, 1.0], [1.0, 0.0], [2.0, 3.0], [3.0, 1.0]]
        s = spectral_summary(pts)
        for k in ("id", "n", "eigenvalues", "decay", "top_mass", "anisotropy"):
            self.assertIn(k, s)
        self.assertEqual(s["n"], 4)

    def test_spectral_summary_empty(self):
        s = spectral_summary([])
        self.assertEqual(s["id"], 0.0)
        self.assertEqual(s["n"], 0)

    def test_spectral_summary_without_numpy(self):
        """Regression: with numpy absent (free-threaded interpreter) the numpy
        branch never runs, so `eigs` must be pre-bound or this raises
        UnboundLocalError. Found by the dual-interpreter smoke check."""
        import CORE.WORMS.expansion.loom as loom
        pts = [[float(i), float(i * i), float(i % 3)] for i in range(20)]
        with unittest.mock.patch.object(loom, "_np", None):
            s = loom.spectral_summary(pts)
            self.assertEqual(s["n"], 20)
            self.assertGreater(s["id"], 0.0)
            self.assertFalse(s["numpy"])
            self.assertEqual(loom.intrinsic_dimension(pts) > 1.0, True)


class TestLoomDrive(unittest.TestCase):

    def test_collapsing_archive_engages_pressure(self):
        d = NoveltyDrive(setpoint=3.0, gain=1.5)
        last = None
        for _ in range(3):
            last = d.update([[0.5] * 4 for _ in range(20)])
        self.assertGreater(last["pressure"], 0.5)
        self.assertTrue(d.should_restart(3))
        self.assertIn("COLLAPSED", d.diagnostic())

    def test_healthy_archive_stays_quiet(self):
        rng = random.Random(1)
        d = NoveltyDrive(setpoint=3.0, gain=1.5)
        for _ in range(3):
            last = d.update([[rng.gauss(0, 1) for _ in range(4)] for _ in range(40)])
        self.assertLess(last["pressure"], 0.35)
        self.assertFalse(d.should_restart(3))

    def test_novelty_weight_is_capped(self):
        d = NoveltyDrive(setpoint=3.0, gain=1.5)
        d.update([[1.0] * 4 for _ in range(10)])
        self.assertLessEqual(d.novelty_weight(base=0.5, ceiling=0.6), 0.6)
        self.assertGreaterEqual(d.novelty_weight(base=0.1, ceiling=0.6), 0.1)

    def test_empty_update_is_safe(self):
        d = NoveltyDrive()
        row = d.update([])
        self.assertEqual(row["n"], 0)
        self.assertIn("pressure", row)

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "loom.json")
            d = NoveltyDrive(path=p)
            d.update([[float(i)] * 3 for i in range(10)])
            self.assertTrue(os.path.exists(p))
            e = NoveltyDrive(path=p)
            e.load()
            self.assertEqual(len(e.history), len(d.history))

    def test_descriptors_from_archive(self):
        cells = [{"descriptor": [0.1, 0.2]}, {"cell": [0.3, 0.4]}, {"nope": 1}, "junk"]
        pts = descriptors_from_archive(cells)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0], [0.1, 0.2])


# ══════════════════════════════════════════════════════════════════════
# Nerve
# ══════════════════════════════════════════════════════════════════════

class TestNerve(unittest.TestCase):

    def test_context_vector_shape_and_bounds(self):
        c = context_vector("advisor", "critical", 5, 20, 0.3, 0.5)
        self.assertEqual(len(c), 6)
        self.assertTrue(all(isinstance(x, float) for x in c))
        self.assertTrue(all(-1.0 <= x <= 1.0 for x in c), c)

    def test_context_vector_hash_stable(self):
        """Must not use salted hash() — buckets have to survive a new process."""
        self.assertEqual(context_vector("a", "b", 1, 10, 0.1),
                         context_vector("a", "b", 1, 10, 0.1))

    def test_modulations_within_bounds_numpy_backend(self):
        m = Neuromodulator(use_torch=False)
        mods = m.modulations(context_vector("s", "sev", 3, 10, 0.2))
        self.assertEqual(len(mods), m.n_out)
        for v in mods:
            self.assertGreaterEqual(v, MODULATION_LO - 1e-9)
            self.assertLessEqual(v, MODULATION_HI + 1e-9)

    def test_param_vector_roundtrip_numpy_backend(self):
        m = Neuromodulator(use_torch=False)
        before = m.modulations([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
        params = m.get_params()
        self.assertEqual(len(params), m.n_params)
        scaled = [p * 2.0 for p in params]
        m.set_params(scaled)
        after = m.modulations([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
        self.assertNotEqual(before, after)
        m.set_params(params)
        self.assertEqual(m.modulations([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]), before)

    def test_modulated_q_update_moves_toward_reward(self):
        q = {}
        td = modulated_q_update(q, "s", "a", 1.0, "s2", 0.1, 0.9, [1.0, 1.0, 1.0])
        self.assertAlmostEqual(td, 1.0, places=6)
        self.assertAlmostEqual(q[("s", "a")], 0.1, places=6)
        modulated_q_update(q, None, "a", 1.0, None, 0.1, 0.9, [2.0, 1.0, 1.0])
        self.assertGreater(q[(None, "a")], 0.1)

    def test_alpha_modulation_is_capped_at_one(self):
        q = {}
        modulated_q_update(q, "s", "a", 1.0, None, 0.5, 0.9, [99.0, 1.0, 1.0])
        self.assertLessEqual(q[("s", "a")], 1.0)

    def test_modulated_q_update_handles_terminal_state(self):
        q = {}
        modulated_q_update(q, "s", "a", 0.0, None, 0.1, 0.99, [1.0, 1.0, 1.0])
        self.assertIn(("s", "a"), q)

    def test_neuromodulator_degrades_without_torch(self):
        """Default construction requests torch; absence must degrade, not raise."""
        import CORE.WORMS.expansion.nerve as nerve
        with unittest.mock.patch.object(nerve, "_torch", None):
            m = nerve.Neuromodulator()
            self.assertEqual(m.backend, "numpy")
            mods = m.modulations(nerve.context_vector("s", "sev", 1, 10, 0.2))
            self.assertEqual(len(mods), m.n_out)


# ══════════════════════════════════════════════════════════════════════
# Chorus
# ══════════════════════════════════════════════════════════════════════

class _StubProvider:
    """Minimal stand-in for CORE.inference.providers.OpenAIProvider.chat."""
    def __init__(self, payload, raises=False):
        self.payload = payload
        self.raises = raises
        self.calls = 0

    def chat(self, prompt, system_prompt=None, model=None, max_tokens=None,
             temperature=None):
        self.calls += 1
        if self.raises:
            raise RuntimeError("provider exploded")
        return {"content": self.payload, "model": "stub"}


class TestChorusParsing(unittest.TestCase):

    def test_parses_bare_array(self):
        out = parse_proposals('[{"op": "nest", "gene": "learning_rate"}]')
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["op"], "nest")

    def test_parses_code_fenced(self):
        text = "Here you go:\n```json\n[{\"op\":\"drop_gene\",\"gene\":\"epsilon\"}]\n```\nHope that helps."
        out = parse_proposals(text)
        self.assertEqual(out[0]["op"], "drop_gene")

    def test_parses_prose_wrapped_object(self):
        out = parse_proposals('I suggest {"op": "nest", "gene": "epsilon"} because it helps.')
        self.assertEqual(out[0]["op"], "nest")

    def test_garbage_returns_empty(self):
        self.assertEqual(parse_proposals("no json here at all"), [])
        self.assertEqual(parse_proposals(""), [])
        self.assertEqual(parse_proposals(None), [])

    def test_filters_non_dict_entries(self):
        out = parse_proposals('[1, "two", {"op": "tune", "gene": "epsilon"}]')
        self.assertEqual(len(out), 1)

    def test_limit_respected(self):
        payload = json.dumps([{"op": "nest", "gene": "g%d" % i} for i in range(10)])
        self.assertEqual(len(parse_proposals(payload, limit=3)), 3)


class TestChorusProposer(unittest.TestCase):

    def setUp(self):
        self.pool = seed_pool_from_bounds({"learning_rate": (0.01, 0.9),
                                           "epsilon": (0.0, 0.5)})
        self.genome = Genome.from_flat({"learning_rate": (0.01, 0.9),
                                        "epsilon": (0.0, 0.5)},
                                       {"learning_rate": 0.5, "epsilon": 0.3})

    def test_digest_renders_failure_modes(self):
        d = FailureDigest(holdout_failures=4, pinned_genes=["learning_rate"],
                          flat_fraction=0.84, starved_arms=["cma"],
                          genome_dims=[4, 5], best_fitness=25.04)
        txt = d.render()
        self.assertIn("holdout", txt.lower())
        self.assertIn("learning_rate", txt)
        self.assertIn("cma", txt)

    def test_digest_from_analysis_artifacts(self):
        d = FailureDigest.from_analysis(
            peak={"holdout_failures": 4, "pinned_genes": ["learning_rate"], "flat_fraction": 0.84},
            allocator_report={"cma": {"weight": 0.01}, "ga": {"weight": 0.5}},
            top_rows=[{"fitness": 25.0, "dimension": 4}])
        self.assertEqual(d.holdout_failures, 4)
        self.assertIn("cma", d.starved_arms)
        self.assertEqual(d.genome_dims, [4])

    def test_valid_proposal_accepted(self):
        payload = json.dumps([{"op": "add_gene", "gene": "use_schema_features",
                               "reason": "flat trajectories need a state change"}])
        c = Chorus(budget=4, provider=_StubProvider(payload), cache=False)
        out = c.propose(FailureDigest(), self.genome, self.pool)
        self.assertEqual(len(out), 1)
        child, op, reason = out[0]
        self.assertEqual(op, "add_gene")
        ok, why = viability(child)
        self.assertTrue(ok, why)
        self.assertGreater(child.dimension, self.genome.dimension)

    def test_hallucinated_gene_rejected(self):
        payload = json.dumps([{"op": "add_gene", "gene": "not_a_real_gene"}])
        c = Chorus(budget=4, provider=_StubProvider(payload), cache=False)
        self.assertEqual(c.propose(FailureDigest(), self.genome, self.pool), [])
        self.assertGreater(c.rejected, 0)
        self.assertIn("unknown_pool_gene", c.report()["rejection_reasons"])

    def test_hallucinated_kwarg_rejected(self):
        payload = json.dumps([{"op": "rewire_gene", "gene": "learning_rate",
                               "value": "secret_hidden_option"}])
        c = Chorus(budget=4, provider=_StubProvider(payload), cache=False)
        self.assertEqual(c.propose(FailureDigest(), self.genome, self.pool), [])
        self.assertIn("invalid_kwarg", c.report()["rejection_reasons"])

    def test_non_gene_kwarg_rewire_rejected(self):
        """Rewiring onto n_episodes would create fake selection pressure."""
        payload = json.dumps([{"op": "rewire_gene", "gene": "epsilon", "value": "n_episodes"}])
        c = Chorus(budget=4, provider=_StubProvider(payload), cache=False)
        self.assertEqual(c.propose(FailureDigest(), self.genome, self.pool), [])

    def test_provider_exception_fails_open(self):
        c = Chorus(budget=4, provider=_StubProvider("", raises=True), cache=False)
        self.assertEqual(c.propose(FailureDigest(), self.genome, self.pool), [])
        self.assertTrue(c.report()["calls"] >= 1)

    def test_budget_is_enforced(self):
        payload = json.dumps([{"op": "nest", "gene": "learning_rate"}])
        c = Chorus(budget=2, provider=_StubProvider(payload), cache=False)
        c.propose(FailureDigest(), self.genome, self.pool)
        c.propose(FailureDigest(), self.genome, self.pool)
        self.assertTrue(c.exhausted)
        self.assertEqual(c.propose(FailureDigest(), self.genome, self.pool), [])
        self.assertEqual(c.report()["calls"], 2)

    def test_none_genome_returns_empty(self):
        c = Chorus(budget=4, provider=_StubProvider("[]"), cache=False)
        self.assertEqual(c.propose(FailureDigest(), None, self.pool), [])

    def test_duplicate_proposals_deduped(self):
        payload = json.dumps([{"op": "nest", "gene": "learning_rate"},
                              {"op": "nest", "gene": "learning_rate"}])
        c = Chorus(budget=4, provider=_StubProvider(payload), cache=False)
        out = c.propose(FailureDigest(), self.genome, self.pool, max_ops=4)
        self.assertEqual(len(out), 1)

    def test_unknown_op_rejected(self):
        payload = json.dumps([{"op": "delete_everything", "gene": "epsilon"}])
        c = Chorus(budget=4, provider=_StubProvider(payload), cache=False)
        self.assertEqual(c.propose(FailureDigest(), self.genome, self.pool), [])
        self.assertIn("unknown_op", c.report()["rejection_reasons"])

    def test_no_provider_configured_fails_open(self):
        """No API key at all: still must not raise."""
        c = Chorus(budget=1, provider=None, cache=False)
        c._provider_ready = True
        c._provider = None
        self.assertEqual(c.propose(FailureDigest(), self.genome, self.pool), [])


# ══════════════════════════════════════════════════════════════════════
# Allocation (Circulation)
# ══════════════════════════════════════════════════════════════════════

class TestCirculationArms(unittest.TestCase):

    def test_proteus_arm_set_includes_structural_operators(self):
        for a in ("ga", "cma", "de", "tpe", "morph", "arena", "chorus"):
            self.assertIn(a, PROTEUS_ARMS)

    def test_holdout_pass_outweighs_snapshot_win(self):
        s_pass, w_pass = reward_for_outcome("holdout_pass")
        s_beat, w_beat = reward_for_outcome("beat_baseline")
        self.assertTrue(s_pass and s_beat)
        self.assertGreater(w_pass, w_beat)

    def test_holdout_failure_counts_against(self):
        success, weight = reward_for_outcome("holdout_fail")
        self.assertFalse(success)
        self.assertGreater(weight, 0.0)

    def test_unknown_outcome_fails_closed(self):
        success, weight = reward_for_outcome("something_new")
        self.assertFalse(success)
        self.assertEqual(weight, 1.0)

    def test_floor_prevents_starvation(self):
        """E10: the fixed 2x gate starved the second arm 50/50."""
        alloc = ThompsonAllocator(list(PROTEUS_ARMS), floor=0.02, rng=random.Random(0))
        for _ in range(40):
            alloc.update("ga", success=True, weight=1.0)
            alloc.update("arena", success=False, weight=1.0)
        budget = alloc.allocation(100)
        self.assertEqual(sum(budget.values()), 100)
        self.assertGreater(budget["arena"], 0, "floor must keep every arm alive")

    def test_structural_arm_can_earn_budget(self):
        alloc = ThompsonAllocator(list(PROTEUS_ARMS), floor=0.01, rng=random.Random(1))
        for _ in range(30):
            alloc.update("morph", success=True, weight=1.0)
            alloc.update("ga", success=False, weight=1.0)
        self.assertGreater(alloc.weights()["morph"], alloc.weights()["ga"])
        self.assertIn("morph", alloc.choose(3))

    def test_persistence_roundtrip(self):
        alloc = ThompsonAllocator(list(PROTEUS_ARMS), rng=random.Random(0))
        alloc.update("chorus", success=True, weight=0.5)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            alloc.save(p)
            back = ThompsonAllocator.load(p)
            self.assertEqual(back.report()["chorus"]["wins"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
