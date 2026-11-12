# coding: utf-8
"""Tests for debate_patch deterministic core (no models)."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "debate_patch_test", ROOT / "scripts" / "debate_patch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDebateCore(unittest.TestCase):
    def test_normalize_ok_and_empty(self):
        mod = _load()
        self.assertEqual(mod.normalize_patch({"old": "a", "new": "b"}), ("a", "b"))
        with self.assertRaises(ValueError):
            mod.normalize_patch({"old": "", "new": "b"})

    def test_patches_equal(self):
        mod = _load()
        self.assertTrue(mod.patches_equal({"old": "a", "new": "b"},
                                          {"old": "a", "new": "b"}))
        self.assertFalse(mod.patches_equal({"old": "a", "new": "b"},
                                           {"old": "a", "new": "c"}))
        # Un-normalizable pair: literal dict equality (identical malformed
        # proposals are converged; differing ones are not).
        self.assertTrue(mod.patches_equal({"old": "", "new": "b"},
                                          {"old": "", "new": "b"}))
        self.assertFalse(mod.patches_equal({"old": "", "new": "b"},
                                           {"old": "", "new": "c"}))

    def test_normalize_lines_form(self):
        mod = _load()
        old, new = mod.normalize_patch(
            {"lines": [1, 2], "new": "Z"},
            "src/CORE/WORMS/expansion/spine.py")
        self.assertTrue(old)
        self.assertEqual(new, "Z")
        with self.assertRaises(ValueError):
            mod.normalize_patch({"lines": [1, 5000], "new": "Z"},
                                "src/CORE/WORMS/expansion/spine.py")
        with self.assertRaises(ValueError):
            mod.normalize_patch({"lines": [1, 2], "new": "Z"})

    def test_agreement_matrix(self):
        mod = _load()
        self.assertEqual(mod.agreement("approve", "APPLY"), "unanimous")
        self.assertEqual(mod.agreement("flawed", "REJECT"), "unanimous")
        self.assertEqual(mod.agreement("approve", "REJECT"), "split")
        self.assertEqual(mod.agreement("flawed", "APPLY"), "split")

    def test_confidence_derived(self):
        mod = _load()
        self.assertEqual(mod.derive_confidence("unanimous"), ("HIGH", 1.0))
        self.assertEqual(mod.derive_confidence("split")[0], "LOW")

    def test_extract_json_skips_prose(self):
        mod = _load()
        out = mod._extract_json('noise {"a": 1} trailing')
        self.assertEqual(out, {"a": 1})
        out = mod._extract_json('{"x": 1}{"x": 2}')
        self.assertEqual(out, {"x": 1})
        with self.assertRaises(ValueError):
            mod._extract_json("no braces here")

    def test_old_occurrences_counts(self):
        mod = _load()
        self.assertGreater(
            mod._old_occurrences("src/CORE/WORMS/expansion/spine.py",
                                 "self._morphogenesis"), 1)
        self.assertEqual(mod._old_occurrences("src/CORE/WORMS/expansion/spine.py",
                                              "no-such-string-xyz"), 0)
        self.assertEqual(mod._old_occurrences("nonexistent.py", "x"), -1)


if __name__ == "__main__":
    unittest.main()
