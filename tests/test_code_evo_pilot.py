# coding: utf-8
"""Pilot gate tests: sandbox-only, never touches prod."""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from code_evo_pilot import apply_unified_diff, pilot


def _file(tmp_path, name="m.py", body="a = 1\nb = 2\n"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_clean_diff_applies(tmp_path):
    _file(tmp_path)
    diff = ("--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n a = 1\n-b = 2\n+b = 3\n")
    r = apply_unified_diff(tmp_path, diff)
    assert r == {"applied": ["m.py"], "failed": []}
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "a = 1\nb = 3\n"


def test_bad_context_refuses(tmp_path):
    _file(tmp_path, body="a = 1\nb = 2\n")
    diff = ("--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n a = 1\n-b = 999\n+b = 3\n")
    r = apply_unified_diff(tmp_path, diff)
    assert r["applied"] == [] and r["failed"] == ["m.py"]
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"


def test_escape_and_missing_refused(tmp_path):
    _file(tmp_path)
    r = apply_unified_diff(tmp_path, "--- a/../evil.py\n+++ b/../evil.py\n"
                                     "@@ -1 +1 @@\n-x\n+y\n")
    assert r["failed"] and not r["applied"]
    r = apply_unified_diff(tmp_path, "--- a/nope.py\n+++ b/nope.py\n"
                                     "@@ -1 +1 @@\n-x\n+y\n")
    assert r["applied"] == [] and r["failed"][0].startswith("nope.py")


def test_pilot_rejects_before_copy():
    r = pilot("../outside.py", "nope.diff", "tests/test_x.py")
    assert r["ok"] is False and "escapes" in r["error"]
    r = pilot("src/CORE/web_search.py", "nope.diff", "tests/test_x.py")
    assert r["ok"] is False and "patch" in r["error"]


def test_gate_tests_sandbox_not_prod(tmp_path):
    """The gate must import from the sandbox, never the live tree.

    Regression for 2026-09-26: site .pth entries + conftest's early CORE
    import made the gate silently test prod with the patch unapplied —
    a green gate that proved nothing.
    """
    import sys as _sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
    from code_evo_pilot import run_gate
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pinmod_xyz.py").write_text("X = 42\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pin.py").write_text(
        "def test_pin():\n"
        "    import pinmod_xyz\n"
        "    assert pinmod_xyz.__file__.startswith(r'%s')\n"
        "    assert pinmod_xyz.X == 42\n" % str(tmp_path), encoding="utf-8")
    gate = run_gate(tmp_path, "tests/test_pin.py")
    assert gate["passed"] is True, gate["output"][-1500:]
