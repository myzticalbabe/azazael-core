# coding: utf-8
"""Tests for PROTEUS Phase D/E hooks (cranium, circulation, morph rotation)."""

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

from CORE.WORMS.expansion.proteus_hooks import (
    cranium_decide, circulation_decide, circulation_update, row_payload,
    MORPH_CANDIDATES, MORPH_TRAINER_DEFAULTS, morph_candidate_gene,
    morph_effective, morph_gene_specs, morph_is_inert,
    morph_trainer_kwargs, morph_record, morph_dimension_summary, SEARCH_ARMS,
    cranium_override, morph_ab_delta, morph_ab_plan, morph_treatment_value,
)
from CORE.WORMS.expansion.allocation import PROTEUS_ARMS


def base_sample(bounds):
    lo_hi = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5),
             "discount": (0.5, 0.999), "epsilon_decay": (0.0, 0.3)}
    return {k: (v[0] + v[1]) / 2.0 for k, v in lo_hi.items()}


def _row(pool_id, margin, fitness="v3-dual-robust", baseline=None, best=None):
    d = {"pool_id": pool_id, "margin": margin, "fitness": fitness}
    if baseline is not None:
        d["baseline"] = baseline
    if best is not None:
        d["best_total"] = best
    return {"data": d}


class TestCranium(unittest.TestCase):

    def test_pools_across_eras_instead_of_resetting(self):
        """E4: 28 eras with ONE observation each could never accumulate."""
        rows = [_row("era-%d" % i, 3.0) for i in range(28)]
        out = cranium_decide(rows, "era-28", 2.9, threshold=0.75)
        # all 28 prior eras PLUS the current one
        self.assertEqual(out["n_eras"], 29)
        self.assertEqual(out["n_obs"], 29)
        self.assertEqual(out["decision"], "promote")

    def test_margins_below_threshold_do_not_promote(self):
        rows = [_row("era-%d" % i, 0.05) for i in range(10)]
        out = cranium_decide(rows, "era-10", 0.02, threshold=0.75)
        self.assertNotEqual(out["decision"], "promote")
        self.assertLess(out["p_above_threshold"], 0.5)

    def test_rejects_on_strong_evidence_against(self):
        rows = [_row("era-%d" % i, -2.0) for i in range(12)]
        out = cranium_decide(rows, "era-12", -2.1, threshold=0.75)
        self.assertEqual(out["decision"], "reject")

    def test_single_observation_cannot_promote(self):
        out = cranium_decide([], "era-0", 5.0, threshold=0.75)
        self.assertEqual(out["decision"], "continue")
        self.assertEqual(out["n_obs"], 1)

    def test_ignores_non_v3_rows(self):
        rows = [_row("era-0", 9.0, fitness="v2-single"),
                _row("era-1", 9.0, fitness="legacy")]
        out = cranium_decide(rows, "era-2", 0.1, threshold=0.75)
        self.assertEqual(out["n_obs"], 1, "non-v3 rows must not enter the evidence")

    def test_derives_margin_when_key_absent(self):
        rows = [{"data": {"pool_id": "e0", "fitness": "v3", "best_total": 26.0,
                          "baseline": 24.0}}]
        out = cranium_decide(rows, "e1", 2.0, threshold=0.75)
        self.assertEqual(out["n_obs"], 2)

    def test_malformed_rows_are_skipped(self):
        rows = [{"data": "not json"}, {"nope": 1}, None, _row("e0", 3.0)]
        out = cranium_decide(rows, "e1", 3.0, threshold=0.75)
        self.assertEqual(out["n_obs"], 2)

    def test_reports_posterior_components(self):
        rows = [_row("a", 3.0), _row("a", 3.2), _row("b", 2.6)]
        out = cranium_decide(rows, "c", 3.1, threshold=0.75)
        for k in ("posterior_mean", "posterior_sd", "tau", "sigma", "n_eras"):
            self.assertIn(k, out)
        self.assertGreater(out["posterior_mean"], 2.0)

    def test_non_finite_margin_is_ignored(self):
        rows = [{"data": {"pool_id": "e0", "fitness": "v3", "margin": float("nan")}}]
        out = cranium_decide(rows, "e1", 1.0, threshold=0.75)
        self.assertEqual(out["n_obs"], 1)

    def test_row_payload_variants(self):
        self.assertEqual(row_payload({"data": {"a": 1}}), {"a": 1})
        self.assertEqual(row_payload({"a": 1}), {"a": 1})
        self.assertEqual(row_payload({"data": '{"a": 1}'}), {"a": 1})
        self.assertIsNone(row_payload({"data": "not json"}))
        self.assertIsNone(row_payload("string"))


