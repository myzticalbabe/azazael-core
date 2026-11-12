# coding: utf-8
"""Tombstone enforcement on the WRITE path (2026-09-16).

The tombstone work made deletion durable against disk and against peers, and
enforced it on every MERGE path (both disk loads, the gossip merge, and
``audit_state``). It was not enforced on the WRITE path, which left the purge
holdable against the filesystem and against a peer but not against *this*
process:

    purge_fixtures()        -> key removed, tombstone declared
    record_outcome(key)     -> key re-created in homeostat AND Q
    save_spine_state()      -> the re-created key is written back out

So a purge could report success and the fixture would still be present two
calls later -- the same defect class as the four silent defects of the mesh
audit, located in the store itself: an operation that reports success without
establishing the precondition for it.

Three invariants, and the third is the one that makes the other two worth
having:

1. ``record_outcome`` cannot re-create a purged key (homeostat + Q).
2. The Q kernel cannot either, and ``dream_reinforce`` reaches that kernel
   *without* going through ``record_outcome`` -- so a guard placed only on
   ``record_outcome`` would have missed the offline-learning channel.
3. Removing the guard DOES resurrect the key. Without this, a passing suite
   would only prove that the key happened to stay absent, which is what the
   broken code did too whenever no writer ran.

Refused writes are counted rather than swallowed; a tombstone that keeps
having to refuse writes is a fixture still being exercised.
"""

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE import spine_registry as sr


class _TombstoneCase(unittest.TestCase):
    """A declared fixture key, purged, with every table restored afterwards.

    Uses ``mesh:BadWorm`` because it is in ``_FIXTURE_WORMS`` (so the purge
    recognises it) *and* in ``_FIXTURE_WORMS_WITNESSED`` (so a reader can go
    and find the fixture it came from). ``tests/test_brainstem_playing_field``
    asserts that same invariant independently.
    """

    NATURE = "mesh"
    WORM = "BadWorm"

    def setUp(self):
        self.key = "%s:%s" % (self.NATURE, self.WORM)
        sr.spine_reset()
        sr.record_outcome(self.NATURE, self.WORM, True)
        sr.record_outcome(self.NATURE, self.WORM, True)
        # Preconditions: the key is genuinely present before the purge.
        self.assertIn(self.key, sr._Q_WEIGHTS)
        self.assertIn(self.key, sr._HOMEOSTAT)

    def tearDown(self):
        for table in (sr._Q_WEIGHTS, sr._Q_UPDATE_COUNTS, sr._Q_DREAM_COUNTS,
                      sr._Q_VARIANCE, sr._HOMEOSTAT, sr._PURGED_KEYS,
                      sr._PURGED_BLOCKED_WRITES):
            table.pop(self.key, None)
        with sr._SCAR_LOCK:
            sr._SCARS.pop(self.key, None)

    def assert_absent(self):
        self.assertNotIn(self.key, sr._Q_WEIGHTS)
        self.assertNotIn(self.key, sr._HOMEOSTAT)
        self.assertNotIn(self.key, sr._Q_UPDATE_COUNTS)
        self.assertNotIn(self.key, sr._Q_DREAM_COUNTS)
        self.assertNotIn(self.key, sr._Q_VARIANCE)


class TestWritePathRefusesResurrection(_TombstoneCase):

    def test_purge_removes_the_key_and_declares_a_tombstone(self):
        result = sr.purge_fixtures()
        self.assert_absent()
        self.assertGreaterEqual(result["removed_count"], 1)
        self.assertIn(self.key, sr._PURGED_KEYS)

    def test_record_outcome_cannot_resurrect_a_purged_key(self):
        """The live hot path is the one that actually ran in production."""
        sr.purge_fixtures()
        sr.record_outcome(self.NATURE, self.WORM, True)
        self.assert_absent()

    def test_dream_path_cannot_resurrect_a_purged_key(self):
        """Dreams reach the Q kernel without passing through record_outcome."""
        sr.purge_fixtures()
        sr.dream_reinforce(self.NATURE, self.WORM, 1.0)
        self.assertNotIn(self.key, sr._Q_WEIGHTS)
        self.assertNotIn(self.key, sr._Q_DREAM_COUNTS)

    def test_refusal_is_counted_rather_than_swallowed(self):
        """A silent refusal is indistinguishable from a healthy key."""
        sr.purge_fixtures()
        sr.record_outcome(self.NATURE, self.WORM, True)
        sr.record_outcome(self.NATURE, self.WORM, False)
        counts = sr.purged_blocked_writes()
        self.assertEqual(counts.get(self.key), 2)

    def test_a_non_fixture_key_is_untouched_by_the_guard(self):
        """The guard must be scoped to declared removals, not to a mood."""
        sr.purge_fixtures()
        other = "pftest:OrdinaryWorm"
        try:
            sr.record_outcome("pftest", "OrdinaryWorm", True)
            self.assertIn(other, sr._Q_WEIGHTS)
            self.assertNotIn(other, sr.purged_blocked_writes())
        finally:
            for table in (sr._Q_WEIGHTS, sr._Q_UPDATE_COUNTS, sr._Q_VARIANCE,
                          sr._HOMEOSTAT):
                table.pop(other, None)

    def test_reset_clears_the_refusal_counter(self):
        sr.purge_fixtures()
        sr.record_outcome(self.NATURE, self.WORM, True)
        self.assertTrue(sr.purged_blocked_writes())
        sr.spine_reset()
        self.assertEqual(sr.purged_blocked_writes(), {})
        self.assertEqual(sr._PURGED_KEYS, {})

    def test_tombstone_reaches_disk(self):
        """The durable half: a purge that is not persisted is not a purge."""
        sr.purge_fixtures(persist=True)
        payload = sr._STATE_FILE.read_text(encoding="utf-8")
        self.assertIn("purged_keys", payload)
        self.assertIn(self.WORM, payload)


class TestGuardIsLoadBearing(_TombstoneCase):

    def test_without_the_guard_the_key_comes_back(self):
        """Simulates the pre-fix code by disabling the predicate.

        This is the assertion that distinguishes "the guard works" from "the
        key stayed away on its own". A test that only checked absence would
        pass on the broken source too, whenever no writer happened to run --
        the same mistake as the G6 distinctness test, which passed pre-fix.
        """
        sr.purge_fixtures()
        original = sr._purged_blocked
        sr._purged_blocked = lambda key: False
        try:
            sr.record_outcome(self.NATURE, self.WORM, True)
            self.assertIn(self.key, sr._Q_WEIGHTS,
                          "guard removed but the key did not return: the test "
                          "is not exercising the write path at all")
            self.assertIn(self.key, sr._HOMEOSTAT)
        finally:
            sr._purged_blocked = original
        # And with the guard restored, the same call is refused again.
        sr.record_outcome(self.NATURE, self.WORM, True)
        self.assertIn(self.key, sr._Q_WEIGHTS)  # the resurrected copy remains
        before = sr._Q_UPDATE_COUNTS.get(self.key, 0)
        sr.record_outcome(self.NATURE, self.WORM, True)
        self.assertEqual(sr._Q_UPDATE_COUNTS.get(self.key, 0), before,
                         "a tombstoned key accepted an observation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
