"""Tests for the spawn-credit join.

The load-bearing test here is `test_reward_is_not_a_function_of_state`: the
existing RL task is degenerate because reward and state share their only input
(severity), which makes the best policy a lookup table. This module exists to
avoid repeating that, so the property is asserted, not assumed.

Run:
    /c/Azazael/.venvs/py315t/Scripts/python.exe -m pytest tests/test_spawn_credit.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.WORMS.expansion.spawn_credit import (  # noqa: E402
    OUTCOME_EVENT,
    SPAWN_EVENT,
    WormOutcome,
    build_spawn_credit,
    new_decision_id,
    read_choices,
    read_decisions,
    read_outcome_decisions,
    read_outcomes,
)


class FakeStore:
    """Minimal stand-in: query_memories(event_type=...) -> newest first."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query_memories(self, event_type=None, limit=50, **kw):
        self.calls.append((event_type, limit))
        out = [r for r in self.rows if r.get("event_type") == event_type]
        return out[:limit]


def row(event_type, data, severity="info"):
    return {"event_type": event_type, "severity": severity,
            "data": json.dumps(data) if isinstance(data, dict) else data}


def outcome(worm, success=True, duration_ms=100.0, error=None, severity="info"):
    return row(OUTCOME_EVENT, {"worm": worm, "success": success,
                               "duration_ms": duration_ms, "error": error},
               severity=severity)


def choice(worm_type, category="diagnostic", priority=5):
    return row(SPAWN_EVENT, {"worm_type": worm_type, "category": category,
                             "priority": priority})


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def test_read_outcomes_aggregates_per_worm():
    store = FakeStore([
        outcome("Alpha", True, 100.0),
        outcome("Alpha", False, 300.0, error="boom"),
        outcome("Beta", True, 200.0),
    ])
    got = read_outcomes(store)

    assert set(got) == {"Alpha", "Beta"}
    assert got["Alpha"].n == 2
    assert got["Alpha"].ok == 1
    assert got["Alpha"].p_success == pytest.approx(0.5)
    assert got["Alpha"].mean_duration_ms == pytest.approx(200.0)
    assert got["Alpha"].n_errors == 1
    assert got["Alpha"].last_error == "boom"
    assert got["Beta"].p_success == pytest.approx(1.0)


def test_read_outcomes_accepts_data_as_dict_or_json_string():
    store = FakeStore([
        row(OUTCOME_EVENT, {"worm": "AsDict", "success": True, "duration_ms": 10}),
        outcome("AsString", True, 20.0),
    ])
    got = read_outcomes(store)
    assert set(got) == {"AsDict", "AsString"}


def test_read_outcomes_skips_rows_without_a_worm():
    store = FakeStore([outcome(None, True), outcome("Real", True)])
    assert set(read_outcomes(store)) == {"Real"}


def test_read_choices_counts_per_worm_type():
    store = FakeStore([
        choice("Alpha"), choice("Alpha"), choice("Beta"),
        row(OUTCOME_EVENT, {"worm": "Alpha", "success": True}),
    ])
    assert read_choices(store) == {"Alpha": 2, "Beta": 1}


def test_read_choices_accepts_legacy_worm_key():
    store = FakeStore([row(SPAWN_EVENT, {"worm": "Legacy"})])
    assert read_choices(store) == {"Legacy": 1}


# --------------------------------------------------------------------------
# Pagination (the store clamps one query to 500 rows)
# --------------------------------------------------------------------------

class PagedStore:
    """Honours limit/offset the way LocalMemoryStore does, including its clamp."""

    def __init__(self, rows, clamp=500):
        self.rows = rows
        self.clamp = clamp

    def query_memories(self, event_type=None, limit=50, offset=0, **kw):
        sel = [r for r in self.rows if r.get("event_type") == event_type]
        return sel[offset:offset + min(limit, self.clamp)]


def test_paginates_past_the_store_clamp():
    """500 of 4286 rows is a 12% sample, not a population. Read all of it."""
    rows = [dict(outcome("Alpha", True, 100.0), id=i) for i in range(1200)]
    got = read_outcomes(PagedStore(rows), limit=20000)
    assert got["Alpha"].n == 1200


