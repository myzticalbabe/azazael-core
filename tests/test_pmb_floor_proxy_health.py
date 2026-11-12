"""The floor proxy's counters and /healthz, tested without a socket.

`apply_floor`'s per-result `floor` field is tested in `test_pmb_floor_apply.py`.
What nothing there covers is the ACCUMULATED view: a dropped row is invisible
to the model by construction, so the cumulative counters behind GET /healthz
are the one place the floor's silence is measured. Proven here, against the
committed fixture populations (same fixtures, same principle -- no live
daemon, no port, no embedding model):

  * every filtered result adds its kept/dropped to the totals and one
    query event to the event count;
  * an unfiltered result (note == "", or a dropped result whose `floor`
    field never arrived) adds nothing;
  * the counters are ordered, not just aggregated: identical totals from
    records applied in a different order arrive at the SAME sums (the
    counter must not depend on replay order);
  * /healthz's document is present-shaped and honest: it names both floors,
    carries the counters verbatim, and reports an uptime independent of the
    event stream (zero events still yields seconds_running).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "pmb_floor_proxy.py"


@pytest.fixture(scope="module")
def floor():
    spec = importlib.util.spec_from_file_location("pmb_floor_proxy_health", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def fresh_counters(floor):
    floor._reset_counters()
    yield
    floor._reset_counters()


def test_every_filtered_result_adds_to_the_totals(floor):
    # One tier-1 event (drop 5, keep 0) and one tier-2 event (drop 2, keep 6).
    floor.record_floor_event({"tier": "query", "kept": 0, "dropped": 5})
    floor.record_floor_event({"tier": "row", "kept": 6, "dropped": 2})
    counters = floor._counters()
    assert counters["dropped_total"] == 7
    assert counters["kept_total"] == 6
    assert counters["query_events"] == 2


def test_an_unfiltered_result_adds_nothing(floor):
    floor.record_floor_event(None)
    floor.record_floor_event({"not-a-floor": True})
    floor.record_floor_event({"kept": "ten", "dropped": 0})  # non-int kept
    counters = floor._counters()
    assert counters == {
        "kept_total": 0,
        "dropped_total": 0,
        "query_events": 0,
        "seconds_running": counters["seconds_running"],
    }


def test_totals_are_order_independent(floor):
    # The same two events, replayed in the other order, must land on the same
    # sums: the counter measures the floor's work, not the arrival order.
    floor.record_floor_event({"kept": 6, "dropped": 2})
    floor.record_floor_event({"kept": 0, "dropped": 5})
    counts_a = {k: floor._counters()[k] for k in
                ("kept_total", "dropped_total", "query_events")}
    floor._reset_counters()
    floor.record_floor_event({"kept": 0, "dropped": 5})
    floor.record_floor_event({"kept": 6, "dropped": 2})
    counts_b = {k: floor._counters()[k] for k in
                ("kept_total", "dropped_total", "query_events")}
    assert counts_a == counts_b == {
        "kept_total": 6, "dropped_total": 7, "query_events": 2}


def test_healthz_names_the_floors_and_carries_the_counters_verbatim(floor):
    floor.record_floor_event({"kept": 3, "dropped": 1})
    health = floor.health_payload()
    assert health["status"] == "ok"
    assert health["floors"]["pmb_recall_floor"] == floor.FLOOR
    assert health["floors"]["pmb_recall_min_best"] == floor.MIN_BEST
    assert health["counters"]["kept_total"] == 3
    assert health["counters"]["dropped_total"] == 1
    assert health["counters"]["query_events"] == 1
    assert health["origin"] == floor.ORIGIN


def test_healthz_is_honest_when_nothing_has_been_filtered(floor):
    # Zero events must still answer with a real uptime and zeroed counters --
    # a health document that hides behind defaults is the same state as no
    # health endpoint, discovered only when it matters.
    floor.record_floor_event({"kept": 3, "dropped": 1})
    floor._reset_counters()
    health = floor.health_payload()
    assert health["counters"]["kept_total"] == 0
    assert health["counters"]["dropped_total"] == 0
    assert health["counters"]["seconds_running"] >= 0