class TestCraniumDuplicateCollapse(unittest.TestCase):
    """A repeated identical margin is one evaluation, not new evidence.

    Live store, 2026-09-12: one era held 58 rows with margin 3.295 on every
    one. Counting them independently shrinks the posterior sd and inflates
    `p_above` toward certainty, so a re-evaluation manufactured significance.
    """

    def test_identical_margins_collapse_to_one_observation(self):
        rows = [{"fitness": "v3", "pool_id": "e1", "margin": 3.295}
                for _ in range(58)]
        dec = cranium_decide(rows, "e1", 3.295, threshold=0.75)
        self.assertEqual(dec["n_obs"], 1, "58 repeats are ONE evaluation")
        self.assertEqual(dec["rows_read"], 58, "raw row count stays visible")
        self.assertEqual(dec["duplicate_rows"], 57)

    def test_duplicate_only_evidence_cannot_promote(self):
        """30 identical margins must not manufacture a promotion.

        Before the collapse this read as n=30 of consistent positive evidence;
        it is one evaluation re-recorded, and one observation can never clear
        PROMOTE_MIN_OBS-equivalent confidence.
        """
        rows = [{"fitness": "v3", "pool_id": "e1", "margin": 3.295}
                for _ in range(30)]
        dec = cranium_decide(rows, "e1", 3.295, threshold=0.75)
        self.assertEqual(dec["n_obs"], 1)
        self.assertNotEqual(dec["decision"], "promote")

    def test_cycles_own_margin_is_not_double_counted(self):
        rows = [{"fitness": "v3", "pool_id": "e1", "margin": 1.25}]
        dec = cranium_decide(rows, "e1", 1.25, threshold=0.75)
        self.assertEqual(dec["n_obs"], 1)

    def test_distinct_margins_are_all_kept(self):
        rows = [{"fitness": "v3", "pool_id": "e1", "margin": m}
                for m in (1.0, 1.1, 1.2, 1.3)]
        dec = cranium_decide(rows, "e2", 0.5, threshold=0.75)
        self.assertEqual(dec["n_obs"], 5)  # 4 rows + the cycle's own
        self.assertEqual(dec["duplicate_rows"], 0)

    def test_non_v3_rows_are_still_ignored(self):
        rows = [{"fitness": "v2", "pool_id": "e1", "margin": 9.0}]
        dec = cranium_decide(rows, "e1", 0.1, threshold=0.75)
        self.assertEqual(dec["n_obs"], 1)


