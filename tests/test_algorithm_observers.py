# coding: utf-8
"""The observer worms must read what the producers actually write.

Why this file exists
--------------------
Same defect class as `test_monitor_artifact_paths.py`: a panel that watches a
file nobody produces reports silence as a state. These tests IMPORT the
producer constants (`algorithm_lab_cycle.DEFAULT_OUTPUT`,
`meta_evolution_cycle.REPORT_PATH`) and pin the observer paths to them, so the
two cannot drift. The honesty contract is pinned too: a missing artifact is
`unknown`, never `ok`, and no field is ever a plausible invented number.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_project_root, os.path.join(_project_root, "scripts"),
           os.path.join(_project_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CORE.WORMS.expansion import algorithm_observers as ao  # noqa: E402


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _treerel(mod, path):
    """Path relative to the tree that owns the importing module.

    2026-10-04: the editable install resolves CORE.* to the hub tree while
    top-level script modules resolve to this worktree, so two constants
    imported in one test can name different TREES. The contract under test
    is same-ARTIFACT (observer reads what the producer writes), not
    same-absolute-path -- relativize each side to its own tree and compare
    those. A genuine drift (different relative artifacts) still fails.

    2026-10-30: the tree is found by its REPOSITORY marker first. The old
    `src` + `scripts` heuristic silently picked `src/` for every CORE module,
    because this worktree carries untracked `src/src/` and `src/scripts/`
    directories -- so an observer path relativised to `..\\state\\...` while the
    producer's relativised to `state\\...`, and
    test_algorithm_lab_path_matches_producer failed on its own normalisation
    while both constants named the SAME file. `.git` cannot be faked by a
    stray copy of a directory tree.
    """
    anchor = Path(getattr(mod, "__file__", "") or ".").resolve()
    root = anchor.parent
    for cand in [anchor.parent, *anchor.parents]:
        if (cand / ".git").exists():
            root = cand
            break
    else:
        for cand in [anchor.parent, *anchor.parents]:
            if (cand / "src").is_dir() and (cand / "scripts").is_dir():
                root = cand
                break
    try:
        return os.path.normcase(os.path.relpath(
            os.path.abspath(str(path)), str(root)))
    except ValueError:
        return _norm(path)


def _fresh_iso():
    """A fixture artifact timestamp, fresh at RUN time.

    The observers age by the timestamp INSIDE the artifact ("a frozen copy must
    not look live"), so a fixture stamped with an authoring-date literal goes
    stale the day after it was written — measured 2026-09-27: three parsing
    tests reported 'stale' and never reached the fields they exist to pin.
    Generate the stamp instead of hardcoding it.
    """
    return datetime.now(timezone.utc).isoformat()


def _ago_iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


class TestObserverPathsMatchProducers(unittest.TestCase):
    """The check that cannot drift: import both sides, compare constants."""

    def test_algorithm_lab_path_matches_producer(self):
        import algorithm_lab_cycle

        self.assertEqual(_treerel(ao, ao.ALGORITHM_LAB_STATE),
                         _treerel(algorithm_lab_cycle,
                                  algorithm_lab_cycle.DEFAULT_OUTPUT))

    def test_meta_evolution_path_matches_producer(self):
        import meta_evolution_cycle

        self.assertEqual(_treerel(ao, ao.META_EVOLUTION_STATE),
                         _treerel(meta_evolution_cycle,
                                  meta_evolution_cycle.REPORT_PATH))


class TestObserverHonesty(unittest.TestCase):

    def test_missing_artifact_is_unknown_not_ok(self):
        missing = Path(__file__).parent / "_no_such_state.json"
        for worm in (ao.AlgorithmLabWorm(missing), ao.MetaEvolutionWorm(missing)):
            out = worm.execute()
            self.assertEqual(out["status"], "unknown", out)
            self.assertIsNone(out["at"])
            self.assertIsNone(out["age_min"])

    def test_lab_evidence_is_parsed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "current.json"
            path.write_text(json.dumps({
                "generated_at": _fresh_iso(),
                "policy": [{"algorithm": "REINFORCE", "loss": -0.265},
                           {"algorithm": "RLOO", "loss": -0.075}],
                "diversity": {"map_elites": {"filled": 8, "total_cells": 9,
                                             "coverage": 0.888, "qd_score": 1.01},
                              "novelty_archive": 12,
                              "nsga3": {"input": 8, "selected": 4}},
                "qualification": [
                    {"component": "POLICY_LAB", "status": "served"},
                    {"component": "DIVERSITY_QD_NSGA3", "status": "served"},
                ],
            }), encoding="utf-8")
            out = ao.AlgorithmLabWorm(path).execute()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["components"],
                         {"POLICY_LAB": "served",
                          "DIVERSITY_QD_NSGA3": "served"})
        self.assertEqual(out["policy_rows"], 2)
        self.assertEqual(out["algorithms"], ["REINFORCE", "RLOO"])
        self.assertEqual(out["diversity"]["map_elites"]["filled"], 8)
        self.assertEqual(out["diversity"]["nsga3"], {"input": 8, "selected": 4})

    def test_meta_evo_open_candidate_is_counted_not_hidden(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "current.json"
            path.write_text(json.dumps({
                "at": _fresh_iso(),
                "apply": False,
                "lifecycle": {"history": ["idle", "observe", "select", "blocked"],
                              "state": "blocked"},
                "candidate": {"id": "97ea2fab490ba4f8",
                              "path": "src/CORE/WORMS/expansion/db_monitor.py",
                              "line": 300, "detector": "default_true",
                              "severity": "HIGH",
                              "why": "a missing verification field reads as PASS"},
            }), encoding="utf-8")
            out = ao.MetaEvolutionWorm(path).execute()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["lifecycle"], "blocked")
        self.assertEqual(out["open_candidates"], 1)
        self.assertEqual(out["candidate"]["severity"], "HIGH")
        self.assertFalse(out["apply"])

    def test_truncated_artifact_is_unknown_not_fabricated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "current.json"
            path.write_text(json.dumps({"generated_at": _fresh_iso()}),
                            encoding="utf-8")
            out = ao.AlgorithmLabWorm(path).execute()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["components"], {})
        self.assertIsNone(out["policy_rows"])
        self.assertEqual(out["algorithms"], [])

    def test_stale_lab_evidence_flags_stale(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "current.json"
            path.write_text(json.dumps({
                # Old enough to be stale for every observer window, at any
                # future run date.
                "generated_at": _ago_iso(days=3),
                "policy": [], "diversity": {}, "qualification": [],
            }), encoding="utf-8")
            out = ao.AlgorithmLabWorm(path).execute()
        self.assertEqual(out["status"], "stale")


class TestObserverRegistration(unittest.TestCase):

    def test_both_worms_are_registered(self):
        import importlib

        import CORE.worm_registry as wr
        importlib.import_module("CORE.WORMS.expansion.algorithm_observers")
        for name in ("AlgorithmLabWorm", "MetaEvolutionWorm"):
            self.assertIn(name, wr.WORM_REGISTRY,
                          f"{name} missing from the mesh census")


if __name__ == "__main__":
    unittest.main()
