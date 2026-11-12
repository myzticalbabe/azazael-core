# coding: utf-8
"""
Accuracy/regression tests for the evolution subsystem (fixed 2026-09-08).

  1. Reward model accuracy  - _compute_reward matches the documented
                              severity x action table + success/duration rules.
  2. persist=False          - GA eval runs add zero rows to the store and
                              register no models (was ~96 rows per cycle).
  3. Import safety          - importing evolution_cycle does not run the GA.
  4. Promotion path         - apply_evolved_overrides resolves to the same
                              config/ file evolution_cycle.py writes to.

Run:  python -m pytest src/tests/test_evolution_accuracy.py -v
   or: python -m unittest src.tests.test_evolution_accuracy -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Shared scratch dir (same fixed path as test_learning_loop.py) so the
# process-global store singleton binds to ONE db across the whole suite.
# NOTE: AZAZAEL_MEMORY_DIR is a *directory* in production
# (local_memory._get_store_path mkdirs it and appends memory_store.db),
# so the scratch var must be a dir and the db a file inside it —
# passing the bare path as db_path makes sqlite fail with
# "unable to open database file" (2026-09-08 fix).
_SCRATCH_DIR = str(Path(tempfile.gettempdir()) / "azazael_test_scratch_shared")
_SCRATCH = str(Path(_SCRATCH_DIR) / "memory_store.db")
os.environ["AZAZAEL_MEMORY_DIR"] = _SCRATCH_DIR

from CORE.local_memory import get_memory_store  # noqa: E402
from CORE.WORMS.expansion.learning_rl import RLTrainLoop, _compute_reward  # noqa: E402
from typing import List
import inspect

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here

STORE = get_memory_store()


def setUpModule():
    """Wipe the shared scratch DB so persisted metacog state from a previous
    run (or another module) can't leak into these tests."""
    import CORE.local_memory as _lm
    _lm._global_store = None
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(_SCRATCH + suffix)
        except OSError as exc:
            note_failure("test_evolution_accuracy:L51", exc)
    _lm._global_store = _lm.LocalMemoryStore(db_path=_SCRATCH)
    globals()["STORE"] = _lm._global_store

# Expected reward table (documented in learning_rl.py)
REWARD_TABLE = {
    ("error", 3): 1.0, ("error", 2): 0.4, ("error", 1): -0.3, ("error", 0): -1.0,
    ("warning", 3): 0.2, ("warning", 2): 0.7, ("warning", 1): 0.0, ("warning", 0): -0.7,
    ("info", 3): -0.5, ("info", 2): -0.3, ("info", 1): 0.3, ("info", 0): 0.2,
}


class TestRewardModelAccuracy(unittest.TestCase):
    """Reward must be exact per (severity, action) — the GA optimizes it."""

    def test_all_severity_action_pairs_exact(self):
        for (sev, act), want in REWARD_TABLE.items():
            got = _compute_reward({"severity": sev}, act)
            self.assertAlmostEqual(got, want, places=9,
                                   msg=f"{sev}/{act} reward off")

    def test_critical_severity_is_mapped_not_flat(self):
        # UPDATED 2026-10-10: "critical" was unmapped/flat when this test was
        # written, but 2026-09-30 evidence (db_health_alert pool: 7/7 rows are
        # `critical`) mapped it explicitly — reward {3:1.4, 2:0.7, 1:-0.5,
        # 0:-1.4}. The old assertion (all four actions = 0.0) now asserted
        # AGAINST the deliberate fix. Assert the real spread instead: argmax
        # stays remediate(3), and the absolute scale exceeds `error`'s row.
        self.assertEqual(_compute_reward({"severity": "critical"}, 3), 1.4)
        self.assertEqual(_compute_reward({"severity": "critical"}, 2), 0.7)
        self.assertEqual(_compute_reward({"severity": "critical"}, 1), -0.5)
        self.assertEqual(_compute_reward({"severity": "critical"}, 0), -1.4)

    def test_genuinely_unmapped_severity_is_flat(self):
        # Unmapped severities still collapse to 0.0 with the neutral row
        # (and are recorded via note_unmapped_severity — evidence, not silence).
        for act in range(4):
            self.assertEqual(
                _compute_reward({"severity": "definitely_not_mapped_b拉斯"}, act), 0.0)

    def test_success_refinement_rules(self):
        self.assertAlmostEqual(
            _compute_reward({"severity": "error", "data": {"success": True}}, 3), 1.3)
        self.assertAlmostEqual(
            _compute_reward({"severity": "error", "data": {"success": False}}, 2), 0.0)
        self.assertAlmostEqual(
            _compute_reward({"severity": "error", "data": {"success": False}}, 3), 0.6)
        self.assertAlmostEqual(
            _compute_reward({"severity": "info", "data": {"success": True}}, 1), 0.4)
        self.assertAlmostEqual(
            _compute_reward({"severity": "info", "data": {"success": True}}, 0), 0.3)

    def test_duration_penalty(self):
        # 1000 ms shaves 0.1 off any reward
        self.assertAlmostEqual(
            _compute_reward({"severity": "error", "data": {"duration_ms": 1000}}, 3), 0.9)
        self.assertAlmostEqual(
            _compute_reward({"severity": "info", "data": {"duration_ms": 500}}, 1), 0.25)

    def test_argmax_matches_documented_best_action(self):
        # The policy the Q-learner must converge to
        for sev, want_act in (("error", 3), ("warning", 2), ("info", 1)):
            best = max(range(4), key=lambda a: _compute_reward({"severity": sev}, a))
            self.assertEqual(best, want_act, f"{sev} argmax wrong")


