"""EP345 — pivotal-turn location and pivotal-aware credit assignment.

The prune half and the prefix-reuse half of EP345 already ship in
`CORE/dispatch_plan.py` (`record_backtrack` prunes a branch and remembers its
lesson; `reuse_prefix` reads it back and reports the isolation rule). These tests
pin the half that was missing from the verbal buffer: locating WHICH turn was
decisive, weighting credit by distance from it, quarantining the suffix, and
proving the new fields are additive for a caller that passes no trajectory.

Repo convention: tests insert src/ themselves (tests/conftest.py does not).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from CORE.WORMS.expansion import reflexion  # noqa: E402


class _FakeStore:
    """Minimal store double: the writer plus the paged reader the module uses."""

    def __init__(self):
        self.rows = []

    def record_event(self, **kwargs):
        self.rows.append(kwargs)
        return True

    def fetch_all_memories(self, event_type=None, limit=500):
        rows = [{"data": dict(r.get("data") or {})} for r in self.rows]
        return rows[-limit:]


# ------------------------------------------------------------ pivotal location

def test_pivotal_turn_is_the_start_of_the_unrecovered_tail():
    turns = [{"ok": True}, {"ok": True}, {"ok": False}, {"ok": False}]
    assert reflexion.locate_pivotal_turn(turns) == 2


def test_recovered_trajectory_has_no_pivotal_turn():
    """A success after the last failure means the run recovered, not that the
    last failure was pivotal."""
    assert reflexion.locate_pivotal_turn([{"ok": True}, {"ok": False},
                                          {"ok": True}]) is None


def test_clean_trajectory_has_no_pivotal_turn():
    assert reflexion.locate_pivotal_turn([{"ok": True}]) is None


def test_empty_and_unreadable_input_never_raises():
    for bad in (None, [], 7, object()):
        assert reflexion.locate_pivotal_turn(bad) is None


def test_untyped_turn_is_not_treated_as_a_failure():
    """A row that says nothing must not manufacture a pivotal turn."""
    assert reflexion.locate_pivotal_turn([{"note": "x"}, {"note": "y"}]) is None


def test_error_keyed_turns_are_located():
    turns = [{"error": None}, {"error": "boom"}, {"error": "boom"}]
    assert reflexion.locate_pivotal_turn(turns) == 1


def test_bare_booleans_are_read_as_turns():
    assert reflexion.locate_pivotal_turn([True, False, False]) == 1


# ------------------------------------------------------- credit assignment

def test_credit_weights_sum_to_one_and_quarantine_the_suffix():
    turns = [{"ok": True}, {"ok": True}, {"ok": True},
             {"ok": False}, {"ok": False}]
    weights = reflexion.pivotal_credit_weights(turns)
    assert len(weights) == len(turns)
    assert abs(sum(weights) - 1.0) < 1e-6
    assert weights[3] == max(weights)          # the pivotal turn carries most
    assert weights[4] == 0.0                   # suffix is quarantined
    assert weights[0] < weights[1] < weights[2] < weights[3]


def test_suffix_quarantine_is_the_numeric_form_of_the_isolation_rule():
    # The rule this pins is `CORE/dispatch_plan.py::reuse_prefix`'s: "suffixes
    # AFTER a pivotal turn are quarantined ... do not blend into the reused
    # prefix". The pivot is the turn whose lesson record_backtrack KEEPS, so the
    # quarantine begins after it -- a trajectory whose last turn is itself the
    # pivot has no suffix to quarantine, which is why this needs a trailing
    # failure beyond the pivot. (The pivot carrying the largest weight is pinned
    # in test_credit_weights_sum_to_one_and_quarantine_the_suffix.)
    turns = [{"ok": True}, {"ok": False}, {"ok": False}]
    weights = reflexion.pivotal_credit_weights(turns)
    assert weights[2] == 0.0      # after the pivot: not blended into the prefix
    assert weights[1] > 0.0       # the pivot: the reusable lesson
    assert weights[0] > 0.0       # before it: reusable prefix
    # Quarantine is exact, not merely small: the suffix contributes nothing to
    # the normalisation, so the reused turns carry the whole distribution.
    assert abs(sum(weights[:2]) - 1.0) < 1e-6


def test_credit_weights_are_empty_when_nothing_is_pivotal():
    assert reflexion.pivotal_credit_weights([{"ok": True}]) == []
    assert reflexion.pivotal_credit_weights(None) == []


def test_out_of_range_pivot_is_refused():
    assert reflexion.pivotal_credit_weights([{"ok": False}], pivotal=9) == []
    assert reflexion.pivotal_credit_weights([{"ok": False}],
                                            pivotal="nonsense") == []


# --------------------------------------------------------- record_reflection

def test_record_reflection_writes_pivotal_fields_when_turns_supplied():
    store = _FakeStore()
    ok = reflexion.record_reflection(
        "q", "failed", "retry", store=store,
        turns=[{"ok": True}, {"ok": False}, {"ok": False}])
    assert ok is True
    data = store.rows[-1]["data"]
    assert data["pivotal_turn"] == 1
    assert data["n_turns"] == 3
    assert data["turns_analysed"] == 3
    assert data["credit"][2] == 0.0


def test_record_reflection_honours_an_explicit_pivotal_turn():
    store = _FakeStore()
    reflexion.record_reflection("q", "failed", "retry", store=store,
                                turns=[{"ok": True}, {"ok": False}],
                                pivotal_turn=0)
    assert store.rows[-1]["data"]["pivotal_turn"] == 0


def test_record_reflection_records_analysis_even_with_no_pivot():
    """Analysed-but-recovered is information: it is distinguishable from a row
    that was never analysed."""
    store = _FakeStore()
    reflexion.record_reflection("q", "failed", "retry", store=store,
                                turns=[{"ok": False}, {"ok": True}])
    data = store.rows[-1]["data"]
    assert data["turns_analysed"] == 2
    assert "pivotal_turn" not in data


def test_record_reflection_row_shape_is_unchanged_without_a_trajectory():
    store = _FakeStore()
    reflexion.record_reflection("q", "failed", "retry", store=store)
    assert set(store.rows[-1]["data"]) == {"question", "what_failed", "what_next"}


# ------------------------------------------------------------ recall ordering

def test_recall_ranks_a_pivoted_reflection_ahead_of_an_unpivoted_one():
    store = _FakeStore()
    reflexion.record_reflection("q", "a", "b", store=store,
                                realised=0.5, predicted=0.5)
    reflexion.record_reflection("q", "c", "d", store=store,
                                realised=0.5, predicted=0.5,
                                turns=[{"ok": True}, {"ok": False}])
    rows = reflexion.reflections_for("q", store=store)
    assert rows[0]["pivotal_turn"] == 1


def test_recall_prefers_the_earlier_relative_pivot():
    store = _FakeStore()
    reflexion.record_reflection("q", "late", "x", store=store,
                                turns=[{"ok": True}, {"ok": True},
                                       {"ok": False}])
    reflexion.record_reflection("q", "early", "y", store=store,
                                turns=[{"ok": False}, {"ok": False},
                                       {"ok": False}])
    rows = reflexion.reflections_for("q", store=store)
    assert rows[0]["what_failed"] == "early"
    assert rows[0]["pivotal_turn"] == 0


def test_recall_keeps_the_old_key_set_for_old_rows():
    store = _FakeStore()
    reflexion.record_reflection("q", "a", "b", store=store)
    row = reflexion.reflections_for("q", store=store)[0]
    assert set(row) == {"question", "what_failed", "what_next",
                        "decision_id", "outcome", "predicted", "realised"}
