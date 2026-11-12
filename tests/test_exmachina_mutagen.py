# coding: utf-8
"""MUTAGEN — the 17th organ: did the genome GROW, or was it given away?

Why this test exists
--------------------
Phase E (Morph) appends one row per cycle to `state/proteus_morph.jsonl` with the
gene it trialled and the fitness delta it measured. That ledger is the record of
whether adding a gene helped, and until MUTAGEN existed no organ read it.

What the ledger showed on 2026-09-12 is the failure this organ encodes: 76% of
the structural trials ran with the trialled gene resolved to its OFF state, and
every one of them was retained. A gene that is switched off in the trial arm
cannot have produced the gain, so the delta was measuring the comparison rather
than the gene.

Two things are therefore locked here:

* the organ's classification of "provably inert", and the rule that an absent
  ledger yields NO signal rather than a calm zero;
* the mirror of the producer's boolean threshold (`proteus_hooks._as_bool`,
  which crosses at 0.5). The tier is deliberately loadable as a bare directory,
  so it cannot always import the producer; the mirror is pinned so the two
  cannot drift apart in silence.
"""
import os
import sys
import unittest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `scripts` is on the path too so the panel test can import the viewer on its
# own: it previously only worked when another test file had already added it,
# which made this file fail when run alone.
for _p in (_project_root, os.path.join(_project_root, "src"),
           os.path.join(_project_root, "src", "CORE", "WORMS", "expansion"),
           os.path.join(_project_root, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import exmachina as ex                                    # noqa: E402
from CORE.WORMS.expansion import proteus_hooks as ph      # noqa: E402

_LEDGER = os.path.join(_project_root, "state", "proteus_morph.jsonl")


def _row(gene="use_schema_features", value=False, delta=1.0, dim=5, cycle=0):
    """One morph-ledger row, shaped exactly as `proteus_hooks.morph_record`."""
    return {"cycle_index": cycle, "gene": gene, "dimension": dim,
            "fitness": 20.0 + float(delta), "baseline_fitness": 20.0,
            "extra": {gene: value}}


def _inert_ledger(n=6, delta=1.0):
    return [_row(value=False, delta=delta, cycle=i) for i in range(n)]


class TestMirrorOfTheProducerThreshold(unittest.TestCase):
    """The tier mirrors `_as_bool`'s threshold; lock it to the producer."""

    def test_mirror_threshold_matches_the_producer(self):
        self.assertEqual(ex._MORPH_SWITCH_AT, 0.5)
        # If the producer ever moves its crossing point, these flip and this
        # test fails -- which is the point: a silent divergence would make the
        # organ count inert trials against a rule nobody else uses.
        self.assertIs(ph._as_bool(0.5), True)
        self.assertIs(ph._as_bool(0.4999), False)
        self.assertIs(ph._as_bool(True), True)
        self.assertIs(ph._as_bool(False), False)

    def test_observed_ledger_values_resolve_as_the_producer_resolves_them(self):
        # values that actually occur in the ledger
        cases = [0.05632200445147071, 0.06856321716945209, 0.5141935498649961,
                 True, False]
        for v in cases:
            self.assertIs(ph._as_bool(v),
                          v if isinstance(v, bool) else (float(v) >= ex._MORPH_SWITCH_AT),
                          "mirror disagrees with the producer on %r" % (v,))

    def test_resolver_reports_which_path_it_took(self):
        _resolve, how = ex._switch_resolver()
        self.assertIn(how, ("producer", "threshold:0.5"))


class TestProvablyInertTrials(unittest.TestCase):

    def test_gene_off_in_every_trial_is_never_enabled(self):
        m = ex.mutagenesis(_inert_ledger(6))
        self.assertEqual(m["trials"], 6)
        self.assertEqual(m["inert_trials"], 6)
        self.assertEqual(m["inert_share"], 1.0)
        self.assertEqual(m["never_enabled"], ["use_schema_features"])
        # a gene that never went on has no observable value effect
        self.assertIsNone(m["genes"][0]["value_effect"])
        self.assertTrue(m["genes"][0]["never_enabled"])

    def test_free_growth_reports_the_retention_of_unattributable_trials(self):
        m = ex.mutagenesis(_inert_ledger(6))
        self.assertEqual(m["verdict"], "free growth")
        # 6/6 is significant (p = 1/64) but the discount is real on purpose:
        # with so few trials the share is not yet trustworthy on its own.
        self.assertGreater(m["free_growth"], 0.5)
        self.assertLessEqual(m["free_growth"], 1.0)
        self.assertEqual(m["inert_positive"], 6)

    def test_more_evidence_moves_free_growth_toward_the_inert_share(self):
        small = ex.mutagenesis(_inert_ledger(6))["free_growth"]
        big = ex.mutagenesis(_inert_ledger(40))["free_growth"]
        self.assertGreater(big, small)
        self.assertAlmostEqual(big, 1.0, places=3)

    def test_resolvable_trials_with_the_gene_on_are_not_inert(self):
        rows = [_row(value=True, delta=(1.0 if i % 2 == 0 else -1.0), cycle=i)
                for i in range(8)]
        m = ex.mutagenesis(rows)
        self.assertEqual(m["inert_trials"], 0)
        self.assertEqual(m["inert_share"], 0.0)
        self.assertEqual(m["free_growth"], 0.0)
        self.assertEqual(m["verdict"], "selection active")

    def test_weak_evidence_discounts_free_growth(self):
        # half the inert trials lost: retention is not distinguishable from noise
        rows = [_row(value=False, delta=(1.0 if i % 2 == 0 else -1.0), cycle=i)
                for i in range(8)]
        m = ex.mutagenesis(rows)
        self.assertEqual(m["inert_trials"], 8)
        self.assertLess(m["free_growth"], 0.15,
                        "free growth must be discounted when the trials did not all win")
        self.assertEqual(m["verdict"], "selection active")

    def test_value_effect_is_the_on_minus_off_differential(self):
        rows = ([_row(value=True, delta=2.0, cycle=i) for i in range(3)]
                + [_row(value=False, delta=0.5, cycle=i) for i in range(3)])
        m = ex.mutagenesis(rows)
        self.assertEqual(m["inert_trials"], 3)
        self.assertAlmostEqual(m["genes"][0]["value_effect"], 1.5, places=4)


class TestDefaultAwareInertness(unittest.TestCase):
    """Inert is not "off" — it is "the trainer was called as production calls it".

    `RLTrainLoop.train_from_memory` defaults `use_schema_features=False` and
    `by_session=True`, so for the latter it is ON that is the no-op. The earlier
    on/off reading counted every `by_session=True` trial as a real treatment and
    credited the gene for a margin the trial could not have produced: on the
    2026-09-12 ledger that was 17 of the 52 inert rows.
    """

    def test_morph_defaults_mirror_the_producer(self):
        self.assertEqual(ex._MORPH_TRAINER_DEFAULTS, ph.MORPH_TRAINER_DEFAULTS)
        # and both mirror the trainer itself
        import inspect
        from CORE.WORMS.expansion.learning_rl import RLTrainLoop
        sig = inspect.signature(RLTrainLoop.train_from_memory)
        for kwarg, default in ex._MORPH_TRAINER_DEFAULTS.items():
            self.assertIs(sig.parameters[kwarg].default, default,
                          "%s default drifted from the trainer" % kwarg)

    def test_default_valued_trial_is_inert(self):
        rows = [_row(gene="by_session", value=True, delta=1.0, cycle=i)
                for i in range(6)]
        m = ex.mutagenesis(rows)
        self.assertEqual(m["inert_trials"], 6)
        self.assertEqual(m["inert_share"], 1.0)
        self.assertEqual(m["never_enabled"], ["by_session"])
        self.assertEqual(m["genes"][0]["changed"], 0)

    def test_leaving_the_default_is_a_real_treatment(self):
        rows = [_row(gene="by_session", value=False, delta=1.0, cycle=i)
                for i in range(6)]
        m = ex.mutagenesis(rows)
        self.assertEqual(m["inert_trials"], 0)
        self.assertEqual(m["genes"][0]["changed"], 6)

    def test_value_effect_is_changed_minus_default_matched(self):
        rows = ([_row(gene="by_session", value=False, delta=2.0, cycle=i)
                 for i in range(3)]
                + [_row(gene="by_session", value=True, delta=0.5, cycle=i)
                   for i in range(3)])
        m = ex.mutagenesis(rows)
        self.assertEqual(m["inert_trials"], 3)
        self.assertAlmostEqual(m["genes"][0]["value_effect"], 1.5, places=4)

    def test_a_ledgers_own_verdict_outranks_the_mirror(self):
        """The producer owns the trainer's signature, so where it has stamped a
        row the tier must believe the stamp rather than re-derive it."""
        stamped = [dict(_row(gene="by_session", value=True, delta=1.0, cycle=i),
                        inert=True) for i in range(6)]
        self.assertEqual(ex.mutagenesis(stamped)["inert_trials"], 6)

        overridden = [dict(_row(gene="use_schema_features", value=0.9,
                                delta=1.0, cycle=i), inert=False)
                      for i in range(6)]
        m = ex.mutagenesis(overridden)
        self.assertEqual(m["inert_trials"], 0,
                         "a stamped row must not be reclassified as inert")
        self.assertEqual(m["flagged_rows"], 6)

    def test_disagreement_between_stamp_and_rule_is_reported(self):
        """Drift between the tier's mirror and the producer is the one failure
        the mirror cannot detect by itself, so it is counted rather than
        assumed away."""
        rows = [dict(_row(gene="use_schema_features", value=False, delta=1.0,
                          cycle=i), inert=False) for i in range(6)]
        self.assertEqual(ex.mutagenesis(rows)["inert_disagreements"], 6)

    def test_unknown_gene_is_not_counted_as_inert(self):
        rows = [_row(gene="mystery", value=0.1, delta=1.0, cycle=i)
                for i in range(6)]
        m = ex.mutagenesis(rows)
        self.assertEqual(m["inert_trials"], 0)
        self.assertEqual(m["genes"][0]["unknown"], 6)


class TestMatchedAB(unittest.TestCase):
    """The organ must report the ATTRIBUTABLE effect when the cycle measured one.

    The fitness delta in a morph row compares a 5-gene champion against the
    4-gene production baseline, so it conflates the gene with the search — and
    52 of 69 rows never moved the trainer call at all. The matched A/B holds the
    champion and the pools fixed and moves ONE gene, so its delta is
    attributable by construction; MUTAGEN reports it separately rather than
    folding it into the confounded comparison.
    """

    def _ab_row(self, gene="by_session", value=True, delta=1.0,
                control=22.4, treatment=22.1, cycle=0):
        r = _row(gene=gene, value=value, delta=delta, cycle=cycle)
        r["ab"] = {"gene": gene, "control_fitness": control,
                   "treatment_fitness": treatment}
        return r

    def test_measured_trials_are_counted(self):
        rows = [self._ab_row(cycle=i) for i in range(6)]
        m = ex.mutagenesis(rows)
        self.assertEqual(m["measured_trials"], 6)
        self.assertEqual(m["measured_share"], 1.0)
        self.assertEqual(m["never_measured"], [])

    def test_ab_mean_is_the_attributable_effect(self):
        # treatment loses every time: the gene did not pay for itself
        rows = [self._ab_row(cycle=i) for i in range(6)]
        m = ex.mutagenesis(rows)
        self.assertAlmostEqual(m["ab_mean"], -0.3, places=4)
        self.assertEqual(m["ab_positive"], 0)
        self.assertEqual(m["ab_p"], 1.0)
        g = m["genes"][0]
        self.assertEqual(g["ab_n"], 6)
        self.assertAlmostEqual(g["ab_mean"], -0.3, places=4)
        self.assertFalse(g["never_measured"])

    def test_a_winning_treatment_reads_as_attributable(self):
        rows = [self._ab_row(control=22.0, treatment=22.5, cycle=i)
                for i in range(6)]
        m = ex.mutagenesis(rows)
        self.assertAlmostEqual(m["ab_mean"], 0.5, places=4)
        self.assertEqual(m["ab_positive"], 6)
        self.assertLess(m["ab_p"], 0.05)

    def test_genes_without_an_ab_are_never_measured(self):
        rows = [_row(value=False, delta=1.0, cycle=i) for i in range(6)]
        m = ex.mutagenesis(rows)
        self.assertEqual(m["measured_trials"], 0)
        self.assertEqual(m["never_measured"], ["use_schema_features"])
        self.assertIsNone(m["ab_mean"])
        self.assertEqual(m["genes"][0]["ab_n"], 0)
        self.assertTrue(m["genes"][0]["never_measured"])

    def test_a_recorded_summary_delta_is_used_when_present(self):
        # 6 rows, not 4: below `min_trials` the organ reports `unavailable` and
        # deliberately carries no measurement keys at all.
        rows = []
        for i in range(6):
            r = _row(value=True, delta=1.0, cycle=i)
            r["ab"] = {"gene": "use_schema_features", "delta": 0.25}
            rows.append(r)
        m = ex.mutagenesis(rows)
        self.assertEqual(m["measured_trials"], 6)
        self.assertAlmostEqual(m["ab_mean"], 0.25, places=4)

    def test_too_few_rows_reports_unavailable_without_measurement_keys(self):
        rows = [self._ab_row(cycle=i) for i in range(3)]
        m = ex.mutagenesis(rows)
        self.assertEqual(m["verdict"], "unavailable")
        self.assertNotIn("measured_trials", m)

    def test_a_broken_ab_is_not_counted_as_measured(self):
        rows = []
        for i in range(6):
            r = _row(value=False, delta=1.0, cycle=i)
            r["ab"] = {"gene": "use_schema_features"}       # no arms at all
            rows.append(r)
        m = ex.mutagenesis(rows)
        self.assertEqual(m["measured_trials"], 0)
        self.assertIsNone(m["ab_mean"])


class TestAbsenceIsNotCalm(unittest.TestCase):

    def test_no_ledger_yields_no_signal(self):
        m = ex.mutagenesis(None)
        self.assertEqual(m["verdict"], "unavailable")
        self.assertIsNone(m["free_growth"], "an absent ledger must not score 0.0")
        self.assertNotIn("mutagenesis", ex.epistemic_report([])["signals"])

    def test_too_few_rows_is_unavailable_not_active(self):
        m = ex.mutagenesis(_inert_ledger(3))
        self.assertEqual(m["verdict"], "unavailable")
        self.assertIsNone(m["free_growth"])

    def test_signal_appears_only_when_the_ledger_is_supplied(self):
        rep = ex.epistemic_report([], morph_rows=_inert_ledger(6))
        self.assertIn("mutagenesis", rep["signals"])
        self.assertGreater(rep["signals"]["mutagenesis"], 0.5)

    def test_never_raises_on_garbage(self):
        for junk in (None, [], [None], [{}], [{"fitness": "x"}], "nonsense", 42,
                     [{"fitness": 1.0, "baseline_fitness": None}], object()):
            try:
                ex.mutagenesis(junk)
            except Exception as exc:                        # noqa: BLE001
                self.fail("mutagenesis(%r) raised %s: %s"
                          % (junk, type(exc).__name__, exc))


class TestLoadAndSuiteEvidence(unittest.TestCase):

    def test_genetic_load_and_the_wilson_bound(self):
        genome = {"usefulness_floor": 0.15,
                  "genes": [{"name": "a", "usefulness": 0.30},
                            {"name": "b", "usefulness": 0.05}]}
        mutation = {"at": "2026-01-01T00:00:00", "mutants": 30, "killed": 30,
                    "target": "x.py"}
        m = ex.mutagenesis(_inert_ledger(6), genome=genome, mutation=mutation)
        self.assertEqual(m["load"]["below_floor"], ["b"])
        self.assertEqual(m["load"]["share"], 0.5)
        self.assertEqual(m["tests"]["score"], 1.0)
        # 30/30 is NOT "score 1.0" -- with n=30 the honest statement is a bound,
        # and the difference between those two claims is the whole point.
        self.assertEqual(m["tests"]["lower"], 0.8865)

    def test_missing_artifacts_leave_load_and_tests_unset(self):
        m = ex.mutagenesis(_inert_ledger(6))
        self.assertIsNone(m["load"])
        self.assertIsNone(m["tests"])


class TestRegistry(unittest.TestCase):

    def test_mutagen_is_registered_in_the_organ_table(self):
        keys = [o["key"] for o in ex.ORGANS]
        self.assertIn("mutagen", keys)
        self.assertEqual(len(ex.ORGANS), 17,
                         "the organ table gained or lost an entry")
        names = {o["name"] for o in ex.ORGANS}
        self.assertIn("MUTAGEN", names)

    def test_mutagenesis_is_a_weighted_severity_signal(self):
        self.assertIn("mutagenesis", ex.SEVERITY_WEIGHTS)
        weight, _why = ex.SEVERITY_WEIGHTS["mutagenesis"]
        self.assertGreater(weight, 0.0)
        self.assertIn("mutagenesis", ex.__all__)

    def test_panel_declares_the_mutagen_organ(self):
        import exmachina_live_monitor as mon
        names = {n for n, _t, _d in mon.EX_ORGANS}
        self.assertIn("MUTAGEN", names)
        self.assertIn("mutagen", mon._GLYPH_TIERS["ascii"])
        self.assertIn("mutagen", mon._GLYPH_TIERS["unicode"])


class TestRealLedger(unittest.TestCase):
    """Structural checks against the committed artifact (not brittle numbers)."""

    def setUp(self):
        if not os.path.exists(_LEDGER):
            self.skipTest("no morph ledger on this checkout")
        import json
        with open(_LEDGER, encoding="utf-8") as f:
            self.rows = [json.loads(l) for l in f if l.strip()]

    def test_real_ledger_produces_a_verdict(self):
        m = ex.mutagenesis(self.rows)
        # Usable rows only: mutagenesis deliberately skips no-op cycles
        # (no gene) and rows without fitness — counting them would bias the
        # share, per the function's own documented rationale. The test pins
        # THAT contract, not raw row count.
        usable = [r for r in self.rows
                  if isinstance(r, dict) and r.get("gene")
                  and r.get("fitness") is not None
                  and r.get("baseline_fitness") is not None]
        self.assertEqual(m["trials"], len(usable))
        self.assertIn(m["verdict"], ("free growth", "drift", "selection active"))
        self.assertIsNotNone(m["free_growth"])
        self.assertGreaterEqual(m["free_growth"], 0.0)
        self.assertLessEqual(m["free_growth"], 1.0)

    def test_real_ledger_retention_is_not_a_selection_signal(self):
        m = ex.mutagenesis(self.rows)
        # The point of the organ: near-total retention, which no genuinely
        # selective operator produces.
        self.assertGreater(m["retention"], 0.9)


if __name__ == "__main__":
    unittest.main()
