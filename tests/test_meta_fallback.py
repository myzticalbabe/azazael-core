# coding: utf-8
"""Tests for proposal fallback (primary unusable -> second model, once)."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def _load_cycle():
    spec = importlib.util.spec_from_file_location(
        "meta_evolution_cycle_fb", ROOT / "scripts" / "meta_evolution_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCand:
    path = "x.py"
    line = 1
    detector = "t"
    severity = "MED"
    code = "x"
    why = "t"


class TestFallback(unittest.TestCase):
    def test_primary_valid_no_fallback(self):
        mod = _load_cycle()
        calls = []
        good = {"ok": True, "patch": {"subs": {"x.py": [["a", "b"]]}}}
        mod._ask_model = lambda c, m, timeout=600, pattern=None: (calls.append(m), good)[1]
        real_validate = mod.me.validate_substitution_patch
        mod.me.validate_substitution_patch = lambda root, p: ("x.py", "a", "b")
        try:
            out = mod._propose_with_fallback(FakeCand(), "m1")
        finally:
            mod.me.validate_substitution_patch = real_validate
        self.assertTrue(out["ok"])
        self.assertEqual(calls, ["m1"])
        self.assertEqual(len(out["attempts"]), 1)

    def test_invalid_primary_runs_debate(self):
        import types
        mod = _load_cycle()
        bad = {"ok": True, "patch": {"subs": {}}}
        mod.me.validate_substitution_patch = lambda root, p: (_ for _ in ()).throw(
            mod.me.MetaEvolutionError("empty")) if p is bad["patch"] else ("x", "a", "b")
        mod._ask_model = lambda c, m, timeout=600, pattern=None: dict(bad)
        fake_debate = types.ModuleType("debate_patch")
        fake_debate.run_debate = lambda finding, timeout=590: {
            "patch": {"subs": {"x.py": [["a", "b"]]}},
            "agreement": "unanimous", "confidence": "HIGH", "rounds": 1,
            "verdict": {"decision": "APPLY"}}
        sys.modules["debate_patch"] = fake_debate
        try:
            out = mod._propose_with_fallback(FakeCand(), "primary")
        finally:
            del sys.modules["debate_patch"]
        self.assertTrue(out["ok"])
        self.assertEqual(out["patch"], {"subs": {"x.py": [["a", "b"]]}})
        self.assertEqual(len(out["attempts"]), 2)
        self.assertEqual(out["attempts"][1]["model"], "debate")
        self.assertEqual(out["attempts"][1]["agreement"], "unanimous")

    def test_debate_rejection_reports_attempts(self):
        import types
        mod = _load_cycle()
        mod._ask_model = lambda c, m, timeout=600, pattern=None: {"ok": False, "error": "down"}
        fake_debate = types.ModuleType("debate_patch")
        fake_debate.run_debate = lambda finding, timeout=590: {
            "patch": None, "agreement": "split", "confidence": "LOW",
            "rounds": 2, "verdict": {"decision": "REJECT", "reason": "weak"}}
        sys.modules["debate_patch"] = fake_debate
        try:
            out = mod._propose_with_fallback(FakeCand(), "primary")
        finally:
            del sys.modules["debate_patch"]
        self.assertFalse(out["ok"])
        self.assertGreaterEqual(len(out["attempts"]), 2)

    def test_uniqueness_warning_in_prompt(self):
        import types
        mod = _load_cycle()
        seen = {}

        def fake_ask(c, m, timeout=600):
            import urllib.request as _u
            seen["done"] = True
            return {"ok": False, "error": "stop-after-prompt"}

        # Capture the prompt by patching urlopen to fail fast with capture.
        import urllib.request
        real_urlopen = urllib.request.urlopen

        def spy(req, timeout=600):
            seen["body"] = req.data.decode("utf-8")
            raise OSError("spy-stop")
        urllib.request.urlopen = spy
        try:
            cand = mod.me.Candidate(
                path="src/CORE/WORMS/expansion/exmachina.py", line=1379,
                detector="count_zero_tie", severity="HIGH", code="if n == 0:",
                why="t", source_hash="t")
            out = mod._ask_model(cand, "m1", timeout=5)
        finally:
            urllib.request.urlopen = real_urlopen
        self.assertFalse(out["ok"])
        # 'if n == 0:' occurs more than once -> warning must be present.
        import json
        body = json.loads(seen["body"])
        user = body["messages"][1]["content"]
        self.assertIn("occurs", user)
        self.assertIn("exactly once", user)


class TestContextScope(unittest.TestCase):
    def test_enclosing_function_scope(self):
        mod = _load_cycle()
        cand = {"path": "src/CORE/WORMS/expansion/exmachina.py", "line": 1379}
        cand = mod.me.Candidate(
            path=cand["path"], line=cand["line"], detector="count_zero_tie",
            severity="HIGH", code="if n == 0:", why="t", source_hash="t")
        ctx = mod._context_for(cand)
        self.assertIn("def _tpm", ctx)
        self.assertLessEqual(len(ctx.splitlines()), 200)
        # The tie site no longer lives inside the TPM helper (refactor moved
        # it out); pin it with its own candidate so this check stays live.
        tie = mod.me.Candidate(
            path="src/CORE/WORMS/expansion/exmachina.py", line=1496,
            detector="count_zero_tie",
            severity="HIGH", code="max(candidates", why="t", source_hash="t")
        ctx2 = mod._context_for(tie)
        self.assertIn("max(candidates", ctx2)
        self.assertLessEqual(len(ctx2.splitlines()), 200)

    def test_module_level_target_keeps_radius(self):
        mod = _load_cycle()
        cand = mod.me.Candidate(
            path="src/CORE/WORMS/expansion/exmachina.py", line=10,
            detector="t", severity="t", code="t", why="t", source_hash="t")
        ctx = mod._context_for(cand, radius=8)
        self.assertLessEqual(len(ctx.splitlines()), 8 + 8 + 1 + 2)


if __name__ == "__main__":
    unittest.main()
