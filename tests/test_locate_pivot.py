# coding: utf-8
"""Tests for dispatch_plan.locate_pivot — EP345 pivotal-turn identification.

reuse_prefix remembers lessons; locate_pivot says WHERE the backtrack
point is: first failing turn splits the reusable prefix from the
quarantined suffix. Pure function, hermetic.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from CORE.dispatch_plan import locate_pivot


def test_first_failure_is_pivot():
    """Branches before the first failure are prefix; from it on is suffix."""
    out = locate_pivot([
        {"branch": "read", "ok": True},
        {"branch": "plan", "ok": True},
        {"branch": "edit", "ok": False},
        {"branch": "verify", "ok": False},
    ])
    assert out["found"] is True
    assert out["pivot_index"] == 2
    assert out["pivot_branch"] == "edit"
    assert out["reusable_prefix"] == ["read", "plan"]
    assert out["quarantined_suffix"] == ["edit", "verify"]
    assert out["reason"] == "first-failure"


def test_failure_at_zero_empties_prefix():
    """A first-turn failure means nothing is reusable."""
    out = locate_pivot([{"branch": "read", "ok": False}])
    assert out["found"] is True
    assert out["pivot_index"] == 0
    assert out["reusable_prefix"] == []
    assert out["quarantined_suffix"] == ["read"]


def test_all_ok_has_no_pivot():
    """No failure, no pivot: the whole run is reusable prefix."""
    out = locate_pivot([{"branch": "a", "ok": True}, {"branch": "b", "ok": True}])
    assert out["found"] is False
    assert out["pivot_index"] is None
    assert out["reusable_prefix"] == ["a", "b"]
    assert out["quarantined_suffix"] == []
    assert out["reason"] == "all-ok"


def test_empty_input_unlocatable():
    """Empty turn list is unlocatable, never a pivot at zero."""
    out = locate_pivot([])
    assert out["found"] is False
    assert out["reason"] == "no-turns"


def test_missing_ok_defaults_true():
    """Turns without an ok flag read as successes (fail-closed toward ok)."""
    out = locate_pivot([{"branch": "a"}, {"branch": "b", "ok": False}])
    assert out["found"] is True
    assert out["pivot_index"] == 1
    assert out["reusable_prefix"] == ["a"]
