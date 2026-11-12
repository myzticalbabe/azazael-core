# coding: utf-8
"""Tests for meta_reflect pure core (no model, no store writes)."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "meta_reflect_test", ROOT / "scripts" / "meta_reflect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMetaReflect(unittest.TestCase):
    def test_normalize_case_space(self):
        mod = _load()
        self.assertEqual(mod.normalize("  Hello   WORLD\n"), "hello world")
        self.assertEqual(mod.normalize(""), "")

    def test_validate_accepts_good(self):
        mod = _load()
        ok, reason, rec = mod.validate_candidate(
            {"instruction": "Quarantine test loggers from production logs.",
             "evidence": "3 tmp-path rows", "scope": "unit tests only"}, [])
        self.assertTrue(ok, reason)
        self.assertEqual(rec["scope"], "unit tests only")

    def test_validate_rejects_empty_long_dup(self):
        mod = _load()
        ok, _, _ = mod.validate_candidate({"instruction": "  ", "evidence": "x"}, [])
        self.assertFalse(ok)
        ok, _, _ = mod.validate_candidate(
            {"instruction": "x" * 281, "evidence": "y"}, [])
        self.assertFalse(ok)
        existing = [{"instruction": "Quarantine test loggers."}]
        ok, reason, _ = mod.validate_candidate(
            {"instruction": "  quarantine   TEST loggers. ", "evidence": "z"},
            existing)
        self.assertFalse(ok)
        self.assertIn("duplicate", reason)

    def test_validate_requires_evidence(self):
        mod = _load()
        ok, _, _ = mod.validate_candidate({"instruction": "Do the thing."}, [])
        self.assertFalse(ok)

    def test_gather_missing_lineage_fail_open(self):
        mod = _load()
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            mod.STATE_DIR = Path(td) / "nostate"
            trajs, note = mod.gather_trajectories()
            self.assertEqual(trajs, [])
            self.assertIn("unreadable", note)


if __name__ == "__main__":
    unittest.main()
