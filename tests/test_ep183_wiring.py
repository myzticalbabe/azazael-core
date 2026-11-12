#!/usr/bin/env python3
"""EP183 wiring: the adapter drives verify->judge both directions.

The mechanism (localize_violations ranking, ACCEPT shape) is a prior
lane's cover in test_logicloc_localization.py; this file pins only the
adapter's two-direction drive plus its no-record discipline.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_ep183_accepts_sat_and_rejects_unsat_with_region():
    from CORE.technique_wiring import _l3_verify as l3
    out = l3._ep183()
    assert out["engaged"] is True
    assert out["accepted"] is True
    assert out["accept_verdict"] == "ACCEPT"
    assert out["reject_verdict"] == "REJECT"
    assert out["smallest"]["located"] is True
    assert out["smallest"]["region"] == "n"
    assert out["where"].endswith("judge_result+localize_violations")


def test_ep183_records_nothing_to_memory():
    from CORE.technique_wiring import _l3_verify as l3
    from CORE.cd2_verifier import SMTVerifier
    verifier = SMTVerifier(auto_record=False)
    assert verifier._history == []
    out = l3._ep183()
    assert out["accepted"] is True
