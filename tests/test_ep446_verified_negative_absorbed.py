"""EP446 ATP: an accepted gate must record a VERIFIED NEGATIVE.

Absorption test for engram #632/#640, slice 3.

The defect this locks down: `audit_trail.audit_verified_negative`
(`audit_trail.py:691`, "ATP capsules/negatives", P0 slice 1 of the SOTA
integration blueprint) is the documented home for *proof that something did NOT
occur*, and it had **zero callers outside its own module** -- measured over the
whole repo, not just `src/`. The registry entry named it as EP446's mechanism
(`technique_wiring/_l3_verify.py`), so the technique was registered while its
mechanism ran nowhere.

`verify_before_accept` already writes to the hash chain through
`audit_verification_gate` behind a `record` opt-in. An **accepted** gate is
exactly a verified negative: every supplied condition was tested and the failure
did not occur. So the negative is now recorded alongside the acceptance, on the
same chain, behind the same opt-in.

Per this repo's standard (tests/test_simulate_vs_spawn.py): a mechanism nothing
calls is EXERCISE-ONLY by construction, so the wiring is part of the contract.
"""

import sys
from pathlib import Path

# Repo convention: tests insert src/ themselves (tests/conftest.py does not).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def _spy(monkeypatch):
    """Capture calls to both ledger writers instead of touching the real chain."""
    from CORE import audit_trail as at

    calls = {"negative": [], "gate": []}

    def _neg(subject, claim, evidence="", checked_by=""):
        calls["negative"].append({"subject": subject, "claim": claim,
                                  "evidence": evidence, "checked_by": checked_by})
        return "cite-negative-1"

    def _gate(subject, accepted, reasons, remediations):
        calls["gate"].append({"subject": subject, "accepted": accepted,
                              "reasons": list(reasons)})
        return "cite-gate-1"

    monkeypatch.setattr(at, "audit_verified_negative", _neg, raising=True)
    monkeypatch.setattr(at, "audit_verification_gate", _gate, raising=True)
    return calls


def _accept(subject, **kw):
    from CORE import verification_gate as vg

    return vg.verify_before_accept(subject, **kw)


def test_an_accepted_gate_records_a_verified_negative(monkeypatch):
    """The live gate must produce the negative, not merely be able to."""
    calls = _spy(monkeypatch)

    out = _accept("ep446-probe",
                  confidences=[{"id": "a", "confidence": 0.95},
                               {"id": "b", "confidence": 0.9}],
                  record=True)

    assert out["accepted"] is True, out
    assert len(calls["negative"]) == 1, calls
    assert calls["negative"][0]["subject"] == "ep446-probe"
    assert calls["negative"][0]["checked_by"] == \
        "CORE.verification_gate:verify_before_accept"


def test_the_negative_carries_the_gate_reasons_as_evidence(monkeypatch):
    """Evidence must be the gate's own recorded checks, not a placeholder."""
    calls = _spy(monkeypatch)

    _accept("ep446-evidence",
            confidences=[{"id": "a", "confidence": 0.95}],
            record=True)

    evidence = calls["negative"][0]["evidence"]
    assert "margins-ok" in evidence, evidence
    assert evidence != "no checks supplied", evidence


def test_a_rejected_gate_records_no_verified_negative(monkeypatch):
    """A rejection is NOT a proof of non-occurrence -- it must not claim one."""
    calls = _spy(monkeypatch)

    out = _accept("ep446-reject",
                  confidences=[{"id": "a", "confidence": 0.1}],
                  record=True)

    assert out["accepted"] is False, out
    assert calls["negative"] == [], calls


def test_record_false_writes_nothing(monkeypatch):
    """Opt-in guard: no churn on the audit chain when recording is off."""
    calls = _spy(monkeypatch)

    out = _accept("ep446-norecord",
                  confidences=[{"id": "a", "confidence": 0.95}],
                  record=False)

    assert out["accepted"] is True, out
    assert calls["negative"] == [] and calls["gate"] == [], calls


def test_the_gate_citation_contract_is_preserved(monkeypatch):
    """Additive only: the existing gate citation must still be written."""
    calls = _spy(monkeypatch)

    out = _accept("ep446-citation",
                  confidences=[{"id": "a", "confidence": 0.95}],
                  record=True)

    assert len(calls["gate"]) == 1, calls
    assert out["citation"] == "cite-gate-1", out
