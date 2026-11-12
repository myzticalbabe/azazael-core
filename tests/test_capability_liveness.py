# coding: utf-8
"""Tests for tools/capability_liveness.py.

The tool exists because a mechanism can be present, documented, reviewed and
still have never run. So the tool itself has to be trustworthy about the
difference between "never executed" and "has not happened yet" -- otherwise it
becomes a gate that cries wolf, and a gate that cries wolf gets switched off.

These tests pin the discriminator, not just the exit code.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "capability_liveness", ROOT / "tools" / "capability_liveness.py")
cl = importlib.util.module_from_spec(_spec)
sys.modules["capability_liveness"] = cl
_spec.loader.exec_module(cl)


def _state(**kw):
    base = {"homeostat": {}, "q_weights": {}, "q_update_counts": {},
            "q_evidence_floor": {}}
    base.update(kw)
    return base


from datetime import datetime, timedelta, timezone


def _recent_iso():
    # Rolling "recent" (2026-10-06): a PINNED date rots — the staleness
    # window flipped these tests from FAIL to DORMANT the day the pin aged
    # past it. Relative to now, the semantics ("active route") never drift.
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


RECENT = _recent_iso()
OLD = "2025-01-01T00:00:00"


def test_a_never_executed_capability_fails():
    """Route failures with no censored event: the mechanism is not running."""
    st = _state(homeostat={"route:windows>wsl2-ubuntu": {
        "successes": 10, "failures": 50, "censored": 0, "last_at": RECENT}},
        q_update_counts={"route:windows>wsl2-ubuntu": 60})
    status, detail = cl.check_route_censoring(st, since=RECENT)
    assert status == cl.FAIL
    assert "0 censored" in detail


def test_an_executed_capability_passes():
    st = _state(homeostat={"route:windows>wsl2-ubuntu": {
        "successes": 10, "failures": 50, "censored": 7, "last_at": RECENT}},
        q_update_counts={"route:windows>wsl2-ubuntu": 60})
    status, _ = cl.check_route_censoring(st, since=RECENT)
    assert status == cl.OK


def test_history_that_predates_the_fix_is_not_a_live_defect():
    """The whole point of the `unproven` state.

    181 failures recorded before the mechanism was reachable say nothing about
    the code running now. Calling that a FAIL is indistinguishable from a real
    defect, and a gate that cannot tell them apart is a gate that gets muted.
    """
    st = _state(homeostat={"route:windows>wsl2-ubuntu": {
        "successes": 10, "failures": 181, "censored": 0, "last_at": OLD}},
        q_update_counts={"route:windows>wsl2-ubuntu": 191})
    status, _ = cl.check_route_censoring(st, since=RECENT)
    assert status == cl.DORMANT, "pre-fix history was scored as a live defect"


def test_zero_everything_is_dormant_not_failed():
    """No route has ever existed: there is nothing to have failed to censor."""
    status, _ = cl.check_route_censoring(_state(), since=RECENT)
    assert status == cl.DORMANT


def test_activity_with_no_attribution_is_unproven_not_fail():
    """Recent activity, but no way to tell which code produced it."""
    st = _state(homeostat={"route:windows>wsl2-ubuntu": {
        "successes": 10, "failures": 181, "censored": 0, "last_at": RECENT}},
        q_update_counts={"route:windows>wsl2-ubuntu": 191})
    status, detail = cl.check_route_censoring(st, since=None)
    assert status == cl.UNPROVEN
    assert "--since" in detail, "the unproven state must say how to settle it"
    assert status in cl.PASSING, "unproven must not fail the gate"


def test_an_invalidation_restored_above_its_floor_fails():
    st = _state(q_evidence_floor={"route:a>b": 0},
                q_update_counts={"route:a>b": 100})
    status, detail = cl.check_invalidation_persisted(st)
    assert status == cl.FAIL
    assert "restored" in detail


def test_an_honoured_floor_passes():
    st = _state(q_evidence_floor={"route:a>b": 0}, q_update_counts={"route:a>b": 0})
    assert cl.check_invalidation_persisted(st)[0] == cl.OK


def test_a_hardcoded_latency_series_is_caught():
    """The eBPF signature: a real latency series is not periodic."""
    st = _state(homeostat={"route:windows>wsl2-kali": {
        "successes": 10, "failures": 3,
        "hist_ms": [99.2, 8000.0, 98.1, 8000.0, 96.0, 8000.0],
        "last_at": RECENT}})
    status, detail = cl.check_route_ledger_writers(st)
    assert status == cl.FAIL
    assert "8000" in detail


def test_a_real_latency_series_passes():
    st = _state(homeostat={"route:windows>wsl2-kali": {
        "successes": 10, "failures": 3,
        "hist_ms": [99.2, 98.1, 104.5, 96.0, 101.3, 97.7],
        "last_at": RECENT}})
    assert cl.check_route_ledger_writers(st)[0] == cl.OK


def _state_only_rows(state_path):
    """Rows from the LEDGER checks only.

    `main()` also runs `derived_artifacts_fresh`, which reads the real repo and
    is legitimately failing there right now. Asserting the global exit code
    from a unit test would make this file fail for a reason that has nothing to
    do with what it is testing.
    """
    rows = cl.run(state_path)
    return [r for r in rows if r["check"] != "derived_artifacts_fresh"]


def test_a_missing_store_is_not_a_capability_failure(tmp_path):
    """A missing ledger must not be reported as a broken capability."""
    rows = _state_only_rows(tmp_path / "absent.json")
    assert not [r for r in rows if r["status"] == cl.FAIL], \
        "an absent store was reported as a broken capability"


def test_a_healthy_store_produces_no_failing_row(tmp_path):
    state = tmp_path / "s.json"
    state.write_text(json.dumps(_state(homeostat={
        "route:windows>wsl2-kali": {
            "successes": 9, "failures": 1, "censored": 1, "last_at": RECENT,
            "hist_ms": [99.2, 98.1, 104.5, 96.0, 101.3, 97.7]}},
        q_update_counts={"route:windows>wsl2-kali": 10},
        q_evidence_floor={"route:a>b": 0},
        )), encoding="utf-8")
    rows = _state_only_rows(state)
    assert not [r for r in rows if r["status"] == cl.FAIL], rows
