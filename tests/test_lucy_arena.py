"""Arena judging for Lucy: five gates, one champion, no model.

Every candidate reply faces the same five gates (the five throws);
the process IS the verdict. Run under 316t with::

    pytest tests/test_lucy_arena.py -q
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "Project_LUCY"))

import lucy as L


def _ground():
    return L.therapy_response("stressed", "work deadline")


def test_judge_shapes_match_throw_order():
    assert [j("m", "n", "x")["throw"] for j in L.JUDGES] == list(L.THROWS)
    for j in L.JUDGES:
        rep = j("stressed", "deadline", _ground())
        assert set(rep) == {"throw", "gate", "passed", "reason"}
        assert isinstance(rep["passed"], bool)


def test_rock_needs_the_grounding_script():
    assert L.judge_rock("m", "n", _ground())["passed"] is True
    assert L.judge_rock("m", "n", "warm words with no technique")["passed"] is False


def test_spock_refuses_clinician_language():
    bad = "You have depression and need medication"
    assert L.judge_spock("m", "n", bad)["passed"] is False
    assert L.judge_spock("m", "n", _ground())["passed"] is True


def test_paper_refuses_invented_particulars():
    assert L.judge_paper("m", "n", "take 20mg twice http://x")["passed"] is False
    assert L.judge_paper("m", "n", "warm plain words")["passed"] is True


def test_scissors_cuts_the_long():
    assert L.judge_scissors("m", "n", "word " * 61)["passed"] is False
    assert L.judge_scissors("m", "n", "short and warm?")["passed"] is True


def test_lizard_holds_the_thread_or_asks_on():
    assert L.judge_lizard("m", "n", "generic comfort, no question")["passed"] is False
    assert L.judge_lizard("m", "deadline", "this deadline sounds heavy")["passed"] is True
    assert L.judge_lizard("m", "n", "warm words, what next?")["passed"] is True


def test_bout_is_merit_not_mood():
    good, bad = _ground(), "You have depression, take 20mg daily, no question"
    import random
    rep = L.bout(good, bad, "stressed", "deadline", random.Random(316))
    assert rep["winner"] == good and rep["tiebreak"] is False


def test_arena_crowns_a_grounded_champion_deterministically():
    first = L.arena_response("stressed", "work deadline")
    second = L.arena_response("stressed", "work deadline")
    assert first["champion"] == second["champion"]
    assert "Breathe in 4 counts" in first["champion"]
    assert [p for rnd in first["rounds"] for p in rnd]


def test_arena_is_all_inclusive_without_limitations():
    assert L.arena([], "m", "n")["champion"] is None
    odd = L.arena(["a one?", "b two?", "c three?"], "m", "n")
    assert odd["champion"] in ("a one?", "b two?", "c three?")
    many = L.arena(["reply %d?" % i for i in range(9)], "m", "n")
    assert many["champion"] in ["reply %d?" % i for i in range(9)]


def test_companion_contract_untouched_by_the_arena():
    out = L.companion_response("stressed", "arch check")
    assert "Breathe in 4 counts" in out["response"]


def test_cli_arena_crowns_and_exits_zero(capsys):
    assert L.main(["--arena", "stressed", "work"]) == 0
    assert "Breathe in 4 counts" in capsys.readouterr().out
