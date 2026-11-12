# coding: utf-8
"""SOTA-row consumers on the allocator (EP253 / EP187 / EP223, 2026-10-10).

Falsifiable claims:

  1. The existing path is UNCHANGED: `weights`, `allocation`, `update` and
     `reward_for_outcome` behave exactly as before the views were added.
  2. `participation_prior()` is a distribution (sums to 1), respects the floor
     so no arm is starved out of it, tracks successes, and mutates nothing.
  3. `contribution_weights()` ranks an arm holding more evidence above one
     holding the same posterior mean with less, and mutates nothing.
  4. `ingest_outcome()` records EXACTLY what the manual
     `reward_for_outcome` + `update` path records, for every named outcome,
     and an unknown outcome still fails closed.
  5. `action_space()` describes the arm set; `joint_action_space()` composes
     several allocators into one prefixed action set with an owner map, and
     mutates none of them.
"""
import random
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.allocation import (  # noqa: E402
    PROTEUS_ARMS, ThompsonAllocator, joint_action_space, reward_for_outcome,
)

OUTCOMES = ("holdout_pass", "promoted", "beat_baseline", "novel", "flat",
            "holdout_fail", "error", "success", "fail")


def _alloc(arms=("ga", "cma", "de"), **kw):
    return ThompsonAllocator(list(arms), rng=random.Random(0), **kw)


def _collapse(alloc, arm, fails=60):
    for _ in range(fails):
        alloc.update("ga", success=True, weight=1.0)
        alloc.update(arm, success=False, weight=1.0)


class TestExistingPathUnchanged(unittest.TestCase):
    """Claim 1 — the new views did not alter the posterior arithmetic."""

    def test_fresh_weights_are_the_prior_mean(self):
        alloc = _alloc()
        for arm, w in alloc.weights().items():
            self.assertAlmostEqual(w, 0.5, places=6,
                                   msg="prior alpha=beta=1 must mean 0.5")

    def test_update_moves_the_posterior_mean(self):
        alloc = _alloc()
        for _ in range(10):
            alloc.update("ga", success=True, weight=1.0)
        self.assertAlmostEqual(alloc.weights()["ga"], 11.0 / 12.0, places=6)
        self.assertAlmostEqual(alloc.weights()["cma"], 0.5, places=6)

    def test_allocation_still_sums_to_total_and_floors_every_arm(self):
        alloc = ThompsonAllocator(list(PROTEUS_ARMS), floor=0.02,
                                  rng=random.Random(1))
        budget = alloc.allocation(100)
        self.assertEqual(sum(budget.values()), 100)
        self.assertEqual(set(budget), set(PROTEUS_ARMS))
        self.assertGreaterEqual(min(budget.values()), 1,
                                "the floor exists so no operator is starved")

    def test_allocation_gives_a_collapsed_arm_a_share(self):
        alloc = ThompsonAllocator(list(PROTEUS_ARMS), floor=0.02,
                                  rng=random.Random(2))
        _collapse(alloc, "de")
        budget = alloc.allocation(100)
        self.assertGreaterEqual(budget["de"], 1,
                                "E10: a regime change must stay reachable")
        self.assertEqual(sum(budget.values()), 100)

    def test_reward_for_outcome_still_fails_closed(self):
        self.assertEqual(reward_for_outcome("holdout_pass"), (True, 1.0))
        self.assertEqual(reward_for_outcome("not_a_real_outcome"), (False, 1.0))


class TestParticipationPrior(unittest.TestCase):
    """Claim 2 — EP253's learned replacement for a static pool map."""

    def test_is_a_distribution(self):
        prior = _alloc().participation_prior()
        self.assertAlmostEqual(sum(prior.values()), 1.0, places=9)
        self.assertEqual(set(prior), {"ga", "cma", "de"})

    def test_tracks_successes(self):
        alloc = _alloc()
        for _ in range(5):
            alloc.update("ga", success=True, weight=1.0)
        prior = alloc.participation_prior()
        self.assertGreater(prior["ga"], prior["cma"])

    def test_floor_keeps_a_collapsed_arm_in_the_prior(self):
        alloc = _alloc(floor=0.02)
        _collapse(alloc, "de")
        prior = alloc.participation_prior()
        self.assertGreater(prior["de"], 0.0,
                           "a starved arm must stay reachable (the floor)")
        self.assertAlmostEqual(sum(prior.values()), 1.0, places=9)

    def test_mutates_nothing(self):
        alloc = _alloc()
        alloc.update("ga", success=True, weight=1.0)
        before = alloc.to_dict()
        alloc.participation_prior()
        self.assertEqual(alloc.to_dict(), before)


