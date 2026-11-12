# coding: utf-8
"""Tests for the ask_owner mid-flight clarification channel.

A worm that needs owner input raises NeedsClarification (or calls
ask_owner); the executor normalises to a needs_input outcome instead of a
crash, and the rendezvous is bounded (watchdog-safe) and bus-independent.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from CORE.WORMS.expansion.delegation import (
    ClarificationTimeout,
    NeedsClarification,
    answer_question,
    ask_owner,
    clarification_outcome,
    on_question,
    pending_count,
)


def test_exception_carries_fields_and_truncates():
    exc = NeedsClarification("q" * 2000, decision_id="d-1", worm="w")
    assert exc.decision_id == "d-1"
    assert exc.worm == "w"
    assert len(exc.question) <= 800


def test_roundtrip_across_threads():
    did = "d-roundtrip-1"
    box = {}

    def worker():
        box["answer"] = ask_owner("proceed?", decision_id=did,
                                  worm="w", timeout_s=5.0, bus=False)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.monotonic() + 5.0
    while pending_count() == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert pending_count() == 1
    assert answer_question(did, {"proceed": True}, bus=False) is True
    t.join(timeout=5)
    assert not t.is_alive()
    assert box.get("answer") == {"proceed": True}
    assert pending_count() == 0


def test_ask_owner_timeout_is_fail_closed():
    t0 = time.monotonic()
    with pytest.raises(ClarificationTimeout) as ei:
        ask_owner("which target?", decision_id="d-timeout-1",
                  worm="w", timeout_s=0.3, bus=False)
    assert time.monotonic() - t0 < 5.0
    assert ei.value.decision_id == "d-timeout-1"
    assert pending_count() == 0


def test_ask_owner_zero_timeout_fails_fast():
    with pytest.raises(ClarificationTimeout):
        ask_owner("q?", decision_id="d-zero-1", timeout_s=0, bus=False)
    assert pending_count() == 0


def test_answer_unknown_id_is_false():
    assert answer_question("d-nope-missing", "x", bus=False) is False


def test_outcome_shape_matches_executor_contract():
    exc = NeedsClarification("confirm delete?", decision_id="d-out-1")
    out = clarification_outcome("WormA", exc, 12.5)
    assert out["name"] == "WormA"
    assert out["success"] is False
    assert out["duration_ms"] == 12.5
    assert out["needs_input"] == "confirm delete?"
    assert out["decision_id"] == "d-out-1"
    assert out["timed_out"] is False
    assert "needs owner input" in out["error"]
    assert out["result"]["needs_input"] == "confirm delete?"


def test_timeout_outcome_names_deadline():
    exc = ClarificationTimeout("which?", decision_id="d-out-2", waited_s=0.3)
    out = clarification_outcome("WormB", exc, 300.0)
    assert out["timed_out"] is True
    assert "timed out" in out["error"]
    assert out["result"]["timed_out"] is True


def test_executor_normalises_instead_of_raising():
    from CORE.WORMS.CLUSTERWORMS import (
        ClusterState, WormConfig, WormExecutor, _resolve_worm_dynamic,
    )
    import CORE.WORMS.CLUSTERWORMS as _cw

    def _raise(_module):
        raise NeedsClarification("proceed with reboot?",
                                 decision_id="d-exec-1")

    old = _cw._resolve_worm_dynamic
    _cw._resolve_worm_dynamic = _raise
    try:
        ex = WormExecutor("RebootWorm",
                          WormConfig(name="RebootWorm", module="RebootWorm"),
                          ClusterState("c1"))
        out = ex._execute_direct()
    finally:
        _cw._resolve_worm_dynamic = old
    assert out["success"] is False
    assert out["needs_input"] == "proceed with reboot?"
    assert out["decision_id"] == "d-exec-1"
    assert "needs owner input" in out["error"]


def test_executor_timeout_path_no_retry_no_hang():
    from CORE.WORMS.CLUSTERWORMS import (
        ClusterState, WormConfig, WormExecutor,
    )
    import CORE.WORMS.CLUSTERWORMS as _cw

    def _raise(_module):
        raise ClarificationTimeout("pick one", decision_id="d-exec-2",
                                   waited_s=0.1)

    old = _cw._resolve_worm_dynamic
    _cw._resolve_worm_dynamic = _raise
    try:
        ex = WormExecutor("PickWorm",
                          WormConfig(name="PickWorm", module="PickWorm"),
                          ClusterState("c1"))
        t0 = time.monotonic()
        out = ex._execute_direct()
        assert time.monotonic() - t0 < 10.0
    finally:
        _cw._resolve_worm_dynamic = old
    assert out["timed_out"] is True
    assert out["result"]["timed_out"] is True


def test_on_question_without_bus_is_none():
    assert on_question(lambda _m: None, bus=False) is None


def test_category_boundary_normalises_instead_of_crashing():
    """U3: was ClusterWORMZ._run_worm_method; now _execute_worm_class.

    Same contract (clarification normalises, decision_id preserved, plain
    errors stay errors), flat outcome shape.
    """
    from CORE.WORMS.CLUSTERWORMS import _execute_worm_class

    class _Asking:
        def get_status(self):
            raise NeedsClarification("which scope?",
                                     decision_id="d-nat-1")

    class _Broken:
        def get_status(self):
            raise RuntimeError("boom")

    out = _execute_worm_class("AskWorm", _Asking)
    assert out["success"] is False
    assert out["needs_input"] == "which scope?"
    assert out["decision_id"] == "d-nat-1"
    assert out["method"] == "get_status"
    out = _execute_worm_class("BadWorm", _Broken)
    assert out["success"] is False
    assert "needs_input" not in out
    assert out["error"] == "boom"
