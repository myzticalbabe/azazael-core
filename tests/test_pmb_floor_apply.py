"""Floor differential against a COMMITTED fixture, not the live store.

`pmb_floor_proxy.apply_floor()` is where the two-tier relevance floor lives:
tier 1 (`PMB_RECALL_MIN_BEST`, 0.89) drops a whole scored result when its best
row is under the query floor and names the drop; tier 2 (`PMB_RECALL_FLOOR`,
0.70) trims the weak tail of a genuine result. The live-differential gate in
`mcp_startup_gate.py` proves the filtering is real NOW; these tests pin the
*property* against populations measured 2026-10-30 and committed as
`tests/fixtures/pmb_floor_fixture.json`, so the differential survives corpus
purges that move the live numbers:

  * the thresholds sit INSIDE the measured gap (strictly over the loudest
    noise, strictly under the weakest real query) -- not on a guess;
  * a noise query returns an EMPTY, explicitly-named result, not an answer
    wearing the shape of one;
  * a real query keeps its strong rows and loses only the tail, `n_results`
    must agree, and the drop is named;
  * a partly-scored or unscored payload is never touched.

These need no daemon, no port, no embedding model: `apply_floor` is pure.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "pmb_floor_proxy.py"
FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "pmb_floor_fixture.json"


def load_floor_module():
    spec = importlib.util.spec_from_file_location("pmb_floor_proxy_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def floor():
    return load_floor_module()


@pytest.fixture
def fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _scored_rows(scores):
    return [{"score": s, "content": "row-%s" % s} for s in scores]


# --------------------------------------------------------------------------- #
# Threshold placement is inside the measured gap
# --------------------------------------------------------------------------- #

def test_defaults_match_the_committed_floors(floor, fixture_data):
    # The defaults in the tool must equal what the fixture recorded: a drift
    # between code and calibration is the exact state where the sorted answer
    # reads clean but the boundary has moved.
    assert floor.MIN_BEST == pytest.approx(
        fixture_data["floors"]["PMB_RECALL_MIN_BEST"])
    assert floor.FLOOR == pytest.approx(
        fixture_data["floors"]["PMB_RECALL_FLOOR"])


def test_the_query_floor_sits_inside_the_measured_gap(floor, fixture_data):
    noise = fixture_data["populations"]["noise_best"]
    real = fixture_data["populations"]["real_best"]
    gap = fixture_data["floors"]["measured_gap"]
    assert floor.MIN_BEST > max(noise), (
        "a noise query beat the query floor -- tier 1 would answer %r"
        % max(noise))
    assert floor.MIN_BEST < min(real), (
        "the weakest real query is under the query floor -- a genuine answer "
        "would be dropped")
    assert gap[0] < floor.MIN_BEST < gap[1]
    # The row floor must not cut the median real row: it only trims the tail.
    assert floor.FLOOR < fixture_data["populations"]["real_row_stats"]["median"]


# --------------------------------------------------------------------------- #
# The differential, expressed as two result sets
# --------------------------------------------------------------------------- #

def test_a_noise_query_returns_empty_and_names_its_drop(floor, fixture_data):
    population_len = len(fixture_data["populations"]["noise_best"])
    # n_results included: apply_floor rewrites it when present, and a daemon
    # recall result carries it -- the zero must be visible in the rewritten
    # payload, not just implied by the empty list.
    payload = {"results": _scored_rows(fixture_data["populations"]["noise_best"]),
               "n_results": population_len}
    out, note = floor.apply_floor(payload)
    assert out["results"] == []
    assert out["n_results"] == 0
    assert note and "no match" in note
    assert out["floor"]["tier"] == "query"
    assert out["floor"]["dropped"] == population_len
    assert out["floor"]["kept"] == 0


def test_a_payload_without_n_results_is_not_given_one(floor, fixture_data):
    # n_results is only rewritten when the source payload has it; apply_floor
    # must not invent keys that the upstream result never carried.
    payload = {"results": _scored_rows(fixture_data["populations"]["noise_best"])}
    out, note = floor.apply_floor(payload)
    assert out["results"] == []
    assert "n_results" not in out
    assert note and "no match" in note


def test_a_real_query_keeps_its_strong_rows_and_trims_the_tail(
        floor, fixture_data):
    tail = fixture_data["populations"]["real_row_tail"]  # 0.600 .. 0.937
    out, note = floor.apply_floor({"results": _scored_rows(tail),
                                   "n_results": len(tail)})
    survivors = [r["score"] for r in out["results"]]
    assert survivors == [s for s in tail if s >= floor.FLOOR]
    assert out["n_results"] == len(survivors) == 2
    assert out["floor"]["tier"] == "row"
    assert out["floor"]["kept"] == 2
    assert out["floor"]["dropped"] == len(tail) - 2
    assert note and "below relevance floor" in note


# --------------------------------------------------------------------------- #
# Unsuitable payloads are never touched
# --------------------------------------------------------------------------- #

def test_a_partly_scored_list_is_left_whole(floor):
    rows = [{"score": 0.99, "content": "scored"}, {"content": "UNSCORED"}]
    original = {"results": [dict(r) for r in rows]}
    out, note = floor.apply_floor(original)
    assert note == ""
    assert out["results"] == rows
    assert "floor" not in out


def test_unscored_payloads_are_never_touched(floor):
    payload = {"decisions": ["x"], "stats": {"n": 1}, "n_results": 1}
    out, note = floor.apply_floor(payload)
    assert out == payload
    assert note == ""