class TestContributionWeights(unittest.TestCase):
    """Claim 3 — EP187's contribution weighting for the scheduler."""

    def test_more_evidence_outranks_the_same_mean(self):
        alloc = _alloc(arms=("a", "b"))
        alloc.update("a", success=True, weight=1.0)
        alloc.update("a", success=False, weight=1.0)
        for _ in range(20):
            alloc.update("b", success=True, weight=1.0)
            alloc.update("b", success=False, weight=1.0)
        self.assertAlmostEqual(alloc.weights()["a"], alloc.weights()["b"],
                               places=6)
        contrib = alloc.contribution_weights()
        self.assertGreater(contrib["b"], contrib["a"],
                           "same mean, more pulls => a larger contribution")
        self.assertAlmostEqual(sum(contrib.values()), 1.0, places=9)

    def test_mutates_nothing(self):
        alloc = _alloc()
        before = alloc.to_dict()
        alloc.contribution_weights()
        self.assertEqual(alloc.to_dict(), before)


class TestIngestOutcome(unittest.TestCase):
    """Claim 4 — the reward-ingest path is the existing path, spelled once."""

    def test_matches_the_manual_reward_path_for_every_outcome(self):
        for outcome in OUTCOMES:
            with self.subTest(outcome=outcome):
                via_api = _alloc()
                manual = _alloc()
                ok, weight = via_api.ingest_outcome("cma", outcome)
                self.assertEqual((ok, weight), reward_for_outcome(outcome))
                m_ok, m_w = reward_for_outcome(outcome)
                manual.update("cma", success=m_ok, weight=m_w)
                self.assertEqual(via_api.to_dict(), manual.to_dict(),
                                 "ingest_outcome must not diverge from update")

    def test_unknown_outcome_fails_closed(self):
        alloc = _alloc()
        alloc.ingest_outcome("ga", "an_outcome_nobody_declared")
        report = alloc.report()["ga"]
        self.assertEqual(report["failures"], 1.0,
                         "an unknown outcome counts as a full-weight failure")
        self.assertEqual(report["successes"], 0.0)
        self.assertEqual(report["pulls"], 1)

    def test_creates_an_unseen_arm_like_update_does(self):
        alloc = _alloc()
        alloc.ingest_outcome("chorus", "promoted")
        self.assertIn("chorus", alloc.arms)
        self.assertEqual(alloc.report()["chorus"]["wins"], 1)


class TestActionSpaces(unittest.TestCase):
    """Claim 5 — EP223's joint action space."""

    def test_action_space_describes_the_arm_set(self):
        alloc = _alloc()
        space = alloc.action_space()
        self.assertEqual(space["kind"], "discrete")
        self.assertEqual(space["actions"], list(alloc.arms))
        self.assertEqual(space["n_actions"], len(alloc.arms))
        self.assertAlmostEqual(sum(space["priors"].values()), 1.0, places=9)
        self.assertEqual(space["floor"], alloc.floor)

    def test_action_space_mutates_nothing(self):
        alloc = _alloc()
        before = alloc.to_dict()
        alloc.action_space()
        self.assertEqual(alloc.to_dict(), before)

    def test_joint_space_prefixes_uniquely_and_maps_owners(self):
        left = _alloc(arms=("ga", "cma"))
        right = _alloc(arms=("tpe", "de"))
        joint = joint_action_space(left, right, name="joint")
        self.assertEqual(joint["n_actions"], 4)
        self.assertEqual(len(set(joint["actions"])), 4)
        self.assertAlmostEqual(sum(joint["priors"].values()), 1.0, places=9)
        self.assertEqual(joint["owners"]["joint:ga"], 0)
        self.assertEqual(joint["owners"]["joint:tpe"], 1)

    def test_duplicate_arm_names_do_not_collide(self):
        left = _alloc(arms=("ga", "cma"))
        right = _alloc(arms=("cma", "de"))
        joint = joint_action_space(left, right)
        self.assertEqual(joint["n_actions"], 4, "each arm names one action")
        self.assertEqual(joint["owners"]["joint:cma"], 0,
                         "the first allocator owns the shared name")

    def test_joint_space_mutates_none_of_its_inputs(self):
        left, right = _alloc(), _alloc()
        before_left, before_right = left.to_dict(), right.to_dict()
        joint_action_space(left, right)
        self.assertEqual(left.to_dict(), before_left)
        self.assertEqual(right.to_dict(), before_right)

    def test_empty_joint_space_is_empty_not_an_error(self):
        joint = joint_action_space()
        self.assertEqual(joint["n_actions"], 0)
        self.assertEqual(joint["priors"], {})


if __name__ == "__main__":
    unittest.main()