def seed_records(source="SystemZ", n=6):
    for i in range(n):
        STORE.record_event(
            source=source, event_type="monitor",
            severity="error" if i % 2 == 0 else "warning",
            data={"duration_ms": 20, "success": False}, tags=["test"])


class TestPersistFalseNoWrite(unittest.TestCase):
    """GA eval runs (persist=False) must not write back to the store."""

    @classmethod
    def setUpClass(cls):
        seed_records()  # train_from_memory returns early with zero records

    def _count_training_rows(self):
        # count_memories, not len(query_memories(limit=5000)): the read API
        # clamps at MAX_QUERY_LIMIT (500) and would compare two capped samples
        # instead of two true counts, so this no-write assertion could pass on
        # a truncated view.
        return STORE.count_memories(
            source="RLTrainLoop", event_type="training_complete")

    def test_no_training_complete_rows_written(self):
        before = self._count_training_rows()
        loop = RLTrainLoop()
        loop.train_from_memory(n_episodes=3, max_steps=10, persist=False)
        after = self._count_training_rows()
        self.assertEqual(after, before, "persist=False wrote training_complete rows")

    def test_default_persists(self):
        # Default persist=True must keep the old behavior (callers rely on it)
        before = self._count_training_rows()
        loop = RLTrainLoop()
        loop.train_from_memory(n_episodes=3, max_steps=10, persist=True)
        after = self._count_training_rows()
        self.assertGreater(after, before, "default persist=True did not write")


class TestImportSafety(unittest.TestCase):
    """evolution_cycle must be importable without running the GA."""

    def test_import_does_not_run_ga(self):
        import scripts.evolution_cycle as evc
        self.assertTrue(callable(evc.fitness_genes))
        self.assertTrue(callable(evc.main))
        self.assertTrue(hasattr(evc, "PROMOTE_MARGIN"))
        # helpers present means the module loaded; no GA ran (would have
        # recorded an evolution_result artifact into the scratch store)
        rows = STORE.query_memories(event_type="evolution_result", limit=5)
        self.assertEqual(len(rows), 0, "import triggered a GA run")

    def test_genome_seed_deterministic(self):
        import scripts.evolution_cycle as evc
        genes = {"learning_rate": 0.3, "epsilon": 0.1, "discount": 0.9}
        self.assertEqual(evc.genome_seed(genes, "a"), evc.genome_seed(genes, "a"))
        self.assertNotEqual(evc.genome_seed(genes, "a"), evc.genome_seed(genes, "b"))


class TestPromotionPath(unittest.TestCase):
    """apply_evolved_overrides must read the same file evolution writes."""

    def test_paths_agree(self):
        import scripts.evolution_cycle as evc
        from CORE.WORMS.expansion import scheduling

        write_path = Path(evc.PROMOTE_JSON).resolve()
        # Extract the read path from apply_evolved_overrides source
        src = inspect.getsource(scheduling.ScheduleConfig.apply_evolved_overrides)
        # it builds p from _Path(__file__) parents; just verify resolution matches
        cfg = scheduling.ScheduleConfig()
        # When no file exists in scratch, it must return False (not crash)
        self.assertIsInstance(cfg.apply_evolved_overrides(), bool)
        # UPDATED 2026-10-10: evolution_cycle.PROMOTE_JSON moved config/ →
        # state/ on 2026-09-10 (audit EV-1/D-1: a git restore silently
        # reverted production genes from the tracked config/ copy). The
        # writer is now <root>/state/, and apply_evolved_overrides reads
        # state/ first with config/ as legacy fallback. Assert agreement
        # with the CURRENT contract, not the pre-move one.
        self.assertEqual(write_path.parent.name, "state")
        self.assertEqual(write_path.name, "evolved_rl_params.json")
        # The consumer's primary read path resolves to <root>/state/...,
        # the same dir the writer now uses (both derive from module roots,
        # so this asserts they agree structurally).
        primary_read = (Path(scheduling.__file__).resolve()
                        .parent.parent.parent.parent.parent / "state"
                        / "evolved_rl_params.json")
        self.assertEqual(primary_read, write_path,
                         "apply_evolved_overrides reads a different path than evolution writes")


def tearDownModule():
    # Reset the process-global store singleton so the next test module in the
    # suite rebinds to its own scratch dir (first-import wins otherwise, and
    # the loser's tests hit "unable to open database file" after the winner
    # deletes its scratch).
    try:
        STORE.close() if hasattr(STORE, "close") else None
    except Exception as exc:
        note_failure("test_evolution_accuracy:L193", exc)
    try:
        import CORE.local_memory as _lm
        _lm._global_store = None
    except Exception as exc:
        note_failure("test_evolution_accuracy:L198", exc)
    # Do NOT rmtree the shared scratch dir here: another test module in the
    # suite may still be using it. OS temp cleans it up eventually.


if __name__ == "__main__":
    unittest.main(verbosity=2)