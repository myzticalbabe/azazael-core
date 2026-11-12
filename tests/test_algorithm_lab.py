"""Regression contracts for the executable algorithm laboratory."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for entry in (SRC, SCRIPTS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from CORE.algorithm_qualification import (  # noqa: E402
    QualificationEvidence,
    classify,
    qualified_components,
)
from CORE.WORMS.expansion.diversity import (  # noqa: E402
    Individual,
    NSGAIII,
    PersistentMAPElites,
    run_diversity_evidence,
)
from CORE.WORMS.expansion.policy_optimization import (  # noqa: E402
    SPOTracker,
    TokenTrace,
    cispo_is_weights,
    cispo_policy_loss,
    depo_filter,
    dpo_loss,
    dppo_rescale,
    gdpo_advantages,
    grpo_advantages,
    ipo_loss,
    kto_loss,
    ngrpo_mix,
    reinforce_plus_plus_advantages,
    rloo_advantages,
    sapo_gate,
    sapo_policy_loss,
    select_dapo_groups,
    sequence_policy_loss,
    simpo_loss,
    token_policy_loss,
)


class TestPolicyContracts(unittest.TestCase):
    def test_advantage_estimators_have_explicit_contracts(self):
        self.assertEqual(rloo_advantages([1, 2, 4]), [-2.0, -0.5, 2.5])
        self.assertEqual(grpo_advantages([1, 1, 1]), [0.0, 0.0, 0.0])
        self.assertEqual(
            reinforce_plus_plus_advantages([1, 2], global_rewards=[1, 2, 3]),
            [1.224744871391589, 2.449489742783178],
        )

    def test_dapo_requires_informative_group_and_budget(self):
        groups = [
            TokenTrace((0.0, 0.0), group_rewards=(0.0, 1.0)),
            TokenTrace((0.0, 0.0), group_rewards=(1.0, 1.0)),
            TokenTrace((0.0, 0.0), group_rewards=(0.0, 1.0), overlong=True),
        ]
        selected, skipped = select_dapo_groups(groups, max_tokens=2, target_groups=2)
        self.assertEqual(len(selected), 1)
        self.assertEqual(skipped, 2)

    def test_masked_tokens_are_ignored_and_clipping_is_bounded(self):
        trace = TokenTrace((0.0, 0.0), mask=(True, False))
        self.assertAlmostEqual(token_policy_loss(trace, 1.0), -0.28)
        self.assertAlmostEqual(sequence_policy_loss(trace, 1.0), -0.28)
        self.assertEqual(trace.valid_indices(), (0,))

    def test_gdpo_preserves_within_stream_differences(self):
        # Two streams where joint normalization collapses resolution:
        # stream A separates samples 0/1, stream B is flat. GDPO keeps
        # A's ordering (adv[0] < adv[1]) instead of washing it out.
        adv = gdpo_advantages({"a": [0.0, 10.0], "b": [5.0, 5.0]})
        self.assertEqual(len(adv), 2)
        self.assertLess(adv[0], adv[1])

    def test_gdpo_zero_variance_stream_contributes_nothing(self):
        adv = gdpo_advantages({"flat": [3.0, 3.0, 3.0]})
        self.assertEqual(adv, [0.0, 0.0, 0.0])

    def test_gdpo_rejects_ragged_streams_and_empties(self):
        with self.assertRaises(ValueError):
            gdpo_advantages({"a": [1.0, 2.0], "b": [1.0]})
        with self.assertRaises(ValueError):
            gdpo_advantages({})

    def test_cispo_weights_clip_stale_tokens(self):
        # Identical policies -> unit weights; a token the behavior policy
        # favored 10x over current is clipped instead of exploding.
        self.assertEqual(
            cispo_is_weights([0.1, 0.2], [0.1, 0.2]),
            [1.0, 1.0],
        )
        weights = cispo_is_weights([2.302585092994046, 0.0], [0.0, 0.0],
                                   clip_high=2.0)
        self.assertAlmostEqual(weights[0], 2.0)
        self.assertAlmostEqual(weights[1], 1.0)

    def test_cispo_rejects_mismatched_and_nonfinite_ratios(self):
        with self.assertRaises(ValueError):
            cispo_is_weights([0.1], [0.1, 0.2])
        with self.assertRaises(ValueError):
            cispo_is_weights([float("inf")], [0.0])

    def test_cispo_loss_reweights_stale_batch(self):
        # On-policy: plain negative advantage.
        trace = TokenTrace((0.0, 0.0), mask=(True, True))
        self.assertAlmostEqual(
            cispo_policy_loss(trace, 1.0, [0.0, 0.0]), -1.0)
        # Current policy 2x above the rollout policy: clipped weight 2.0
        # doubles the gradient instead of discarding the stale batch.
        moved = TokenTrace((0.6931471805599453,) * 2, mask=(True, True))
        self.assertAlmostEqual(
            cispo_policy_loss(moved, 1.0, [0.0, 0.0],
                              clip_high=2.0), -2.0)

    def test_depo_keeps_only_the_competence_frontier(self):
        kept = depo_filter({"easy": 0.0, "mid": 0.5, "hard": 1.0,
                            "a": 0.2, "b": 0.9})
        self.assertEqual(kept, ["mid", "a", "b"])
        with self.assertRaises(ValueError):
            depo_filter({"x": 0.5}, low=0.9, high=0.1)

    def test_dppo_rescale_is_inverse_keep_prob(self):
        self.assertEqual(dppo_rescale(0.5), 2.0)
        self.assertEqual(dppo_rescale(1.0), 1.0)
        with self.assertRaises(ValueError):
            dppo_rescale(0.0)
        with self.assertRaises(ValueError):
            dppo_rescale(1.5)

    def test_spo_tracker_normalizes_against_history(self):
        tracker = SPOTracker()
        self.assertEqual(tracker.advantages([1.0, 2.0]), [0.0, 0.0])
        tracker.update([1.0, 2.0, 3.0])
        adv = tracker.advantages([1.0, 2.0, 3.0])
        self.assertAlmostEqual(adv[0], -1.224744871391589, places=6)
        self.assertAlmostEqual(adv[1], 0.0, places=6)
        self.assertAlmostEqual(adv[2], 1.224744871391589, places=6)

    def test_dpo_prefers_winners_with_reference_anchor(self):
        self.assertAlmostEqual(
            dpo_loss(0.0, 0.0, 0.0, 0.0, beta=1.0), 0.6931471805599453)
        self.assertAlmostEqual(
            dpo_loss(1.0, 0.0, 0.0, 0.0, beta=1.0), 0.3133, places=4)
        with self.assertRaises(ValueError):
            dpo_loss(1.0, 0.0, 0.0, 0.0, beta=0.0)

    def test_ipo_saturates_near_ties(self):
        self.assertAlmostEqual(
            ipo_loss(1.0, 0.0, 0.0, 0.0, beta=1.0), 0.25)

    def test_kto_weighs_losses_more_than_wins(self):
        win = kto_loss(1.0, 0.0, desirable=True, beta=1.0)
        loss = kto_loss(1.0, 0.0, desirable=False, beta=1.0)
        self.assertAlmostEqual(win, 0.3133, places=4)
        self.assertAlmostEqual(loss, 2.6265, places=4)
        self.assertGreater(loss, win)

    def test_simpo_needs_no_reference_model(self):
        self.assertAlmostEqual(
            simpo_loss(-10.0, -30.0, chosen_len=10, rejected_len=10,
                       beta=2.0, gamma=0.5), 0.0486, places=4)
        with self.assertRaises(ValueError):
            simpo_loss(-10.0, -30.0, chosen_len=0, rejected_len=10)

    def test_sapo_gate_is_soft_not_hard(self):
        self.assertEqual(sapo_gate(1.0), 1.0)
        self.assertAlmostEqual(sapo_gate(100.0), 0.0198, places=4)
        self.assertGreater(sapo_gate(2.0), sapo_gate(100.0))
        with self.assertRaises(ValueError):
            sapo_gate(-1.0)

    def test_ngrpo_mixing_stays_convex(self):
        self.assertEqual(
            ngrpo_mix([1.0, 0.0], [[0.0, 1.0]], alpha=0.5), [0.5, 0.5])
        self.assertEqual(
            ngrpo_mix([1.0, 0.0], [[0.0, 1.0]], alpha=0.0), [1.0, 0.0])
        self.assertEqual(ngrpo_mix([1.0, 0.0], [], alpha=0.5), [1.0, 0.0])
        with self.assertRaises(ValueError):
            ngrpo_mix([1.0, 0.0], [[0.0]], alpha=0.5)


class TestDiversityContracts(unittest.TestCase):
    def test_persistent_map_elites_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.json"
            archive = PersistentMAPElites(
                [("speed", 2, 0.0, 1.0)],
                {"x": (0.0, 1.0)},
                seed=9,
                path=path,
            )
            self.assertTrue(archive.add(Individual({"x": 0.2}, (), 1.0, (0.1,))))
            self.assertTrue(archive.add(Individual({"x": 0.8}, (), 2.0, (0.9,))))
            archive.save()
            loaded = PersistentMAPElites(
                [("speed", 2, 0.0, 1.0)],
                {"x": (0.0, 1.0)},
                path=path,
            )
            self.assertTrue(loaded.load())
            self.assertEqual(loaded.stats()["filled"], 2)
            self.assertEqual(
                loaded.archive,
                {
                    key: {
                        **value,
                        "objectives": list(value["objectives"]),
                        "behavior": list(value["behavior"]),
                    }
                    for key, value in archive.archive.items()
                },
            )

    def test_nsga3_selects_valid_objective_width(self):
        candidates = [
            Individual({"x": float(index)}, (float(index % 3), float(index // 3)))
            for index in range(8)
        ]
        selected = NSGAIII(population_size=4, divisions=2).select(candidates)
        self.assertEqual(len(selected), 4)
        self.assertTrue(all(len(row.objectives) == 2 for row in selected))

    def test_evidence_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            first = run_diversity_evidence(
                seed=17, archive_path=Path(directory) / "first.json"
            )
            second = run_diversity_evidence(
                seed=17, archive_path=Path(directory) / "second.json"
            )
        self.assertEqual(first, second)
        self.assertEqual(first["nsga3"]["selected"], 4)


class TestQualificationContracts(unittest.TestCase):
    def test_gate_fails_closed_and_requires_all_obligations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "runner.py"
            artifact = root / "result.json"
            executable.write_text("pass\n", encoding="utf-8")
            artifact.write_text("{}", encoding="utf-8")
            base = dict(
                component="LAB",
                executable_path=str(executable),
                deterministic_seed=11,
                result_artifact=str(artifact),
            )
            self.assertEqual(classify(QualificationEvidence(**base))["status"], "executable")
            passing = dict(
                base,
                regression_evidence={"passed": 3, "vacuous": False},
            )
            self.assertEqual(classify(QualificationEvidence(**passing))["status"], "qualified")
            served = dict(passing, production_callable="scripts/runner.py")
            self.assertEqual(classify(QualificationEvidence(**served))["status"], "served")
            self.assertEqual(qualified_components([classify(QualificationEvidence(**served))]), ["LAB"])

    def test_lab_components_are_production_but_not_rotation(self):
        import track_registry

        # ROTATION stays disjoint on purpose: it is the training
        # orchestrator's dispatch table (kind sb3/cycle/neural) and the lab
        # components have no reward event and no orchestrator runner.
        self.assertTrue(set(track_registry.LAB_COMPONENTS).isdisjoint(track_registry.ROTATION))
        # Inverted 2026-09-24 when the laboratory went to production
        # (operator decision): the components must be production WITH a
        # recorded callable, so `classify` can reach `served` on real
        # evidence. The old pin (`all(not row["production"])`) now guards the
        # opposite drift -- a component silently demoted back to manual-only.
        self.assertTrue(all(row["production"] for row in track_registry.LAB_COMPONENTS.values()))
        self.assertTrue(all(row.get("production_callable")
                            for row in track_registry.LAB_COMPONENTS.values()))


class TestLabRunner(unittest.TestCase):
    def test_runner_writes_evidence_without_promoting(self):
        import pytest as _pytest
        default_report = ROOT / "state" / "algorithm_lab" / "regression_report.json"
        if not default_report.exists():
            # No in-repo producer writes this file: the default-evidence
            # branch is only exercisable where a lab run left it behind.
            _pytest.skip("no default regression evidence at %s" % default_report)
        spec = importlib.util.spec_from_file_location(
            "algorithm_lab_cycle_test", SCRIPTS / "algorithm_lab_cycle.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = module.run_cycle(
                seed=5,
                output=root / "lab.json",
                archive=root / "archive.json",
            )
            self.assertTrue((root / "lab.json").is_file())
            self.assertTrue((root / "archive.json").is_file())
            self.assertEqual(len(report["policy"]), 5)
            # Production default (2026-09-24): the chain's `algolab` run
            # carries the regression evidence and reaches `served` through the
            # recorded production callable.
            self.assertTrue(all(row["status"] == "served" for row in report["qualification"]))
            self.assertFalse(report["production_rotation_changed"])
            # Fail closed: evidence explicitly withheld -> the runner cannot
            # qualify past `executable`, whichever way it is invoked.
            bare = module.run_cycle(
                seed=5,
                output=root / "lab_bare.json",
                archive=root / "archive_bare.json",
                regression_report=None,
            )
            self.assertTrue(all(row["status"] == "executable" for row in bare["qualification"]))


if __name__ == "__main__":
    unittest.main()
