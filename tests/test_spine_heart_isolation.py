"""Regression tests for the spine-heart test-isolation fixture.
import pytest

pytestmark = pytest.mark.order

WHY THESE TWO TESTS ARE ORDERED AND NAMED THIS WAY
--------------------------------------------------
The defect they pin is a *cross-module* leak: `startup_warm.warm_caches_now()`
starts a daemon heart thread, and a heartbeat holds the process-global cycle
gate (`_GLOBAL_CYCLE_GATE`) for the duration of a beat. That refusal happens
inside `run_cycle()` *before* the min-interval throttle, so the refused cycle
returns without setting `last_run_at`.

Observed failure, before the `_quiesce_spine_heart` fixture existed in
`conftest.py`:

    pytest tests/test_startup_warm_wiring.py tests/test_spine_mesh.py ...
    FAILED tests/test_spine_mesh.py::test_spine_cycle_throttle_and_reentry_guard
      assert second.get("skipped") == "min_interval"   # got None

`test_spine_mesh.py` alone passed (63 passed), so the failure depended on what
ran before it -- the defining signature of leaked process-wide state.

The two tests below are deliberately ordered: the first starts a heart and
asserts it is alive, the second asserts it is already gone. The second can only
pass if the autouse fixture is doing its job, so it cannot be satisfied by
accident or by a test that simply never started one. They live in their own
module rather than in `test_spine_mesh.py` so the leak is reproduced across the
module boundary that the real failure crossed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# `src` must be on sys.path explicitly: a POSIX-style PYTHONPATH does not reach
# the Windows interpreter, so the suite's convention is that each test module
# inserts it itself. Without this the imports below fail with
# ModuleNotFoundError while the same PYTHONPATH appears to work on the command
# line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.mark.slow  # starts a LIVE heart (daemon torch cycles): solo process.
def test_heart_started_by_this_test_is_alive(monkeypatch):
    """A heart we start here must actually be running at the end of this test."""
    from CORE import startup_warm as sw

    # Opt out of the suite-wide heart pen (root conftest sets
    # AZAZAEL_NO_HEART=1): this test's whole point is a LIVE heart.
    monkeypatch.delenv("AZAZAEL_NO_HEART", raising=False)
    assert sw.start_spine_heart(interval=30.0) is True
    assert sw._SPINE_HEART_ALIVE.is_set(), "the heart should be beating"
    assert sw.spine_heart_status()["beating"] is True


def test_heart_from_the_previous_test_was_stopped():
    """Runs AFTER the test above; only the autouse fixture can make this true.

    If this fails, a heartbeat survived into the next test and would have been
    able to refuse a cycle with `cycle_already_running_global` -- which is what
    made `test_spine_cycle_throttle_and_reentry_guard` flaky.
    """
    from CORE import startup_warm as sw

    assert sw._SPINE_HEART_ALIVE.is_set() is False, (
        "a spine heartbeat leaked out of the previous test; the "
        "_quiesce_spine_heart fixture in tests/conftest.py is not working")
    assert sw.spine_heart_status()["beating"] is False


def test_cycle_gate_is_free_so_a_cycle_can_actually_run():
    """End-to-end shape of the original failure: the gate must be acquirable.

    This is the assertion the failing test really needed. If a leaked heartbeat
    held the gate, `run_cycle` would return `cycle_already_running_global`
    instead of running, and every downstream throttle assertion would misfire.
    """
    from CORE.WORMS.expansion.spine import SpineCycleWorm

    cyc = SpineCycleWorm(min_interval_seconds=0.0)
    rep = cyc.run_cycle(apply=False)
    assert rep.get("skipped") != "cycle_already_running_global", (
        "the process-global cycle gate was held by a leaked heartbeat")
    assert rep["success"] is True
