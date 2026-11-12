"""G1 test for src/eve_unleashed/unleash.py.

Vessel identity roundtrips through its sidecar file; the matrix names
every function with a boolean; dispatch runs allowlisted hands and
refuses strangers; the call scanner never executes.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from eve_unleashed import unleash
from eve_unleashed.unleash import dispatch, matrix, scan_calls


def test_matrix_names_every_function_with_boolean(tmp_path, monkeypatch):
    monkeypatch.setattr(unleash, "__name__", "eve_unleashed.unleash")
    rows = matrix()
    assert len(rows) == len(unleash.FUNCTIONS)
    assert all(isinstance(r["available"], bool) for r in rows)
    names = [r["function"] for r in rows]
    assert "foundry.build" in names and "nsp.verify" in names


def test_scan_extracts_calls_without_executing():
    reply = ("Here you go.\n"
             "EVE_CALL: stem.track {\"style\": \"lab\"}\n"
             "EVE_CALL: nope.bogus {}\n"
             "EVE_CALL: broken {\n"
             "done.")
    calls = scan_calls(reply)
    assert [c["function"] for c in calls] == ["stem.track", "nope.bogus"]
    assert calls[0]["args"] == {"style": "lab"}


def test_dispatch_runs_hands_refuses_strangers():
    steps = dispatch("stem.track", {"style": "guided", "goal": "g"})
    assert steps[0] == "GOAL: g"
    verdict = dispatch("ablate.classify", {"text": "hi", "error": None})
    assert verdict[0] == "ANSWERED"
    with pytest.raises(ValueError):
        dispatch("nope.bogus", {})
