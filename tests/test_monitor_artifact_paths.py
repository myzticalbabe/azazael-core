# coding: utf-8
"""The monitor's artifact paths must match the paths the cycle actually writes.

Why this file exists
--------------------
Twice, the panel watched a file nobody produces and reported the resulting
silence as a state:

* `proteus_allocator.json` — the cycle writes `proteus_circulation.json`, so
  the allocator's age was never reported and the CIRC row read **"idle" while
  the allocator was actively learning**.
* `logs/morph_history.jsonl` — the cycle writes `state/proteus_morph.jsonl`, so
  the MORPH row reported no data while the artifact existed.

Both are worse than a crash: a missing panel row looks like a healthy idle
subsystem, and this panel exists precisely to stop "absence" being read as
"evidence". Comparing the two modules' real constants is the only check that
cannot drift, so the paths are IMPORTED rather than grepped.
"""
import os
import sys
import unittest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_project_root, os.path.join(_project_root, "scripts"),
           os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import exmachina_live_monitor as lm   # noqa: E402  (panel now has its own viewer)
import evolution_cycle as ec       # noqa: E402


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


class TestMonitorArtifactPaths(unittest.TestCase):

    # (monitor artifact key, cycle constant name)
    PRODUCED = {
        "oracle": "ORACLE_JSON",
        "gate": "ORACLE_LOG",
        "alloc": "CIRCULATION_JSON",
        "promote": "PROMOTE_JSON",
        # The tensor genome is the deliverable artifact; the panel reads it, so
        # its path is locked to the producer like every other watched file.
        "tensor": "TENSOR_GENOME_JSON",
    }

    def _monitor_paths(self):
        return {name: path for name, path in lm._EX_ARTIFACTS}

    def test_every_produced_artifact_resolves_to_the_same_file(self):
        paths = self._monitor_paths()
        for key, const in self.PRODUCED.items():
            self.assertIn(key, paths, "monitor lost artifact key %r" % key)
            produced = getattr(ec, const)
            watched = os.path.join(lm._EX_STATE, paths[key])
            self.assertEqual(
                _norm(watched), _norm(produced),
                "monitor watches %r for %r but the cycle writes %r (%s)"
                % (paths[key], key, produced, const))

    def test_morph_history_path_matches_the_producer(self):
        self.assertEqual(_norm(lm._EX_MORPH), _norm(ec.MORPH_HISTORY),
                         "morph history path drifted from MORPH_HISTORY")

    def test_monitor_and_cycle_share_one_state_directory(self):
        self.assertEqual(_norm(lm._EX_STATE),
                         _norm(os.path.dirname(ec.PROMOTE_JSON)))

    def test_qd_archive_path_matches(self):
        """The QD archive is produced by the cycle, watched by the monitor."""
        paths = self._monitor_paths()
        self.assertIn("archive", paths)
        watched = os.path.join(lm._EX_STATE, paths["archive"])
        produced = getattr(ec, "QD_ARCHIVE_PATH", None)
        if produced is None:
            self.skipTest("cycle exposes no QD_ARCHIVE_PATH")
        self.assertEqual(_norm(watched), _norm(produced))

    def test_no_known_stale_paths_remain_in_the_monitor(self):
        """Regression on the exact two names that caused the silent-idle bug."""
        src = open(os.path.join(_project_root, "scripts",
                                "exmachina_live_monitor.py"),
                   encoding="utf-8").read()
        for stale in ("proteus_allocator.json", "morph_history.jsonl"):
            # The string may still appear in a comment explaining the history.
            for line in src.splitlines():
                if stale in line and not line.strip().startswith("#"):
                    self.fail("stale path %r still used in code: %s"
                              % (stale, line.strip()))

    def test_every_watched_artifact_is_reachable_from_the_cycle(self):
        """No artifact the monitor watches should be a name the cycle never
        mentions: that is exactly the silent-idle shape."""
        cycle_src = open(os.path.join(_project_root, "scripts",
                                      "evolution_cycle.py"),
                         encoding="utf-8").read()
        for name, fn in lm._EX_ARTIFACTS:
            self.assertIn(fn, cycle_src,
                          "monitor watches %r (%s) but the cycle never "
                          "mentions that filename" % (name, fn))

    def test_state_artifacts_are_produced_under_state(self):
        """Guards the class of bug: a path rooted somewhere the producer
        does not write."""
        for path in (ec.CIRCULATION_JSON, ec.MORPH_HISTORY, ec.PROMOTE_JSON,
                     ec.ORACLE_JSON):
            self.assertEqual(_norm(os.path.dirname(path)), _norm(lm._EX_STATE),
                             "%s is not under the shared state dir" % path)


class TestMonitorPathConstantsAreNotDuplicated(unittest.TestCase):
    """The monitor should not re-declare a producer's filename by hand.

    It must reference the filename literal (that is how the panel is wired),
    so the guarantee we can actually give is that the literal equals the
    producer's basename. This test states that explicitly so a future rename
    of one side fails here with a readable message.
    """

    def test_basenames_match_producers(self):
        for key, const in TestMonitorArtifactPaths.PRODUCED.items():
            fn = dict(lm._EX_ARTIFACTS)[key]
            self.assertEqual(fn, os.path.basename(getattr(ec, const)),
                             "%s basename mismatch" % key)

    def test_morph_basename_matches_producer(self):
        self.assertEqual(os.path.basename(lm._EX_MORPH),
                         os.path.basename(ec.MORPH_HISTORY))


if __name__ == "__main__":
    unittest.main()