def test_detects_a_store_that_ignores_offset():
    """Must report the limitation, not loop forever re-reading page one."""
    class IgnoresOffset:
        def __init__(self, rows):
            self.rows = rows

        def query_memories(self, event_type=None, limit=50, offset=0, **kw):
            sel = [r for r in self.rows if r.get("event_type") == event_type]
            return sel[:limit]

    rows = [dict(outcome("Alpha", True, 100.0), id=i) for i in range(1200)]
    errors = []
    got = read_outcomes(IgnoresOffset(rows), limit=20000, errors=errors)
    assert any("offset" in e for e in errors)
    assert got["Alpha"].n > 0


def test_unpaginated_read_undercounts_where_paginated_does_not():
    """Documents exactly why the clamp matters."""
    rows = [dict(outcome("Alpha", True, 100.0), id=i) for i in range(1200)]
    clamped = read_outcomes(PagedStore(rows, clamp=500), limit=1200)
    full = read_outcomes(PagedStore(rows), limit=20000)
    assert clamped["Alpha"].n == 1200 == full["Alpha"].n  # pagination beats the clamp


# --------------------------------------------------------------------------
# The join
# --------------------------------------------------------------------------

def test_join_merges_both_taps():
    store = FakeStore([
        choice("Alpha"), choice("Alpha"), choice("Ghost"),
        outcome("Alpha", True, 100.0), outcome("Alpha", True, 100.0),
    ])
    credit = build_spawn_credit(store)

    assert credit.n_outcome_rows == 2
    assert credit.n_choice_rows == 3
    assert credit.median_duration_ms == pytest.approx(100.0)
    assert credit.rewards["Alpha"] > 0.4   # always succeeded (2/2), lcb-discounted


def test_join_reports_chosen_but_never_run():
    """A worm selected for a spawn with no execution row is a silent failure.

    Outcome-only accounting would never see it, which is exactly how a spawn
    decision can fail invisibly.
    """
    store = FakeStore([choice("Alpha"), choice("Ghost"), outcome("Alpha", True, 100.0)])
    credit = build_spawn_credit(store)
    assert credit.chosen_but_never_run() == ["Ghost"]


def test_join_is_fail_soft_on_a_broken_store():
    class Exploding:
        def query_memories(self, **kw):
            raise RuntimeError("db gone")

    credit = build_spawn_credit(Exploding())
    assert credit.outcomes == {}
    assert credit.errors, "a broken store must be reported, not swallowed silently"


def test_broken_read_is_distinguishable_from_no_data():
    """The failure this module exists to avoid: 'no data' must never be
    reported when the truth is 'the read broke'."""
    class Exploding:
        def query_memories(self, **kw):
            raise RuntimeError("db gone")

    broken = build_spawn_credit(Exploding())
    empty = build_spawn_credit(FakeStore([]))

    assert broken.outcomes == empty.outcomes == {}
    assert broken.errors and not empty.errors


def test_malformed_rows_are_counted_not_silently_dropped():
    store = FakeStore([
        row(OUTCOME_EVENT, "{not valid json"),
        outcome("Alpha", True, 100.0),
    ])
    credit = build_spawn_credit(store)
    assert set(credit.outcomes) == {"Alpha"}
    assert credit.n_malformed == 1
    assert credit.to_dict()["n_malformed"] == 1


def test_join_handles_store_returning_none_and_missing_store():
    class NoneStore:
        def query_memories(self, **kw):
            return None

    assert build_spawn_credit(NoneStore()).outcomes == {}
    assert build_spawn_credit(None).outcomes == {}


# --------------------------------------------------------------------------
# The reward
# --------------------------------------------------------------------------

def test_unobserved_worm_is_neutral_not_starved():
    """Never-run worms must not be penalised into permanent starvation."""
    o = WormOutcome(worm_type="New", n=0)
    assert o.reward() == 0.0


def test_reward_is_success_dominant():
    good = WormOutcome("Good", n=10, ok=9, mean_duration_ms=100.0)
    bad = WormOutcome("Bad", n=10, ok=1, mean_duration_ms=100.0)
    assert good.reward() > bad.reward()