class TestCraniumOverride(unittest.TestCase):
    """The Phase D policy in isolation.

    These exist because the inline version was unreachable: it sat behind a
    `beats` guard that 25/25 live cycles on 2026-09-12 failed, so neither
    branch ever executed in production. A policy that cannot be reached by the
    regime it is meant for must at least be reachable by a test.
    """

    def test_rescue_promotes_a_stalled_sprt(self):
        out = cranium_override("continue", {"decision": "promote"})
        self.assertEqual(out["action"], "rescue")
        self.assertEqual(out["decision"], "promote")
        self.assertTrue(out["sprt_ok"])

    def test_rescue_from_not_run(self):
        out = cranium_override("not_run", {"decision": "promote"})
        self.assertEqual(out["action"], "rescue")
        self.assertTrue(out["sprt_ok"])

    def test_veto_withdraws_a_promote_to_continue_not_reject(self):
        """Withdrawn to 'continue': the SPRT itself rejected nothing, and
        recording a rejection would overstate how decisive it was."""
        out = cranium_override("promote", {"decision": "reject"})
        self.assertEqual(out["action"], "veto")
        self.assertEqual(out["decision"], "continue")
        self.assertFalse(out["sprt_ok"])

    def test_veto_is_not_recorded_as_a_rejection(self):
        out = cranium_override("promote", {"decision": "reject"})
        self.assertNotEqual(out["decision"], "reject")

    def test_agreement_leaves_sprt_untouched(self):
        for sprt in ("promote", "continue", "reject", "inconclusive"):
            for cd in ("promote", "continue", "reject"):
                out = cranium_override(sprt, {"decision": cd})
                if (cd == "promote" and sprt != "promote") or \
                        (cd == "reject" and sprt == "promote"):
                    continue
                self.assertEqual(out["decision"], sprt)
                self.assertEqual(out["action"], "agree")

    def test_malformed_verdict_is_noop(self):
        for bad in ({}, None, {"decision": None}, {"error": "boom"},
                    {"decision": "nonsense"}):
            out = cranium_override("promote", bad)
            self.assertEqual(out["action"], "noop")
            self.assertEqual(out["decision"], "promote")
            self.assertTrue(out["sprt_ok"])

    def test_noop_never_upgrades_a_non_promote(self):
        out = cranium_override("continue", {"error": "boom"})
        self.assertFalse(out["sprt_ok"])
        self.assertEqual(out["action"], "noop")

    def test_end_to_end_decide_then_override_on_real_shaped_rows(self):
        """cranium_decide -> cranium_override, the exact production pipeline.

        Multiple eras on purpose: these fixtures are cross-era shapes, and a
        single-era fixture would exercise the deferral path instead of the
        rescue the test is about.
        """
        rows = []
        for i, base in enumerate((1.45, 1.30, 1.55, 1.35, 1.50, 1.40)):
            rows += [{"fitness": "v3", "pool_id": "era-%d" % i,
                      "margin": base + 0.02 * j} for j in range(3)]
        dec = cranium_decide(rows, "era-6", 1.42, threshold=0.75)
        out = cranium_override("continue", dec)
        self.assertEqual(out["action"], "rescue")
        self.assertGreater(dec["p_above_threshold"], 0.75)
        self.assertGreaterEqual(dec["n_eras"], 2)

        # Strong negative evidence across many eras: veto.
        neg = []
        for i, base in enumerate((-2.5, -2.4, -2.6, -2.45, -2.55, -2.35)):
            neg += [{"fitness": "v3", "pool_id": "era-B%d" % i,
                     "margin": base - 0.02 * j} for j in range(3)]
        dec2 = cranium_decide(neg, "era-B6", -2.5, threshold=0.75)
        out2 = cranium_override("promote", dec2)
        self.assertEqual(out2["action"], "veto")

    def test_single_era_defers_to_the_sprt(self):
        """No pooling happened, so Cranium must not restate the SPRT.

        One era with two observations reported p_above = 1.0000 and proposed a
        promotion from two numbers.
        """
        rows = [{"fitness": "v3", "pool_id": "e0", "margin": 3.0 + 0.01 * i}
                for i in range(3)]
        dec = cranium_decide(rows, "e0", 3.0, threshold=0.75)
        self.assertEqual(dec["n_eras"], 1)
        self.assertEqual(dec["decision"], "continue")
        self.assertIsNotNone(dec["deferred"])
        self.assertIn("single era", dec["deferred"])

    def test_boundary_sd_is_not_read_as_certainty(self):
        """Identical era means pin the variance estimate at its boundary.

        Measured sd was 5.8e-07 on a 0.75 threshold - ~1.3M sigma of clearance
        - which would report p_above = 1.0 and promote. The decision must
        refuse, and must SAY it refused.
        """
        rows = [{"fitness": "v3", "pool_id": "e%d" % i, "margin": 3.0}
                for i in range(3)]
        dec = cranium_decide(rows, "e9", 3.0, threshold=0.75)
        self.assertEqual(dec["decision"], "continue")
        self.assertLess(dec["raw_posterior_sd"], dec["sd_floor"])
        self.assertIsNotNone(dec["deferred"])

    def test_spread_across_eras_still_decides(self):
        """The guards must not neuter the organ on its intended shape."""
        rows = [{"fitness": "v3", "pool_id": "e%d" % i,
                 "margin": 2.5 + 0.05 * i} for i in range(6)]
        dec = cranium_decide(rows, "e9", 2.6, threshold=0.75)
        self.assertEqual(dec["decision"], "promote")
        self.assertIsNone(dec["deferred"])


