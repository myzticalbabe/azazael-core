"""Tests for the Jev advisor.

Hermetic by construction: the transport is stubbed, so no test can spend the
operator's credits or depend on a third-party service being up. The fake store
captures rows so the assertions are about the RECORD, not about a live database.
"""

from __future__ import annotations

import os
import sys

import pytest

# Same bootstrap the rest of `tests/` uses: `pytest.ini` does not set
# `pythonpath`, so each module puts `src/` on the path itself.
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from CORE.WORMS.expansion import jev_advisor as jev  # noqa: E402


class FakeStore:
    """Minimal stand-in for LocalMemoryStore: capture writes, answer queries."""

    def __init__(self, rows=None):
        self.written = []
        self.rows = list(rows or [])

    def record_event(self, *, source, event_type, data=None, severity="info",
                     tags=None, session_id=None, correlation_id=None):
        self.written.append({
            "source": source, "event_type": event_type, "data": data,
            "severity": severity, "correlation_id": correlation_id,
        })
        return len(self.written)

    def query_memories(self, *, event_type=None, limit=500, offset=0):
        matching = [r for r in self.rows if r.get("event_type") == event_type]
        return matching[offset:offset + limit]


def _valid(choice, ids, confidence=0.9):
    others = [i for i in ids if i != choice]
    rest = (1.0 - confidence) / len(others) if others else 0.0
    probs = {i: rest for i in ids}
    probs[choice] = confidence
    return {"choice": choice, "confidence": confidence, "probabilities": probs}


CANDIDATES = {"alpha": "does alpha", "beta": "does beta", "gamma": "does gamma"}


# ── Validation: it cannot fabricate, and it cannot contradict itself ──

def test_valid_answer_passes():
    got = jev.validate_choice(_valid("beta", CANDIDATES), CANDIDATES)
    assert got["choice"] == "beta"
    assert got["confidence"] == pytest.approx(0.9)


def test_choice_outside_the_offered_set_is_rejected():
    """The central safety property: a candidate we did not offer cannot win."""
    answer = _valid("beta", CANDIDATES)
    answer["choice"] = "worm_that_does_not_exist"
    answer["probabilities"]["worm_that_does_not_exist"] = 0.9
    with pytest.raises(jev.JevError):
        jev.validate_choice(answer, CANDIDATES)


def test_distribution_must_cover_exactly_the_candidates():
    answer = _valid("beta", CANDIDATES)
    del answer["probabilities"]["gamma"]
    with pytest.raises(jev.JevError):
        jev.validate_choice(answer, CANDIDATES)


def test_distribution_must_sum_to_one():
    answer = _valid("beta", CANDIDATES)
    answer["probabilities"] = {k: v * 0.5 for k, v in answer["probabilities"].items()}
    with pytest.raises(jev.JevError):
        jev.validate_choice(answer, CANDIDATES)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.5, "0.9"])
def test_non_finite_or_out_of_range_numbers_are_rejected(bad):
    answer = _valid("beta", CANDIDATES)
    answer["probabilities"]["beta"] = bad
    with pytest.raises(jev.JevError):
        jev.validate_choice(answer, CANDIDATES)


def test_choice_that_is_not_the_argmax_is_rejected():
    """A pick inconsistent with its own probabilities is a contradiction."""
    answer = _valid("beta", CANDIDATES, confidence=0.5)
    answer["probabilities"] = {"alpha": 0.8, "beta": 0.1, "gamma": 0.1}
    with pytest.raises(jev.JevError):
        jev.validate_choice(answer, CANDIDATES)


def test_missing_fields_are_rejected_not_defaulted():
    with pytest.raises(jev.JevError):
        jev.validate_choice({"choice": "beta"}, CANDIDATES)


# ── Fail-open, and every fallback is recorded ────────────────────────

def test_no_credentials_returns_none_and_records_unavailable():
    store = FakeStore()
    advisor = jev.JevAdvisor(api_key=None, store=store, enabled=False)
    assert advisor.choose(question="q", goal="g", candidates=CANDIDATES) is None
    assert len(store.written) == 1
    row = store.written[0]
    assert row["event_type"] == jev.DECISION_TAP
    assert row["data"]["outcome"] == "unavailable"
    assert row["severity"] == "warning"


def test_kill_switch_stops_the_advisor_even_with_a_key(monkeypatch):
    monkeypatch.setenv("AZAZAEL_JEV_ENABLED", "0")
    advisor = jev.JevAdvisor(api_key="present-key", store=FakeStore())
    assert advisor.enabled is False
    assert advisor.available is False