def test_reward_is_invariant_to_latency_across_worms():
    """REGRESSION GUARD for a defect found by running on live telemetry.

    An earlier revision penalised being slower than the fleet median. Live
    data: fleet median 1.89 ms, while SYSTEMZ/PROCESSZ/DISKZ (86% of all
    executions) average ~700 ms. That ranked the busiest, most reliable
    components at the clamp floor (-1.000) and one-shot probes above them -
    it would have taught the scheduler to starve the core monitors.

    Cross-worm latency must therefore NOT move the reward.
    """
    fast = WormOutcome("Fast", n=4, ok=4, mean_duration_ms=1.9)
    slow = WormOutcome("Slow", n=4, ok=4, mean_duration_ms=700.0)
    assert fast.reward() == slow.reward() > 0.0   # latency must not move it

    # The reliability ranking must survive a large latency spread.
    busy_reliable = WormOutcome("SYSTEMZ", n=1246, ok=1236, mean_duration_ms=700.0)
    one_shot = WormOutcome("Probe", n=1, ok=1, mean_duration_ms=0.5)
    assert busy_reliable.reward() > one_shot.reward()


def test_slow_regression_is_per_worm_and_tolerant():
    """Latency is a drift question, measured against the worm's own baseline."""
    o = WormOutcome("W", n=10, ok=10, mean_duration_ms=250.0)
    assert o.slow_regression(125.0) == 0.0          # exactly 2x tolerance is not a regression
    assert o.slow_regression(100.0, tolerance=1.0) > 0.0
    assert o.slow_regression(0.0) == 0.0            # no baseline -> no claim
    assert WormOutcome("New", n=0).slow_regression(100.0) == 0.0


def test_reward_prices_in_sample_size():
    """Thin evidence must not beat demonstrated reliability."""
    once = WormOutcome("Once", n=1, ok=1)
    proven = WormOutcome("Proven", n=1246, ok=1236)
    assert once.reward() < proven.reward() < 1.0


def test_reward_is_clamped():
    o = WormOutcome("Awful", n=10, ok=0, mean_duration_ms=1e9)
    assert -1.0 <= o.reward() <= 1.0


def test_reward_is_not_a_function_of_state():
    """THE regression guard.

    The severity->action task is unlearnable because its reward's only input is
    already in its state key. Spawn credit must not repeat that: two worms with
    identical outcomes but different severities must score identically, because
    severity is a property of the record stream, not of the worm's performance.
    """
    store_a = FakeStore([outcome("W", True, 100.0, severity="info")])
    store_b = FakeStore([outcome("W", True, 100.0, severity="error")])
    store_c = FakeStore([outcome("W", True, 100.0, severity="critical")])

    ra = build_spawn_credit(store_a).rewards["W"]
    rb = build_spawn_credit(store_b).rewards["W"]
    rc = build_spawn_credit(store_c).rewards["W"]
    assert ra == rb == rc
    assert ra > 0.0


def test_reward_rankings_follow_observed_performance():
    """Sanity: the credit table ranks by what was measured, nothing else."""
    store = FakeStore([
        outcome("Reliable", True, 100.0),
        outcome("Reliable", True, 100.0),
        outcome("Flaky", True, 100.0),
        outcome("Flaky", False, 100.0),
    ])
    rewards = build_spawn_credit(store).rewards
    assert rewards["Reliable"] > rewards["Flaky"]


# --------------------------------------------------------------------------
# The decision-id join (unifying the two taps)
# --------------------------------------------------------------------------

def decision_row(worm_type, decision_id, nature="diagnostic", rank=0):
    return row(SPAWN_EVENT, {"worm_type": worm_type, "decision_id": decision_id,
                             "nature": nature, "rank": rank, "reason": "order"})


def outcome_with_decision(worm, decision_id, success=True, duration_ms=100.0):
    return row(OUTCOME_EVENT, {"worm": worm, "success": success,
                               "duration_ms": duration_ms,
                               "decision_id": decision_id})


