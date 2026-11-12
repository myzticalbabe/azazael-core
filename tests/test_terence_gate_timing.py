# coding: utf-8
"""The `gate_timing` oracle must fire on a tree with no caller and be silent on
a tree with one, and its silence must be the predicate's doing.

Why this file exists. TERENCE ships a negation control for `gate_timing`
(`_control_no_false_gate`, terence_invariants.py:1126) that proves the REFERENCE
TEST `_invokes_gate` can see an `import` and an attribute access and ignores a
docstring. That control passed while the ORACLE it validates was broken: the
oracle guarded its scan with `os.path.isdir(SENTINEL)` on a file path, so the
walk never ran, `referrers` stayed empty, and the critical "no file invokes the
gate" fault was appended on every tree -- including one with real callers. A
control that tests a helper cannot validate the caller that is supposed to use
it. These fixtures test the ORACLE.

Four clauses:
  1. a fixture whose tree contains a real caller is SILENT;
  2. a fixture whose tree has no caller reports exactly the known fault;
  3. a file that merely NAMES the gate in a docstring is not a caller (the
     2026-09-30 false pass, re-pinned at oracle level);
  4. stubbing `_invokes_gate` makes clause 1's fixture report the fault, so
     clause 1 provably rests on the predicate and not on the fixture shape.

The fixtures are synthetic because the oracle's job is a shape, not a path: it
reads two roots (`scripts/`, `src/`) under `AZ` and one existence check on
`SENTINEL`. Both are module globals, so a test can point them at a temp tree
without touching the real one.
"""

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import terence_invariants as ti  # noqa: E402 - after sys.path

FAULT_ID = "gate_fires_only_after_work"


def _build(tmp_path, caller_body=None, *, caller_dir="scripts"):
    """A minimal tree the oracle can walk: the gate file, plus one caller."""
    gate = tmp_path / "src" / "SCRIPTS" / "sentinel_gate.py"
    gate.parent.mkdir(parents=True)
    gate.write_text("def validate_rule_0b_a(*a, **k):\n    return True\n",
                    encoding="utf-8")
    if caller_body is not None:
        target = tmp_path / caller_dir
        target.mkdir(exist_ok=True)
        (target / "runner.py").write_text(caller_body, encoding="utf-8")
    return gate


def _point_at(monkeypatch, tmp_path, gate):
    """The oracle resolves its roots from module globals, not arguments."""
    monkeypatch.setattr(ti, "AZ", str(tmp_path))
    monkeypatch.setattr(ti, "SENTINEL", str(gate))


# -- clause 1: a real caller keeps it quiet ---------------------------------

def test_a_tree_that_invokes_the_gate_reports_nothing(tmp_path, monkeypatch):
    """The defect this pins: the oracle fired here unconditionally."""
    gate = _build(tmp_path, "import sentinel_gate\n\nsentinel_gate.main()\n")
    _point_at(monkeypatch, tmp_path, gate)
    faults, err = ti.oracle_gate_timing()
    assert err is None, err
    assert faults == [], [f["fault_id"] for f in faults]


def test_the_attribute_call_path_also_counts_as_a_caller(tmp_path, monkeypatch):
    """`SentinelValidator()` is how the enforcer actually reaches the gate."""
    gate = _build(tmp_path, "def go():\n    return SentinelValidator(mode='x')\n",
                  caller_dir="src")
    _point_at(monkeypatch, tmp_path, gate)
    faults, err = ti.oracle_gate_timing()
    assert err is None, err
    assert faults == [], [f["fault_id"] for f in faults]


# -- clause 2: no caller is the finding it exists for -----------------------

def test_a_tree_with_no_caller_reports_the_fault(tmp_path, monkeypatch):
    gate = _build(tmp_path, None)
    _point_at(monkeypatch, tmp_path, gate)
    faults, err = ti.oracle_gate_timing()
    assert err is None, err
    assert len(faults) == 1, faults
    fault = faults[0]
    assert fault["fault_id"] == FAULT_ID, fault
    assert fault["klass"] == FAULT_ID, fault
    assert fault["severity"] == "critical", fault
    assert fault["subject"] == str(gate), fault
    # No remedy: wiring an agent tool path is a decision, not a patch.
    assert fault["remedy"] is None, fault


# -- clause 3: a docstring is not a caller ----------------------------------

def test_a_docstring_naming_the_gate_is_not_a_caller(tmp_path, monkeypatch):
    """The exact 2026-09-30 false pass: prose consumed as integration."""
    gate = _build(
        tmp_path,
        '"""Designed for integration with azazael/src/SCRIPTS/sentinel_gate.py."""\n'
        "x = 1\n")
    _point_at(monkeypatch, tmp_path, gate)
    faults, err = ti.oracle_gate_timing()
    assert err is None, err
    assert [f["fault_id"] for f in faults] == [FAULT_ID], faults


# -- clause 4: the silence is load-bearing -----------------------------------

def test_stubbing_the_reference_test_makes_the_caller_tree_fire(
        tmp_path, monkeypatch):
    """Remove the predicate and clause 1's fixture must stop being silent.

    Without this, clause 1 could pass because the fixture fails to parse, or
    because the walk never reached the file -- which is exactly the bug this
    file was written to catch.
    """
    gate = _build(tmp_path, "import sentinel_gate\n\nsentinel_gate.main()\n")
    _point_at(monkeypatch, tmp_path, gate)
    monkeypatch.setattr(ti, "_invokes_gate", lambda *a, **k: (False, "stubbed"))
    faults, err = ti.oracle_gate_timing()
    assert err is None, err
    assert [f["fault_id"] for f in faults] == [FAULT_ID], faults


# -- the guard that hid all of the above ------------------------------------

def test_the_existence_guard_tests_the_gate_file_not_a_directory(
        tmp_path, monkeypatch):
    """`os.path.isdir` on a file path is False, so the scan was unreachable.

    A missing gate must still be reported (the oracle has nothing to point at),
    but its presence must be decided by `isfile`, which is what makes the
    clauses above reachable at all.
    """
    gate = _build(tmp_path, "import sentinel_gate\n")
    _point_at(monkeypatch, tmp_path, gate)
    assert Path(ti.SENTINEL).is_file(), "fixture must point at a file"
    faults, err = ti.oracle_gate_timing()
    assert err is None, err
    assert faults == [], "a file path must not be treated as a directory"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
