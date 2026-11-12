#!/usr/bin/env python3
"""EP106 wiring: the symbolic half is the auditable one.

Drives the full Worker-Verifier-Judge pipeline both directions and pins the
auditable property (rationale + confidence on every verdict). The mechanism
shapes (ACCEPT/REJECT, localisation) are a prior lane's cover in
test_logicloc_localization.py; this file pins only the adapter's pipeline
drive plus its no-record discipline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_ep106_pipeline_auditable_both_directions():
    from CORE.technique_wiring import _l3_verify as l3
    out = l3._ep106()
    assert out["engaged"] is True
    assert out["accepted"] is True
    assert out["accept_verdict"] == "ACCEPT"
    assert out["reject_verdict"] == "REJECT"
    assert isinstance(out["accept_rationale"], str) and out["accept_rationale"] != ""
    assert isinstance(out["reject_rationale"], str) and out["reject_rationale"] != ""
    assert "n" in out["reject_rationale"]
    assert out["where"].endswith("SMTVerifier.worker_verifier_judge")


def test_ep106_records_nothing_to_memory():
    from CORE.technique_wiring import _l3_verify as l3
    from CORE.cd2_verifier import SMTVerifier
    verifier = SMTVerifier(auto_record=False)
    assert verifier._history == []
    out = l3._ep106()
    assert out["accepted"] is True
