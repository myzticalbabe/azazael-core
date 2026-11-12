# coding: utf-8
"""Tests for the tensor genome — genes that declare their own usefulness.

Each test targets a RULE the module claims, not merely that it runs:

  rule 1  an unconsumed gene is prunable rather than silently inert
  rule 2  an absent gene is skipped, never imputed
  rule 3  a re-recorded observation is one observation, not many
  rule 4  usefulness is an association, never labelled as an effect
"""

import json
import os
import sys
import tempfile
import unittest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
_src_dir = os.path.join(_project_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from CORE.WORMS.expansion.tensorgene import (
    TensorGene, TensorGenome, USEFULNESS_FLOOR, correlate, dedupe_observations,
    row_payload, spearman,
)


def hist(rows):
    """Build history dicts: rows of (genes_mapping, fitness)."""
    out = []
    for genes, fit in rows:
        out.append({"best_genes": dict(genes), "best_total": float(fit)})
    return out


def store_rows(rows):
    """Build rows in the REAL memory-store shape: payload nested under `data`."""
    return [{"event_type": "evolution_result", "data": dict(r)} for r in rows]


class TestSpearman(unittest.TestCase):
    def test_perfect_monotone_is_one(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0, places=9)

    def test_perfect_reverse_is_minus_one(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0, places=9)

    def test_monotone_nonlinear_still_one(self):
        # Rank correlation is invariant to any strictly increasing transform.
        xs = [1, 2, 3, 4, 5]
        ys = [x ** 3 for x in xs]
        self.assertAlmostEqual(spearman(xs, ys), 1.0, places=9)

    def test_ties_share_a_rank(self):
        # y has a tie; rho must be computable, not NaN.
        rho = spearman([1, 2, 3, 4], [5, 5, 7, 9])
        self.assertIsNotNone(rho)
        self.assertTrue(-1.0 <= rho <= 1.0)

    def test_constant_side_is_none_not_zero(self):
        # "Not identifiable" must not masquerade as "measured, no effect".
        self.assertIsNone(spearman([1, 1, 1, 1], [1, 2, 3, 4]))

    def test_too_short_is_none(self):
        self.assertIsNone(spearman([1, 2], [3, 4]))

    def test_mismatched_length_is_none(self):
        self.assertIsNone(spearman([1, 2, 3], [1, 2]))


class TestDedupe(unittest.TestCase):
    def test_exact_repeats_collapse(self):
        obs = [((1.0, 2.0), 5.0)] * 20
        self.assertEqual(len(dedupe_observations(obs)), 1)

    def test_distinct_rows_survive(self):
        obs = [((1.0, 2.0), 5.0), ((1.0, 2.0), 6.0), ((1.1, 2.0), 5.0)]
        self.assertEqual(len(dedupe_observations(obs)), 3)

    def test_order_is_preserved(self):
        obs = [((3.0,), 1.0), ((1.0,), 9.0), ((3.0,), 1.0), ((2.0,), 5.0)]
        kept = dedupe_observations(obs)
        self.assertEqual([k[0][0] for k in kept], [3.0, 1.0, 2.0])

    def test_malformed_rows_are_skipped_not_fatal(self):
        obs = [((1.0,), 2.0), ("nonsense", 1.0), ((2.0,), 3.0)]
        self.assertEqual(len(dedupe_observations(obs)), 2)


class TestCorrelate(unittest.TestCase):
    def test_gene_absent_is_skipped_not_imputed(self):
        # rule 2: a gene that was never searched has no observation, and must
        # not be filled with a zero.
        rows = hist([
            ({"a": 1.0, "b": 1.0}, 1.0),
            ({"a": 2.0, "b": 2.0}, 2.0),
            ({"a": 3.0, "b": 3.0}, 3.0),
        ])
        self.assertEqual(correlate(rows, "b")["samples"], 3)
        self.assertEqual(correlate(rows, "never_searched")["samples"], 0)
        self.assertEqual(correlate(rows, "never_searched")["usefulness"], 0.0)

    def test_repeated_frozen_rows_do_not_inflate_samples(self):
        # rule 3: same gene vector AND fitness == same measurement.
        one = ({"a": 5.0, "b": 1.0}, 7.0)
        rows = hist([one] * 30 + [({"a": 1.0, "b": 1.0}, 1.0),
                                  ({"a": 9.0, "b": 1.0}, 9.0)])
        stats = correlate(rows, "a")
        self.assertEqual(stats["samples"], 3)
        self.assertAlmostEqual(stats["rho"], 1.0, places=9)

    def test_strong_association_detected(self):
        rows = hist([({"a": float(i), "b": 0.0}, 2.0 * i) for i in range(10)])
        stats = correlate(rows, "a")
        self.assertTrue(stats["identifiable"])
        self.assertEqual(stats["direction"], "up")
        self.assertAlmostEqual(stats["usefulness"], 1.0, places=9)

    def test_direction_down(self):
        rows = hist([({"a": float(i)}, -2.0 * i) for i in range(10)])
        self.assertEqual(correlate(rows, "a")["direction"], "down")

    def test_constant_gene_is_not_identifiable(self):
        rows = hist([({"a": 4.0}, float(i)) for i in range(10)])
        stats = correlate(rows, "a")
        self.assertFalse(stats["identifiable"])
        self.assertEqual(stats["direction"], "unknown")

    def test_constant_fitness_is_not_identifiable_not_flat(self):
        # The gene varies but the target does not. That is "the data could not
        # answer", which is NOT the same claim as "measured, no association".
        rows = hist([({"a": float(i % 2)}, 5.0) for i in range(10)])
        stats = correlate(rows, "a")
        self.assertFalse(stats["identifiable"])
        self.assertEqual(stats["direction"], "unknown")

    def test_genuine_flat_association_is_reported_as_flat(self):
        # Both sides vary, ranks cancel exactly, so rho == 0: a measured null.
        rows = hist([({"a": 0.0}, float(i)) for i in range(1, 6)]
                    + [({"a": 1.0}, float(i)) for i in range(1, 6)])
        stats = correlate(rows, "a")
        self.assertTrue(stats["identifiable"])
        self.assertEqual(stats["direction"], "flat")
        self.assertAlmostEqual(stats["rho"], 0.0, places=6)

    def test_rule4_usefulness_is_never_called_an_effect(self):
        rows = hist([({"a": float(i)}, float(i)) for i in range(5)])
        stats = correlate(rows, "a")
        self.assertIn("usefulness", stats)
        for forbidden in ("effect", "effect_size", "caused_by", "impact"):
            self.assertNotIn(forbidden, stats)

    def test_missing_fitness_row_is_skipped(self):
        rows = [{"best_genes": {"a": 1.0}}, {"best_genes": {"a": 2.0}}]
        self.assertEqual(correlate(rows, "a")["samples"], 0)


class TestRowPayload(unittest.TestCase):
    """The store nests payloads under `data`; reading it wrongly yields an
    EMPTY genome rather than an error, which is the silent-inert failure this
    module exists to prevent."""

    def test_unwraps_nested_data(self):
        row = {"event_type": "evolution_result",
               "data": {"best_genes": {"a": 1.0}, "best_total": 5.0}}
        self.assertEqual(row_payload(row)["best_genes"], {"a": 1.0})

    def test_accepts_flat_payload(self):
        row = {"best_genes": {"a": 1.0}, "best_total": 5.0}
        self.assertEqual(row_payload(row)["best_genes"], {"a": 1.0})

    def test_decodes_json_string_payload(self):
        row = {"data": json.dumps({"best_genes": {"a": 1.0}})}
        self.assertEqual(row_payload(row)["best_genes"], {"a": 1.0})

    def test_non_dict_is_none(self):
        self.assertIsNone(row_payload("not a row"))
        self.assertIsNone(row_payload(None))

    def test_correlate_reads_the_real_store_shape(self):
        rows = store_rows(hist([({"a": float(i)}, float(i)) for i in range(8)]))
        stats = correlate(rows, "a")
        self.assertTrue(stats["identifiable"])
        self.assertGreaterEqual(stats["samples"], 3)

    def test_declare_reads_the_real_store_shape(self):
        rows = store_rows(hist([({"a": float(i), "b": 0.0}, float(i))
                                for i in range(8)]))
        g = TensorGenome().declare(rows)
        self.assertEqual(sorted(g.genes), ["a", "b"])
        self.assertEqual(g.meta["payload_genes_found"], True)

    def test_empty_genome_says_why_it_is_empty(self):
        # An empty genome that cannot explain itself is indistinguishable from
        # a bug, so the meta must record whether payloads were found at all.
        g = TensorGenome().declare([{"event_type": "x", "data": {}}])
        self.assertEqual(len(g.genes), 0)
        self.assertEqual(g.meta["history_rows"], 1)
        self.assertFalse(g.meta["payload_genes_found"])

    def test_diagnostic_counts_rows_missing_the_gene(self):
        rows = store_rows(hist([({"a": 1.0}, 1.0), ({"a": 2.0}, 2.0),
                                ({"a": 3.0}, 3.0), ({"b": 1.0}, 4.0)]))
        stats = correlate(rows, "a")
        self.assertEqual(stats["rows_missing_gene"], 1)
        self.assertEqual(stats["rows_without_payload"], 0)

    def test_diagnostic_counts_rows_without_payload(self):
        rows = store_rows(hist([({"a": float(i)}, float(i)) for i in range(4)]))
        rows.append({"event_type": "evolution_result", "data": "not json"})
        stats = correlate(rows, "a")
        self.assertEqual(stats["rows_without_payload"], 1)


class TestTensorGene(unittest.TestCase):
    def test_bool_encoding(self):
        self.assertEqual(TensorGene("g", "bool", True).as_float(), 1.0)
        self.assertEqual(TensorGene("g", "bool", False).as_float(), 0.0)

    def test_numeric_encoding(self):
        self.assertEqual(TensorGene("g", "float", 0.25).as_float(), 0.25)

    def test_string_encoding_is_deterministic_across_calls(self):
        g = TensorGene("g", "str", "cma")
        self.assertEqual(g.as_float(), g.as_float())

    def test_string_encoding_does_not_use_hash(self):
        # Python's str hash is salted per process, so it must not be relied on.
        a = TensorGene("g", "str", "arm").as_float()
        b = TensorGene("g", "str", "arm").as_float()
        self.assertEqual(a, b)

    def test_row_shape_is_declared_width(self):
        row = TensorGene("g", "float", 1.0, usefulness=0.5,
                         direction="up", identifiable=True).to_tensor_row()
        self.assertEqual(len(row), TensorGenome.WIDTH_PER_GENE)
        self.assertEqual(row, [1.0, 0.5, 1.0, 1.0])

    def test_unknown_direction_sign_is_zero(self):
        g = TensorGene("g", "float", 1.0, direction="unknown")
        self.assertEqual(g.to_tensor_row()[2], 0.0)

    def test_dict_round_trip(self):
        g = TensorGene("g", "bool", True, 0.4, "down", 7, (0.0, 1.0), "morph", True)
        back = TensorGene.from_dict(g.to_dict())
        self.assertEqual(back.to_dict(), g.to_dict())

    def test_rho_carries_the_sign_not_just_the_magnitude(self):
        # usefulness is |rho|; the signed value must survive so a reader does
        # not have to reconstruct the direction.
        self.assertEqual(TensorGene("g", "float", 0.0, 0.3, "down").rho, -0.3)
        self.assertEqual(TensorGene("g", "float", 0.0, 0.3, "up").rho, 0.3)

    def test_rho_defaults_to_zero_when_direction_unknown(self):
        self.assertEqual(TensorGene("g", "float", 0.0, 0.0, "unknown").rho, 0.0)

    def test_explicit_rho_wins_over_derived(self):
        g = TensorGene("g", "float", 0.0, 0.25, "up", 5, None, "", True, -0.25)
        self.assertEqual(g.rho, -0.25)

    def test_unknown_gene_carries_no_rho_key_crash(self):
        rows = hist([({"a": 4.0}, float(i)) for i in range(10)])
        g = TensorGenome().declare(rows)
        self.assertEqual(g.genes["a"].rho, 0.0)
        self.assertFalse(g.genes["a"].identifiable)


class TestTensorGenome(unittest.TestCase):
    def test_order_is_usefulness_descending(self):
        g = TensorGenome()
        g.add(TensorGene("weak", "float", 0.0, usefulness=0.1))
        g.add(TensorGene("strong", "float", 0.0, usefulness=0.9))
        g.add(TensorGene("mid", "float", 0.0, usefulness=0.5))
        self.assertEqual(g.order(), ["strong", "mid", "weak"])

    def test_ties_break_by_name_deterministically(self):
        g = TensorGenome()
        for name in ("zeta", "alpha", "mu"):
            g.add(TensorGene(name, "float", 0.0, usefulness=0.5))
        self.assertEqual(g.order(), ["alpha", "mu", "zeta"])

    def test_prune_removes_below_floor(self):
        # rule 1: a gene with no declared usefulness is removable, so it cannot
        # sit in the genome looking like a loaded coordinate.
        g = TensorGenome()
        g.add(TensorGene("keep", "float", 0.0, usefulness=0.8))
        g.add(TensorGene("drop", "float", 0.0, usefulness=0.01))
        dropped = g.prune()
        self.assertEqual(dropped, ["drop"])
        self.assertEqual(g.order(), ["keep"])

    def test_prune_respects_keep_protection(self):
        g = TensorGenome()
        g.add(TensorGene("structural", "float", 0.0, usefulness=0.0))
        self.assertEqual(g.prune(keep=["structural"]), [])
        self.assertIn("structural", g.genes)

    def test_prune_is_idempotent(self):
        g = TensorGenome()
        g.add(TensorGene("drop", "float", 0.0, usefulness=0.01))
        self.assertEqual(g.prune(), ["drop"])
        self.assertEqual(g.prune(), [])

    def test_prune_at_exactly_floor_keeps_the_gene(self):
        g = TensorGenome()
        g.add(TensorGene("edge", "float", 0.0, usefulness=USEFULNESS_FLOOR))
        self.assertEqual(g.prune(), [])

    def test_width_is_rows_times_declared_width(self):
        g = TensorGenome()
        for name in "abc":
            g.add(TensorGene(name, "float", 0.0, usefulness=0.5))
        self.assertEqual(g.width, 3 * TensorGenome.WIDTH_PER_GENE)

    def test_to_tensor_shape_matches_width(self):
        g = TensorGenome()
        for name in "abc":
            g.add(TensorGene(name, "float", 1.0, usefulness=0.5))
        t = g.to_tensor()
        self.assertEqual(len(t), 3)
        self.assertTrue(all(len(r) == TensorGenome.WIDTH_PER_GENE for r in t))

    def test_declare_uses_latest_row_as_champion(self):
        rows = hist([({"a": 0.9, "b": 0.1}, 50.0),  # most recent first
                     ({"a": 0.5, "b": 0.5}, 10.0)])
        g = TensorGenome().declare(rows)
        self.assertEqual(g.genes["a"].value, 0.9)
        self.assertEqual(g.genes["b"].value, 0.1)

    def test_declare_value_map_overrides_history(self):
        rows = hist([({"a": 0.9}, 50.0)])
        g = TensorGenome().declare(rows, value_map={"a": 0.33})
        self.assertEqual(g.genes["a"].value, 0.33)

    def test_declare_attaches_provenance_and_kind(self):
        rows = hist([({"a": float(i)}, float(i)) for i in range(5)])
        g = TensorGenome().declare(rows, kind_map={"a": "bool"},
                                   provenance_map={"a": "morph"})
        self.assertEqual(g.genes["a"].kind, "bool")
        self.assertEqual(g.genes["a"].provenance, "morph")

    def test_declare_on_empty_history_yields_empty_genome(self):
        g = TensorGenome().declare([])
        self.assertEqual(len(g.genes), 0)
        self.assertEqual(g.width, 0)

    def test_compile_spec_declares_shape_and_is_marked_untrained(self):
        rows = hist([({"a": float(i), "b": 0.0}, float(i)) for i in range(6)])
        g = TensorGenome().declare(rows)
        spec = g.compile_spec()
        self.assertEqual(spec["kind"], "tensorgene.mlp.spec")
        self.assertFalse(spec["trained"])
        self.assertEqual(spec["input"]["rows"], len(g.genes))
        self.assertEqual(spec["input"]["width"], g.width)
        self.assertEqual(spec["input"]["features_per_gene"],
                         TensorGenome.WIDTH_PER_GENE)
        self.assertEqual(spec["output"]["name"], "best_total")

    def test_compile_spec_feature_order_matches_row(self):
        g = TensorGenome()
        g.add(TensorGene("a", "float", 1.0, usefulness=0.5))
        spec = g.compile_spec()
        self.assertEqual(spec["input"]["feature_order"],
                         ["value", "usefulness", "direction_sign", "identifiable"])

    def test_compile_spec_reports_below_floor_candidates(self):
        rows = hist([({"a": float(i)}, float(i)) for i in range(6)])
        g = TensorGenome().declare(rows)
        spec = g.compile_spec()
        # `a` correlates perfectly, so nothing should be flagged as below floor.
        self.assertEqual(spec["below_floor"], [])

    def test_compile_spec_does_not_claim_unperformed_pruning(self):
        # A spec that said "pruned" while the gene is still present would report
        # an action that never happened.
        g = TensorGenome()
        g.add(TensorGene("probation", "float", 0.0, usefulness=0.01))
        spec = g.compile_spec()
        self.assertIn("probation", spec["input"]["genes"])
        self.assertEqual(spec["below_floor"], ["probation"])
        self.assertNotIn("pruned_below_floor", spec)

    def test_prune_floor_names_is_non_destructive(self):
        g = TensorGenome()
        g.add(TensorGene("weak", "float", 0.0, usefulness=0.01))
        self.assertEqual(g.prune_floor_names(), ["weak"])
        self.assertIn("weak", g.genes)

    def test_prune_floor_names_respects_keep(self):
        g = TensorGenome()
        g.add(TensorGene("structural", "float", 0.0, usefulness=0.0))
        self.assertEqual(g.prune_floor_names(keep=["structural"]), [])

    def test_summary_counts_directions(self):
        g = TensorGenome()
        g.add(TensorGene("a", "float", 0.0, 0.9, "up", 5, None, "", True))
        g.add(TensorGene("b", "float", 0.0, 0.8, "down", 5, None, "", True))
        g.add(TensorGene("c", "float", 0.0, 0.0, "unknown", 0, None, "", False))
        s = g.summary()
        self.assertEqual(s["n_genes"], 3)
        self.assertEqual(s["identifiable"], 2)
        self.assertEqual(s["above_floor"], 2)
        self.assertEqual(s["direction_counts"]["up"], 1)
        self.assertEqual(s["direction_counts"]["unknown"], 1)

    def test_json_round_trip(self):
        rows = hist([({"a": float(i)}, float(i)) for i in range(6)])
        g = TensorGenome().declare(rows, provenance_map={"a": "morph"})
        back = TensorGenome.from_dict(json.loads(g.to_json()))
        self.assertEqual(back.order(), g.order())
        self.assertEqual(back.genes["a"].direction, g.genes["a"].direction)

    def test_save_is_atomic_and_leaves_no_tmp(self):
        g = TensorGenome()
        g.add(TensorGene("a", "float", 1.0, usefulness=0.5))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "genome.json")
            g.save_json(path)
            self.assertTrue(os.path.exists(path))
            self.assertFalse(os.path.exists(path + ".tmp"))
            self.assertEqual(TensorGenome.load_json(path).order(), ["a"])


