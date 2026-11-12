"""Tests for the spawn decision and the choice->outcome unification.

Two things are under test, and both were missing from the architecture:

1. `SpawnDiversityEngine.decide_execution_order` — the real dispatch decision.
    Before 2026-09-21 the engine was instantiated by `ClusterManager` and
    never called (`_diversity.` had ZERO call sites), so its 608 recorded
    `spawn_selected` rows described choices nothing ever executed. (U3: the
    manager is gone; the engine lives on in worm_registry/WORM_SELECTOR and
    these tests pin the decision contract, not the dead caller.)

2. The round trip: a decision, its recorded rows, the executions it causes, and
   the join that reads them back. Those two halves lived in different
   components and shared exactly one worm name out of 13 chosen / 28 executed,
   so no join existed at any level.

Run:
    /c/Azazael/.venvs/py315t/Scripts/python.exe -m pytest tests/test_spawn_decision.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from CORE.worm_registry import WORM_REGISTRY, SpawnDiversityEngine  # noqa: E402
from CORE.WORMS.expansion.spawn_credit import build_spawn_credit  # noqa: E402


class StubWorm:
    """Minimal worm: `_run_worm_method` will find and call `execute`."""

    def execute(self):
        return {"stub": True}


@pytest.fixture
def engine(monkeypatch):
    """An engine whose PAMS recording is captured instead of persisted."""
    eng = SpawnDiversityEngine()
    rows = []
    monkeypatch.setattr(eng, "_record_pams",
                        lambda et, data: rows.append((et, dict(data))))
    return eng, rows


def picks(rows):
    return [d for et, d in rows if et == "spawn_selected"]


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------

def test_decision_returns_an_id_and_an_order(engine):
    eng, rows = engine
    order, decision_id = eng.decide_execution_order("diagnostic", ["A", "B", "C"])
    assert sorted(order) == ["A", "B", "C"]
    assert decision_id and decision_id.startswith("d-")


def test_ordering_never_drops_a_candidate(engine):
    """Demotion is the strongest action: a droppable policy could starve the
    fleet, including the worms that keep this loop alive."""
    eng, _ = engine
    order, _ = eng.decide_execution_order(
        "diagnostic", ["A", "B", "C"], credit={"A": -1.0, "B": -1.0})
    assert sorted(order) == ["A", "B", "C"]


def test_grounded_credit_dominates_ordering(engine):
    eng, _ = engine
    order, _ = eng.decide_execution_order(
        "diagnostic", ["A", "B", "C"], credit={"C": 0.9, "A": 0.1})
    assert order[0] == "C"


def test_spine_q_weight_is_secondary_to_credit(engine):
    eng, _ = engine
    # Credit is present for A, so it wins despite B's Q-weight.
    order, _ = eng.decide_execution_order(
        "n", ["A", "B"], q_weights={"n:B": 5.0}, credit={"A": 0.5})
    assert order == ["A", "B"]
    # No credit -> the Q-weight decides.
    order2, _ = eng.decide_execution_order("n", ["A", "B"],
                                           q_weights={"n:B": 5.0})
    assert order2 == ["B", "A"]


def test_registry_priority_is_the_fallback(engine, monkeypatch):
    eng, _ = engine
    monkeypatch.setitem(WORM_REGISTRY, "FakeHi", {"priority": 9})
    monkeypatch.setitem(WORM_REGISTRY, "FakeLo", {"priority": 1})
    order, _ = eng.decide_execution_order("n", ["FakeLo", "FakeHi"])
    assert order == ["FakeHi", "FakeLo"]


def test_tie_break_is_deterministic(engine):
    eng, _ = engine
    first, _ = eng.decide_execution_order("n", ["Zed", "Alpha", "Mid"])
    second, _ = eng.decide_execution_order("n", ["Zed", "Alpha", "Mid"])
    assert first == second == ["Alpha", "Mid", "Zed"]


def test_duplicate_candidates_collapse(engine):
    eng, rows = engine
    order, _ = eng.decide_execution_order("n", ["A", "A", "B", None, ""])
    assert order == ["A", "B"]
    assert len(picks(rows)) == 2


def test_empty_candidates_decides_nothing(engine):
    eng, rows = engine
    assert eng.decide_execution_order("n", []) == ([], None)
    assert picks(rows) == []


def test_each_decision_gets_a_fresh_id(engine):
    eng, _ = engine
    ids = {eng.decide_execution_order("n", ["A"])[1] for _ in range(20)}
    assert len(ids) == 20


def test_recorded_rows_carry_the_join_keys(engine):
    """These rows are what makes the two taps joinable at all."""
    eng, rows = engine
    _, decision_id = eng.decide_execution_order(
        "diagnostic", ["A", "B"], reason="nature-order")
    recorded = picks(rows)

    assert len(recorded) == 2
    assert {p["decision_id"] for p in recorded} == {decision_id}
    assert {p["rank"] for p in recorded} == {0, 1}
    assert all(p["n_targets"] == 2 for p in recorded)
    assert all(p["nature"] == "diagnostic" for p in recorded)
    assert all(p["reason"] == "nature-order" for p in recorded)


def test_recorded_rows_are_json_serialisable(engine):
    eng, rows = engine
    eng.decide_execution_order("n", ["A"], credit={"A": 0.25})
    json.dumps(picks(rows))  # must not raise


# --------------------------------------------------------------------------
# The dispatch path honours the decision
# --------------------------------------------------------------------------
# The round trip: decision rows + outcome rows -> one causal join
# --------------------------------------------------------------------------

class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def query_memories(self, event_type=None, limit=50, offset=0, **kw):
        sel = [r for r in self.rows if r.get("event_type") == event_type]
        return sel[offset:offset + limit]


def test_decision_and_outcome_halves_join_end_to_end(monkeypatch):
    """The whole point: a decision written by the engine and an outcome written
    by the executor must be readable as ONE decision by spawn_credit."""
    eng = SpawnDiversityEngine()
    rows = []
    monkeypatch.setattr(eng, "_record_pams",
                        lambda et, data: rows.append((et, dict(data))))
    _order, decision_id = eng.decide_execution_order("diagnostic",
                                                      ["Alpha", "Beta"])
    choice_rows = picks(rows)

    # Execution half: the canonical outcome schema, sharing the decision id.
    outcome_rows = [
        {"event_type": "clusterwormz_worm_executed", "severity": "info",
         "data": json.dumps({"worm": "Alpha", "success": True,
                             "duration_ms": 12.0, "decision_id": decision_id,
                             "nature": "diagnostic"})},
        {"event_type": "clusterwormz_worm_executed", "severity": "error",
         "data": json.dumps({"worm": "Beta", "success": False,
                             "duration_ms": 30.0, "decision_id": decision_id,
                             "nature": "diagnostic"})},
    ]
    store_rows = [
        {"event_type": "spawn_selected", "severity": "info",
         "data": json.dumps(c)} for c in choice_rows
    ] + outcome_rows

    credit = build_spawn_credit(FakeStore(store_rows))

    assert credit.attribution_mode == "per-decision (causal)"
    joined = credit.joined_decisions[decision_id]
    assert joined["n_chosen"] == 2 and joined["n_ran"] == 2
    assert joined["results"]["Beta"]["success"] is False
    assert joined["chosen_but_never_ran"] == []


def test_decision_survives_a_missing_pams_recorder(monkeypatch):
    """Fail-soft: a decision must still be made when persistence is down.

    The choice row is evidence, not the decision itself - losing memory must
    never stop the fleet from being ordered.
    """
    import CORE.worm_registry as wr

    eng = SpawnDiversityEngine()
    monkeypatch.setattr(wr, "_get_pams_recorder", lambda: None)

    order, decision_id = eng.decide_execution_order("n", ["A", "B"])
    assert order == ["A", "B"]
    assert decision_id


# --------------------------------------------------------------------------
# Version stamps: which calibration made this decision
# --------------------------------------------------------------------------

def test_every_choice_row_carries_the_versions_that_made_it(engine):
    """Without a version block, a promoted calibration and its effect are
    indistinguishable in the ledger from any other change in the fleet. The
    choice rows and the outcome rows join on `decision_id`, so the versions
    written here are inherited by every outcome the decision caused."""
    eng, rows = engine
    eng.decide_execution_order("diagnostic", ["A", "B", "C"])
    picked = picks(rows)
    assert len(picked) == 3
    for row in picked:
        v = row.get("versions")
        assert isinstance(v, dict), "a choice with no version cannot be attributed"
        assert set(v) == {"hp", "wg", "wg_gen", "spine_edges"}
        assert v["hp"], "the hyperparameter fingerprint must be stamped"


def test_all_rows_of_one_decision_share_one_version_block(engine):
    """Resolved once per decision: a decision cannot be partly made by one
    calibration and partly by another."""
    eng, rows = engine
    eng.decide_execution_order("diagnostic", ["A", "B", "C"])
    blocks = {json.dumps(r["versions"], sort_keys=True) for r in picks(rows)}
    assert len(blocks) == 1


def test_a_hypothetical_ranking_carries_no_version(engine):
    """`record=False` must not enter the ledger as a decision that was taken,
    so it must not carry the evidence that would make it joinable either."""
    eng, rows = engine
    order, _ = eng.decide_execution_order("diagnostic", ["A", "B"], record=False)
    assert order == ["A", "B"]
    assert picks(rows) == []


def test_a_genome_that_was_never_promoted_is_reported_as_none(monkeypatch,
                                                             tmp_path):
    """`None` means "not readable", never "absent": a registry with no promoted
    genome is a real state and a reader must be able to tell the two apart."""
    import CORE.worm_registry as wr

    monkeypatch.setattr(wr, "WIRING_PROMOTED_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setattr(wr, "_wiring_cache", None)
    monkeypatch.setattr(wr, "_wiring_cache_stamp", None)

    v = wr._decision_versions()
    assert v["wg"] is None and v["wg_gen"] is None
    assert v["hp"], "the parameter fingerprint is independent of the genome"


def test_a_broken_version_probe_does_not_break_dispatch(monkeypatch):
    """Dispatch must always produce an order: a version stamp is evidence, not
    a precondition, and a learner must never be able to stop the fleet."""
    import CORE.worm_registry as wr

    def _boom():
        raise RuntimeError("genome probe down")

    monkeypatch.setattr(wr, "_load_promoted_wiring", _boom)
    eng = SpawnDiversityEngine()
    monkeypatch.setattr(eng, "_record_pams", lambda et, data: None)

    order, decision_id = eng.decide_execution_order("diagnostic", ["A", "B"])
    assert order == ["A", "B"] and decision_id


def test_decision_survives_a_broken_pams_recorder(monkeypatch):
    """A recorder that raises must not propagate into the dispatch path."""
    import CORE.worm_registry as wr

    class Exploding:
        def record_event(self, *a, **kw):
            raise RuntimeError("pams down")

    eng = SpawnDiversityEngine()
    monkeypatch.setattr(wr, "_get_pams_recorder", lambda: Exploding())

    order, decision_id = eng.decide_execution_order("n", ["A", "B"])
    assert order == ["A", "B"] and decision_id
