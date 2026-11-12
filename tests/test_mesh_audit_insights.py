# coding: utf-8
import pytest

pytestmark = pytest.mark.env
"""Tests for the 2026-09-16 mesh per-function audit findings and their fixes.

Four groups:

1. ``fencing`` -- the stale-writer primitive added because ``evolve()`` lost a
   whole generation to a read-modify-write across a lock boundary.
2. ``silence`` -- the silent-failure ledger added because probe PF15 found 118
   handlers whose entire body is ``pass``, making "healed" and "hid it"
   indistinguishable.
3. The three concrete ``evolution`` fixes: the lost-update race, the elite-slot
   ``IndexError``, and MOEA/D fallback weights that did not sum to 1.
4. ``liveness`` -- obligations with deadlines, added because all three defects
   above were events that did NOT happen, which no occurrence-driven monitor
   can observe at any sample rate.

The concurrency tests assert an *invariant* (generation advances exactly once
per committed round) rather than a specific interleaving, so they stay honest
whether or not a given run actually loses the race.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.fencing import (
    Fence, fence_for, fences_reset, fence_report,
)
from CORE.WORMS.expansion.silence import (
    ignoring, record, silence_report, silence_reset, tripped, signatures,
)
from CORE.WORMS.expansion import liveness as lv
from CORE.WORMS.expansion.evolution import (
    EvolutionEngine, generate_reference_points, moead_weights,
)


class TestFencingTokens(unittest.TestCase):
    """Tokens make a stale write unrepresentable rather than discouraged."""

    def setUp(self):
        fences_reset()

    def test_claim_is_monotonic(self):
        f = Fence("t.monotonic")
        tokens = [f.claim() for _ in range(5)]
        self.assertEqual(tokens, [1, 2, 3, 4, 5])
        self.assertEqual(f.token(), 5)

    def test_current_token_commits(self):
        f = Fence("t.commit")
        token = f.claim()
        committed, value = f.commit(token, lambda: "applied")
        self.assertTrue(committed)
        self.assertEqual(value, "applied")
        self.assertEqual(f.snapshot()["commits"], 1)

    def test_stale_token_is_rejected_and_never_applied(self):
        f = Fence("t.stale")
        first = f.claim()
        f.claim()  # someone else moved the fence on
        applied = []
        committed, value = f.commit(first, lambda: applied.append(True))
        self.assertFalse(committed)
        self.assertIsNone(value)
        self.assertEqual(applied, [], "a rejected token must not run apply")
        self.assertEqual(f.snapshot()["rejections"], 1)

    def test_failed_apply_reports_but_does_not_count_as_commit(self):
        f = Fence("t.applyfail")
        token = f.claim()

        def boom():
            raise ValueError("nope")

        committed, value = f.commit(token, boom)
        self.assertFalse(committed)
        self.assertIsInstance(value, ValueError)
        snap = f.snapshot()
        self.assertEqual(snap["commits"], 0)
        self.assertEqual(snap["rejections"], 0)

    def test_fence_for_is_process_wide(self):
        a = fence_for("shared.resource")
        b = fence_for("shared.resource")
        self.assertIs(a, b, "two instances must contend on one fence")

    def test_report_covers_every_live_fence(self):
        fence_for("report.a").claim()
        fence_for("report.b").claim()
        rep = fence_report()
        self.assertIn("report.a", rep)
        self.assertIn("report.b", rep)
        self.assertEqual(rep["report.a"]["claims"], 1)


class TestSilenceLedger(unittest.TestCase):
    """A swallowed exception is a measurement, not just control flow."""

    def setUp(self):
        silence_reset()

    def test_ignoring_absorbs_and_counts(self):
        with ignoring("test.absorb") as ctx:
            raise KeyError("missing")
        self.assertIsInstance(ctx.exception, KeyError)
        self.assertEqual(silence_report()["counts"]["test.absorb"], 1)

    def test_budget_zero_trips_immediately(self):
        with ignoring("test.fault", budget=0):
            raise RuntimeError("a real fault")
        self.assertTrue(tripped("test.fault"))
        self.assertIn("test.fault", tripped())

    def test_budget_absorbs_up_to_its_limit(self):
        for _ in range(3):
            with ignoring("test.tolerant", budget=5):
                raise OSError("flaky peer")
        self.assertFalse(tripped("test.tolerant"))
        with ignoring("test.tolerant", budget=5):
            raise OSError("flaky peer")
        with ignoring("test.tolerant", budget=5):
            raise OSError("flaky peer")
        with ignoring("test.tolerant", budget=5):
            raise OSError("flaky peer")
        self.assertTrue(tripped("test.tolerant"))

    def test_new_signature_is_visible_even_when_the_total_looks_normal(self):
        """The alarm is a rising *signature* count, not a rising total."""
        with ignoring("test.drift", budget=100):
            raise OSError("connection reset")
        with ignoring("test.drift", budget=100):
            raise OSError("connection reset")
        self.assertEqual(len(signatures("test.drift")), 1)
        with ignoring("test.drift", budget=100):
            raise ValueError("nobody predicted this")
        self.assertEqual(len(signatures("test.drift")), 2)
        self.assertEqual(
            signatures("test.drift")["OSError: connection reset"], 2)

    def test_clean_block_records_nothing(self):
        with ignoring("test.clean", budget=0):
            pass
        self.assertNotIn("test.clean", silence_report()["counts"])

    def test_reraise_still_accounts_for_the_failure(self):
        with self.assertRaises(KeyError):
            with ignoring("test.reraise", budget=0, reraise=True):
                raise KeyError("visible")
        self.assertEqual(silence_report()["counts"]["test.reraise"], 1)

    def test_hand_recorded_failure_is_accounted(self):
        record("test.manual", ValueError("by hand"))
        self.assertEqual(silence_report()["counts"]["test.manual"], 1)

    def test_report_shape(self):
        with ignoring("test.shape", budget=0):
            raise RuntimeError("x")
        rep = silence_report()
        for key in ("tags", "total_ignored", "tripped", "counts",
                    "signatures", "first_seen", "last_seen"):
            self.assertIn(key, rep)


class TestEvolutionAuditFixes(unittest.TestCase):
    """The three concrete defects the audit found in evolution.py."""

    def _engine(self, population_size=8, elitism_count=2):
        engine = EvolutionEngine(population_size=population_size,
                                 elitism_count=elitism_count,
                                 memory_store="none")
        engine.initialize_population({"x": (0.0, 1.0), "y": (0.0, 1.0)})
        return engine

    # -- fix 1: read-modify-write across a lock boundary ----------------
    def test_concurrent_evolve_advances_generation_exactly_once_per_commit(self):
        fences_reset()
        engine = self._engine()
        start = engine._generation
        results = []
        guard = threading.Lock()

        def worker():
            out = engine.evolve()
            with guard:
                results.append(out)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)

        self.assertEqual(len(results), 6, "every worker must return")
        committed = [r for r in results if "error" not in r]
        superseded = [r for r in results if "error" in r]
        self.assertEqual(len(committed) + len(superseded), 6)
        # The invariant the fix establishes: a superseded round must not have
        # bumped the generation, so the counter advances exactly once per
        # round that actually published a population.
        self.assertEqual(engine._generation - start, len(committed))
        self.assertLessEqual(engine._generation - start, 6)

    def test_population_stays_within_bounds_under_contention(self):
        fences_reset()
        engine = self._engine()
        threads = [threading.Thread(target=engine.evolve) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)
        self.assertLessEqual(len(engine._population), engine.population_size)
        self.assertGreaterEqual(len(engine._population), 2)

    # -- fix 2: elite slot bounds --------------------------------------
    def test_multi_objective_survives_more_objectives_than_survivors(self):
        engine = self._engine(population_size=2, elitism_count=0)
        objectives = [
            lambda g: g.genes["x"],
            lambda g: 1.0 - g.genes["x"],
            lambda g: abs(g.genes["x"] - 0.5),
        ]
        out = engine.optimize_multi_objective(objectives, generations=1,
                                             use_curative=False)
        self.assertNotIn("error", out)
        self.assertIn("pareto_front", out)

    # -- fix 3: MOEA/D fallback weights --------------------------------
    def test_das_dennis_points_sum_to_one(self):
        for n_obj, p in ((2, 4), (3, 4), (4, 3)):
            for pt in generate_reference_points(n_obj, p):
                self.assertAlmostEqual(sum(pt), 1.0, places=9)

    def test_moead_weights_for_two_objectives_sum_to_one(self):
        # Force the random fallback: ask for far more points than the simplex
        # can supply at p <= 12.
        weights = moead_weights(pop_size=100000, n_obj=2)
        self.assertEqual(len(weights), 100000)
        for w in weights:
            self.assertEqual(len(w), 2)
            self.assertAlmostEqual(w[0] + w[1], 1.0, places=9)

    def test_moead_weights_shape_for_many_objectives(self):
        weights = moead_weights(pop_size=100000, n_obj=3)
        self.assertEqual(len(weights), 100000)
        for w in weights:
            self.assertEqual(len(w), 3)

    def test_moead_weights_prefers_the_simplex_when_it_fits(self):
        weights = moead_weights(pop_size=5, n_obj=2)
        self.assertEqual(len(weights), 5)
        for w in weights:
            self.assertAlmostEqual(sum(w), 1.0, places=9)


class TestLivenessObligations(unittest.TestCase):
    """Absence is observable: a promised event that never arrives is a fault.

    Every defect this audit found was an expected event that did not occur, so
    the tests care most about the three ways an obligation can be unmet, and
    about the ambiguity the report is built to remove -- a green panel with
    nothing declared is UNKNOWN, not an all-clear.
    """

    def setUp(self):
        lv.liveness_reset(clear_declarations=True)

    def tearDown(self):
        lv.liveness_reset(clear_declarations=True)

    def test_discharged_in_time_is_not_a_miss(self):
        lv.expect("generation.commit", ttl=10.0)
        self.assertTrue(lv.fulfill("generation.commit"))
        self.assertEqual(lv.overdue(), [])

    def test_armed_but_never_discharged_is_unfulfilled(self):
        lv.expect("generation.commit", ttl=0.02, detail="generation 7")
        time.sleep(0.05)
        misses = lv.overdue()
        self.assertEqual([m["kind"] for m in misses], ["unfulfilled"])
        self.assertEqual(misses[0]["detail"], "generation 7")
        self.assertGreater(misses[0]["overdue_by"], 0.0)

    def test_required_but_never_beaten_is_never_beaten(self):
        """The producer never started -- the failure an occurrence monitor
        cannot see, and the reason require() starts its own clock."""
        lv.require("spine.cycle", every=0.02)
        time.sleep(0.05)
        self.assertEqual([m["kind"] for m in lv.overdue()], ["never_beaten"])

    def test_beating_then_stopping_is_silent(self):
        lv.beat("spine.cycle", every=0.02)
        self.assertEqual(lv.overdue(), [], "an on-time beat must not report")
        time.sleep(0.05)
        self.assertEqual([m["kind"] for m in lv.overdue()], ["silent"])

    def test_context_manager_discharges_only_on_success(self):
        with lv.obliged("block.ok", ttl=0.02):
            pass
        self.assertEqual(lv.overdue(), [])

        with self.assertRaises(ValueError):
            with lv.obliged("block.raises", ttl=0.02):
                raise ValueError("boom")
        time.sleep(0.05)
        self.assertEqual([m["kind"] for m in lv.overdue()], ["unfulfilled"])

    def test_grace_scales_the_deadline(self):
        lv.require("slack.cycle", every=0.05)
        time.sleep(0.07)
        first = lv.overdue(grace=1.0)
        second = lv.overdue(grace=1.0)
        # Compare WHAT is reported, not how late it was measured to be. The two
        # polls are microseconds apart, so `overdue_by` advances between them by
        # design; asserting float equality on it made this test measure the
        # clock instead of the ledger and fail under aggregate load (measured
        # 2026-09-22: 0.020 vs 0.021, Error #199).
        self.assertEqual(len(first), 1, "the lateness must still be reported")
        self.assertEqual([(m["name"], m["kind"]) for m in first],
                         [(m["name"], m["kind"]) for m in second],
                         "two polls must agree on what is late")
        self.assertLess(abs(first[0]["overdue_by"] - second[0]["overdue_by"]), 0.05,
                        "two polls apart in time must not disagree wildly")
        self.assertEqual(lv.overdue(grace=10.0), [], "grace must absorb lateness")

    def test_green_with_nothing_declared_is_unknown_not_healthy(self):
        report = lv.liveness_report()
        self.assertTrue(report["healthy"])
        self.assertFalse(report["observable"],
                         "an empty ledger reported as healthy is the bug")
        lv.beat("now.watched", every=60)
        self.assertTrue(lv.liveness_report()["observable"])

    def test_overdue_is_idempotent_so_a_polling_monitor_cannot_be_blinded(self):
        """A miss must be reported on EVERY poll while it is unmet.

        An earlier version consumed it (reported once, then returned clean), so
        a monitor polling every 60s saw the fault once and a healthy board
        afterwards -- output that decays is indistinguishable from a system
        that repaired itself.
        """
        lv.expect("late.commit", ttl=0.02)
        time.sleep(0.05)
        first = lv.overdue()
        second = lv.overdue()
        third = lv.overdue()
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1, "a persisting miss vanished on poll 2")
        self.assertEqual(len(third), 1)
        self.assertEqual(first[0]["name"], second[0]["name"])
        # ...and the miss log still counts misses, not polls.
        self.assertEqual(lv.liveness_report()["misses_total"], 1)

    def test_discharging_clears_the_miss_and_it_stays_cleared(self):
        lv.expect("heal.now", ttl=0.02)
        time.sleep(0.05)
        self.assertEqual(len(lv.overdue()), 1)
        lv.fulfill("heal.now")
        self.assertEqual(lv.overdue(), [])
        self.assertEqual(lv.overdue(), [])

    def test_a_late_discharge_heals_the_recorded_miss(self):
        lv.expect("heal.commit", ttl=0.02)
        time.sleep(0.05)
        lv.overdue()
        self.assertTrue(lv.fulfill("heal.commit"),
                        "a late discharge must be recorded, not dropped")

    def test_unmonitored_names_a_producer_that_stopped_being_armed(self):
        lv.require("gone.cycle", every=0.02)
        lv.LEDGER._required.pop("gone.cycle")
        self.assertIn("gone.cycle", lv.unmonitored())

    def test_garbage_never_raises(self):
        self.assertEqual(lv.expect(None, ttl="not-a-number"), 0.0)
        self.assertIsInstance(lv.beat(object(), every=None), bool)
        self.assertFalse(lv.fulfill(None))
        self.assertIsInstance(lv.liveness_report(), dict)

    def test_concurrent_beats_and_polls_do_not_corrupt_the_ledger(self):
        stop = threading.Event()
        errors = []

        def beater():
            while not stop.is_set():
                try:
                    lv.beat("race.cycle", every=30)
                except Exception as exc:      # pragma: no cover
                    errors.append(exc)

        def poller():
            while not stop.is_set():
                try:
                    lv.overdue()
                    lv.liveness_report()
                except Exception as exc:      # pragma: no cover
                    errors.append(exc)

        threads = ([threading.Thread(target=beater) for _ in range(4)]
                   + [threading.Thread(target=poller) for _ in range(2)])
        for t in threads:
            t.start()
        time.sleep(0.3)
        stop.set()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(lv.overdue(), [],
                         "an on-time beat must never be reported as silent")
        self.assertGreater(lv.liveness_report()["beats_total"], 0)


if __name__ == "__main__":
    unittest.main()
