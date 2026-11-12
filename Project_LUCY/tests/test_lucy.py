"""Lucy tests. Stdlib only, no network, no models, no audio hardware."""

import sys
from pathlib import Path

LUCY = Path(__file__).resolve().parents[1]
REPO = LUCY.parent
sys.path.insert(0, str(LUCY))
sys.path.insert(0, str(REPO / "src"))

import lucy


def test_response_is_grounding_and_offline():
    out = lucy.companion_response("overwhelmed", "deadline tomorrow")
    assert "Breathe in 4 counts" in out["response"]
    assert "overwhelmed" in out["therapy_prompt"]
    assert "deadline tomorrow" in out["therapy_prompt"]


def test_eliza_reading_is_honest_without_graph():
    r = lucy.eliza_reading({"workload": 0.9})
    assert r["known"] is False
    assert "reason" in r


def test_empty_cues_say_so():
    r = lucy.eliza_reading({})
    assert r == {"known": False,
                 "reason": "no cues -- nothing to recognise"}


def test_lucy_cli_runs():
    assert lucy.main(["stressed", "long day"]) == 0