def test_new_decision_id_is_unique():
    ids = {new_decision_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(i.startswith("d-") for i in ids)


def test_decision_id_join_is_causal_when_both_sides_carry_it():
    """One decision covering two worms must stay ONE decision with two rows.

    Regression guard: an earlier revision keyed decisions by uuid alone, so the
    last worm of a decision stood in for the whole decision.
    """
    store = FakeStore([
        decision_row("Alpha", "d-1", rank=0),
        decision_row("Beta", "d-1", rank=1),
        outcome_with_decision("Alpha", "d-1", True, 100.0),
        outcome_with_decision("Beta", "d-1", False, 500.0),
    ])
    credit = build_spawn_credit(store)

    assert credit.attribution_mode == "per-decision (causal)"
    assert set(credit.decisions) == {"d-1"}
    assert credit.decisions["d-1"]["n_targets"] == 2      # both worms kept

    joined = credit.joined_decisions["d-1"]
    assert joined["n_chosen"] == 2
    assert joined["n_ran"] == 2
    assert joined["results"]["Beta"]["success"] is False
    assert joined["chosen_but_never_ran"] == []


def test_decision_join_reports_per_decision_silent_failures():
    """A worm chosen and never observed is attributable to a decision now."""
    store = FakeStore([
        decision_row("Alpha", "d-2", rank=0),
        decision_row("Ghost", "d-2", rank=1),
        outcome_with_decision("Alpha", "d-2"),
    ])
    joined = build_spawn_credit(store).joined_decisions["d-2"]
    assert joined["chosen_but_never_ran"] == ["Ghost"]
    assert joined["n_ran"] == 1


def test_decision_join_reports_unexpected_executions():
    store = FakeStore([
        decision_row("Alpha", "d-3"),
        outcome_with_decision("Alpha", "d-3"),
        outcome_with_decision("Intruder", "d-3"),
    ])
    assert build_spawn_credit(store).joined_decisions["d-3"]["unexpected"] == ["Intruder"]


def test_attribution_stays_statistical_without_decision_ids():
    """Legacy rows must not be silently promoted to causal credit."""
    store = FakeStore([choice("Alpha"), outcome("Alpha", True, 100.0)])
    credit = build_spawn_credit(store)
    assert credit.attribution_mode == "per-worm-type (statistical)"
    assert credit.decisions == {} and credit.joined_decisions == {}
    assert "decision_id" in credit.to_dict()["attribution_note"]


def test_a_choice_with_no_matching_outcome_is_not_joined():
    store = FakeStore([
        decision_row("Alpha", "d-4"),
        outcome_with_decision("Alpha", "d-other"),
    ])
    credit = build_spawn_credit(store)
    assert credit.decisions and not credit.joined_decisions
    assert credit.attribution_mode == "per-worm-type (statistical)"


def test_worm_outcome_records_its_decision_ids():
    store = FakeStore([outcome_with_decision("Alpha", "d-1"),
                       outcome_with_decision("Alpha", "d-2")])
    assert read_outcomes(store)["Alpha"].decision_ids == {"d-1", "d-2"}


def test_legacy_outcome_rows_have_no_decision_ids():
    store = FakeStore([outcome("Alpha", True, 100.0)])
    assert read_outcomes(store)["Alpha"].decision_ids == set()


def test_decision_readers_are_fail_soft():
    class Exploding:
        def query_memories(self, **kw):
            raise RuntimeError("db gone")

    errs = []
    assert read_decisions(Exploding(), errors=errs) == {}
    assert read_outcome_decisions(Exploding(), errors=errs) == {}
    assert len(errs) == 2


def test_causal_payload_is_json_serialisable():
    store = FakeStore([decision_row("Alpha", "d-9"),
                       outcome_with_decision("Alpha", "d-9")])
    payload = build_spawn_credit(store).to_dict()
    json.dumps(payload)  # must not raise
    assert payload["attribution"] == "per-decision (causal)"
    assert payload["n_decisions"] == payload["n_joined_decisions"] == 1


def test_payload_is_json_serialisable_and_self_describing():
    store = FakeStore([choice("Ghost"), outcome("Alpha", True, 100.0)])
    payload = build_spawn_credit(store).to_dict()
    json.dumps(payload)  # must not raise

    assert payload["chosen_but_never_run"] == ["Ghost"]
    assert payload["attribution"] == "per-worm-type (statistical)"
    assert "decision_id" in payload["attribution_note"]
