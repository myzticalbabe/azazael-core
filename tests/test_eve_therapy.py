"""G1 test for src/eve_unleashed/therapy.py.

No network, no audio hardware: prompt carries the mood, the fallback
is always supportive, and the voice parser maps to allowlisted actions
only (it never executes).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eve_unleashed.therapy import (build_prompt, fallback_response,
                                   parse_voice_command)


def test_prompt_carries_mood_and_note():
    p = build_prompt("overwhelmed", "deadline tomorrow")
    assert "overwhelmed" in p and "deadline tomorrow" in p
    assert "No diagnosis" in p


def test_fallback_is_supportive_and_grounded():
    r = fallback_response("stressed", "long day")
    assert "Breathe in 4 counts" in r
    assert "long day" in r
    assert len(r) > 100


def test_voice_parser_allowlist():
    assert parse_voice_command("I feel anxious")["action"] == "therapy"
    assert parse_voice_command("let's breathe")["action"] == "breathe"
    assert parse_voice_command("apply best evolution")[
        "action"] == "apply-best"
    assert parse_voice_command("revert the game")["action"] == "revert"
    assert parse_voice_command("next step")["action"] == "next"
    assert parse_voice_command("order pizza")["action"] == "unknown"