class TestCirculation(unittest.TestCase):

    def test_decide_returns_a_funded_arm(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            out = circulation_decide(p)
            self.assertIn("arm", out)
            self.assertIsInstance(out["run_secondary"], bool)
            self.assertTrue(out["weights"])

    def test_update_persists_and_shifts_weight(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            for _ in range(20):
                r = circulation_update(p, "cma", "holdout_pass")
                self.assertTrue(r["updated"])
            self.assertTrue(os.path.exists(p))
            out = circulation_decide(p)
            self.assertGreater(out["weights"]["cma"], out["weights"]["ga"])

    def test_unknown_outcome_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            r = circulation_update(p, "ga", "some_new_outcome_string")
            self.assertFalse(r["success"])
            self.assertEqual(r["weight"], 1.0)

    def test_floor_keeps_starved_arm_alive(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            for _ in range(30):
                circulation_update(p, "ga", "holdout_pass")
                circulation_update(p, "de", "holdout_fail")
            out = circulation_decide(p)
            self.assertGreater(out["weights"]["de"], 0.0)

    def test_default_decision_set_is_only_fundable_arms(self):
        """Regression for the 2026-09-12 mislabelling bug.

        The allocator originally chose from the full plan-level PROTEUS_ARMS
        (7 organs). It drew `arena`/`morph`/`chorus`, the cycle ran the
        slot-alternating `cma` arm anyway, and the feedback credited `cma` —
        updating the posterior of an arm that was never selected. A decision
        set wider than the executable set cannot help but mislabel outcomes.
        """
        self.assertEqual(set(SEARCH_ARMS), {"ga", "cma", "de"})
        # The plan-level list is wider; the decision set must be narrower.
        self.assertTrue(set(SEARCH_ARMS) <= set(PROTEUS_ARMS))
        self.assertLess(len(SEARCH_ARMS), len(PROTEUS_ARMS))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            for _ in range(40):
                out = circulation_decide(p)
                self.assertIn(out["arm"], SEARCH_ARMS,
                              "allocator funded %r, which no arm implements"
                              % out["arm"])
                self.assertEqual(set(out["weights"]), set(SEARCH_ARMS))

    def test_decision_reports_whether_a_secondary_runs(self):
        """`run_secondary` must be False exactly when the primary is funded."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            seen = set()
            for _ in range(60):
                out = circulation_decide(p)
                seen.add(out["arm"])
                self.assertEqual(out["run_secondary"], out["arm"] != "ga")
            self.assertTrue(seen)

    def test_corrupt_state_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            out = circulation_decide(p)
            self.assertIn("arm", out)

    def test_new_arm_is_registered_on_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "alloc.json")
            circulation_update(p, "ga", "promoted", arms=["ga"])
            out = circulation_decide(p, arms=["ga", "chorus"])
            self.assertIn("chorus", out["weights"])


class TestMorphRotation(unittest.TestCase):

    def test_round_robin_is_deterministic_and_fair(self):
        n = len(MORPH_CANDIDATES)
        picks = [morph_candidate_gene(i)["name"] for i in range(n * 3)]
        # each candidate appears exactly 3 times across 3n cycles
        for name, *_ in MORPH_CANDIDATES:
            self.assertEqual(picks.count(name), 3)
        self.assertEqual(picks[0], morph_candidate_gene(0)["name"])
        self.assertEqual(picks[n], picks[0])

    def test_gene_specs_include_base_bounds_and_bool(self):
        base = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5)}
        specs, gene = morph_gene_specs(0, base)
        self.assertIn("learning_rate", specs)
        self.assertIn(gene["name"], specs)
        # "type" is the key EvolutionEngine reads (see the round-trip test).
        self.assertEqual(specs[gene["name"]]["type"], "bool")
        self.assertEqual(specs["learning_rate"]["type"], "float")

    def test_morph_specs_round_trip_through_engine(self):
        """Regression: the spec dict must be READ by the real engine.

        The hooks originally emitted `"kind"` while the engine reads `"type"`.
        Every isolation test still passed; the gene silently became a
        CONTINUOUS coordinate. This test drives the actual consumer, so the
        key name cannot drift again.
        """
        from CORE.WORMS.expansion.evolution import EvolutionEngine
        base = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5),
                "discount": (0.5, 0.999), "epsilon_decay": (0.0, 0.3)}
        specs, gene = morph_gene_specs(0, base)
        evo = EvolutionEngine(population_size=24)
        evo.initialize_population(dict(base), gene_specs=specs)
        self.assertTrue(evo._population, "engine produced no population")
        for g in evo._population:
            self.assertIn(gene["name"], g.genes,
                          "the added gene never entered the population")
            self.assertIsInstance(g.genes[gene["name"]], bool,
                                  "bool gene sampled as %r, not a switch"
                                  % type(g.genes[gene["name"]]).__name__)
            self.assertIn("learning_rate", g.genes)
        self.assertEqual(evo._gene_specs[gene["name"]]["type"], "bool")

    def test_morph_strip_removes_every_candidate_gene(self):
        from CORE.WORMS.expansion.proteus_hooks import morph_strip
        g = {"learning_rate": 0.1, "epsilon": 0.2, "discount": 0.9,
             "epsilon_decay": 0.01, "use_schema_features": True,
             "by_session": True}
        out = morph_strip(g)
        self.assertEqual(set(out), {"learning_rate", "epsilon", "discount",
                                    "epsilon_decay"})

    def test_stripped_seed_does_not_accumulate_the_next_gene(self):
        """Regression: dimension crept 4 -> 5 -> 6 and never came back.

        Measured on the live artifact: a row with dimension 6 carrying BOTH
        `use_schema_features` and `by_session`, because the previous cycle's
        winner became the next cycle's seed and the engine never prunes keys.

        The rotation is now single-candidate (`by_session`), so the leaky case
        is a seed carried over from an older cycle that still holds the RETIRED
        `use_schema_features`. Stripping must remove that too -- a seed is
        stripped against the registry, not against the current rotation.
        """
        from CORE.WORMS.expansion.proteus_hooks import morph_strip
        from CORE.WORMS.expansion.evolution import EvolutionEngine
        base = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5),
                "discount": (0.5, 0.999), "epsilon_decay": (0.0, 0.3)}

        # A stale seed left by a cycle that trialled BOTH genes.
        stale = dict(base_sample(base))
        stale["use_schema_features"] = False
        stale["by_session"] = True

        # The current cycle adds `by_session` again. Unstripped, the seed keeps
        # the retired gene alongside the current one -> dimension 6.
        specs_next, gene_next = morph_gene_specs(0, base)
        self.assertEqual(gene_next["name"], "by_session")

        evo_bad = EvolutionEngine(population_size=24)
        evo_bad.initialize_population(base, seed_genomes=[dict(stale)],
                                      gene_specs=specs_next)
        bad_dims = {len(g.genes) for g in evo_bad._population}
        self.assertIn(6, bad_dims, "the leak should be reproducible")

        evo_ok = EvolutionEngine(population_size=24)
        evo_ok.initialize_population(base, seed_genomes=[morph_strip(stale)],
                                     gene_specs=specs_next)
        ok_dims = {len(g.genes) for g in evo_ok._population}
        self.assertEqual(ok_dims, {5}, "stripping must hold the dimension at 4+1")

    def test_morph_bool_gene_is_consumed_by_trainer_kwargs(self):
        """End to end: engine-sampled gene -> real trainer kwargs."""
        base = {"learning_rate": (0.01, 0.9)}
        specs, gene = morph_gene_specs(1, base)
        drawn = {"learning_rate": 0.2, gene["name"]: True}
        kw = morph_trainer_kwargs(drawn)
        self.assertEqual(kw, {gene["kwargs"]: True})
        self.assertEqual(specs[gene["name"]]["type"], "bool")

    def test_gene_specs_disabled_adds_nothing(self):
        base = {"learning_rate": (0.01, 0.9)}
        specs, gene = morph_gene_specs(0, base, enabled=False)
        self.assertIsNone(gene)
        self.assertEqual(set(specs), {"learning_rate"})

    def test_trainer_kwargs_forward_extra_gene(self):
        """THE point of Phase E: the added gene must reach the trainer."""
        name = MORPH_CANDIDATES[0][0]
        kw = morph_trainer_kwargs({name: 1})
        self.assertEqual(kw, {name: True})
        kw = morph_trainer_kwargs({name: 0})
        self.assertEqual(kw, {name: False})

    def test_trainer_kwargs_ignores_unknown_and_missing(self):
        self.assertEqual(morph_trainer_kwargs({"learning_rate": 0.5}), {})
        self.assertEqual(morph_trainer_kwargs({}), {})
        self.assertEqual(morph_trainer_kwargs(None), {})

    def test_trainer_kwargs_thresholds_continuous_bool_at_half(self):
        """The secondary arms hand the switch in as a (0.0, 1.0) coordinate.

        `bool(0.3)` is True, so without thresholding almost every CMA/DE
        candidate would have read as "switch on" and the relaxed search would
        not have agreed with the GA's discrete switch.
        """
        name = MORPH_CANDIDATES[0][0]
        self.assertEqual(morph_trainer_kwargs({name: 0.0}), {name: False})
        self.assertEqual(morph_trainer_kwargs({name: 0.3}), {name: False})
        self.assertEqual(morph_trainer_kwargs({name: 0.49}), {name: False})
        self.assertEqual(morph_trainer_kwargs({name: 0.5}), {name: True})
        self.assertEqual(morph_trainer_kwargs({name: 0.9}), {name: True})
        self.assertEqual(morph_trainer_kwargs({name: True}), {name: True})
        self.assertEqual(morph_trainer_kwargs({name: False}), {name: False})

    def test_arm_bounds_carry_the_extra_gene(self):
        """Regression: a secondary-arm win dropped the extra dimension.

        `_run_cma_arm` used to build its own optimiser from the 4-key
        GENE_BOUNDS, so an arm winner had NO extra gene (measured: cycle 8,
        dimension 4, extra {}). The cycle now passes 4 + 1 bounds instead.
        """
        base = {"learning_rate": (0.01, 0.9), "epsilon": (0.0, 0.5),
                "discount": (0.5, 0.999), "epsilon_decay": (0.0, 0.3)}
        specs, gene = morph_gene_specs(0, base)
        arm_bounds = dict(base)
        arm_bounds[gene["name"]] = (0.0, 1.0)
        from CORE.WORMS.expansion.evolution import SepCMAES
        opt = SepCMAES({k: tuple(v) for k, v in arm_bounds.items()})
        self.assertIn(gene["name"], opt.names)
        self.assertEqual(len(opt.names), 5)
        self.assertEqual(opt.bounds[gene["name"]], (0.0, 1.0))

    def test_trainer_kwargs_coerces_string_bool(self):
        name = MORPH_CANDIDATES[0][0]
        self.assertEqual(morph_trainer_kwargs({name: "true"}), {name: True})
        self.assertEqual(morph_trainer_kwargs({name: "0"}), {name: False})


class TestMorphAccounting(unittest.TestCase):

    def test_summary_reports_verdict_per_gene(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            # Each gene is moved OFF its trainer default, so both rows are
            # EFFECTIVE trials -- an inert row is not scored at all. One gene
            # is retired from the rotation but still known, which is the point:
            # the verdict reads the registry, not the current rotation.
            g0 = "use_schema_features"   # default False -> True is a treatment
            g1 = "by_session"            # default True  -> False is a treatment
            morph_record(p, 0, {"name": None}, {"a": 1, "b": 2, "c": 3, "d": 4}, 25.0)
            morph_record(p, 1, {"name": g0}, {"a": 1, "b": 2, "c": 3, "d": 4, g0: True}, 25.5)
            morph_record(p, 2, {"name": g1}, {"a": 1, "b": 2, "c": 3, "d": 4, g1: False}, 24.0)
            out = morph_dimension_summary(p)
            self.assertEqual(out["baseline_4gene_best"], 25.0)
            self.assertTrue(out["verdict"][g0]["beats_4gene"])
            self.assertFalse(out["verdict"][g1]["beats_4gene"])
            self.assertAlmostEqual(out["verdict"][g0]["delta_vs_4gene"], 0.5)

    def test_records_dimension_alongside_fitness(self):
        """The plan's honest-risk mitigation: fitness is only comparable
        within a dimension."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            morph_record(p, 0, None, {"a": 1, "b": 2, "c": 3, "d": 4}, 25.0)
            morph_record(p, 1, {"name": "x"}, {"a": 1, "b": 2, "c": 3, "d": 4, "x": True}, 26.0)
            with open(p, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(rows[0]["dimension"], 4)
            self.assertEqual(rows[1]["dimension"], 5)

    def test_summary_handles_missing_file(self):
        out = morph_dimension_summary(os.path.join(tempfile.gettempdir(), "nope_morph.jsonl"))
        self.assertEqual(out["n_rows"], 0)
        self.assertIsNone(out["baseline_4gene_best"])

    def test_record_never_raises_on_bad_input(self):
        morph_record(os.path.join(tempfile.gettempdir(), "ok_morph.jsonl"),
                     0, None, None, None)


class TestMorphInertTrials(unittest.TestCase):
    """A trial that leaves the trainer call unchanged measures the SEARCH, not
    the gene. Measured 2026-09-12: 69 rows, 52 of them inert (75%), and
    `use_schema_features` resolved OFF in 35/35 of its trials while being
    credited as a win every single time.
    """

    def test_defaults_mirror_the_trainers_own_signature(self):
        """The rule is "resolved == the trainer's default", so the default table
        is the load-bearing fact — a hand-copied one would drift silently and
        every inert verdict would be wrong in a way no test noticed."""
        import inspect
        from CORE.WORMS.expansion.learning_rl import RLTrainLoop
        sig = inspect.signature(RLTrainLoop.train_from_memory)
        live = {name: p.default for name, p in sig.parameters.items()
                if isinstance(p.default, bool)}
        for kwarg, default in MORPH_TRAINER_DEFAULTS.items():
            if not isinstance(default, bool):
                # Non-switch genes (nest scalars default None): the trainer
                # must still accept the kwarg; absence == scalar path.
                self.assertIn(kwarg, sig.parameters,
                              "%s is not a trainer kwarg any more" % kwarg)
                self.assertIsNone(sig.parameters[kwarg].default,
                                  "nest default drift: trainer says %s=%r"
                                  % (kwarg, sig.parameters[kwarg].default))
                continue
            self.assertIn(kwarg, live,
                          "%s is not a trainer kwarg any more" % kwarg)
            self.assertEqual(live[kwarg], default,
                             "MORPH_TRAINER_DEFAULTS drift: trainer says "
                             "%s=%r" % (kwarg, live[kwarg]))

    def test_gene_resolving_to_the_default_is_inert(self):
        # OFF for a default-False switch, ON for a default-True one.
        self.assertTrue(morph_is_inert("use_schema_features",
                                       {"use_schema_features": 0.056}))
        self.assertTrue(morph_is_inert("by_session", {"by_session": True}))
        self.assertTrue(morph_is_inert("by_session", {"by_session": 0.9}))

    def test_gene_that_changes_the_call_is_effective(self):
        self.assertFalse(morph_is_inert("use_schema_features",
                                        {"use_schema_features": 0.75}))
        self.assertFalse(morph_is_inert("by_session", {"by_session": False}))
        self.assertFalse(morph_is_inert("by_session", {"by_session": 0.2}))

    def test_unknown_gene_is_not_inert(self):
        """Absence of evidence is not evidence of inaction."""
        self.assertFalse(morph_is_inert("nope", {"nope": 0}))
        self.assertFalse(morph_is_inert(None, {}))
        self.assertFalse(morph_is_inert("use_schema_features", {}))

    def test_effective_reports_what_the_trainer_receives(self):
        self.assertIs(morph_effective("use_schema_features",
                                      {"use_schema_features": 0.9}), True)
        self.assertIs(morph_effective("use_schema_features",
                                      {"use_schema_features": 0.1}), False)
        self.assertIsNone(morph_effective("use_schema_features", {}))

    def test_record_stamps_the_row_with_its_own_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            morph_record(p, 0, {"name": "use_schema_features"},
                         {"use_schema_features": 0.056}, 22.4, 21.5)
            morph_record(p, 1, {"name": "by_session"},
                         {"by_session": False}, 22.6, 21.5)
            with open(p, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        self.assertTrue(rows[0]["inert"])
        self.assertEqual(rows[0]["applied"], {"use_schema_features": False})
        self.assertFalse(rows[1]["inert"])
        self.assertEqual(rows[1]["applied"], {"by_session": False})

    def test_verdict_does_not_credit_an_inert_gene(self):
        """THE regression: the old verdict read every row, so a switch the
        trainer never saw change came out as a winner."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            for i in range(4):
                morph_record(p, i, {"name": "use_schema_features"},
                             {"use_schema_features": 0.056, "other": 1.0},
                             22.0 + i, 21.5)
            out = morph_dimension_summary(p)
        self.assertEqual(out["n_rows"], 4)
        self.assertEqual(out["inert_rows"], 4)
        self.assertEqual(out["effective_rows"], 0)
        self.assertEqual(out["inert_share"], 1.0)
        v = out["verdict"]["use_schema_features"]
        self.assertFalse(v["tested"])
        self.assertIsNone(v["best"])
        self.assertIsNone(v["beats_4gene"])
        self.assertIn("no informative trial", v["note"])
        self.assertEqual(v["inert_n"], 4)

    def test_verdict_uses_effective_rows_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            for i in range(3):          # inert: switch resolving to its default
                morph_record(p, i, {"name": "use_schema_features"},
                             {"use_schema_features": 0.2}, 30.0, 21.5)
            morph_record(p, 3, {"name": "use_schema_features"},
                         {"use_schema_features": 0.9}, 22.0, 21.5)
            out = morph_dimension_summary(p)
        self.assertEqual(out["inert_rows"], 3)
        self.assertEqual(out["effective_rows"], 1)
        self.assertEqual(out["by_gene"]["use_schema_features"]["n"], 1)
        self.assertEqual(out["verdict"]["use_schema_features"]["best"], 22.0)

    def test_legacy_rows_without_the_flag_are_reclassified(self):
        """Rows written before the fix carry no verdict, so the correction has
        to be re-derived on read or the history stays wrong forever."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "cycle_index": 0, "gene": "use_schema_features",
                    "dimension": 5, "fitness": 22.37, "baseline_fitness": 21.46,
                    "extra": {"use_schema_features": 0.0563}}) + "\n")
            out = morph_dimension_summary(p)
        self.assertEqual(out["inert_rows"], 1)
        self.assertFalse(out["verdict"]["use_schema_features"]["tested"])


class TestMorphMatchedAB(unittest.TestCase):
    """The trial's own comparison is confounded; the A/B is not.

    `fitness - baseline_fitness` compares a 5-gene champion against the 4-gene
    PRODUCTION baseline, so it cannot separate the added gene from the search
    that produced the champion -- and 52 of 69 rows resolved the gene to the
    trainer's default, so the trial never moved the thing it was testing. The
    A/B holds the champion and the pools fixed and moves ONE gene.
    """

    def test_treatment_value_is_the_negation_of_the_default(self):
        # per-gene, because the defaults are not all False
        self.assertIs(morph_treatment_value("use_schema_features"), True)
        self.assertIs(morph_treatment_value("by_session"), False)
        self.assertIsNone(morph_treatment_value("not_a_gene"))
        self.assertIsNone(morph_treatment_value(None))

    def test_ab_plan_pairs_the_default_against_the_treatment(self):
        plan = morph_ab_plan("by_session", {"by_session": True})
        self.assertEqual(plan["control"], True)          # the trainer's default
        self.assertEqual(plan["treatment"], False)       # the value that MOVES it
        self.assertIs(plan["default"], True)

    def test_ab_plan_needs_the_gene_to_be_in_the_genome(self):
        self.assertIsNone(morph_ab_plan("by_session", {"learning_rate": 0.1}))
        self.assertIsNone(morph_ab_plan("mystery", {"mystery": 1}))
        self.assertIsNotNone(morph_ab_plan("by_session"))

    def test_ab_delta_sign_convention(self):
        """POSITIVE means the treatment beat the trainer's default."""
        self.assertEqual(morph_ab_delta(22.0, 22.4), 0.4)
        self.assertEqual(morph_ab_delta(22.4, 22.0), -0.4)
        self.assertIsNone(morph_ab_delta(None, 22.0))
        self.assertIsNone(morph_ab_delta(22.0, None))
        self.assertIsNone(morph_ab_delta("x", 22.0))
        self.assertIsNone(morph_ab_delta(float("nan"), 22.0))

    def test_record_stores_the_measurement(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            morph_record(p, 0, {"name": "by_session"}, {"by_session": True},
                         22.4, 21.5,
                         ab={"gene": "by_session", "control": True,
                             "treatment": False, "control_fitness": 22.4,
                             "treatment_fitness": 22.1})
            with open(p, encoding="utf-8") as fh:
                row = json.loads(fh.readline())
        self.assertEqual(row["ab"]["gene"], "by_session")
        self.assertEqual(row["ab"]["treatment"], False)

    def test_summary_reports_the_attributable_effect(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            for i, delta in enumerate((0.4, 0.2, -0.1)):
                morph_record(p, i, {"name": "by_session"}, {"by_session": True},
                             22.4, 21.5,
                             ab={"gene": "by_session", "control": True,
                                 "treatment": False, "control_fitness": 22.4,
                                 "treatment_fitness": 22.4 + delta})
            out = morph_dimension_summary(p)
        self.assertEqual(out["measured_rows"], 3)
        v = out["verdict"]["by_session"]
        self.assertEqual(v["ab_n"], 3)
        self.assertAlmostEqual(v["ab_mean"], 0.1667, places=4)
        self.assertEqual(v["ab_positive"], 2)
        self.assertTrue(v["ab_beats_default"])          # mean > 0
        self.assertEqual(v["inert_n"], 3, "all three still inert vs the trainer's default")

    def test_rows_without_an_ab_are_not_measured(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            morph_record(p, 0, {"name": "by_session"}, {"by_session": True},
                         22.4, 21.5)
            out = morph_dimension_summary(p)
        self.assertEqual(out["measured_rows"], 0)
        self.assertEqual(out["verdict"]["by_session"]["ab_n"], 0)
        self.assertIsNone(out["verdict"]["by_session"]["ab_mean"])

    def test_arm_scores_alone_are_enough_to_measure(self):
        """A row that records the two arms but no summary delta still counts."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "morph.jsonl")
            morph_record(p, 0, {"name": "by_session"}, {"by_session": True},
                         22.4, 21.5,
                         ab={"gene": "by_session", "control_fitness": 22.4,
                             "treatment_fitness": 22.0})
            out = morph_dimension_summary(p)
        self.assertEqual(out["measured_rows"], 1)
        self.assertAlmostEqual(out["by_gene_ab"]["by_session"]["mean"], -0.4, places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
