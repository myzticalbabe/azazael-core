# coding: utf-8
"""Hermetic pins for tools/test_paradigm_gate.py (Engram #784 rule 1)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import paradigm_gate as gate  # noqa: E402


DIFF = """\
diff --git a/src/CORE/x.py b/src/CORE/x.py
@@ -1,2 +1,5 @@
+def score_pure(a, b):
+    return a + b
+def _private(x):
+    return x
 def existing(y):
-    return y
+    return y * 2
"""


def test_a_failed_git_call_raises_rather_than_reading_as_an_empty_index(monkeypatch):
    """A git failure must not look like 'nothing staged' (a silent pass)."""
    import subprocess as sp

    class _Failed:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(sp, "run", lambda *a, **k: _Failed())
    with pytest.raises(RuntimeError, match="not a git repository"):
        gate._git(["diff", "--cached"])


def test_new_top_level_functions_are_parsed_from_the_diff():
    assert gate.new_functions(DIFF) == ["score_pure", "_private"]


def test_an_existing_function_modified_in_place_is_not_counted_as_new():
    assert "existing" not in gate.new_functions(DIFF)


def test_adding_a_function_without_a_property_test_is_a_violation():
    msgs = gate.evaluate({"src/CORE/x.py": ["score_pure"]}, {"tests/test_x.py": "def test_a(): pass"})
    assert len(msgs) == 1
    assert "score_pure" in msgs[0] and "Rule 1" in msgs[0]


def test_a_staged_property_test_clears_the_violation():
    tests = {"tests/test_x_pbt.py": "from hypothesis import given\n@given(st.integers())\ndef test_p(n): pass"}
    assert gate.evaluate({"src/CORE/x.py": ["score_pure"]}, tests) == []


def test_a_commit_that_adds_no_functions_is_never_a_violation():
    assert gate.evaluate({"src/CORE/x.py": []}, {}) == []
    assert gate.evaluate({}, {}) == []


def test_only_the_property_mark_counts_not_the_word_hypothesis_in_a_comment():
    # The word alone (a comment or docstring) must not satisfy the rule.
    tests = {"tests/test_x.py": "# we considered hypothesis but did not use it\ndef test_a(): pass"}
    assert len(gate.evaluate({"src/CORE/x.py": ["f"]}, tests)) == 1