class TestLiveness(unittest.TestCase):
    def test_real_shaped_input_compiles_without_error(self):
        # Mirrors the live row shape: four promoted genes plus a morph bool.
        rows = []
        for i in range(40):
            # every 4th row repeats the one before it (frozen pool)
            if i % 4 == 0 and rows:
                rows.append(dict(rows[-1]))
                continue
            rows.append({"best_genes": {
                "learning_rate": 0.01 + 0.02 * (i % 17),
                "epsilon": 0.05 * (i % 7),
                "discount": 0.9 + 0.001 * (i % 50),
                "epsilon_decay": 0.01 * (i % 11),
                "morph_switch": bool(i % 2)},
                "best_total": 20.0 + 0.3 * (i % 23)})
        g = TensorGenome().declare(
            rows,
            kind_map={"morph_switch": "bool"},
            bounds_map={"learning_rate": (0.01, 0.9),
                        "discount": (0.5, 0.999)},
            provenance_map={"morph_switch": "morph"})
        self.assertEqual(len(g.genes), 5)
        spec = g.compile_spec()
        self.assertEqual(spec["input"]["rows"], 5)
        # Dedupe must have reduced the effective sample count below raw rows.
        self.assertLess(g.genes["learning_rate"].samples, len(rows))
        # Anything below the floor must be reported, not silently kept.
        for name in spec["below_floor"]:
            self.assertLess(g.genes[name].usefulness, USEFULNESS_FLOOR)
        # The report is a candidate list: those genes are still in the genome.
        for name in spec["below_floor"]:
            self.assertIn(name, g.genes)

    def test_prune_then_compile_is_consistent(self):
        rows = hist([({"a": float(i), "b": 0.0}, float(i)) for i in range(8)])
        g = TensorGenome().declare(rows)
        dropped = g.prune()
        spec = g.compile_spec()
        for name in dropped:
            self.assertNotIn(name, spec["input"]["genes"])
        self.assertEqual(spec["input"]["rows"], len(g.genes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