def test_env_switch_off_beats_credentials(monkeypatch):
    monkeypatch.setenv("AZAZAEL_JEV_ENABLED", "false")
    assert jev.JevAdvisor(api_key="k").enabled is False


def test_transport_error_returns_none_and_records_error(monkeypatch):
    store = FakeStore()

    def boom(*_a, **_k):
        raise jev.JevError("Jev returned HTTP 500")

    monkeypatch.setattr(jev, "_post_json", boom)
    advisor = jev.JevAdvisor(api_key="k", store=store)
    assert advisor.choose(question="q", goal="g", candidates=CANDIDATES) is None
    assert store.written[0]["data"]["outcome"] == "error"
    assert "HTTP 500" in store.written[0]["data"]["error"]


def test_malformed_answer_records_invalid_not_error(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(jev, "_post_json",
                        lambda *a, **k: {"answers": {"q": {"choice": "ghost"}}})
    advisor = jev.JevAdvisor(api_key="k", store=store)
    assert advisor.choose(question="q", goal="g", candidates=CANDIDATES) is None
    assert store.written[0]["data"]["outcome"] == "invalid"


def test_single_candidate_question_is_refused_without_calling(monkeypatch):
    """Asking a one-option question burns a request to be told the obvious."""
    called = []
    monkeypatch.setattr(jev, "_post_json", lambda *a, **k: called.append(1) or {})
    store = FakeStore()
    advisor = jev.JevAdvisor(api_key="k", store=store)
    assert advisor.choose(question="q", goal="g", candidates={"only": "one"}) is None
    assert called == []
    assert store.written[0]["data"]["outcome"] == "refused"


# ── The happy path, and the request contract ─────────────────────────

def test_answer_is_returned_and_recorded_with_the_decision_id(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(jev, "_post_json",
                        lambda *a, **k: {"answers": {"q": _valid("gamma", CANDIDATES)},
                                         "model": "jev-1", "usage": {"total_tokens": 7}})
    advisor = jev.JevAdvisor(api_key="k", store=store)
    answer = advisor.choose(question="q", goal="pick one", candidates=CANDIDATES,
                            decision_id="dec-123")
    assert answer is not None and answer.choice == "gamma"
    row = store.written[0]
    assert row["data"]["outcome"] == "answered"
    assert row["data"]["decision_id"] == "dec-123"
    assert row["correlation_id"] == "dec-123"
    assert row["severity"] == "info"
    assert set(row["data"]["candidates"]) == set(CANDIDATES)


def test_request_body_matches_the_typesafe_contract(monkeypatch):
    captured = {}

    def capture(url, key, body, **kwargs):
        captured.update(url=url, key=key, body=body, kwargs=kwargs)
        return {"answers": {"q": _valid("alpha", CANDIDATES)}}

    monkeypatch.setattr(jev, "_post_json", capture)
    jev.JevAdvisor(api_key="secret", model="jev-1", store=FakeStore()).choose(
        question="q", goal="the goal", candidates=CANDIDATES,
        instructions=["rule one"], context={"evidence": 3})

    assert captured["url"] == jev.JEV_ENDPOINT
    assert captured["key"] == "secret"
    body = captured["body"]
    assert body["model"] == "jev-1"
    assert body["state"]["goal"] == "the goal"
    assert body["state"]["context"] == {"evidence": 3}
    question = body["questions"]["q"]
    assert question["type"] == "choice"
    assert question["criteria"] == {k: v for k, v in CANDIDATES.items()}
    assert question["instructions"]["rules"] == ["rule one"]


# ── The independent check ────────────────────────────────────────────

def _decision(choice, decision_id, outcome="answered"):
    return {"event_type": jev.DECISION_TAP,
            "data": {"outcome": outcome, "choice": choice, "decision_id": decision_id}}


def _outcome(success, decision_id):
    return {"event_type": "clusterwormz_worm_executed",
            "data": {"worm": "x", "success": success, "decision_id": decision_id}}


def test_accuracy_comes_from_measured_outcomes_not_the_model():
    store = FakeStore([
        _decision("alpha", "d1"), _decision("beta", "d2"), _decision("gamma", "d3"),
        _outcome(True, "d1"), _outcome(False, "d2"),
    ])
    report = jev.grade_choices(store=store)
    assert report["answered"] == 3
    assert report["graded"] == 2
    assert report["correct"] == 1
    assert report["accuracy"] == pytest.approx(0.5)
    assert report["ungraded"] == 1


def test_ungraded_decisions_are_reported_not_scored_as_failures():
    store = FakeStore([_decision("alpha", "d1")])
    report = jev.grade_choices(store=store)
    assert report["graded"] == 0
    assert report["accuracy"] is None, "no evidence must not read as 0% accuracy"
    assert report["ungraded"] == 1
    assert report["ungraded_ids"] == ["d1"]


def test_fallbacks_are_counted_separately_from_answers():
    store = FakeStore([
        _decision(None, None, outcome="unavailable"),
        _decision(None, None, outcome="unavailable"),
        _decision(None, None, outcome="refused"),
        _decision("alpha", "d1"),
    ])
    report = jev.grade_choices(store=store)
    assert report["fallbacks"] == {"unavailable": 2, "refused": 1}
    assert report["answered"] == 1


def test_partial_success_on_a_decision_counts_as_correct():
    """A decision can cover several executions; any success means it worked."""
    store = FakeStore([_decision("alpha", "d1"),
                       _outcome(False, "d1"), _outcome(True, "d1")])
    report = jev.grade_choices(store=store)
    assert report["graded"] == 1 and report["correct"] == 1


# ── The boundary: no import-time client, no hot-path dependency ──────

def test_importing_the_module_creates_no_client_and_needs_no_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    advisor = jev.JevAdvisor()
    assert advisor.available is False
    assert jev.DEFAULT_MODEL and jev.JEV_ENDPOINT.startswith("https://")


# ── The question we actually ask: which unobserved organ to exercise ─

class _Entry:
    """Stand-in for a registry entry (class or worm object)."""

    def __init__(self, category, document):
        self.category = category
        self.__doc__ = document


REGISTRY = {
    "Measured": _Entry("monitor", "Already runs."),
    "Alpha": _Entry("analysis", "Parses binaries.\nMore detail."),
    "Zeta": _Entry("analysis", "Scores risk."),
    "Beta": _Entry("security", "Audits ports."),
}


def test_only_unobserved_organs_are_offered():
    got = jev.exploration_candidates(REGISTRY, {"Measured"})
    assert "Measured" not in got
    assert set(got) == {"Alpha", "Zeta", "Beta"}


def test_order_is_deterministic_so_the_same_question_repeats():
    first = list(jev.exploration_candidates(REGISTRY, {"Measured"}))
    second = list(jev.exploration_candidates(dict(reversed(list(REGISTRY.items()))),
                                             {"Measured"}))
    assert first == second == ["Alpha", "Zeta", "Beta"], "sorted by category, then name"


def test_candidates_cannot_include_a_name_outside_the_registry():
    """The offered set is the registry, so a phantom cannot be proposed."""
    got = jev.exploration_candidates(REGISTRY, {"Measured", "FleetOrch", "NetDiag"})
    assert not ({"FleetOrch", "NetDiag"} & set(got))
    assert all(name in REGISTRY for name in got)


def test_limit_is_respected():
    assert len(jev.exploration_candidates(REGISTRY, set(), limit=2)) == 2
    assert jev.exploration_candidates(REGISTRY, set(), limit=0) == {}


def test_criterion_states_the_evidence_and_the_purpose():
    got = jev.exploration_candidates(REGISTRY, {"Measured"})
    assert "category=analysis" in got["Alpha"]
    assert "Parses binaries." in got["Alpha"]
    assert "More detail." not in got["Alpha"], "first line only"
    assert "executions_observed=0" in got["Alpha"]


def test_missing_docstring_and_category_do_not_crash():
    entry = _Entry(None, None)
    entry.__doc__ = None
    got = jev.exploration_candidates({"Nameless": entry}, set())
    assert "(no docstring)" in got["Nameless"]
    assert "unclassified" in got["Nameless"]


def test_the_dispatch_path_does_not_import_the_advisor():
    """The ordering decision must not be able to block on a network call.

    Asserted on the source of the module that owns that path, because the
    property is "this file does not reference it" - a runtime check would only
    prove that today's code path happens not to reach it.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "CORE"
    for name in ("worm_registry.py",):
        source = (root / name).read_text(encoding="utf-8", errors="replace")
        assert "jev_advisor" not in source, f"{name} must not depend on Jev"
