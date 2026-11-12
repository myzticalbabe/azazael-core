"""Governance SOTA rows: graded confidence (EP156/EP349) and residual obligations (EP382).

Three rows in `SOTA_ROW_RESCORE.md` name this module as their real host, and the
honest version of each is a small additive change rather than a new subsystem:

  * **EP156** (agent uncertainty quantification, host `ConfidenceGate`) — the gate
    answered pass/fail only, so a caller could not tell a pass that cleared the
    threshold by a mile from one that cleared it by less than the disagreement
    between its own inputs. `quantify()` now reports the signed margin against the
    threshold AND the dispersion across sources, and calls a pass that sits inside
    its own standard error UNCERTAIN.
  * **EP349** (LLM-as-a-Verifier, continuous verification, same host) — this tree
    consumes models as opaque providers, so there is no in-repo grader to call.
    The continuous scoring source is therefore SUPPLIED (`add_scorer`), and
    `score_supplied()` is the only thing that runs it. No scorer is invented here.
  * **EP382** (effect-typed obligations) — an ALLOW/DENY verdict cannot express a
    duty that outlives the decision, so `RealtimeAuditPipeline` now keeps a
    residual-obligation register it can report while the obligations are open.

What is asserted, beyond the new behaviour working: that the OLD behaviour is
byte-compatible. A weighted mean at default weights must equal the old plain mean
exactly, `evaluate()` must still return its original keys with the original
verdict, and a pipeline with no obligations must behave as before.
"""


import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _gate(threshold=0.7):
    from CORE.compliance.governance import ConfidenceGate

    return ConfidenceGate(threshold=threshold)


# --------------------------------------------------------------- EP156: margin


def test_default_weights_reproduce_the_old_plain_mean():
    """The weighted mean must equal the previous average at default weights."""
    gate = _gate(threshold=0.5)
    for source, score in (("policy", 0.9), ("reviewer", 0.6), ("ast", 0.3)):
        gate.add_score(source, score)

    assert gate.get_aggregate() == (0.9 + 0.6 + 0.3) / 3


def test_a_weight_actually_changes_the_aggregate():
    gate = _gate(threshold=0.5)
    gate.add_score("heavy", 1.0, weight=3.0)
    gate.add_score("light", 0.0, weight=1.0)

    assert gate.get_aggregate() == 0.75  # not the unweighted 0.5


def test_quantify_reports_a_signed_margin_not_just_a_side():
    gate = _gate(threshold=0.7)
    gate.add_score("a", 0.9)
    gate.add_score("b", 0.9)

    quantified = gate.quantify()

    assert quantified["verdict"] == "ACCEPT"
    assert quantified["margin"] == 0.2  # signed: how far clear of the bar
    assert quantified["threshold"] == 0.7
    assert quantified["source_count"] == 2


def test_a_pass_inside_its_own_dispersion_is_uncertain_not_accept():
    """0.8 clears 0.7 by 0.1, but the sources disagree by more than that.

    This is the case the old pass/fail gate reported as a plain success.
    """
    gate = _gate(threshold=0.7)
    for source, score in (("a", 1.0), ("b", 0.4), ("c", 1.0)):
        gate.add_score(source, score)

    quantified = gate.quantify()

    assert quantified["aggregate_score"] > quantified["threshold"]  # it did pass
    assert quantified["margin"] > 0
    assert quantified["margin"] <= quantified["uncertainty_band"]
    assert quantified["verdict"] == "UNCERTAIN"


def test_below_threshold_is_rejected_with_a_negative_margin():
    gate = _gate(threshold=0.7)
    gate.add_score("a", 0.2)
    gate.add_score("b", 0.3)

    quantified = gate.quantify()

    assert quantified["verdict"] == "REJECT"
    assert quantified["margin"] < 0


def test_no_evidence_is_uncertain_even_though_the_legacy_gate_passes_it():
    """The asymmetry is deliberate and pinned here so neither half drifts.

    `evaluate()` keeps its historical "no scores = no risk" contract because
    callers predate quantification; `quantify()` refuses to call silence a pass.
    """
    gate = _gate(threshold=0.7)

    assert gate.evaluate()["passed"] is True
    assert gate.quantify()["verdict"] == "UNCERTAIN"
    assert gate.quantify()["source_count"] == 0


def test_evaluate_keeps_its_original_keys_and_verdict():
    gate = _gate(threshold=0.5)
    gate.add_score("policy", 0.9)

    result = gate.evaluate()

    assert set(result) == {"passed", "aggregate_score", "threshold", "sources", "source_count"}
    assert result["passed"] is True
    assert result["sources"] == {"policy": 0.9}


# ------------------------------------------------------- EP349: supplied scorer


def test_the_supplied_scorer_is_the_only_thing_that_scores():
    """Registration alone must not add a score -- nothing here calls the model."""
    gate = _gate(threshold=0.5)
    gate.add_scorer("verifier", lambda: 0.42)

    assert gate.scorer_count == 1
    assert gate.quantify()["source_count"] == 0  # not run yet

    reported = gate.score_supplied()

    assert reported == ["verifier"]
    assert gate.quantify()["sources"]["scorer:verifier"] == 0.42


def test_a_raising_scorer_is_recorded_and_skipped_never_counted_as_zero():
    def broken():
        raise RuntimeError("no grader available")

    gate = _gate(threshold=0.5)
    gate.add_scorer("broken", broken)
    gate.add_scorer("fine", lambda: 0.8)

    reported = gate.score_supplied()

    assert reported == ["fine"]                       # the failure is not a source
    assert "scorer:broken" not in gate.quantify()["sources"]
    assert gate.quantify()["sources"]["scorer:fine"] == 0.8


def test_evaluate_with_uncertainty_wraps_without_changing_the_base_result():
    gate = _gate(threshold=0.5)
    gate.add_score("a", 0.9)

    combined = gate.evaluate_with_uncertainty()

    assert combined["passed"] is True
    assert combined["quantified"]["verdict"] == "ACCEPT"
    assert set(gate.evaluate()) == {"passed", "aggregate_score", "threshold",
                                    "sources", "source_count"}


# ------------------------------------------------------ EP382: residual duties


def _pipeline():
    from CORE.compliance.governance import RealtimeAuditPipeline

    return RealtimeAuditPipeline()


def test_an_obligation_outlives_the_decision_and_is_reported_while_open():
    pipeline = _pipeline()

    pipeline.register_obligation("recheck", "src/CORE/foo.py", due_in_s=3600,
                                 reason="allowed, but re-verify after the edit lands")

    open_duties = pipeline.residual_obligations()

    assert open_duties["pending_count"] == 1
    assert open_duties["overdue_count"] == 0
    assert open_duties["resolved_count"] == 0
    assert open_duties["pending"][0]["subject"] == "src/CORE/foo.py"
    assert open_duties["pending"][0]["overdue"] is False


def test_a_duty_past_its_due_time_is_overdue_not_pending():
    pipeline = _pipeline()
    pipeline.register_obligation("late", "src/CORE/bar.py", due_in_s=-10)

    open_duties = pipeline.residual_obligations()

    assert open_duties["overdue_count"] == 1
    assert open_duties["pending_count"] == 0
    assert open_duties["overdue"][0]["overdue"] is True


def test_overdue_tracking_is_computable_at_an_injected_time():
    """`now` is injectable so the boundary needs no sleep."""
    pipeline = _pipeline()
    record = pipeline.register_obligation("timed", "subject", due_in_s=60)

    registered_at = datetime.fromisoformat(record["registered_at"])
    later = registered_at.replace(tzinfo=timezone.utc)

    open_duties = pipeline.residual_obligations(now=later + timedelta(seconds=120))

    assert open_duties["overdue_count"] == 1
    assert open_duties["overdue"][0]["age_s"] >= 100


def test_resolving_a_duty_closes_it_and_unknown_ids_report_failure():
    pipeline = _pipeline()
    pipeline.register_obligation("d1", "subject", due_in_s=60)

    assert pipeline.resolve_obligation("d1", evidence="re-verified green") is True
    assert pipeline.resolve_obligation("never-registered") is False

    open_duties = pipeline.residual_obligations()

    assert open_duties["resolved_count"] == 1
    assert open_duties["pending_count"] == 0
    assert open_duties["total"] == 1


def test_snapshot_is_counts_only_and_a_fresh_pipeline_is_unchanged():
    pipeline = _pipeline()

    assert pipeline.obligation_snapshot() == {"open": 0, "total": 0}
    assert pipeline.get_results() == []  # additive: nothing else moved

    pipeline.register_obligation("d1", "subject")
    pipeline.register_obligation("d2", "subject")
    pipeline.resolve_obligation("d2")

    assert pipeline.obligation_snapshot() == {"open": 1, "total": 2}
